# -*- coding: utf-8 -*-
"""LLM 路由：按会话策略选择通道，并处理失败降级。

降级规则（安全优先）：
- mode="api" 且带受限补丁（群聊）→ 失败自动降级 harness（同样带补丁），
  绝不让受限场景静默升级为全权限；
- mode="api" 且无补丁（私聊直连）→ 失败直接抛错（不自动切 harness，避免
  私聊任务被静默改成慢通道造成意外）；
- mode="harness" → 直接走 headless。
"""
from __future__ import annotations

from wechat_agent.logging_setup import log
from wechat_agent.ports.llm import LLMResult


class LLMRouter:
    def __init__(self, api, harness, group_patch: str | None = None):
        self._api = api
        self._harness = harness
        self._group_patch = group_patch

    @property
    def group_patch(self) -> str | None:
        return self._group_patch

    def call(self, prompt: str, *, mode: str, patch: str | None = None) -> LLMResult:
        """mode: "api" | "harness"。patch：受限模式补丁（群聊用）。"""
        if mode == "harness":
            return self._harness.call(prompt, patch=patch)

        # api 模式
        try:
            return self._api.call(prompt)
        except Exception as e:
            if patch:
                log(f"  ⚠️ [api] 失败（{e}），自动降级 harness（带安全补丁）", "warning")
                return self._harness.call(prompt, patch=patch)
            raise
