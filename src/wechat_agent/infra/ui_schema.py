# -*- coding: utf-8 -*-
"""Web 控制台「配置」Tab 的字段 schema。

前端根据这里的定义，把 settings.yaml 渲染成可视化表单（开关/下拉/输入框），
而不是让用户写 JSON。每个字段都带：
  - path     : 在 settings.yaml 里的点路径（如 llm.api_model）
  - label    : 中文名
  - type     : 控件类型 bool/number/text/textarea/select/key_field/chat_target/skills
  - options  : select 的可选值（可选）
  - hint     : 中文说明
  - addhint  : “要增加/修改，去哪加、怎么加”的说明
  - group / order : 分组与排序

新增字段的方法：在 config.py 的对应 dataclass 加一个字段，并在这里加一行定义，
前端就会自动多出一个表单项。
"""
from __future__ import annotations

# 模型可选清单（也可手填）
MODEL_OPTIONS = [
    "mimo-v2.5-pro", "mimo-v2.5",
    "deepseek-v4-flash", "deepseek-v4-pro",
    "claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5",
    "qwen3.5-plus", "kimi-k2.5", "glm-5.2", "grok-4.5", "gpt-5.6-luna",
]

CHAT_TARGET_OPTIONS = ["私聊（大号）", "群聊"]
MODE_OPTIONS = ["api", "harness"]
KEY_FIELD_OPTIONS = ["XIAOMI_API_KEY", "DEEPSEEK_API_KEY", "OPENCODE_GO_API_KEY", "OPENCODE_API_KEY"]

# settings.yaml 各字段的渲染定义
SETTINGS_SCHEMA = [
    # ---------- wechat ----------
    dict(group="wechat", path="wechat.master_chat", label="大号昵称（全部自动回复）",
         type="text",
         hint="收到该昵称的私聊 → 全部自动处理（清空则无大号）。",
         addhint="改这里的文本框即可；想再加一个大号，改 config.py 里 WeChatConfig.master_chat 为支持列表需动代码。"),
    dict(group="wechat", path="wechat.self_nick", label="小号昵称（bot 自己）",
         type="text",
         hint="bot 本体在群里的昵称 / @ 时显示的名字。",
         addhint="直接改；换小号时改这里 + 重新登录微信。"),
    dict(group="wechat", path="wechat.bot_account", label="bot 账号目录（重要）",
         type="text",
         hint="电脑上登录的小号账号目录（wxid_xxx_abcd）。多账号时务必填对，否则收不到消息。",
         addhint="在 settings.yaml 的 wechat.bot_account 填小号的账号目录（微信数据目录下 wxid_ 开头、含 4 位后缀的文件夹名）。"),
    dict(group="wechat", path="wechat.ignore_self", label="忽略 bot 自己发的内容",
         type="bool",
         hint="开：bot 发出去的消息不会当成“新收到的消息”再处理（防回声）。",
         addhint="保持默认即可；无需新增。"),
    # ---------- llm ----------
    dict(group="llm", path="llm.api_model", label="直连 API 模型",
         type="select", options=MODEL_OPTIONS,
         hint="OpenAI 兼容直连（群聊/快速模式）用的模型。",
         addhint="下拉选一个；想填别的模型，直接在 /config/settings.yaml 的 llm.api_model 手填 id（须在 opencode/你的 API 模型目录里）。"),
    dict(group="llm", path="llm.api_key_field", label="API Key 字段名",
         type="select", options=KEY_FIELD_OPTIONS,
         hint="从 ~/.dsh/.credentials.yaml 读哪个字段当 Key。换模型供应商时改。",
         addhint="在 /config/settings.yaml 的 llm.api_key_field 下拉选；对应的 key 写在 ~/.dsh/.credentials.yaml。"),
    dict(group="llm", path="llm.group_default", label="群聊默认通道",
         type="select", options=MODE_OPTIONS,
         hint="群聊@我默认走 api（快、无工具）还是 harness（慢、带安全补丁）。",
         addhint="下拉选即可。"),
    dict(group="llm", path="llm.harness_concurrency", label="harness 并发上限",
         type="number",
         hint="并行 headless 进程数；冷启动吃资源，别开大。",
         addhint="数字改大改小；若要多进程并发可调大。"),
    dict(group="llm", path="llm.dsh_timeout", label="harness 超时(秒)",
         type="number", hint="本地 harness 单次调用超时。", addhint="长任务可调大。"),
    dict(group="llm", path="llm.api_timeout", label="直连 API 超时(秒)",
         type="number", hint="直连 API 单次超时。", addhint="网络差可调大。"),
    dict(group="llm", path="llm.max_task_chars", label="提示词上限(字符)",
         type="number", hint="headless 任务的提示词硬上限。", addhint="一般别动；历史超长可调大。"),
    dict(group="llm", path="llm.api_retries", label="API 重试次数",
         type="number", hint="瞬时失败的重试次数。", addhint="按需。"),
    # ---------- skills ----------
    dict(group="skills", path="skills.basic", label="始终注入的技能",
         type="list",
         hint="每次群聊对话都注入的技能目录名（如 wechat-natural-chat）。",
         addhint="在 group-skills/ 或 ~/.dsh/skills/ 新建 <名字>/SKILL.md，再把目录名加进 /config/settings.yaml 的 skills.basic 列表。"),
    dict(group="skills", path="skills.max_skill_chars", label="单技能注入上限(字符)",
         type="number", hint="单个 SKILL.md 最多注入多少字。", addhint="改数字。"),
    # ---------- behavior ----------
    dict(group="behavior", path="behavior.mention_context_count", label="@ 前抓取上下文条数",
         type="number", hint="群里被@时，抓@之前的几条消息做上下文。", addhint="改数字。"),
    dict(group="behavior", path="behavior.ack_message", label="慢通道确认文案",
         type="text",
         hint="harness 慢通道先发的一条“收到”提醒。", addhint="改这里文案。"),
    dict(group="behavior", path="behavior.max_history_msgs", label="私聊历史条数",
         type="number", hint="私聊记忆的历史最大条数。", addhint="改数字。"),
    dict(group="behavior", path="behavior.max_history_chars", label="私聊历史字符上限",
         type="number", hint="私聊历史总字符上限。", addhint="改数字。"),
    dict(group="behavior", path="behavior.group_history_limit", label="群聊历史轮数",
         type="number", hint="群聊记忆最近几轮(@+回复)。", addhint="改数字。"),
    dict(group="behavior", path="behavior.group_context_limit", label="群上下文条数",
         type="number", hint="记录群里几条最近消息供参考。", addhint="改数字。"),
    dict(group="behavior", path="behavior.max_reply_chars", label="回复最大长度(字)",
         type="number", hint="AI 回复超长自动截断（防微信卡死）。", addhint="改数字。"),
    dict(group="behavior", path="behavior.group_summary_every", label="群记忆总结阈值",
         type="number", hint="群消息积累多少条自动总结一次写入记忆。", addhint="改数字。"),
    dict(group="behavior", path="behavior.group_active_speak", label="群聊主动发言",
         type="bool", hint="非@消息偶尔主动插话。", addhint="开关即可。"),
    dict(group="behavior", path="behavior.group_active_cooldown", label="主动发言冷却(秒)",
         type="number", hint="两次主动发言的最小间隔。", addhint="改数字。"),
    dict(group="behavior", path="behavior.catchup_window_minutes", label="离线补发窗口(分钟)",
         type="number", hint="程序离线/睡眠错过的日程，只补发最近这么多分钟内。", addhint="改数字。"),
    dict(group="behavior", path="behavior.memory_max_lines", label="长期记忆条数上限",
         type="number", hint="AI 记忆最多保多少条。", addhint="改数字。"),
    dict(group="behavior", path="behavior.schedule_keep_hours", label="已提醒日程保留(小时)",
         type="number", hint="过期的已提醒日程多久清理。", addhint="改数字。"),
    dict(group="behavior", path="behavior.sender_queue_max", label="发送队列上限",
         type="number", hint="超过时丢弃低优先级（主动发言）。", addhint="改数字。"),
    # ---------- webui ----------
    dict(group="webui", path="webui.enabled", label="Web 控制台开关",
         type="bool", hint="关掉则不再起 http 服务。", addhint="改这个开关后可热生效。"),
    dict(group="webui", path="webui.host", label="监听地址",
         type="select", options=["127.0.0.1", "0.0.0.0"],
         hint="127.0.0.1 仅本机访问；0.0.0.0 局域网可访问（有 token 保护）。",
         addhint="改这里；需重启生效。"),
    dict(group="webui", path="webui.port", label="端口",
         type="number", hint="控制台 HTTP 端口。", addhint="改端口；需重启生效。"),
]


GROUPS = [
    ("wechat", "微信目标"),
    ("llm", "LLM / 通道 / 模型"),
    ("skills", "技能"),
    ("behavior", "行为"),
    ("webui", "Web 控制台"),
]

# 人设 / 规则 / 安全 三个文件的可视化说明（前端展示“去哪加”）
PERSONAS_HINT = "新增面具：在 /config/personas.yaml 的 masks: 下加：\n  面具名: {persona: 人设, greeting: 开场白, aliases: [别名]}；保存后群里/页面能 mask 该面具。"
RULES_HINT = "规则在 /config/rules.yaml 的 rules: 列表里，每条一个字符串；也可在私聊里用 #规则 动态添加（高等级，AI 只读）。"
POLICY_HINT = "群聊安全补丁在 /config/group-policy.yaml 的 disabled_tools: 列表，列出禁用的工具 id（tool- 前缀）。"


def build_schema_payload() -> dict:
    return {
        "fields": SETTINGS_SCHEMA,
        "groups": [{"id": g, "label": l} for g, l in GROUPS],
        "hints": {
            "personas": PERSONAS_HINT,
            "rules": RULES_HINT,
            "policy": POLICY_HINT,
        },
    }
