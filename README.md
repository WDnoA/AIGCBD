# 微信AI自动回复系统

基于 wechatauto（本地数据库读取 + UIA 发送）与 Playwright 网页自动化的微信自动回复工具。
通过自动化操作 chat.qwen.ai 网页调用通义千问，**无需 API Key**。

## 功能特性

- ✅ 监听指定微信好友/群聊的消息，读本地数据库，不依赖聊天窗口在前台
- ✅ 通过网页自动化调用通义千问生成回复，无需 API Key
- ✅ 发送走 UIA 控件定位，不依赖窗口尺寸与像素坐标标定
- ✅ 支持多轮对话上下文，会话之间自动隔离、互不串扰
- ✅ 支持按会话配置「第三方 / 只回答聊天对象」两种身份模式
- ✅ 支持自定义提示词与回复格式模板
- ✅ 支持消息转发到其他联系人/群聊
- ✅ 程序自己发出的回复带前缀标识，防止 AI 复读自己造成死循环

> ⚠️ 生成回复时，聊天内容会发送至通义千问网页（chat.qwen.ai）。
> 本项目不含自有服务端、不收集数据，但内容仍会离开本机，请自行评估。

## 快速开始

### 1. 环境要求

- Windows 系统（发送依赖 Windows UIA 控件接口）
- Python 3.9+
- 微信 PC 客户端，需保持打开并登录

### 2. 安装依赖

```bash
pip install -r requirements.txt
# 安装 Playwright 浏览器
playwright install chromium
```

### 3. 准备 wechatauto 源码

`wechatauto` 未发布到 PyPI，且不随本仓库分发，需自行获取其源码，
放在项目根目录下的 `wechatauto-replica-main/` 目录中（程序以 `sys.path`
方式引入）：

```
wechatauto-replica-main/
└── wechatauto/
    ├── db.py       # 提供 WeChatDB / Listener（消息读取与监听）
    ├── guia.py     # 提供 WeChatGUI（消息发送）
    └── ...
```

缺失该目录时，启动会直接报错退出并提示路径。

### 4. 配置聊天规则

编辑 `config/config.yaml`，设置要监听的聊天对象与提示词：

```yaml
chat_rules:
  - chat_name: "张三"
    prompt_third: |
      ...
    prompt_opponent: |
      ...
    reply_template: "{reply}"
    forward_to: []
```

若某个会话同时配置了 `prompt_third` 和 `prompt_opponent`，
启动时会弹窗让你二选一，选中项会覆盖该会话的 `prompt`。

### 5. 运行程序

```bash
python src/main.py
```

程序启动后会自动：

1. 打开浏览器访问 chat.qwen.ai（首次需手动登录，之后保持登录态）
2. 为配置了双提示词的会话弹出身份模式选择窗
3. 读取微信本地数据库，把 `chat_name` 反查为会话 username
4. 预热微信窗口与 UIA 发送通道（首次约 20 秒）
5. 开始监听新消息，收到后调用通义千问生成回复并发送

## 配置说明

### AI 配置

```yaml
ai:
  url: "https://chat.qwen.ai/"   # 通义千问网页地址
  system_prompt: |               # 全局默认提示词（会话未单独配置 prompt 时使用）
    你在替我回复微信消息。
  wait_timeout: 60               # 等待 AI 回复的超时时间（秒）；首次提问会跳转新会话，耗时较长
```

### 微信配置

```yaml
wechat:
  window_class: "Qt51514QWindowIcon"   # 微信主窗口类名
  window_title: "微信"                  # 微信主窗口标题
  poll_interval: 1.2     # 数据库轮询间隔（秒）。越小越及时，但过低会频繁触发微信主库 WAL 撕裂
  backfill: 0            # 启动时补发「对方漏回消息」的条数，0=关闭
  self_prefix: "贾维斯："  # 程序自己发出的回复所加前缀，监听端据此识别并忽略，防死循环
  send_verify: false     # 是否用数据库回读校验发送结果，建议保持 false（原因见下）
```

关于 `send_verify`：保持 `false` 时以 UIA 的「粘贴 + 回车已执行」为准，每次发送约 2~4 秒。
改为 `true` 后每次发送都要重建解密缓存（10~40 秒），且微信持续写入导致 WAL 撕裂时
会读到旧快照，必然误报「数据库未确认」——而消息其实已经发出。仅在排查发送问题时临时开启。

### 聊天规则

```yaml
chat_rules:
  - chat_name: "张三"          # 监听的好友/群聊名称，支持昵称、备注或 wxid
    # 单身份模式：只写 prompt 即可，启动不弹窗
    prompt: |
      ...
    # 双身份模式：同时写下面两项，启动时弹窗二选一
    prompt_third: |            # 第三方：可回答双方信息
      ...
    prompt_opponent: |         # 只回答聊天对象信息
      ...
    reply_template: "{reply}"  # 回复格式模板，{reply} 会被替换为 AI 回复
    forward_to: []             # 转发目标（可选，留空表示不转发）
```

### 全局设置

```yaml
settings:
  log_file: "data/logs/messages.log"   # 日志文件路径，同时输出到终端
```

## 注意事项

- 仅支持 Windows 系统
- 微信 PC 客户端必须保持打开并登录
- 建议先用小号测试，避免主号被封
- chat.qwen.ai 需保持登录状态（首次运行需手动登录）
- 浏览器窗口运行时不要最小化
- wechatauto 针对特定微信版本开发，微信大版本升级后控件可能失效，需同步更新该依赖

## 项目结构

```
AIDH/
├── config/
│   └── config.yaml              # 配置文件
├── src/
│   ├── main.py                  # 主程序入口
│   ├── ai_client.py             # AI 客户端模块（Playwright 网页自动化）
│   ├── wechat_client.py         # 微信客户端模块（数据库读取 + UIA 发送）
│   ├── message_handler.py       # 消息处理模块
│   ├── config_loader.py         # 配置加载模块
│   └── identity_picker.py       # 身份模式选择弹窗
├── data/
│   ├── browser_profile/         # 浏览器持久化目录（登录态，已 gitignore）
│   └── logs/                    # 日志目录（已 gitignore）
├── wechatauto-replica-main/     # wechatauto 源码（自行准备，已 gitignore）
├── requirements.txt             # Python 依赖
└── README.md                    # 项目说明
```

## 常见问题

**Q: 为什么收不到消息？**
A: 确认微信已登录，且 `chat_name` 与微信里显示的昵称/备注一致。
启动日志会逐条打印「✓ 「名称」→ wxid」或「✗ 未找到会话」，据此排查。

**Q: 日志反复出现「主库持续处于撕裂状态，改用上一份可用副本」？**
A: 这是微信持续写入与读取并发导致的 WAL 撕裂，属 wechatauto 的固有现象。
调大 `poll_interval`（如 1.2~1.5）可显著降低出现频率。

**Q: AI 回复很慢怎么办？**
A: 首次提问 qwen 会跳转新会话地址，耗时较长，可适当调大 `wait_timeout`；
若持续超时会自动重试一次，仍无结果则本轮不发送（不会发出半截内容）。

**Q: 为什么我的消息也会触发 AI 回复？**
A: 第三方身份模式下，AI 会自行判断你的消息是否需要补话；
不需要时输出 `[忽略]`，程序据此跳过发送、且不会把该占位符写入对话历史。

**Q: 如何清除对话历史？**
A: 清空内存即可——重启程序，或切换到其他会话时会刷新页面重置上下文。

**Q: chat.qwen.ai 需要登录吗？**
A: 首次运行需手动在浏览器中登录，之后登录态保存在 `data/browser_profile/`。

## 许可证

MIT License
