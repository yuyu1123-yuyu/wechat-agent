# -*- coding: utf-8 -*-
"""AI 主动发言：非@消息时偶尔自然地插句话。

触发条件（任一）+ 冷却期已过：
- 消息里提到了我的昵称
- 像问句
- 群里冷场（最近 N 条都是同一个人发）
AI 判断值不值得说；AI 输出"（安静）"等则不发言。
"""
from __future__ import annotations

import time

from wechat_agent.app import prompts as prompt_builders
from wechat_agent.config import Settings
from wechat_agent.logging_setup import log

_QUIET_MARKS = ("（安静）", "安静", "没什么好说的", "无")


class ActiveSpeakService:
    def __init__(self, router, store, settings: Settings):
        self._router = router
        self._store = store
        self._s = settings
        self._last: dict[str, float] = {}

    def maybe_speak(self, chat_id: str, body: str, sender_nick: str) -> str | None:
        """返回要发的话；不值得/冷却中返回 None。"""
        b = self._s.behavior
        if not b.group_active_speak:
            return None
        now = time.time()
        if now - self._last.get(chat_id, 0) < b.group_active_cooldown:
            return None

        reason = self._trigger_reason(chat_id, body, sender_nick)
        if not reason:
            return None

        ctx = self._store.load_context(chat_id)
        tone = prompt_builders.build_group_tone(self._s.personas.group_persona_params)
        task = prompt_builders.build_active_speak_task(
            ctx, reason, tone, max_ctx=b.group_active_context)
        try:
            reply = self._router.call(task, mode="api").text.strip()
        except Exception as e:
            log(f"  ⚠️ [主动] 判断失败：{e}", "warning")
            return None
        if reply in _QUIET_MARKS:
            log("  ├ [主动] AI 决定保持安静")
            return None
        self._last[chat_id] = now
        return reply

    def _trigger_reason(self, chat_id: str, body: str, sender_nick: str) -> str | None:
        names = self._s.wechat.nick_names
        if any(n and n in body for n in names):
            return f"{sender_nick}提到了我"
        if self._is_question(body):
            return "群里有提问"
        if self._is_cold(chat_id):
            return "群里有点冷场"
        return None

    def _is_question(self, text: str) -> bool:
        if text.rstrip().endswith(("？", "?", "吗", "呢", "咋", "怎么")):
            return True
        for kw in ("什么", "哪", "谁", "几", "多少", "是不是", "要不要", "有没有", "为啥"):
            if kw in text:
                return True
        return False

    def _is_cold(self, chat_id: str) -> bool:
        """冷场判定：最近 N 条群消息都是同一个人发（自言自语）。"""
        n = self._s.behavior.group_active_cold_msg
        try:
            ctx = self._store.load_context(chat_id)
            recent = ctx[-n:]
            if len(recent) < n:
                return False
            senders = {m.get("sender") for m in recent}
            return len(senders) <= 1
        except Exception:
            return False
