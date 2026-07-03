"""
Playlist manager – maintains the current playback queue, index, and play mode.

Communicates state changes via Qt signals so the UI layer never needs to
poll for the current song or index.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, Signal

from app.models.song import PlayMode, Song
from app.paths import playlists_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Playlist directory helpers
# ---------------------------------------------------------------------------

CURRENT_PLAYLIST_NAME = "current"


def _playlists_dir() -> Path:
    """Return the ``~/.lunaplayer/playlists/`` directory, creating it if needed."""
    return playlists_dir()


class PlaylistManager(QObject):
    """Manages the active playlist and implements playback-mode logic.

    Signals
    -------
    current_index_changed(index)
        Emitted when the current song index changes (including on load).
    current_song_changed(song)
        Emitted when the actual current song object changes (includes ``None``).
    playlist_loaded()
        Emitted after a new playlist has been loaded.
    song_added(index)
        Emitted when a song is inserted into the playlist.
    song_removed(index, song)
        Emitted when a song is removed from the playlist.
        *index* is the song's position **before** removal; *song* is the
        removed :class:`Song` object.
    play_mode_changed(mode)
        Emitted when the play mode changes.
    """

    current_index_changed = Signal(int)
    current_song_changed = Signal(object)  # Song | None
    playlist_loaded = Signal()
    song_added = Signal(int)
    song_removed = Signal(int, object)  # (index_before_removal, removed_song)
    play_mode_changed = Signal(object)  # PlayMode

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)

        self._playlist: List[Song] = []
        self._current_index: int = -1  # -1 means "nothing selected"
        self._play_mode: PlayMode = PlayMode.SEQUENTIAL
        self._session_state: dict[str, object] = {}

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def playlist(self) -> List[Song]:
        """Return the active playlist (read-only copy)."""
        return list(self._playlist)

    @property
    def current_index(self) -> int:
        return self._current_index

    @property
    def play_mode(self) -> PlayMode:
        return self._play_mode

    @property
    def session_state(self) -> dict[str, object]:
        """Return a copy of the saved playback session metadata."""
        return dict(self._session_state)

    # ------------------------------------------------------------------
    # Playlist mutation
    # ------------------------------------------------------------------

    def load_playlist(self, songs: List[Song], start_index: int = 0) -> None:
        """Replace the current playlist with *songs* and optionally jump to *start_index*."""
        self._playlist = list(songs)
        self._current_index = start_index if 0 <= start_index < len(songs) else -1
        self.playlist_loaded.emit()
        self.current_index_changed.emit(self._current_index)
        self.current_song_changed.emit(self._current_song_or_none())

    def add_song(self, song: Song) -> int:
        """Append *song* to the playlist and return its index."""
        self._playlist.append(song)
        index = len(self._playlist) - 1
        self.song_added.emit(index)
        return index

    def reorder_playlist(self, songs: List[Song]) -> None:
        """Replace playlist ordering without restarting the current song."""
        current_song = self._current_song_or_none()
        current_path = current_song.file_path if current_song is not None else ""

        self._playlist = list(songs)
        if current_path:
            self._current_index = next(
                (i for i, song in enumerate(self._playlist) if song.file_path == current_path),
                -1,
            )
        elif not self._playlist:
            self._current_index = -1
        elif not (0 <= self._current_index < len(self._playlist)):
            self._current_index = -1

        self.playlist_loaded.emit()
        self.current_index_changed.emit(self._current_index)

        new_current = self._current_song_or_none()
        new_path = new_current.file_path if new_current is not None else ""
        if new_path != current_path:
            self.current_song_changed.emit(new_current)

    def remove_song(self, index: int) -> Optional[Song]:
        """Remove the song at *index* and return it, or ``None`` if out of range.

        Emits :attr:`song_removed` with the *index* **before** removal and
        the removed :class:`Song` object.
        """
        if not 0 <= index < len(self._playlist):
            return None

        removed = self._playlist.pop(index)

        # Adjust current index
        if self._current_index == index:
            # The currently-playing song was removed
            if index < len(self._playlist):
                pass  # keep pointing at the same slot (now the next song)
            else:
                self._current_index = len(self._playlist) - 1 if self._playlist else -1
            self.current_song_changed.emit(self._current_song_or_none())
        elif self._current_index > index:
            self._current_index -= 1

        self.song_removed.emit(index, removed)
        self.current_index_changed.emit(self._current_index)
        return removed

    def clear(self) -> None:
        """Remove all songs from the playlist."""
        self._playlist.clear()
        self._current_index = -1
        self.playlist_loaded.emit()
        self.current_index_changed.emit(-1)
        self.current_song_changed.emit(None)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def get_current_song(self) -> Optional[Song]:
        """Return the song at *current_index*, or ``None``."""
        return self._current_song_or_none()

    def go_to(self, index: int) -> Optional[Song]:
        """Jump to *index* and return the song there (or ``None``)."""
        if not 0 <= index < len(self._playlist):
            return None
        self._current_index = index
        self.current_index_changed.emit(index)
        song = self._current_song_or_none()
        self.current_song_changed.emit(song)
        return song

    def next(self) -> Optional[Song]:
        """Advance to the next song based on the current play mode."""
        if not self._playlist:
            return None

        if self._play_mode == PlayMode.SINGLE_LOOP:
            # Stay on the same index
            pass

        else:  # SEQUENTIAL or LOOP
            next_idx = self._current_index + 1
            if next_idx >= len(self._playlist):
                if self._play_mode == PlayMode.LOOP:
                    next_idx = 0
                else:
                    # SEQUENTIAL: stop
                    self._current_index = -1
                    self.current_index_changed.emit(-1)
                    self.current_song_changed.emit(None)
                    return None
            self._current_index = next_idx

        self.current_index_changed.emit(self._current_index)
        song = self._current_song_or_none()
        self.current_song_changed.emit(song)
        return song

    def previous(self) -> Optional[Song]:
        """Go back to the previous song."""
        if not self._playlist:
            return None

        if self._current_index < 0:
            # Nothing is selected — jump to the first song.
            return self.go_to(0)

        if self._play_mode == PlayMode.SINGLE_LOOP:
            pass  # stay on same index

        else:  # SEQUENTIAL or LOOP
            prev_idx = self._current_index - 1
            if prev_idx < 0:
                if self._play_mode == PlayMode.LOOP:
                    prev_idx = len(self._playlist) - 1
                else:
                    prev_idx = 0  # stay at first
            self._current_index = prev_idx

        self.current_index_changed.emit(self._current_index)
        song = self._current_song_or_none()
        self.current_song_changed.emit(song)
        return song

    # ------------------------------------------------------------------
    # Play mode
    # ------------------------------------------------------------------

    def set_play_mode(self, mode: PlayMode) -> None:
        """Change the playback mode."""
        if mode == self._play_mode:
            return
        self._play_mode = mode
        self.play_mode_changed.emit(mode)

    def cycle_play_mode(self) -> PlayMode:
        """Rotate through the available play modes and return the new mode."""
        modes = list(PlayMode)
        idx = modes.index(self._play_mode)
        new_mode = modes[(idx + 1) % len(modes)]
        self.set_play_mode(new_mode)
        return new_mode

    def set_session_state(self, state: dict[str, object]) -> None:
        """Store non-playlist playback state to be persisted with the queue."""
        self._session_state = dict(state)

    def clear_session_state(self) -> None:
        """Discard any previously loaded or pending playback session state."""
        self._session_state = {}

    # ------------------------------------------------------------------
    # M3U persistence (class methods)
    # ------------------------------------------------------------------

    def save_to_m3u(self, name: str = CURRENT_PLAYLIST_NAME) -> str:
        """Save the current playlist as an M3U8 file in the playlists directory.

        A companion ``.meta.json`` file stores ``current_index`` and
        ``play_mode``.

        Returns the full path to the saved ``.m3u8`` file.
        """
        import json

        base = _playlists_dir() / name
        m3u_path = base.with_suffix(".m3u8")
        meta_path = base.with_suffix(".meta.json")

        try:
            with open(m3u_path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for song in self._playlist:
                    dur = int(song.duration) if song.duration is not None else -1
                    display = f"{song.artist} - {song.title}" if song.artist else song.title
                    f.write(f"#EXTINF:{dur},{display}\n")
                    f.write(f"{song.file_path}\n")
        except OSError as exc:
            logger.warning("Failed to save M3U: %s", exc)
            return ""

        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                meta = {
                    "current_index": self._current_index,
                    "play_mode": int(self._play_mode),
                }
                meta.update(self._session_state)
                json.dump(meta, f, ensure_ascii=False)
        except OSError as exc:
            logger.warning("Failed to save playlist meta: %s", exc)

        return str(m3u_path)

    def load_from_m3u(self, name: str = CURRENT_PLAYLIST_NAME) -> bool:
        """Load a playlist from an M3U8 file in the playlists directory.

        Returns ``True`` if at least one song was loaded.
        """
        import json

        base = _playlists_dir() / name
        m3u_path = base.with_suffix(".m3u8")
        meta_path = base.with_suffix(".meta.json")

        if not m3u_path.exists():
            return False

        from app.core.music_scanner import extract_metadata

        songs: list[Song] = []
        try:
            lines = m3u_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False

        session_state: dict[str, object] = {}
        meta_index = 0
        mode_val = 0
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            meta_index = meta.get("current_index", 0)
            mode_val = meta.get("play_mode", 0)
            session_state = {
                "play_state": meta.get("play_state", 0),
                "position_ms": meta.get("position_ms", 0),
                "current_file_path": meta.get("current_file_path", ""),
            }
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            path = Path(line)
            if not path.is_absolute():
                path = m3u_path.parent / line
            resolved = str(path.resolve())

            if not Path(resolved).exists():
                logger.info("Skipping missing playlist entry: %s", resolved)
                continue

            try:
                song = extract_metadata(resolved)
                if song is not None:
                    songs.append(song)
            except Exception:
                pass

        if not songs:
            return False

        current_file_path = str(session_state.get("current_file_path") or "")
        if current_file_path:
            resolved_current = str(Path(current_file_path).resolve())
            matching_index = next(
                (i for i, song in enumerate(songs) if song.file_path == resolved_current),
                None,
            )
            if matching_index is not None:
                meta_index = matching_index

        if not (0 <= meta_index < len(songs)):
            meta_index = 0

        self._session_state = session_state
        self.load_playlist(songs, meta_index)
        try:
            self.set_play_mode(PlayMode(mode_val))
        except (ValueError, TypeError):
            pass
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _current_song_or_none(self) -> Optional[Song]:
        if 0 <= self._current_index < len(self._playlist):
            return self._playlist[self._current_index]
        return None
