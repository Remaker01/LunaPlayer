#!/usr/bin/env python3
"""
LunaPlayer – Web-based test interface (FastAPI).

Usage
-----
    python webui.py [<music-directory>]

Opens a browser at http://localhost:8765 with a remote control for the music
player.  If a directory is provided on the command line it is used as the
initial music folder; otherwise you can set it from the web UI.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from PySide6.QtCore import QCoreApplication, QTimer

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.core.audio_engine import AudioEngine, PlayState
from app.core.music_scanner import SUPPORTED_EXTENSIONS, extract_metadata
from app.core.playlist_manager import PlaylistManager
from app.models.song import PlayMode, Song

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PORT = 8765
POLL_INTERVAL_MS = 50
STATUS_UPDATE_MS = 300
CONFIG_FILE = _PROJECT_ROOT / ".webui_config.json"

PLAY_MODE_NAMES = {
    PlayMode.SEQUENTIAL: "sequential",
    PlayMode.LOOP: "loop",
    PlayMode.SINGLE_LOOP: "single-loop",
}
MODE_NAME_TO_ENUM = {v: k for k, v in PLAY_MODE_NAMES.items()}
STATE_NAMES = {0: "stopped", 1: "playing", 2: "paused"}

# ---------------------------------------------------------------------------
# Shared state (bridge between Qt main thread and FastAPI worker thread)
# ---------------------------------------------------------------------------

status: Dict[str, Any] = {
    "state": "stopped",
    "position_ms": 0,
    "duration_ms": 0,
    "volume": 100,
    "mode": "sequential",
    "current_index": -1,
    "song_title": None,
    "song_artist": None,
    "songs": [],
    "music_dir": "",
}

cmd_queue: Queue = Queue()
_scanning: bool = False

# ===================================================================
# FastAPI app
# ===================================================================

app = FastAPI(title="LunaPlayer")

WEBUI_DIR = _PROJECT_ROOT / "test_webui"


class SetPathBody(BaseModel):
    path: str


@app.get("/api/status")
def get_status() -> Dict[str, Any]:
    return status


@app.get("/api/songs")
def get_songs() -> list:
    return status.get("songs", [])


@app.get("/api/getpath")
def get_path() -> Dict[str, str]:
    return {"path": status.get("music_dir", "")}


@app.post("/api/setpath")
def set_path(body: SetPathBody) -> Dict[str, Any]:
    p = body.path.strip()
    if not os.path.isdir(p):
        raise HTTPException(400, f"Not a valid directory: {p}")
    status["music_dir"] = os.path.abspath(p)
    _save_config()
    # Trigger scan in the backend.
    cmd_queue.put(("scan", ""))
    return {"ok": True, "path": status["music_dir"]}


@app.post("/api/{command}")
@app.post("/api/{command}/{arg}")
def post_command(command: str, arg: str = "") -> Dict[str, bool]:
    cmd_queue.put((command, arg))
    return {"ok": True}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEBUI_DIR / "index.html")


if WEBUI_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEBUI_DIR)), name="static")


# ===================================================================
# Config persistence
# ===================================================================

def _load_config() -> Dict[str, Any]:
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_config() -> None:
    try:
        CONFIG_FILE.write_text(
            json.dumps({"music_dir": status.get("music_dir", "")},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


# ===================================================================
# Qt backend
# ===================================================================

class LunaPlayerBackend:
    """Wires core modules and bridges HTTP requests to the Qt main thread."""

    def __init__(self, cli_music_dir: Optional[str] = None) -> None:
        self._app = QCoreApplication.instance() or QCoreApplication([])

        # Playlist
        self._playlist = PlaylistManager()

        # Audio engine
        self._engine = AudioEngine()
        self._engine.position_changed.connect(self._on_position)
        self._engine.state_changed.connect(self._on_state)
        self._engine.duration_changed.connect(self._on_duration)
        self._engine.error_occurred.connect(self._on_error)

        # Restore config (CLI arg overrides saved config).
        config = _load_config()
        music_dir = cli_music_dir or config.get("music_dir", "")
        self._set_music_dir(music_dir)

        self._refresh_song_list()

        # Timers
        self._status_timer = QTimer()
        self._status_timer.setInterval(STATUS_UPDATE_MS)
        self._status_timer.timeout.connect(self._tick_status)
        self._status_timer.start()

        self._cmd_timer = QTimer()
        self._cmd_timer.setInterval(POLL_INTERVAL_MS)
        self._cmd_timer.timeout.connect(self._tick_commands)
        self._cmd_timer.start()

    # ------------------------------------------------------------------
    # Music directory
    # ------------------------------------------------------------------

    def _set_music_dir(self, path: str) -> None:
        if path and os.path.isdir(path):
            status["music_dir"] = os.path.abspath(path)
        else:
            status["music_dir"] = ""

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    def _tick_commands(self) -> None:
        try:
            while True:
                cmd, arg = cmd_queue.get_nowait()
                self._execute(cmd, arg)
        except Empty:
            pass

    def _execute(self, cmd: str, arg: str) -> None:
        if cmd == "scan":
            return self._do_scan()
        if cmd == "play":
            self._do_play(arg)
            return
        if cmd == "pause":
            return self._engine.pause()
        if cmd == "toggle":
            return self._engine.toggle_play_pause()
        if cmd == "stop":
            return self._engine.stop()
        if cmd == "next":
            return self._play_current() if self._playlist.next() else None
        if cmd == "prev":
            return self._play_current() if self._playlist.previous() else None
        if cmd == "vol":
            try:
                self._engine.set_volume(int(arg) / 100.0)
            except ValueError:
                pass
            return
        if cmd == "seek":
            try:
                self._engine.seek(int(float(arg) * 1000))
            except ValueError:
                pass
            return
        if cmd == "mode":
            mode = MODE_NAME_TO_ENUM.get(arg)
            if mode is not None:
                self._playlist.set_play_mode(mode)
            return

    def _do_scan(self) -> None:
        global _scanning
        if _scanning:
            return
        music_dir = status.get("music_dir", "")
        if not music_dir or not os.path.isdir(music_dir):
            return
        _scanning = True
        songs: list[Song] = []
        seen_paths: set[str] = set()
        for root, _dirs, files in os.walk(music_dir):
            for name in files:
                if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                file_path = str((Path(root) / name).resolve())
                if file_path in seen_paths:
                    continue
                song = extract_metadata(file_path)
                if song is None:
                    continue
                seen_paths.add(file_path)
                songs.append(song)

        self._playlist.load_playlist(songs, start_index=0 if songs else -1)
        setattr(self, "_scan_done", len(songs))

    def _do_play(self, arg: str) -> None:
        if arg:
            try:
                idx = int(arg)
            except ValueError:
                return
            songs = self._playlist.playlist
            if 0 <= idx < len(songs):
                self._playlist.load_playlist(songs, start_index=idx)
                self._play_current()
            return
        if self._engine.state == PlayState.PAUSED:
            self._engine.resume()
        else:
            self._play_current()

    def _play_current(self) -> None:
        song = self._playlist.get_current_song()
        if song is None or not os.path.isfile(song.file_path):
            return
        self._engine.play(song.file_path)

    def _refresh_song_list(self) -> None:
        songs = self._playlist.playlist
        status["songs"] = [
            {"id": s.id, "title": s.title, "artist": s.artist,
             "album": s.album, "duration": s.duration}
            for s in songs
        ]

    # ------------------------------------------------------------------
    # Qt signal handlers
    # ------------------------------------------------------------------

    def _on_position(self, pos_ms: int) -> None:
        status["position_ms"] = pos_ms

    def _on_state(self, state: int) -> None:
        status["state"] = STATE_NAMES.get(state, "stopped")

    def _on_duration(self, dur_ms: int) -> None:
        status["duration_ms"] = dur_ms

    def _on_error(self, msg: str) -> None:
        print(f"[ERR] {msg}")

    def _tick_status(self) -> None:
        global _scanning
        # Check if scan just finished.
        if hasattr(self, "_scan_done") and self._scan_done is not None:
            imported = self._scan_done
            self._scan_done = None
            _scanning = False
            self._refresh_song_list()
            print(f"[Scan] {imported} new songs imported")

        status["volume"] = int(self._engine.volume * 100)
        status["mode"] = PLAY_MODE_NAMES.get(self._playlist.play_mode, "sequential")
        status["current_index"] = self._playlist.current_index
        song = self._playlist.get_current_song()
        status["song_title"] = song.title if song else None
        status["song_artist"] = (song.artist or "—") if song else None

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> None:
        import uvicorn

        cfg = uvicorn.Config(app, host="0.0.0.0", port=PORT,
                             log_level="warning", access_log=False)
        server = uvicorn.Server(cfg)
        t = Thread(target=server.run, daemon=True)
        t.start()

        print(f"[LunaPlayer]  http://localhost:{PORT}")
        if status.get("music_dir"):
            print(f"  Music dir: {status['music_dir']}")
        else:
            print("  No music dir set – configure it in the web UI.")
        print("  Press Ctrl+C to stop.")

        try:
            while True:
                self._app.processEvents()
                time.sleep(0.05)
        except KeyboardInterrupt:
            pass
        finally:
            server.should_exit = True
            t.join(timeout=3)
            self._engine.stop()
            print("\nBye!")


# ===================================================================
# Entry point
# ===================================================================

def main() -> None:
    cli_dir = sys.argv[1] if len(sys.argv) > 1 else None
    if cli_dir and not os.path.isdir(cli_dir):
        print(f"[ERR] Not a directory: {cli_dir}")
        print("Starting without a music directory. Set it in the web UI.")
        cli_dir = None

    LunaPlayerBackend(cli_dir).run()


if __name__ == "__main__":
    main()
