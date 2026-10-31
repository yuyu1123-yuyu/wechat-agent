# -*- coding: utf-8 -*-
"""Actions 段解析与旧格式兼容测试。"""
from wechat_agent.domain.directive import (
    parse_actions_block,
    parse_legacy_marks,
    parse_reply,
)


def test_parse_actions_block():
    reply = "好的，我记下了～\n\n<actions>\nremember: 群友A喜欢吃辣\nschedule: 周五下午3点 高数考试\n</actions>"
    clean, directives = parse_actions_block(reply)
    assert "好的，我记下了" in clean
    assert "<actions>" not in clean
    assert [(d.kind, d.value) for d in directives] == [
        ("remember", "群友A喜欢吃辣"),
        ("schedule", "周五下午3点 高数考试"),
    ]


def test_actions_block_case_insensitive_and_extra_whitespace():
    reply = "正文\n<ACTIONS>\n  weather : 安阳 \n</ACTIONS>"
    clean, directives = parse_actions_block(reply)
    assert clean == "正文"
    assert [(d.kind, d.value) for d in directives] == [("weather", "安阳")]


def test_unknown_kind_ignored():
    reply = "<actions>\nunknown: x\nwebsearch: 今天新闻\n</actions>"
    clean, directives = parse_actions_block(reply)
    assert [(d.kind, d.value) for d in directives] == [("websearch", "今天新闻")]


def test_no_actions_block():
    reply = "就是普通回复"
    clean, directives = parse_actions_block(reply)
    assert clean == reply
    assert directives == []


def test_legacy_marks():
    reply = "记住了\n【记住】对方喜欢喝奶茶\n另外\n【日程】周五下午3点 高数考试"
    clean, directives = parse_legacy_marks(reply)
    assert "记住了" in clean and "另外" in clean
    assert "【记住】" not in clean
    kinds = [d.kind for d in directives]
    assert "remember" in kinds and "schedule" in kinds


def test_legacy_mark_inline():
    reply = "好的记住了【记住】对方喜欢喝奶茶"
    clean, directives = parse_legacy_marks(reply)
    assert clean == "好的记住了"
    assert directives[0].kind == "remember"


def test_parse_reply_both():
    reply = ("回复正文\n<actions>\nremember: 新信息\n</actions>\n"
             "【天气】安阳")
    clean, directives = parse_reply(reply)
    assert "回复正文" in clean
    kinds = [d.kind for d in directives]
    assert kinds == ["remember", "weather"]
