# -*- coding: utf-8 -*-
"""发送队列：所有发送（AI 回复 / 日程提醒 / 主动发言）唯一入口。

- 单一工作线程串行操作微信窗口（GUI 操作不能并发）；
- **复用同一个 WeChatGUI/UIA 实例**：UIA 驱动向 Weixin 进程挂接（热激活）
  一次要 10~30 秒，每次发送都新建实例等于每次都付这笔激活税；
  现在只建一次，会话失效（窗口重开）时才重建；
- **启动预热**：worker 线程启动时先做一次完整初始化，首个真实发送直接走快速路径；
- 失败自动重试（send_retry 次），重试后仍失败默认记日志；
  interactive_failover 开启时才阻塞等人工回车；
- 队列超限时丢弃 drop_ok 任务（主动发言），不丢 AI 回复；
- 纯 UIA 发送（不走会挂死的 OCR 兜底）。
"""
from __future__ import annotations

import queue
import threading
import time

from wechat_agent.domain.message import truncate_text
from wechat_agent.infra.wechat import layout
from wechat_agent.logging_setup import log


class SenderQueue:
    def __init__(self, max_size: int = 50, retry: int = 1,
                 interactive: bool = False, max_chars: int = 1500,
                 outbox_path=None):
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._max_size = max_size
        self._retry = max(0, retry)
        self._interactive = interactive
        self._max_chars = max_chars
        self._gui = None          # 复用的 WeChatGUI 实例（仅 worker 线程访问）
        from wechat_agent.infra.wechat.outbox import Outbox
        from pathlib import Path
        self._outbox = Outbox(outbox_path or Path(__file__).resolve().parents[4] / 'data' / 'outbox.sqlite3')

    # ---- Sender 协议 ----

    def enqueue(self, target: str, text: str, *,
                chat_id: str = "", drop_ok: bool = False) -> None:
        text = truncate_text(text, self._max_chars)
        if self._q.qsize() >= self._max_size:
            if drop_ok:
                log(f"  ⚠️ 发送队列已满，丢弃低优先级消息（→ {target}）", "warning")
                return
            # 关键消息也塞入（队列会被 worker 逐步消化）
            log(f"  ⚠️ 发送队列已满，但仍入队关键消息（→ {target}）", "warning")
        self._outbox.add(target, text, chat_id)

    # ---- 生命周期 ----

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, name="sender-queue",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def size(self) -> int:
        return self._outbox.size + self._q.qsize()

    # ---- 内部 ----

    def _worker(self) -> None:
        import pythoncom
        pythoncom.CoInitialize()
        log("  ├ [发送] 发送队列已启动，预热微信窗口/UIA（首次约 10-30s，仅一次）...")
        t0 = time.time()
        try:
            self._ensure_gui()
            log(f"  ├ [发送] 预热完成（{time.time() - t0:.1f}s），后续发送走快速路径")
        except Exception as e:
            log(f"  ⚠️ [发送] 预热失败（{e}），首次发送时重试", "warning")
        while not self._stop.is_set():
            try:
                task = self._q.get(timeout=0.5)
            except queue.Empty:
                task = self._outbox.next()
                if task is None:
                    continue
                self._deliver_pending(task)
                continue
            if task is None:
                break
            if isinstance(task, dict):
                try:
                    from wechat_agent.infra.wechat.native_stickers import save_latest
                    task['ok'] = save_latest(self._ensure_gui(), task['target'], task['id'])
                except Exception as error:
                    log(f'[native-sticker] Save failed: {type(error).__name__}', 'warning')
                finally:
                    task['done'].set()
                continue
            self._send_with_retry(*task)
        pythoncom.CoUninitialize()

    def _deliver_pending(self, task):
        from wechat_agent.infra.webui import get_status
        if time.time() - task['created'] > 6 * 3600:
            self._outbox.hold(task['id'])
            log('[发送] 待发回复超过6小时，保留供人工检查，不自动发送', 'warning')
            return
        if self._send_with_retry(task['target'], task['text'], task['chat_id']):
            self._outbox.finish(task['id'])
            get_status().incr('sent')
        elif '[[sticker:' in task['text'] or '[[image:' in task['text']:
            self._outbox.hold(task['id'])
            log('[发送] 多段媒体发送失败，保留供人工检查以免重复发送', 'warning')
        else:
            self._outbox.retry(task['id'])
            get_status().incr('send_retry')

    def _ensure_gui(self):
        """复用 GUI 实例：存在且窗口仍存活就直接用，否则重建（含 UIA 激活）。"""
        if self._gui is not None:
            try:
                if self._gui.is_alive():
                    return self._gui
            except Exception:
                pass
        self._gui = None
        # 关键：先确保布局配置匹配当前窗口尺寸，否则 WeChatGUI 构造会触发
        # OCR 校准而挂死（本机 WinRT OCR 不可用）。
        layout.ensure_layout()
        from wechatauto import WeChatGUI
        self._gui = WeChatGUI()
        uia = self._gui._get_uia()   # 触发 UIA 驱动初始化/热激活（慢，仅一次）
        if uia is None:
            log("  ⚠️ [发送] UIA 引擎不可用", "warning")
        return self._gui

    def collect_sticker(self, target, sticker_id):
        task={'target':target,'id':sticker_id,'done':threading.Event(),'ok':False}
        self._q.put(task)
        task['done'].wait(20)
        return task['ok']

    _MAX_INTERACTIVE_RETRIES = 5

    def _send_with_retry(self, target: str, text: str, chat_id: str) -> bool:
        t0 = time.time()
        interactive_rounds = 0
        while True:
            for attempt in range(self._retry + 1):
                try:
                    ok = self._send_via_uia(text, target)
                    if ok:
                        log(f"  ├ [发送] → {target} 成功（{time.time() - t0:.1f}s）")
                        return True
                    self._gui = None
                except Exception as e:
                    # 会话/驱动可能已失效：丢弃实例，下次发送重建
                    self._gui = None
                    log(f"  ⚠️ [发送] → {target} 异常：{e}", "warning")
                time.sleep(2)
            log(f"  ✗ [发送] → {target} 失败（已重试 {self._retry} 次）", "error")
            if not self._interactive:
                return False
            interactive_rounds += 1
            if interactive_rounds > self._MAX_INTERACTIVE_RETRIES:
                log(f"  ✗ [发送] → {target} 交互重试已达上限（{self._MAX_INTERACTIVE_RETRIES}），放弃", "error")
                return False
            try:
                input(
                    "\n⚠️ 未识别到微信对话框。\n"
                    "   请确认：① 微信窗口已打开且没锁屏 ② 微信窗口没被完全遮挡\n"
                    "   确认后按【回车】重试一次；或按 Ctrl+C 放弃这条消息……")
            except (KeyboardInterrupt, EOFError):
                log("  [发送] 用户放弃重试", "warning")
                return False

    def _send_via_uia(self, text: str, target: str) -> bool:
        """纯 UIA 发送：打开目标会话 → 输入文字 → 回车。任一步失败返回 False。"""
        try:
            gui = self._ensure_gui()
        except Exception as e:
            log(f"  ✗ [发送] 微信窗口初始化失败：{e}", "error")
            return False

        import re
        from pathlib import Path
        native = re.findall(r'\[\[sticker:([A-Za-z0-9_-]+)\]\]', text)
        if native:
            from wechat_agent.infra.wechat.native_stickers import send_registered
            caption = re.sub(r'\[\[sticker:[^\]]+\]\]', '', text).strip()
            if caption and not self._send_via_uia(caption, target):
                return False
            return send_registered(gui, target, native[0])
        matches = re.findall(r'\[\[image:([A-Za-z0-9_-]+\.png)\]\]', text)
        if matches:
            from wechat_agent.config import load_settings
            root = (Path(load_settings().llm.work_dir) / 'stickers').resolve()
            path = (root / matches[0]).resolve()
            if path.parent != root or not path.is_file():
                raise ValueError('Sticker not in local library')
            caption = re.sub(r'\[\[image:[^\]]+\]\]', '', text).strip()
            if caption and not self._send_via_uia(caption, target):
                return False
            result = gui.send_image(str(path), who=target)
            return bool(result)

        uia = gui._get_uia()
        if uia is None:
            log("  ✗ [发送] UIA 引擎不可用", "warning")
            return False
        cur = uia.current_chat()
        if cur != target:
            if not uia.open_chat(target):
                log(f"  ✗ [发送] 打开会话失败 目标='{target}'（当前='{cur}'）", "warning")
                return False
        if not uia.send_text(text):
            log(f"  ✗ [发送] 输入/发送失败 目标='{target}'", "warning")
            return False
        return True
