# -*- coding: utf-8 -*-
"""会话服务：一次对话的编排（提示词构建 + 通道路由 + 指令执行 + 历史保存）。

模式路由：
- 私聊默认 harness（全权限）；消息带【直连】/【api】切 API，带【harness】切回；
- 群聊默认 settings.llm.group_default（api）；带【harness】切受限 harness；
- 偏好按会话持久化（mode.json）。
"""
from __future__ import annotations

import time

from wechat_agent.app import prompts as prompt_builders
from wechat_agent.config import Settings
from wechat_agent.domain.message import is_group
from wechat_agent.logging_setup import log


class ChatService:
    _API_MARKS = ("【直连】", "【api】", "【API】", "[api]", "[API]")
    _HARNESS_MARKS = ("【harness】", "【HARNESS】", "[harness]", "[HARNESS]")

    def __init__(self, store, router, settings: Settings, skill_loader,
                 directives, session_manager=None):
        self._store = store
        self._router = router
        self._s = settings
        self._skills = skill_loader
        self._directives = directives
        self._sessions = session_manager   # 可选的会话管理器（mask/共享记忆来源）

    def set_session_manager(self, sm) -> None:
        self._sessions = sm

    # ===================== 模式开关 =====================

    def _detect_mode(self, text: str) -> str | None:
        low = (text or "").lower()
        if any(m in low for m in ("【直连】", "【api】", "[api]")):
            return "api"
        if any(m in low for m in ("【harness】", "[harness]")):
            return "harness"
        return None

    def strip_mode_marks(self, text: str) -> str:
        for m in self._API_MARKS + self._HARNESS_MARKS:
            text = text.replace(m, "")
        return text.strip()

    def _session_mode(self, chat_id: str) -> str:
        """读 Web 会话页设置的 per-session 模式（""=未显式设置→用全局）。"""
        if self._sessions is not None:
            try:
                st = self._sessions.get(chat_id)
                if st:
                    m = getattr(st, "mode", "")
                    return m if m in ("api", "harness") else ""
            except Exception:
                pass
        return ""

    def _remember_mode(self, chat_id: str, mode: str) -> None:
        """把显式设置的模式同步到 mode.json 与 SessionManager（命令/标记用）。"""
        try:
            self._store.set_mode(chat_id, mode)
        except Exception:
            pass
        if self._sessions is not None:
            try:
                self._sessions.set_mode(chat_id, mode)
            except Exception:
                pass

    def resolve_private_mode(self, chat_id: str, text: str) -> tuple:
        """解析私聊模式，返回 (mode, 清理后的文本)。

        优先级：消息标记【直连】/【harness】→ Web 会话页 per-session 模式
        → mode.json 历史偏好 → 默认 harness。
        """
        sw = self._detect_mode(text)
        cleaned = self.strip_mode_marks(text)
        if sw == "api":
            self._remember_mode(chat_id, "api")
            return "api", cleaned
        if sw == "harness":
            self._remember_mode(chat_id, "harness")
            return "harness", cleaned
        sm = self._session_mode(chat_id)
        if sm:
            return sm, cleaned
        pref = self._store.get_mode(chat_id)
        return ("harness" if pref == "harness" else "api"), cleaned

    def resolve_group_mode(self, chat_id: str, mentions: list) -> tuple:
        """解析群聊模式，返回 (mode, 清理后的 mentions)。

        优先级同私聊；Web 会话页 per-session 模式优先于全局 group_default。
        """
        switch = None
        cleaned = []
        for nick, body in mentions:
            sw = self._detect_mode(body)
            if sw is not None:
                switch = sw
            cleaned.append((nick, self.strip_mode_marks(body)))
        if switch == "api":
            self._remember_mode(chat_id, "api")
            return "api", cleaned
        if switch == "harness":
            self._remember_mode(chat_id, "harness")
            return "harness", cleaned
        sm = self._session_mode(chat_id)
        if sm:
            return sm, cleaned
        pref = self._store.get_mode(chat_id)
        if pref == "harness":
            return "harness", cleaned
        if pref == "api":
            return "api", cleaned
        return (self._s.llm.group_default or "api"), cleaned

    # ===================== 人设：统一由面具(mask)决定 =====================

    def resolve_mask(self, chat_id: str) -> str:
        """返回该聊天当前生效的面具名（来源：会话语义状态 → 历史 mask）。
        人设只由面具决定，不再区分 master/group 两套人设。"""
        if self._sessions is not None:
            try:
                st = self._sessions.get(chat_id)
                if st and getattr(st, "mask", ""):
                    return st.mask
            except Exception:
                pass
        if not is_group(chat_id):
            try:
                m = self._store.get_active_mask(chat_id)
                if m:
                    return m
            except Exception:
                pass
        return self._s.personas.default_mask

    def _persona_for(self, chat_id: str, mask: str | None) -> str:
        """人设 = 该聊天面具的 persona；未知/缺省用 default_mask。"""
        personas = self._s.personas
        name = mask or self.resolve_mask(chat_id)
        m = personas.masks.get(name)
        if m is not None:
            return m.persona
        fallback = personas.masks.get(personas.default_mask)
        return fallback.persona if fallback else ""

    def _rules_for(self, chat_id: str) -> list:
        return list(self._s.rules) + list(self._store.load_rules(chat_id))

    # ===================== 私聊一次对话 =====================

    def private_turn(self, chat_id: str, text: str, *,
                     mask: str | None = None, mode: str | None = None) -> tuple:
        """完整一次私聊对话，返回 (回复文本, 使用模式)。"""
        if mode is None:
            mode, text = self.resolve_private_mode(chat_id, text)
        self._store.append_history(chat_id, "user", text, mask)

        prompt = prompt_builders.build_private_task(
            persona=self._persona_for(chat_id, mask),
            rules=self._rules_for(chat_id),
            memory=self._store.load_memory(chat_id, mask),
            history=self._store.load_history(chat_id, mask),
            mode=mode,
            web_guide=self._web_guide(),
            max_chars=self._s.llm.max_task_chars,
        )
        ch = self.channel_label(mode)
        log(f"  ├ [ai] 私聊请求：模式={mode}  通道={ch}  提示词约{len(prompt)}字 ...")
        t0 = time.time()
        result = self._router.call(prompt, mode=mode)
        log(f"  ├ [ai] 回复生成完成：{mode} → {len(result.text)} 字（{time.time() - t0:.1f}s）")
        reply, suffix = self._directives.process(result.text, chat_id, mask)
        reply = reply + suffix
        self._store.append_history(chat_id, "assistant", reply, mask)
        return reply, mode

    def channel_label(self, mode: str) -> str:
        """返回本次调用实际使用的通道 + 模型/供应商（用于日志/看板）。"""
        if mode == "harness":
            return "本地 DeepSeek Harness（headless，模型见 Harness 设置）"
        api = self._s.llm
        host = ""
        if "opencode.ai" in api.api_url:
            host = "opencode.ai"
        elif "deepseek.com" in api.api_url:
            host = "DeepSeek 官方"
        return f"{host or api.api_url} / {api.api_model}"

    # ===================== 群聊一次对话 =====================

    def group_turn(self, chat_id: str, context: list, mentions: list) -> tuple:
        """群聊一次对话（@ 触发），返回 (回复文本, 使用模式)。"""
        mode, mentions = self.resolve_group_mode(chat_id, mentions)
        mask = self.resolve_mask(chat_id)     # 群聊也用会话选定的面具
        patch = self._router.group_patch  # 群聊永远带受限补丁（防升级）

        # 技能注入：基础始终注入 + 按需（命中关键词才注入）
        skills_text = self._skills.basic_text()
        probe = " ".join([c for _, c in context] + [b for _, b in mentions])
        extra = self._skills.on_demand_text(probe)
        injected = list(self._skills.match(probe))
        if extra:
            skills_text = (skills_text + "\n\n" + extra) if skills_text else extra
            log(f"  ├ [skill] 按需注入：{'、'.join(injected)}")
        if self._skills.basic_text() and self._s.skills.basic:
            log(f"  ├ [skill] 基础注入：{'、'.join(self._s.skills.basic)}")

        # 优先用 personas.yaml 里用户自定义的 group_guide，为空时用精简版
        group_guide = self._s.personas.group_guide or prompt_builders.build_group_guide_lite()
        prompt = prompt_builders.build_group_task(
            persona=self._persona_for(chat_id, mask),
            rules=self._rules_for(chat_id),
            memory=self._store.load_memory(chat_id, mask),
            group_guide=group_guide,
            context=context,
            mentions=mentions,
            history=self._store.load_history(chat_id, mask),
            skills_text=skills_text,
            mode=mode,
            web_guide=self._web_guide(),
            max_chars=self._s.llm.max_task_chars,
        )
        ch = self.channel_label(mode)
        log(f"  ├ [ai] 群聊请求：模式={mode}  面具={mask}  通道={ch}  "
            f"上下文{len(context)}条  @{len(mentions)}人  提示词约{len(prompt)}字")
        t0 = time.time()
        result = self._router.call(prompt, mode=mode, patch=patch)
        log(f"  ├ [ai] 回复生成完成：{mode} → {len(result.text)} 字（{time.time() - t0:.1f}s）")
        reply, suffix = self._directives.process(result.text, chat_id, mask)
        return reply + suffix, mode

    # ===================== 公共段 =====================

    def _web_guide(self) -> str:
        b = self._s.behavior
        return b.web_search_guide if b.web_search_guide_enabled else ""

