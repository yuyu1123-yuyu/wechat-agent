# -*- coding: utf-8 -*-
"""FileChatStore —— 每个聊天独立数据的文件存储实现。

目录结构（data/chats/<safe_id>/）：
    persona.md / rules.md / memory.md     文本，用户可手改
    history.json                          对话历史（版本化）
    schedules.json                        日程（版本化）
    mode.json / active_mask.json          通道偏好 / 面具
    group_context.jsonl                   群聊上下文（追加式 JSONL）
    summary_progress.json                 群记忆总结进度
    masks/<面具名>/                       私聊面具独立数据

并发：每个会话一把写锁（per-chat lock），总结线程与指令线程不会互相踩踏。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from wechat_agent.config import BehaviorConfig, PersonasConfig
from wechat_agent.domain import chat as chat_domain
from wechat_agent.domain import schedule as schedule_domain
from wechat_agent.infra.store import schema

_MEMORY_HEADER = "# AI 记忆（AI 自动维护）\n"
_RULES_HEADER = "# 高等级规则（用户专属，AI 只读）\n"


def _md_items(text: str) -> list:
    """只取 '- ' 开头的行（兼容手写注释/标题）。"""
    return [l.strip() for l in text.splitlines() if l.strip().startswith("- ")]


def _join_items(items) -> str:
    return "\n".join(f"- {it}" for it in items)


class FileChatStore:
    def __init__(self, data_dir: Path, behavior: BehaviorConfig,
                 personas: PersonasConfig):
        self._data = Path(data_dir)
        self._data.mkdir(parents=True, exist_ok=True)
        self._b = behavior
        self._p = personas
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # 记忆共享路由：(chat_id, mask) -> 共享记忆目录；不在表中则用该聊天自己的目录。
        self._memory_scope: dict[tuple, Path] = {}
        self._mem_lock = threading.RLock()

    # ===================== 公开查询接口 =====================

    @property
    def default_mask(self) -> str:
        """返回配置中的默认面具名。"""
        return self._p.default_mask

    @property
    def data_dir(self) -> Path:
        """返回数据根目录。"""
        return self._data

    # ===================== 记忆共享路由 =====================

    def set_memory_scope(self, chat_id: str, mask: str,
                         shared_dir: Path | None) -> None:
        """配置该聊天的记忆作用域：shared_dir 提供时，此聊天的该 mask 记忆
        读写到 shared_dir（多聊天共享同一面具记忆）；None 恢复为独立记忆。"""
        key = (chat_id, mask)
        with self._mem_lock:
            if shared_dir is None:
                self._memory_scope.pop(key, None)
            else:
                shared_dir.mkdir(parents=True, exist_ok=True)
                self._memory_scope[key] = Path(shared_dir)

    def _memory_dir(self, chat_id: str, mask: str | None) -> Path:
        """返回该聊天(当前生效 mask)的记忆目录（共享 or 独立）。"""
        scope = self._scope(chat_id, mask) if mask else self._scope(chat_id)
        if mask:
            with self._mem_lock:
                sh = self._memory_scope.get((chat_id, mask))
            if sh is not None:
                return sh
        return scope

    # ===================== 内部 =====================

    def _lock_for(self, chat_id: str) -> threading.RLock:
        """每会话一把可重入写锁（RLock）：append_history 等会嵌套调用
        load_* 再拿同一把锁，普通 Lock 会造成自死锁。"""
        with self._locks_guard:
            lock = self._locks.get(chat_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[chat_id] = lock
            return lock

    def _chat_dir(self, chat_id: str) -> Path:
        d = self._data / "chats" / chat_domain.safe_dir_name(chat_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _scope(self, chat_id: str, mask: str | None = None) -> Path:
        return chat_domain.scope_dir(
            self._chat_dir(chat_id), chat_id, mask, self._p.default_mask)

    def _active_mask(self, chat_id: str) -> str:
        return self.get_active_mask(chat_id)

    # ===================== 记忆（memory.md） =====================

    def load_memory(self, chat_id: str, mask: str | None = None) -> str:
        with self._lock_for(chat_id):
            path = self._memory_dir(chat_id, mask) / "memory.md"
            if not path.is_file():
                return ""
            try:
                return "\n".join(_md_items(path.read_text(encoding="utf-8")))
            except Exception:
                return ""

    def append_memory(self, chat_id: str, text: str, mask: str | None = None) -> bool:
        with self._lock_for(chat_id):
            path = self._memory_dir(chat_id, mask) / "memory.md"
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write("- " + text.strip().replace("\n", " ") + "\n")
                return True
            except Exception:
                return False

    def count_memory(self, chat_id: str, mask: str | None = None) -> int:
        with self._lock_for(chat_id):
            return len(_md_items(self.load_memory(chat_id, mask)))

    def set_memory(self, chat_id: str, text: str, mask: str | None = None) -> None:
        with self._lock_for(chat_id):
            path = self._memory_dir(chat_id, mask) / "memory.md"
            items = _md_items(text)
            path.write_text(_MEMORY_HEADER + _join_items(items) + "\n",
                            encoding="utf-8")

    def clear_memory(self, chat_id: str, mask: str | None = None) -> bool:
        with self._lock_for(chat_id):
            path = self._memory_dir(chat_id, mask) / "memory.md"
            try:
                path.write_text(_MEMORY_HEADER, encoding="utf-8")
                return True
            except Exception:
                return False

    # ===================== 高等级规则（rules.md） =====================

    def load_rules(self, chat_id: str) -> list:
        with self._lock_for(chat_id):
            path = self._scope(chat_id) / "rules.md"
            if not path.is_file():
                return []
            try:
                return _md_items(path.read_text(encoding="utf-8"))
            except Exception:
                return []

    def add_rule(self, chat_id: str, text: str) -> bool:
        with self._lock_for(chat_id):
            path = self._scope(chat_id) / "rules.md"
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write("- " + text.strip().replace("\n", " ") + "\n")
                return True
            except Exception:
                return False

    def clear_rules(self, chat_id: str) -> bool:
        with self._lock_for(chat_id):
            path = self._scope(chat_id) / "rules.md"
            try:
                path.write_text(_RULES_HEADER, encoding="utf-8")
                return True
            except Exception:
                return False

    # ===================== 专属性格（persona.md） =====================

    def load_persona(self, chat_id: str, default: str,
                     mask: str | None = None) -> str:
        with self._lock_for(chat_id):
            path = self._scope(chat_id, mask) / "persona.md"
            if path.is_file():
                try:
                    content = path.read_text(encoding="utf-8").strip()
                    if content:
                        return content
                except Exception:
                    pass
            return default

    # ===================== 对话历史（history.json） =====================

    def load_history(self, chat_id: str, mask: str | None = None) -> list:
        with self._lock_for(chat_id):
            path = self._scope(chat_id, mask) / "history.json"
            data = schema.read_json(path, None)
            if isinstance(data, dict):
                msgs = data.get("messages")
                if isinstance(msgs, list):
                    return msgs
            return []

    def append_history(self, chat_id: str, role: str, content: str,
                       mask: str | None = None) -> None:
        with self._lock_for(chat_id):
            path = self._scope(chat_id, mask) / "history.json"
            history = self.load_history(chat_id, mask)
            history.append({"role": role, "content": content,
                            "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
            if len(history) > self._b.max_history_msgs:
                history = history[-self._b.max_history_msgs:]
            while (sum(len(m["content"]) for m in history)
                   > self._b.max_history_chars and len(history) > 1):
                history.pop(0)
            schema.write_json(path, schema.wrap({"messages": history}))

    def append_group_turn(self, chat_id: str, user_text: str, assistant_text: str,
                          mask: str | None = None) -> None:
        with self._lock_for(chat_id):
            path = self._scope(chat_id, mask) / "history.json"
            history = self.load_history(chat_id, mask)
            history.append({"role": "user", "content": user_text,
                            "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
            history.append({"role": "assistant", "content": assistant_text,
                            "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
            limit = self._b.group_history_limit * 2
            if len(history) > limit:
                history = history[-limit:]
            while (sum(len(m["content"]) for m in history)
                   > self._b.group_history_chars and len(history) > 2):
                history.pop(0)
                history.pop(0)
            schema.write_json(path, schema.wrap({"messages": history}))

    def clear_history(self, chat_id: str, mask: str | None = None) -> None:
        with self._lock_for(chat_id):
            path = self._scope(chat_id, mask) / "history.json"
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass

    # ===================== 群聊上下文（group_context.jsonl） =====================

    def append_context(self, chat_id: str, sender: str, content: str) -> None:
        with self._lock_for(chat_id):
            path = self._chat_dir(chat_id) / "group_context.jsonl"
            ctx = schema.read_jsonl(path)
            last_seq = max([m.get("seq", 0) for m in ctx], default=0)
            schema.append_jsonl(path, {
                "seq": last_seq + 1, "sender": sender, "content": content,
                "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
            if len(ctx) + 1 > self._b.group_context_limit:
                items = schema.read_jsonl(path)
                schema.rewrite_jsonl(path, items[-self._b.group_context_limit:])

    def load_context(self, chat_id: str) -> list:
        with self._lock_for(chat_id):
            return schema.read_jsonl(self._chat_dir(chat_id) / "group_context.jsonl")

    def _summary_progress(self, chat_id: str) -> int:
        path = self._chat_dir(chat_id) / "summary_progress.json"
        data = schema.read_json(path, {})
        return int(data.get("last_seq", 0) or 0)

    def pending_summary_count(self, chat_id: str) -> int:
        with self._lock_for(chat_id):
            last = self._summary_progress(chat_id)
            ctx = self.load_context(chat_id)
            return len([m for m in ctx if m.get("seq", 0) > last])

    def mark_summary_done(self, chat_id: str, seq: int) -> None:
        with self._lock_for(chat_id):
            cur = self._summary_progress(chat_id)
            path = self._chat_dir(chat_id) / "summary_progress.json"
            schema.write_json(path, {"last_seq": max(cur, seq)})

    # ===================== 日程（schedules.json） =====================

    def _schedule_dirs(self, chat_id: str) -> list:
        """该聊天可能放 schedules.json 的所有目录（顶层 + 各面具）。"""
        root = self._chat_dir(chat_id)
        dirs = [root]
        masks_dir = root / "masks"
        if masks_dir.is_dir():
            for name in sorted(masks_dir.iterdir()):
                if name.is_dir():
                    dirs.append(name)
        return dirs

    def load_schedules(self, chat_id: str, mask: str | None = None) -> list:
        with self._lock_for(chat_id):
            path = self._scope(chat_id, mask) / "schedules.json"
            data = schema.read_json(path, {})
            items = data.get("items") if isinstance(data, dict) else None
            return items if isinstance(items, list) else []

    def add_schedule(self, chat_id: str, when: datetime, event: str,
                     mask: str | None = None) -> bool:
        with self._lock_for(chat_id):
            path = self._scope(chat_id, mask) / "schedules.json"
            s = schedule_domain.Schedule(
                id=schedule_domain.make_id(when, event), when=when, event=event)
            items = self.load_schedules(chat_id, mask)
            items.append(s.to_dict())
            schema.write_json(path, schema.wrap({"items": items}))
            # 顺手写进记忆，方便 AI 查询
            self.append_memory(chat_id, "日程 " + when.strftime("%m-%d %H:%M") + " " + event, mask)
            return True

    def due_schedules(self, chat_id: str, now: datetime, window: timedelta,
                      mask: str | None = None) -> list:
        """扫描该聊天的所有作用域（顶层+面具），返回 [(when, event)] 到期待发，
        并把已扫描的日程标记为已提醒（防重复）。"""
        with self._lock_for(chat_id):
            due = []
            for d in self._schedule_dirs(chat_id):
                path = d / "schedules.json"
                if not path.is_file():
                    continue
                data = schema.read_json(path, {})
                items = data.get("items") if isinstance(data, dict) else None
                if not isinstance(items, list):
                    continue
                changed = False
                for it in items:
                    if it.get("reminded"):
                        continue
                    try:
                        s = schedule_domain.Schedule.from_dict(it)
                    except Exception:
                        continue
                    if schedule_domain.is_due(s, now):
                        it["reminded"] = True
                        changed = True
                        if schedule_domain.within_catchup(s, now, window):
                            due.append((s.when, s.event))
                if changed:
                    schema.write_json(path, schema.wrap({"items": items}))
            due.sort(key=lambda x: x[0])
            return due

    def next_due(self, chat_id: str, now: datetime) -> datetime | None:
        """最近的未提醒到期时间；没有返回 None（扫描顶层+面具）。"""
        with self._lock_for(chat_id):
            best = None
            for d in self._schedule_dirs(chat_id):
                path = d / "schedules.json"
                if not path.is_file():
                    continue
                data = schema.read_json(path, {})
                items = data.get("items") if isinstance(data, dict) else None
                if not isinstance(items, list):
                    continue
                for it in items:
                    if it.get("reminded"):
                        continue
                    try:
                        s = schedule_domain.Schedule.from_dict(it)
                    except Exception:
                        continue
                    if s.when > now and (best is None or s.when < best):
                        best = s.when
            return best

    def cleanup_schedules(self, chat_id: str, keep_hours: int,
                          mask: str | None = None) -> None:
        with self._lock_for(chat_id):
            keep = timedelta(hours=keep_hours)
            for d in self._schedule_dirs(chat_id):
                path = d / "schedules.json"
                if not path.is_file():
                    continue
                data = schema.read_json(path, {})
                items = data.get("items") if isinstance(data, dict) else None
                if not isinstance(items, list):
                    continue
                now = datetime.now()
                kept = []
                for it in items:
                    try:
                        s = schedule_domain.Schedule.from_dict(it)
                    except Exception:
                        continue
                    if s.reminded and (now - s.when) > keep:
                        continue          # 已提醒且过期：删除
                    if (not s.reminded) and (now - s.when) > keep:
                        continue          # 从未提醒且过期：丢弃（避免永远挂起）
                    kept.append(it)
                if len(kept) != len(items):
                    schema.write_json(path, schema.wrap({"items": kept}))

    def delete_schedule(self, chat_id: str, schedule_id: str,
                        mask: str | None = None) -> bool:
        """按 id 删除一条日程；返回是否找到并删除。"""
        with self._lock_for(chat_id):
            path = self._scope(chat_id, mask) / "schedules.json"
            data = schema.read_json(path, {})
            items = data.get("items") if isinstance(data, dict) else None
            if not isinstance(items, list):
                return False
            new_items = [it for it in items if it.get("id") != schedule_id]
            if len(new_items) == len(items):
                return False
            schema.write_json(path, schema.wrap({"items": new_items}))
            return True

    def list_all_schedules(self) -> list[dict]:
        """聚合所有会话的日程（含面具），返回 [{chat_id, nick_hint, mask, item}, ...]。
        nick_hint 仅从目录名推断，调用方可用 DBAdapter.nickname 补全。"""
        result = []
        chats_dir = self._data / "chats"
        if not chats_dir.is_dir():
            return result
        for chat_dir in sorted(chats_dir.iterdir()):
            if not chat_dir.is_dir():
                continue
            chat_id = chat_dir.name
            # 顶层 schedules
            for it in self._read_schedules_file(chat_dir / "schedules.json"):
                result.append({"chat_id": chat_id, "mask": "", "item": it})
            # 各面具 schedules
            masks_dir = chat_dir / "masks"
            if masks_dir.is_dir():
                for mask_dir in sorted(masks_dir.iterdir()):
                    if not mask_dir.is_dir():
                        continue
                    for it in self._read_schedules_file(mask_dir / "schedules.json"):
                        result.append({"chat_id": chat_id,
                                       "mask": mask_dir.name, "item": it})
        return result

    @staticmethod
    def _read_schedules_file(path) -> list:
        if not path.is_file():
            return []
        data = schema.read_json(path, {})
        items = data.get("items") if isinstance(data, dict) else None
        return items if isinstance(items, list) else []

    # ===================== 通道偏好（mode.json） =====================

    def get_mode(self, chat_id: str) -> str | None:
        with self._lock_for(chat_id):
            path = self._chat_dir(chat_id) / "mode.json"
            data = schema.read_json(path, {})
            m = data.get("mode")
            return m if m in ("api", "harness") else None

    def set_mode(self, chat_id: str, mode: str) -> None:
        if mode not in ("api", "harness"):
            return
        with self._lock_for(chat_id):
            path = self._chat_dir(chat_id) / "mode.json"
            schema.write_json(path, schema.wrap({"mode": mode}))

    # ===================== 面具（active_mask.json） =====================

    def get_active_mask(self, chat_id: str) -> str:
        with self._lock_for(chat_id):
            path = self._chat_dir(chat_id) / "active_mask.json"
            data = schema.read_json(path, {})
            m = data.get("mask")
            return m if m in self._p.masks else self._p.default_mask

    def set_active_mask(self, chat_id: str, name: str) -> bool:
        if name not in self._p.masks:
            return False
        with self._lock_for(chat_id):
            path = self._chat_dir(chat_id) / "active_mask.json"
            schema.write_json(path, schema.wrap({"mask": name}))
            # 首次使用该面具：把配置人设写入 persona.md，方便后续手改
            d = self._chat_dir(chat_id) / "masks" / name
            d.mkdir(parents=True, exist_ok=True)
            persona_path = d / "persona.md"
            if not persona_path.is_file():
                try:
                    persona_path.write_text(
                        self._p.masks[name].persona.strip() + "\n", encoding="utf-8")
                except Exception:
                    pass
            return True

    # ===================== 水位（watermark.json） =====================

    def load_watermark(self) -> dict:
        path = self._data / "watermark.json"
        data = schema.read_json(path, {})
        users = data.get("users") if isinstance(data, dict) else None
        return users if isinstance(users, dict) else {}

    def save_watermark(self, wm: dict) -> None:
        schema.write_json(self._data / "watermark.json",
                          schema.wrap({"users": dict(wm or {})}))
