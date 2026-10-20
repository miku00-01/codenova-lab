# -*- coding: utf-8 -*-
"""练习进度与提交记录（SQLite 持久化）。

之前进度完全不存在：判题只在前端比字符串，刷新就丢。这里用一个单文件
SQLite 记录「谁 / 哪题 / 第几次 / 是否通过 / 最后一次源码与输出」，
并用 ``passed`` 表示一次性点亮（通过后不会被后续失败覆盖）。
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS progress (
    user          TEXT NOT NULL,
    challenge_id  TEXT NOT NULL,
    language      TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'attempted',
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_stdout   TEXT NOT NULL DEFAULT '',
    last_source   TEXT NOT NULL DEFAULT '',
    first_passed  TEXT,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (user, challenge_id)
);
CREATE TABLE IF NOT EXISTS submissions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user          TEXT NOT NULL,
    challenge_id  TEXT NOT NULL,
    language      TEXT NOT NULL DEFAULT '',
    passed        INTEGER NOT NULL DEFAULT 0,
    stdout        TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_submissions_user ON submissions (user, created_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clip(text: object, limit: int) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[:limit] + "…"


class ProgressStore:
    """线程安全的最小进度仓库。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextlib.contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def record(
        self,
        user: str,
        challenge_id: str,
        *,
        language: str = "",
        source: str = "",
        stdout: str = "",
        passed: bool = False,
    ) -> dict:
        """记一次提交，返回该题的最新状态。"""
        now = _now()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM progress WHERE user = ? AND challenge_id = ?",
                (user, challenge_id),
            ).fetchone()
            if row is None:
                status = "passed" if passed else "attempted"
                conn.execute(
                    "INSERT INTO progress (user, challenge_id, language, status, attempts,"
                    " last_stdout, last_source, first_passed, updated_at)"
                    " VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)",
                    (user, challenge_id, language, status, _clip(stdout, 4000),
                     _clip(source, 8000), now if passed else None, now),
                )
            else:
                status = "passed" if (passed or row["status"] == "passed") else "attempted"
                first_passed = row["first_passed"] or (now if passed else None)
                conn.execute(
                    "UPDATE progress SET language = ?, status = ?, attempts = attempts + 1,"
                    " last_stdout = ?, last_source = ?, first_passed = ?, updated_at = ?"
                    " WHERE user = ? AND challenge_id = ?",
                    (language or row["language"], status, _clip(stdout, 4000),
                     _clip(source, 8000), first_passed, now, user, challenge_id),
                )
            conn.execute(
                "INSERT INTO submissions (user, challenge_id, language, passed, stdout, source, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user, challenge_id, language, 1 if passed else 0,
                 _clip(stdout, 4000), _clip(source, 8000), now),
            )
            row = conn.execute(
                "SELECT * FROM progress WHERE user = ? AND challenge_id = ?",
                (user, challenge_id),
            ).fetchone()
        return dict(row) if row else {}

    def get(self, user: str) -> dict[str, dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM progress WHERE user = ?", (user,)
            ).fetchall()
        return {row["challenge_id"]: dict(row) for row in rows}

    def summary(self, user: str, total: int = 0) -> dict:
        rows = self.get(user)
        items = []
        for challenge_id, row in sorted(rows.items(), key=lambda kv: kv[1]["updated_at"]):
            items.append({
                "challenge_id": challenge_id,
                "language": row["language"],
                "status": row["status"],
                "attempts": row["attempts"],
                "updated_at": row["updated_at"],
                "first_passed": row["first_passed"],
            })
        solved = [item["challenge_id"] for item in items if item["status"] == "passed"]
        return {
            "user": user,
            "items": items,
            "solved": solved,
            "solved_count": len(solved),
            "total": total,
            "attempts": sum(item["attempts"] for item in items),
        }

    def reset(self, user: str) -> int:
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM progress WHERE user = ?", (user,))
            conn.execute("DELETE FROM submissions WHERE user = ?", (user,))
            return cursor.rowcount
