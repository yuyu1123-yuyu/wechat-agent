# -*- coding: utf-8 -*-
"""指令执行器：解析 AI 回复里的 <actions> 段（及旧格式标记）并执行。

    remember  → 写入该聊天长期记忆
    rule      → 低优先级行为笔记（同样写入记忆，AI 不可改高等级规则）
    schedule  → 中文时间解析后存日程（到点主动提醒）
    weather   → 查真实天气，拼入回复末尾
    websearch → 轻量联网搜索，拼入回复末尾
返回 (干净回复, 追加后缀)；标记本身不会发给对方。
"""
from __future__ import annotations

from wechat_agent.config import BehaviorConfig
from wechat_agent.domain.directive import Directive, parse_reply
from wechat_agent.domain.timeparse import parse_zh_datetime
from wechat_agent.logging_setup import log


class DirectiveExecutor:
    def __init__(self, store, behavior: BehaviorConfig,
                 weather_fn=None, websearch_fn=None):
        self._store = store
        self._b = behavior
        self._weather_fn = weather_fn
        self._websearch_fn = websearch_fn

    def process(self, reply: str, chat_id: str,
                mask: str | None = None) -> tuple:
        """解析并执行指令，返回 (干净回复, 追加后缀文本)。"""
        clean, directives = parse_reply(reply)
        suffix_parts = []
        for d in directives:
            self._execute(d, chat_id, mask, suffix_parts)
        suffix = ("\n" + "\n".join(suffix_parts)) if suffix_parts else ""
        return clean, suffix

    def _execute(self, d: Directive, chat_id: str, mask: str | None,
                 suffix: list) -> None:
        from wechat_agent.infra.webui import get_status
        kind, value = d.kind, d.value
        if kind in ("remember", "rule"):
            if kind == "remember":
                get_status().incr("memory_writes")
                get_status().tool("记住记忆")
            else:
                get_status().tool("规则笔记")
        elif kind == "schedule":
            get_status().tool("记日程")
        elif kind == "weather":
            get_status().tool("查天气")
        elif kind == "websearch":
            get_status().tool("联网搜索")
        if kind == "remember":
            if self._store.append_memory(chat_id, value, mask):
                log(f"  ├ [指令->记忆] 记住：{value}")
            else:
                log(f"  ⚠️ [指令->记忆] 写入失败：{value}", "warning")
        elif kind == "rule":
            if self._store.append_memory(chat_id, value, mask):
                log(f"  ├ [指令->记忆] 行为笔记：{value}")
        elif kind == "schedule":
            dt, event = parse_zh_datetime(value)
            if dt and event:
                if self._store.add_schedule(chat_id, dt, event, mask):
                    log(f"  ├ [指令->日程] 已安排提醒：{dt:%m-%d %H:%M} {event}")
            else:
                # 解析不出时间：先记进记忆，不丢信息
                self._store.append_memory(chat_id, "日程 " + value, mask)
                log(f"  ├ [指令->日程] 未解析到时间，已记进记忆：{value}")
        elif kind == "weather":
            city = value
            text = self._weather_fn(city) if self._weather_fn else None
            if text:
                log(f"  ├ [指令->工具/天气] 已查 {city}：{text}")
                suffix.append(f"（{text}）")
            else:
                log(f"  ⚠️ [指令->工具/天气] 查询失败：{city}", "warning")
                suffix.append(
                    f"（{city}的天气没查到，给你个链接："
                    "https://www.weather.com.cn/ 自己看下）")
        elif kind == "websearch":
            text = self._websearch_fn(value) if self._websearch_fn else None
            if text:
                log(f"  ├ [指令->工具/搜索] 已联网搜索：{value}")
                suffix.append(f"（搜到了：{text}）")
            else:
                log(f"  ⚠️ [指令->工具/搜索] 失败：{value}", "warning")
        else:
            log(f"  ⚠️ [指令] 未知指令类型：{kind}", "warning")
