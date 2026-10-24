# wechatauto 实测接口契约

> 这份文件记录的是**踩过的坑**，不是抄来的文档。
> 网上关于 wechatauto 的说明大多过时或凭猜测，下表每一条都在本机
> （微信 4.x / 账号「天恒」/ wechatauto v1.2.3）实测验证过。
> 改 `src/wechat_client.py` 之前先看这里。

## 一、目录结构要求

`src/wechat_client.py` 用 `sys.path.insert` 引入本地源码，插入的那一级**下面
必须直接就是 `wechatauto` 包**：

```
I:\WD\AIGCBD\
├── src\
├── config\
└── wechatauto-replica-main\        ← sys.path 插这一级
    └── wechatauto\
        ├── __init__.py
        ├── db.py
        └── guia.py
```

从 ZIP 解压后如果多套了一层（`wechatauto-replica-main/wechatauto-replica-main/wechatauto`），
把内层移上来即可。

来源：`https://github.com/ITquanh/wechatauto-replica`，tag `1.2.3`。
该目录已在 `.gitignore` 里，不进仓库；更新直接进目录 `git pull`（保留了 `.git`）。

## 二、接口契约（实测，与网上资料有差异）

| 成员 | 实际签名 / 行为 |
|---|---|
| `WeChatDB()` | 无参构造，定位 `xwechat_files/*/db_storage` 并扫进程内存取 SQLCipher 密钥。首次约 6 秒，之后走缓存 |
| `db.get_self_info()` | 返回 `{username, nick_name, remark}` |
| `db.get_messages(user, limit=N)` | **新→旧**。键：`local_id` `type` `sender_id` `sender_username` `create_time` `content` `sort_seq`。**没有 `username` 键** |
| `db.get_sessions(limit=N)` | 会话列表，键含 `username` |
| `db.search_contact(kw)` | 按昵称/备注/微信号/alias 模糊搜，返回 `{username, nick_name, remark}` 列表 |
| `db.get_nickname(user)` | 昵称反查 |
| `Listener(db, interval=, watermark=, watermark_file=)` | `watermark_file=""` 禁用落盘 |
| `listener.add_listener(user, cb)` | 每会话一条串行工作线程，跨会话并行 |
| 回调收到的 msg | `Listener._dispatch()` 会注入 `m["username"] = user` 再分发，**回调里能拿到 `username`**（`get_messages()` 的返回值里没有） |
| `msg["type"]` | **中文名**：`"文本"`、`"图片"`…。由 `db.MSG_TYPE_NAMES` 映射（1→文本、3→图片、49→文件/链接/卡片），复合码取低 32 位 |
| `msg["sender_id"]` | **整数**（不是 wxid 字符串）。实测本账号自己 = **1**，不是网上说的 2 |
| `WeChatGUI()` / `.main_hwnd` / `._get_uia(refresh=False)` | 与调用方一致 |
| `gui.send_msg(text, who=None, verify=False)` | 返回 `WxResponse`（`dict` 子类，键 `status`/`message`/`data`），`__bool__` 取 `status == '成功'` |

### 四个最容易踩的点

1. **`type` 是字符串 `"文本"`，不是数字 1。**
   网上多数微信 hook 项目用 `1`/`49` 这类数字码，wechatauto 返回中文名。
   `src/wechat_client.py` 的 `MSG_TYPE_TEXT = "文本"` 就是为这个。

2. **`sender_id` 不要硬编码。**
   网传「2 表示自己」是错的，本机实测是 `1`，且不同账号可能不同。
   正确做法：拿 `filehelper`（文件传输助手只有自己发的消息）最新一条反查，
   代码里就是这么做的。

3. **`get_messages()` 返回值里没有 `username`。**
   会话名只由 `Listener._dispatch()` 注入到回调的 msg 里。哪天改成手动轮询
   而不是用 Listener，会静默拿不到会话名。

4. **`send_msg` 的 `who` 要传昵称，不能传 wxid。**
   微信搜索框不认 wxid，传 wxid 会让 `open_chat` 白试两轮约 8 秒；
   而且 UIA 的 `current_chat()` 返回昵称，传昵称才能命中会话复用判断，
   连续回复时可以跳过整个搜索定位。

## 三、布局不依赖像素坐标

wechatauto 1.2.3 的布局用**比例常量**（`sidebar_ratio` / `send_button_ratio`），
窗口 896×648 也能自动校准。所以 `config.yaml` 里的 `search_box` / `input_click`
现在只在 `provider: web` 的老路径里用到，改窗口大小不影响发送。

## 四、依赖的真实必要性

`requirements.txt` 列了 14 个包，实际分三档：

| 包 | 必要性 |
|---|---|
| `pyyaml` `pyautogui` `pyperclip` | 本项目直接依赖 |
| `uiautomation` `cryptography` `pywin32` `zstandard` `psutil` `Pillow` `opencv-python` `pypinyin` `colorama` | wechatauto 运行依赖，缺一不可 |
| `winsdk` | **可以不装**。只在「坐标 + OCR」兜底路径里惰性导入。实测不装也能 `import wechatauto.db` 和 `WeChatGUI`。哪天报 `No module named 'winsdk'` 再装（7MB sdist，py3.13 要现场编 C++，很慢） |
| `playwright` | **只在 `provider: web` 时需要**。openai 模式已改成懒加载 |

装包注意：本机 pypi 实测只有 20~47 kB/s，44MB 的 `opencv-python` 靠
`%LOCALAPPDATA%\pip\cache` 才装得快；**装包和运行必须是同一个解释器**，
否则会「装了但跑起来找不到」。

## 五、为什么 `.bat` 里一个中文都没有

`install_deps.bat` 是纯 ASCII 启动器，中文提示全在 `install_deps.py` 里。

第一版把中文直接写在 bat 里，双击后报了一长串
`'緷璧?' 不是内部或外部命令`。原因是中文 Windows 的 cmd 用 OEM 代码页 936(GBK)
**逐字节**解析批处理文件，而文件是 UTF-8 存的——中文字节被当 GBK 解码后，
其中恰好出现的 `0x26`(`&`)、`|` 等字节会被 cmd 切成新命令去执行。

加 `chcp 65001` **救不回来**：cmd 是按字节偏移边读边执行，中途换代码页
只会让后续偏移全部错位，症状更怪。

规矩：**`.bat` 只写 ASCII，中文交给 `.py` 输出**。
自检：`LC_ALL=C grep -c $'[\x80-\xff]' xxx.bat` 必须是 0。
