# -*- coding: utf-8 -*-
"""中文自然语言时间解析（用于日程提醒，纯函数，零依赖）。

支持：今天/明天/后天/大后天/周X/星期X/X月X号 + 凌晨/早上/上午/中午/下午/晚上
+ X点/X点半/X点Y分/HH:MM。
返回 (datetime, 剩余文本)；解析不出时间返回 (None, 原文)。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

_WEEKDAY = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
_PERIODS = (("凌晨", 0), ("早上", 7), ("早晨", 7), ("上午", 8), ("中午", 12),
            ("下午", 13), ("傍晚", 18), ("晚上", 19), ("夜里", 20))
_DAY_WORDS = ("大后天", "后天", "明天", "今晚", "明晚", "今天")

_TIME_RE = re.compile(
    r"(\d{1,2})\s*点\s*半"
    r"|(\d{1,2})\s*点\s*(\d{1,2})\s*分?"
    r"|(\d{1,2})\s*点"
    r"|(\d{1,2}):(\d{2})"
)


def _match_prefix(text: str, words) -> tuple:
    """按词长优先匹配前缀，返回 (命中词, 剩余文本)；未命中返回 (None, 原文)。"""
    for w in sorted(words, key=len, reverse=True):
        if text.startswith(w):
            return w, text[len(w):]
    return None, text


def _extract_time(text: str) -> tuple:
    """从文本里找时间数字，返回 (hour, minute, 去掉时间后的剩余文本)。"""
    m = _TIME_RE.search(text)
    if not m:
        return None, None, text
    if m.group(1):                       # X点半
        hour, minute = int(m.group(1)), 30
    elif m.group(3):                     # X点Y分
        hour, minute = int(m.group(2)), int(m.group(3))
    elif m.group(4):                     # X点
        hour, minute = int(m.group(4)), 0
    else:                                # HH:MM
        hour, minute = int(m.group(5)), int(m.group(6))
    rest = text[:m.start()] + text[m.end():]
    return hour, minute, rest


def parse_zh_datetime(text: str, now: datetime | None = None) -> tuple:
    """
    从自然语言里解析出日程时间。返回 (datetime, 剩余文本[事项])。
    示例：
        '周五下午3点 高数考试' → (本周五 15:00, '高数考试')
        '明天早上8点 叫我'     → (明天 08:00, '叫我')
    """
    now = now or datetime.now()
    t = (text or "").strip()
    if not t:
        return None, text

    day = None
    week_based = False
    kw, rest = _match_prefix(t, _DAY_WORDS)
    evening_hint = False
    if kw:
        delta = {"大后天": 3, "后天": 2, "明天": 1, "明晚": 1, "今晚": 0, "今天": 0}[kw]
        day = now.date() + timedelta(days=delta)
        evening_hint = kw in ("今晚", "明晚")

    if day is None:
        m = re.match(r"^(星期|周)([一二三四五六日天])", rest)
        if m:
            wd = _WEEKDAY[m.group(2)]
            day = now.date() + timedelta(days=(wd - now.isoweekday()) % 7)
            week_based = True
            rest = rest[len(m.group(0)):]

    if day is None:
        m = re.match(r"^(\d{1,2})月(\d{1,2})[号日]", rest)
        if m:
            month, d = int(m.group(1)), int(m.group(2))
            rest = rest[len(m.group(0)):]
            try:
                day = datetime(now.year, month, d).date()
                if day < now.date():
                    day = datetime(now.year + 1, month, d).date()
            except ValueError:
                day = None

    # 时段（早上/上午/下午/晚上...）
    hour_bias = None
    for w, h in _PERIODS:
        if rest.startswith(w):
            hour_bias = h
            rest = rest[len(w):]
            break
    # "今晚/明晚" 默认按晚上处理（如 今晚8点 → 20:00）
    if hour_bias is None and evening_hint:
        hour_bias = 19

    hour, minute, rest = _extract_time(rest)
    if hour is None:
        return None, text

    # 时段换算：下午3点 → 15点
    if hour_bias is not None and hour < 12:
        if hour_bias >= 12:
            hour += 12
        elif hour_bias <= 5 and hour >= 12:
            hour += 12
    hour %= 24

    day = day or now.date()
    dt = datetime(day.year, day.month, day.day, hour, minute or 0)

    # 已过：今天的时间已过 → 明天；周X已过 → 下周X
    if dt < now:
        if week_based:
            dt += timedelta(days=7)
        elif day == now.date():
            dt += timedelta(days=1)

    event = rest.strip(" ，,、.。;；:：-—")
    return dt, event
