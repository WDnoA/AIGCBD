# -*- coding: utf-8 -*-
"""
对话历史持久化与交班

解决两个问题：
  1. 重启失忆     —— 历史落在 data/history/*.json，启动时自动载回
  2. 长对话丢细节 —— 超过阈值时把最老的若干条压成「交接摘要」，
                     摘要随会话累积，近期消息原样保留

参考 DeepSeek Harness 的 handoff 思路（上下文用到某个比例就交班、
交班期间新消息排队补投），但做了两处本地化：
  · 触发条件用「消息条数」而不是 token 比例 —— 本地拿不到可靠的 token 计数
  · 摘要允许规则式兜底 —— 模型调用失败时不阻塞主流程，宁可摘要粗糙也不丢消息

本模块只做纯 IO 与规则式压缩，不调模型；模型摘要由 AIClient 编排。
"""
import hashlib
import json
import os
import re
import time

# Windows 文件名非法字符
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# 摘要合并后的上限（字符）。超出时保留尾部——越新的内容越重要。
MAX_SUMMARY_CHARS = 1200

# 规则式压缩时单行截断长度
LINE_MAX = 48


def safe_name(chat_name):
    """
    把会话名映射成安全文件名

    保留中文可读性（NTFS 支持 UTF-16 文件名），只替换真正非法的字符，
    再拼 8 位哈希防碰撞——两个不同会话名清洗后可能撞车，哈希兜住。
    """
    readable = _BAD_NAME.sub("_", chat_name or "").strip(" .")[:32] or "chat"
    digest = hashlib.md5((chat_name or "").encode("utf-8")).hexdigest()[:8]
    return f"{readable}_{digest}"


def compress_rule_based(messages, line_max=LINE_MAX):
    """
    规则式压缩：把若干条消息压成一行行「前情提要」

    不调模型、不依赖网络，永远不会失败。质量不如模型摘要，但保证
    「有东西可用」——主流程不该因为摘要失败而卡住。
    """
    lines = []
    for m in messages or []:
        who = "我" if m.get("role") == "assistant" else "对方"
        text = re.sub(r"\s+", " ", (m.get("content") or "")).strip()
        if not text:
            continue
        if len(text) > line_max:
            text = text[:line_max] + "…"
        lines.append(f"{who}：{text}")
    return "\n".join(lines)


def merge_summary(old, new, limit=MAX_SUMMARY_CHARS):
    """
    合并新旧摘要

    超出上限时保留尾部并标注截断。摘要的作用是「让 AI 记得大概」，
    不是「完整存档」——完整内容在 JSONL 归档里，不会丢。
    """
    parts = [x.strip() for x in (old or "", new or "") if x and x.strip()]
    body = "\n".join(parts)
    if len(body) > limit:
        body = "（更早的摘要已省略）\n" + body[-limit:]
    return body


class HistoryStore:
    """
    对话历史存储

    每个会话一个 JSON 文件：{chat_name, summary, messages[], updated_at}
    另有一个全局 JSONL 归档，只追加不修改，用于事后复盘（AI 到底回过什么）。
    """

    def __init__(self, root, enabled=True):
        self.root = root
        self.enabled = bool(enabled)
        self._archive = os.path.join(root, "_archive.jsonl")

    def _path(self, chat_name):
        return os.path.join(self.root, safe_name(chat_name) + ".json")

    def _ensure_dir(self):
        if not self.enabled:
            return False
        try:
            os.makedirs(self.root, exist_ok=True)
            return True
        except Exception as e:
            print(f"[警告] 无法创建历史目录 {self.root}: {e}")
            self.enabled = False
            return False

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------

    def load_all(self):
        """
        载入所有会话历史

        Returns:
            dict: {chat_name: {"summary": str, "messages": list[dict]}}
        """
        state = {}
        if not self._ensure_dir():
            return state
        try:
            files = [f for f in os.listdir(self.root) if f.endswith(".json")]
        except Exception as e:
            print(f"[警告] 读取历史目录失败: {e}")
            return state

        for fn in files:
            path = os.path.join(self.root, fn)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                chat = data.get("chat_name") or fn
                msgs = data.get("messages") or []
                if not isinstance(msgs, list):
                    continue
                state[chat] = {
                    "summary": data.get("summary") or "",
                    "messages": [m for m in msgs
                                 if isinstance(m, dict) and m.get("role")],
                }
            except Exception as e:
                # 单个文件损坏不该拖垮启动，跳过并告警
                print(f"[警告] 历史文件损坏，已跳过 {fn}: {e}")
        return state

    def save(self, chat_name, summary, messages):
        """落盘一个会话（原子写：先写临时文件再替换）"""
        if not self.enabled or not self._ensure_dir():
            return False
        path = self._path(chat_name)
        data = {
            "chat_name": chat_name,
            "summary": summary or "",
            "messages": list(messages or []),
            "updated_at": time.time(),
        }
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
            return True
        except Exception as e:
            print(f"[警告] 保存历史失败（{chat_name}）: {e}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            return False

    def archive(self, chat_name, role, content):
        """
        追加一条到全量归档（只增不删）

        历史窗口会被交班压缩，归档不会。以后想知道「上周三它到底回了什么」，
        翻这个文件就行，不用再靠滚屏。
        """
        if not self.enabled or not self._ensure_dir():
            return
        try:
            with open(self._archive, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "chat": chat_name,
                    "role": role,
                    "content": content,
                }, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[警告] 写归档日志失败: {e}")

    def clear(self, chat_name=None):
        """删除某会话或全部会话的历史文件"""
        if not self._ensure_dir():
            return
        try:
            if chat_name is None:
                for fn in os.listdir(self.root):
                    if fn.endswith(".json") or fn.endswith(".json.tmp"):
                        os.remove(os.path.join(self.root, fn))
            else:
                p = self._path(chat_name)
                if os.path.exists(p):
                    os.remove(p)
        except Exception as e:
            print(f"[警告] 清除历史失败: {e}")


# ---------------------------------------------------------------------------
# 交班决策（纯函数，便于测试）
# ---------------------------------------------------------------------------

def should_handoff(message_count, total_chars, max_messages, max_chars):
    """
    是否需要交班（条数或字符数任一超标）

    只用条数判断不够：60 条「嗯」「好」占不了多少上下文，而 10 条
    长段落可能已经几千字。字符数才是真正决定 token 消耗的量。
    """
    return message_count > max_messages or total_chars > max_chars


def split_for_handoff(messages, keep_recent, keep_chars=0):
    """
    切分出「要压缩的老消息」和「原样保留的近期消息」

    先保留最近 keep_recent 条；若给了 keep_chars 且保留部分仍然超预算，
    继续从尾部往前削——保证交班后的上下文一定能塞进窗口。

    Returns:
        (old, recent) —— old 可能为空（消息太少）
    """
    msgs = list(messages or [])
    if not msgs:
        return [], []
    keep = max(0, keep_recent)
    recent = msgs[-keep:] if keep else []
    if keep_chars > 0 and recent:
        # 从最近的一条往前累加，加上第 i 条就超预算则从 i+1 开始保留
        total = 0
        cut = 0
        for i in range(len(recent) - 1, -1, -1):
            total += len(recent[i].get("content") or "")
            if total > keep_chars:
                cut = i + 1
                break
        recent = recent[cut:]
    return msgs[:len(msgs) - len(recent)], recent
