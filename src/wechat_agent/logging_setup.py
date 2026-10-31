# -*- coding: utf-8 -*-
"""日志：控制台 + 文件双输出，支持 trace_id，并推送实时流给 Web UI。

- 控制台符号可读性：emoji 全部替换成 ASCII 标签（避免在部分终端显示成方块）；
- LogBroker：环形缓冲（供 Web UI /api/logs 拉最近日志）+ 订阅者队列
  （供 Web UI SSE 实时推送）；log() 同时写入控制台、文件、broker。
- trace_id：每条消息一个短 id，方便在日志里追踪一条消息的完整生命周期。

注意：wechatauto 库导入时会清空根日志器 handler。setup_logging 在组合根
里调用，统一接管根 handler。
"""
from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import uuid
from collections import deque

_tls = threading.local()


# ===================== emoji → ASCII =====================

_EMOJI_MAP = {
    "🤖": "[bot]", "📋": "[cfg]", "📇": "[cfg]", "✅": "[OK]", "👍": "[OK]",
    "⚠️": "[!!]", "⚠": "[!!]", "❌": "[X]", "✗": "[X]", "✘": "[X]",
    "🆗": "[OK]", "📩": "[in]", "📥": "[in]", "📤": "[out]", "💬": "[ai]",
    "🔖": "[cmd]", "⏰": "[alarm]", "⏳": "[tick]", "⏱": "[tick]",
    "🖥️": "[win]", "🖥": "[win]", "📱": "[wx]", "🔍": "[scan]",
    "🔔": "[bell]", "📌": "[pin]", "🚀": "[go]", "🔁": "[loop]",
    "🗑": "[del]", "🧠": "[mem]", "🛠": "[tool]", "🌐": "[net]", "🌦": "[wxq]",
    "😄": " :D", "😅": " :P", "😭": " :'(",
}
_ASTRAL_RE = None


def sanitize_console(text: str) -> str:
    """把 emoji 替换成 ASCII 标签，去掉其余 4 字节特殊字符，控制台可读。"""
    global _ASTRAL_RE
    import re
    if _ASTRAL_RE is None:
        # 4 字节 UTF-8（补充平面，绝大部分是 emoji）
        _ASTRAL_RE = re.compile(
            "[\U0001F000-\U0001FAFF\U00002700-\U000027BF]")
    if not text:
        return text
    for k, v in _EMOJI_MAP.items():
        if k and k in text:
            text = text.replace(k, v)
    return _ASTRAL_RE.sub("", text)


# ===================== trace id =====================

def set_trace_id(tid: str) -> None:
    _tls.tid = tid


def get_trace_id() -> str:
    return getattr(_tls, "tid", "-")


def new_trace_id() -> str:
    return uuid.uuid4().hex[:8]


# ===================== LogBroker（实时流 + 最近日志） =====================

class LogBroker:
    """日志环形缓冲 + 订阅者广播。Web UI 的 SSE 和 /api/logs 都靠它。"""

    _instance = None

    def __init__(self, capacity: int = 600):
        self._capacity = capacity
        self._buf: deque = deque(maxlen=capacity)
        self._lock = threading.RLock()
        self._subs: dict = {}          # ident -> queue.Queue
        self._next_id = 0

    @classmethod
    def instance(cls) -> "LogBroker":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def push(self, line: str) -> None:
        with self._lock:
            self._buf.append(line)
            dead = []
            for ident, q in list(self._subs.items()):
                try:
                    q.put_nowait(line)
                except queue.Full:
                    dead.append(ident)
            for ident in dead:
                self._subs.pop(ident, None)

    def snapshot(self) -> list:
        with self._lock:
            return list(self._buf)

    def subscribe(self) -> int:
        with self._lock:
            ident = self._next_id
            self._next_id += 1
            self._subs[ident] = queue.Queue(maxsize=500)
            return ident

    def pull(self, ident: int, timeout: float = 1.0) -> str | None:
        """同步等待该订阅者的下一条日志（供 SSE）；无则返回 None。"""
        with self._lock:
            q = self._subs.get(ident)
        if q is None:
            return None
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            return None

    def unsubscribe(self, ident: int) -> None:
        with self._lock:
            self._subs.pop(ident, None)


broker = LogBroker.instance


# ===================== 日志初始化 =====================

class _TraceFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.tid = get_trace_id()
        return super().format(record)


_configured = False


def setup_logging(log_dir: str | None = None, level: int = logging.INFO) -> None:
    """初始化根日志器：控制台（stdout，ASCII 标签）+ 文件（带 trace_id）。幂等。"""
    global _configured
    if _configured:
        return
    root = logging.getLogger()
    root.setLevel(level)

    # 清掉第三方（wechatauto）加的根 handler，统一由我们输出
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(
        "[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
    root.addHandler(console)

    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        fh = logging.FileHandler(
            os.path.join(log_dir, "bridge.log"), encoding="utf-8")
        fh.setFormatter(_TraceFormatter(
            "%(asctime)s [%(levelname)s] [tid=%(tid)s] %(message)s"))
        fh.setLevel(logging.DEBUG)
        root.addHandler(fh)

    # 第三方库日志收敛，避免刷屏
    for name in ("comtypes", "urllib3", "requests", "asyncio", "PIL"):
        logging.getLogger(name).setLevel(logging.WARNING)

    _configured = True


def log(msg: str, level: str = "info") -> None:
    """统一分步提示：写入控制台（ASCII 标签）、文件（trace_id）、实时流 broker。"""
    text = sanitize_console(str(msg))
    logger = logging.getLogger("bridge")
    if level == "warning":
        logger.warning(text)
    elif level == "error":
        logger.error(text)
    else:
        logger.info(text)
    # 实时流：标记级别 + 完整内容（Web UI 上 emoji 可正常显示）
    raw = str(msg)
    broker().push(f"[{level.upper()}] {get_trace_id()} {raw}")
