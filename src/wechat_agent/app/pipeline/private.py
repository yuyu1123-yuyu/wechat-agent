# -*- coding: utf-8 -*-
"""私聊流水线：大号消息全部自动处理。

阶段：规范化 → 过滤 → 指令识别（不走 AI）→ 模式路由 → 确认 → AI 对话
→ 指令执行 → 历史保存 → 发送。
"""
from __future__ import annotations

from wechat_agent.config import Settings
from wechat_agent.domain.message import truncate_text
from wechat_agent.logging_setup import log, new_trace_id, set_trace_id


class PrivatePipeline:
    def __init__(self, *, db, store, commands, chat_service, sender,
                 settings: Settings):
        self._db = db
        self._store = store
        self._commands = commands
        self._chat = chat_service
        self._sender = sender
        self._s = settings

    def handle(self, chat_id: str, msg: dict, listener=None) -> None:
        set_trace_id(new_trace_id())
        try:
            body = (msg.get("content") or "").strip()
            mtype = msg.get("type", "")
            if self._s.wechat.ignore_self and msg.get("sender_id") == 2:
                return
            nick = self._db.nickname(chat_id)
            if mtype == '图片' and msg.get('local_id') is not None:
                from wechatauto.media import MediaDownloader
                from pathlib import Path
                try:
                    root = Path(self._s.llm.work_dir) / 'stickers'
                    root.mkdir(parents=True, exist_ok=True)
                    media = MediaDownloader(self._db.raw, save_dir=str(root))
                    image = media.download_image(chat_id, msg['local_id'], str(root))
                    if not image:
                        raise ValueError('Image unavailable')
                    from PIL import Image
                    import hashlib
                    source = Path(image)
                    target = root / (hashlib.sha256(source.read_bytes()).hexdigest()[:24]+'.png')
                    with Image.open(source) as picture:
                        picture.seek(0)
                        picture.convert('RGB').save(target)
                    body = '对方发来图片，请实际用图片读取工具查看文件，再自然回复：'+str(target.resolve())
                except Exception as error:
                    log(f'[image] Receive failed: {type(error).__name__}', 'warning')
                    self._sender.enqueue(nick, '这张图片暂时没读到，先在电脑微信点开原图，再发一次试试。', chat_id=chat_id)
                    return
            if '<emoji' in body:
                from wechat_agent.infra.wechat.stickers import receive_sticker
                from pathlib import Path
                try:
                    image = receive_sticker(body, Path(self._s.llm.work_dir) / 'stickers')
                    import hashlib
                    native_id = 's_' + hashlib.sha256(body.encode()).hexdigest()[:20]
                    body = ('对方发来一张表情图片。请实际使用图片读取工具查看本地文件：'
                            + str(image) + '。这是动画表情的首帧，不代表完整动画。'
                            '看不到必须如实说明，不能凭文件名猜。自然接话。')
                    body += (' 这张表情尚未因本次消息自动收藏。只有你实际喜欢、符合当前人设的表达风格、'
                             '且以后有使用价值时，才在最终回复附加[[collect:'+native_id+
                             ']]申请收藏；不喜欢或重复就不收藏，不要每张都收藏。'
                             '不要声称收藏已完成。默认不把刚收到的表情原样回送，先用文字自然接话。')
                except Exception as error:
                    log(f'[sticker] Receive failed: {type(error).__name__}', 'warning')
                    self._sender.enqueue(nick, '这张表情图片暂时没取到，能换成普通图片再发一次吗？', chat_id=chat_id)
                    return
            if not body:
                return
            # 占位符（微信把长文本加密/压缩，wechatauto 读不出）：
            # 先尝试从会话摘要恢复明文；恢复不了就明确告知，不静默丢弃
            if body == f"[{mtype}]":
                recovered = self._db.recover_placeholder_text(chat_id, msg)
                if recovered:
                    log(f"  📩 收到大号消息（全文索引恢复明文）：{recovered[:40]}...")
                    body = recovered
                elif mtype == "文本":
                    log(f"  ⚠️ 收到文本消息但读不出内容（微信加密/压缩），已通知对方重发", "warning")
                    self._sender.enqueue(nick, self._s.behavior.unreadable_notice,
                                         chat_id=chat_id, drop_ok=True)
                    return
                else:
                    return  # 图片/语音等占位：正常跳过
            body = truncate_text(body, self._s.behavior.max_input_chars)
            from wechat_agent.infra.webui import get_status
            get_status().incr("inbound")
            log(f"[in] 收到大号消息：{body[:80]}")

            mask = self._chat.resolve_mask(chat_id)   # 面具来源：会话语义状态 → 历史

            # 1. 指令（直接处理，不走 AI）
            reply = self._commands.try_handle(chat_id, body, is_group=False,
                                              mask=mask)
            if reply is not None:
                log(f"  [cmd] 命中指令，走本地处理（不调用 AI）：{reply[:40]}")
                self._sender.enqueue(nick, reply, chat_id=chat_id)
                get_status().incr("queued")
                return

            # 2. 模式路由 + 慢通道先发确认
            mode, body = self._chat.resolve_private_mode(chat_id, body)
            if not body:
                # 只发了模式标记（如"【直连】"）：直接确认切换，不调 AI
                confirm = ("已切换到 API 直连模式（快速回复，无工具）"
                           if mode == "api"
                           else "已切换到本地 harness 模式（全权限，可操作电脑）")
                log(f"  [模式] {confirm}")
                self._sender.enqueue(nick, confirm, chat_id=chat_id)
                get_status().incr("queued")
                return
            if mode == "harness":
                log("  [模式] 本地 harness（直接执行，只发送结果）")
            else:
                log("  [模式] API 直连（快速，不先发确认）")

            # 3. AI 对话
            md = self._chat.channel_label(mode)
            log(f"  [plan] 将调用 {md} 处理这条消息")
            reply, mode = self._chat.private_turn(chat_id, body, mask=mask,
                                                  mode=mode)
            import re
            requested = re.findall(r'\[\[collect:([A-Za-z0-9_-]+)\]\]', reply)
            reply = re.sub(r'\[\[collect:[^\]]+\]\]', '', reply).strip()
            if requested and '<emoji' in (msg.get('content') or ''):
                latest = self._db.raw.get_messages(chat_id, limit=1)
                current = (bool(latest) and latest[0].get('local_id') == msg.get('local_id')
                           and latest[0].get('content') == msg.get('content'))
                if current and requested[0] == native_id:
                    self._sender.collect_sticker(nick, native_id)
            if '<emoji' in (msg.get('content') or ''):
                reply = re.sub(r'\[\[(?:sticker|image):[^\]]+\]\]', '', reply).strip()
            if not reply:
                return
            log(f"  [out] 私聊回复（{mode}）：{reply[:60]}...")
            self._sender.enqueue(nick, reply, chat_id=chat_id)
            get_status().incr("queued")
        except Exception as e:
            log(f"  [X] 私聊处理失败：{e}", "error")
            self._sender.enqueue(self._db.nickname(chat_id),
                                 '刚才这次处理出错了，没能完成回复。请稍后再试一次。',
                                 chat_id=chat_id)
        finally:
            set_trace_id("-")
