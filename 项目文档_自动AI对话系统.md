# 微信AI自动回复系统 - 项目文档

## 一、项目概述

### 1.1 项目目标

构建一个微信消息自动化工具，读取微信PC本地数据库并自动回复消息，实现：

- 监听指定微信会话（好友/群聊）的文本消息
- 将消息与预设提示词发送到 AI 大模型（通义千问网页 chat.qwen.ai）获取回复
- 将 AI 回复发回微信聊天界面
- 支持三起会话弹窗选择身份模式，以及会话间上下文隔离
- 个人开发者独立完成，本地运行

### 1.2 核心工作流

```
微信PC本地数据库(wechatauto) → Listener监听指定会话 → 入队新文本消息
→ 发送到AI网页(Playwright) → 获取AI回复 → 加前缀 → 发回微信聊天(pyautogui)
```

### 1.3 项目定位

- **个人工具**：单人开发、单人使用
- **AI增强**：通过网页自动化集成通义千问，无需 API Key
- **轻量部署**：本地运行，无需服务器
- **仅Windows**：依赖微信PC客户端与窗口坐标发送，仅支持 Windows

---

## 二、技术方案

### 2.1 选定方案（经实测多方案对比后确定）

| 环节       | 采用方案                                  | 说明                                      |
| ---------- | ----------------------------------------- | ----------------------------------------- |
| **消息读取** | wechatauto-replica-main（本地数据库）      | WeChatDB 读库 + Listener 增量监听，无需窗口前台；按昵称反查会话、按 sort_seq 增量、sender_id 区分双方、watermark 水位机制 |
| **消息发送** | pyautogui 坐标点击 + 剪贴板粘贴           | 沿实测标定窗口几何参数；中文经剪贴板   |
| **AI 对话** | Playwright 自动化 chat.qwen.ai 网页       | 持久化浏览器 profile 保留登录态，无需 API Key |
| **配置管理** | YAML                                     | 监听规则、提示词、AI/微信参数配置          |

### 2.2 方案对比结论（历史教训）

| 备选方案       | 结论                                                            |
| -------------- | --------------------------------------------------------------- |
| wechat-pc-auto / UIAutomation | **不可用**：WeChat 4.1.13.65 控件无法访问                        |
| PyAutoGUI 纯坐标 | 部分可用：仅能发送，无法可靠读取消息，需配合数据库读取          |
| Hook 注入（WeChatFerry） | 不推荐：需旧版微信、有封号风险                                    |

因此读取与发送分离：**读取走本地数据库，发送走坐标**。

---

## 三、技术栈

| 组件       | 技术选型                        | 说明                                       |
| ---------- | ------------------------------- | ------------------------------------------ |
| 微信读取   | wechatauto-replica-main (wechatauto) | 本地依赖源码，读 SQLite 数据库 + Listener |
| 微信发送   | pyautogui + pyperclip           | 坐标点击、剪贴板粘贴                        |
| AI大模型   | Playwright 网页自动化            | 操作 chat.qwen.ai，持久化登录态             |
| 运行环境   | Python（aids 虚拟环境）          | 仅 Windows                                  |
| 微信客户端 | 微信PC版（4.1.13.65）            | 需保持登录状态                               |
| 配置管理   | YAML                             | 监听规则、提示词、AI/微信参数                |

---

## 四、核心功能模块

### 4.1 消息读取模块（wechat_client.py）

- [x] 读取微信本地数据库（WeChatDB，首次扫描密钥约 10 秒）
- [x] 按昵称/备注精确反查会话 username（`_resolve_chat_id`）
- [x] Listener 增量监听指定会话的文本新消息（watermark 水位置为最新 sort_seq）
- [x] 用 sender_id 区分「自己」与「对方」（`_detect_self_sender_id` 经文件传输助手探测，默认 self_id=5）
- [x] 启动补发漏回消息（backfill，仅处理对方消息、文本次序）
- [x] 运行时动态增删监听会话（`add_chat` / `remove_chat`）

### 4.2 AI对话模块（ai_client.py）

- [x] Playwright 持久化上下文启动浏览器，保留 qwen 登录态
- [x] 启动前清理残留 Chromium 进程与 Singleton 锁文件，防 profile 被锁
- [x] 会话切换时刷新网页，清空 qwen 残留历史，避免跨会话上下文串扰
- [x] 每会话独立对话历史（保留最近10轮），提示词会话级优先
- [x] 回复「文本稳定」判定，超时返回 None 不发送半截内容

### 4.3 消息回复模块（wechat_client.py + message_handler.py）

- [x] 通过搜索框打开目标聊天，坐标点击输入框，剪贴板粘贴发送
- [x] 发送内容自动加识别前缀（`self_prefix`，默认「贾维斯：」），监听端据此忽略自己发出的消息，防 AI 复读死循环
- [x] 回复格式模板（replace_template）
- [x] 支持转发到其他联系人/群聊（forward_to）

### 4.4 配置与身份模式

- [x] 监听规则、提示词、AI/微信参数 YAML 配置
- [x] 身份模式弹窗：会话同时配置 `prompt_third` + `prompt_opponent` 时，启动弹窗让使用者二选一并覆盖 `prompt`
- [x] `self_prefix` 配置化（微信发送前缀）

### 4.5 日志与监控

- [x] 双路输出：终端 + 日志文件（`data/logs/messages.log`），`_Tee` 统一
- [x] 消息收发、AI 回复、异常日志

---

## 五、详细实现方案

### 5.1 环境准备

```bash
# 激活 conda 环境（aids）
conda activate aids

# 依赖（Playwright + pyautogui + pyyaml 等）见 requirements.txt
pip install -r requirements.txt

# 安装 Playwright Chromium（需与本机一致）
playwright install chromium

# 确保微信PC客户端已登录，且 wechatauto-replica-main 源码目录与 src 同级
```

> 读取需管理员权限打开微信数据库（首次扫描密钥）；无管理员权限时若已解锁可尝试。

### 5.2 目录结构

```
AIDH/
├── config/
│   └── config.yaml                 # 唯一配置文件
├── src/
│   ├── main.py                     # 主入口（事件驱动主循环）
│   ├── ai_client.py               # AI客户端（Playwright 网页自动化）
│   ├── wechat_client.py           # 微信客户端（数据库读取 + 坐标发送）
│   ├── message_handler.py         # 消息处理（匹配规则、调AI、回复/转发）
│   ├── identity_picker.py         # 身份模式弹窗（tkinter）
│   └── config_loader.py           # 配置加载与校验
├── data/
│   ├── logs/version                # 日志
│   └── browser_profile/           # Playwright 持久化浏览器资料（保留登录态）
├── wechatauto-replica-main/       # wechatauto 本地源码（WeChatDB + Listener）
└── requirements.txt
```

### 5.3 配置文件（config/config.yaml）要点

```yaml
ai:
  url: "https://chat.qwen.ai/"        # 通义千问网页
  system_prompt: |                    # 全局默认提示词（无会话级 prompt 时）
  wait_timeout: 60                    # 等待AI回复超时（秒）

wechat:
  window_class: "Qt51514QWindowIcon"  # 微信真实窗口类名
  window_title: "微信"
  search_box: [185, 57]               # 搜索框中心（窗口相对像素）
  input_click: [575, -114]            # 输入框点击点（负值为距窗口底边）
  send_key: "enter"                   # 发送键
  poll_interval: 1.0                  # 数据库轮询间隔（秒）
  backfill: 3                         # 启动补发漏回消息条数（0=关闭）
  self_prefix: "贾维斯："             # 自己发送的前缀，用于识别 & 防死循环

chat_rules:                           # 监听会话列表
  - chat_name: "魏晓彤"               # 命中该会话即处理
    prompt_third: |                   # 第三方身份提示词（可选）
    prompt_opponent: |                # 仅答聊天对象方式（可选）
    reply_template: "{reply}"
    forward_to: []

settings:
  log_file: "data/logs/messages.log"
```

**要点**：
- 会话同时配置 `prompt_third` + `prompt_opponent` → 启动弹窗二选一，选中值覆盖 `prompt`
- 仅配置 `prompt` → 不弹窗，直接使用
- `self_prefix` 为发送前缀，监听端读到该前缀消息直接忽略，防 AI 复读死循环

### 5.4 启动流程（main.py）

1. 加载并校验 config
2. 启用双路日志（终端 + 文件）
3. 初始化 AI 客户端（Playwright）→ 启动浏览器 → 打开 qwen 网页
4. 对配置了双身份提示词的每个会话弹身份选择窗口，覆盖 prompt
5. 初始化微信客户端（读库、反查会话）
6. 激活微信窗口（失败仅告警，不影响监听）
7. 启动 Listener 监听 + backfill 补发
8. 事件驱动主循环：`wait_message` 阻塞等待新消息 → `handle_message`

### 5.5 死循环防护（重要）

- 发送的所有 AI 回复都带 `self_prefix` 前缀
- 监听回调读到带前缀的消息直接 `return` 忽略
- 会话切换时刷新 qwen 网页，防止上一会话内容串扰当前会话

---

## 六、运行与使用

```bash
# 确保微信PC已登录、窗口未最小化
conda activate aids
python e:/PYnowjj/AIDH/src/main.py
```

运行中：
- 保持微信窗口可见（发送靠坐标）
- 启动时若配置了双身份提示词会弹窗选择
- Ctrl+C 停止，finally 关闭浏览器与监听

---

## 七、风险与注意事项

### 7.1 账号风险

| 风险         | 说明                 | 应对措施                          |
| ------------ | -------------------- | --------------------------------- |
| 操作频率检测 | 频繁点击可能触发风控 | 保持 poll_interval、send 间隔合理 |
| 账号封禁     | 异常行为可能导致封号 | 建议用小号测试，主号谨慎          |

### 7.2 技术风险

| 风险             | 说明                                       | 应对措施                              |
| ---------------- | ------------------------------------------ | ------------------------------------- |
| 微信版本更新     | DB 结构/窗口控件变化导致失效                | 关注 wechatauto 更新；坐标需重新标定  |
| qwen 页面改版    | 选择器失效                                  | 重新标定 ai_client.py 顶部选择器      |
| profile 被锁     | 异常退出残留 chrome 锁死 browser_profile    | 已内置启动前自动清理进程与锁文件      |
| 网络波动         | goto 浏览器时 ERR_CONNECTION_ABORTED        | 检查网络后重试                         |
| 管理员权限       | 读库首次扫描密钥需要权限                    | 以管理员运行                           |

### 7.3 安全建议

- **使用小号测试**，不要直接用主号测试自动化
- **控制频率**：保持轮询与发送间隔，模拟人类行为
- **保持微信窗口可见**：不要最小化、避免遮挡
- **本地运行**：数据本地处理不下传

---

## 八、参考资源

- wechatauto-replica-main 源码（本地依赖，读库 + Listener）
- 微信PC Web版 / 通义千问 chat.qwen.ai（AI 网页）

---

**文档版本**：v4.0
**创建日期**：2026-09-20
**最后更新**：2026-09-23
**说明**：本文档已按当前实际实现（数据库读取 + Playwright 网页 AI + 坐标发送）重写，废弃早期 wechat-pc-auto + DashScope API 的旧方案描述。