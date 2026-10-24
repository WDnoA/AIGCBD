# -*- coding: utf-8 -*-
"""
可靠性工具：熔断、防重、精确匹配等纯逻辑

这几个函数都是纯函数，不依赖外部 IO，方便写单元测试。
它们对应优化建议里的 P0 项，把「会出错」的判定从散落的业务代码里抽出来。
"""
import time


# ---------------------------------------------------------------------------
# 1. 电路熔断器（优化建议 #3：模型服务卡住会阻塞 120 秒）
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """
    连续失败熔断：失败 N 次后进入冷却期，期间直接短路不再调用。

    典型场景：llama.cpp 重新加载模型时不拒连、只是不响应，
    urllib 会等满 http_timeout。熔断器让程序在「疑似故障」时立刻放弃，
    而不是每次都干等一个完整超时。
    """

    def __init__(self, fail_threshold=3, cooldown=30.0):
        self.fail_threshold = fail_threshold
        self.cooldown = cooldown
        self._failures = 0
        self._open_until = 0.0

    @property
    def is_open(self):
        """是否处于熔断（开路）状态"""
        return time.time() < self._open_until

    def allow(self):
        """
        调用前询问：是否放行。

        熔断中且冷却未结束 → False（调用方应短路）；
        冷却结束 → 自动半开，放行一次探测。
        """
        if self.is_open:
            return False
        return True

    def record_success(self):
        self._failures = 0
        self._open_until = 0.0

    def record_failure(self):
        self._failures += 1
        if self._failures >= self.fail_threshold:
            self._open_until = time.time() + self.cooldown
            self._failures = 0


# ---------------------------------------------------------------------------
# 2. 发送防重（优化建议 #1：发送失败完全静默 -> 补上重试与失败计数）
# ---------------------------------------------------------------------------

def resolve_chat_id(name, hits):
    """
    从 search_contact 的结果里精确解析会话 username（优化建议 #4：模糊匹配发错人）

    优先级：精确匹配备注 -> 精确匹配昵称 -> 唯一命中 -> 多个命中则返回 None（绝不猜）。

    Args:
        name: 配置里的聊天名称
        hits: db.search_contact(name) 返回的 list[dict]

    Returns:
        str | None: 命中的 username；多个候选且无精确匹配时返回 None，
                    由调用方打印候选列表让人工介入。
    """
    if not hits:
        return None

    # 1) 精确匹配备注（备注是用户自己写的，最可靠）
    for h in hits:
        if (h.get("remark") or "").strip() == name:
            return h.get("username")

    # 2) 精确匹配昵称
    for h in hits:
        if (h.get("nick_name") or "").strip() == name:
            return h.get("username")

    # 3) 只有一个候选，直接采用
    if len(hits) == 1:
        return hits[0].get("username")

    # 4) 多个模糊命中：返回 None，让调用方把候选列出来，人工决定，绝不乱猜
    return None


def collect_candidates(hits):
    """把 search_contact 的命中格式化成可打印的候选清单"""
    lines = []
    for h in hits:
        remark = (h.get("remark") or "").strip()
        nick = (h.get("nick_name") or "").strip()
        username = h.get("username") or ""
        label = f"备注={remark} 昵称={nick}" if remark else f"昵称={nick}"
        lines.append(f"  · {label} → {username}")
    return lines


# ---------------------------------------------------------------------------
# 3. 「我在跟人聊天，什么时候才轮到你插话」判定
# ---------------------------------------------------------------------------

# 默认触发词。命中的是「有没有叫贾维斯」，不是「这句话值不值得回」——
# 后者交给 9B 判断会翻车（实测它会把自己发的消息全判成不需要回复）。
DEFAULT_MENTION_KEYWORDS = ("贾维斯", "维斯", "小贾", "@贾维斯")


def is_mentioned(text, keywords=None):
    """
    判断这条消息有没有点名让助手接话

    用于 self_reply=mention：老板自己发的消息，默认只记上下文，
    只有明确喊了助手名字才调用模型。这是他跟别人正常聊天时
    AI 不在旁边抢话的唯一可靠保证。

    Args:
        text: 消息正文
        keywords: 触发词序列，默认 DEFAULT_MENTION_KEYWORDS

    Returns:
        bool
    """
    if not text:
        return False
    words = keywords if keywords is not None else DEFAULT_MENTION_KEYWORDS
    low = text.lower()
    return any(w and w.lower() in low for w in words)
