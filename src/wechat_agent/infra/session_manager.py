# -*- coding: utf-8 -*-
"""会话管理器：运行时维护每个会话的监听/主动发言/模式/面具状态。

- 启动时从 config + store 加载初始状态；
- 提供 provider 返回【全部微信会话】，snapshot() = 全量会话 + 运行时状态合并；
- Web UI 修改监听开关 → 调用 controller 真实动态 add/remove listener；
- 状态持久化到 data/session_state.json（重启恢复）。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from wechat_agent.infra.store.schema import read_json, write_json


class SessionState:
    """单个会话的运行时状态。"""
    __slots__ = ("chat_id", "nick", "kind", "listen", "active_speak",
                 "active_cooldown", "mode", "mask", "share")

    def __init__(self, chat_id: str, nick: str, kind: str,
                 listen: bool = False, active_speak: bool = True,
                 active_cooldown: int = 900, mode: str = "", mask: str = "",
                 share: bool = False):
        self.chat_id = chat_id
        self.nick = nick
        self.kind = kind            # "私聊" | "群聊"
        self.listen = listen
        self.active_speak = active_speak
        self.active_cooldown = active_cooldown
        self.mode = mode            # "" = 用全局默认
        self.mask = mask            # "" = 用默认面具
        self.share = share          # True=记忆进全局共享；False=按当前面具独立

    def to_dict(self) -> dict:
        return {
            "chat_id": self.chat_id, "nick": self.nick, "kind": self.kind,
            "listen": self.listen, "active_speak": self.active_speak,
            "active_cooldown": self.active_cooldown,
            "mode": self.mode, "mask": self.mask, "share": self.share,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SessionState":
        # 兼容旧值：share_memory 曾保存面具名，非空视为共享
        old_share = str(d.get("share_memory", ""))
        share = bool(d.get("share", False)) or bool(old_share)
        return cls(
            chat_id=d["chat_id"], nick=d.get("nick", ""),
            kind=d.get("kind", "群聊"),
            listen=bool(d.get("listen", False)),
            active_speak=bool(d.get("active_speak", True)),
            active_cooldown=int(d.get("active_cooldown", 900)),
            mode=str(d.get("mode", "")),
            mask=str(d.get("mask", "")),
            share=share,
        )


class SessionManager:
    """线程安全的会话状态容器 + 持久化 + 动态监听控制。"""

    def __init__(self, state_file: Path, provider=None, controller=None):
        self._state_file = state_file
        self._lock = threading.RLock()
        self._states: dict[str, SessionState] = {}
        self._provider = provider      # callable -> list[{chat_id,nick,kind}]
        self._controller = controller  # callable(chat_id, enabled) 真实 attach/detach
        self._store = None             # FileChatStore（用于记忆共享同步）
        self._load()

    def set_provider(self, provider) -> None:
        self._provider = provider

    def set_controller(self, controller) -> None:
        self._controller = controller

    def set_store(self, store) -> None:
        self._store = store

    def _load(self) -> None:
        data = read_json(self._state_file, {})
        items = data.get("sessions") if isinstance(data, dict) else []
        for it in (items or []):
            try:
                s = SessionState.from_dict(it)
                self._states[s.chat_id] = s
            except Exception:
                pass

    def _save(self) -> None:
        write_json(self._state_file, {
            "version": 1,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sessions": [s.to_dict() for s in self._states.values()],
        })

    # ---- 查询 ----

    def get(self, chat_id: str) -> SessionState | None:
        with self._lock:
            return self._states.get(chat_id)

    def snapshot(self) -> list[dict]:
        """返回【全部微信会话】+ 合并的运行时状态。

        已在 state 里的用其状态；不在的会话按默认（监听关）生成，保证全量展示。
        """
        result = []
        if self._provider:
            try:
                raw = self._provider() or []
            except Exception:
                raw = []
            with self._lock:
                for item in raw:
                    cid = item.get("chat_id") or item.get("username")
                    if not cid:
                        continue
                    nick = item.get("nick") or ""
                    kind = item.get("kind") or ("群聊" if str(cid).endswith("@chatroom") else "私聊")
                    s = self._states.get(cid)
                    if s is None:
                        # 保证 state 里有记录，便于后续 set_listen
                        s = SessionState(chat_id=cid, nick=nick, kind=kind, listen=False)
                        self._states[cid] = s
                    else:
                        if not s.nick and nick:
                            s.nick = nick
                        if not s.kind:
                            s.kind = kind
                    result.append(s.to_dict())
            self._save()
        else:
            # 无 provider：退回只列 state
            with self._lock:
                result = [s.to_dict() for s in self._states.values()]
        return result

    # ---- 初始化（启动时调用） ----

    def ensure(self, chat_id: str, nick: str, kind: str,
               default_mode: str = "", default_mask: str = "",
               default_listen: bool = False,
               default_active_speak: bool = True,
               default_cooldown: int = 900) -> SessionState:
        """确保会话存在；已存在则只补全昵称/类型，不覆盖用户改过的设置。"""
        with self._lock:
            s = self._states.get(chat_id)
            if s is None:
                s = SessionState(chat_id=chat_id, nick=nick, kind=kind,
                                 listen=default_listen,
                                 active_speak=default_active_speak,
                                 active_cooldown=default_cooldown,
                                 mode=default_mode, mask=default_mask)
                self._states[chat_id] = s
                self._save()
            else:
                if nick and not s.nick:
                    s.nick = nick
                if kind and not s.kind:
                    s.kind = kind
            return s

    # ---- 运行时修改（Web UI 调用） ----

    def set_listen(self, chat_id: str, enabled: bool) -> bool:
        with self._lock:
            s = self._states.get(chat_id)
            if s is None:
                # 会话可能尚未经 snapshot 收录：尝试用 provider 建默认态
                meta = self._find_meta(chat_id)
                if meta is None:
                    return False
                s = SessionState(chat_id=chat_id, nick=meta["nick"],
                                 kind=meta["kind"], listen=False)
                self._states[chat_id] = s
            changed = (s.listen != enabled)
            s.listen = enabled
            self._save()
        # 真实动态 attach/detach listener，放在锁外执行
        if changed and self._controller:
            try:
                self._controller(chat_id, enabled)
            except Exception:
                pass
        return True

    def _find_meta(self, chat_id: str) -> dict | None:
        if self._provider:
            try:
                for item in (self._provider() or []):
                    if (item.get("chat_id") or item.get("username")) == chat_id:
                        nick = item.get("nick") or ""
                        kind = item.get("kind") or \
                            ("群聊" if str(chat_id).endswith("@chatroom") else "私聊")
                        return {"nick": nick, "kind": kind}
            except Exception:
                pass
        return None

    def set_active_speak(self, chat_id: str, enabled: bool,
                         cooldown: int | None = None) -> bool:
        with self._lock:
            s = self._states.get(chat_id)
            if s is None:
                return False
            s.active_speak = enabled
            if cooldown is not None:
                s.active_cooldown = max(60, int(cooldown))
            self._save()
            return True

    def set_mode(self, chat_id: str, mode: str) -> bool:
        if mode not in ("api", "harness", ""):
            return False
        with self._lock:
            s = self._states.get(chat_id)
            if s is None:
                return False
            s.mode = mode
            self._save()
            return True

    def set_mask(self, chat_id: str, mask: str) -> bool:
        with self._lock:
            s = self._states.get(chat_id)
            if s is None:
                return False
            s.mask = mask
            self._save()
        self._sync_memory_scope(chat_id)
        return True

    def set_share(self, chat_id: str, share: bool) -> bool:
        """设置记忆是否全局共享：True → 该会话记忆进全局共享空间；
        False → 按该会话当前面具分开存。"""
        with self._lock:
            s = self._states.get(chat_id)
            if s is None:
                return False
            s.share = bool(share)
            self._save()
        self._sync_memory_scope(chat_id)
        return True

    def _sync_memory_scope(self, chat_id: str) -> None:
        """把记忆共享配置同步到 store 的记忆路由。
        共享 → data/memory/shared（跨面具全局）；独立 → data/memory/<mask>（按当前面具）。"""
        if self._store is None:
            return
        with self._lock:
            s = self._states.get(chat_id)
            if s is None:
                return
            mask = s.mask or self._store.default_mask
            share = s.share
        mem_root = self._store.data_dir / "memory"
        if share:
            target = mem_root / "shared"
        else:
            target = mem_root / f"mask_{mask}"
        try:
            self._store.set_memory_scope(chat_id, mask, target)
        except Exception:
            pass

    def sync_all_memory_scopes(self) -> None:
        """同步所有会话的记忆共享路由（启动时/设置 store 后调用）。"""
        with self._lock:
            cids = list(self._states.keys())
        for cid in cids:
            try:
                self._sync_memory_scope(cid)
            except Exception:
                pass

    def batch_update(self, updates: list[dict]) -> dict:
        """一次批量应用多个会话的修改（Web「保存」用）。
        updates: [{chat_id, nick?, kind?, listen?, active_speak?, cooldown?,
                   mode?, mask?, share?}, ...]
        返回 {ok: bool, applied: int}；监听变化触发 controller，mask/share 触发记忆同步。
        """
        applied = 0
        listen_changes: list[tuple] = []
        memory_changes: list[str] = []
        with self._lock:
            for u in updates:
                cid = u.get("chat_id")
                if not cid:
                    continue
                s = self._states.get(cid)
                if s is None:
                    meta = self._find_meta(cid)
                    if meta is None:
                        continue
                    s = SessionState(chat_id=cid, nick=meta["nick"],
                                     kind=meta["kind"], listen=False)
                    self._states[cid] = s
                if u.get("nick") and not s.nick:
                    s.nick = u["nick"]
                if u.get("kind") and not s.kind:
                    s.kind = u["kind"]
                if "listen" in u and bool(u["listen"]) != s.listen:
                    listen_changes.append((cid, bool(u["listen"])))
                    s.listen = bool(u["listen"])
                if "active_speak" in u:
                    s.active_speak = bool(u["active_speak"])
                if "cooldown" in u:
                    s.active_cooldown = max(60, int(u["cooldown"]))
                if "mode" in u:
                    s.mode = u["mode"] if u["mode"] in ("api", "harness") else ""
                if "mask" in u:
                    s.mask = str(u["mask"]) if u["mask"] else ""
                    memory_changes.append(cid)
                if "share" in u:
                    s.share = bool(u["share"])
                    memory_changes.append(cid)
                applied += 1
            if applied:
                self._save()
        # 锁外执行监听/记忆同步
        for cid, enabled in listen_changes:
            if self._controller:
                try:
                    self._controller(cid, enabled)
                except Exception:
                    pass
        for cid in memory_changes:
            try:
                self._sync_memory_scope(cid)
            except Exception:
                pass
        return {"ok": True, "applied": applied}
