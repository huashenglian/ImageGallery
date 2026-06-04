from __future__ import annotations

import gzip
import json
import logging
import os
import sqlite3
import time

from paths import CACHE_DIR, is_excluded_scan_path

_logger = logging.getLogger(__name__)


class ClassifyCache:
    def __init__(self) -> None:
        self._db_path = os.path.join(CACHE_DIR, "classify_result.db")
        self._conn: sqlite3.Connection | None = None
        self._dirty_count = 0
        self._dirty_threshold = 50
        self._last_save_time = 0.0
        self._save_interval = 5.0
        self._progress_current: int = 0
        self._progress_total: int = 0

    @property
    def path(self) -> str:
        return self._db_path

    @property
    def exists(self) -> bool:
        return os.path.isfile(self._db_path)

    @property
    def total_classified(self) -> int:
        conn = self._ensure_conn()
        row = conn.execute("SELECT COUNT(*) FROM classify_cache").fetchone()
        return row[0] if row else 0

    @property
    def classified_at(self) -> float:
        return float(self._get_meta("classified_at", "0"))

    @property
    def skipped(self) -> int:
        return int(self._get_meta("skipped", "0"))

    @property
    def preset_name(self) -> str:
        return self._get_meta("preset_name", "")

    @property
    def progress_current(self) -> int:
        if self._progress_current > 0:
            return self._progress_current
        return int(self._get_meta("progress_current", "0"))

    @property
    def progress_total(self) -> int:
        if self._progress_total > 0:
            return self._progress_total
        return int(self._get_meta("progress_total", "0"))

    def set_progress(self, current: int, total: int) -> None:
        self._progress_current = current
        self._progress_total = total

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA cache_size=-4096")
            self._conn.execute("PRAGMA temp_store=MEMORY")
            self._create_tables()
        return self._conn

    def _create_tables(self) -> None:
        conn = self._conn
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS classify_cache (
                image_path   TEXT PRIMARY KEY,
                category     TEXT NOT NULL,
                label        TEXT,
                confidence   REAL,
                model_id     TEXT,
                fallback_used INTEGER DEFAULT 0,
                classified_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_category ON classify_cache(category);
            CREATE INDEX IF NOT EXISTS idx_model ON classify_cache(model_id);
            CREATE TABLE IF NOT EXISTS classify_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

    def _get_meta(self, key: str, default: str = "") -> str:
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT value FROM classify_meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else default

    def _set_meta(self, key: str, value: str) -> None:
        conn = self._ensure_conn()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO classify_meta (key, value) VALUES (?, ?)",
                (key, value)
            )

    def load(self) -> None:
        self._ensure_conn()
        self._remove_project_paths()
        self._migrate_from_json()

    def _migrate_from_json(self) -> None:
        old_path = os.path.join(CACHE_DIR, "classify_result.json.gz")
        if not os.path.isfile(old_path):
            return
        conn = self._ensure_conn()
        count = conn.execute("SELECT COUNT(*) FROM classify_cache").fetchone()[0]
        if count > 0:
            _logger.info("SQLite 缓存已有数据，跳过 JSON 迁移")
            try:
                os.remove(old_path)
            except OSError:
                pass
            return
        try:
            with gzip.open(old_path, "rb") as f:
                raw = json.loads(f.read().decode("utf-8"))
            meta = raw.get("meta", {})
            data = raw.get("data", {})
            with conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO classify_cache "
                    "(image_path, category, label, confidence, model_id, fallback_used, classified_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (path, entry.get("category", ""), entry.get("label", ""),
                         entry.get("confidence", 0.0), entry.get("model_id", ""),
                         int(entry.get("fallback_used", False)),
                         entry.get("classified_at", 0.0))
                        for path, entry in data.items()
                    ]
                )
                for key, value in meta.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO classify_meta (key, value) VALUES (?, ?)",
                        (key, str(value))
                    )
            _logger.info("已从 JSON 缓存迁移 %d 条记录到 SQLite", len(data))
            try:
                os.remove(old_path)
            except OSError:
                pass
        except (OSError, json.JSONDecodeError, EOFError) as e:
            _logger.warning("JSON 缓存迁移失败: %s", e)

    def _remove_project_paths(self) -> None:
        conn = self._ensure_conn()
        rows = conn.execute("SELECT image_path FROM classify_cache").fetchall()
        removed = 0
        with conn:
            for (path,) in rows:
                if is_excluded_scan_path(path):
                    conn.execute("DELETE FROM classify_cache WHERE image_path = ?", (path,))
                    removed += 1
        if removed:
            _logger.info("已从分类缓存中清除 %d 条排除路径", removed)

    def save(self) -> None:
        self._flush_meta()

    def _flush_meta(self) -> None:
        conn = self._ensure_conn()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO classify_meta (key, value) VALUES (?, ?)",
                ("total_classified", str(self.total_classified))
            )
            conn.execute(
                "INSERT OR REPLACE INTO classify_meta (key, value) VALUES (?, ?)",
                ("classified_at", str(time.time()))
            )
            if self._progress_current > 0 or self._progress_total > 0:
                conn.execute(
                    "INSERT OR REPLACE INTO classify_meta (key, value) VALUES (?, ?)",
                    ("progress_current", str(self._progress_current))
                )
                conn.execute(
                    "INSERT OR REPLACE INTO classify_meta (key, value) VALUES (?, ?)",
                    ("progress_total", str(self._progress_total))
                )
        self._dirty_count = 0
        self._last_save_time = time.monotonic()

    def get(self, image_path: str) -> dict | None:
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT category, label, confidence, model_id, fallback_used, classified_at "
            "FROM classify_cache WHERE image_path = ?",
            (image_path,)
        ).fetchone()
        if row is None:
            return None
        return {
            "category": row[0],
            "label": row[1],
            "confidence": row[2],
            "model_id": row[3],
            "fallback_used": bool(row[4]),
            "classified_at": row[5],
        }

    def get_category(self, image_path: str) -> str | None:
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT category FROM classify_cache WHERE image_path = ?",
            (image_path,)
        ).fetchone()
        return row[0] if row else None

    def get_model_id(self, image_path: str) -> str | None:
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT model_id FROM classify_cache WHERE image_path = ?",
            (image_path,)
        ).fetchone()
        return row[0] if row else None

    def was_fallback_used(self, image_path: str) -> bool:
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT fallback_used FROM classify_cache WHERE image_path = ?",
            (image_path,)
        ).fetchone()
        return bool(row[0]) if row else False

    def update(self, results: dict) -> None:
        conn = self._ensure_conn()
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO classify_cache "
                "(image_path, category, label, confidence, model_id, fallback_used, classified_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (path, e["category"], e.get("label"), e.get("confidence"),
                     e.get("model_id"), int(e.get("fallback_used", False)),
                     e.get("classified_at", time.time()))
                    for path, e in results.items()
                ]
            )
        self._dirty_count += len(results)

    def maybe_save(self) -> None:
        if self._dirty_count <= 0:
            return
        now = time.monotonic()
        if self._dirty_count >= self._dirty_threshold or (now - self._last_save_time) >= self._save_interval:
            self._flush_meta()

    def flush(self) -> None:
        if self._dirty_count > 0:
            self._flush_meta()

    def get_images_by_category(self) -> dict[str, list[str]]:
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT category, image_path FROM classify_cache ORDER BY image_path"
        ).fetchall()
        mapping: dict[str, list[str]] = {}
        for cat, path in rows:
            mapping.setdefault(cat, []).append(path)
        return mapping

    def get_category_counts(self) -> dict[str, int]:
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT category, COUNT(*) FROM classify_cache GROUP BY category"
        ).fetchall()
        return {cat: cnt for cat, cnt in rows}

    def remove_paths(self, paths: list[str]) -> int:
        if not paths:
            return 0
        conn = self._ensure_conn()
        with conn:
            cursor = conn.executemany(
                "DELETE FROM classify_cache WHERE image_path = ?",
                [(p,) for p in paths]
            )
        return cursor.rowcount

    def remove_orphans(self, existing_paths: set[str]) -> int:
        conn = self._ensure_conn()
        rows = conn.execute("SELECT image_path FROM classify_cache").fetchall()
        orphans = [r[0] for r in rows if r[0] not in existing_paths]
        if orphans:
            with conn:
                conn.executemany(
                    "DELETE FROM classify_cache WHERE image_path = ?",
                    [(p,) for p in orphans]
                )
        return len(orphans)

    def clear(self) -> None:
        if self._conn is not None:
            try:
                with self._conn:
                    self._conn.execute("DELETE FROM classify_cache")
                    self._conn.execute("DELETE FROM classify_meta")
            except sqlite3.Error:
                pass
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
        if os.path.isfile(self._db_path):
            try:
                os.remove(self._db_path)
            except OSError:
                pass
        for suffix in ("-wal", "-shm"):
            wal_path = self._db_path + suffix
            if os.path.isfile(wal_path):
                try:
                    os.remove(wal_path)
                except OSError:
                    pass
        self._progress_current = 0
        self._progress_total = 0

    def has_incomplete(self) -> bool:
        if self.total_classified == 0:
            return False
        incomplete = self._get_meta("incomplete", "")
        if incomplete.lower() in ("true", "1"):
            return True
        cur = self.progress_current
        tot = self.progress_total
        return tot > 0 and cur < tot

    def mark_incomplete(self) -> None:
        self._set_meta("incomplete", "True")
        self._flush_meta()

    def mark_complete(self, skipped: int = 0) -> None:
        self._set_meta("incomplete", "False")
        self._set_meta("skipped", str(skipped))
        self._progress_current = 0
        self._progress_total = 0
        self._set_meta("progress_current", "0")
        self._set_meta("progress_total", "0")
        self._flush_meta()

    def set_preset_name(self, name: str) -> None:
        self._set_meta("preset_name", name)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._flush_meta()
            except sqlite3.Error:
                pass
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
