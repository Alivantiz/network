"""SQLite-backed state: what we've already seen, cut and posted.

The pipeline is restartable — every stage records its result here, so a crashed
or rate-limited run picks up exactly where it stopped instead of re-downloading
and re-posting.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id     TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    channel      TEXT,
    url          TEXT NOT NULL,
    duration     INTEGER,
    published_at TEXT,
    status       TEXT NOT NULL DEFAULT 'discovered',
    source_path  TEXT,
    note         TEXT,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS clips (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    idx          INTEGER NOT NULL,
    total        INTEGER NOT NULL,
    path         TEXT NOT NULL,
    start        REAL NOT NULL,
    duration     REAL NOT NULL,
    title        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    publish_id   TEXT,
    error        TEXT,
    uploaded_at  REAL,
    created_at   REAL NOT NULL,
    UNIQUE (video_id, idx)
);

CREATE INDEX IF NOT EXISTS idx_clips_status ON clips(status);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
"""

VIDEO_STATUSES = ("discovered", "downloaded", "split", "done", "failed", "skipped")
CLIP_STATUSES = ("pending", "uploading", "uploaded", "failed")


@dataclass
class ClipRow:
    id: int
    video_id: str
    idx: int
    total: int
    path: str
    start: float
    duration: float
    title: str
    status: str
    publish_id: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ClipRow:
        return cls(
            id=row["id"],
            video_id=row["video_id"],
            idx=row["idx"],
            total=row["total"],
            path=row["path"],
            start=row["start"],
            duration=row["duration"],
            title=row["title"],
            status=row["status"],
            publish_id=row["publish_id"],
        )


class Store:
    """Thin wrapper over a SQLite file. Use as a context manager."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # ---- videos -------------------------------------------------------
    def has_video(self, video_id: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM videos WHERE video_id = ?", (video_id,))
        return cur.fetchone() is not None

    def add_video(self, video: dict[str, Any]) -> bool:
        """Insert a discovered video. Returns False if it was already known."""
        now = time.time()
        try:
            self.conn.execute(
                """INSERT INTO videos
                   (video_id, title, channel, url, duration, published_at,
                    status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'discovered', ?, ?)""",
                (
                    video["video_id"],
                    video["title"],
                    video.get("channel"),
                    video["url"],
                    video.get("duration"),
                    video.get("published_at"),
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            return False
        self.conn.commit()
        return True

    def set_video_status(
        self, video_id: str, status: str, *, source_path: str | None = None, note: str | None = None
    ) -> None:
        if status not in VIDEO_STATUSES:
            raise ValueError(f"unknown video status: {status}")
        self.conn.execute(
            """UPDATE videos
               SET status = ?, updated_at = ?,
                   source_path = COALESCE(?, source_path),
                   note = COALESCE(?, note)
               WHERE video_id = ?""",
            (status, time.time(), source_path, note, video_id),
        )
        self.conn.commit()

    def get_video(self, video_id: str) -> sqlite3.Row | None:
        cur = self.conn.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,))
        return cur.fetchone()

    def videos_with_status(self, *statuses: str, limit: int | None = None) -> list[sqlite3.Row]:
        placeholders = ",".join("?" * len(statuses))
        sql = f"SELECT * FROM videos WHERE status IN ({placeholders}) ORDER BY created_at"
        params: list[Any] = list(statuses)
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return list(self.conn.execute(sql, params))

    # ---- clips --------------------------------------------------------
    def add_clips(self, video_id: str, clips: Iterable[dict[str, Any]]) -> int:
        now = time.time()
        rows = [
            (
                video_id,
                c["idx"],
                c["total"],
                str(c["path"]),
                float(c["start"]),
                float(c["duration"]),
                c["title"],
                now,
            )
            for c in clips
        ]
        cur = self.conn.executemany(
            """INSERT OR IGNORE INTO clips
               (video_id, idx, total, path, start, duration, title, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self.conn.commit()
        return cur.rowcount

    def pending_clips(self, limit: int | None = None) -> list[ClipRow]:
        sql = (
            "SELECT * FROM clips WHERE status IN ('pending', 'uploading') "
            "ORDER BY video_id, idx"
        )
        params: list[Any] = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [ClipRow.from_row(r) for r in self.conn.execute(sql, params)]

    def clips_for_video(self, video_id: str) -> list[ClipRow]:
        cur = self.conn.execute("SELECT * FROM clips WHERE video_id = ? ORDER BY idx", (video_id,))
        return [ClipRow.from_row(r) for r in cur]

    def set_clip_status(
        self,
        clip_id: int,
        status: str,
        *,
        publish_id: str | None = None,
        error: str | None = None,
    ) -> None:
        if status not in CLIP_STATUSES:
            raise ValueError(f"unknown clip status: {status}")
        uploaded_at = time.time() if status == "uploaded" else None
        self.conn.execute(
            """UPDATE clips
               SET status = ?, publish_id = COALESCE(?, publish_id),
                   error = ?, uploaded_at = COALESCE(?, uploaded_at)
               WHERE id = ?""",
            (status, publish_id, error, uploaded_at, clip_id),
        )
        self.conn.commit()

    def uploads_since(self, since_ts: float) -> int:
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM clips WHERE status = 'uploaded' AND uploaded_at >= ?",
            (since_ts,),
        )
        return int(cur.fetchone()[0])

    def last_upload_ts(self) -> float | None:
        cur = self.conn.execute("SELECT MAX(uploaded_at) FROM clips WHERE status = 'uploaded'")
        value = cur.fetchone()[0]
        return float(value) if value is not None else None

    def video_is_complete(self, video_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM clips WHERE video_id = ? AND status != 'uploaded'",
            (video_id,),
        )
        return int(cur.fetchone()[0]) == 0

    # ---- reporting ----------------------------------------------------
    def summary(self) -> dict[str, dict[str, int]]:
        videos = {
            row["status"]: row["n"]
            for row in self.conn.execute(
                "SELECT status, COUNT(*) AS n FROM videos GROUP BY status"
            )
        }
        clips = {
            row["status"]: row["n"]
            for row in self.conn.execute(
                "SELECT status, COUNT(*) AS n FROM clips GROUP BY status"
            )
        }
        return {"videos": videos, "clips": clips}
