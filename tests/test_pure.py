# -*- coding: utf-8 -*-
"""
纯函数单元测试（不依赖模型、微信、网络）

覆盖：
  - reliability.resolve_chat_id   精确匹配 / 多命中拒绝
  - reliability.CircuitBreaker    熔断开关与冷却
  - ai_client.clean_reply          来源标记剥除、引号剥除
  - ai_client.is_ignore_reply      占位符变体判定
  - message_handler.is_non_conversational  非文本过滤
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from reliability import (resolve_chat_id, collect_candidates, CircuitBreaker,
                         compose_outgoing)
from ai_client import (clean_reply, is_ignore_reply, is_repeated_reply,
                       normalize_reply)
from message_handler import is_non_conversational

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ FAIL: {name}")


# --- resolve_chat_id ---
def test_resolve():
    print("resolve_chat_id")
    # 精确匹配备注优先
    hits = [{"remark": "魏晓彤", "nick_name": "月辞.", "username": "wxid_a"}]
    check("备注精确命中", resolve_chat_id("魏晓彤", hits) == "wxid_a")
    # 精确匹配昵称（无备注命中）
    hits = [{"remark": "", "nick_name": "魏晓彤", "username": "wxid_b"}]
    check("昵称精确命中", resolve_chat_id("魏晓彤", hits) == "wxid_b")
    # 唯一模糊命中 → 采用
    hits = [{"remark": "月辞.", "nick_name": "小魏", "username": "wxid_c"}]
    check("唯一命中采用", resolve_chat_id("魏", hits) == "wxid_c")
    # 多个模糊命中 → 拒绝（返回 None）
    hits = [
        {"remark": "魏晓彤", "nick_name": "月辞.", "username": "wxid_d"},
        {"remark": "", "nick_name": "小魏同学", "username": "wxid_e"},
    ]
    check("多命中拒绝(不精确)", resolve_chat_id("魏", hits) is None)
    # 空结果
    check("空结果", resolve_chat_id("魏", []) is None)


# --- CircuitBreaker ---
def test_breaker():
    print("CircuitBreaker")
    cb = CircuitBreaker(fail_threshold=3, cooldown=30.0)
    check("初始关闭", not cb.is_open)
    cb.record_failure()
    cb.record_failure()
    check("两次失败仍关闭", not cb.is_open)
    cb.record_failure()
    check("三次失败打开", cb.is_open)
    # 熔断中 allow() 返回 False（短路），且不提前结束冷却
    check("熔断中短路", cb.allow() is False)
    check("短路后仍开路", cb.is_open)
    # 成功复位
    cb.record_success()
    check("成功复位", not cb.is_open)


# --- clean_reply ---
def test_clean():
    print("clean_reply")
    check("剥【我】", clean_reply("【我】老板，您去把奶茶换了") == "老板，您去把奶茶换了")
    check("剥【对方】", clean_reply("【对方】宝宝，想我啦？") == "宝宝，想我啦？")
    check("剥[我]", clean_reply("[我]老板，我在") == "老板，我在")
    check("剥「我：」", clean_reply("我：老板，在呢") == "老板，在呢")
    check("剥「贾维斯：」", clean_reply("贾维斯：宝宝乖") == "宝宝乖")
    check("剥全角引号", clean_reply("「好呀」") == "好呀")
    check("剥双引号", clean_reply('"你好"') == "你好")
    check("叠层剥除", clean_reply("【我】「老板，在」") == "老板，在")
    check("正常文本不动", clean_reply("宝宝，我想你了") == "宝宝，我想你了")
    # 多行必须压成一行。放开长度后模型会输出两行，微信是整段粘贴再回车，
    # 多行在输入框里会留成多行，观感像公告。
    check("行尾有标点直连",
          clean_reply("在呢。\n咋样了？") == "在呢。咋样了？")
    check("行尾无标点补逗号",
          clean_reply("我在\n咋了") == "我在，咋了")
    check("空行丢弃",
          clean_reply("好\n\n\n嗯") == "好，嗯")
    check("标记与换行同时出现",
          clean_reply("【我】宝宝，我在呢\n想吃啥") == "宝宝，我在呢，想吃啥")


# --- is_ignore_reply ---
def test_ignore():
    print("is_ignore_reply")
    check("[忽略]", is_ignore_reply("[忽略]"))
    check("忽略", is_ignore_reply("忽略"))
    check("【忽略】", is_ignore_reply("【忽略】"))
    check("(忽略)", is_ignore_reply("(忽略)"))
    check("[忽略]。", is_ignore_reply("[忽略]。"))
    check("空串", is_ignore_reply(""))
    check("None", is_ignore_reply(None))
    check("正常内容不判忽略", not is_ignore_reply("宝宝，在吗"))


# --- is_non_conversational ---
def test_nonconv():
    print("is_non_conversational")
    check("[动画表情]", is_non_conversational("[动画表情]"))
    check("[图片]", is_non_conversational("[图片]"))
    check("[位置]", is_non_conversational("[位置]"))
    check("纯emoji", is_non_conversational("😀😀"))
    check("XML", is_non_conversational("<msg><img aeskey='x'/></msg>"))
    check("空", is_non_conversational(""))
    check("空白", is_non_conversational("   "))
    check("正常文本", not is_non_conversational("哈喽"))
    check("文本+表情(可回)", not is_non_conversational("你好[动画表情]"))


# --- is_mentioned ---
def test_mentioned():
    print("is_mentioned")
    from reliability import is_mentioned
    check("显式点名", is_mentioned("贾维斯，你觉得呢"))
    check("句尾点名", is_mentioned("这个怎么办，贾维斯"))
    check("简称命中", is_mentioned("小贾帮我看下"))
    check("@点名", is_mentioned("@贾维斯 在吗"))
    check("普通聊天不命中", not is_mentioned("你对话长度多少"))
    check("普通聊天2", not is_mentioned("能不能长一点"))
    check("空文本", not is_mentioned(""))
    check("None", not is_mentioned(None))
    # 自定义触发词：改配置后旧默认词不该再生效
    check("自定义词命中", is_mentioned("小助手帮我", ["小助手"]))
    check("自定义词替换默认", not is_mentioned("贾维斯在吗", ["小助手"]))


def test_repeated():
    print("\n[防复读] is_repeated_reply / normalize_reply")

    hist = [
        {"role": "user", "content": "今天好累啊"},
        {"role": "assistant", "content": "宝宝，累了就歇会儿，我陪你。"},
        {"role": "user", "content": "算了，不说了"},
    ]
    # 同一句只差标点 —— 必须判成复读（模型换个句号就想躲过，得归一化）
    check("原句命中", is_repeated_reply("宝宝，累了就歇会儿，我陪你。", hist))
    check("换标点命中", is_repeated_reply("宝宝 累了就歇会儿我陪你", hist))
    check("换句尾命中", is_repeated_reply("宝宝，累了就歇会儿，我陪你！", hist))
    # 内容不同就不该判复读，否则正常回复也被拦
    check("不同句不命中", not is_repeated_reply("谁又惹你了", hist))
    check("短句不误伤", not is_repeated_reply("嗯", hist))
    check("空历史", not is_repeated_reply("在吗", []))
    check("空回复", not is_repeated_reply("", hist))
    # 只跟自己说过的话比：对方说过的同一句不算复读
    check("不比对对方的话", not is_repeated_reply("今天好累啊", hist))
    # window 之外的老话不管（避免隔了很久还被判复读）
    old = [{"role": "assistant", "content": "老句子"}]
    old += [{"role": "assistant", "content": f"第{i}句"} for i in range(10)]
    check("窗口外不命中", not is_repeated_reply("老句子", old, window=8))
    check("窗口内命中", is_repeated_reply("第9句", old, window=8))

    check("归一化去标点", normalize_reply("嗯……好，吧！") == "嗯好吧")
    check("归一化保留文字", normalize_reply("宝宝 我在") == "宝宝我在")


def test_dialogue():
    print("\n[单聊判定] MessageHandler._alone_with_ai")
    from message_handler import MessageHandler

    class _FakeAI:
        def __init__(self, history):
            self.chat_history = history

    def build(history, **rule):
        h = MessageHandler.__new__(MessageHandler)   # 绕开 __init__，只要能判定
        h.ai = _FakeAI(history)
        return h, rule

    # 历史里 role=user 就是对方发的；assistant 是老板手发或 AI 回复
    h, r = build({})
    check("空历史视为单聊", h._alone_with_ai("x", r) is True)

    h, r = build({"x": [{"role": "assistant", "content": "哈喽"}]})
    check("只有自己发的 = 单聊", h._alone_with_ai("x", r) is True)

    h, r = build({"x": [{"role": "assistant", "content": "哈喽"},
                        {"role": "user", "content": "在呢"}]})
    check("对方说过话 = 不插嘴", h._alone_with_ai("x", r) is False)

    # 对方消息在窗口之外，说明早就退出这场对话了
    old = [{"role": "user", "content": "早先那句"}]
    old += [{"role": "assistant", "content": f"第{i}句"} for i in range(12)]
    h, r = build({"x": old})
    check("对方消息在窗口外 = 单聊", h._alone_with_ai("x", r) is True)

    h, r = build({"x": old}, dialogue_window=20)
    check("窗口放大后能看到对方", h._alone_with_ai("x", r) is False)

    h, r = build({"x": old}, dialogue_window="not-a-number")
    check("窗口值非法时退回 12", h._alone_with_ai("x", r) is True)

    check("提示词取第一个关键词",
          MessageHandler._mention_hint(
              {"mention_keywords": ["贾维斯", "维斯"]}) == "贾维斯")
    check("关键词为空时用默认",
          MessageHandler._mention_hint({"mention_keywords": []}) == "贾维斯")
    check("关键词是字符串也能用",
          MessageHandler._mention_hint({"mention_keywords": "小贾"}) == "小贾")


def test_routing():
    print("\n[发送分流] 默认回原会话，配了 self_chat 才改道")
    import asyncio
    from message_handler import MessageHandler

    class _FakeAI:
        def __init__(self):
            self.chat_history = {}
        def add_history(self, *a, **k):
            pass
        async def get_response(self, *a, **k):
            return "好的老板"
        def clear_history(self, *a, **k):
            pass

    class _FakeWX:
        def __init__(self, self_chat=""):
            self.self_chat = self_chat
            self.calls = []
        def send_message(self, name, text, add_prefix=True):
            self.calls.append(name)
            return True

    def rule(**over):
        r = {"chat_name": "T", "_mode": "third", "self_reply": "dialogue",
             "prompt": "x"}
        r.update(over)
        return r

    def run(is_self, wx, msg="哈喽"):
        h = MessageHandler(wx, _FakeAI(), [rule()])
        asyncio.run(h.handle_message("T", msg, is_self=is_self))
        return wx.calls

    # 默认留空：第三方模式下它的话就该出现在这个聊天里
    calls = run(True, _FakeWX())
    check("默认自消息回复回原会话", calls == ["T"])

    calls = run(False, _FakeWX())
    check("对方消息回复回原会话", calls == ["T"])

    # 显式配置才改道（私人参谋用法）
    calls = run(True, _FakeWX(self_chat="文件传输助手"))
    check("配了 self_chat 则改道", calls == ["文件传输助手"])

    # 对方消息不受 self_chat 影响
    calls = run(False, _FakeWX(self_chat="文件传输助手"))
    check("对方消息不受 self_chat 影响", calls == ["T"])


def test_outgoing():
    print("\n[发送前缀] compose_outgoing")
    # 默认 show_prefix=False：对方看到的是纯人话
    check("默认不加前缀", compose_outgoing("在呢", True, False) == "在呢")
    check("显式开启才加", compose_outgoing("在呢", True, True) == "贾维斯：在呢")
    # opponent 模式调用方本来就不让加，show_prefix 开着也不该加
    check("调用方禁止则不加", compose_outgoing("在呢", False, True) == "在呢")
    check("自定义前缀", compose_outgoing("在", True, True, "[AI]") == "[AI]在")
    check("空内容不加", compose_outgoing("", True, True) == "")


test_resolve()
test_breaker()
test_clean()
test_ignore()
test_nonconv()
test_mentioned()
test_repeated()
test_outgoing()
test_dialogue()
test_routing()

print(f"\n===== {PASS} 通过, {FAIL} 失败 =====")
sys.exit(1 if FAIL else 0)
