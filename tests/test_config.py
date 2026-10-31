# -*- coding: utf-8 -*-
"""配置加载与群聊补丁渲染测试（用真实 config/ 目录）。"""
from pathlib import Path

from wechat_agent import config as cfg


def test_load_settings():
    st = cfg.load_settings()
    assert st.wechat.master_chat == "浅华"
    assert "相亲相爱一寝室" in st.wechat.group_chats
    assert st.wechat.self_nick == "qianhua"
    assert st.llm.group_default == "api"
    assert st.llm.max_task_chars > 0
    assert st.behavior.catchup_window_minutes == 60
    assert st.skills.basic == ["wechat-natural-chat"]
    assert "weather-zh" in st.skills.on_demand
    assert "女友" in st.personas.masks
    assert st.personas.default_mask == "ai助手"
    assert len(st.rules) >= 3
    assert st.behavior.group_help_text


def test_env_interpolation():
    import os
    os.environ["DSH_NODE"] = "C:\\fake\\node.exe"
    st = cfg.load_settings()
    assert st.llm.dsh_node == "C:\\fake\\node.exe"
    del os.environ["DSH_NODE"]


def test_render_group_patch():
    st = cfg.load_settings()
    path = cfg.render_group_patch(st)
    assert path.is_file()
    content = path.read_text(encoding="utf-8")
    assert "tool-pwsh" in content
    assert "disabled: true" in content
    assert "skill-filesystem" in content
    assert str(st.base_dir) in content   # {base} 已替换


def test_base_dir_points_to_project():
    st = cfg.load_settings()
    assert (st.base_dir / "config" / "settings.yaml").is_file()
