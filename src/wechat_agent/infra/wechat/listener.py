# -*- coding: utf-8 -*-
"""监听适配：封装 wechatauto.db.Listener（轮询微信数据库 + 每会话串行分发）。

- 水位（watermark）持久化到磁盘：重启后从断点续读，补回离线消息且不重复；
- 每会话一条串行工作线程：同一会话消息保序，不同会话并行（已由库保证）。
"""
from __future__ import annotations

from wechatauto.db import Listener

from wechat_agent.infra.wechat.db import DBAdapter
from wechat_agent.logging_setup import log


class ListenerAdapter:
    def __init__(self, db_adapter: DBAdapter, interval: float = 1.0,
                 watermark: dict | None = None):
        self._db = db_adapter
        self._lst = Listener(db_adapter.raw, interval=interval, watermark=watermark)
        self._callbacks: dict[str, object] = {}

    def register(self, chat_id: str, callback) -> None:
        """注册新消息回调：callback(msg: dict, listener)。"""
        if chat_id in self._callbacks:
            log(f"  ⚠️ [监听] 会话已注册，跳过：{chat_id}", "warning")
            return
        self._lst.add_listener(chat_id, callback)
        self._callbacks[chat_id] = callback

    def detach(self, chat_id: str) -> None:
        """动态移除某会话的监听（Web 会话管理用）。"""
        cb = self._callbacks.pop(chat_id, None)
        if cb is not None:
            self._lst.remove_listener(chat_id, cb)
            log(f"  ├ [监听] 已移除监听：{chat_id}")

    @property
    def attached(self) -> list:
        return list(self._callbacks.keys())

    def start(self) -> None:
        self._lst.start()
        log("  ├ [监听] 已启动")

    def stop(self) -> None:
        self._lst.stop()

    @property
    def watermark(self) -> dict:
        return self._lst.watermark

    @property
    def raw_listener(self) -> Listener:
        return self._lst
