# -*- coding: utf-8 -*-
"""从 ``data/challenges.json`` 加载练习题。

题目从代码里挪到数据文件，改题/加题不用动 Python；模块仍然导出
``CHALLENGES`` 与 ``CHALLENGES_BY_ID``，旧调用方（模板、测试）无需改动。
"""

from __future__ import annotations

import json
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent / "data" / "challenges.json"

_REQUIRED = ("id", "language", "level", "title", "summary", "goal", "starter", "expected")


def _load() -> list[dict]:
    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("challenges.json 应该是一个数组")
    seen: set[str] = set()
    for index, item in enumerate(raw, 1):
        missing = [key for key in _REQUIRED if key not in item]
        if missing:
            raise ValueError(f"第 {index} 题缺少字段: {', '.join(missing)}")
        if item["id"] in seen:
            raise ValueError(f"题目 id 重复: {item['id']}")
        seen.add(item["id"])
        if item["language"] not in ("shell", "python"):
            raise ValueError(f"{item['id']} 的语言只能是 shell 或 python")
    return raw


CHALLENGES: list[dict] = _load()
CHALLENGES_BY_ID: dict[str, dict] = {item["id"]: item for item in CHALLENGES}


def by_language(language: str) -> list[dict]:
    return [item for item in CHALLENGES if item["language"] == language]


def next_challenge(challenge_id: str) -> dict | None:
    """返回同语言的下一个任务，用于「做对了，下一题」。"""
    current = CHALLENGES_BY_ID.get(challenge_id)
    if not current:
        return None
    siblings = by_language(current["language"])
    for index, item in enumerate(siblings):
        if item["id"] == challenge_id and index + 1 < len(siblings):
            return siblings[index + 1]
    return None
