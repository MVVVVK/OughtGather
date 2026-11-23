"""按内容源维护抓取 URL 快照的去重追踪器。"""

import os
import tempfile
import threading
import time
from typing import Dict, Iterable, Set

from src.utils.helpers import generate_content_id
from src.utils.logger import get_logger


class DedupTracker:
    """记录每个内容源最近一次成功抓取到的 URL 集合。

    设计与文件格式见 docs/DEDUP.md。
    """

    def __init__(self, data_file: str = "data/fetched_urls.txt"):
        self.data_file = data_file
        self.logger = get_logger()
        self.source_ids: Dict[str, Set[str]] = {}
        self._pending_snapshots: Dict[str, Set[str]] = {}
        self._pending_snapshot_stats: Dict[str, dict] = {}
        self.fetched_ids: Set[str] = set()
        self.new_ids: Set[str] = set()
        self._session_new_ids: Set[str] = set()
        self._session_snapshot_added = 0
        self._session_snapshot_removed = 0
        self._lock = threading.Lock()
        self.retry_records = {}  # (source, URL hash) -> (attempts, last attempt UTC epoch)
        self._dirty = False
        self._load()

    @staticmethod
    def make_source_key(source) -> str:
        return source if isinstance(source, str) else f"{source.type}:{source.src}"

    def _refresh_fetched_ids(self):
        self.fetched_ids = set().union(*self.source_ids.values()) if self.source_ids else set()

    def _load(self):
        if not os.path.exists(self.data_file):
            self.logger.info(f"No existing dedup file found at {self.data_file}")
            return
        try:
            loaded = 0
            with open(self.data_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.rstrip("\n")
                    if not line.strip():
                        continue
                    if "\t" not in line:
                        raise ValueError("dedup file contains an unsupported legacy record")
                    parts = line.split("\t")
                    if parts[0] == "@retry" and len(parts) == 5:
                        _, source_key, content_id, count, last = parts
                        self.retry_records[(source_key, content_id)] = (int(count), float(last))
                        continue
                    if len(parts) != 2:
                        raise ValueError("unsupported dedup record")
                    source_key, content_id = parts
                    if source_key and content_id:
                        self.source_ids.setdefault(source_key, set()).add(content_id)
                        loaded += 1
            with self._lock:
                self._refresh_fetched_ids()
            self.logger.info(
                f"去重记录加载完成: 运行开始时={sum(len(ids) for ids in self.source_ids.values())} 条"
            )
        except Exception as e:
            self.logger.exception(f"去重记录加载失败: {e}")
            raise

    def is_fetched(self, url: str, source_key: str) -> bool:
        if not url:
            return False
        with self._lock:
            return generate_content_id(url) in self.source_ids.get(source_key, set())

    def retry_status(self, url, source_key, now=None):
        """Return None, retry_cooldown, or retry_exhausted for a failed item."""
        with self._lock:
            record = self.retry_records.get((source_key, generate_content_id(url)))
        if not record:
            return None
        count, last = record
        if count >= 3:
            return "retry_exhausted"
        if (time.time() if now is None else now) - last < 86400:
            return "retry_cooldown"
        return None

    def retry_count(self, url, source_key):
        with self._lock:
            return self.retry_records.get((source_key, generate_content_id(url)), (0, 0))[0]

    def record_failure(self, url, source_key, now=None):
        if not url:
            return
        key = (source_key, generate_content_id(url))
        with self._lock:
            count, _ = self.retry_records.get(key, (0, 0))
            self.retry_records[key] = (min(count + 1, 3), time.time() if now is None else now)
            self._dirty = True

    def clear_retry(self, url, source_key):
        key = (source_key, generate_content_id(url))
        with self._lock:
            if self.retry_records.pop(key, None) is not None:
                self._dirty = True

    def reset_retries(self):
        with self._lock:
            if self.retry_records:
                self.retry_records.clear()
                self._dirty = True

    def mark_as_fetched(self, url: str, source_key: str):
        if not url:
            return
        content_id = generate_content_id(url)
        with self._lock:
            ids = self.source_ids.setdefault(source_key, set())
            if content_id not in ids:
                ids.add(content_id)
                self.fetched_ids.add(content_id)
                self.new_ids.add(content_id)
                self._session_new_ids.add(content_id)
                self.logger.debug(
                    f"Marked as fetched: source={source_key}, url={url}, hash={content_id}"
                )

    def stage_source_snapshot(self, source_key: str, urls: Iterable[str]):
        """暂存非空源快照，实际替换在 save() 时完成。

        参见 docs/DEDUP.md 的“生命周期”章节。
        """
        ids = {generate_content_id(url) for url in urls if url}
        if ids:
            with self._lock:
                previous = self.source_ids.get(source_key, set())
                self._pending_snapshots[source_key] = ids
                self._pending_snapshot_stats[source_key] = {
                    "previous": len(previous),
                    "current": len(ids),
                    "added": len(ids - previous),
                    "removed": len(previous - ids),
                    "retained": len(previous & ids),
                }
                stats = self._pending_snapshot_stats[source_key]
                self._session_snapshot_added += stats["added"]
                self._session_snapshot_removed += stats["removed"]
                self.logger.debug(
                    f"去重快照变化: source={source_key}, "
                    f"上次={stats['previous']} 条, 本次={stats['current']} 条, "
                    f"新增={stats['added']} 条, 移除={stats['removed']} 条, "
                    f"保留={stats['retained']} 条"
                )

    def save(self):
        with self._lock:
            if not self._pending_snapshots and not self.new_ids and not self._dirty:
                self.logger.info("No new content to save")
                return
            for source_key, ids in self._pending_snapshots.items():
                self._session_new_ids.update(ids - self.source_ids.get(source_key, set()))
                self.source_ids[source_key] = set(ids)
            self._pending_snapshots.clear()
            self._pending_snapshot_stats.clear()
            self._refresh_fetched_ids()
            snapshot = {key: set(ids) for key, ids in self.source_ids.items() if ids}
            retries = dict(self.retry_records)

        try:
            directory = os.path.dirname(self.data_file) or "."
            os.makedirs(directory, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(prefix=".fetched_urls.", dir=directory, text=True)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    for source_key, ids in snapshot.items():
                        for content_id in sorted(ids):
                            f.write(f"{source_key}\t{content_id}\n")
                    for (source_key, content_id), (count, last) in sorted(retries.items()):
                        f.write(f"@retry\t{source_key}\t{content_id}\t{count}\t{last}\n")
                os.replace(temp_path, self.data_file)
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            with self._lock:
                self.new_ids.clear()
                self._dirty = False
            self.logger.info(
                f"去重数据库保存完成: 当前={sum(len(ids) for ids in snapshot.values())} 条"
            )
        except Exception as e:
            self.logger.exception(f"去重数据库保存失败: {e}")
            raise

    def clear(self, persist=True):
        with self._lock:
            self.source_ids.clear()
            self.retry_records.clear()
            self._dirty = True
            self._pending_snapshots.clear()
            self._pending_snapshot_stats.clear()
            self.fetched_ids.clear()
            self.new_ids.clear()
            self._session_new_ids.clear()
            self._session_snapshot_added = 0
            self._session_snapshot_removed = 0
            if persist and os.path.exists(self.data_file):
                try:
                    with open(self.data_file, "w", encoding="utf-8") as f:
                        f.write("")
                    self.logger.info(f"Cleared dedup file at {self.data_file}")
                except Exception as e:
                    self.logger.error(f"Failed to clear dedup file: {e}")

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "total_fetched": sum(len(ids) for ids in self.source_ids.values()),
                "new_fetched": len(self._session_new_ids),
                "snapshot_added": self._session_snapshot_added,
                "snapshot_removed": self._session_snapshot_removed,
            }

    def clear_new_ids(self):
        with self._lock:
            self.new_ids.clear()
            self._session_new_ids.clear()
