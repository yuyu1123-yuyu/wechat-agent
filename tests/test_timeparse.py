# -*- coding: utf-8 -*-
"""timeparse 中文时间解析测试。"""
from datetime import datetime

import pytest

from wechat_agent.domain.timeparse import parse_zh_datetime


def test_weekday_afternoon():
    now = datetime(2026, 6, 8, 10, 0)   # 周一
    dt, event = parse_zh_datetime("周五下午3点 高数考试", now=now)
    assert dt == datetime(2026, 6, 12, 15, 0)
    assert event == "高数考试"


def test_tomorrow_morning():
    now = datetime(2026, 6, 8, 10, 0)
    dt, event = parse_zh_datetime("明天早上8点 叫我", now=now)
    assert dt == datetime(2026, 6, 9, 8, 0)
    assert event == "叫我"


def test_tonight():
    now = datetime(2026, 6, 8, 10, 0)
    dt, event = parse_zh_datetime("今晚8点 吃饭", now=now)
    assert dt == datetime(2026, 6, 8, 20, 0)


def test_plain_hour_literal():
    # 无时段词的"X点"按字面 24h 解析（技能要求 AI 写清上午/下午）
    now = datetime(2026, 6, 8, 10, 0)
    dt, event = parse_zh_datetime("3点开会", now=now)
    assert dt == datetime(2026, 6, 9, 3, 0)   # 今天 03:00 已过 → 明天


def test_past_time_today_rolls_to_tomorrow():
    now = datetime(2026, 6, 8, 16, 0)
    dt, event = parse_zh_datetime("10点开会", now=now)
    assert dt == datetime(2026, 6, 9, 10, 0)   # 今天的10点已过 → 明天


def test_hhmm_format():
    now = datetime(2026, 6, 8, 10, 0)
    dt, event = parse_zh_datetime("14:30 开会", now=now)
    assert dt == datetime(2026, 6, 8, 14, 30)


def test_half_hour():
    now = datetime(2026, 6, 8, 10, 0)
    dt, event = parse_zh_datetime("后天下午2点半 看电影", now=now)
    assert dt == datetime(2026, 6, 10, 14, 30)


def test_month_day():
    now = datetime(2026, 6, 8, 10, 0)
    dt, event = parse_zh_datetime("6月20号上午9点 考试", now=now)
    assert dt == datetime(2026, 6, 20, 9, 0)


def test_past_weekday_rolls_to_next_week():
    now = datetime(2026, 6, 8, 10, 0)   # 周一
    dt, _ = parse_zh_datetime("周一上午9点 周会", now=now)
    # 本周一已过 → 下周一
    assert dt == datetime(2026, 6, 15, 9, 0)


def test_no_time_returns_none():
    now = datetime(2026, 6, 8, 10, 0)
    dt, text = parse_zh_datetime("明天", now=now)
    assert dt is None
    assert text == "明天"


def test_empty():
    assert parse_zh_datetime("", datetime(2026, 6, 8)) == (None, "")
    assert parse_zh_datetime(None, datetime(2026, 6, 8))[0] is None
