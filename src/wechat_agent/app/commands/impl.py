# -*- coding: utf-8 -*-
"""内置命令实现：#记住 / #查看记忆 / #规则 / mask / list 等。"""
from __future__ import annotations

from wechat_agent.app.commands.registry import Command, CommandCtx, CommandRegistry
from wechat_agent.config import BehaviorConfig, PersonasConfig


def build_registry(store, personas: PersonasConfig,
                   behavior: BehaviorConfig) -> CommandRegistry:
    reg = CommandRegistry()

    def cmd(name, fn, aliases=(), scope="master"):
        reg.register(Command(name=name, handler=fn, aliases=list(aliases),
                             scope=scope))

    # ---- 记忆 ----
    def do_remember(ctx: CommandCtx) -> str:
        if not ctx.args:
            return "用法：#记住 内容（例如：#记住 我喜欢喝奶茶）"
        return ("记住了：" + ctx.args) if store.append_memory(
            ctx.chat_id, ctx.args, ctx.mask) else "记忆保存失败"

    def do_show_memory(ctx: CommandCtx) -> str:
        mem = store.load_memory(ctx.chat_id, ctx.mask)
        return mem if mem else "（还没有任何记忆）"

    def do_clear_long_memory(ctx: CommandCtx) -> str:
        return "已清空长期记忆。" if store.clear_memory(
            ctx.chat_id, ctx.mask) else "清空失败"

    def do_clear_history(ctx: CommandCtx) -> str:
        store.clear_history(ctx.chat_id, ctx.mask)
        return "已清空我们的对话记忆。从这条消息开始，我们重新认识！"

    # ---- 高等级规则 ----
    def do_add_rule(ctx: CommandCtx) -> str:
        if not ctx.args:
            return "用法：#规则 内容（例如：#规则 群里绝不提考试）"
        return ("已添加高等级规则：" + ctx.args) if store.add_rule(
            ctx.chat_id, ctx.args) else "规则保存失败"

    def do_show_rules(ctx: CommandCtx) -> str:
        rules = store.load_rules(ctx.chat_id)
        return ("当前高等级规则：\n" + "\n".join(f"- {r}" for r in rules)
                if rules else "（还没有高等级规则）")

    def do_clear_rules(ctx: CommandCtx) -> str:
        return "已清空所有高等级规则。" if store.clear_rules(
            ctx.chat_id) else "清空失败"

    # ---- 面具 ----
    def do_mask(ctx: CommandCtx) -> str:
        arg = ctx.args.lstrip(":：").strip()
        if not arg:
            cur = store.get_active_mask(ctx.chat_id)
            return (f"当前面具：{cur}\n"
                    f"可用面具：{'、'.join(personas.masks)}\n"
                    f"切换：mask <面具名>")
        matched = None
        for name, m in personas.masks.items():
            if name == arg or any(a == arg or a in arg for a in m.aliases):
                matched = name
                break
        if not matched:
            return f"没找到面具「{arg}」，可用：{'、'.join(personas.masks)}"
        if store.get_active_mask(ctx.chat_id) == matched:
            return f"已经在用面具「{matched}」了"
        if not store.set_active_mask(ctx.chat_id, matched):
            return "切换面具失败"
        greeting = personas.masks[matched].greeting
        return f"已切换到面具「{matched}」" + (f"\n{greeting}" if greeting else "")

    # ---- 能力/指令说明（私聊/群聊通用） ----
    def do_list(ctx: CommandCtx) -> str:
        return behavior.list_help_text or behavior.group_help_text or "（未配置能力说明）"

    cmd("#记住", do_remember)
    cmd("#查看记忆", do_show_memory)
    cmd("#清空长期记忆", do_clear_long_memory)
    cmd("#清空记忆", do_clear_history, aliases=("#清空", "/clear"))
    cmd("#规则", do_add_rule)
    cmd("#查看规则", do_show_rules)
    cmd("#清空规则", do_clear_rules)
    cmd("mask", do_mask)
    cmd("list", do_list, scope="any")
    return reg
