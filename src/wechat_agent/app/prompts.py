# -*- coding: utf-8 -*-
"""提示词构建（纯函数，零 I/O）。

每次对话对 AI 都是全新会话，靠这段文本让它"想起"之前聊过什么。
注入顺序：人设 → 规则 → 记忆 → 群聊行为指南 → 上下文 → 指令协议 → 搜索指导。
预算管理：历史/上下文是弹性部分，超出提示词硬上限时从最老的消息开始裁剪。
"""
from __future__ import annotations

from wechat_agent.logging_setup import log

# ===================== 指令协议（Actions 段说明） =====================

_ACTIONS_PROTOCOL = """【程序指令】如果你需要程序帮你执行以下动作，请在回复的【最后单独一行】输出一个 <actions> 块，
每行一个动作，格式：动作名: 内容（可以同时输出多个）：
- remember: 需要长期记住的重要信息（如 对方喜欢喝奶茶）
- rule: 你观察到的行为笔记（低优先级，如 群友喜欢简短回复）
- schedule: 具体时间+事项（如 周五下午3点 高数考试），程序会到点主动提醒
- weather: 城市名（如 安阳），程序会查真实天气
- websearch: 搜索关键词，程序会帮你联网查（需要实时/最新信息时用）

示例：
<actions>
remember: 对方喜欢喝奶茶
schedule: 周五下午3点 高数考试
weather: 安阳
</actions>

程序会替你执行，<actions> 块本身不会显示给对方。不需要就正常回复，不要输出。"""


# ===================== 预算工具 =====================

def _fit_history(history: list, budget: int) -> list:
    """按字符预算裁剪历史（丢最老，至少保留 1 条）。"""
    if budget <= 0:
        return []
    keep = list(history)
    while sum(len(m.get("content", "")) for m in keep) > budget and len(keep) > 1:
        keep.pop(0)
    return keep


def enforce_budget(text: str, max_chars: int) -> str:
    """最终兜底：超限时从中间裁剪并注明（硬上限护栏）。"""
    if len(text) <= max_chars:
        return text
    if max_chars <= 200:
        return text[:max_chars]
    head = text[:max_chars // 2]
    tail = text[-(max_chars // 2 - 100):]
    log(f"  ⚠️ [提示词] 超预算（{len(text)} 字），已裁剪到 {max_chars}", "warning")
    return head + "\n…（内容过长已截断）…\n" + tail


# ===================== 公共段 =====================

def _rules_block(rules: list) -> str:
    return "【规则】\n" + "\n".join(f"- {r}" for r in rules)


def _memory_block(memory: str) -> str:
    return f"【AI 记忆（你之前记下的，务必遵守）】\n{memory}"


def _web_guide_block(web_guide: str) -> str:
    return web_guide


# ===================== 私聊任务 =====================

def build_private_task(*, persona: str, rules: list, memory: str,
                       history: list, mode: str, web_guide: str = "",
                       max_chars: int = 20000) -> str:
    """私聊任务文本：人设/规则/记忆 + 历史 + 指令协议。

    mode == "harness" 时注入"可用工具"说明 + 联网搜索指导。
    """
    header = [persona, "", _rules_block(rules)]
    if memory:
        header.append("")
        header.append(_memory_block(memory))
    header.append("")
    header_text = "\n".join(header)

    instruction = [
        "",
        "以下是我们的对话历史（'用户'=对方，'助手'=你）：",
        "",
    ]
    instruction_text = "\n".join(instruction)

    tail = [
        "",
        "请基于以上对话历史回复对方最新的那条消息。直接给出要发送的回复内容，不要复述历史。",
    ]
    if mode == "harness":
        tail.append("如果有必要，你可以使用工具查看或操作电脑上的文件（你的工作目录是当前目录）。")
    tail.append("")
    tail.append(_ACTIONS_PROTOCOL)
    if mode == "harness" and web_guide:
        tail.append("")
        tail.append(_web_guide_block(web_guide))
    tail_text = "\n".join(tail)

    # 历史是弹性部分：预算 = 总预算 - 固定段
    budget = max_chars - len(header_text) - len(instruction_text) - len(tail_text) - 500
    history = _fit_history(history, max(2000, budget))

    lines = [header_text, instruction_text]
    for m in history:
        who = "用户" if m.get("role") == "user" else "助手"
        lines.append(f"{who}：{m.get('content', '')}")
    lines.append(tail_text)
    return enforce_budget("\n".join(lines), max_chars)


# ===================== 群聊任务 =====================

def build_group_tone(params: dict) -> str:
    """根据人设参数生成群聊语气描述。"""
    humor = params.get("humor", 0.5)
    formality = params.get("formality", 0.3)
    if humor >= 0.7:
        tone = "幽默爱开玩笑，偶尔玩梗，让人放松"
    elif humor >= 0.3:
        tone = "轻松自然，偶尔轻松一下，不做作"
    else:
        tone = "正经靠谱，不爱开玩笑，说正事"
    if formality >= 0.7:
        tone += "；用词严谨，表达正式"
    else:
        tone += "；说话随意口语化，像朋友"
    return tone


def build_group_task(*, persona: str, rules: list, memory: str,
                     group_guide: str, context: list, mentions: list,
                     history: list, skills_text: str, mode: str,
                     web_guide: str = "", max_chars: int = 20000) -> str:
    """群聊任务文本：人设/规则/记忆 + 行为指南 + 技能 + 上下文 + @消息。"""
    header = [persona, "", _rules_block(rules)]
    if memory:
        header.append("")
        header.append(_memory_block(memory))
    header.append("")
    header.append(group_guide)
    header.append("")
    header_text = "\n".join(header)

    ctx_lines = ["以下是被@之前群里最近的几条消息（发送者：内容）："]
    for nick, content in context:
        ctx_lines.append(f"{nick}：{content}")
    ctx_lines.append("")
    ctx_text = "\n".join(ctx_lines)

    if len(mentions) == 1:
        nick, body = mentions[0]
        mention_text = (f"有人@了我（{nick}），内容是：{body}\n"
                        "请主要针对这条@我的消息进行回复。")
    else:
        lines = ["下面这些消息里都有人@了我，请全部都回应："]
        for nick, body in mentions:
            lines.append(f"  @我的消息（{nick}）：{body}")
        mention_text = "\n".join(lines)

    tail = [
        "",
        "你的回复会直接发到这个微信群里，回复【开头@你回应的人】（如 @张三），"
        "语气要自然口语化，像朋友聊天一样，不要太长（一般不超过150字）。"
        "【严禁使用任何表情符号（emoji）】，一个表情都不要加，纯文字聊天。"
        "直接给出要发送的回复内容。",
        "",
        _ACTIONS_PROTOCOL,
    ]
    if mode == "harness" and web_guide:
        tail.append("")
        tail.append(_web_guide_block(web_guide))
    tail_text = "\n".join(tail)

    # 弹性部分：历史 + 技能文本 + 上下文
    fixed = len(header_text) + len(ctx_text) + len(mention_text) + len(tail_text)
    budget = max_chars - fixed - 500
    history = _fit_history(history, max(1500, budget // 3))
    skills_text = (skills_text or "")[:max(0, budget - len(history))]

    hist_lines = []
    if history:
        hist_lines.append("")
        hist_lines.append("【我参与过的群聊对话（用于延续我的说话风格）】")
        for m in history:
            who = "用户" if m.get("role") == "user" else "我"
            hist_lines.append(f"{who}：{m.get('content', '')}")
        hist_lines.append("")

    body = [
        header_text,
        ctx_text,
        mention_text,
        *hist_lines,
        *(["【技能说明（遇到相关场景请按说明执行）】\n" + skills_text] if skills_text else []),
        tail_text,
    ]
    return enforce_budget("\n".join(body), max_chars)


# ===================== 群聊行为指南（精简） =====================

def build_group_guide_lite() -> str:
    """群聊硬性行为（精简版，不再从 personas 大段注入）。"""
    return "【群聊行为】被@才回复、没人@时保持安静；只答@你的事、不串话题；" \
           "回复简短（≤150字）、禁表情符号；不暴露AI、不编造；" \
           "遇隐私/争执降温不引战；需要实时信息时联网查。"


# ===================== 主动发言 / 记忆总结 =====================

def build_active_speak_task(context: list, trigger_reason: str,
                            tone: str, max_ctx: int = 15) -> str:
    """AI 主动发言：根据最近群消息判断是否值得插一句话。"""
    lines = [
        f"你是微信群里的一名普通群友（替'主人'在群里说话）。语气：{tone}。",
        "不要用表情符号。",
        "",
        "【最近群消息（发送者：内容）】",
    ]
    for m in context[-max_ctx:]:
        lines.append(f"{m.get('sender')}：{m.get('content')}")
    lines.append("")
    lines.append(f"触发原因：{trigger_reason}（没人@你，但这种情况可以自然地插句话）")
    lines.append("")
    lines.append(
        "请判断：如果值得说一句自然的话（接话/回答问题/活跃气氛），直接输出这句话，"
        "不超过50字，【严禁使用任何表情符号（emoji）】，纯文字；"
        "如果没什么值得说的，只输出（安静）两个字。"
    )
    return "\n".join(lines)


def build_summary_task(new_msgs: list, old_mem: str) -> str:
    """群聊记忆自动总结：让 AI 输出更新后的完整记忆列表。"""
    lines = [
        "你是群聊记忆管家。群里有个 AI 助手（替群里的'主人'说话）需要长期记住群里的人和事。",
        "",
        "【现有记忆】",
        old_mem,
        "",
        "【最近的新消息（发送者：内容）】",
    ]
    for m in new_msgs:
        lines.append(f"{m.get('sender')}：{m.get('content')}")
    lines.append("")
    lines.append(
        "请总结值得长期记住的信息，输出【更新后的完整记忆列表】：\n"
        "1. 群成员的性格特点、说话风格、喜好、雷区\n"
        "2. 重要的约定、日期、事件（如聚餐、考试、任务）\n"
        "3. 群里的长期话题、背景信息\n"
        "4. 每行一条，以'- '开头；保留旧记忆中有价值的，删去过时琐碎的\n"
        "5. 总条数控制在 20 条以内，只输出记忆列表，不要其他文字"
    )
    return "\n".join(lines)
