# -*- coding: utf-8 -*-
"""配置文件读写器：PyYAML 读→改→写回（接受注释丢失）。

支持 settings.yaml / personas.yaml / rules.yaml / group-policy.yaml 四个文件。
所有写入都原子替换（先写临时文件再 rename），避免写到一半损坏。
线程安全：全局锁防并发写。
"""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

import yaml

_lock = threading.Lock()


def _atomic_write(path: Path, data) -> None:
    """原子写入 YAML：先写临时文件再 replace。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False,
                           default_flow_style=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_yaml(path: Path) -> dict | list:
    """读取 YAML 文件；不存在或空返回 {}。"""
    if not path.is_file():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if data is not None else {}


def write_yaml(path: Path, data) -> None:
    """原子写入 YAML。"""
    with _lock:
        _atomic_write(path, data)


def update_yaml(path: Path, patch: dict) -> dict:
    """浅合并更新顶层键（patch 中的键覆盖原值；值为 None 则删除该键）。
    返回更新后的完整数据。"""
    with _lock:
        data = read_yaml(path)
        if not isinstance(data, dict):
            data = {}
        for k, v in patch.items():
            if v is None:
                data.pop(k, None)
            else:
                data[k] = v
        _atomic_write(path, data)
        return data


def deep_update(base: dict, patch: dict) -> dict:
    """递归合并两个 dict（patch 覆盖 base；仅对嵌套 dict 递归，其余直接覆盖）。"""
    out = dict(base)
    for k, v in patch.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_update(out[k], v)
        elif v is None:
            out.pop(k, None)
        else:
            out[k] = v
    return out


def deep_update_file(path: Path, patch: dict) -> dict:
    """递归合并更新 YAML 文件。"""
    with _lock:
        data = read_yaml(path)
        if not isinstance(data, dict):
            data = {}
        merged = deep_update(data, patch)
        _atomic_write(path, merged)
        return merged


def write_obj(path: Path, obj) -> dict:
    """整体覆盖写 YAML 文件（用于前端“整表替换”提交，需带 __replace 标记）。"""
    with _lock:
        if obj is None or not isinstance(obj, dict):
            obj = {}
        obj.pop("__replace", None)
        _atomic_write(path, obj)
        return obj
