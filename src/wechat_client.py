# -*- coding: utf-8 -*-
"""
微信客户端模块

读取：采用 wechatauto 的本地数据库路线（WeChatDB + Listener）
      —— 按昵称精确反查会话 username、基于 sort_seq 增量监听新消息，
         消息内容取自数据库原文，并能通过 sender_id 区分对方与自己。
发送：复用 wechatauto 的 UIA 快速发送（guia.WeChatGUI.send_msg），
     不依赖固定像素坐标。GUI 实例在本类内复用，避免每次发送都重新
     热激活 UIA 树；发送结果默认不做数据库回读校验（见 send_verify）。

两条路线互相独立：读取不需要微信窗口在前台，只有发送才需要。
"""
import ctypes
import ctypes.wintypes as wt
import os
import queue
import sys
import time

# DPI 感知必须在导入 pyautogui 之前设置，否则截图/点击坐标与物理像素不一致
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import pyautogui  # noqa: E402

SW_RESTORE = 9

# wechatauto 源码目录（本地依赖，未发布到 PyPI）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WECHATAUTO_DIR = os.path.join(_PROJECT_ROOT, "wechatauto-replica-main")

# 消息类型名：wechatauto 返回的是中文类型名，不是数字码
MSG_TYPE_TEXT = "文本"

# 内容回声比对窗口（秒）：探测不到自己 sender_id 时的兜底判定，
# 窗口内收到与刚发出内容相同的消息，视为自己的消息回声
ECHO_WINDOW = 30.0


class WeChatClient:
    """微信客户端封装（数据库读取 + UIA 发送）"""

    def __init__(self, wechat_config=None):
        """
        初始化微信客户端

        Args:
            wechat_config: config.yaml 中的 wechat 段配置
        """
        cfg = wechat_config or {}

        # 发送所需的窗口参数（窗口相对像素，依据 827x1044 @100%DPI 实测标定）
        self.window_class = cfg.get("window_class", "Qt51514QWindowIcon")
        self.window_title = cfg.get("window_title", "微信")
        self.search_box = cfg.get("search_box", [185, 57])       # 搜索框中心
        self.input_click = cfg.get("input_click", [575, -114])   # 输入框，负值=距窗口底边
        self.send_key = cfg.get("send_key", "enter")
        # 自己发出的 AI 回复前缀（可通过 config.yaml 的 wechat.self_prefix 修改）
        # 空字符串会让 startswith() 恒为真、吞掉所有消息，必须回退到默认前缀
        self.self_prefix = cfg.get("self_prefix") or "贾维斯："

        # 数据库读取
        self.poll_interval = cfg.get("poll_interval", 1.0)
        self.backfill = int(cfg.get("backfill", 0) or 0)   # 启动时补发条数，0=关闭
        self.db = None
        self.listener = None
        self._chat_ids = {}     # {配置中的聊天名称: 会话 username}
        self._chat_rev = {}     # 反查：{会话 username: 聊天名称}，供统一回调用
        self._queue = queue.Queue()
        self._sent = {}         # {已发送内容: 时间戳}，用于识别自己消息的回声
        self._self_id = None    # 自己在消息表里的 sender_id（由文件传输助手反查）

        # 发送用状态
        self.hwnd = None
        self.rect = None
        self._win_w = 0
        self._win_h = 0
        # 复用的 WeChatGUI 实例：quick_send() 每次新建会导致 UIA 反复热激活
        self._gui = None
        # 是否用数据库回读校验发送结果。默认关闭——数据库解密缓存重建耗时
        # 10~40 秒，且 WAL 撕裂时读到的是旧快照，必然误报「发送失败」。
        self.send_verify = bool(cfg.get("send_verify", False))

        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0.05

    # ------------------------------------------------------------ 数据库读取
    def load_db(self, chat_names):
        """
        初始化微信本地数据库，并把聊天名称解析为会话 username

        Args:
            chat_names: 需要监听的聊天名称列表（昵称/备注/wxid）

        Returns:
            bool: 是否成功
        """
        if os.path.isdir(WECHATAUTO_DIR) and WECHATAUTO_DIR not in sys.path:
            sys.path.insert(0, WECHATAUTO_DIR)

        try:
            from wechatauto.db import WeChatDB, Listener
        except ImportError as e:
            print(f"✗ 无法导入 wechatauto（请确认目录存在: {WECHATAUTO_DIR}）: {e}")
            return False

        # 微信未打开/未登录时 WeChatDB() 会失败；不直接退出，
        # 循环等待用户打开并登录，检测到数据库可读后自动继续。
        retry = 0
        while True:
            try:
                print("正在读取微信数据库（首次需扫描密钥，约 10 秒）...")
                self.db = WeChatDB()
                break
            except Exception as e:
                retry += 1
                if retry == 1:
                    print(f"✗ 微信数据库初始化失败: {e}")
                    print("  请打开微信并登录，检测到后自动继续（每 5 秒检测一次）...")
                elif retry % 12 == 0:   # 每 60 秒再提醒一次，避免刷屏
                    print(f"  ...仍在等待微信登录（已等待约 {retry * 5} 秒）")
                time.sleep(5)

        info = self.db.get_self_info()
        print(f"✓ 微信数据库已就绪，账号: {info.get('nick_name') or info.get('username')}")
        self._self_id = self._detect_self_sender_id()
        print(f"  自己发出的消息 sender_id = {self._self_id}"
              f"（用于过滤，探测不到时回退内容比对）")

        # 昵称 -> 会话 username（含反查表，供统一回调定位聊天名）
        self._chat_ids = {}
        self._chat_rev = {}
        for name in chat_names:
            username = self._resolve_chat_id(name)
            if username:
                self._chat_ids[name] = username
                self._chat_rev[username] = name
                print(f"  ✓ 「{name}」→ {username}")
            else:
                print(f"  ✗ 未找到会话「{name}」，请检查配置中的 chat_name")

        if not self._chat_ids:
            print("✗ 没有任何可监听的会话")
            return False

        # 方案A：禁用监听水位持久化（watermark_file=""）。启动时 add_listener
        # 会以「当前最新消息」作为水位，只处理启动后的新消息，避免上次非正常
        # 退出（水位未落盘）后沿用旧水位重放历史消息。
        self.listener = Listener(self.db, interval=self.poll_interval, watermark_file="")
        return True

    def _resolve_chat_id(self, name):
        """
        把聊天名称解析为会话 username

        优先按微信号/群 id 直接识别，其次通过昵称或备注模糊搜索。
        匹配不到返回 None。
        """
        if name.startswith("wxid_") or "@chatroom" in name or name == "filehelper":
            return name
        try:
            hits = self.db.search_contact(name)
        except Exception as e:
            print(f"  [警告] 查询联系人失败: {e}")
            return None
        for hit in hits:
            if hit.get("username"):
                return hit["username"]
        return None

    def start_listening(self):
        """注册回调并启动监听（回调在工作线程触发，消息入队由主循环处理）"""
        if not self.listener:
            return False
        for name, username in self._chat_ids.items():
            self.listener.add_listener(username, self._on_message)
        if self.backfill > 0:
            self._backfill_pending()
        self.listener.start()
        return True

    def _detect_self_sender_id(self):
        """
        推断「自己」在消息表里的 sender_id

        文件传输助手只会出现自己发出的消息，用它反查最可靠（db.py 的
        sender_username 字段由 SenderName2Id 映射而来，对本账号并不可信）。
        取不到时返回 None，调用方应放弃补发而不是猜测。
        """
        try:
            msgs = self.db.get_messages("filehelper", limit=1)
        except Exception:
            msgs = []
        return msgs[0].get("sender_id") if msgs else None

    def _backfill_pending(self):
        """
        补发启动前漏掉的「对方消息」

        判定：对方消息里时间晚于「自己最后一条消息」的，就是还没有被回复过的
        （自己最后一条之后的对方消息没有归属的回复）。因为回复过之后自己必定
        变成最新一条，所以反复重启也不会重复补发，无需额外状态文件。
        每个会话最多补 self.backfill 条，按时间从旧到新入队。
        """
        self_id = self._self_id
        if self_id is None:
            print("[警告] 无法判断「自己」的 sender_id，跳过漏回消息补发")
            return

        for name, username in self._chat_ids.items():
            try:
                msgs = self.db.get_messages(username, limit=max(self.backfill * 3, 20))
            except Exception as e:
                print(f"[警告] 读取「{name}」历史消息失败: {e}")
                continue

            last_self_seq = max((m["sort_seq"] for m in msgs
                                 if m.get("sender_id") == self_id), default=0)
            pending = []
            for m in reversed(msgs):    # get_messages 返回新→旧，反转成旧→新
                if m.get("sender_id") == self_id or m["sort_seq"] <= last_self_seq:
                    continue
                if m.get("type") != MSG_TYPE_TEXT:
                    continue
                content = (m.get("content") or "").strip()
                if content:
                    pending.append(content)

            for content in pending[-self.backfill:]:
                print(f"  ↻ 补发「{name}」漏回消息: {content}")
                self._queue.put((name, content, False))

    def _on_message(self, msg, _listener):
        """
        统一消息回调（所有会话共用）

        用 msg['username'] 反查当前监听的聊天名，不在监听列表则丢弃。
        这样运行时用 add_listener/remove_listener 增删会话时，无需重建回调。
        """
        if msg.get("type") != MSG_TYPE_TEXT:
            return
        chat_name = self._chat_rev.get(msg.get("username"))
        if chat_name is None:
            return
        content = (msg.get("content") or "").strip()
        if not content:
            return
        # 带自识别前缀的消息是程序自己刚发出的 AI 回复，忽略，防止死循环
        if content.startswith(self.self_prefix):
            return
        if self._self_id is not None and msg.get("sender_id") == self._self_id:
            # 自己手发的消息：入队（is_self=True），由 message_handler 按身份模式
            # 决定——opponent 模式只记上下文；third 模式由 AI 判断是否补回
            self._queue.put((chat_name, content, True))
            return
        if self._self_id is None:
            # 探测不到自己 sender_id 时的兜底：只能靠内容比对
            if time.time() - self._sent.get(content, 0) < ECHO_WINDOW:
                return
        self._queue.put((chat_name, content, False))

    def add_chat(self, name):
        """运行中动态新增一个监听会话（水位置为最新，不回灌历史）"""
        if name in self._chat_ids:
            return False
        username = self._resolve_chat_id(name)
        if not username:
            print(f"✗ 未找到会话「{name}」，添加失败")
            return False
        self._chat_ids[name] = username
        self._chat_rev[username] = name
        if self.listener:
            self.listener.add_listener(username, self._on_message)
            print(f"✓ 新增监听「{name}」→ {username}")
        return True

    def remove_chat(self, name):
        """运行中动态移除一个监听会话"""
        username = self._chat_ids.pop(name, None)
        if not username:
            print(f"✗ 未在监听中的会话「{name}」")
            return False
        self._chat_rev.pop(username, None)
        if self.listener:
            self.listener.remove_listener(username, self._on_message)
            # 移除后该会话仍会被轮询，直接在监听表里摘除，减少多余空查库
            cbs = self.listener._callbacks
            if cbs.get(username) == []:
                cbs.pop(username, None)
            print(f"✓ 已移除监听「{name}」")
        return True

    def wait_message(self, timeout=1.0):
        """
        阻塞等待一条新消息（供主循环在线程中调用）

        Returns:
            tuple[str, str, bool] | None: (聊天名称, 消息内容, 是否自己发出)
        """
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        """停止监听并落盘水位"""
        if self.listener:
            self.listener.stop()
            print("✓ 消息监听已停止")

    # ------------------------------------------------------------------ 发送
    def load_wechat(self):
        """定位并激活微信窗口，缓存窗口矩形（发送前需要）"""
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(self.window_class, self.window_title)
            if not hwnd:
                hwnd = user32.FindWindowW(self.window_class, None)
            if not hwnd:
                print(f"✗ 未找到微信窗口 (class={self.window_class}, title={self.window_title})")
                return False

            self.hwnd = hwnd
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
            time.sleep(1.0)

            self._refresh_rect()
            print(f"✓ 微信窗口已激活: {self._win_w}x{self._win_h} "
                  f"@ ({self.rect[0]},{self.rect[1]})")

            # 预热：首次创建 WeChatGUI 需要热激活 UIA 树（实测约 20 秒），
            # 提前到这里完成，避免第一条消息被白白拖慢
            self._prewarm_gui()
            return True
        except Exception as e:
            print(f"✗ 激活微信窗口失败: {e}")
            return False

    def _prewarm_gui(self):
        """启动时预热 GUI 与 UIA 驱动（失败不影响监听，发送时会自动重试）"""
        t0 = time.time()
        try:
            gui = self._get_gui()
            gui._get_uia()
            print(f"✓ 发送通道预热完成（{time.time() - t0:.1f} 秒）")
        except Exception as e:
            print(f"[警告] 发送通道预热失败（不影响监听）: {e}")

    def _get_gui(self):
        """
        获取复用的 WeChatGUI 实例

        wechatauto 的 quick_send() 每次都新建 WeChatGUI，导致 UIA 树反复
        热激活（实测约 20 秒/次）。这里缓存实例，同一进程只热激活一次；
        微信重启后主窗口句柄失效时自动重建。
        """
        if self._gui is not None:
            if ctypes.windll.user32.IsWindow(self._gui.main_hwnd):
                return self._gui
            print("[警告] 微信主窗口已失效，重建 GUI 实例")
            self._gui = None

        # 确保 wechatauto 源码目录已加入搜索路径
        if os.path.isdir(WECHATAUTO_DIR) and WECHATAUTO_DIR not in sys.path:
            sys.path.insert(0, WECHATAUTO_DIR)
        from wechatauto.guia import WeChatGUI
        self._gui = WeChatGUI()
        return self._gui

    def _refresh_rect(self):
        """刷新缓存的窗口矩形"""
        rect = wt.RECT()
        ctypes.windll.user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        self.rect = (rect.left, rect.top, rect.right, rect.bottom)
        self._win_w = rect.right - rect.left
        self._win_h = rect.bottom - rect.top

    def send_message(self, chat_name, message):
        """
        发送消息（复用 wechatauto 的 UIA 快速发送）

        发送使用目标会话的 username（wxid），由 wechatauto 内部按控件定位，
        不依赖固定像素坐标，避免微信窗口尺寸/位置变化导致发错或发不出。

        Args:
            chat_name: 目标聊天名称
            message: 消息内容

        Returns:
            bool: 是否发送成功
        """
        try:
            gui = self._get_gui()
        except Exception as e:
            print(f"[错误] 无法初始化 wechatauto.guia 用于发送: {e}")
            return False

        try:
            # 给待发送内容加识别前缀，供监听端区分"程序发出的消息"与"手发/对方消息"
            message = f"{self.self_prefix}{message}"
            # 直接传昵称：微信搜索框不认 wxid（wechatauto 自己的注释也写明了），
            # 传 wxid 会让 open_chat 白试两轮约 8 秒；且 UIA 的 current_chat()
            # 返回昵称，传昵称后会话复用判断才能命中，连续回复可跳过整个搜索定位。
            who = chat_name

            resp = gui.send_msg(message, who=who, verify=self.send_verify)
            ok = bool(resp) if resp is not None else False
            if ok:
                print(f"[已发送] → {chat_name}")
                self._sent[message] = time.time()
            else:
                detail = getattr(resp, 'message', None) or str(resp)
                print(f"[错误] 发送给「{chat_name}」失败: {detail}")
            return ok
        except Exception as e:
            print(f"[错误] 发送消息失败: {e}")
            return False