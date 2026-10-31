# -*- coding: utf-8 -*-
"""固定间隔任务调度器：水位落盘 / 心跳 / 日程清理 等统一管理。"""
from __future__ import annotations

import threading
import time

from wechat_agent.logging_setup import log


class JobScheduler:
    def __init__(self):
        self._jobs: list = []          # (name, fn, seconds, next_run)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def add(self, name: str, fn, seconds: float) -> None:
        self._jobs.append((name, fn, seconds, time.monotonic() + seconds))

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop,
                                        name="job-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            for i, (name, fn, seconds, next_run) in enumerate(self._jobs):
                if now >= next_run:
                    try:
                        fn()
                    except Exception as e:
                        log(f"  ⚠️ [任务] {name} 执行失败：{e}", "error")
                    self._jobs[i] = (name, fn, seconds, now + seconds)
            # 睡到下一个任务到期
            nxt = min((j[3] for j in self._jobs), default=now + 5)
            self._stop.wait(min(max(0.5, nxt - time.monotonic()), 5))
