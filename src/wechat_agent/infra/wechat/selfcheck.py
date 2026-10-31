# -*- coding: utf-8 -*-
"""启动自检：多维度判断微信窗口是否真正"就绪"。

背景：WeChatGUI.desktop_available() 只靠"渲染区域截图白色像素占比 > 5%"判断，
锁屏浅色画面、二维码登录页、窗口最小化时都可能误报。这里做多维度检查：
- 窗口句柄有效 / 可见 / 未最小化（Win32 原生信号）
- UIA 能否定位聊天输入框（已登录且可聊天的【最可靠】信号）
- 渲染画面截图检测（锁屏/遮挡仅作参考）
"""
from __future__ import annotations

import ctypes
import time

from wechat_agent.logging_setup import log


def _has_current_chat(gui):
    """UIA 能否定位到聊天输入框（登录态的最可靠信号）。"""
    try:
        uia = gui._get_uia()  # 内部 ensure_window(wake=True)，顺便唤醒窗口
        if uia is None:
            return None
        return bool(uia.current_chat())
    except Exception:
        return None


def probe_wechat(gui, retries: int = 3, interval: float = 1.5):
    """
    多维度自检微信窗口。返回 (ready, checks)。
    checks: [(名称, 是否通过, 说明)]，None 表示该项无法检测。
    """
    u = ctypes.windll.user32
    checks = None
    for attempt in range(retries):
        alive = bool(u.IsWindow(gui.main_hwnd))
        visible = alive and bool(u.IsWindowVisible(gui.main_hwnd))
        minimized = alive and bool(u.IsIconic(gui.main_hwnd))
        has_chat = _has_current_chat(gui)
        try:
            screen_ok = gui.desktop_available()
        except Exception:
            screen_ok = False

        checks = [
            ("窗口存在", alive, None),
            ("窗口可见", visible, None),
            ("未最小化", not minimized, None),
            ("当前会话", has_chat, "UIA 能否定位聊天输入框（登录态的最可靠信号）"),
            ("画面正常", screen_ok, "渲染区域截图检测（锁屏/遮挡仅参考）"),
        ]
        if has_chat is not None:
            ready = alive and visible and (not minimized) and has_chat
        else:
            ready = alive and visible and (not minimized) and screen_ok
        if ready:
            return True, checks
        if attempt < retries - 1:
            time.sleep(interval)
    return False, checks


def log_checks(checks) -> None:
    """逐项打印自检结果。"""
    for name, ok, note in checks:
        suffix = f"（{note}）" if note else ""
        if ok is True:
            log(f"      ✅ {name}{suffix}")
        elif ok is False:
            log(f"      ⚠️ {name}{suffix}", "warning")
        else:
            log(f"      ℹ️ {name}：无法检测{suffix}")
