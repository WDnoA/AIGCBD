# -*- coding: utf-8 -*-
"""
消息处理模块
负责处理消息逻辑
"""

from ai_client import IGNORE_PLACEHOLDER


class MessageHandler:
    """消息处理器"""

    def __init__(self, wechat_client, ai_client, chat_rules):
        """
        初始化消息处理器

        Args:
            wechat_client: 微信客户端实例
            ai_client: AI客户端实例
            chat_rules: 聊天规则列表
        """
        self.wechat = wechat_client
        self.ai = ai_client
        self.chat_rules = chat_rules

    async def handle_message(self, chat_name, content, is_self=False):
        """
        处理一条消息：匹配规则 -> 调AI -> 回复/转发

        Args:
            chat_name: 聊天名称
            content: 消息内容
            is_self: True 表示是「我自己手发的消息」
        """
        rule = self._find_chat_rule(chat_name)

        if not rule:
            print(f"[跳过] {chat_name} 未配置规则")
            return

        if is_self and rule.get("_mode", "third") != "third":
            # opponent（只回答聊天对象）模式：自己发的消息只记入上下文，不触发回复
            self.ai.add_history(chat_name, "assistant", content)
            return

        try:
            print(f"\n[处理] {chat_name}: {content}")

            # 调用AI获取回复（会话级提示词优先；自消息用 third 的双方人设）
            ai_reply = await self.ai.get_response(
                chat_name, content, system_prompt=rule.get("prompt"),
                actor="assistant" if is_self else "user"
            )
            # 自消息在第三方模式下由 AI 判断：返回 [忽略] 等占位则不外发
            if is_self and (not ai_reply or ai_reply.strip() == IGNORE_PLACEHOLDER):
                print("[跳过] AI 判断我的消息无需回复")
                return
            if not ai_reply:
                print("[跳过] AI 未返回有效回复，本次不发送")
                return
            print(f"[AI回复] {ai_reply}")

            # 格式化回复
            reply_template = rule.get('reply_template', '{reply}')
            formatted_reply = reply_template.format(reply=ai_reply)

            # 发送回复
            self.wechat.send_message(chat_name, formatted_reply)

            # 转发到其他聊天（仅对方消息触发时转发）
            forward_to = rule.get('forward_to', []) if not is_self else []
            if forward_to:
                for target in forward_to:
                    forward_msg = f"[来自 {chat_name} 的消息]\n{content}\n\n[AI回复]\n{ai_reply}"
                    self.wechat.send_message(target, forward_msg)
                    print(f"[已转发] 消息已转发到 {target}")

        except Exception as e:
            print(f"[错误] 处理消息失败: {e}")
            import traceback
            traceback.print_exc()

    def _find_chat_rule(self, chat_name):
        """查找匹配的聊天规则"""
        for rule in self.chat_rules:
            if rule['chat_name'] == chat_name:
                return rule
        return None

    def clear_history(self, chat_name=None):
        """
        清除对话历史

        Args:
            chat_name: 聊天窗口名称，None表示清除所有
        """
        self.ai.clear_history(chat_name)
