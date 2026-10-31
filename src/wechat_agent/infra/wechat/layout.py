# -*- coding: utf-8 -*-
"""微信布局配置工具（移植自旧项目 tools/gen_layout.py）。

背景：本机 WinRT OCR 不可用（调用挂起），而 wechatauto 在"布局配置与窗口
尺寸不匹配"时会触发 OCR 校准（卡死）。本模块用库内置默认比例 + 当前窗口
尺寸直接生成配置，让库认为"已校准过"从而跳过 OCR。
"""
from __future__ import annotations

import ctypes
import json
import os
import time

from wechatauto import guia

u = ctypes.windll.user32

_SW_RESTORE = 9


def find_windows_by_class_prefix(prefix: str) -> list:
    """按类名前缀枚举顶级窗口，返回句柄列表。"""
    found = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, lparam):
        buf = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, buf, 256)
        if buf.value.startswith(prefix):
            found.append(hwnd)
        return True

    u.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def _get_rect(hwnd) -> tuple:
    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]
    r = RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _current_window_size() -> tuple:
    """返回当前微信窗口尺寸 (w, h)，找不到返回 (None, None)。"""
    render_windows = find_windows_by_class_prefix(guia.WX_RENDER_WIN_CLASS_PREFIX)
    main_windows = find_windows_by_class_prefix(guia.WX_MAIN_WIN_CLASS_PREFIX)
    if not main_windows:
        return None, None
    hwnd = render_windows[0] if render_windows else main_windows[0]
    l, t, r, b = _get_rect(hwnd)
    return r - l, b - t


def _write_layout(render_w, render_h) -> str:
    """用默认比例写布局配置文件。"""
    layout = {
        "machine": guia._machine_id(),
        "sidebar_ratio": guia.SIDEBAR_RATIO,
        "send_button_ratio": list(guia.SEND_BUTTON_RATIO),
        "render_w": render_w,
        "render_h": render_h,
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = guia._layout_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(layout, f, ensure_ascii=False, indent=2)
    return path


def ensure_layout() -> bool:
    """
    检查布局配置是否匹配当前微信窗口尺寸；不匹配则用默认比例重新生成。
    窗口最小化时先恢复再测尺寸，避免把"最小化的小尺寸"写进配置。
    """
    # 1. 若微信窗口处于最小化状态，先恢复
    main_windows = find_windows_by_class_prefix(guia.WX_MAIN_WIN_CLASS_PREFIX)
    for hwnd in main_windows:
        try:
            if u.IsIconic(hwnd):
                u.ShowWindow(hwnd, _SW_RESTORE)
                time.sleep(0.6)
                break
        except Exception:
            continue

    w, h = _current_window_size()
    if w is None:
        return False
    # 2. 读现有配置，判断是否匹配（阈值与 guia._load_layout 一致：>15% 视为不匹配）
    path = guia._layout_path()
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            old_w = float(d.get("render_w", 0) or 0)
            if old_w > 0 and abs(old_w - w) / max(w, 1) <= 0.15:
                return True
        except Exception:
            pass
    # 3. 不匹配：用默认比例重新生成（不调 OCR）
    try:
        _write_layout(w, h)
        print(f"[布局] 窗口尺寸变化，已自动重新生成布局配置 {w}x{h}", flush=True)
        return True
    except Exception as e:
        print(f"[布局] 重新生成失败：{e}", flush=True)
        return False
