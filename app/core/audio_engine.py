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


import enum
import logging
from typing import Optional

from PySide6.QtCore import QIODevice, QMutex, QMutexLocker, QObject, QThread, QTimer, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

import av.container
from av.audio.resampler import AudioResampler
from av.error import InvalidDataError

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
        # Seek target applied ONCE at the start of run(), before the decode
        # loop.  No in-flight seek commands — avoids race with finished signal.
        self._start_position_ms: int = 0
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

    def configure(self, file_path: str, start_position_ms: int = 0) -> None:
        """Set the file to decode and an optional start position.

        The decoder does a one-shot seek to *start_position_ms* at the
        beginning of :meth:`run`, before entering the decode loop.  This
        is the only seek mechanism — there is no "request seek mid-decode"
        API because it causes a race with ``finished`` that can kill
        playback (the old "seek → next track" bug).
        """
        self._file_path = file_path
        self._start_position_ms = max(0, start_position_ms)

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
            duration_ms = 0
            if container.duration and container.duration > 0:
                duration_ms = int(container.duration // 1000)
            if duration_ms <= 0 and audio_stream.duration and audio_stream.duration > 0:
                duration_ms = int(
                    float(audio_stream.duration * audio_stream.time_base) * 1000
                )
            if duration_ms > 0:
                self.duration_changed.emit(duration_ms)

            # --- initial seek (if requested) ---
            # This replaces the old "request_seek during decode" approach.
            # Seeking BEFORE the decode loop means:
            #   1. No race with the finished signal.
            #   2. No stale PCM data in the buffer that needs discarding.
            #   3. The decoder simply behaves as if the file started here.
            if self._start_position_ms > 0:
                seek_ts = int((self._start_position_ms / 1000.0) / audio_stream.time_base)
                container.seek(seek_ts, stream=audio_stream)

            # --- log stream properties for debugging ---
            try:
                from pathlib import Path
                _ = Path(self._file_path).name
                logger.info(
                    "Decoding: %s – fmt=%s layout=%s rate=%d start=%dms",
                    _,
                    getattr(audio_stream.codec_context, "format", "?"),
                    getattr(audio_stream.codec_context, "layout", "?"),
                    getattr(audio_stream.codec_context, "sample_rate", 0) or getattr(audio_stream, "sample_rate", 0),
                    self._start_position_ms,
                )
            except Exception:
                pass

            # --- resampler for rate / format conversion (lazy init) ---
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

                # A corrupt packet (e.g. trailing garbage after a truncated
                # MP3) makes avcodec_send_packet return AVERROR_INVALIDDATA,
                # which PyAV surfaces as InvalidDataError while iterating the
                # decoded frames.  Treating that as fatal kills the whole
                # track right at its end, so playback stops with an error
                # instead of advancing to the next song.  Skip the bad packet
                # and keep decoding — this matches ffplay's behaviour.
                try:
                    for frame in packet.decode():
                        if self._should_stop():
                            break
                        if frame is None:
                            continue

                        # Determine if rate conversion is needed.
                        need_rate_conv = (frame.sample_rate != TARGET_SAMPLE_RATE)

                        if need_rate_conv:
                            out_frames = _ensure_resampler().resample(frame)
                            pcm = _frames_to_pcm_bytes(out_frames)
                        else:
                            pcm = _frame_to_s16_packed(frame)
                            if pcm is None:
                                out_frames = _ensure_resampler().resample(frame)
                                pcm = _frames_to_pcm_bytes(out_frames)

                        if pcm:
                            self._pcm_buf.extend(pcm)

                        # Emit batched PCM when buffer is full enough.
                        if len(self._pcm_buf) >= PCM_BATCH_SIZE:
                            self.pcm_data_ready.emit(bytes(self._pcm_buf))
                            self._pcm_buf.clear()
                except InvalidDataError as exc:
                    # Packet-level corruption is not fatal — log and move on
                    # to the next packet.
                    logger.warning(
                        "Skipping undecodable packet in %s: %s",
                        self._file_path, exc,
                    )
                    continue

                # Back-pressure to protect the main thread.
                if self._engine_pending_bytes > MAX_PENDING_BYTES:
                    self.msleep(int(DECODER_BACKPRESSURE_SLEEP_S * 1000))

            # Emit remaining buffered data.
            if self._pcm_buf:
                self.pcm_data_ready.emit(bytes(self._pcm_buf))
                self._pcm_buf.clear()

            # Drain the resampler.
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
        """Resume after a pause.

        If the decoder reached EOF while we were paused, restart the
        end-of-track drain polling so the track can finish naturally.
        """
        if self._state != PlayState.PAUSED:
            return
        if self._sink is not None:
            self._sink.resume()
        self._set_state(PlayState.PLAYING)

        # The decoder may have finished while we were paused.  In that
        # case _check_finished already ran once and returned early
        # (because the state was PAUSED).  Kick it again so the
        # remaining buffered data can drain and trigger track_finished.
        if self._finish_seq == self._track_seq:
            self._check_finished()

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

        **Why restart the whole decoder?**  The old approach tried to send a
        seek command to a running decoder thread while simultaneously
        stop/starting the QAudioSink.  Those two async operations raced with
        the decoder's ``finished`` signal — if the decoder reached EOF right
        after the seek, ``_check_finished()`` would call ``stop()`` and kill
        the new sink, making it look like the track skipped to the next song.

        By tearing everything down and creating a fresh decoder with the
        seek position baked into ``configure()``, the ``finished`` signal
        from the old decoder is disconnected before it can fire, and the new
        decoder starts cleanly from the target position.  ``_track_seq``
        provides a second layer of defence: even if a stale signal somehow
        arrives, the sequence mismatch causes ``_check_finished()`` to bail.
        """
        if position_ms < 0:
            position_ms = 0
        if self._decoder is None:
            return

        # Remember the file path before killing the old decoder.
        current_file = self._decoder._file_path
        was_paused = (self._state == PlayState.PAUSED)

        # --- 1. Bump sequence BEFORE teardown so any stale finished signal
        #     from the old decoder is ignored by _check_finished ---
        self._track_seq += 1

        # --- 2. Disconnect old decoder signals so they cannot fire
        #     after the new decoder is already connected ---
        self._decoder.pcm_data_ready.disconnect(self._on_pcm_data)
        self._decoder.finished.disconnect(self._on_decoder_finished)
        self._decoder.duration_changed.disconnect(self._on_duration)
        self._decoder.error_occurred.disconnect(self._on_error)

        # --- 3. Tear down old decoder ---
        self._decoder.request_stop()
        self._decoder.wait(2000)
        self._decoder.deleteLater()
        self._decoder = None

        # --- 4. Reset sink (must be stopped before starting anew) ---
        if self._sink is not None:
            self._sink.stop()
        self._pending_buffer = b""
        self._seek_offset = position_ms

        # --- 5. Create new decoder that starts from position_ms ---
        self._decoder = AudioDecoder()
        self._decoder.pcm_data_ready.connect(self._on_pcm_data)
        self._decoder.finished.connect(self._on_decoder_finished)
        self._decoder.duration_changed.connect(self._on_duration)
        self._decoder.error_occurred.connect(self._on_error)
        self._decoder.configure(current_file, start_position_ms=position_ms)

        # --- 6. Restart sink and decoder ---
        if self._sink is not None:
            self._sink_device = self._sink.start()
        self._decoder.start()

        # --- 7. Restore pause state ---
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
