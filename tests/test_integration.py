# -*- coding: utf-8 -*-
"""集成测试：真实 FileChatStore + 流水线 + Fake LLM/发送器，验证完整流程。

不连接微信、不调用真实 LLM；FakeRouter 返回固定回复（含 <actions>），
验证历史/记忆/日程/指令/提醒的闭环。
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

from wechat_agent import config as cfg
from wechat_agent.app.commands.impl import build_registry
from wechat_agent.app.directives import DirectiveExecutor
from wechat_agent.app.pipeline.group import GroupPipeline
from wechat_agent.app.pipeline.private import PrivatePipeline
from wechat_agent.app.service.active_speak import ActiveSpeakService
from wechat_agent.app.service.chat import ChatService
from wechat_agent.app.service.reminder import ReminderService
from wechat_agent.infra.skills import SkillLoader
from wechat_agent.infra.store.files import FileChatStore
from wechat_agent.ports.llm import LLMResult

_GROUP_MSG_RE = re.compile(r"^(wxid_[0-9a-zA-Z_-]+|\d+|.*@chatroom):\s*(.*)$", re.S)


# ===================== Fakes =====================

class FakeRouter:
    """按提示词内容返回固定回复（可编程）。"""

    def __init__(self, reply: str = "好的～"):
        self.reply = reply
        self.calls: list = []
        self.group_patch = "fake-patch.yml"

    def call(self, prompt: str, *, mode: str, patch: str | None = None) -> LLMResult:
        self.calls.append((mode, patch, prompt))
        return LLMResult(text=self.reply, mode=mode, elapsed=0.01)


class FakeSender:
    def __init__(self):
        self.sent: list = []

    def enqueue(self, target: str, text: str, *,
                chat_id: str = "", drop_ok: bool = False) -> None:
        self.sent.append((target, text, chat_id))


class FakeChatSource:
    """模拟 DBAdapter 的群消息解析/上下文接口。"""

    def __init__(self, nick="浅华", summary="", fts_text=""):
        self.nick = nick
        self.self_username = "wxid_qianhua"
        self.summary = summary
        self.fts_text = fts_text

    def nickname(self, username):
        return self.nick

    def messages(self, user, limit=20, offset=0):
        return []

    def parse_group_msg(self, content):
        m = _GROUP_MSG_RE.match(str(content or ""))
        if m:
            return type("P", (), {"body": m.group(2).strip(), "sender_id": m.group(1)})()
        return type("P", (), {"body": str(content or "").strip(), "sender_id": None})()

    def resolve_sender_nick(self, sender):
        return sender if isinstance(sender, str) and not sender.isdigit() else "群友A"

    def is_self_sender(self, msg, parsed_sender):
        if msg.get("sender_id") == 2:
            return True
        return parsed_sender == self.self_username

    def context_before(self, group_username, mention_seq, n):
        return [("群友A", "今天天气咋样")]

    def mentions_after(self, group_username, msg, window_sec=90, self_nicks=()):
        return []

    def sessions(self, limit=300):
        return []

    def session_summary(self, username):
        return self.summary

    def recover_placeholder_text(self, chat_id, msg):
        content = str(msg.get("content") or "")
        mtype = msg.get("type", "")
        if mtype == "文本" and content == "[文本]":
            # 模拟：优先全文索引（完整），兜底摘要（截断）
            return self.fts_text or self.summary
        return ""


def _services(tmp_path, settings, router):
    store = FileChatStore(tmp_path / "data", settings.behavior, settings.personas)
    skills = SkillLoader(
        roots=[settings.base_dir / "group-skills"],
        basic=settings.skills.basic,
        on_demand=settings.skills.on_demand,
        max_skill_chars=settings.skills.max_skill_chars,
    )
    directives = DirectiveExecutor(store, settings.behavior,
                                   weather_fn=lambda c: f"{c}今天晴",
                                   websearch_fn=lambda q: "结果1")
    chat = ChatService(store, router, settings, skills, directives)
    commands = build_registry(store, settings.personas, settings.behavior)
    active = ActiveSpeakService(router, store, settings)
    return store, chat, commands, active, directives


# ===================== 私聊流程 =====================

def test_private_turn_full_flow(tmp_path):
    settings = cfg.load_settings()
    router = FakeRouter(
        reply="好的记住了\n<actions>\nremember: 对方喜欢喝奶茶\n</actions>")
    store, chat, _, _, _ = _services(tmp_path, settings, router)

    reply, mode = chat.private_turn("wxid_master", "我喜欢喝奶茶，记住", mode="harness")
    assert "好的记住了" in reply
    assert "<actions>" not in reply
    assert mode == "harness"

    # 历史保存了 user + assistant
    hist = store.load_history("wxid_master")
    assert len(hist) == 2
    assert hist[0]["role"] == "user"
    # 记忆写入了
    mem = store.load_memory("wxid_master")
    assert "喜欢喝奶茶" in mem


def test_private_mode_marks(tmp_path):
    settings = cfg.load_settings()
    router = FakeRouter("在的")
    store, chat, _, _, _ = _services(tmp_path, settings, router)

    # 【直连】切 API 并持久化
    mode, text = chat.resolve_private_mode("wxid_master", "【直连】你好")
    assert mode == "api"
    assert text == "你好"
    assert store.get_mode("wxid_master") == "api"
    # 再次无标记 → 沿用 api
    mode, text = chat.resolve_private_mode("wxid_master", "在吗")
    assert mode == "api"


def test_private_pipeline_command(tmp_path):
    settings = cfg.load_settings()
    router = FakeRouter("x")
    store, chat, commands, _, _ = _services(tmp_path, settings, router)
    sender = FakeSender()
    db = FakeChatSource(nick=settings.wechat.master_chat)
    pipe = PrivatePipeline(db=db, store=store, commands=commands,
                           chat_service=chat, sender=sender, settings=settings)

    pipe.handle("wxid_master", {"content": "#记住 我喜欢喝奶茶",
                                "type": 1, "sender_id": 5})
    assert store.load_memory("wxid_master") == "- 我喜欢喝奶茶"
    assert sender.sent[0][1].startswith("记住了")


def test_private_mode_switch_mark_alone(tmp_path):
    """只发【直连】标记：确认切换，不调 AI。"""
    settings = cfg.load_settings()
    router = FakeRouter("x")
    store, chat, commands, _, _ = _services(tmp_path, settings, router)
    sender = FakeSender()
    db = FakeChatSource(nick=settings.wechat.master_chat)
    pipe = PrivatePipeline(db=db, store=store, commands=commands,
                           chat_service=chat, sender=sender, settings=settings)

    pipe.handle("wxid_master", {"content": "【直连】", "type": 1, "sender_id": 5})
    assert store.get_mode("wxid_master") == "api"
    assert len(sender.sent) == 1
    assert "API 直连" in sender.sent[0][1]
    assert router.calls == []          # 没调 AI

    # 单独发【harness】切回
    pipe.handle("wxid_master", {"content": "【harness】", "type": 1, "sender_id": 5})
    assert store.get_mode("wxid_master") == "harness"
    assert "harness" in sender.sent[-1][1]


def test_private_placeholder_recovered_from_summary(tmp_path):
    """长文本被微信加密成 [文本] 占位符：从全文索引恢复完整明文并正常回复。"""
    settings = cfg.load_settings()
    router = FakeRouter("在听呢，你继续说")
    store, chat, commands, _, _ = _services(tmp_path, settings, router)
    sender = FakeSender()
    full = ("她说她其实从一开始就没有那么喜欢我，后来相处中也没有更加喜欢我，"
            "但我感觉我对她很好啊，我们一起玩的时候也很开心。"
            "就是有时候她的话题我不感兴趣会敷衍，我的话题不感兴趣她也会敷衍，"
            "我觉得这正常吧")
    db = FakeChatSource(nick=settings.wechat.master_chat,
                        fts_text=full, summary=full[:47] + "…（截断）")
    pipe = PrivatePipeline(db=db, store=store, commands=commands,
                           chat_service=chat, sender=sender, settings=settings)

    pipe.handle("wxid_master", {"content": "[文本]", "type": "文本",
                                "sender_id": 5, "sort_seq": 100,
                                "local_id": 160})
    assert router.calls, "应调用 AI"
    assert "她说她其实" in router.calls[0][2]          # 恢复的明文进了提示词
    assert "我觉得这正常吧" in router.calls[0][2]      # 是完整全文，不是截断版
    assert not any("读不出" in t for _, t, _ in sender.sent)   # 没发"读不出"通知


def test_private_placeholder_unrecoverable_notice(tmp_path):
    """占位符且恢复失败：发提示，不调 AI，不静默丢弃。"""
    settings = cfg.load_settings()
    router = FakeRouter("x")
    store, chat, commands, _, _ = _services(tmp_path, settings, router)
    sender = FakeSender()
    db = FakeChatSource(nick=settings.wechat.master_chat, summary="")  # 恢复失败
    pipe = PrivatePipeline(db=db, store=store, commands=commands,
                           chat_service=chat, sender=sender, settings=settings)

    pipe.handle("wxid_master", {"content": "[文本]", "type": "文本",
                                "sender_id": 5, "sort_seq": 100})
    assert router.calls == []
    assert sender.sent and "读不出内容" in sender.sent[0][1]


def test_private_placeholder_image_skipped(tmp_path):
    """图片等非文本占位符：正常跳过，不通知。"""
    settings = cfg.load_settings()
    router = FakeRouter("x")
    store, chat, commands, _, _ = _services(tmp_path, settings, router)
    sender = FakeSender()
    db = FakeChatSource(nick=settings.wechat.master_chat)
    pipe = PrivatePipeline(db=db, store=store, commands=commands,
                           chat_service=chat, sender=sender, settings=settings)

    pipe.handle("wxid_master", {"content": "[图片]", "type": "图片",
                                "sender_id": 5, "sort_seq": 101})
    assert router.calls == []
    assert sender.sent == []


# ===================== 群聊流程 =====================

def test_group_pipeline_mention_and_schedule(tmp_path):
    settings = cfg.load_settings()
    router = FakeRouter(
        reply="记好了\n<actions>\nschedule: 明天下午3点 高数考试\nweather: 安阳\n</actions>")
    store, chat, commands, active, _ = _services(tmp_path, settings, router)
    sender = FakeSender()
    db = FakeChatSource(nick=settings.wechat.group_chats[0])

    class FakeSummary:
        def maybe_enqueue(self, chat_id): pass

    pipe = GroupPipeline(db=db, store=store, commands=commands,
                         chat_service=chat, sender=sender, settings=settings,
                         active_speak=active, summary=FakeSummary())

    msg = {"content": "wxid_a: @qianhua 帮我记个日程",
           "type": 1, "sort_seq": 100, "sender_id": 5,
           "create_time": time.time()}
    pipe.handle("wxid_group", msg)

    # 发送了回复（去掉 actions 后的干净文本 + 天气后缀）
    assert sender.sent, "应发送回复"
    target, text, _ = sender.sent[-1]
    assert target == settings.wechat.group_chats[0]
    assert "记好了" in text
    assert "安阳今天晴" in text
    assert "<actions>" not in text

    # 日程已存（明天下午3点）
    scheds = store.load_schedules("wxid_group")
    assert len(scheds) == 1
    assert "高数考试" in scheds[0]["event"]
    # 群聊对话历史已保存
    hist = store.load_history("wxid_group")
    assert len(hist) == 2


def test_group_pipeline_no_mention_no_reply(tmp_path):
    settings = cfg.load_settings()
    router = FakeRouter("（安静）")
    store, chat, commands, active, _ = _services(tmp_path, settings, router)
    sender = FakeSender()
    db = FakeChatSource()
    pipe = GroupPipeline(db=db, store=store, commands=commands,
                         chat_service=chat, sender=sender, settings=settings,
                         active_speak=active, summary=type("S", (), {
                             "maybe_enqueue": lambda self, c: None})())

    pipe.handle("wxid_group", {"content": "wxid_a: 随便聊聊",
                               "type": 1, "sender_id": 5})
    # 没 @ → 主动发言返回（安静）→ 不发送
    assert sender.sent == []
    # 但上下文记录了
    ctx = store.load_context("wxid_group")
    assert ctx and ctx[-1]["content"] == "随便聊聊"


# ===================== 指令 =====================

def test_mask_switch_isolates_data(tmp_path):
    settings = cfg.load_settings()
    router = FakeRouter("ok")
    store, chat, commands, _, _ = _services(tmp_path, settings, router)

    # 默认面具：写记忆
    store.append_memory("wxid_master", "默认面具的记忆")
    # 切到女友
    assert store.set_active_mask("wxid_master", "女友")
    assert store.get_active_mask("wxid_master") == "女友"
    # 新面具记忆是空的
    assert store.load_memory("wxid_master", "女友") == ""
    store.append_memory("wxid_master", "女友的记忆", "女友")
    # 互不影响
    assert "默认面具的记忆" in store.load_memory("wxid_master")
    assert "女友的记忆" in store.load_memory("wxid_master", "女友")

    # mask 命令切换
    reply = commands.try_handle("wxid_master", "mask ai助手", is_group=False)
    assert "ai助手" in reply


# ===================== 日程提醒 =====================

def test_reminder_fires_due_and_skips_catchup(tmp_path):
    settings = cfg.load_settings()
    settings.behavior.catchup_window_minutes = 60
    router = FakeRouter("x")
    store, _, _, _, _ = _services(tmp_path, settings, router)
    sender = FakeSender()
    reminder = ReminderService(store, sender, settings)
    reminder.register_target("wxid_master", "浅华")

    now = datetime.now()
    # 一条在窗口内（30 分钟前到期）、一条超窗（2 小时前到期）
    store.add_schedule("wxid_master", now - timedelta(minutes=30), "窗口内提醒")
    store.add_schedule("wxid_master", now - timedelta(hours=2), "过期提醒")

    reminder._fire(now)
    texts = [t for _, t, _ in sender.sent]
    assert any("窗口内提醒" in t for t in texts)
    assert not any("过期提醒" in t for t in texts)
    # 全部标记已提醒，避免重复
    due = store.due_schedules("wxid_master", now + timedelta(seconds=1),
                              timedelta(minutes=60))
    assert due == []


def test_reminder_next_due_driven(tmp_path):
    settings = cfg.load_settings()
    store = FileChatStore(tmp_path / "data", settings.behavior, settings.personas)
    assert store.next_due("wxid_x", datetime.now()) is None
    when = datetime.now().replace(second=0, microsecond=0) + timedelta(hours=1)
    store.add_schedule("wxid_x", when, "一小时后的事")
    assert store.next_due("wxid_x", datetime.now()) == when


def test_schedule_delete_and_list_all(tmp_path):
    """Web 日程管理用到的存储方法：删除 + 聚合列表。"""
    settings = cfg.load_settings()
    store = FileChatStore(tmp_path / "data", settings.behavior, settings.personas)
    when = datetime.now().replace(second=0, microsecond=0) + timedelta(days=1)

    store.add_schedule("wxid_a", when, "日程A")
    store.add_schedule("wxid_b", when, "日程B")
    # 面具下的日程也应被聚合
    store.set_active_mask("wxid_a", "女友")
    store.add_schedule("wxid_a", when, "面具日程", "女友")

    all_s = store.list_all_schedules()
    events = {(x["chat_id"], x["item"]["event"]) for x in all_s}
    assert ("wxid_a", "日程A") in events
    assert ("wxid_b", "日程B") in events
    assert ("wxid_a", "面具日程") in events

    # 删除：先删面具下的
    sid_mask = next(x["item"]["id"] for x in all_s if x["item"]["event"] == "面具日程")
    assert store.delete_schedule("wxid_a", sid_mask, mask="女友")
    assert not store.delete_schedule("wxid_a", sid_mask, mask="女友")  # 已删
    # 再删顶层的
    sid_a = next(x["item"]["id"] for x in store.list_all_schedules()
                 if x["item"]["event"] == "日程A")
    assert store.delete_schedule("wxid_a", sid_a)
    left = [x["item"]["event"] for x in store.list_all_schedules()]
    assert "日程A" not in left and "面具日程" not in left
    assert "日程B" in left
