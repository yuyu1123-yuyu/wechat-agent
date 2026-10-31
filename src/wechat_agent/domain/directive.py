# -*- coding: utf-8 -*-
"""AI 指令协议（Actions 段）解析 —— 纯函数，零 I/O。

AI 回复里可能夹带程序指令。协议：回复【最后】单独输出一个 <actions> 块：

    <actions>
    remember: 需要长期记住的信息
    rule: 低优先级行为笔记
    schedule: 周五下午3点 高数考试
    weather: 安阳
    websearch: 搜索关键词
    </actions>

<actions> 块不会显示给对方，由程序解析执行。
同时兼容旧项目的单行标记（【记住】/【规则】/【日程】/【天气】）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

ACTIONS_RE = re.compile(r"<actions>(.*?)</actions>", re.S | re.I)
LINE_RE = re.compile(r"^\s*([a-zA-Z_]+)\s*[:：]\s*(.*)$")

VALID_KINDS = {"remember", "rule", "schedule", "weather", "websearch"}

# 旧格式单行标记兼容：前缀 -> 指令类型
LEGACY_PREFIXES = [
    ("【记住】", "remember"),
    ("记住：", "remember"),
    ("#记住", "remember"),
    ("【规则】", "rule"),
    ("规则：", "rule"),
    ("【日程】", "schedule"),
    ("日程：", "schedule"),
    ("【日程提醒】", "schedule"),
    ("【提醒】", "schedule"),
    ("提醒：", "schedule"),
    ("【天气】", "weather"),
    ("天气：", "weather"),
]


@dataclass(frozen=True)
class Directive:
    kind: str       # remember | rule | schedule | weather | websearch
    value: str


def parse_actions_block(reply: str) -> tuple:
    """提取 <actions> 块，返回 (去掉块后的回复, [Directive, ...])。"""
    m = ACTIONS_RE.search(reply)
    if not m:
        return reply, []
    block = m.group(1)
    clean = (reply[:m.start()] + reply[m.end():]).strip()
    directives = []
    for line in block.splitlines():
        lm = LINE_RE.match(line)
        if not lm:
            continue
        kind = lm.group(1).strip().lower()
        value = lm.group(2).strip()
        if kind in VALID_KINDS and value:
            directives.append(Directive(kind, value))
    return clean, directives


def parse_legacy_marks(reply: str) -> tuple:
    """兼容旧格式：行内【记住】/【规则】/【日程】/【天气】前缀。
    返回 (去掉标记后的回复, [Directive, ...])。
    """
    lines = reply.split("\n")
    kept = []
    directives = []
    for line in lines:
        s = line.strip()
        matched = False
        for prefix, kind in LEGACY_PREFIXES:
            i = s.find(prefix)
            if i >= 0:
                value = s[i + len(prefix):].strip()
                if value:
                    directives.append(Directive(kind, value))
                before = s[:i].strip()
                if before:
                    kept.append(before)
                matched = True
                break
        if not matched:
            kept.append(line)
    return "\n".join(kept).strip(), directives


def parse_reply(reply: str) -> tuple:
    """统一入口：先解析 <actions> 块，再解析旧格式标记。"""
    clean, actions = parse_actions_block(reply)
    clean, legacy = parse_legacy_marks(clean)
    return clean, actions + legacy
