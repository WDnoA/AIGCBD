# -*- coding: utf-8 -*-
"""
微信AI自动回复系统 - 主程序
"""
import time
import sys
import os
import asyncio

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_loader import load_config, validate_config
from ai_client import AIClient
from wechat_client import WeChatClient
from message_handler import MessageHandler
from identity_picker import choose_identities


class _Tee:
    """
    把输出同时写到终端与日志文件

    Playwright 启动的 Chromium 会继承 stdout 句柄，输出被重定向到文件时
    print 的内容会丢失；用 Tee 绕开该句柄，保证后台运行时日志不丢。
    """

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            try:
                stream.write(data)
                stream.flush()
            except Exception:
                pass
        return len(data)

    def flush(self):
        for stream in self.streams:
            try:
                stream.flush()
            except Exception:
                pass

    def isatty(self):
        return False


def setup_log_file(log_path):
    """启用双路输出：终端 + 日志文件"""
    if not log_path:
        return
    try:
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    except Exception as e:
        print(f"[警告] 无法打开日志文件 {log_path}: {e}")
        return
    sys.stdout = _Tee(sys.__stdout__, log_file)
    sys.stderr = _Tee(sys.__stderr__, log_file)


async def main():
    """主函数"""
    print("=" * 60)
    print("微信AI自动回复系统")
    print("=" * 60)

    # 加载配置
    try:
        config = load_config("config/config.yaml")
        print("✓ 配置文件加载成功")
    except Exception as e:
        print(f"✗ 加载配置文件失败: {e}")
        return

    # 启用双路输出（终端 + 日志文件）
    setup_log_file(config.get("settings", {}).get("log_file"))

    # 验证配置
    if not validate_config(config):
        print("\n请检查配置文件 config/config.yaml")
        return

    # 初始化组件
    ai_client = None    # 预置，便于初始化异常时释放已启动的浏览器
    try:
        # 初始化AI客户端（provider=openai 走本地接口；provider=web 走网页自动化）
        ai_client = AIClient(config["ai"])
        if ai_client.provider == "openai":
            print(f"✓ AI客户端初始化成功 (接口: {ai_client.base_url}  模型: {ai_client.model})")
            print("正在连接模型服务...")
        else:
            print(f"✓ AI客户端初始化成功 (网页: {config['ai'].get('url', '')})")
            print("正在启动浏览器...")
        if not await ai_client.init_browser():
            print("✗ AI 后端初始化失败")
            return

        # 聊天规则
        chat_rules = config.get("chat_rules", [])
        if not chat_rules:
            print("\n警告: 没有配置任何聊天规则")
            await ai_client.close()
            return
        chat_names = [rule['chat_name'] for rule in chat_rules]

        # 启动时用一个窗口为所有配置了双模式提示词的对象选身份模式，
        # 选好后把对应提示词写回 rule["prompt"]，handler/AI 无需感知模式
        dual_rules = [r for r in chat_rules
                      if r.get("prompt_third") and r.get("prompt_opponent")]
        if dual_rules:
            modes = choose_identities([r["chat_name"] for r in dual_rules])
            for rule in dual_rules:
                mode = modes.get(rule["chat_name"], "third")
                rule["prompt"] = (rule["prompt_third"] if mode == "third"
                                  else rule["prompt_opponent"])
                rule["_mode"] = mode   # 记下身份模式，供运行时区分自消息处理
                print(f"✓ 「{rule['chat_name']}」身份模式: "
                      f"{'第三方' if mode == 'third' else '只回答聊天对象'}")

        # 初始化微信客户端（数据库读取 + 坐标发送）
        wechat_client = WeChatClient(config.get("wechat", {}))
        print("✓ 微信客户端初始化成功")

        # 读取微信数据库并解析会话
        if not wechat_client.load_db(chat_names):
            print("\n✗ 微信数据库初始化失败")
            await ai_client.close()
            return

        # 初始化消息处理器
        message_handler = MessageHandler(
            wechat_client,
            ai_client,
            chat_rules
        )
        print("✓ 消息处理器初始化成功")

    except Exception as e:
        print(f"✗ 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        # 初始化中途失败也要释放浏览器：残留 chrome 会锁住 browser_profile，
        # 导致下次启动报「Target page, context or browser has been closed」
        if ai_client is not None:
            try:
                await ai_client.close()
            except Exception:
                pass
        return

    # 初始化发送所需的微信窗口（失败不影响消息监听）
    if not wechat_client.load_wechat():
        print("\n[警告] 未找到微信窗口，自动回复将无法发送")

    # 开始监听
    wechat_client.start_listening()

    print("\n" + "=" * 60)
    print("系统运行中，已开始监听新消息")
    print("按 Ctrl+C 停止系统")
    print("=" * 60 + "\n")

    # 主循环：阻塞等待监听到的新消息
    try:
        while True:
            item = await asyncio.to_thread(wechat_client.wait_message, 1.0)
            if item is None:
                continue
            chat_name, content, is_self = item
            await message_handler.handle_message(chat_name, content, is_self)

    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n\n收到停止信号，正在退出...")
    except Exception as e:
        print(f"\n[错误] {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("[退出] 正在停止消息监听...")
        try:
            wechat_client.stop()
        except Exception as e:
            print(f"[警告] 停止监听出错: {e}")
        print("[退出] 正在关闭浏览器...")
        try:
            await ai_client.close()
        except Exception as e:
            print(f"[警告] 关闭浏览器出错: {e}")
        print("[退出] 已安全退出，可以关闭窗口")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
