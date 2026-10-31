# -*- coding: utf-8 -*-
"""日程模型与到期计算（纯函数）。

存储格式（schedules.json，由 store 层维护）：
    {"version": 1, "items": [{"id", "when": "YYYY-MM-DD HH:MM", "event", "reminded", "created_at"}]}
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

TIME_FMT = "%Y-%m-%d %H:%M"


@dataclass
class Schedule:
    id: str
    when: datetime
    event: str
    reminded: bool = False
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "when": self.when.strftime(TIME_FMT),
            "event": self.event,
            "reminded": self.reminded,
            "created_at": self.created_at or time.strftime(TIME_FMT),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Schedule":
        return cls(
            id=d.get("id", ""),
            when=datetime.strptime(d["when"], TIME_FMT),
            event=d.get("event", ""),
            reminded=bool(d.get("reminded", False)),
            created_at=d.get("created_at", ""),
        )


def make_id(when: datetime, event: str) -> str:
    """生成稳定的日程 id（同时间同事件幂等）。"""
    raw = f"{when.strftime('%Y%m%d%H%M')}-{event}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def is_due(s: Schedule, now: datetime) -> bool:
    return (not s.reminded) and s.when <= now


def within_catchup(s: Schedule, now: datetime, window: timedelta) -> bool:
    """到期时间是否落在补发窗口内（防离线积压轰炸）。"""
    return (now - s.when) <= window


def next_due(schedules: list, now: datetime) -> datetime | None:
    """最近的未提醒到期时间；没有返回 None。"""
    due = [s.when for s in schedules if not s.reminded and s.when > now]
    return min(due) if due else None
