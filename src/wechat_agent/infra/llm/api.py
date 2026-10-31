# -*- coding: utf-8 -*-
"""OpenAI 兼容 API 直连网关（无工具，快）。

- API Key 从 ~/.dsh/.credentials.yaml 读取（字段名可配置，
  如 DEEPSEEK_API_KEY / OPENCODE_GO_API_KEY，与 Harness 用的同一个文件）；
- 对瞬时错误（HTTP 429 / 5xx / 网络超时）做指数退避重试；
- 只发文本，天然没有工具（安全）；
- 目标为 opencode.ai 时自动附带 opencode 专属请求头（普通 OpenAI 客户端
  缺这些头会被 403，参考 opencode2api 的实现：x-opencode-client/session/request/project）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request

from wechat_agent.logging_setup import log
from wechat_agent.ports.llm import LLMResult


def load_api_key(key_file: str | None = None, field: str = "DEEPSEEK_API_KEY") -> str:
    """从 credentials.yaml 读取指定字段的 API Key，失败读环境变量同名项。"""
    key_file = key_file or os.path.join(os.path.expanduser("~"), ".dsh", ".credentials.yaml")
    pattern = r'^\s*' + re.escape(field) + r'\s*:\s*["\']?([^"\'\s]+)'
    try:
        with open(os.path.expanduser(key_file), "r", encoding="utf-8") as f:
            for line in f:
                m = re.match(pattern, line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return os.environ.get(field, "")


def _opencode_ids(seed: str) -> dict:
    """生成 opencode 客户端协商标识（session 用 seed 稳定、request 随机）。"""
    h = hashlib.sha256(("ses\x00" + seed).encode("utf-8")).hexdigest()[:24]
    return {
        "x-opencode-client": "cli",
        "x-opencode-project": "prj_" + h,
        "x-opencode-session": "ses_" + h,
        "x-opencode-request": "req_" + secrets.token_hex(16),
    }


def build_headers(url: str, key: str) -> dict:
    """构造请求头；目标主机是 opencode.ai 时附加 opencode 专属头。"""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {key}",
    }
    if "opencode.ai" in url:
        headers["User-Agent"] = "opencode/1.18.18 (windows amd64; go1.24.0)"
        headers.update(_opencode_ids(seed=time.strftime("%Y%m%d%H") + url))
    return headers


class ApiGateway:
    def __init__(self, url: str, model: str, key_file: str | None = None,
                 key_field: str = "DEEPSEEK_API_KEY",
                 timeout: int = 180, retries: int = 2):
        self._url = url
        self._model = model
        self._key_file = key_file
        self._key_field = key_field
        self._timeout = timeout
        self._retries = retries

    def call(self, prompt: str, patch: str | None = None) -> LLMResult:
        # patch 对 API 直连无意义（本来就没有工具），忽略。
        key = load_api_key(self._key_file, self._key_field)
        if not key:
            raise RuntimeError(
                f"未找到 {self._key_field}（~/.dsh/.credentials.yaml）")

        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        req = urllib.request.Request(
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            headers=build_headers(self._url, key),
        )

        t0 = time.time()
        last_err = None
        for attempt in range(self._retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"].strip()
                dt = time.time() - t0
                log(f"  ├ [api] 完成（{dt:.1f}s，回复 {len(content)} 字）")
                return LLMResult(text=content, mode="api", elapsed=dt)
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")[:200]
                last_err = RuntimeError(f"API HTTP {e.code}：{body}")
                if e.code in (429,) or e.code >= 500:
                    if attempt < self._retries:
                        _sleep_backoff(attempt)
                        continue
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = RuntimeError(f"API 网络错误：{e}")
                if attempt < self._retries:
                    _sleep_backoff(attempt)
                    continue
            except Exception as e:
                last_err = RuntimeError(f"API 调用失败：{e}")
                break
        log(f"  ✗ [api] 失败：{last_err}", "error")
        raise last_err


def _sleep_backoff(attempt: int) -> None:
    time.sleep(1.5 * (2 ** attempt))
