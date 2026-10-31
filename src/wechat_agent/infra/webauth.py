# -*- coding: utf-8 -*-
"""Web UI 简单鉴权：token 文件 + cookie/header 校验。

- 首次启动时若 `.webui-token` 不存在则生成随机 token 并写入；
- 控制台打印 token（用户复制粘贴到浏览器）；
- /api/auth POST {token} → 设置 HttpOnly cookie；
- 其余 /api/* 请求校验 cookie 或 Authorization: Bearer <token>；
- 静态页面（/、/index.html）不校验（页面本身不含敏感数据，JS 调 API 时才校验）。
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

_TOKEN_FILE_NAME = ".webui-token"
_COOKIE_NAME = "wb_token"


def _token_path(base_dir: Path) -> Path:
    return base_dir / _TOKEN_FILE_NAME


def ensure_token(base_dir: Path) -> str:
    """读取或生成 Web UI token。返回 token 字符串。"""
    p = _token_path(base_dir)
    if p.is_file():
        try:
            t = p.read_text(encoding="utf-8").strip()
            if t:
                return t
        except Exception:
            pass
    token = secrets.token_urlsafe(24)
    p.write_text(token + "\n", encoding="utf-8")
    return token


def read_token(base_dir: Path) -> str:
    """只读已有 token；不存在返回空串。"""
    p = _token_path(base_dir)
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def verify_request(headers: dict, cookies: str, expected: str) -> bool:
    """校验请求是否携带有效 token（cookie 或 Authorization header）。"""
    if not expected:
        return False
    # 1. cookie
    for part in cookies.split(";"):
        kv = part.strip().split("=", 1)
        if len(kv) == 2 and kv[0].strip() == _COOKIE_NAME and kv[1].strip() == expected:
            return True
    # 2. Authorization: Bearer <token>
    auth = headers.get("Authorization", "") or headers.get("authorization", "")
    if auth.startswith("Bearer ") and auth[7:].strip() == expected:
        return True
    return False


def set_cookie_header(token: str) -> str:
    """生成 Set-Cookie 头值（HttpOnly, SameSite=Lax, Path=/）。"""
    return f"{_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax"
