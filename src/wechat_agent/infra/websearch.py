# -*- coding: utf-8 -*-
"""轻量联网搜索（供群聊 websearch 指令用）。

实现：DuckDuckGo HTML 端点抓取（零依赖，仅 urllib），best effort——
搜索失败返回 None，调用方自然降级（不阻塞回复）。
"""
from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request

_RESULT_RE = re.compile(
    r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*>(.*?)</a>'
    r'.*?'
    r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
    re.S)

_STRIP_TAGS = re.compile(r"<[^>]+>")


def _clean(fragment: str) -> str:
    return html.unescape(_STRIP_TAGS.sub("", fragment)).strip()


def web_search(query: str, max_results: int = 3, timeout: int = 15) -> str | None:
    """搜索并返回格式化结果（标题+摘要），失败返回 None。"""
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0 Safari/537.36")})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            page = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None

    found = []
    for m in _RESULT_RE.finditer(page):
        title = _clean(m.group(1))
        snippet = _clean(m.group(2))
        if title:
            found.append(f"{title}：{snippet}" if snippet else title)
        if len(found) >= max_results:
            break
    if not found:
        return None
    return "；".join(found)[:400]
