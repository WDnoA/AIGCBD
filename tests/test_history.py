# -*- coding: utf-8 -*-
"""
历史持久化与交班测试（不依赖模型、微信、网络）

覆盖：
  - history_store 的纯函数：文件名清洗、规则式压缩、摘要合并、交班切分
  - 落盘 / 载回 / 损坏文件跳过 / 清除
  - AIClient 的交班流程：条数与字符双阈值、模型摘要、规则式兜底
  - 重启恢复：新实例读同一目录能拿回历史与摘要
  - 摘要并进 system prompt（且只有一条 system）
"""
import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ai_client import AIClient
from history_store import (HistoryStore, safe_name, compress_rule_based,
                           merge_summary, should_handoff, split_for_handoff)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


print("=== 纯函数 ===")
check("safe_name 保留中文", safe_name("择天").startswith("择天_"))
check("safe_name 去非法字符", "/" not in safe_name("a/b"))
check("safe_name 不同名不碰撞", safe_name("择天") != safe_name("择天 "))
check("空名不炸", len(safe_name("")) > 0)

msgs = [
    {"role": "assistant", "content": "在吗"},
    {"role": "user", "content": "在的，怎么了"},
]
comp = compress_rule_based(msgs)
check("规则式含角色", "我：在吗" in comp and "对方：在的" in comp)

long = [{"role": "user", "content": "啊" * 200}]
check("规则式截断", len(compress_rule_based(long)) < 80)

check("摘要合并", merge_summary("旧", "新") == "旧\n新")
check("摘要空值安全", merge_summary("", "") == "")
huge = merge_summary("a" * 2000, "b" * 100)
check("摘要超限截断", len(huge) < 1300 and huge.startswith("（更早"))

check("未超阈不交班", not should_handoff(60, 3000, 60, 3000))
check("条数超阈交班", should_handoff(61, 100, 60, 3000))
check("字符超阈交班", should_handoff(10, 3001, 60, 3000))
check("日常长度不交班", not should_handoff(14, 300, 60, 3000))
old, recent = split_for_handoff([{"role": "user", "content": str(i)} for i in range(30)], 12)
check("分割 18+12", len(old) == 18 and len(recent) == 12)
check("分割后 recent 是尾部", recent[-1]["content"] == "29")
old2, recent2 = split_for_handoff([{"role": "user", "content": "x"}] * 5, 12)
check("消息不足全保留", old2 == [] and len(recent2) == 5)

# 字符预算：每条 10 字，预算 35 -> 只能保留 3 条
many = [{"role": "user", "content": "0123456789"} for _ in range(10)]
o3, r3 = split_for_handoff(many, 10, 35)
check("字符预算生效", len(r3) == 3 and len(o3) == 7)


print("\n=== 持久化往返 ===")
tmp = tempfile.mkdtemp(prefix="aigcbd_hist_")
try:
    store = HistoryStore(tmp)
    store.save("择天", "这是摘要", [{"role": "user", "content": "你好"}])
    store.archive("择天", "user", "你好")
    loaded = store.load_all()
    check("载回会话", "择天" in loaded)
    check("载回摘要", loaded["择天"]["summary"] == "这是摘要")
    check("载回消息", len(loaded["择天"]["messages"]) == 1)
    check("归档文件存在", os.path.exists(os.path.join(tmp, "_archive.jsonl")))

    # 损坏文件不该拖垮启动
    with open(os.path.join(tmp, "broken.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    loaded2 = store.load_all()
    check("损坏文件被跳过", "择天" in loaded2)

    store.clear("择天")
    check("清除后不再载回", "择天" not in store.load_all())
finally:
    shutil.rmtree(tmp, ignore_errors=True)


print("\n=== 交班流程（真实 AIClient + 假摘要）===")
tmp2 = tempfile.mkdtemp(prefix="aigcbd_ai_")
try:
    ai = AIClient({
        "provider": "openai",
        "base_url": "http://127.0.0.1:9/v1",
        "model": "local",
        "history": {"dir": tmp2, "max_messages": 24, "keep_recent": 12,
                    "use_model_summary": True},
    })

    async def fake_summarize(chat, old):
        return f"摘要：压了{len(old)}条"

    ai._summarize = fake_summarize

    for i in range(30):
        ai.add_history("择天", "assistant" if i % 2 == 0 else "user", f"第{i}句")

    print(f"  交班前: {len(ai.chat_history['择天'])} 条")
    asyncio.run(ai._maybe_handoff("择天"))
    after = len(ai.chat_history["择天"])
    print(f"  交班后: {after} 条，摘要 = {ai._summaries.get('择天')!r}")
    check("交班后剩 keep_recent 条", after == 12)
    check("摘要已生成", "摘要：压了18条" in (ai._summaries.get("择天") or ""))
    check("最新消息保留", ai.chat_history["择天"][-1]["content"] == "第29句")

    # 落盘应同步缩减
    disk = ai.store.load_all()
    check("落盘消息数同步", len(disk["择天"]["messages"]) == 12)
    check("落盘摘要同步", "压了18条" in disk["择天"]["summary"])

    # 重启模拟：新实例读同一目录应恢复
    ai2 = AIClient({
        "provider": "openai", "base_url": "http://127.0.0.1:9/v1",
        "history": {"dir": tmp2, "max_messages": 24, "keep_recent": 12},
    })
    check("重启后历史恢复", len(ai2.chat_history.get("择天", [])) == 12)
    check("重启后摘要恢复", "压了18条" in (ai2._summaries.get("择天") or ""))

    # 模型摘要失败 -> 退回规则式
    ai3 = AIClient({
        "provider": "openai", "base_url": "http://127.0.0.1:9/v1",
        "history": {"dir": tmp2, "max_messages": 10, "keep_recent": 4},
    })

    async def broken(chat, old):
        return None

    ai3._summarize = broken
    for i in range(20):
        ai3.add_history("魏晓彤", "user", f"内容{i}")
    asyncio.run(ai3._maybe_handoff("魏晓彤"))
    check("摘要失败仍能交班", len(ai3.chat_history["魏晓彤"]) == 4)
    check("退回规则式压缩", "内容" in (ai3._summaries.get("魏晓彤") or ""))

    # 上下文构建：摘要进 system
    print("\n  构建上下文检查:")
    ai4 = AIClient({
        "provider": "openai", "base_url": "http://127.0.0.1:9/v1",
        "history": {"dir": tmp2},
    })
    ai4._summaries["择天"] = "早期提要XYZ"
    ai4.add_history("择天", "user", "新消息")
    captured = {}

    def fake_http(url, payload, method="GET"):
        captured["payload"] = payload
        raise RuntimeError("stop")

    ai4._http_json = fake_http
    try:
        asyncio.run(ai4.get_response("择天", "测试", system_prompt="提示词"))
    except Exception:
        pass
    p = captured.get("payload") or {}
    sysmsg = (p.get("messages") or [{}])[0].get("content", "")
    check("摘要并进 system", "早期提要XYZ" in sysmsg)
    check("system 只有一条", sum(1 for m in p.get("messages", [])
                                  if m.get("role") == "system") == 1)
    check("新消息带来源标记", any("测试" in str(m.get("content"))
                                  for m in p.get("messages", [])))
finally:
    shutil.rmtree(tmp2, ignore_errors=True)

print(f"\n===== {PASS} 通过, {FAIL} 失败 =====")
sys.exit(1 if FAIL else 0)
