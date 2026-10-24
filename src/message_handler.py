# -*- coding: utf-8 -*-
"""
消息处理模块
负责处理消息逻辑
"""

from ai_client import IGNORE_PLACEHOLDER, clean_reply, is_ignore_reply
from reliability import is_mentioned
import re
import time

# 非对话内容：微信里表情包、图片、名片、转账、系统提示等，文本字段常常是
# 「[动画表情]」「[图片]」这种方括号占位，或者 <msg><img .../></msg> 这类 XML。
# 之前完全靠提示词让模型自己判断「遇到这些要输出[忽略]」，但 9B 模型不稳定：
# 实测同一句「[动画表情]」，换成「择天」的提示词后就变成「择天，心情不错吧」
# 直接发出去了。这类内容根本不该拿去推理——过滤掉省一次推理，也不会误发。
NON_TEXT_RE = re.compile(
    r"^(?:\s|\[[^\]]{1,16}\]|<[^>]+>|\[CDATA\[.*\]\]|[\U0001F300-\U0001FAFF\u2600-\u27BF])*$",
    re.S,
)


def is_non_conversational(text):
    """判断内容是不是纯占位/纯 XML/纯 emoji（没有可回复的文字）"""
    body = (text or "").strip()
    if not body:
        return True
    return bool(NON_TEXT_RE.match(body))


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

        # 纯表情/图片/系统提示：没有可回复的文字，不推理也不进上下文
        if is_non_conversational(content):
            print(f"[跳过] {chat_name}: 非文本内容（{content[:20]!r}），无需回复")
            return

        if is_self and rule.get("_mode", "third") != "third":
            # opponent（只回答聊天对象）模式：自己发的消息只记入上下文，不触发回复
            self.ai.add_history(chat_name, "assistant", content)
            return

        try:
            who = "我" if is_self else "对方"
            print(f"\n[处理] {chat_name}: {content}   (来自{who})")
            t0 = time.time()

            # 会话级提示词优先；自消息用 third 的双方人设
            prompt = rule.get("prompt")

            # 自己发的消息怎么处理（默认 dialogue）：
            #   dialogue 对方最近没说话 → 你就是在跟 AI 单聊，直接回；
            #            对方在场 → 只记上下文，绝不插嘴。两个场景自动切换，
            #            不用来回改配置（这是默认档）
            #   mention 只有点名叫了助手才回，其余只记上下文
            #   never   完全不回，只记上下文，连关键词都不看
            #   always  强制回复（用于「让它接我的话」的演示场景）
            #   auto    交给 AI 判断（9B 极不稳定，慎用）
            if is_self:
                policy = (rule.get("self_reply") or "dialogue").strip().lower()
                if policy == "never":
                    self.ai.add_history(chat_name, "assistant", content)
                    print("[跳过] self_reply=never：自己发的消息只记上下文，不触发回复")
                    return
                if policy == "dialogue" and not self._alone_with_ai(chat_name, rule):
                    self.ai.add_history(chat_name, "assistant", content)
                    print(f"[跳过] 对方在场，不插话（{chat_name} 最近有对方消息）；"
                          f"想让它回，请点名「{self._mention_hint(rule)}」")
                    return
                if policy in ("mention", "auto"):
                    keywords = rule.get("mention_keywords")
                    if policy == "mention" and not is_mentioned(content, keywords):
                        self.ai.add_history(chat_name, "assistant", content)
                        print("[跳过] 自己发的消息未点名助手，只记上下文")
                        return
                if policy == "always" and prompt:
                    prompt = (prompt.rstrip()
                              + "\n\n【本轮要求】这条是「我」自己刚发出的消息，"
                                "你必须给出回复，禁止输出「[忽略]」或任何占位内容。")

            # 第三方模式下「老板」是贾维斯对主人的专属称呼，回对方的话里
            # 混进这个词，对方只会一头雾水。命中就重新生成一次。
            forbid = (["老板"]
                      if (not is_self and rule.get("_mode", "third") == "third")
                      else None)

            ai_reply = await self.ai.get_response(
                chat_name, content, system_prompt=prompt,
                actor="assistant" if is_self else "user",
                forbid_words=forbid
            )
            cost = time.time() - t0

            if not ai_reply or not ai_reply.strip():
                print(f"[跳过] AI 未返回有效回复（{cost:.1f}s），本次不发送")
                return
            # 占位符判定要对「对方消息」也生效：否则模型返回 [忽略] 时
            # 会把这两个字当成正文真的发给对方（原实现只在 is_self 时检查）
            if is_ignore_reply(ai_reply):
                print(f"[跳过] AI 判定无需回复（{cost:.1f}s，原文 {ai_reply!r}）")
                return

            ai_reply = clean_reply(ai_reply)
            print(f"[AI回复] {ai_reply}   ({cost:.1f}s)")

            # 格式化回复
            reply_template = rule.get('reply_template', '{reply}')
            formatted_reply = reply_template.format(reply=ai_reply)

            # 回复发到哪：
            #   · 默认（wechat.self_chat 留空）→ 一律回原会话。
            #     第三方模式下贾维斯是这场聊天里独立存在的一员，它说的话
            #     本来就该出现在聊天里、双方都能看到，这才是「第三方」。
            #   · 显式配了 self_chat → 自己发消息触发的回复改发到那里，
            #     只给自己看（私人参谋用法）。对方消息的回复不受影响。
            target = chat_name
            if is_self:
                target = ((getattr(self.wechat, "self_chat", "") or "").strip()
                          or chat_name)

            ok = self.wechat.send_message(
                target, formatted_reply,
                add_prefix=rule.get("_mode", "third") == "third")
            if not ok:
                print(f"[告警] 「{target}」回复发送失败，本条已丢弃："
                      f"{ai_reply[:40]}")

            # 转发到其他聊天（仅对方消息触发时转发）
            forward_to = rule.get('forward_to', []) if not is_self else []
            if forward_to:
                for target in forward_to:
                    forward_msg = f"[来自 {chat_name} 的消息]\n{content}\n\n[AI回复]\n{ai_reply}"
                    fwd_ok = self.wechat.send_message(target, forward_msg)
                    print(f"[{'已转发' if fwd_ok else '转发失败'}] 消息{'已转发到' if fwd_ok else '未能转发到'} {target}")

        except Exception as e:
            print(f"[错误] 处理消息失败: {e}")
            import traceback
            traceback.print_exc()

    def _alone_with_ai(self, chat_name, rule):
        """
        判断「老板现在是在跟 AI 单聊」还是「在和真人聊天」

        依据只有一条：最近 N 条消息里有没有对方发来的（历史里 role=user）。
          · 没有 → 对方根本没参与这轮对话，老板发的消息就是给 AI 的
          · 有   → 真人正在说话，AI 插一句就是抢话

        为什么不用「AI 是否回过话」来判：历史里老板手发的和 AI 回的
        都记成 assistant，区分不了；而「对方在不在场」这个信号本身
        就足够可靠——真人不可能连续 12 条消息不吭声。
        """
        try:
            window = int(rule.get("dialogue_window") or 12)
        except (TypeError, ValueError):
            window = 12
        recent = (self.ai.chat_history.get(chat_name) or [])[-window:]
        return not any(m.get("role") == "user" for m in recent)

    @staticmethod
    def _mention_hint(rule):
        """提示语里用哪个召唤词（取配置的第一个关键词）"""
        words = rule.get("mention_keywords") or []
        if isinstance(words, (list, tuple)):
            for w in words:
                if w:
                    return w
        elif isinstance(words, str) and words.strip():
            return words.strip()
        return "贾维斯"

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
