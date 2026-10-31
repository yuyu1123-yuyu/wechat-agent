# -*- coding: utf-8 -*-
"""技能加载与按需注入（API 直连模式由程序代替 AI 加载技能）。

- 基础技能：始终注入（默认 wechat-natural-chat，聊天自然指南）
- 按需技能：命中触发关键词才注入（wechat-schedule / weather-zh ...）
- 注入内容有长度上限（防提示词膨胀）
覆盖目录：群聊专属（group-skills/）+ 通用（~/.dsh/skills/）
"""
from __future__ import annotations

from pathlib import Path


class SkillLoader:
    def __init__(self, roots: list, basic: list, on_demand: dict,
                 max_skill_chars: int = 800):
        self._roots = [Path(r) for r in roots]
        self._basic = list(basic)
        self._on_demand = {name: [k.lower() for k in kws if k]
                           for name, kws in on_demand.items()}
        self._max_skill_chars = max_skill_chars

    def _find(self, name: str) -> str:
        """按目录名在技能根里找 SKILL.md 内容，找不到返回空串。"""
        for root in self._roots:
            p = root / name / "SKILL.md"
            if p.is_file():
                try:
                    content = p.read_text(encoding="utf-8")
                    if len(content) > self._max_skill_chars:
                        return content[:self._max_skill_chars] + "\n…（技能过长已截断）"
                    return content
                except Exception:
                    return ""
        return ""

    def basic_text(self) -> str:
        """始终注入的基础技能。"""
        parts = [c for n in self._basic if (c := self._find(n))]
        return "\n\n".join(parts)

    def match(self, text: str) -> list:
        """按关键词命中规则，返回应注入的按需技能目录名列表。"""
        low = (text or "").lower()
        return [name for name, kws in self._on_demand.items()
                if any(k in low for k in kws)]

    def on_demand_text(self, text: str) -> str:
        """命中关键词的技能才注入。"""
        parts = [c for n in self.match(text) if (c := self._find(n))]
        return "\n\n".join(parts)

    def index_lines(self) -> str:
        """全量技能索引（每个技能一行：名称 + 用途），常驻提示词让 AI 知道有什么。"""
        lines = []
        for root in self._roots:
            if not root.is_dir():
                continue
            for d in sorted(root.iterdir()):
                if d.is_dir() and (d / "SKILL.md").is_file():
                    lines.append(f"- {d.name}")
        return "\n".join(lines)
