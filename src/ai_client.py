# -*- coding: utf-8 -*-
"""
AI客户端模块
通过Playwright自动化操作chat.qwen.ai网页（无需API Key）

选择器基于实测的 Qwen Studio 页面结构标定：
    输入框    textarea.message-input-textarea
    发送按钮  button.send-button[aria-label="发送"]
    回复容器  .qwen-chat-message-assistant
    回复正文  .response-message-content.phase-answer
页面改版后需要重新标定。
"""
import glob
import os
import subprocess
import time
import asyncio

from playwright.async_api import async_playwright


class AIClient:
    """AI客户端封装 - 通过网页自动化"""

    # --- 页面选择器（实测标定） ---
    INPUT_SEL = "textarea.message-input-textarea"
    SEND_SEL = 'button.send-button[aria-label="发送"]'
    ASSISTANT_SEL = ".qwen-chat-message-assistant"
    ANSWER_SEL = ".response-message-content.phase-answer"
    ANSWER_FALLBACK_SEL = ".qwen-markdown"

    def __init__(self, ai_config):
        """
        初始化AI客户端

        Args:
            ai_config: AI配置字典
        """
        self.url = ai_config.get("url", "https://chat.qwen.ai/")
        self.system_prompt = ai_config.get("system_prompt", "你是一个有帮助的助手。")
        self.wait_timeout = ai_config.get("wait_timeout", 30)  # 等待AI回复的超时时间（秒）

        # 持久化用户数据目录：保留登录态与历史，避免每次启动都处于未登录状态
        default_profile = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "browser_profile",
        )
        self.user_data_dir = ai_config.get("user_data_dir") or default_profile

        # Playwright相关
        self.playwright = None
        self.context = None
        self.page = None

        # 对话历史（每个聊天窗口独立）
        self.chat_history = {}
        # 当前 qwen 页面服务于哪个会话（用于检测会话切换后刷新网页，隔离残留历史）
        self._active_chat = None

    def _cleanup_stale_profile(self):
        """
        启动前回收被残留 Chromium 进程锁定的 profile 目录

        上次运行若异常退出（强制结束、崩溃），其子进程仍会占用 user_data_dir，
        新实例检测到 profile 被占用后会瞬间退出（Target page...closed）。
        这里在启动前：1) 结束仍在占用本 profile 路径的 chrome 进程；
        2) 删除 Chromium 残留的 Singleton* 锁文件。
        """
        # 匹配带引号/不带引号的绝对路径，避免误杀用户日常浏览器
        profile = os.path.abspath(self.user_data_dir).replace("\\", "/").lower()
        try:
            ps = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/FO", "CSV"],
                capture_output=True, text=True, errors="ignore", timeout=30,
            )
            for line in ps.stdout.splitlines()[1:]:
                if not line.startswith('"chrome.exe"'):
                    continue
                pid = line.split(',')[1].strip('"')
                try:
                    cmd = subprocess.run(
                        ["wmic", "process", "where",
                         f"ProcessId={pid}", "get", "CommandLine",
                         "/VALUE"],
                        capture_output=True, text=True, errors="ignore", timeout=15,
                    )
                    cl = cmd.stdout.replace("CommandLine=", "").replace("\\", "/").lower()
                    if profile in cl:
                        subprocess.run(
                            ["taskkill", "/PID", pid, "/F"],
                            capture_output=True, timeout=15,
                        )
                except Exception:
                    continue
        except Exception as e:
            print(f"[警告] 清理残留 chrome 进程失败: {e}")

        # 删除 profile 目录下的 Chromium 单例锁文件（被占用时会删除失败，可忽略）
        for lock in glob.glob(os.path.join(self.user_data_dir, "Singleton*")):
            try:
                os.remove(lock)
            except OSError:
                pass

    async def init_browser(self):
        """初始化浏览器（持久化上下文，保留登录态）"""
        try:
            os.makedirs(self.user_data_dir, exist_ok=True)
            # 回收上次异常退出遗留的进程/锁文件，防止新实例启动即被关闭
            self._cleanup_stale_profile()

            self.playwright = await async_playwright().start()
            self.context = await self.playwright.chromium.launch_persistent_context(
                self.user_data_dir,
                headless=False,
                args=['--start-maximized'],
                viewport=None,   # 配合 --start-maximized 使用真实窗口尺寸
            )
            self.page = (self.context.pages[0] if self.context.pages
                         else await self.context.new_page())

            # 访问通义千问网页
            await self.page.goto(self.url, timeout=60000, wait_until="domcontentloaded")
            print(f"✓ 已打开通义千问网页: {self.url}")

            # 等输入框就绪即视为加载完成（比 networkidle 稳定，不会卡死）
            await self.page.wait_for_selector(self.INPUT_SEL, timeout=30000)
            print("✓ 浏览器初始化成功")
            return True

        except Exception as e:
            print(f"✗ 浏览器初始化失败: {e}")
            # 失败时释放已启动的浏览器资源，避免后台残留 chrome 进程再次锁死 profile
            await self.close()
            return False

    async def get_response(self, chat_name, user_message, system_prompt=None, actor="user"):
        """
        获取AI回复

        Args:
            chat_name: 聊天窗口名称
            user_message: 用户消息
            system_prompt: 本次使用的提示词，为空时使用全局配置

        Returns:
            str: AI回复内容
        """
        if not self.page:
            print("[错误] 浏览器未初始化")
            return "抱歉，AI客户端未初始化。"

        try:
            # 构建完整的提示词（会话级提示词优先，否则用全局提示词）
            prompt = system_prompt or self.system_prompt
            # 说话方：assistant=我（男朋友）自己发的话，user=对方（女朋友）发的消息
            speaker = ("我（自己发出的信息，不是对方发的）"
                       if actor == "assistant"
                       else "对方（我聊天对象发来的消息）")
            # 只有切换会话时才会刷新页面，刷新后 qwen 页面的上下文被清空，
            # 需要把本地历史补进 prompt；同一会话内页面自己保留了上下文，
            # 再拼一次属于重复投喂，只会拉长 prompt 拖慢首 token 生成。
            switched = self._active_chat != chat_name
            history = self.chat_history.get(chat_name, [])
            if history and switched:
                ctx = "\n".join(
                    f"{'我' if m['role'] == 'assistant' else '对方（晓彤）'}：{m['content']}"
                    for m in history
                )
                full_prompt = (f"{prompt}\n\n最近的聊天记录：\n{ctx}"
                               f"\n\n{speaker}：{user_message}")
            else:
                full_prompt = f"{prompt}\n\n{speaker}：{user_message}"

            # 切换到其他会话时刷新网页，清空 qwen 页面残留的历史，
            # 防止 AI 把上一会话的内容当成当前会话的上下文（回复串扰）
            if switched:
                try:
                    await self.page.goto(self.url, timeout=60000,
                                         wait_until="domcontentloaded")
                    await self.page.wait_for_selector(self.INPUT_SEL, timeout=30000)
                    self._active_chat = chat_name
                except Exception as e:
                    print(f"[警告] 切换会话刷新失败: {e}")

            # 最多尝试 2 次：偶发的 qwen 卡顿/跳转可用重试兜住；
            # 对「。。。」这类 qwen 拒答的输入重试同样无果（会多等一轮），
            # 属可接受的代价。
            ai_reply = None
            for attempt in range(2):
                # 每次尝试前都重新取基准，避免上一次残留的回复数量干扰判定
                before = len(await self.page.query_selector_all(self.ASSISTANT_SEL))

                await self.page.click(self.INPUT_SEL)
                await self.page.fill(self.INPUT_SEL, full_prompt)
                await asyncio.sleep(0.3)

                try:
                    await self.page.click(self.SEND_SEL, timeout=3000)
                except Exception:
                    # 兜底：回车发送
                    await self.page.press(self.INPUT_SEL, "Enter")
                print("[AI输入] 已发送提问" if attempt == 0
                      else "[重试] 上次超时，重新发送提问")

                ai_reply = await self._wait_reply(before)
                if ai_reply:
                    break

            if not ai_reply:
                print("[错误] AI回复超时（已重试1次），本次不发送")
                return None

            # 更新对话历史
            history = self.chat_history.setdefault(chat_name, [])
            history.append({"role": actor, "content": user_message})
            history.append({"role": "assistant", "content": ai_reply})

            # 只保留最近10轮对话
            if len(history) > 20:
                self.chat_history[chat_name] = history[-20:]

            return ai_reply

        except Exception as e:
            print(f"[AI错误] {e}")
            return None

    async def _answer_text(self, message_handle):
        """从一条 assistant 消息中取出回复正文（过滤掉思考过程等噪声）"""
        el = await message_handle.query_selector(self.ANSWER_SEL)
        if el is None:
            el = await message_handle.query_selector(self.ANSWER_FALLBACK_SEL)
        if el is None:
            return ""
        try:
            return (await el.inner_text()).strip()
        except Exception:
            return ""

    async def _wait_reply(self, before_count):
        """
        等待新回复生成完毕

        采用「文本稳定」判定：连续两次读取结果一致即视为生成结束，
        避免依赖「停止生成」按钮一类易变的选择器。

        Args:
            before_count: 发送前已有的 assistant 消息数量

        Returns:
            str | None: 回复正文，超时或页面异常时返回 None
        """
        deadline = time.time() + self.wait_timeout
        last, stable = "", 0

        while time.time() < deadline:
            # 0.8 秒一次：判定需要连续两次读到相同文本，故固定开销约 1.6 秒。
            # 再往小调（如 0.6）会把「流式输出中途卡顿」误判成生成结束，
            # 导致只发出半截回复，不值得为省 0.4 秒冒这个险。
            await asyncio.sleep(0.8)

            try:
                msgs = await self.page.query_selector_all(self.ASSISTANT_SEL)
                if len(msgs) <= before_count:
                    continue                  # 新回复尚未出现
                text = await self._answer_text(msgs[-1])
            except Exception:
                # 首次发送后 qwen 会跳转到新会话地址，DOM 重建会让执行上下文失效，
                # 跳过本轮、等页面稳定后重试
                continue

            if text and text == last:
                stable += 1
                if stable >= 2:
                    print(f"[AI回复] 生成完毕，长度: {len(text)}")
                    return text
            else:
                stable, last = 0, text

        # 超时不返回半截内容，交由调用方跳过发送
        print("[错误] AI回复超时")
        return None

    async def close(self):
        """关闭浏览器"""
        try:
            if self.context:
                await self.context.close()
            if self.playwright:
                await self.playwright.stop()
            print("✓ 浏览器已关闭")
        except Exception as e:
            print(f"[警告] 关闭浏览器时出错: {e}")

    def add_history(self, chat_name, role, content):
        """
        向指定会话注入一条对话历史（供后续做上下文，不触发回复）

        Args:
            chat_name: 聊天窗口名称
            role: "user"（对方）或 "assistant"（我）
            content: 消息内容
        """
        history = self.chat_history.setdefault(chat_name, [])
        history.append({"role": role, "content": content})
        if len(history) > 20:
            self.chat_history[chat_name] = history[-20:]

    def clear_history(self, chat_name=None):
        """
        清除对话历史

        Args:
            chat_name: 聊天窗口名称，None表示清除所有
        """
        if chat_name:
            if chat_name in self.chat_history:
                self.chat_history[chat_name] = []
                print(f"已清除 {chat_name} 的对话历史")
        else:
            self.chat_history.clear()
            print("已清除所有对话历史")