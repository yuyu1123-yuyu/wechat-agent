# -*- coding: utf-8 -*-
"""安全策略模型（纯数据 + 补丁渲染规则）。

群聊 = 受限策略（无文件/命令/子代理）；私聊 = 全权限（无补丁）。
补丁渲染由 config.render_group_patch 完成，这里只定义数据结构。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Policy:
    """会话安全策略。full=True 表示全权限（私聊）。"""
    full: bool = True
    patch_path: str | None = None      # 受限模式下使用的 dsh --patch 文件

    def describe(self) -> str:
        return "全权限" if self.full else "受限（群聊安全补丁）"
