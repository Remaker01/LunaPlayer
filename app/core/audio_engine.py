"""
Audio engine – PyAV decoding in a background thread + QAudioSink playback.

Architecture
------------
*AudioDecoder* (QThread)
    Opens an audio file with PyAV, decodes and resamples audio frames to a
    uniform PCM format (44100 Hz, stereo, signed 16-bit little-endian), then
    emits the raw bytes via a cross-thread signal.

*AudioEngine* (QObject)
    Lives in the main thread.  Creates a QAudioSink with the matching format,
    receives PCM data from the decoder and feeds it to the sink's IO device.
    Exposes play/pause/stop/seek/volume controls and periodic position updates.

All communication between the two classes happens through Qt signals – the
decoder thread never touches any QObject that lives in the main thread.
"""

from __future__ import annotations

import enum
import logging
from typing import Optional

from PySide6.QtCore import QIODevice, QMutex, QMutexLocker, QObject, QThread, QTimer, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

import av.container, av.stream
from av.audio.resampler import AudioResampler

# ---------------------------------------------------------------------------
# Helper: convert a decoded AudioFrame to packed s16le PCM bytes
# ---------------------------------------------------------------------------

def _frame_to_s16_packed(frame) -> Optional[bytearray]:
    """Convert *frame* to interleaved s16 little-endian PCM, or ``None``
    if the format is unsupported (caller should use AudioResampler).

    **Critical**: uses ``frame.samples`` (not plane buffer length) as the
    authoritative sample count.  PyAV plane buffers may be larger than
    the valid data due to FFmpeg's internal alignment/padding – reading
    beyond ``frame.samples`` picks up uninitialised memory, which is the
    root cause of noise + wrong playback speed.

    Supported formats:
      s16/s16p   – 16-bit integer  (packed / planar)
      s32/s32p   – 32-bit integer  (packed / planar)  → downscale
      flt/fltp   – 32-bit float    (packed / planar)
      dbl/dblp   – 64-bit float    (packed / planar)  → float64 → int16
    """
    import struct

    try:
        fmt_name = frame.format.name if hasattr(frame.format, "name") else str(frame.format)
    except Exception:
        return None

    n_planes = len(frame.planes)
    if n_planes == 0:
        return bytearray()

    ns = int(frame.samples)            # valid samples per channel
    try:
        nc = int(frame.layout.channels)
    except Exception:
        nc = n_planes if n_planes > 1 else 2  # assume stereo for packed
    if ns <= 0:
        return bytearray()

    fmt_lower = fmt_name.lower()
    is_float = fmt_lower in ("flt", "fltp")
    is_double = fmt_lower in ("dbl", "dblp")
    is_s32 = fmt_lower in ("s32", "s32p")
    is_s16 = fmt_lower in ("s16", "s16p")

    if not (is_float or is_double or is_s32 or is_s16):
        return None  # fallback → AudioResampler

    planes = frame.planes

    # ── 1. s16 / s16p ──────────────────────────────────────────────
    if is_s16:
        if n_planes == 1:
            # Packed s16: valid bytes = ns * nc * 2
            nbytes = int(ns * nc * 2)
            return bytearray(memoryview(planes[0])[:nbytes])
        # Planar s16p: ns samples per plane.
        views = [memoryview(p).cast("h") for p in planes]
        buf = bytearray()
        for i in range(ns):
            for v in views:
                buf.extend(v[i].to_bytes(2, "little", signed=True))
        return buf

    # ── 2. s32 / s32p (downscale 32→16 bit) ────────────────────────
    if is_s32:
        if n_planes == 1:
            # Packed s32: valid int32 values = ns * nc
            nvals = ns * nc
            data = memoryview(planes[0]).cast("i")
            buf = bytearray()
            for val in data[:nvals]:
                s16 = max(-32768, min(32767, val >> 16))
                buf.extend(struct.pack("<h", s16))
            return buf
        views = [memoryview(p).cast("i") for p in planes]
        buf = bytearray()
        for i in range(ns):
            for v in views:
                s16 = max(-32768, min(32767, v[i] >> 16))
                buf.extend(struct.pack("<h", s16))
        return buf

    # ── 3. flt / fltp ──────────────────────────────────────────────
    if is_float:
        if n_planes == 1:
            data = memoryview(planes[0]).cast("f")
            buf = bytearray()
            for val in data[: ns * nc]:
                s16 = int(max(-1.0, min(1.0, val)) * 32767)
                buf.extend(struct.pack("<h", s16))
            return buf
        views = [memoryview(p).cast("f") for p in planes]
        buf = bytearray()
        for i in range(ns):
            for v in views:
                s16 = int(max(-1.0, min(1.0, v[i])) * 32767)
                buf.extend(struct.pack("<h", s16))
        return buf

    # ── 4. dbl / dblp (float64 → int16) ────────────────────────────
    if is_double:
        if n_planes == 1:
            data = memoryview(planes[0]).cast("d")
            buf = bytearray()
            for val in data[: ns * nc]:
                s16 = int(max(-1.0, min(1.0, val)) * 32767)
                buf.extend(struct.pack("<h", s16))
            return buf
        views = [memoryview(p).cast("d") for p in planes]
        buf = bytearray()
        for i in range(ns):
            for v in views:
                s16 = int(max(-1.0, min(1.0, v[i])) * 32767)
                buf.extend(struct.pack("<h", s16))
        return buf

    return None


def _frames_to_pcm_bytes(out_frames: list) -> bytearray:
    """Legacy helper – delegates to _frame_to_s16_packed with fallback."""
    buf = bytearray()
    for out_fr in out_frames:
        if out_fr is None:
            continue
        converted = _frame_to_s16_packed(out_fr)
        if converted is None:
            # Unsupported format – skip (caller should use resampler).
            continue
        buf.extend(converted)
    return buf

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TARGET_SAMPLE_RATE = 44100
TARGET_CHANNELS = 2
TARGET_SAMPLE_FORMAT = QAudioFormat.Int16  # s16le
BYTES_PER_FRAME = TARGET_CHANNELS * 2  # 2 bytes per sample × 2 channels

# Maximum amount of undelivered PCM we keep in memory before asking the
# decoder to slow down.  ~2 seconds of audio.
MAX_PENDING_BYTES = TARGET_SAMPLE_RATE * BYTES_PER_FRAME * 2

# Decoder-side batch size: accumulate this many PCM bytes before emitting
# a signal to the main thread.  Larger batches reduce UI thread load but
# increase latency.  ~50 ms of audio = 44100 * 4 * 0.05 ≈ 8820 bytes.
PCM_BATCH_SIZE = 8192

# How long the decoder sleeps (in seconds) when the engine's pending buffer
# is full, to avoid flooding the main thread.
DECODER_BACKPRESSURE_SLEEP_S = 0.05

POSITION_UPDATE_INTERVAL_MS = 250


# ---------------------------------------------------------------------------
# Playback state
# ---------------------------------------------------------------------------

class PlayState(enum.IntEnum):
    STOPPED = 0
    PLAYING = 1
    PAUSED = 2


# ===================================================================
# AudioDecoder – background thread
# ===================================================================

class AudioDecoder(QThread):
    """Decodes an audio file in a background thread using PyAV.

    Signals
    -------
    pcm_data_ready(data)
        Emitted whenever a chunk of PCM bytes has been decoded.
    duration_changed(duration_ms)
        Emitted once when the file duration is known.
    finished()
        Emitted when decoding reaches end-of-file.
    error_occurred(message)
        Emitted on any fatal decoding error.
    """

    pcm_data_ready = Signal(bytes)
    duration_changed = Signal(int)
    finished = Signal()
    error_occurred = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._file_path: str = ""
        self._seek_pos_ms: int = -1   # -1 means "no pending seek"
        self._stop_requested: bool = False
        self._mutex = QMutex()

        # -- PCM batching accumulator --
        self._pcm_buf = bytearray()

        # -- Back-pressure: engine sets this to inform the decoder how much
        #    data is still waiting to be consumed.  Updated from main thread.
        self._engine_pending_bytes = 0

    # ------------------------------------------------------------------
    # Public API (thread-safe)
    # ------------------------------------------------------------------

    def configure(self, file_path: str) -> None:
        """Set the file to decode.  Safe to call before start()."""
        self._file_path = file_path

    def request_seek(self, position_ms: int) -> None:
        """Schedule a seek to *position_ms*.  Handled on the next decode loop iteration."""
        with QMutexLocker(self._mutex):
            self._seek_pos_ms = position_ms

    def request_stop(self) -> None:
        """Ask the decoder thread to exit as soon as possible."""
        with QMutexLocker(self._mutex):
            self._stop_requested = True

    def set_pending_bytes(self, count: int) -> None:
        """Thread-safe: update the engine's pending byte count for back-pressure."""
        self._engine_pending_bytes = count

    # ------------------------------------------------------------------
    # QThread run()
    # ------------------------------------------------------------------

    def run(self) -> None:
        try:
            container = av.container.open(self._file_path)

            # --- locate audio stream ---
            audio_stream = next((s for s in container.streams if s.type == "audio"), None)
            if audio_stream is None:
                self.error_occurred.emit("No audio stream found")
                container.close()
                return

            # --- duration ---
            # PyAV duration is in AV_TIME_BASE (microseconds).
            duration_ms = 0
            if container.duration and container.duration > 0:
                duration_ms = int(container.duration // 1000)
            if duration_ms <= 0 and audio_stream.duration and audio_stream.duration > 0:
                duration_ms = int(
                    float(audio_stream.duration * audio_stream.time_base) * 1000
                )
            if duration_ms > 0:
                self.duration_changed.emit(duration_ms)

            # Log stream properties for debugging.
            try:
                logger.info(
                    "Decoding: %s  – fmt=%s layout=%s rate=%d",
                    Path(self._file_path).name,
                    getattr(audio_stream.codec_context, "format", "?"),
                    getattr(audio_stream.codec_context, "layout", "?"),
                    getattr(audio_stream.codec_context, "sample_rate", 0) or getattr(audio_stream, "sample_rate", 0),
                )
            except Exception:
                pass

            # --- resampler for rate changes only (lazy init) ---
            _resampler: Optional[AudioResampler] = None

            def _ensure_resampler() -> AudioResampler:
                nonlocal _resampler
                if _resampler is None:
                    _resampler = AudioResampler(
                        format="s16",
                        layout="stereo",
                        rate=TARGET_SAMPLE_RATE,
                    )
                return _resampler

            # --- decode loop ---
            for packet in container.demux(audio_stream):
                if self._should_stop():
                    break

                self._handle_pending_seek_v2(container, audio_stream)

                for frame in packet.decode():
                    if self._should_stop():
                        break
                    if frame is None:
                        continue

                    # Determine if rate conversion is needed.
                    need_rate_conv = (frame.sample_rate != TARGET_SAMPLE_RATE)

                    if need_rate_conv:
                        # AudioResampler handles rate + format conversion.
                        out_frames = _ensure_resampler().resample(frame)
                        pcm = _frames_to_pcm_bytes(out_frames)
                    else:
                        # Same rate – convert format manually.
                        pcm = _frame_to_s16_packed(frame)
                        if pcm is None:
                            # Unsupported format – fall back to resampler.
                            out_frames = _ensure_resampler().resample(frame)
                            pcm = _frames_to_pcm_bytes(out_frames)

                    if pcm:
                        self._pcm_buf.extend(pcm)

                    # Emit batched PCM when buffer is full enough.
                    if len(self._pcm_buf) >= PCM_BATCH_SIZE:
                        self.pcm_data_ready.emit(bytes(self._pcm_buf))
                        self._pcm_buf.clear()

                # Back-pressure to protect the main thread.
                if self._engine_pending_bytes > MAX_PENDING_BYTES:
                    self.msleep(int(DECODER_BACKPRESSURE_SLEEP_S * 1000))

            # Emit remaining buffered data.
            if self._pcm_buf:
                self.pcm_data_ready.emit(bytes(self._pcm_buf))
                self._pcm_buf.clear()

            # Drain the resampler (only if it was used for rate changes).
            if _resampler is not None:
                try:
                    tail = _resampler.resample(None)
                    if tail:
                        tail_pcm = _frames_to_pcm_bytes(tail)
                        if tail_pcm:
                            self.pcm_data_ready.emit(bytes(tail_pcm))
                except Exception:
                    try:
                        _resampler.flush()
                    except Exception:
                        pass

            container.close()

            if not self._should_stop():
                self.finished.emit()

        except Exception as exc:
            logger.exception("AudioDecoder error")
            self.error_occurred.emit(str(exc))

    # ------------------------------------------------------------------
    # Internal helpers (called from run thread)
    # ------------------------------------------------------------------

    def _should_stop(self) -> bool:
        with QMutexLocker(self._mutex):
            return self._stop_requested

    def _handle_pending_seek(self, container: av.container.Container,
                             stream: av.stream.Stream,
                             resampler: AudioResampler) -> bool:
        """If a seek was requested since the last iteration, perform it.

        Returns ``True`` if a seek was actually performed.
        """
        with QMutexLocker(self._mutex):
            pos = self._seek_pos_ms
            self._seek_pos_ms = -1

        if pos < 0:
            return False

        try:
            seek_ts = int((pos / 1000.0) / stream.time_base)
            container.seek(seek_ts, stream=stream)
            stream.codec_context.flush_buffers()
            self._pcm_buf.clear()
            resampler.flush()
            logger.debug("Seeked to %d ms (timestamp %d)", pos, seek_ts)
            return True
        except Exception as exc:
            logger.warning("Seek to %d ms failed: %s", pos, exc)
            return False

    def _handle_pending_seek_v2(self, container: av.container.Container,
                                 stream: av.stream.Stream) -> bool:
        """Version without resampler parameter – clears buffer on seek."""
        with QMutexLocker(self._mutex):
            pos = self._seek_pos_ms
            self._seek_pos_ms = -1

        if pos < 0:
            return False

        try:
            seek_ts = int((pos / 1000.0) / stream.time_base)
            container.seek(seek_ts, stream=stream)
            stream.codec_context.flush_buffers()
            # Discard pre-seek accumulated PCM.
            self._pcm_buf.clear()
            logger.debug("Seeked to %d ms (timestamp %d)", pos, seek_ts)
            return True
        except Exception as exc:
            logger.warning("Seek to %d ms failed: %s", pos, exc)
            return False


# ===================================================================
# AudioEngine – main-thread playback controller
# ===================================================================

class AudioEngine(QObject):
    """High-level audio playback controller.

    Creates and manages the decoder thread and the audio sink.  All public
    methods are expected to be called from the **main (GUI) thread**.

    Signals
    -------
    position_changed(position_ms)
        Emitted periodically (~250 ms) during playback.
    state_changed(state)
        Emitted when the play state changes (PlayState value).
    duration_changed(duration_ms)
        Emitted when a new file's duration is known.
    track_finished()
        Emitted when the current track reaches natural end-of-file
        (not triggered by an explicit ``stop()`` call).
    error_occurred(message)
        Emitted on errors.
    """

    position_changed = Signal(int)
    state_changed = Signal(int)   # PlayState as int
    duration_changed = Signal(int)
    track_finished = Signal()
    error_occurred = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)

        # -- decoder (created lazily in play()) --
        self._decoder: Optional[AudioDecoder] = None

        # -- audio output --
        self._sink: Optional[QAudioSink] = None
        self._sink_device: Optional[QIODevice] = None

        self._audio_format = QAudioFormat()
        self._audio_format.setSampleRate(TARGET_SAMPLE_RATE)
        self._audio_format.setChannelCount(TARGET_CHANNELS)
        self._audio_format.setSampleFormat(TARGET_SAMPLE_FORMAT)

        # -- state --
        self._state: PlayState = PlayState.STOPPED
        self._volume: float = 1.0
        self._pending_buffer = b""           # PCM overflow not yet written to sink

        # -- seek offset --
        # After seek(ms), `processedUSecs()` starts from 0, so we add this
        # offset to report the correct absolute position.
        self._seek_offset: int = 0

        # -- track sequence counter (prevents _check_finished races) --
        self._track_seq: int = 0
        self._finish_seq: int = -1

        # -- timers --
        self._pos_timer = QTimer(self)
        self._pos_timer.setInterval(POSITION_UPDATE_INTERVAL_MS)
        self._pos_timer.timeout.connect(self._update_position)

        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(60)    # ~16 Hz drain rate
        self._flush_timer.timeout.connect(self._flush_pending)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> PlayState:
        return self._state

    @property
    def volume(self) -> float:
        return self._volume

    # ------------------------------------------------------------------
    # Playback controls (main thread only)
    # ------------------------------------------------------------------

    def play(self, file_path: str) -> None:
        """Load *file_path* and start playback."""
        self._track_seq += 1
        self.stop()

        # Reset seek offset since we're starting a new track from position 0.
        self._seek_offset = 0

        # Create a fresh decoder for each playback (QThread cannot restart).
        self._decoder = AudioDecoder()
        self._decoder.pcm_data_ready.connect(self._on_pcm_data)
        self._decoder.finished.connect(self._on_decoder_finished)
        self._decoder.duration_changed.connect(self._on_duration)
        self._decoder.error_occurred.connect(self._on_error)

        self._decoder.configure(file_path)
        self._init_sink()
        self._decoder.start()
        self._set_state(PlayState.PLAYING)

    def pause(self) -> None:
        """Pause playback.  Call :meth:`resume` to continue."""
        if self._state != PlayState.PLAYING:
            return
        if self._sink is not None:
            self._sink.suspend()
        self._set_state(PlayState.PAUSED)

    def resume(self) -> None:
        """Resume after a pause."""
        if self._state != PlayState.PAUSED:
            return
        if self._sink is not None:
            self._sink.resume()
        self._set_state(PlayState.PLAYING)

    def toggle_play_pause(self) -> None:
        """Convenience: play if paused / paused if playing."""
        if self._state == PlayState.PLAYING:
            self.pause()
        else:
            self.resume()

    def stop(self) -> None:
        """Stop playback and reset the engine."""
        if self._decoder is not None:
            self._decoder.request_stop()
            if not self._decoder.wait(2000):
                logger.warning("AudioDecoder thread did not finish in time – terminating")
                self._decoder.terminate()
                self._decoder.wait()
            self._decoder.deleteLater()
            self._decoder = None

        self._cleanup_sink()
        self._pending_buffer = b""
        self._seek_offset = 0
        self._pos_timer.stop()
        self._flush_timer.stop()
        self._set_state(PlayState.STOPPED)

    def seek(self, position_ms: int) -> None:
        """Seek to *position_ms* in the current file.

        Preserves the paused/playing state — if the engine was paused
        when seek is called the sink is re-suspended after restart so
        that a slider drag does not unexpectedly resume playback.
        """
        if position_ms < 0:
            position_ms = 0
        was_paused = (self._state == PlayState.PAUSED)

        if self._decoder is not None:
            self._decoder.request_seek(position_ms)
        # Stop and restart the sink so it is in a clean Started state
        # ready to accept new PCM data.  On Windows, reset() can leave
        # the sink in an idle state where no audio plays.
        if self._sink is not None:
            self._sink.stop()
        self._pending_buffer = b""
        if self._sink is not None:
            self._sink_device = self._sink.start()
        # After restart, processedUSecs() returns 0, so we offset it.
        self._seek_offset = position_ms

        # Re-apply pause if the engine was paused before the seek.
        if was_paused and self._sink is not None:
            self._sink.suspend()

    def set_volume(self, volume: float) -> None:
        """Set output volume (0.0 … 1.0)."""
        self._volume = max(0.0, min(1.0, volume))
        if self._sink is not None:
            self._sink.setVolume(self._volume)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _set_state(self, state: PlayState) -> None:
        if self._state == state:
            return
        self._state = state
        self.state_changed.emit(int(state))

        if state == PlayState.PLAYING:
            self._pos_timer.start()
            self._flush_timer.start()
        else:
            self._pos_timer.stop()
            self._flush_timer.stop()

    def _init_sink(self) -> None:
        self._cleanup_sink()

        # Validate the audio format before creating the sink.
        if not self._audio_format.isValid():
            logger.error("QAudioFormat is invalid! Falling back to defaults.")
            self._audio_format = QAudioFormat()
            self._audio_format.setSampleRate(TARGET_SAMPLE_RATE)
            self._audio_format.setChannelCount(TARGET_CHANNELS)
            self._audio_format.setSampleFormat(TARGET_SAMPLE_FORMAT)

        # Log the actual format for debugging.
        device = QMediaDevices.defaultAudioOutput()
        logger.debug(
            "Audio format: %d Hz, %d ch, fmt=%s | device=%s",
            self._audio_format.sampleRate(),
            self._audio_format.channelCount(),
            self._audio_format.sampleFormat().name if hasattr(self._audio_format.sampleFormat(), 'name') else str(self._audio_format.sampleFormat()),
            device.description() if device else "none",
        )

        self._sink = QAudioSink(self._audio_format, self)
        self._sink.setVolume(self._volume)
        self._sink_device = self._sink.start()

    def _cleanup_sink(self) -> None:
        if self._sink is not None:
            self._sink.stop()
            self._sink.deleteLater()
            self._sink = None
            self._sink_device = None

    def _on_pcm_data(self, data: bytes) -> None:
        """Slot invoked (in main thread) when decoder emits PCM data.

        Buffers incoming data and eagerly writes to the sink to keep its
        internal buffer full – preventing underruns that cause crackling.
        The flush timer acts as a safety net for any remaining data.
        """
        self._pending_buffer += data
        self._flush_pending()

    def _flush_pending(self) -> None:
        """Write as much pending PCM data as possible to the audio sink."""
        if not self._pending_buffer or self._sink_device is None:
            return

        free = self._sink.bytesFree() if self._sink is not None else 0
        if free <= 0:
            return

        chunk = self._pending_buffer[:free]
        written = self._sink_device.write(chunk)
        if written > 0:
            self._pending_buffer = self._pending_buffer[written:]

        # Inform the decoder how much data is still pending (back-pressure).
        if self._decoder is not None:
            self._decoder.set_pending_bytes(len(self._pending_buffer))

    def _update_position(self) -> None:
        """Emit current playback position based on QAudioSink.processedUSecs + seek offset."""
        if self._sink is not None:
            usecs = self._sink.processedUSecs()
            pos_ms = self._seek_offset + int(usecs // 1000)
            self.position_changed.emit(pos_ms)

    def _on_decoder_finished(self) -> None:
        """Called when the decoder reaches the end of the file.

        We let the pending buffer drain naturally.  Once the sink goes idle
        (or all data is consumed) we stop.
        """
        # Remember which track sequence this finish belongs to, so that
        # _check_finished can detect if a new play() started in the
        # meantime and avoid killing the new track.
        self._finish_seq = self._track_seq

        # We cannot stop immediately – the sink may still be playing buffered
        # PCM data.  Use a short single-shot timer to check later.
        QTimer.singleShot(500, self._check_finished)

    def _check_finished(self) -> None:
        """Stop the engine once the sink has consumed all data."""
        # If a new play() was started since _on_decoder_finished, bail out.
        if self._finish_seq != self._track_seq:
            return
        if self._state != PlayState.PLAYING:
            return
        if self._pending_buffer:
            QTimer.singleShot(200, self._check_finished)
            return
        if self._sink is not None:
            SinkState = type(self._sink.state())
            if self._sink.state() == SinkState.ActiveState:
                QTimer.singleShot(200, self._check_finished)
                return

        # Stop the old engine FIRST.
        self.stop()

        # Emit track_finished ASYNCHRONOUSLY (deferred one tick) to avoid
        # re-entrancy: the slot may immediately call play() for the next
        # track, which sets up a new decoder and sink.  Doing this from
        # within _check_finished's call stack creates a race between the
        # old cleanup code and the new initialisation code.
        QTimer.singleShot(0, self.track_finished.emit)

    def _on_duration(self, duration_ms: int) -> None:
        self.duration_changed.emit(duration_ms)

    def _on_error(self, msg: str) -> None:
        self.stop()
        self.error_occurred.emit(msg)
