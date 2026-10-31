# -*- coding: utf-8 -*-
"""main.py —— 组合根：装配依赖并启动。

启动流程：
1. 解析参数（--check 只校验配置与通道，不启动监听）
2. 加载配置 + 渲染群聊安全补丁（缺失则拒绝启动受限模式）
3. 构建存储 / LLM 网关 / 路由 / 技能 / 工具 / 指令 / 命令 / 服务 / 流水线
4. 连接微信数据库 + 打运行时补丁 + 窗口多维度自检
5. 扫描会话、注册监听（大号私聊 / 群聊 @）
6. 启动：发送队列 / 监听 / 日程提醒 / 记忆总结 / 任务调度
7. 心跳主循环，Ctrl+C 优雅退出
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from wechat_agent import config as cfg
from wechat_agent.app.commands.impl import build_registry
from wechat_agent.app.directives import DirectiveExecutor
from wechat_agent.app.pipeline.group import GroupPipeline
from wechat_agent.app.pipeline.private import PrivatePipeline
from wechat_agent.app.service.active_speak import ActiveSpeakService
from wechat_agent.app.service.chat import ChatService
from wechat_agent.app.service.reminder import ReminderService
from wechat_agent.app.service.scheduler import JobScheduler
from wechat_agent.app.service.summary import SummaryService
from wechat_agent.infra import websearch
from wechat_agent.infra.llm.api import ApiGateway, load_api_key
from wechat_agent.infra.llm.dsh import HeadlessGateway
from wechat_agent.infra.llm.gateway import LLMRouter
from wechat_agent.infra.skills import SkillLoader
from wechat_agent.infra.store.files import FileChatStore
from wechat_agent.infra.weather import query_weather
from wechat_agent.infra.wechat import patch as wechat_patch
from wechat_agent.infra.wechat import selfcheck
from wechat_agent.infra.wechat.db import DBAdapter
from wechat_agent.infra.wechat.gui import SenderQueue
from wechat_agent.infra.wechat.listener import ListenerAdapter
from wechat_agent.logging_setup import log, setup_logging


def build_services(settings: cfg.Settings) -> dict:
    """构建除微信连接外的一切（便于单测）。"""
    store = FileChatStore(settings.data_dir, settings.behavior, settings.personas)

    harness = HeadlessGateway(
        node=settings.llm.dsh_node,
        bin_path=settings.llm.dsh_bin,
        work_dir=settings.llm.work_dir,
        timeout=settings.llm.dsh_timeout,
        concurrency=settings.llm.harness_concurrency,
        max_task_chars=settings.llm.max_task_chars,
        api_key=load_api_key(settings.llm.api_key_file, settings.llm.api_key_field),
        profile=settings.llm.dsh_profile,
    )
    api = ApiGateway(
        url=settings.llm.api_url,
        model=settings.llm.api_model,
        key_file=settings.llm.api_key_file,
        key_field=settings.llm.api_key_field,
        timeout=settings.llm.api_timeout,
        retries=settings.llm.api_retries,
    )
    patch_path = str(cfg.render_group_patch(settings))
    router = LLMRouter(api, harness, group_patch=patch_path)

    skill_loader = SkillLoader(
        roots=[settings.base_dir / "group-skills",
               os.path.join(os.path.expanduser("~"), ".dsh", "skills")],
        basic=settings.skills.basic,
        on_demand=settings.skills.on_demand,
        max_skill_chars=settings.skills.max_skill_chars,
    )
    directives = DirectiveExecutor(store, settings.behavior,
                                   weather_fn=query_weather,
                                   websearch_fn=websearch.web_search)
    chat_service = ChatService(store, router, settings, skill_loader, directives)
    commands = build_registry(store, settings.personas, settings.behavior)
    active_speak = ActiveSpeakService(router, store, settings)
    summary = SummaryService(store, router, settings)
    return {
        "store": store, "router": router, "chat": chat_service,
        "commands": commands, "active_speak": active_speak,
        "summary": summary, "patch_path": patch_path,
    }


def _check_channels(settings: cfg.Settings) -> bool:
    """启动前检查两个 LLM 通道是否至少一个可用，并记录供应商/模型。"""
    from wechat_agent.infra.webui import get_status
    key = load_api_key(settings.llm.api_key_file, settings.llm.api_key_field)
    node_ok = os.path.isfile(settings.llm.dsh_node)
    bin_ok = os.path.isfile(settings.llm.dsh_bin)
    harness_ok = node_ok and bin_ok
    api_ok = bool(key)

    api_label = "opencode.ai" if "opencode.ai" in settings.llm.api_url else \
        "DeepSeek 官方" if "deepseek.com" in settings.llm.api_url else \
        (settings.llm.api_url.split("//")[-1].split("/")[0] if settings.llm.api_url else "?")
    log(f"  ├ [通道] API 直连：{'[OK]' if api_ok else '[X] 无 Key'}"
        f"  目标={api_label}  模型={settings.llm.api_model}  "
        f"key字段={settings.llm.api_key_field}")
    log(f"  ├ [通道] harness：{'[OK]' if harness_ok else '[X] 路径不可用'}  "
        f"node/是否可执行；模型由 DeepSeek Harness 默认模型决定（见 Harness 设置）")
    get_status().set("channels", {
        "api": {"ok": api_ok, "provider": api_label,
                "model": settings.llm.api_model, "key_field": settings.llm.api_key_field},
        "harness": {"ok": harness_ok, "provider": "DeepSeek Harness (headless)",
                    "model": "见 Harness 设置", "node": settings.llm.dsh_node},
    })
    if not api_ok and not harness_ok:
        log("  ✗ 两个 LLM 通道都不可用，无法继续。", "error")
        return False
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="微信 ↔ DeepSeek Harness 桥接机器人 v3")
    parser.add_argument("--check", action="store_true",
                        help="只校验配置与通道，不启动监听")
    parser.add_argument("--config-dir", default=None,
                        help="配置文件目录（默认 config/）")
    args = parser.parse_args(argv)

    settings = cfg.load_settings(args.config_dir)
    setup_logging(str(settings.log_dir))

    log("=" * 50)
    log("[bot] 微信 AI 桥接程序 v3 启动中...")
    log("=" * 50)
    log("[cfg] 配置摘要：")
    log(f"  ├ 大号（全部回复）：{settings.wechat.master_chat}")
    log(f"  ├ 群聊（@才回复）：{'、'.join(settings.wechat.group_chats) or '（无）'}")
    log(f"  ├ 小号昵称：{settings.wechat.self_nick}")
    log(f"  ├ 群聊默认通道：{settings.llm.group_default}")
    log(f"  ├ Web 看板：{'on（端口 ' + str(settings.webui.port) + '）' if settings.webui.enabled else 'off'}")

    if not _check_channels(settings):
        return 1

    svc = build_services(settings)

    if args.check:
        log("✅ --check 通过：配置、存储、通道、补丁均就绪（未连接微信）")
        return 0

    # ===================== 微信连接 =====================
    try:
        from wechatauto import WeChatDB, WeChatGUI
    except ImportError as e:
        log(f"  ✗ wechatauto 库不可用：{e}", "error")
        return 1

    log("📱 连接微信数据库...")
    try:
        if settings.wechat.bot_account:
            log(f"  ⚙️ 显式指定 bot 账号：{settings.wechat.bot_account}")
            db = WeChatDB(account=settings.wechat.bot_account)
        else:
            # 多账号残留时 _pick_account() 可能选错，建议在配置里写 bot_account
            db = WeChatDB()
    except Exception as e:
        log(f"  ✗ 无法连接微信数据库（请确认微信已登录）：{e}", "error")
        return 1
    db_adapter = DBAdapter(db)
    log(f"  ✅ 当前账号（小号）：{db_adapter.self_nick} ({db_adapter.self_username})")
    if settings.wechat.master_chat and db_adapter.self_nick == settings.wechat.master_chat:
        log("  ⚠️ 检测到「bot 自身账号昵称 == master_chat（浅华）」：多半是账号选错了。"
            "请确认电脑上登录的是小号（qianhua），并在 config/settings.yaml 的 "
            "wechat.bot_account 里显式填小号的账号目录。", "warning")

    # 运行时补丁（重装 wechatauto 也不丢）
    wechat_patch.apply_patches()

    # 窗口自检（多维度）
    log("🖥️ 检查微信窗口...")
    try:
        import pythoncom
        pythoncom.CoInitialize()
        gui = WeChatGUI()
        gui.bring_to_front()
        ready, checks = selfcheck.probe_wechat(gui)
        selfcheck.log_checks(checks)
        if not ready:
            log("  ⚠️ 微信窗口未就绪（见上方 ⚠️ 项），发送可能失败，请先处理。", "warning")
    except Exception as e:
        log(f"  ⚠️ 微信窗口预检失败（{e}），继续启动监听。", "warning")

    # ===================== 发送队列 + 流水线 =====================
    b = settings.behavior
    sender = SenderQueue(max_size=b.sender_queue_max, retry=b.send_retry,
                         interactive=b.interactive_failover,
                         max_chars=b.max_reply_chars,
                         outbox_path=settings.data_dir / 'outbox.sqlite3')
    reminder = ReminderService(svc["store"], sender, settings)
    listener = ListenerAdapter(db_adapter, interval=1.0,
                               watermark=svc["store"].load_watermark())

    private_pipe = PrivatePipeline(db=db_adapter, store=svc["store"],
                                   commands=svc["commands"],
                                   chat_service=svc["chat"], sender=sender,
                                   settings=settings)
    group_pipe = GroupPipeline(db=db_adapter, store=svc["store"],
                               commands=svc["commands"],
                               chat_service=svc["chat"], sender=sender,
                               settings=settings,
                               active_speak=svc["active_speak"],
                               summary=svc["summary"])

    # ===================== 扫描会话并注册监听 =====================
    log("[scan] 扫描会话并注册监听...")
    sessions = db_adapter.sessions(limit=300)
    log(f"  发现 {len(sessions)} 个会话")
    added = 0
    listening = []
    for s in sessions:
        u = s["username"]
        nick = db_adapter.nickname(u)
        if nick == settings.wechat.master_chat:
            listener.register(u, lambda msg, lst, u=u: private_pipe.handle(u, msg, lst))
            reminder.register_target(u, nick)
            log(f"  ├ [priv] 私聊模式（全部回复）：{nick}")
            listening.append({"id": u, "nick": nick, "kind": "私聊"})
            added += 1
        elif nick in settings.wechat.group_chats:
            listener.register(u, lambda msg, lst, u=u: group_pipe.handle(u, msg, lst))
            reminder.register_target(u, nick)
            log(f"  ├ [group] 群聊模式（@我才回复）：{nick}")
            listening.append({"id": u, "nick": nick, "kind": "群聊"})
            added += 1
    if added == 0:
        log("  ✗ 没有匹配到任何目标会话！", "warning")
        log("    请检查 config/settings.yaml 里 master_chat / group_chats "
            "是否和微信里完全一致。", "warning")
        for s in sessions:
            log(f"      - {s['username']} | {db_adapter.nickname(s['username'])}", "warning")

    # ===================== 状态 & Web 控制台 =====================
    from wechat_agent.infra.webui import WebUI, get_status
    from wechat_agent.infra.webauth import ensure_token
    from wechat_agent.infra.session_manager import SessionManager
    from wechat_agent.infra.hot_reload import reload_with_diff

    patch_path = svc["patch_path"]
    get_status().update({
        "wechat": {"account": f"{db_adapter.self_nick} ({db_adapter.self_username})",
                   "sessions": len(sessions)},
        "listening": listening,
        "patch": {"path": patch_path, "injected": True,
                  "disabled_tools": list(settings.group_policy.disabled_tools)},
    })
    log(f"[cfg] 安全补丁：{patch_path}")
    log(f"[cfg]    ├ 注入={'已注入' if True else ''}；禁用工具 {len(settings.group_policy.disabled_tools)} 个："
        f"{'、'.join(t.replace('tool-', '') for t in settings.group_policy.disabled_tools)}")
    # ---- Session Manager ----
    def _session_provider() -> list:
        """返回全部微信会话（chat_id / nick / kind），供 Web 会话列表全量展示。"""
        out = []
        try:
            for s in db_adapter.sessions(limit=400):
                u = s["username"]
                nick = db_adapter.nickname(u)
                kind = "群聊" if str(u).endswith("@chatroom") else "私聊"
                out.append({"chat_id": u, "username": u, "nick": nick, "kind": kind})
        except Exception as e:
            log(f"  ⚠️ [会话] 全量会话读取失败：{e}", "warning")
        return out

    def _listen_controller(chat_id: str, enabled: bool) -> None:
        """Web 会话管理监听开关的真实施行：动态 attach/detach listener + 提醒目标。"""
        try:
            nick = db_adapter.nickname(chat_id)
            is_g = str(chat_id).endswith("@chatroom")
            if enabled:
                if is_g:
                    listener.register(
                        chat_id,
                        lambda msg, lst, ci=chat_id: group_pipe.handle(ci, msg, lst))
                else:
                    listener.register(
                        chat_id,
                        lambda msg, lst, ci=chat_id: private_pipe.handle(ci, msg, lst))
                reminder.register_target(chat_id, nick)
                log(f"  ├ [web] 已开启监听：{nick}（{'群聊' if is_g else '私聊'}）")
            else:
                listener.detach(chat_id)
                reminder.unregister_target(chat_id)
                log(f"  ├ [web] 已关闭监听：{nick}")
        except Exception as e:
            log(f"  ⚠️ [web] 监听开关执行失败（{chat_id}）：{e}", "warning")

    sm = SessionManager(settings.data_dir / "session_state.json",
                        provider=_session_provider,
                        controller=_listen_controller)
    sm.set_store(svc["store"])
    for item in listening:
        default_mode = "" if item["kind"] == "群聊" else "harness"
        sm.ensure(item["id"], item["nick"], item["kind"],
                  default_listen=True, default_mode=default_mode)
    sm.sync_all_memory_scopes()
    svc["chat"].set_session_manager(sm)

    # ---- Hot Reload ----
    _current_settings = [settings]
    def _do_reload() -> dict:
        old = _current_settings[0]
        new, cold = reload_with_diff(old, args.config_dir)
        _current_settings[0] = new
        return {"cold": cold}

    # ---- Web UI ----
    token = ensure_token(settings.base_dir) if settings.webui.enabled else ""
    webui = WebUI(host=settings.webui.host, port=settings.webui.port,
                  base_dir=settings.base_dir, token=token,
                  session_manager=sm, reload_fn=_do_reload,
                  store=svc["store"], reminder_service=reminder) \
        if settings.webui.enabled else None
    if webui:
        webui.start()

    # ===================== 后台任务 =====================
    scheduler = JobScheduler()

    def _save_watermark():
        svc["store"].save_watermark(listener.watermark)

    def _heartbeat():
        from wechat_agent.infra.webui import get_status
        st = get_status().snapshot()
        cur_listen = len(listener.attached)   # 实时监听数（含 Web 动态增删）
        log(f"[tick] 运行中：监听 {cur_listen} 个会话，发送队列 {sender.size}，"
            f"提醒目标 {len(reminder.targets)}，已收 {st['counts'].get('inbound', 0)} / "
            f"已发 {st['counts'].get('sent', 0)}")

    def _cleanup_schedules():
        for chat_id in reminder.targets:
            try:
                svc["store"].cleanup_schedules(chat_id, b.schedule_keep_hours)
            except Exception:
                pass

    scheduler.add("watermark-save", _save_watermark, 15)
    scheduler.add("heartbeat", _heartbeat, 300)
    scheduler.add("schedule-cleanup", _cleanup_schedules, 3600)
    from wechat_agent.app.service.balance import BalanceMonitor
    balance_monitor = BalanceMonitor(settings, sender, listening)
    scheduler.add("balance-check", balance_monitor.check, 1800)

    # ===================== 启动 =====================
    sender.start()
    svc["summary"].start()
    reminder.start()
    scheduler.start()
    listener.start()
    log("=" * 50)
    log(f"[bell] 正在监听 {added} 个目标会话（Ctrl+C 停止）"
        + (f"；看板：{webui.url}" if webui else ""))
    log("=" * 50)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("\n收到停止信号，正在退出...")
    finally:
        listener.stop()
        scheduler.stop()
        reminder.stop()
        svc["summary"].stop()
        sender.stop()
        if webui:
            webui.stop()
        svc["store"].save_watermark(listener.watermark)
        log("已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
