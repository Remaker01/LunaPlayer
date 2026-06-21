"""
MusicProvider – online music search & download (90svip API).
Uses ``httpcore`` with HTTP/2. All I/O runs in background QThreads.
"""

from __future__ import annotations

import json, logging, re
from pathlib import Path
from urllib.parse import urljoin

import httpcore
from PySide6.QtCore import QObject, QThread, Signal

from app.models.song import Song

logger = logging.getLogger(__name__)

BASE_URL = "https://music.90svip.cn/"
UA = ("Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Mobile Safari/537.36")


def _headers(extra: dict | None = None) -> dict:
    h = {"User-Agent": UA, "Accept": "application/json, text/javascript, */*; q=0.01",
         "Accept-Encoding": "gzip, deflate, br", "Origin": BASE_URL.rstrip("/"),
         "Referer": BASE_URL, "X-Requested-With": "XMLHttpRequest"}
    if extra: h.update(extra)
    return h


def _abs(rel: str) -> str:
    if not rel or rel.startswith(("http://", "https://")): return rel
    return urljoin(BASE_URL, rel)


def _build_song(item: dict) -> Song | None:
    title = (item.get("name") or "").strip()
    if not title: return None
    return Song(title=title, artist=(item.get("artist") or "").strip() or "未知艺术家",
                file_path=_abs(item.get("url", "")), file_format="mp3",
                duration=float(item.get("duration") or 0))


def _sanitise(name: str) -> str:
    # Replace illegal filename characters with '_'.
    # Keep trailing spaces/dots — they are valid in most FS but we strip
    # leading/trailing whitespace for convenience.
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


# ---------------------------------------------------------------------------
# Generic worker helper – reduces boilerplate for Search/Download/Lyrics
# ---------------------------------------------------------------------------

class _Worker(QThread):
    """Minimal QThread wrapper. Implement ``run_task()`` in subclasses."""

    # Use a name that does NOT shadow QThread.finished so the built-in
    # signal (which fires on thread exit regardless of success/error)
    # remains available for cleanup in MusicProvider._run().
    task_done = Signal(object)                    # type varies per subclass
    error = Signal(str)

    def run_task(self) -> None: ...               # override in subclass

    def run(self) -> None:
        try:
            self.run_task()
        except Exception as exc:
            logger.exception("%s failed", type(self).__name__)
            self.error.emit(str(exc))


class _SearchWorker(_Worker):
    task_done = Signal(list)   # List[Song]
    def __init__(self, keyword: str, page: int, source: str,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._keyword, self._page, self._source = keyword, page, source

    def run_task(self) -> None:
        with httpcore.ConnectionPool(http2=True, http1=False) as pool:
            body = f"input={self._keyword}&filter=name&type={self._source}&page={self._page}".encode()
            resp = pool.request("POST", BASE_URL,
                headers=_headers({"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                                  "Referer": f"{BASE_URL}?name={self._keyword}&type={self._source}"}),
                content=body)
        if resp.status != 200: self.error.emit(f"HTTP {resp.status}"); return
        payload = json.loads(resp.content)
        if payload.get("code") != 200: self.error.emit(payload.get("error", "?")); return
        songs = [s for item in payload.get("data", []) if (s := _build_song(item))]
        self.task_done.emit(songs)


class _DownloadWorker(_Worker):
    task_done = Signal(str)   # local path
    def __init__(self, url: str, save_path: str,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._url, self._save_path = url, save_path

    def run_task(self) -> None:
        with httpcore.ConnectionPool() as pool:
            url = self._url
            for _ in range(5):
                resp = pool.request("GET", url, headers=_headers({"Referer": BASE_URL}))
                if resp.status == 200: break
                if resp.status in (301, 302, 303, 307, 308):
                    loc = next((v.decode() for n, v in resp.headers if n.lower() == b"location"), None)
                    if loc: url = urljoin(url, loc); continue
                self.error.emit(f"HTTP {resp.status}"); return
            else: self.error.emit("Too many redirects"); return
            Path(self._save_path).parent.mkdir(parents=True, exist_ok=True)
            Path(self._save_path).write_bytes(resp.content)
        self.task_done.emit(self._save_path)


class _LyricsWorker(_Worker):
    task_done = Signal(str)   # LRC text
    def __init__(self, lrc_url: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lrc_url = lrc_url

    def run_task(self) -> None:
        with httpcore.ConnectionPool() as pool:
            resp = pool.request("GET", self._lrc_url, headers=_headers({"Referer": BASE_URL}))
        if resp.status == 200:
            self.task_done.emit(resp.content.decode("utf-8", errors="replace"))
        else:
            self.error.emit(f"HTTP {resp.status}")


# ===================================================================
# MusicProvider – main-thread API
# ===================================================================

class MusicProvider(QObject):
    """Search, download & lyrics.  All operations are async (QThread)."""

    results_ready = Signal(list)        # List[Song]
    search_error = Signal(str)
    download_ready = Signal(object, str)  # Song, local_path
    download_error = Signal(object, str)  # Song, message
    lyrics_ready = Signal(str)
    lyrics_error = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._workers: list[QThread] = []                     # keep GC alive

    def _run(self, w: QThread) -> None:
        # QThread.finished (built-in) fires when the thread exits
        # regardless of success or failure — ensures cleanup always runs.
        w.finished.connect(lambda: self._workers.remove(w))
        self._workers.append(w)
        w.start()

    def search(self, keyword: str, page: int = 1,
               source: str = "netease") -> None:
        w = _SearchWorker(keyword, page, source, self)
        w.task_done.connect(self.results_ready.emit)
        w.error.connect(self.search_error.emit)
        self._run(w)

    def download(self, song: Song, save_dir: str) -> None:
        safe_name = _sanitise(f"{song.artist} - {song.title}") or song.title
        path = str(Path(save_dir) / f"{safe_name}.mp3")
        w = _DownloadWorker(song.file_path, path, self)
        w.task_done.connect(lambda p: self.download_ready.emit(song, p))
        w.error.connect(lambda m: self.download_error.emit(song, m))
        self._run(w)

    def fetch_lyrics(self, lrc_url: str) -> None:
        w = _LyricsWorker(lrc_url, self)
        w.task_done.connect(self.lyrics_ready.emit)
        w.error.connect(self.lyrics_error.emit)
        self._run(w)
