# -*- coding: utf-8 -*-
"""本地 DeepSeek Harness headless 网关（全权限，慢）。

调用方式：node dsh --profile headless [--patch 补丁] "任务文本"
安全约定：
- 指定了安全补丁但文件不存在 → 拒绝运行（绝不退化成全权限）；
- 任务文本有硬上限（headless 经命令行 argv 传入，Windows 命令行约 32k）；
- 并发受限（信号量）：headless 每次全新 Node 进程，冷启动吃资源。
"""
from __future__ import annotations

import os
import subprocess
import threading
import time

from wechat_agent.logging_setup import log
from wechat_agent.ports.llm import LLMResult


class HeadlessGateway:
    def __init__(self, node: str, bin_path: str, work_dir: str,
                 timeout: int = 240, concurrency: int = 2,
                 max_task_chars: int = 20000, api_key: str = '', profile: str = 'headless'):
        self._node = node
        import re
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', profile):
            raise ValueError('Invalid Harness profile')
        self._profile = profile
        self._api_key = api_key
        self._bin = bin_path
        self._work_dir = work_dir
        self._timeout = timeout
        self._max_task_chars = max_task_chars
        self._sem = threading.Semaphore(max(1, concurrency))

    def call(self, prompt: str, patch: str | None = None) -> LLMResult:
        prompt += ('\n【表情使用习惯】你有自己的偏好，偏向贴合语境、有趣且不冒犯人的表达，'
                   '不因为收到表情就机械收藏或回送。默认以自然文字接话，'
                   '只有确实适合时才偶尔选择已收藏表情；避免连续重复使用同一张。'
                   '用户明确要求发回某张时才原样发回。收藏申请不等于已收藏成功。')
        prompt += ('\n【微信原生表情发送接口】当前已验证可发送原生动态表情，'
                   '只有在下方已登记列表中存在的标识才可使用，'
                   '最终输出 [[sticker:已登记标识]] 请求发送原生表情。'
                   '发送表情时优先使用sticker标记而非image。每次最多一个，'
                   '不要在正文解释协议；不要声称已发送，桥接程序负责实际发送。'
                   '需要对应语境或用户要求才发，不要每句话发。'
                   '新收到的表情是否收藏成功以本轮程序提供的结果为准。')
        from wechat_agent.infra.wechat.native_stickers import load_registry
        prompt += '\n已登记的原生表情标识：'+', '.join(load_registry()['items'])
        prompt += ('\n\n【当前微信桥接接口，由本机程序提供】你不需要直接操作微信即可发送图片：'
                   '先查看工作区stickers目录并读取选中的图片，然后在最终回复输出'
                   '[[image:真实文件名.png]]。桥接程序会移除标记并发送图片；'
                   '该标记是已实现的输出协议，不是微信工具名称。'
                   '历史中“只能读图、不能发送”的说法已经过时。'
                   '只有最终输出标记才会触发发送，不要声称尝试过不存在的发送工具。'
                   '只能发送本地stickers目录中已存在的PNG，每次最多一张。')
        if len(prompt) > self._max_task_chars:
            raise RuntimeError(
                f"提示词超长（{len(prompt)} > {self._max_task_chars}），"
                "请减小历史/上下文配置。")
        if patch and not os.path.isfile(patch):
            raise RuntimeError(
                f"安全补丁文件不存在：{patch}\n拒绝以全权限运行受限模式。")

        if self._bin.lower().endswith('.cmd'):
            if patch:
                raise RuntimeError('批处理入口暂不支持群聊补丁；请保持群聊关闭')
            cmd = '"' + os.environ.get('COMSPEC', 'C:\\Windows\\System32\\cmd.exe') + '" /d /s /c ""' + self._bin + '" --profile ' + self._profile + ' -"'
        else:
            cmd = [self._node, self._bin, "--profile", self._profile]
        if patch:
            cmd += ["--patch", patch]
        if not self._bin.lower().endswith('.cmd'):
            cmd.append('-')

        log(f"  ├ [harness] 等待并发槽位（上限 {self._sem._value if hasattr(self._sem, '_value') else '?'}）...")
        t0 = time.time()
        with self._sem:
            try:
                result = subprocess.run(
                    cmd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self._timeout,
                    cwd=self._work_dir,
                    env={**os.environ, **({'DEEPSEEK_API_KEY': self._api_key} if self._api_key else {})},
                )
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"DSH 调用超时（>{self._timeout}s）")
            except FileNotFoundError as e:
                raise RuntimeError(f"DSH 无法启动（{e}）——检查 node/dsh 路径配置")
        dt = time.time() - t0

        if result.returncode != 0:
            raise RuntimeError(
                f"DSH 调用失败（退出码 {result.returncode}）："
                f"{result.stderr[-800:] or result.stdout[-800:]}")
        reply = result.stdout.strip()
        if not reply:
            raise RuntimeError("DSH 返回了空回复")
        log(f"  ├ [harness] 完成（{dt:.1f}s，回复 {len(reply)} 字）")
        return LLMResult(text=reply, mode="harness", elapsed=dt)
