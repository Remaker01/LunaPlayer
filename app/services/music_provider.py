"""
MusicProvider – online music search & download (90svip API).
Uses ``urllib3``. All I/O runs in background QThreads.
"""

from __future__ import annotations

import logging, re
from pathlib import Path
from urllib.parse import quote, unquote, urlencode, urljoin, urlparse

import urllib3
from PySide6.QtCore import QObject, QThread, Signal

from app.models.song import Song

logger = logging.getLogger(__name__)

BASE_URL = "https://music.90svip.cn/"
UA = ("Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Mobile Safari/537.36")
HTTP = urllib3.PoolManager(timeout=20.0, retries=3, maxsize=10)
_CONTENT_TYPE_FORMATS = {
    "audio/aac": "aac",
    "audio/flac": "flac",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/opus": "opus",
    "audio/wav": "wav",
    "audio/x-aac": "aac",
    "audio/x-flac": "flac",
    "audio/x-m4a": "m4a",
    "audio/x-ms-wma": "wma",
    "audio/x-wav": "wav",
}
_FORMAT_ALIASES = {
    "mpeg": "mp3",
    "mp4": "m4a",
    "x-flac": "flac",
    "x-m4a": "m4a",
    "x-ms-wma": "wma",
    "x-wav": "wav",
}

def _headers(extra: dict | None = None) -> dict:
    h = {"User-Agent": UA, "Accept": "application/json, text/javascript, */*; q=0.01",
         "Accept-Encoding": "gzip, deflate", "Origin": BASE_URL.rstrip("/"),
         "Referer": BASE_URL, "X-Requested-With": "XMLHttpRequest"}
    if extra:
        h.update(extra)
    return h


def _abs(rel: str) -> str:
    if not rel or rel.startswith(("http://", "https://")):
        return rel
    return urljoin(BASE_URL, rel)


def _build_song(item: dict) -> Song | None:
    title = (item.get("name") or "").strip()
    if not title:
        return None
    file_format = (
        _normalise_format(item.get("format") or item.get("file_format"))
        or _format_from_url(item.get("url", ""))
        or "mp3"
    )
    return Song(title=title, artist=(item.get("artist") or "").strip() or "未知艺术家",
                file_path=_abs(item.get("url", "")), file_format=file_format,
                duration=float(item.get("duration") or 0))


def _sanitise(name: str) -> str:
    # Replace illegal filename characters with '_'.
    # Keep trailing spaces/dots — they are valid in most FS but we strip
    # leading/trailing whitespace for convenience.
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def _normalise_format(value: object) -> str:
    if value is None:
        return ""
    cleaned = str(value).strip().lower().lstrip(".")
    cleaned = cleaned.split(";", 1)[0].strip()
    cleaned = _FORMAT_ALIASES.get(cleaned, cleaned)
    if not re.fullmatch(r"[a-z0-9]{1,8}", cleaned):
        return ""
    return cleaned


def _format_from_url(url: str) -> str:
    suffix = Path(unquote(urlparse(url).path)).suffix
    return _normalise_format(suffix)


def _format_from_content_disposition(value: str | None) -> str:
    if not value:
        return ""
    match = re.search(r"filename\*\s*=\s*(?:UTF-8'')?([^;]+)", value, re.IGNORECASE)
    if match:
        filename = unquote(match.group(1).strip().strip('"'))
        return _normalise_format(Path(filename).suffix)
    match = re.search(r'filename\s*=\s*"?([^";]+)"?', value, re.IGNORECASE)
    if not match:
        return ""
    return _normalise_format(Path(match.group(1).strip()).suffix)


def _format_from_content_type(value: str | None) -> str:
    if not value:
        return ""
    content_type = value.split(";", 1)[0].strip().lower()
    if content_type in _CONTENT_TYPE_FORMATS:
        return _CONTENT_TYPE_FORMATS[content_type]
    if content_type.startswith("audio/"):
        return _normalise_format(content_type.split("/", 1)[1])
    return ""


def _format_from_magic(data: bytes) -> str:
    if data.startswith(b"ID3") or data[:2] == b"\xff\xfb" or data[:2] == b"\xff\xf3":
        return "mp3"
    if data.startswith(b"fLaC"):
        return "flac"
    if data.startswith(b"OggS"):
        return "ogg"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "wav"
    if data.startswith(b"FORM") and data[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "m4a"
    if data.startswith(b"\x30\x26\xb2\x75\x8e\x66\xcf\x11"):
        return "wma"
    return ""


def _header_value(headers: object, name: str) -> str | None:
    get_header = getattr(headers, "get", None)
    value = get_header(name) if get_header else None
    if value is not None:
        return str(value)
    items = getattr(headers, "items", None)
    if not items:
        return None
    wanted = name.lower()
    for key, item_value in items():
        if str(key).lower() == wanted:
            return str(item_value)
    return None


def _select_audio_format(headers: object, url: str, first_chunk: bytes,
                         fallback: str = "") -> str:
    content_disposition = _header_value(headers, "Content-Disposition")
    content_type = _header_value(headers, "Content-Type")
    fallback_format = _normalise_format(fallback)
    return (
        _format_from_content_disposition(content_disposition)
        or _format_from_magic(first_chunk)
        or _format_from_content_type(content_type)
        or _format_from_url(url)
        or fallback_format
        or "mp3"
    )


# ---------------------------------------------------------------------------
# Generic worker helper – reduces boilerplate for Search/Download/Lyrics
# ---------------------------------------------------------------------------

class _Worker(QThread):
    """Minimal QThread wrapper. Implement ``run_task()`` in subclasses."""

    # Use a name that does NOT shadow QThread.finished so the built-in
    # signal (which fires on thread exit regardless of success/error)
    # remains available for cleanup in MusicProvider._run().
    task_done = Signal(object)                    # type varies per subclass
    error = Signal(str, str)   # (category, detail) — "network" or "server"

    def run_task(self) -> None: ...               # override in subclass

    def run(self) -> None:
        try:
            self.run_task()
        except urllib3.exceptions.MaxRetryError:
            logger.exception("%s failed (network)", type(self).__name__)
            self.error.emit("network", "无法连接到服务器，请检查网络")
        except (urllib3.exceptions.ReadTimeoutError,
                ConnectionError, TimeoutError, OSError):
            logger.exception("%s failed (network)", type(self).__name__)
            self.error.emit("network", "网络连接异常，请稍后重试")
        except Exception as exc:
            logger.exception("%s failed", type(self).__name__)
            self.error.emit("server", str(exc))


class _SearchWorker(_Worker):
    task_done = Signal(list)   # List[Song]
    def __init__(self, keyword: str, page: int, source: str,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._keyword, self._page, self._source = keyword, page, source

    def run_task(self) -> None:
        params = {"input": self._keyword, "filter": "name",
                    "type": self._source, "page": self._page}
        body = urlencode(params).encode()
        referer = f"{BASE_URL}?name={quote(self._keyword, safe='')}&type={self._source}"
        resp = HTTP.request("POST", BASE_URL,
            headers=_headers({"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                                "Referer": referer}),
            body=body)
        if resp.status != 200:
            self.error.emit("server", str(resp.status)); return
        payload = resp.json()
        if payload.get("code") != 200:
            self.error.emit("server", payload.get("error", "?")); return
        songs = [s for item in payload.get("data", []) if (s := _build_song(item))]
        self.task_done.emit(songs)


class _DownloadWorker(_Worker):
    task_done = Signal(str, str)   # local path, detected format
    def __init__(self, url: str, save_dir: str, base_name: str, fallback_format: str,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._url = url
        self._save_dir = save_dir
        self._base_name = base_name
        self._fallback_format = fallback_format

    def run_task(self) -> None:
        resp = HTTP.request("GET", self._url,
            headers=_headers({"Referer": BASE_URL, "Accept": "*/*"}),
            preload_content=False,
            decode_content=True)
        try:
            if resp.status != 200:
                self.error.emit("server", f"HTTP {resp.status}")
                return
            chunks = resp.stream(65536, decode_content=True)
            first_chunk = next(chunks, b"")
            file_format = _select_audio_format(
                resp.headers,
                self._url,
                first_chunk,
                self._fallback_format,
            )
            save_path = Path(self._save_dir) / f"{self._base_name}.{file_format}"
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with save_path.open("wb") as output:
                if first_chunk:
                    output.write(first_chunk)
                for chunk in chunks:
                    output.write(chunk)
        finally:
            resp.release_conn()
        self.task_done.emit(str(save_path), file_format)


class _LyricsWorker(_Worker):
    task_done = Signal(str)   # LRC text
    def __init__(self, lrc_url: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lrc_url = lrc_url

    def run_task(self) -> None:
        resp = HTTP.request("GET", self._lrc_url,
            headers=_headers({"Referer": BASE_URL, "Accept": "*/*"})
        )
        if resp.status == 200:
            self.task_done.emit(resp.data.decode("utf-8", errors="replace"))
        else:
            self.error.emit("server", f"HTTP {resp.status}")


# ===================================================================
# MusicProvider – main-thread API
# ===================================================================

class MusicProvider(QObject):
    """Search, download & lyrics.  All operations are async (QThread)."""

    results_ready = Signal(list)        # List[Song]
    search_error = Signal(str, str)   # (category, detail)
    download_ready = Signal(object, str)  # Song, local_path
    download_error = Signal(object, str)  # Song, message
    lyrics_ready = Signal(str)
    lyrics_error = Signal(str, str)   # (category, detail)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._workers: list[QThread] = []                     # keep GC alive

    def _run(self, w: QThread) -> None:
        # Append *before* connecting finished so the lambda can safely remove.
        self._workers.append(w)
        w.finished.connect(lambda: self._workers.remove(w))
        w.start()

    def search(self, keyword: str, page: int = 1,
               source: str = "netease") -> None:
        w = _SearchWorker(keyword, page, source, self)
        w.task_done.connect(self.results_ready.emit)
        w.error.connect(self.search_error.emit)
        self._run(w)

    def download(self, song: Song, save_dir: str) -> None:
        safe_name = _sanitise(f"{song.artist} - {song.title}") or song.title
        w = _DownloadWorker(song.file_path, save_dir, safe_name, song.file_format, self)
        w.task_done.connect(lambda p, fmt: self._on_download_done(song, p, fmt))
        w.error.connect(lambda cat, msg: self.download_error.emit(song, msg))
        self._run(w)

    def fetch_lyrics(self, lrc_url: str) -> None:
        w = _LyricsWorker(lrc_url, self)
        w.task_done.connect(self.lyrics_ready.emit)
        w.error.connect(lambda cat, msg: self.lyrics_error.emit(cat, msg))
        self._run(w)

    def _on_download_done(self, song: Song, local_path: str, file_format: str) -> None:
        song.file_format = file_format
        self.download_ready.emit(song, local_path)
