# -*- coding: utf-8 -*-
"""运行时补丁：组合根里对 wechatauto 做防御性 monkeypatch。

旧项目是直接改 site-packages 源码（重装库即丢）。这里改为启动时打补丁：
- WeChatUIA.current_chat：兼容微信 4.1.12+ 输入框 Name 带占位提示文本的问题。
补丁幂等（已打过则跳过），不侵入源码。
"""
from __future__ import annotations

from wechat_agent.logging_setup import log

# 与旧项目一致的占位提示列表（微信 4.1.12+ 输入框 Name 形如 "会话名 占位文本"）
_PLACEHOLDER_HINTS = (
    "按住 Ctrl + Win 使用语音输入文字",
    "按住 Ctrl 使用语音输入",
    "按住说话",
    "语音输入",
    "输入消息",
)


def _strip_placeholder(name: str) -> str:
    for hint in _PLACEHOLDER_HINTS:
        if hint in name:
            return name.split(hint)[0].strip()
    return name


def apply_patches() -> None:
    """启动时调用一次。幂等。"""
    from wechatauto.uia_driver import WeChatUIA

    orig = WeChatUIA.current_chat
    if getattr(orig, "_wechat_agent_patched", False):
        return

    def current_chat_patched(self):
        name = orig(self)
        if name:
            return _strip_placeholder(name) or None
        return name

    current_chat_patched._wechat_agent_patched = True
    WeChatUIA.current_chat = current_chat_patched
    log("  ├ [补丁] WeChatUIA.current_chat 占位文本兼容已应用")
