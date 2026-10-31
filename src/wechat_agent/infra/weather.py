# -*- coding: utf-8 -*-
"""天气查询：调用 tools/weather.py（中国天气网数据源）查询城市今天天气。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from wechat_agent.logging_setup import log

_SCRIPT = Path(__file__).resolve().parent.parent.parent.parent / "tools" / "weather.py"


def query_weather(city: str, timeout: int = 40) -> str | None:
    """查询城市今天的天气，返回一句话摘要；失败返回 None。"""
    if not _SCRIPT.is_file():
        log(f"  ⚠️ [天气] 工具脚本不存在：{_SCRIPT}", "warning")
        return None
    try:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "-json", "-today", city],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout)
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if data.get("forecast"):
                f = data["forecast"][0]
                wind = f.get("wind") or ""
                level = f.get("wind_level") or ""
                return (f"{data.get('city')}今天{f.get('weather')}，"
                        f"{f.get('temp_low')}~{f.get('temp_high')}，{wind}{level}")
    except Exception as e:
        log(f"  ⚠️ [天气] {city} 查询失败：{e}", "warning")
    return None
