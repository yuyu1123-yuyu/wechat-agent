# -*- coding: utf-8 -*-
"""本地 Web 控制台（零第三方依赖，stdlib 实现）。

页面（SPA 6 Tab）：
  /               监控（状态卡片 + SSE 实时日志）
  #sessions       会话管理（监听/自主发言/模式/面具）
  #config         settings.yaml 可视化编辑
  #personas       personas.yaml 编辑
  #rules          rules.yaml 编辑
  #security       group-policy.yaml 编辑

API：
  POST /api/auth              {token} → Set-Cookie
  GET  /api/status            运行状态
  GET  /api/logs              最近日志
  GET  /events                SSE 实时日志
  GET  /api/sessions          会话列表+状态
  POST /api/sessions/<id>/listen       {enabled}
  POST /api/sessions/<id>/active-speak {enabled, cooldown?}
  POST /api/sessions/<id>/mode         {mode}
  POST /api/sessions/<id>/mask         {mask}
  GET  /api/config            settings.yaml（脱敏）
  POST /api/config            写入 settings.yaml
  GET  /api/personas          personas.yaml
  POST /api/personas          写入
  GET  /api/rules             rules.yaml
  POST /api/rules             写入
  GET  /api/group-policy      group-policy.yaml
  POST /api/group-policy      写入
  POST /api/reload            热重载配置
"""
from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from wechat_agent.logging_setup import broker, log as lg


# ===================== Status =====================

class Status:
    def __init__(self):
        self._lock = threading.RLock()
        self._d = {
            "started_at": time.time(), "version": "3.0.0",
            "channels": {}, "patch": {}, "wechat": {},
            "listening": [], "counts": {"inbound": 0, "sent": 0,
                                        "memory_writes": 0, "tools": {}},
        }
        self._extra: list[dict] = []

    def set(self, key: str, value) -> None:
        with self._lock:
            self._d[key] = value

    def update(self, mapping: dict) -> None:
        with self._lock:
            self._d.update(mapping)

    def incr(self, key: str, by: int = 1) -> None:
        with self._lock:
            c = self._d.setdefault("counts", {})
            c[key] = c.get(key, 0) + by

    def tool(self, name: str, count: int = 1) -> None:
        with self._lock:
            t = self._d.setdefault("counts", {}).setdefault("tools", {})
            t[name] = t.get(name, 0) + count

    def note(self, text: str) -> None:
        with self._lock:
            self._extra.append({"t": time.strftime("%H:%M:%S"), "s": text})
            del self._extra[:-40]

    def snapshot(self) -> dict:
        with self._lock:
            d = json.loads(json.dumps(self._d, ensure_ascii=False))
            d["uptime"] = int(time.time() - self._d.get("started_at", time.time()))
            d["notes"] = list(self._extra)
            return d


_STATUS = Status()


def get_status() -> Status:
    return _STATUS


# ===================== Handler =====================

class _Handler(BaseHTTPRequestHandler):
    server_version = "WechatBridgeWebUI/2.0"

    def log_message(self, *a):
        pass

    # ---- auth ----

    def _check_auth(self) -> bool:
        """校验 /api/* 请求（/api/auth 除外）。返回 True=通过。

        凭证来源依次：cookie / Authorization header / URL ?token=（供 SSE）"""
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/auth":
            return True
        token = self.server.webui_token  # type: ignore[attr-defined]
        if not token:
            return True  # 未启用鉴权
        cookies = self.headers.get("Cookie", "") or ""
        from wechat_agent.infra.webauth import verify_request
        if verify_request(dict(self.headers), cookies, token):
            return True
        # 允许 ?token=xxx（EventSource 无法设置自定义 header，只能拼 URL）
        from urllib.parse import parse_qs
        q = parse_qs(parsed.query)
        if q.get("token") and q["token"][0] == token:
            return True
        self._send_json({"error": "unauthorized"}, 401)
        return False

    # ---- routing ----

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send_text(_INDEX_HTML, "text/html; charset=utf-8")
            return
        if not self._check_auth():
            return
        if path == "/api/status":
            self._send_json(get_status().snapshot())
        elif path == "/api/logs":
            self._send_json({"logs": broker().snapshot()})
        elif path == "/events":
            self._sse()
        elif path == "/api/sessions":
            sm = self.server.session_manager  # type: ignore[attr-defined]
            self._send_json({"sessions": sm.snapshot() if sm else []})
        elif path == "/api/config":
            self._serve_config("settings")
        elif path == "/api/schema":
            from wechat_agent.infra.ui_schema import build_schema_payload
            self._send_json(build_schema_payload())
        elif path == "/api/personas":
            self._serve_config("personas")
        elif path == "/api/rules":
            self._serve_config("rules")
        elif path == "/api/group-policy":
            self._serve_config("group-policy")
        elif path == "/api/schedules":
            self._handle_schedules_list()
        else:
            self.send_error(404)

    def do_POST(self):
        if not self._check_auth():
            return
        path = urlparse(self.path).path
        body = self._read_body()

        if path == "/api/auth":
            self._handle_auth(body)
        elif path == "/api/reload":
            self._handle_reload()
        elif path == "/api/config":
            self._handle_config_write("settings", body)
        elif path == "/api/personas":
            self._handle_config_write("personas", body)
        elif path == "/api/rules":
            self._handle_config_write("rules", body)
        elif path == "/api/group-policy":
            self._handle_config_write("group-policy", body)
        elif path == "/api/sessions/batch":
            self._handle_sessions_batch(body)
        elif path.startswith("/api/sessions/"):
            self._handle_session_action(path, body)
        elif path == "/api/schedules":
            self._handle_schedule_add(body)
        else:
            self.send_error(404)

    def do_DELETE(self):
        if not self._check_auth():
            return
        path = urlparse(self.path).path
        # /api/schedules/<chat_id>/<schedule_id>
        m = re.match(r"^/api/schedules/([^/]+)/([^/]+)$", path)
        if m:
            self._handle_schedule_delete(m.group(1), m.group(2))
        else:
            self.send_error(404)

    # ---- helpers ----

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length > 0 else b""

    def _parse_json(self, raw: bytes) -> dict | None:
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def _send_text(self, text: str, ctype: str, status: int = 200) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, obj, status: int = 200) -> None:
        self._send_text(json.dumps(obj, ensure_ascii=False),
                        "application/json; charset=utf-8", status)

    # ---- config endpoints ----

    def _config_path(self, name: str) -> Path:
        base = self.server.base_dir  # type: ignore[attr-defined]
        return base / "config" / f"{name}.yaml"

    def _serve_config(self, name: str) -> None:
        from wechat_agent.infra.config_writer import read_yaml
        data = read_yaml(self._config_path(name))
        # 脱敏：隐藏 api key 相关字段的实际值
        if name == "settings":
            llm = data.get("llm", {})
            for k in ("api_key_file",):
                if k in llm:
                    llm[k] = llm[k]  # 路径不脱敏
        self._send_json(data)

    def _handle_config_write(self, name: str, raw: bytes) -> None:
        patch = self._parse_json(raw)
        if patch is None:
            self._send_json({"error": "invalid json"}, 400)
            return
        if isinstance(patch, dict) and patch.get("__replace"):
            # 前端“整表替换”提交：整体覆盖（personas/rules/security 用）
            from wechat_agent.infra.config_writer import write_obj
            result = write_obj(self._config_path(name), patch)
        else:
            from wechat_agent.infra.config_writer import deep_update_file
            result = deep_update_file(self._config_path(name), patch)
        lg(f"[web] 配置已更新：{name}.yaml")
        self._send_json({"ok": True, "data": result})

    # ---- session endpoints ----

    def _handle_sessions_batch(self, raw: bytes) -> None:
        """批量保存会话修改（Web「保存」按钮）。"""
        body = self._parse_json(raw)
        if not body or not isinstance(body.get("updates"), list):
            self._send_json({"error": "需要 updates 列表"}, 400)
            return
        sm = self.server.session_manager  # type: ignore[attr-defined]
        if sm is None:
            self._send_json({"error": "session manager not ready"}, 503)
            return
        updates = [u for u in body["updates"] if isinstance(u, dict)]
        res = sm.batch_update(updates)
        lg(f"[web] 会话批量保存：{res.get('applied', 0)} 条已应用")
        self._send_json(res)

    def _handle_session_action(self, path: str, raw: bytes) -> None:
        # /api/sessions/<id>/<action>
        m = re.match(r"^/api/sessions/([^/]+)/(listen|active-speak|mode|mask|share-memory)$", path)
        if not m:
            self.send_error(404)
            return
        chat_id = m.group(1)
        action = m.group(2)
        body = self._parse_json(raw)
        if body is None:
            self._send_json({"error": "invalid json"}, 400)
            return
        sm = self.server.session_manager  # type: ignore[attr-defined]
        if sm is None:
            self._send_json({"error": "session manager not ready"}, 503)
            return
        ok = False
        if action == "listen":
            ok = sm.set_listen(chat_id, bool(body.get("enabled", False)))
        elif action == "active-speak":
            ok = sm.set_active_speak(chat_id, bool(body.get("enabled", True)),
                                     body.get("cooldown"))
        elif action == "mode":
            ok = sm.set_mode(chat_id, str(body.get("mode", "")))
        elif action == "mask":
            ok = sm.set_mask(chat_id, str(body.get("mask", "")))
        elif action == "share-memory":
            ok = sm.set_share(chat_id, bool(body.get("enabled", False)))
        if ok:
            lg(f"[web] 会话 {chat_id[:20]}… {action} 已更新")
            self._send_json({"ok": True})
        else:
            self._send_json({"error": "update failed"}, 400)

    # ---- schedule endpoints ----

    def _handle_schedules_list(self) -> None:
        store = self.server.store  # type: ignore[attr-defined]
        if store is None:
            self._send_json({"schedules": []})
            return
        items = store.list_all_schedules()
        self._send_json({"schedules": items})

    def _handle_schedule_add(self, raw: bytes) -> None:
        body = self._parse_json(raw)
        if not body or "chat_id" not in body or "when" not in body or "event" not in body:
            self._send_json({"error": "需要 chat_id, when (YYYY-MM-DD HH:MM), event"}, 400)
            return
        from datetime import datetime as dt
        try:
            when = dt.strptime(body["when"], "%Y-%m-%d %H:%M")
        except ValueError:
            self._send_json({"error": "when 格式应为 YYYY-MM-DD HH:MM"}, 400)
            return
        store = self.server.store  # type: ignore[attr-defined]
        if store is None:
            self._send_json({"error": "store not ready"}, 503)
            return
        mask = body.get("mask") or None
        ok = store.add_schedule(body["chat_id"], when, body["event"], mask)
        if ok:
            lg(f"[web] 日程已添加：{body['chat_id'][:20]}… {body['when']} {body['event']}")
            # 通知 ReminderService 重算下次唤醒
            reminder = self.server.reminder_service  # type: ignore[attr-defined]
            if reminder:
                try:
                    reminder.notify()
                except Exception:
                    pass
            self._send_json({"ok": True})
        else:
            self._send_json({"error": "添加失败"}, 500)

    def _handle_schedule_delete(self, chat_id: str, schedule_id: str) -> None:
        store = self.server.store  # type: ignore[attr-defined]
        if store is None:
            self._send_json({"error": "store not ready"}, 503)
            return
        # 尝试顶层 + 各面具
        ok = store.delete_schedule(chat_id, schedule_id)
        if not ok:
            # 可能在某个面具下，遍历 masks 目录
            from pathlib import Path
            masks_dir = store._chat_dir(chat_id) / "masks"
            if masks_dir.is_dir():
                for mask_dir in masks_dir.iterdir():
                    if mask_dir.is_dir():
                        if store.delete_schedule(chat_id, schedule_id, mask=mask_dir.name):
                            ok = True
                            break
        if ok:
            lg(f"[web] 日程已删除：{chat_id[:20]}…/{schedule_id}")
            self._send_json({"ok": True})
        else:
            self._send_json({"error": "未找到该日程"}, 404)

    # ---- auth endpoint ----

    def _handle_auth(self, raw: bytes) -> None:
        body = self._parse_json(raw)
        if not body or "token" not in body:
            self._send_json({"error": "missing token"}, 400)
            return
        expected = self.server.webui_token  # type: ignore[attr-defined]
        if body["token"] != expected:
            self._send_json({"error": "invalid token"}, 403)
            return
        from wechat_agent.infra.webauth import set_cookie_header
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", set_cookie_header(expected))
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    # ---- reload ----

    def _handle_reload(self) -> None:
        reloader = self.server.reload_fn  # type: ignore[attr-defined]
        if reloader is None:
            self._send_json({"error": "reload not available"}, 503)
            return
        try:
            result = reloader()
            lg("[web] 配置热重载完成")
            self._send_json({"ok": True, "cold_changes": result.get("cold", [])})
        except Exception as e:
            lg(f"[web] 热重载失败：{e}", "error")
            self._send_json({"error": str(e)}, 500)

    # ---- SSE ----

    def _sse(self) -> None:
        ident = broker().subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self._sse_send({"type": "status", "data": get_status().snapshot()})
            for line in broker().snapshot():
                self._sse_send({"type": "log", "data": line})
            while True:
                line = broker().pull(ident, timeout=8)
                if line is None:
                    self._sse_send({"type": "ping", "data": ""})
                    continue
                self._sse_send({"type": "log", "data": line})
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            broker().unsubscribe(ident)

    def _sse_send(self, obj) -> None:
        payload = json.dumps(obj, ensure_ascii=False)
        self.wfile.write(("data: " + payload + "\n\n").encode("utf-8"))
        self.wfile.flush()


# ===================== WebUI Server =====================

class WebUI:
    def __init__(self, host: str = "127.0.0.1", port: int = 8640,
                 base_dir: Path | None = None, token: str = "",
                 session_manager=None, reload_fn=None,
                 store=None, reminder_service=None):
        self._host = host
        self._port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url = ""
        self._base_dir = base_dir or Path(".")
        self._token = token
        self._session_manager = session_manager
        self._reload_fn = reload_fn
        self._store = store
        self._reminder_service = reminder_service

    def start(self) -> None:
        try:
            self._httpd = ThreadingHTTPServer((self._host, self._port), _Handler)
        except OSError as e:
            lg(f"[web] 启动失败（{e}）", "error")
            return
        # 注入上下文到 server 实例
        self._httpd.base_dir = self._base_dir  # type: ignore[attr-defined]
        self._httpd.webui_token = self._token  # type: ignore[attr-defined]
        self._httpd.session_manager = self._session_manager  # type: ignore[attr-defined]
        self._httpd.reload_fn = self._reload_fn  # type: ignore[attr-defined]
        self._httpd.store = self._store  # type: ignore[attr-defined]
        self._httpd.reminder_service = self._reminder_service  # type: ignore[attr-defined]
        self.url = f"http://{self._host}:{self._port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="webui", daemon=True)
        self._thread.start()
        lg(f"[web] 控制台已启动：{self.url}")
        if self._token:
            lg(f"[web] 登录 Token：{self._token}（首次打开页面时输入）")

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
        if self._thread:
            self._thread.join(timeout=3)


# ===================== 前端 HTML（从 webui.html 加载，带内嵌兜底） =====================

def _load_ui_html() -> str:
    """加载前端页面。优先读同目录 webui.html（可热改无需重启），找不到用内嵌最小版。"""
    here = Path(__file__).resolve().parent
    f = here / "webui.html"
    if f.is_file():
        try:
            return f.read_text(encoding="utf-8")
        except Exception:
            pass
    # 极简兜底：指向文件缺失提示
    return "<!DOCTYPE html><html><head><meta charset=utf-8><title>加载失败</title></head>"\
           "<body><h1>webui.html 未找到</h1><p>请确保 src/wechat_agent/infra/webui.html 存在。</p></body></html>"


_INDEX_HTML = _load_ui_html()
