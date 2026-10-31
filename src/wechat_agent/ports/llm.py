# -*- coding: utf-8 -*-
"""LLM 网关接口。

两种实现：
    infra.llm.dsh.HeadlessGateway   本地 DeepSeek Harness headless（全能力，慢）
    infra.llm.api.ApiGateway        DeepSeek API 直连（无工具，快）

调用方只依赖本接口 + 路由（infra.llm.gateway.LLMRouter）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class LLMResult:
    text: str
    mode: str          # "harness" | "api"
    elapsed: float


@runtime_checkable
class LLMGateway(Protocol):
    def call(self, prompt: str, patch: str | None = None) -> LLMResult:
        """执行一次完整对话。patch 为受限模式的 dsh 安全补丁（可选）。"""
        ...
