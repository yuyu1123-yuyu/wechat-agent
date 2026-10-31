# -*- coding: utf-8 -*-
"""存储 schema：记录版本与读写辅助。

所有 json 记录带 "version" 字段，未来升级格式时按版本迁移。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

VERSION = 1


def wrap(obj: dict, version: int = VERSION) -> dict:
    return {"version": version, **obj}


def unwrap(data: dict, default=None):
    """读版本号并返回内容；非法结构返回 default。"""
    if isinstance(data, dict) and "version" in data:
        return data
    return default


def read_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path: Path, obj) -> None:
    """原子写：先写临时文件再替换，防半写坏文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_jsonl(path: Path, default=None):
    default = default if default is not None else []
    if not path.is_file():
        return default
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    except Exception:
        return default
    return out


def append_jsonl(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def rewrite_jsonl(path: Path, items: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
