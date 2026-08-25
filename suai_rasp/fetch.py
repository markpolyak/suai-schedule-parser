"""HTTP-клиент к https://guap.ru/rasp с файловым кэшем."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import requests

BASE = "https://guap.ru/rasp/"
BASE_COMPACT = "https://guap.ru/rasp_sm/"
UA = "suai-schedule-parser/1.0 (+research; contact: markpolyak@gmail.com)"

DEFAULT_CACHE = Path(__file__).resolve().parent.parent / ".cache"


class Fetcher:
    def __init__(self, cache_dir: Path | str = DEFAULT_CACHE, delay: float = 0.4, ttl: float | None = None):
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.ttl = ttl  # секунды; None = кэш не протухает
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        self._last = 0.0

    def get(self, params: dict, compact: bool = False) -> str:
        url = BASE_COMPACT if compact else BASE
        key = hashlib.sha1(f"{url}?{sorted(params.items())}".encode()).hexdigest()[:16]
        tag = "-".join(f"{k}{v}" for k, v in sorted(params.items())) or "index"
        path = self.cache / f"{tag}-{key}.html"
        if path.exists() and (self.ttl is None or time.time() - path.stat().st_mtime < self.ttl):
            return path.read_text(encoding="utf-8")

        wait = self.delay - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        r = self.session.get(url, params=params, timeout=30)
        self._last = time.time()
        r.raise_for_status()
        r.encoding = "utf-8"
        path.write_text(r.text, encoding="utf-8")
        return r.text

    def index(self) -> str:
        """Стартовая страница — источник справочников."""
        return self.get({})

    def by_chair(self, chair_id: int) -> str:
        return self.get({"ch": chair_id})

    def by_group(self, group_id: int) -> str:
        return self.get({"gr": group_id})

    def by_teacher(self, teacher_id: int) -> str:
        return self.get({"pr": teacher_id})

    def by_room(self, room_id: int) -> str:
        return self.get({"ad": room_id})
