# -*- coding: utf-8 -*-
"""群聊记忆自动总结：后台 worker，不阻塞消息线程。

- maybe_enqueue：消息到达时轻量检查（非阻塞），达到阈值才投递；
- 每群同时只允许一个总结任务（去重锁）；
- 总结用 API 直连（快），失败兜底 harness + 安全补丁（群聊永不退化）。
"""
from __future__ import annotations

import queue
import threading

from wechat_agent.app import prompts as prompt_builders
from wechat_agent.config import Settings
from wechat_agent.logging_setup import log


class SummaryService:
    def __init__(self, store, router, settings: Settings):
        self._store = store
        self._router = router
        self._s = settings
        self._q: queue.Queue = queue.Queue()
        self._working: set = set()
        self._guard = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ---- 生命周期 ----

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker,
                                        name="summary-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    # ---- 触发 ----

    def maybe_enqueue(self, chat_id: str) -> None:
        """非阻塞检查：新消息积累达到阈值时投递总结任务。"""
        try:
            pending = self._store.pending_summary_count(chat_id)
        except Exception:
            return
        if pending < self._s.behavior.group_summary_every:
            return
        with self._guard:
            if chat_id in self._working:
                return
            self._working.add(chat_id)
        log(f"  ├ [总结] 投递群记忆总结任务（{chat_id}，{pending} 条新消息）")
        self._q.put(chat_id)

    # ---- worker ----

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                chat_id = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self.summarize(chat_id)
            except Exception as e:
                log(f"  ✗ [总结] 异常：{e}", "error")
            finally:
                with self._guard:
                    self._working.discard(chat_id)

    def summarize(self, chat_id: str) -> bool:
        ctx = self._store.load_context(chat_id)
        if not ctx:
            return False
        old_mem = self._store.load_memory(chat_id) or "（无）"
        task = prompt_builders.build_summary_task(ctx, old_mem)
        log(f"  ├ [总结] 群聊记忆自动总结（{len(ctx)} 条上下文）...")
        try:
            summary = self._router.call(task, mode="api").text
        except Exception:
            try:
                # 兜底也强制带安全补丁：群聊永不退化成全权限
                summary = self._router.call(
                    task, mode="harness", patch=self._router.group_patch).text
            except Exception as e:
                log(f"  ✗ [总结] 失败：{e}", "error")
                return False

        items = [l.strip() for l in summary.split("\n") if l.strip().startswith("- ")]
        if not items:
            items = [l.strip() for l in summary.split("\n") if l.strip()][:20]
        # 条数上限：超出裁剪
        max_lines = self._s.behavior.memory_max_lines
        if len(items) > max_lines:
            items = items[:max_lines]
        self._store.set_memory(chat_id, "\n".join(items))
        last_seq = max(m.get("seq", 0) for m in ctx)
        self._store.mark_summary_done(chat_id, last_seq)
        log(f"  ├ [总结] 记忆已更新：{len(items)} 条")
        return True
