# -*- coding: utf-8 -*-
"""诊断：这几条消息究竟是谁发的"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import yaml
from wechat_client import WeChatClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cfg = yaml.safe_load(open(os.path.join(ROOT, "config", "config.yaml"), encoding="utf-8"))

w = WeChatClient(cfg["wechat"])
names = [r["chat_name"] for r in cfg["chat_rules"]]
assert w.load_db(names), "数据库不可用"
self_id = w._self_id
print(f"self_id = {self_id}")

fh = w.db.get_messages("filehelper", limit=5)
print("\n=== 文件传输助手（一定是自己发的）===")
for m in fh:
    print(f"  sender_id={m.get('sender_id')}  {(m.get('content') or '')[:30]!r}")

for name, uname in w._chat_ids.items():
    print(f"\n=== {name} ({uname}) 最近 12 条 ===")
    for m in w.db.get_messages(uname, limit=12):
        sid = m.get("sender_id")
        tag = "★我自己" if sid == self_id else "  对方"
        print(f"  {tag} sid={sid}  {(m.get('content') or '')[:36]!r}")
