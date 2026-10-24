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

from reliability import resolve_chat_id, collect_candidates, CircuitBreaker
from ai_client import clean_reply, is_ignore_reply
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


test_resolve()
test_breaker()
test_clean()
test_ignore()
test_nonconv()
test_mentioned()

print(f"\n===== {PASS} 通过, {FAIL} 失败 =====")
sys.exit(1 if FAIL else 0)
