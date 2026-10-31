# -*- coding: utf-8 -*-
"""日程模型与到期计算测试。"""
from datetime import datetime, timedelta

from wechat_agent.domain.schedule import (
    Schedule,
    is_due,
    make_id,
    next_due,
    within_catchup,
)


def _s(when_str, reminded=False):
    return Schedule(id="x", when=datetime.strptime(when_str, "%Y-%m-%d %H:%M"),
                    event="e", reminded=reminded)


def test_is_due():
    now = datetime(2026, 6, 8, 10, 0)
    assert is_due(_s("2026-06-08 09:00"), now)
    assert not is_due(_s("2026-06-08 11:00"), now)
    assert not is_due(_s("2026-06-08 09:00", reminded=True), now)


def test_within_catchup():
    now = datetime(2026, 6, 8, 10, 0)
    window = timedelta(minutes=60)
    assert within_catchup(_s("2026-06-08 09:30"), now, window)
    assert not within_catchup(_s("2026-06-08 08:00"), now, window)


def test_next_due():
    now = datetime(2026, 6, 8, 10, 0)
    items = [
        _s("2026-06-08 11:00"),
        _s("2026-06-08 12:30"),
        _s("2026-06-08 09:00"),           # 已过，不算
        _s("2026-06-08 11:00", reminded=True),  # 已提醒，不算
    ]
    assert next_due(items, now) == datetime(2026, 6, 8, 11, 0)


def test_next_due_none():
    now = datetime(2026, 6, 8, 10, 0)
    assert next_due([], now) is None
    assert next_due([_s("2026-06-08 09:00")], now) is None


def test_make_id_stable():
    dt = datetime(2026, 6, 12, 15, 0)
    assert make_id(dt, "高数考试") == make_id(dt, "高数考试")
    assert make_id(dt, "高数考试") != make_id(dt, "英语考试")


def test_schedule_roundtrip():
    s = Schedule(id="a", when=datetime(2026, 6, 12, 15, 0), event="考试")
    d = s.to_dict()
    s2 = Schedule.from_dict(d)
    assert s2.when == s.when and s2.event == s.event and s2.reminded is False
