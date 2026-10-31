# -*- coding: utf-8 -*-
"""后台任务接口。

    JobScheduler      —— 固定间隔任务（水位落盘、心跳、摘要安全检查）
    ReminderScheduler —— 事件驱动日程提醒（next-deadline，无日程时零开销）
实现：infra 或 app/service（reminder 在 app/service 里）。
"""
from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class JobScheduler(Protocol):
    def add(self, name: str, fn: Callable[[], None], seconds: float) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...


@runtime_checkable
class ReminderScheduler(Protocol):
    def notify(self) -> None:
        """有新日程加入时唤醒，重算下一个到期时间。"""
        ...

    def start(self) -> None: ...
    def stop(self) -> None: ...
