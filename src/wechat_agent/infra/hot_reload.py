# -*- coding: utf-8 -*-
"""热重载：重新加载配置并重建可变组件。

热生效字段（改完立即生效）：
  - behavior.* （ack_message、上下文条数、主动发言参数等）
  - personas.* （人设、面具、群聊指南）
  - rules.*
  - skills.basic / skills.on_demand / skills.max_skill_chars
  - llm.api_model （API 直连换模型）
  - webui.max_log_lines

冷字段（需重启）：
  - llm.dsh_node / dsh_bin / work_dir / api_url / api_key_file / api_key_field
  - llm.harness_concurrency / dsh_timeout / api_timeout
  - wechat.master_chat / group_chats / self_nick / self_nick_aliases
  - webui.host / port / enabled
  - group_policy.* （补丁文件路径变了要重建）

返回 (reloaded_settings, cold_fields_changed: list[str])。
"""
from __future__ import annotations

from wechat_agent import config as cfg

_COLD_PATHS = {
    "llm.dsh_node", "llm.dsh_bin", "llm.work_dir",
    "llm.api_url", "llm.api_key_file", "llm.api_key_field",
    "llm.harness_concurrency", "llm.dsh_timeout", "llm.api_timeout",
    "wechat.master_chat", "wechat.group_chats",
    "wechat.self_nick", "wechat.self_nick_aliases",
    "webui.host", "webui.port", "webui.enabled",
    "group_policy.disabled_tools", "group_policy.skill_dirs",
    "group_policy.include_default_roots",
}


def detect_cold_changes(old: cfg.Settings, new: cfg.Settings) -> list[str]:
    """比较新旧 Settings，返回发生变化的冷字段路径列表。"""
    changed = []
    for path in _COLD_PATHS:
        parts = path.split(".")
        old_v = old
        new_v = new
        try:
            for p in parts:
                old_v = getattr(old_v, p) if hasattr(old_v, p) else old_v[p]
                new_v = getattr(new_v, p) if hasattr(new_v, p) else new_v[p]
        except Exception:
            continue
        if old_v != new_v:
            changed.append(path)
    return changed


def reload_with_diff(old: cfg.Settings, config_dir=None) -> tuple[cfg.Settings, list[str]]:
    """重新加载并与旧配置比较，返回 (新 Settings, 冷字段变更列表)。"""
    new = cfg.load_settings(config_dir)
    cold = detect_cold_changes(old, new)
    return new, cold
