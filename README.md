# 微信AI自动回复系统

基于 wechatauto（本地数据库读取 + UIA 发送）的微信自动回复工具。
AI 后端可接**本地模型**（llama.cpp / Ollama / vLLM，走 OpenAI 兼容接口）
或网页自动化（chat.qwen.ai，无需 API Key）。

## 功能特性

- ✅ 监听指定微信好友/群聊的消息，读本地数据库，不依赖聊天窗口在前台
- ✅ **AI 后端可切本地模型**：不用登录、可离线、不出本机，单轮 0.5~1.2 秒
- ✅ 发送走 UIA 控件定位，不依赖窗口尺寸与像素坐标标定
- ✅ 支持多轮对话上下文，会话之间自动隔离、互不串扰
- ✅ **历史落盘**：重启不失忆；长对话按阈值交班压缩，原文另存归档
- ✅ 支持按会话配置「第三方 / 只回答聊天对象」两种身份模式
- ✅ **自己发的消息默认不抢话**，点名叫助手才回（`self_reply` 可切换）
- ✅ 支持自定义提示词与回复格式模板
- ✅ 支持消息转发到其他联系人/群聊
- ✅ 发送失败会重试并告警，不会静默丢弃
- ✅ 程序自己发出的回复带前缀标识，防止 AI 复读自己造成死循环

> ⚠️ 接本地模型时聊天内容不出本机；接网页模式时会上传至 chat.qwen.ai。
> 本项目不含自有服务端、不收集数据，但请自行评估内容流向。

> ⚠️ 这是**非官方**的微信自动化，违反微信服务协议，存在封号风险。
> 建议使用小号，不要在主号上长期运行。

## 快速开始

### 1. 环境要求

- Windows 系统（发送依赖 Windows UIA 控件接口）
- Python 3.9+
- 微信 PC 客户端，需保持打开并登录

### 2. 安装依赖

```bash
pip install -r requirements.txt
# 只有 ai.provider = web 时才需要下面这行
playwright install chromium
```

Windows 下可直接双击 `install_deps.bat`（内部调用 `install_deps.py`），
分批安装并自动自检；加 `--check` 参数只检查不安装。

> `.bat` 里刻意不写任何中文：中文 Windows 的 cmd 用 OEM 936(GBK) 解析批处理，
> UTF-8 保存的中文会被当成命令执行，报一堆「不是内部或外部命令」。

### 3. 准备 wechatauto 源码

`wechatauto` 未发布到 PyPI，且不随本仓库分发，需自行获取其源码：

```bash
git clone --depth 1 https://github.com/ITquanh/wechatauto-replica.git wechatauto-replica-main
```

放在项目根目录下，目录结构必须是（程序以 `sys.path` 方式引入）：

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

1. 初始化 AI 后端：
   - `provider: openai` → 探测 `base_url` 的 `/v1/models`，自动对齐模型名（不启动浏览器）
   - `provider: web` → 打开浏览器访问 chat.qwen.ai（首次需手动登录）
2. 为配置了双提示词的会话弹出身份模式选择窗
3. 读取微信本地数据库，把 `chat_name` 反查为会话 username
4. 预热微信窗口与 UIA 发送通道（首次约 20 秒）
5. 开始监听新消息，收到后调用 AI 生成回复并发送

## 配置说明

### AI 配置

支持两种后端，由 `provider` 决定：

```yaml
ai:
  # ---- provider: openai（推荐，本地模型）----
  provider: "openai"
  base_url: "http://127.0.0.1:8080/v1"   # 任意 OpenAI 兼容接口：llama.cpp / vLLM / Ollama / LM Studio
  api_key: "not-needed"                  # 本地服务不校验；接远端服务时换成真实 Key
  model: "local"                         # 填 local 即自动采用服务端已加载的模型
  max_tokens: 512
  temperature: 0.7
  top_p: 0.9
  http_timeout: 30                       # 单次 HTTP 超时（秒）。本地模型首 token <2s，30 足够
  fail_threshold: 3                      # 连续失败 N 次触发熔断
  cooldown: 30                           # 熔断冷却时间（秒），期间不再请求、直接跳过
  disable_thinking: true                 # 关掉 Qwen3.5 的思考链，否则 content 会为空

  # ---- provider: web（网页自动化，已不推荐）----
  url: "https://chat.qwen.ai/"
  wait_timeout: 60                       # 等待网页回复的超时（秒）

  # ---- 两种模式共用 ----
  system_prompt: |
    你在替我回复微信消息。
  # ---- 对话历史（重启不失忆 + 长对话不丢细节）----
  history:
    enabled: true
    dir: "data/history"      # 相对项目根目录；每个会话一个 json，另有 _archive.jsonl 全量归档
    max_messages: 60         # 超过这么多条触发交班
    max_chars: 3000          # 或总字符数超过这么多（两个条件任一满足就交班）
    keep_recent: 30          # 交班后原样保留的近期条数
    use_model_summary: true  # 用模型生成摘要；关掉或失败时退回规则式压缩
```

关于历史：微信聊天本来就短，日常几十条远低于阈值，**平时根本不压缩，
细节零丢失**。只有极端长对话才会交班——把最老的压成「约定 / 待办 /
信息 / 人物 / 禁忌」五类要点，近期 30 条原样留着。被压掉的原文不会消失，
在 `data/history/_archive.jsonl` 里按时间全量存着，随时能查「它到底回过什么」。

> `data/history/` 已在 `.gitignore` 里。**别把它提交到仓库**——里面是明文
> 聊天内容和联系人昵称。

两种模式的实测对比（RTX 4060 8G 跑 Qwen3.5-9B-Q4_K_M）：

| | openai（本地） | web（chat.qwen.ai） |
|---|---|---|
| 单轮延迟 | 0.5 ~ 1.3 秒 | 20 ~ 60 秒 |
| 登录态 | 不需要 | 需要，会过期 |
| 断网可用 | 是 | 否 |
| 页面改版风险 | 无 | 选择器失效即不可用 |

本地模型的启动方式见 `I:\llama.cpp\run_server.bat`（llama.cpp + CUDA 构建）。

### 微信配置

```yaml
wechat:
  window_class: "Qt51514QWindowIcon"   # 微信主窗口类名
  window_title: "微信"                  # 微信主窗口标题
  poll_interval: 1.2     # 数据库轮询间隔（秒）。越小越及时，但过低会频繁触发微信主库 WAL 撕裂
  backfill: 0            # 启动时补发「对方漏回消息」的条数，0=关闭
  self_prefix: "贾维斯："  # 程序自己发出的回复所加前缀，监听端据此识别并忽略，防死循环
  send_verify: false     # 是否用数据库回读校验发送结果，建议保持 false（原因见下）
  send_retries: 2        # 发送失败重试次数（每次间隔 1 秒），失败会累计并在日志告警
```

关于 `send_verify`：保持 `false` 时以 UIA 的「粘贴 + 回车已执行」为准，每次发送约 2~4 秒。
改为 `true` 后每次发送都要重建解密缓存（10~40 秒），且微信持续写入导致 WAL 撕裂时
会读到旧快照，必然误报「数据库未确认」——而消息其实已经发出。仅在排查发送问题时临时开启。

> 发送现在不再是「静默失败」：UIA 发送偶发失败会自动重试 `send_retries` 次，
> 全部失败会在日志打印 `[错误] 发送给「xx」连续 N 次失败`，不再让人误以为已发出。

### 聊天规则

```yaml
chat_rules:
  - chat_name: "张三"          # 监听的好友/群聊名称，支持昵称、备注或 wxid
    # 单身份模式：只写 prompt 即可，启动不弹窗
    prompt: |
      ...
    # 双身份模式：同时写下面两项，启动时弹窗二选一
    prompt_third: |            # 第三方（第三人称）：贾维斯是独立参与者，双方都能答
      ...
    prompt_opponent: |         # 只回答聊天对象（第一人称）：以「我」的身份回复
      ...
    reply_template: "{reply}"  # 回复格式模板，{reply} 会被替换为 AI 回复
    forward_to: []             # 转发目标（可选，留空表示不转发）
    self_reply: "mention"      # 自己发的消息怎么处理，见下
    mention_keywords: ["贾维斯", "小贾"]   # 点名触发词（仅 mention 生效）
```

#### 两种身份模式的区别

| | 第一人称（opponent） | 第三人称（third） |
|---|---|---|
| 谁在说话 | 就是「我」本人 | 贾维斯，一个独立存在的第三方 |
| 自称 | 「我」 | 「我」（指贾维斯自己，**不是**「哥哥」） |
| 能回答谁 | 只答对方 | 双方都能答，谁发消息就回谁 |
| 称呼 | 用「宝宝」「晓彤」 | 对主人叫「老板」，对对方叫「宝宝」「晓彤」 |
| 发送前缀 | 不加 | 加 `self_prefix`（如「贾维斯：」），对方能看到 |
| 对「我」的消息 | 只记上下文，不回复 | 默认也只记上下文，点名叫助手才回 |

#### 消息来源标记（程序自动加，不需要你写）

每条消息喂给模型时都会带上固定标记，模型只看标记判断谁说的：

```
【我】老板自己发出的消息
【对方】聊天对象发来的消息
```

同时有三道**代码层**兜底（不依赖模型自觉）：

1. **非文本内容前置过滤** —— 纯表情、`[图片]`、`<msg>` XML、纯 emoji 直接跳过，
   既不推理也不进上下文。这类内容靠提示词让模型自己判断是不可靠的。
2. **说话对象校验** —— 第三方模式下，回对方的话里如果出现「老板」，说明模型串了对象，
   自动重新生成一次（第二次会把硬约束贴到消息末尾），两次都错就本次不发送。
3. **输出清洗** —— 模型会把喂进去的 `【我】`/`【对方】` 标记抄进正文（实测 7 条里有 3 条），
   `clean_reply()` 会连同整句套的引号一起剥掉。

`self_reply` 只影响「第三方」模式（`opponent` 模式下自己发的消息一律只记上下文）。
**对方发来的消息永远会回复，不受这个开关影响。**

| 值 | 行为 |
|---|---|
| `mention` | **默认。** 自己发的消息只有包含触发词（默认 `贾维斯` / `维斯` / `小贾`）才回，其余只记上下文 |
| `never` | 自己发的消息完全不回，只记上下文，连关键词都不看 |
| `always` | 强制回复：提示词里追加「禁止输出 [忽略]」，一定会发出 |
| `auto` | 交给 AI 判断要不要回（9B 判不准，容易抢话或全忽略，慎用） |

> **为什么默认是 `mention`，不是 `auto`。** 真机验证过：`auto` 下你在自己号上
> 正常聊天，AI 会对你**每句话**都插一句「老板，…」，而且真的发到了对方窗口，
> 对方看到的是莫名其妙的「老板，收到，择天那边还在等」。
> 「这条值不值得回」这种判断交给 9B 不可靠；「有没有叫我」是有明确字面依据的，
> 才稳定。想让它重新变成「每句都接」，改成 `always` 即可。

> 提示词里对「什么时候输出 `[忽略]`」要收得很窄。实测给 9B 一句
> 「拿不准就输出不知道 / 不需要就输出 `[忽略]`」，它会把大量正常消息都判成忽略，
> 而且「不知道」三个字会被当正文真的发出去。现在只允许纯占位、系统消息、
> 完全重复这三种情况忽略，其余都要接话。

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
AIGCBD/
├── config/
│   └── config.yaml              # 配置文件
├── src/
│   ├── main.py                  # 主程序入口
│   ├── ai_client.py             # AI 客户端（本地模型 / 网页双后端 + 交班编排）
│   ├── history_store.py         # 对话历史落盘与交班压缩
│   ├── reliability.py           # 熔断、精确匹配、点名判定（纯函数，可测）
│   ├── wechat_client.py         # 微信客户端模块（数据库读取 + UIA 发送）
│   ├── message_handler.py       # 消息处理模块
│   ├── config_loader.py         # 配置加载模块
│   └── identity_picker.py       # 身份模式选择弹窗
├── tests/
│   ├── test_pure.py             # 纯函数测试（47 例）
│   └── test_history.py          # 历史持久化与交班测试（35 例）
├── data/
│   ├── history/                 # 对话历史与全量归档（已 gitignore，含明文聊天内容）
│   ├── browser_profile/         # 浏览器持久化目录（仅 web 模式，已 gitignore）
│   └── logs/                    # 日志目录（已 gitignore）
├── wechatauto-replica-main/     # wechatauto 源码（自行准备，已 gitignore）
├── requirements.txt             # Python 依赖
└── README.md                    # 项目说明
```

跑测试（不依赖模型和微信，直接执行）：

```bash
python tests/test_pure.py
python tests/test_history.py
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
A: 先确认 `self_reply` 是不是被改成了 `always` 或 `auto`。默认值 `mention` 下，
你自己的消息只有含「贾维斯」「小贾」等触发词时才会回，其余只记入上下文。
想彻底不回就设 `never`；想让它接你的每句话就设 `always`。

**Q: 回复里出现了「老板」，但这句话是发给对方的？**
A: 这就是「说话对象串了」。程序有道校验：回对方的话里出现「老板」会自动重新
生成一次，两次都错就本次不发送。若频繁出现，说明该会话的提示词里没把
「你/他/ta」的人称关系写清楚，改提示词比改代码有效。

**Q: 如何清除对话历史？**
A: 清空内存即可——重启程序，或切换到其他会话时会刷新页面重置上下文。

**Q: chat.qwen.ai 需要登录吗？**
A: 首次运行需手动在浏览器中登录，之后登录态保存在 `data/browser_profile/`。

## 许可证

MIT License
