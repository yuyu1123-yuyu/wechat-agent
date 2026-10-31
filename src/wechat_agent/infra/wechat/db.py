# -*- coding: utf-8 -*-
"""微信只读消息源适配（封装 wechatauto.WeChatDB）。

职责：会话列表、昵称、历史消息、群消息解析、发送者昵称解析、自我消息识别。
"""
from __future__ import annotations

import os
import re

from wechatauto import WeChatDB

from wechat_agent.domain.message import ParsedMsg, is_mention_me  # noqa: F401 (re-export)

_GROUP_MSG_RE = re.compile(r"^(wxid_[0-9a-zA-Z_-]+|\d+|.*@chatroom):\s*(.*)$", re.S)
_NICK_OK_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z0-9]")


class DBAdapter:
    """只读消息源。ChatSource 协议的实现。"""

    def __init__(self, wdb: WeChatDB):
        self._db = wdb
        info = wdb.get_self_info()
        self._self_username = info.get("username", "")
        self._self_nick = info.get("nick_name", "")

    @property
    def raw(self) -> WeChatDB:
        return self._db

    @property
    def self_username(self) -> str:
        return self._self_username

    @property
    def self_nick(self) -> str:
        return self._self_nick

    def sessions(self, limit: int = 300) -> list:
        return self._db.get_sessions(limit=limit)

    def nickname(self, username: str) -> str:
        return self._db.get_nickname(username) or username

    def messages(self, user: str, limit: int = 20, offset: int = 0) -> list:
        return self._db.get_messages(user, limit=limit, offset=offset)

    def parse_group_msg(self, content: str) -> ParsedMsg:
        """解析群消息：微信 4.x content 形如 "wxid: 内容" / "数字id: 内容"。"""
        text = str(content or "")
        m = _GROUP_MSG_RE.match(text)
        if m:
            return ParsedMsg(body=m.group(2).strip(), sender_id=m.group(1))
        return ParsedMsg(body=text.strip(), sender_id=None)

    def resolve_sender_nick(self, sender) -> str:
        """把发送者标识解析成可读昵称；查不到用"群友N"兜底。"""
        if sender is None:
            return "?"
        if isinstance(sender, int) or (isinstance(sender, str) and sender.isdigit()):
            sid = int(sender)
            if sid == 2:
                return "我"  # sender_id=2 通常是自己（小号）
            try:
                u = self._db._sender_id_index().get(sid)
                if u:
                    nick = self._db.get_nickname(u)
                    if nick and nick.strip() and _NICK_OK_RE.search(nick):
                        return nick
            except Exception:
                pass
            return f"群友{sid}"
        nick = self._db.get_nickname(sender)
        return nick or str(sender)

    def is_self_sender(self, msg: dict, parsed_sender) -> bool:
        """判断消息是否自己（小号）发的。"""
        if msg.get("sender_id") == 2:
            return True
        return parsed_sender == self._self_username

    def sender_id_index(self) -> dict:
        try:
            return self._db._sender_id_index()
        except Exception:
            return {}

    # ===================== 加密/压缩文本恢复 =====================
    # 微信 4.x 会把较长的文本消息以加密/压缩容器（0x28b52ffd 头）存入库，
    # wechatauto 无法还原 → content 变成占位符 "[文本]"。
    # 两条恢复路径：
    #   1) 微信全文搜索索引 message_fts（含完整明文，最可靠）
    #   2) session 表的 summary 列（最后一条消息的预览，可能截断）

    def _fts_text(self, local_id, sort_seq) -> str:
        """从微信全文搜索索引恢复消息明文（长文本被加密时的完整来源）。"""
        for rel, path, _ in self._db._db_files:
            if os.path.basename(path) != "message_fts.db":
                continue
            try:
                conn = self._db._open(rel)
            except Exception:
                continue
            try:
                tables = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                for tbl in tables:
                    if not tbl.startswith("message_fts_v4_"):
                        continue
                    try:
                        row = conn.execute(
                            f"SELECT acontent FROM {tbl} "
                            "WHERE message_local_id=? AND sort_seq=? LIMIT 1",
                            (local_id, sort_seq)).fetchone()
                        if row and row[0]:
                            return str(row[0])
                    except Exception:
                        continue
            finally:
                conn.close()
        return ""

    def session_summary(self, username: str) -> str:
        """从 session.db 读该会话最后一条消息的摘要（可能含加密消息的明文预览）。"""
        for rel, path, _ in self._db._db_files:
            if os.path.basename(path) != "session.db":
                continue
            try:
                conn = self._db._open(rel)
                try:
                    row = conn.execute(
                        "SELECT summary FROM SessionTable WHERE username=? LIMIT 1",
                        (username,)).fetchone()
                    if row and row[0]:
                        return str(row[0])
                finally:
                    conn.close()
            except Exception:
                pass
            return ""
        return ""

    def recover_placeholder_text(self, chat_id: str, msg: dict) -> str:
        """把 [文本] 占位消息的明文救回来；失败返回空串。

        优先：全文搜索索引（完整）；兜底：会话摘要（可能截断，且仅对
        最新一条可靠，故先校验 sort_seq 匹配）。
        """
        mtype = msg.get("type", "")
        content = str(msg.get("content") or "")
        if mtype != "文本" or content != f"[{mtype}]":
            return ""
        text = self._fts_text(msg.get("local_id"), msg.get("sort_seq"))
        if text:
            return text
        latest = self.messages(chat_id, limit=1)
        if latest and latest[0].get("sort_seq") == msg.get("sort_seq"):
            return self.session_summary(chat_id)
        return ""

    # ===================== 群聊上下文 =====================

    def context_before(self, group_username: str, mention_seq, n: int) -> list:
        """抓取 @ 消息之前的最近 n 条有效消息作为上下文（时间正序）。"""
        msgs = self._db.get_messages(group_username, limit=50)  # 最新在前
        items = []
        for m in msgs:
            if m["sort_seq"] >= mention_seq:
                continue  # 只要比 @ 消息更早的
            p = self.parse_group_msg(m.get("content") or "")
            if not p.body or (p.body.startswith("[") and p.body.endswith("]")):
                continue
            nick = self.resolve_sender_nick(p.sender_id)
            items.append((nick, p.body))
            if len(items) >= n:
                break
        items.reverse()  # 最早的在前
        return items

    def mentions_after(self, group_username: str, msg: dict,
                       window_sec: int = 90, self_nicks: tuple = ()) -> list:
        """收集当前 @ 消息【之后】短时间内到达的其他 @ 消息（多人同时@ 合并）。"""
        mentions = []
        try:
            base_time = msg.get("create_time") or 0
            for m in self._db.get_messages(group_username, limit=20):
                if m["sort_seq"] <= msg["sort_seq"]:
                    break
                t = m.get("create_time") or 0
                if base_time and t and (t - base_time) > window_sec:
                    break
                p = self.parse_group_msg(m.get("content") or "")
                if not p.body:
                    continue
                if is_mention_me(p.body, self_nicks):
                    nick = self.resolve_sender_nick(p.sender_id)
                    mentions.append((nick or str(p.sender_id), p.body))
        except Exception:
            pass
        return mentions
