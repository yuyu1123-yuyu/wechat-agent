# -*- coding: utf-8 -*-
"""提示词构建与预算管理测试。"""
from wechat_agent.app import prompts


def test_build_private_task_includes_sections():
    text = prompts.build_private_task(
        persona="你是助手",
        rules=["1. 规则A", "2. 规则B"],
        memory="- 记忆1\n- 记忆2",
        history=[{"role": "user", "content": "你好"},
                 {"role": "assistant", "content": "在的"}],
        mode="harness",
        web_guide="【联网搜索】",
        max_chars=20000,
    )
    assert "你是助手" in text
    assert "规则A" in text
    assert "记忆1" in text
    assert "用户：你好" in text
    assert "助手：在的" in text
    assert "<actions>" in text           # 指令协议
    assert "【联网搜索】" in text        # harness 模式注入搜索指导
    assert "操作电脑上的文件" in text    # harness 权限说明


def test_build_private_task_api_mode_no_web_guide():
    text = prompts.build_private_task(
        persona="p", rules=[], memory="", history=[], mode="api",
        web_guide="【联网搜索】", max_chars=20000)
    assert "【联网搜索】" not in text
    assert "操作电脑上的文件" not in text


def test_history_trimmed_by_budget():
    history = [{"role": "user", "content": f"消息{i}：{'长' * 200}"}
               for i in range(20)]
    text = prompts.build_private_task(
        persona="p", rules=[], memory="", history=history, mode="api",
        max_chars=3000)
    # 预算 3000：只保留最近几条
    assert "消息0" not in text
    assert "消息19" in text


def test_enforce_budget_cuts_middle():
    text = prompts.enforce_budget("a" * 5000, 1000)
    assert len(text) <= 1000
    assert "内容过长已截断" in text


def test_build_group_tone():
    assert "幽默" in prompts.build_group_tone({"humor": 0.9, "formality": 0.2})
    assert "正式" in prompts.build_group_tone({"humor": 0.2, "formality": 0.9})


def test_build_group_task_mentions():
    text = prompts.build_group_task(
        persona="群友", rules=[], memory="", group_guide="【行为指南】",
        context=[("张三", "今天天气咋样")],
        mentions=[("张三", "@qianhua 今天天气咋样")],
        history=[], skills_text="", mode="api", max_chars=20000)
    assert "张三：今天天气咋样" in text
    assert "有人@了我" in text
    assert "【行为指南】" in text
    assert "严禁使用任何表情符号" in text
