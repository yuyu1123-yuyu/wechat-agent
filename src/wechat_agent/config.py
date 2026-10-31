# -*- coding: utf-8 -*-
"""配置加载：YAML → dataclass，支持 ${ENV:default} 环境变量插值。

配置文件（config/ 下）：
    settings.yaml      运行配置（微信目标、LLM、行为参数）
    personas.yaml      人设 / 面具
    rules.yaml         默认规则 + 联网搜索指导
    group-policy.yaml  群聊安全策略（渲染成 dsh --patch 补丁）
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


def _detect_base_dir() -> Path:
    """检测项目根目录。打包成 exe 后指向 exe 所在目录，否则按源码结构推断。"""
    import sys
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包：exe 所在目录
        return Path(sys.executable).resolve().parent
    # 源码运行：从 config.py 向上三级（src/wechat_agent/config.py → 项目根）
    return Path(__file__).resolve().parent.parent.parent


BASE_DIR = _detect_base_dir()
CONFIG_DIR = BASE_DIR / "config"

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")


def _interpolate(value):
    """递归替换字符串里的 ${VAR} / ${VAR:default}。"""
    if isinstance(value, str):
        def repl(m):
            name, default = m.group(1), m.group(2)
            return os.environ.get(name, default if default is not None else "")
        return _ENV_RE.sub(repl, value)
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    return value


def _load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件格式错误（应为映射）：{path}")
    return _interpolate(data)


# ===================== 配置模型 =====================

@dataclass
class WeChatConfig:
    master_chat: str = "浅华"
    group_chats: list = field(default_factory=list)
    self_nick: str = "qianhua"
    self_nick_aliases: list = field(default_factory=list)
    ignore_self: bool = True
    # 可选：显式指定 bot 自身登录的微信账号目录（如 wxid_xxx_abcd），
    # 避免多账号残留时 _pick_account() 选错，导致收不到 master_chat 发的消息。
    bot_account: str = ""

    @property
    def nick_names(self) -> list:
        return [n for n in [self.self_nick, *self.self_nick_aliases] if n]


@dataclass
class LLMConfig:
    dsh_profile: str = 'headless'
    dsh_node: str = "node"
    dsh_bin: str = ""
    work_dir: str = ""
    api_key_file: str = "~/.dsh/.credentials.yaml"
    # credentials.yaml 里存 Key 的字段名（与 Harness 一致，如 OPENCODE_API_KEY）
    api_key_field: str = "DEEPSEEK_API_KEY"
    # OpenAI 兼容直连端点（可指向 opencode/其他中转）
    api_url: str = "https://api.deepseek.com/v1/chat/completions"
    api_model: str = "deepseek-v4-flash"
    group_default: str = "api"          # api | harness
    harness_concurrency: int = 2
    dsh_timeout: int = 240
    api_timeout: int = 180
    max_task_chars: int = 20000
    api_retries: int = 2


@dataclass
class BehaviorConfig:
    mention_context_count: int = 10
    ack_message: str = "收到！正在处理中，稍等一下～"
    max_history_msgs: int = 20
    max_history_chars: int = 8000
    group_history_limit: int = 10
    group_history_chars: int = 3000
    group_context_limit: int = 50
    max_reply_chars: int = 1500
    group_summary_every: int = 15
    group_active_speak: bool = True
    group_active_cooldown: int = 900
    group_active_cold_msg: int = 8
    group_active_context: int = 15
    max_input_chars: int = 3000
    catchup_window_minutes: int = 60
    memory_max_lines: int = 50
    schedule_keep_hours: int = 24
    mention_window_sec: int = 90
    send_retry: int = 1
    interactive_failover: bool = False
    sender_queue_max: int = 50
    web_search_guide_enabled: bool = True
    web_search_guide: str = ""
    group_help_text: str = ""
    # list 指令的说明文本（私聊/群聊通用：功能 + 特殊指令）
    list_help_text: str = ""
    # 文本消息内容读不出时（微信加密/压缩），私聊回复的提示
    unreadable_notice: str = ("刚才那条消息微信那边加密/压缩了，我这边读不出内容。"
                              "麻烦缩短一点（50字以内）重新发一下，或拆成几条发～")


@dataclass
class MaskDef:
    persona: str
    greeting: str = ""
    aliases: list = field(default_factory=list)


@dataclass
class PersonasConfig:
    master_persona: str = ""
    group_persona: str = ""
    default_mask: str = "ai助手"
    masks: dict = field(default_factory=dict)       # name -> MaskDef
    group_guide: str = ""
    group_persona_params: dict = field(default_factory=dict)


@dataclass
class SkillsConfig:
    basic: list = field(default_factory=lambda: ["wechat-natural-chat"])
    on_demand: dict = field(default_factory=dict)   # 目录名 -> 触发关键词
    max_skill_chars: int = 800


@dataclass
class GroupPolicyConfig:
    disabled_tools: list = field(default_factory=list)
    skill_dirs: list = field(default_factory=list)
    include_default_roots: bool = True


@dataclass
class WebUIConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8640
    max_log_lines: int = 600


@dataclass
class Settings:
    wechat: WeChatConfig = field(default_factory=WeChatConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)
    personas: PersonasConfig = field(default_factory=PersonasConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    group_policy: GroupPolicyConfig = field(default_factory=GroupPolicyConfig)
    rules: list = field(default_factory=list)
    webui: WebUIConfig = field(default_factory=WebUIConfig)
    base_dir: Path = BASE_DIR

    @property
    def data_dir(self) -> Path:
        return self.base_dir / "data"

    @property
    def log_dir(self) -> Path:
        return self.base_dir / "logs"


# ===================== 加载 =====================

def _build(cls, data: dict):
    """从 dict 构建 dataclass，忽略未知键、补缺省值。"""
    names = {f.name for f in cls.__dataclass_fields__.values()}
    return cls(**{k: v for k, v in (data or {}).items() if k in names})


def _build_masks(raw: dict) -> dict:
    out = {}
    for name, d in (raw or {}).items():
        if isinstance(d, dict):
            out[name] = MaskDef(
                persona=d.get("persona", ""),
                greeting=d.get("greeting", ""),
                aliases=list(d.get("aliases", [])),
            )
    return out


def load_settings(config_dir: str | Path | None = None) -> Settings:
    """读取 config/ 下所有 YAML 并构建 Settings。"""
    cfg_dir = Path(config_dir) if config_dir else CONFIG_DIR

    s = _load_yaml(cfg_dir / "settings.yaml")
    p = _load_yaml(cfg_dir / "personas.yaml")
    r = _load_yaml(cfg_dir / "rules.yaml")
    g = _load_yaml(cfg_dir / "group-policy.yaml")

    behavior = _build(BehaviorConfig, s.get("behavior"))
    behavior.web_search_guide = r.get("web_search_guide", "")

    personas = PersonasConfig(
        master_persona=p.get("master_persona", ""),
        group_persona=p.get("group_persona", ""),
        default_mask=p.get("default_mask", "ai助手"),
        masks=_build_masks(p.get("masks")),
        group_guide=p.get("group_guide", ""),
        group_persona_params=dict(p.get("group_persona_params", {})),
    )

    return Settings(
        wechat=_build(WeChatConfig, s.get("wechat")),
        llm=_build(LLMConfig, s.get("llm")),
        behavior=behavior,
        personas=personas,
        skills=_build(SkillsConfig, s.get("skills")),
        group_policy=_build(GroupPolicyConfig, g),
        rules=list(r.get("rules", [])),
        webui=_build(WebUIConfig, s.get("webui")),
        base_dir=BASE_DIR,
    )


# ===================== 群聊安全补丁渲染 =====================

def render_group_patch(settings: Settings) -> Path:
    """把群聊安全策略渲染成 dsh --patch 补丁文件，返回路径。

    补丁文件不存在时，调用方必须拒绝运行受限模式（绝不退化成全权限）。
    """
    policy = settings.group_policy
    out_dir = settings.data_dir / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "group-lite.yml"

    entries = []
    for tool in policy.disabled_tools:
        entries.append({"id": tool, "disabled": True})
    skill_dirs = [d.replace("{base}", str(settings.base_dir))
                  for d in policy.skill_dirs]
    entries.append({
        "id": "skill-filesystem",
        "config": {
            "customSkillDirs": skill_dirs,
            "includeDefaultRoots": policy.include_default_roots,
        },
    })
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(entries, f, allow_unicode=True, sort_keys=False)
    return path


if __name__ == "__main__":
    st = load_settings()
    print("配置加载成功：")
    print(f"  大号：{st.wechat.master_chat}")
    print(f"  群聊：{st.wechat.group_chats}")
    print(f"  群聊默认通道：{st.llm.group_default}")
    print(f"  面具：{list(st.personas.masks)}")
