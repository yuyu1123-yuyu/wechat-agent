# WeChat Agent

Windows 微信与 DeepSeek Harness / API 的本地桥接实验项目。
基于 [huaqian695-sudo/dsh-wechat-bot](https://github.com/huaqian695-sudo/dsh-wechat-bot) 修改，保留 MIT 许可证。

## 功能

- 指定联系人私聊、可选群聊 @ 回复；默认不启用群聊。
- Harness 工具任务或 API 直连聊天，支持自定义人设。
- 原生收藏表情发送、接收表情图片下载；收藏由模型按语境申请。
- DeepSeek 官方 API 人民币余额低于 10 元提醒。
- 文字回复本地持久队列，失败后 5–60 秒退避重试；超过六小时暂停自动补发。
- Windows 启动器带单实例守护，异常退出后 5–60 秒退避重新启动。

## 安装

需要已登录的 Windows 桌面微信、Python 3.10+。已在 Python 3.12 环境验证。

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item config/local-credentials.example.yaml config/local-credentials.yaml
```

编辑本地 credentials 填入自己的 Key；编辑 settings.yaml 的联系人昵称和机器人昵称。
API 模型名称需按自己的账户实际可用模型配置。
API 通道不需要 Harness；工具任务需要先安装 Harness，并设置 DSH_NODE、DSH_BIN、DSH_WORK_DIR、DSH_PROFILE。
私聊默认使用 Harness，可用项目的模式切换指令选择 API 通道。

```powershell
.venv\Scripts\python.exe -m wechat_agent.main --check
.venv\Scripts\python.exe -m wechat_agent.supervisor
```

也可双击“启动微信AI.bat”。不会注册开机任务。
Web 控制台默认只监听本机；首次运行的访问令牌保存在本地。

## 断网、退出和恢复

保持微信登录、电脑开机且桌面可操作。断网后，微信重新联网并同步到本地数据库的消息才会被读取。
微信退出登录时需本人重新登录，程序不能替代登录。守护进程会在桥接异常退出后重新拉起它，
但守护本身被关闭、电脑关机或系统重启后仍需重新启动。

普通文字发送失败保留在 data/outbox.sqlite3，连接恢复后重试；不要手动上传这个文件。
媒体可能发生部分发送，因此失败后进入 review 状态，不自动重复整条消息。
发送成功指本地微信输入接口返回成功，不等于对方设备已收到；崩溃发生在实际发送与队列确认之间时仍可能重复，
本项目不提供端到端 exactly-once 保证。队列已确认前的上游消息处理也存在极短的崩溃窗口。

## 表情、记忆与权限边界

原生表情依赖当前微信版本 UI，data/native-stickers.json 需要本机实际校验后登记索引。
空登记表不会假定任何用户的收藏顺序；收藏数量检查无法识别“数量不变但重新排序”。
表情识别需要选用的 Harness 模型具备看图工具。本项目不自带视觉服务。
记忆插件需要在自己的 Harness profile 单独安装配置；仓库不包含个人记忆、人设历史、会话或插件数据。
Harness 私聊能调用所配置的工具，请只授权可信联系人，并自行限制工具权限。

## 验证

```powershell
.venv\Scripts\python.exe -m pytest tests/test_outbox.py -q --basetemp=.pytest-recovery
```

已验证队列持久恢复、退避与同联系人顺序、GUI 失败后重建、媒体失败暂停重发。
本机已完成重新登录后的文字接收和回复测试；未以真实整机断网反复验证所有微信状态。
测试通过独立配置 fixture 运行，不依赖或更改使用者的联系人、人设配置。

## 隐私

本仓库是单独整理的源码快照，不包含原工作目录 Git 历史、密钥、聊天记录、登录态、
数据库、个人记忆、表情素材和打包压缩文件。运行日志和待发回复也会含聊天内容，应留在本机。
