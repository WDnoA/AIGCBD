# 微信AI自动回复系统

基于 wechat-pc-auto 和 Playwright 网页自动化的微信自动回复工具。
通过自动化操作 chat.qwen.ai 网页调用通义千问，**无需API Key**。

## 功能特性

- ✅ 监听指定微信好友/群聊的消息
- ✅ 自动通过网页自动化调用通义千问生成回复
- ✅ 无需API Key，直接使用 chat.qwen.ai 网页
- ✅ 支持多轮对话上下文
- ✅ 支持自定义提示词和回复格式
- ✅ 支持消息转发到其他联系人/群聊
- ✅ 本地运行，数据不上传云端

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
# 安装 Playwright 浏览器
playwright install chromium
```

### 2. 配置聊天规则

编辑 `config/config.yaml`，设置要监听的聊天窗口：

```yaml
chat_rules:
  - chat_name: "张三"
    reply_template: "{reply}"
    forward_to: []
```

### 3. 运行程序

```bash
# 确保微信PC客户端已打开并登录
python src/main.py
```

程序启动后会自动：

1. 打开浏览器访问 chat.qwen.ai
2. 激活微信PC客户端窗口
3. 开始监听指定聊天的新消息
4. 收到消息后通过网页调用通义千问生成回复
5. 将回复发回微信聊天窗口

## 配置说明

### AI配置（网页自动化）

```yaml
ai:
  url: "https://chat.qwen.ai/" # 通义千问网页地址
  system_prompt: "你是一个友好的微信助手..." # 系统提示词
  wait_timeout: 30 # 等待AI回复的超时时间（秒）
```

### 聊天规则

```yaml
chat_rules:
  - chat_name: "张三" # 监听的好友/群聊名称
    reply_template: "{reply}" # 回复格式模板
    forward_to: # 转发目标（可选）
      - "文件传输助手"
      - "李四"
```

## 注意事项

- 仅支持 Windows 系统
- 需要 Python 3.8+
- 微信PC客户端必须保持打开并登录
- 建议先用小号测试，避免主号被封
- chat.qwen.ai 网页需要保持登录状态（首次运行需手动登录）
- 浏览器窗口运行时不要最小化

## 项目结构

```
AIDH/
├── config/
│   └── config.yaml          # 配置文件
├── src/
│   ├── main.py              # 主程序入口
│   ├── ai_client.py         # AI客户端模块（Playwright网页自动化）
│   ├── wechat_client.py     # 微信客户端模块
│   ├── message_handler.py   # 消息处理模块
│   └── config_loader.py     # 配置加载模块
├── data/
│   └── logs/                # 日志目录
├── requirements.txt         # Python依赖
└── README.md                # 项目说明
```

## 常见问题

**Q: 为什么收不到消息？**
A: 确保微信窗口没有被最小化，且聊天窗口名称与配置中的 `chat_name` 完全一致。

**Q: AI回复很慢怎么办？**
A: 可以调整 `wait_timeout` 参数，或检查网络连接。

**Q: 如何清除对话历史？**
A: 重启程序即可清除所有对话历史。

**Q: chat.qwen.ai 需要登录吗？**
A: 首次运行需要手动在浏览器中登录，之后会保持登录状态。

## 许可证

MIT License
