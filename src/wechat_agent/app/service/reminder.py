# -*- coding: utf-8 -*-
"""日程主动提醒：事件驱动（next-deadline），无日程时零开销。

- 算出最近的到期时间，睡到那一刻才醒（差多少秒睡多少秒）；
- 有新日程加入时 notify() 唤醒重算；
- 到点发送「【日程提醒】时间 事项」，标记 reminded 防重复；
- 离线补发窗口：只发到期时间在 catchup_window 内的，更早的丢弃（防轰炸）。
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

from wechat_agent.config import Settings
from wechat_agent.logging_setup import log

_REMIND_PREFIX = "【日程提醒】"


class ReminderService:
    def __init__(self, store, sender, settings: Settings):
        self._store = store
        self._sender = sender
        self._s = settings
        self._targets: dict[str, str] = {}   # chat_id -> 昵称
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ---- 目标注册 ----

    def register_target(self, chat_id: str, nick: str) -> None:
        self._targets[chat_id] = nick

    def unregister_target(self, chat_id: str) -> None:
        self._targets.pop(chat_id, None)

    @property
    def targets(self) -> dict:
        return dict(self._targets)

    # ---- 唤醒 ----

    def notify(self) -> None:
        """有新日程加入时调用：唤醒循环重算下一个到期时间。"""
        self._wake.set()

    # ---- 生命周期 ----

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop,
                                        name="reminder", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    # ---- 主循环 ----

    def _loop(self) -> None:
        log("  ├ [提醒] 日程提醒服务已启动（事件驱动，无日程时零开销）")
        while not self._stop.is_set():
            now = datetime.now()
            next_due = self._compute_next(now)
            if next_due is None:
                self._wake.wait(10)     # 兜底周期：新注册目标也能被看到
                self._wake.clear()
                continue
            delay = (next_due - now).total_seconds()
            if delay <= 0:
                self._fire(now)
                continue
            self._wake.wait(timeout=min(delay, 300))
            if self._wake.is_set():
                self._wake.clear()
                continue
            self._fire(datetime.now())

    def _compute_next(self, now: datetime) -> datetime | None:
        best = None
        for chat_id in self._targets:
            try:
                due = self._store.next_due(chat_id, now)
            except Exception:
                continue
            if due and (best is None or due < best):
                best = due
        return best

    def _fire(self, now: datetime) -> None:
        window = timedelta(minutes=self._s.behavior.catchup_window_minutes)
        for chat_id, nick in list(self._targets.items()):
            try:
                due = self._store.due_schedules(chat_id, now, window)
            except Exception as e:
                log(f"  ⚠️ [提醒] 检查失败（{nick}）：{e}", "warning")
                continue
            for when, event in due:
                text = f"{_REMIND_PREFIX}{when:%m-%d %H:%M} {event}"
                log(f"⏰ 主动提醒 → {nick}：{event}")
                self._sender.enqueue(nick, text, chat_id=chat_id, drop_ok=True)
