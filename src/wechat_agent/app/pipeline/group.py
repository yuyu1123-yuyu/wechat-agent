# -*- coding: utf-8 -*-
"""群聊流水线：群里 @ 才回复；非@消息记录上下文 + 可能主动发言。

阶段：解析 → 自己消息记录（不回复）→ 记上下文 → 触发总结检查
→ list 指令 → @判定 → 上下文抓取 → 多人@合并 → AI 对话 → 发送。
"""
from __future__ import annotations

from wechat_agent.config import Settings
from wechat_agent.domain.message import is_mention_me, truncate_text
from wechat_agent.logging_setup import log, new_trace_id, set_trace_id


class GroupPipeline:
    def __init__(self, *, db, store, commands, chat_service, sender,
                 settings: Settings, active_speak, summary):
        self._db = db
        self._store = store
        self._commands = commands
        self._chat = chat_service
        self._sender = sender
        self._s = settings
        self._active = active_speak
        self._summary = summary

    def handle(self, chat_id: str, msg: dict, listener=None) -> None:
        set_trace_id(new_trace_id())
        try:
            parsed = self._db.parse_group_msg(msg.get("content") or "")
            body = truncate_text(parsed.body or "", self._s.behavior.max_input_chars)
            mtype = msg.get("type", "")
            is_self = self._db.is_self_sender(msg, parsed.sender_id)

            # 1. 自己的消息：记进上下文（标"我"，让 AI 记得自己说过什么），不回复
            if is_self:
                if body and body != f"[{mtype}]":
                    self._store.append_context(chat_id, "我", body)
                elif body == f"[{mtype}]" and mtype == "文本":
                    recovered = self._db.recover_placeholder_text(chat_id, msg)
                    if recovered:
                        log(f"  ├ [群] 自己长消息从全文索引恢复明文，记入上下文")
                        self._store.append_context(chat_id, "我", recovered)
                return
            if not body:
                return
            # 占位符（微信把长文本加密/压缩）：尝试从会话摘要恢复明文
            if body == f"[{mtype}]":
                recovered = self._db.recover_placeholder_text(chat_id, msg)
                if recovered:
                    log(f"  ├ [群] 群消息从全文索引恢复明文：{recovered[:40]}...")
                    body = recovered
                elif mtype == "文本":
                    log(f"  ⚠️ [群] 一条文本消息读不出内容（微信加密/压缩）：{chat_id}",
                        "warning")
                    return
                else:
                    return  # 图片/语音等占位：正常跳过

            # 2. 记录所有群消息作为上下文（不只 @）
            sender_nick = self._db.resolve_sender_nick(parsed.sender_id)
            self._store.append_context(chat_id, sender_nick, body)
            from wechat_agent.infra.webui import get_status
            get_status().incr("inbound")
            log(f"  [in] 群消息已记上下文：{sender_nick}：{body[:40]}")
            self._summary.maybe_enqueue(chat_id)

            group_nick = self._db.nickname(chat_id)

            # 3. 命令检测（剥掉 @ 前缀后匹配，不走 AI）
            bare_body = body
            for n in self._s.wechat.nick_names:
                if n and bare_body.startswith("@" + n):
                    bare_body = bare_body[len("@" + n):].strip()
                    break
            reply = self._commands.try_handle(chat_id, bare_body,
                                              is_group=True)
            if reply is not None:
                log(f"  [cmd] 命中群聊指令，走本地处理（不调 AI）：{reply[:40]}")
                self._sender.enqueue(group_nick, reply,
                                     chat_id=chat_id, drop_ok=True)
                get_status().incr("queued")
                return

            # 4. 只有 @我 才回复；非@消息检查是否值得主动发言
            if not self._is_mention(body):
                reply = self._active.maybe_speak(chat_id, body, sender_nick)
                if reply:
                    log(f"  [out] AI 主动发言：{reply[:50]}（非@，主动）")
                    self._sender.enqueue(group_nick, reply, chat_id=chat_id,
                                         drop_ok=True)
                    get_status().incr("queued")
                else:
                    log("  [plan] 非@消息，无需回复（AI 判断保持安静）")
                return

            log(f"  [in] 群里 @我：{sender_nick} 说：{body[:60]}")

            # 5. 抓 @ 消息之前的最近 N 条消息作为上下文
            mention_seq = msg.get("sort_seq") or 0
            context = self._db.context_before(
                chat_id, mention_seq, self._s.behavior.mention_context_count)
            log(f"  [ctx] 抓取 @ 前上下文 {len(context)} 条")

            # 6. 收集同时到达的其他 @ 消息（多人同时 @ 合并处理）
            mentions = [(sender_nick, body)]
            extra = self._db.mentions_after(
                chat_id, msg, self._s.behavior.mention_window_sec,
                tuple(self._s.wechat.nick_names))
            if extra:
                log(f"  [ctx] 合并 {len(extra)} 条同时@的消息")
                mentions.extend(extra)

            # 7. AI 对话
            log(f"  [plan] 将调用 AI 回复 @ 消息，具体通道/模型见下一条 [ai] 日志")
            reply, mode = self._chat.group_turn(chat_id, context, mentions)
            log(f"  [out] 群聊回复（{mode}）：{reply[:60]}...")

            # 8. 保存群聊对话历史（@消息 + AI回复）
            mention_text = "；".join(f"{n}：{b}" for n, b in mentions)
            self._store.append_group_turn(chat_id, mention_text, reply)

            # 9. 发到群里
            self._sender.enqueue(group_nick, reply, chat_id=chat_id)
            get_status().incr("queued")
        except Exception as e:
            log(f"  [X] 群聊处理失败：{e}", "error")
        finally:
            set_trace_id("-")

    # ===================== 判定辅助 =====================

    def _is_mention(self, body: str) -> bool:
        return is_mention_me(body, tuple(self._s.wechat.nick_names))
