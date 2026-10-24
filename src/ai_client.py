# -*- coding: utf-8 -*-
"""
AI客户端模块

支持两种后端（由 config.yaml 的 ai.provider 决定）：

  openai  调用任意 OpenAI 兼容接口（推荐：本地 llama.cpp / vLLM / Ollama）
          —— 速度快、无需登录、聊天内容不出本机
  web     Playwright 自动化操作 chat.qwen.ai 网页（无需 API Key，但依赖网页结构）

web 模式的选择器基于实测的 Qwen Studio 页面结构标定：
    输入框    textarea.message-input-textarea
    发送按钮  button.send-button[aria-label="发送"]
    回复容器  .qwen-chat-message-assistant
    回复正文  .response-message-content.phase-answer
页面改版后需要重新标定。
"""
import glob
import json
import os
import re
import subprocess
import time
import asyncio
import urllib.error
import urllib.request

from reliability import CircuitBreaker
from history_store import (
    HistoryStore, compress_rule_based, merge_summary,
    should_handoff, split_for_handoff,
)

# playwright 只在 web 模式下才需要，改成用到时再导入（见 init_browser）。
# 这样 provider=openai 的用户即使没装 playwright 也能直接跑。

# AI 表示「不需要回复」的占位输出。这类回复不会真正发出，
# 因此不能写入对话历史，否则切换会话时会被当作真实上下文投喂给 AI。
IGNORE_PLACEHOLDER = "[忽略]"


def is_ignore_reply(text):
    """
    判断模型输出是不是「不需要回复」的占位符

    实测 9B 模型不会稳定输出 `[忽略]` 这四个字符，常见变体有
    `忽略`、`【忽略】`、`[忽略]。`、`(忽略)`、全角括号、带句号等。
    早期代码用 `== "[忽略]"` 精确比较，漏判的后果很严重：
    对方消息被判成「无需回复」时，这个占位符会被当成正文**真的发给对方**。
    """
    if not text or not text.strip():
        return True
    core = text.strip().strip('"\''"“”‘’").strip()
    # 反复剥掉成对的括号/方括号，再剥掉尾部标点
    for _ in range(3):
        stripped = core.strip("[]【】()（）<>《》〈〉 ")
        stripped = stripped.rstrip("。.!！~～,，、;； ")
        if stripped == core:
            break
        core = stripped
    return core in ("忽略", "ignore", "ignored", "skip", "无需回复", "不用回复")


SRC_SELF = "【我】"
SRC_OTHER = "【对方】"


def tag_source(text, role):
    """
    给消息正文加上来源标记，喂给模型时用它区分「谁说的」

    Args:
        text: 消息正文
        role: "assistant" 表示「我」自己发出的，其余（"user"/None）表示聊天对象发来的

    标记必须和 system prompt 里写的字符完全一致（`【我】` / `【对方】`），
    否则模型会对不上号——早期 prompt 让模型看「我」开头，代码却喂
    「我（自己发出的信息，不是对方发的）」这种长串，属于自相矛盾。
    """
    body = (text or "").strip()
    if not body:
        return body
    # 已经是带标记的（例如 add_history 时外部塞入的原文）不重复加
    if body.startswith(SRC_SELF) or body.startswith(SRC_OTHER):
        return body
    return (SRC_SELF if role == "assistant" else SRC_OTHER) + body


# 模型会把喂进去的来源标记当成正文模仿输出。实测连续 7 轮里有 3 轮
# 输出了「【我】老板，您去把奶茶换了」这种带前缀的话——因为历史消息里
# 每条都带标记，模型把它当成了「回复应该长这样」的示范。
# prompt 里再怎么写「不要输出标记」也压不住，必须在输出端剥掉。
SRC_TAG_RE = re.compile(
    r"^\s*(?:【\s*(?:我|对方|你)\s*】|\[\s*(?:我|对方)\s*\]|"
    r"(?:我|对方|贾维斯)\s*[：:])\s*"
)


def clean_reply(text):
    """清掉模型给整句套的引号/书名号，以及误抄的来源标记"""
    s = (text or "").strip()
    # 反复剥：可能叠成「【我】宝宝：…」
    for _ in range(3):
        stripped = SRC_TAG_RE.sub("", s)
        if stripped == s:
            break
        s = stripped.strip()

    pairs = [('"', '"'), ("'", "'"), ("“", "”"), ("「", "」"), ("『", "』")]
    changed = True
    while changed and len(s) >= 2:
        changed = False
        for a, b in pairs:
            if s.startswith(a) and s.endswith(b):
                s = s[1:-1].strip()
                changed = True
    return _join_lines(s)


# 行尾已经是这些标点时，下一行直接接上；否则补个逗号，避免两句话糊在一起
_PUNCT_END = tuple("。！？，、；：…~～.!?,;:")


def _join_lines(text):
    """
    把多行回复压成一行

    放开长度之后模型会偶尔输出两行（「在呢老板，正盯着对话框…」换行
    「咋样，又遇到啥难缠的？」）。微信这边是整段粘贴再回车发送，
    换行会在输入框里留成多行，观感像一段公告，也不像手打字。
    压成一行后连逗号都不用改——行尾有标点就直连，没有才补逗号。
    """
    parts = [p.strip() for p in (text or "").splitlines() if p.strip()]
    if len(parts) <= 1:
        return parts[0] if parts else ""
    out = parts[0]
    for p in parts[1:]:
        out += ("" if out.endswith(_PUNCT_END) else "，") + p
    return out


# 判断「两句是不是同一句」时忽略的字符：标点、空白、括号。
# 模型换个句号就躲过精确比较，所以必须先归一化。
_REPEAT_STRIP_RE = re.compile(
    r"[\s，。！？、…~～,.!?;；:：\"'“”‘’（）()【】\[\]《》]"
)


def normalize_reply(text):
    """只留字，去掉标点和空白"""
    return _REPEAT_STRIP_RE.sub("", text or "")


def is_repeated_reply(reply, history, window=8):
    """
    这句是不是和最近说过的某一句一模一样

    本地小模型有个通病：情绪完全不同的两条消息，回的是同一句话。
    实测 8 条覆盖不同语气的测试里，「宝宝，累了就歇会儿，我陪你」
    出现了两次，分别对应「今天好累」和「算了，不说了」——一对比就露馅，
    对方立刻知道这边不是人在打字。

    Args:
        reply: 本次生成的回复
        history: 该会话的历史消息列表
        window: 只跟最近多少条自己说过的话比

    只比最近 window 条、归一化后要求**完全相等**，不做模糊匹配——
    模糊匹配会把「嗯」「行吧」这类本来就会重复的短句全判成复读。
    """
    target = normalize_reply(reply)
    if not target:
        return False
    recent = [normalize_reply(m.get("content") or "")
              for m in (history or []) if m.get("role") == "assistant"]
    return target in recent[-window:]


class AIClient:
    """AI客户端封装 - 支持 OpenAI 兼容接口与网页自动化两种后端"""

    # --- 页面选择器（实测标定，仅 web 模式使用） ---
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
        # --- 后端选择 ---
        self.provider = (ai_config.get("provider") or "web").strip().lower()

        # web 模式参数
        self.url = ai_config.get("url", "https://chat.qwen.ai/")
        self.wait_timeout = ai_config.get("wait_timeout", 30)  # 等待AI回复的超时时间（秒）

        # openai 模式参数（本地 llama.cpp 默认 127.0.0.1:8080/v1）
        self.base_url = (ai_config.get("base_url")
                         or "http://127.0.0.1:8080/v1").rstrip("/")
        self.api_key = ai_config.get("api_key") or "not-needed"
        self.model = ai_config.get("model") or "local"
        self.max_tokens = int(ai_config.get("max_tokens") or 512)
        self.temperature = float(ai_config.get("temperature")
                                 if ai_config.get("temperature") is not None else 0.7)
        self.top_p = float(ai_config.get("top_p")
                           if ai_config.get("top_p") is not None else 0.9)
        # 采样多样性惩罚。本地小模型最明显的「机器味」不是用词差，
        # 而是翻来覆去同一批句式：实测 8 条不同情绪的消息，
        # 「宝宝，…我在呢」出现了 3 次，「累了就歇会儿我陪你」用在了
        # 「好累」和「算了不说了」这两种完全不同的情绪上。
        #   presence_penalty  按「这个词出现过」扣分，拉开句式选择
        #   frequency_penalty 按出现次数累加扣分，压住口头禅刷屏
        # 0.3 / 0.2 是实测值；加到 0.5 以上句子会开始前言不搭后语。
        self.presence_penalty = float(ai_config.get("presence_penalty") or 0.0)
        self.frequency_penalty = float(ai_config.get("frequency_penalty") or 0.0)
        # 长尾截断。temperature 拉到 0.85 后句式活了，但也会偶尔「跑野」：
        # 实测出现过回晓彤时提到「老板」、给老板想话时忘了称呼——这类不是文采问题，
        # 是踩了硬规矩。top_k + min_p 正是干这个的：砍掉概率极低的候选词，
        # 温度带来的多样性留在前 40 个候选里，长尾的胡话直接不进采样池。
        self.top_k = int(ai_config.get("top_k") or 0)
        self.min_p = float(ai_config.get("min_p") or 0.0)
        self.http_timeout = float(ai_config.get("http_timeout") or 30)
        # 思考型模型（如 Qwen3.5）会把 token 预算耗在思考过程上，
        # 导致 content 为空。自动回复场景不需要思考链，默认关闭。
        self.disable_thinking = bool(ai_config.get("disable_thinking", True))
        # 模型服务熔断：连续失败 N 次进入冷却，期间直接短路，避免每次
        # 都干等一个 http_timeout。冷却期后自动半开放行一次探测。
        self._breaker = CircuitBreaker(
            fail_threshold=int(ai_config.get("fail_threshold") or 3),
            cooldown=float(ai_config.get("cooldown") or 30.0),
        )

        # 两种模式共用
        self.system_prompt = ai_config.get("system_prompt", "你是一个有帮助的助手。")

        # 持久化用户数据目录：保留登录态与历史，避免每次启动都处于未登录状态
        default_profile = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "browser_profile",
        )
        self.user_data_dir = ai_config.get("user_data_dir") or default_profile

        # Playwright相关（仅 web 模式使用）
        self.playwright = None
        self.context = None
        self.page = None

        # 对话历史（每个聊天窗口独立）
        self.chat_history = {}
        # 交班产生的摘要（每个会话一段，随对话累积）
        self._summaries = {}
        # 历史持久化与交班：重启不失忆 + 长对话不丢细节
        hist_cfg = ai_config.get("history") or {}
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _dir = hist_cfg.get("dir") or "data/history"
        # 配置里写相对路径时锚定到项目根，避免跟着启动时的工作目录跑
        if not os.path.isabs(_dir):
            _dir = os.path.join(_root, _dir)
        self.history_dir = _dir
        self.history_max = int(hist_cfg.get("max_messages") or 60)
        self.history_max_chars = int(hist_cfg.get("max_chars") or 3000)
        self.history_keep = int(hist_cfg.get("keep_recent") or 30)
        self.history_model_summary = bool(hist_cfg.get("use_model_summary", True))
        self.store = HistoryStore(self.history_dir,
                                  enabled=bool(hist_cfg.get("enabled", True)))
        self._load_history()

        # 当前 qwen 页面服务于哪个会话（用于检测会话切换后刷新网页，隔离残留历史）
        self._active_chat = None

    def _load_history(self):
        """启动时把落盘的历史载回内存"""
        state = self.store.load_all()
        if not state:
            return
        total = 0
        for chat, s in state.items():
            self.chat_history[chat] = s.get("messages") or []
            if s.get("summary"):
                self._summaries[chat] = s["summary"]
            total += len(self.chat_history[chat])
        if total:
            print(f"✓ 已恢复 {len(state)} 个会话的历史（{total} 条），"
                  f"目录: {self.history_dir}")

    def _remember(self, chat_name, role, content):
        """
        记一条历史：进内存 + 落盘 + 归档

        所有历史写入都必须走这里。之前散落着四份「append 完截断到 20 条」
        的重复代码，截断会把第 21 条之前的内容直接扔掉——现在改为落盘后
        等交班压缩，宁可摘要粗糙也不静默丢。
        """
        if not content:
            return
        history = self.chat_history.setdefault(chat_name, [])
        history.append({"role": role, "content": content})
        self.store.save(chat_name, self._summaries.get(chat_name, ""), history)
        self.store.archive(chat_name, role, content)

    async def _maybe_handoff(self, chat_name):
        """
        历史过长时交班：把最老的若干条压成摘要，近期消息保留

        优先用模型摘要（信息密度高）；模型不可用时退回规则式压缩，
        保证交班本身永远不会失败、也不会卡住回复。
        """
        history = self.chat_history.get(chat_name) or []
        total_chars = sum(len(m.get("content") or "") for m in history)
        if not should_handoff(len(history), total_chars,
                              self.history_max, self.history_max_chars):
            return

        old, recent = split_for_handoff(history, self.history_keep,
                                        self.history_max_chars // 2)
        if not old:
            return

        summary = ""
        if self.history_model_summary and self._breaker.allow():
            summary = await self._summarize(chat_name, old) or ""

        if not summary:
            summary = compress_rule_based(old)
            note = "（规则式压缩）"
        else:
            note = "（模型摘要）"

        self._summaries[chat_name] = merge_summary(
            self._summaries.get(chat_name, ""), summary)
        self.chat_history[chat_name] = recent
        self.store.save(chat_name, self._summaries[chat_name], recent)
        print(f"[交班] {chat_name}: {len(old)} 条压成摘要{note}，"
              f"保留最近 {len(recent)} 条")

    async def _summarize(self, chat_name, messages):
        """调模型把一批老消息压成交接摘要；失败返回 None 由调用方兜底"""
        body = compress_rule_based(messages, line_max=120)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system",
                 "content": "你在为一段微信聊天做交接摘要，交给接班的助手。\n\n"
                            "做法：从头到尾扫一遍，每发现一件「独立的事」就单独写一行。"
                            "不要把两件事合并成一行，不要因为某件事只说了两句就跳过它。\n\n"
                            "每行格式为「【类别】内容」，类别只能是这五种：\n"
                            "【约定】约好的时间地点、答应做的事\n"
                            "【待办】还没做完、需要记得的事\n"
                            "【信息】金额、数量、地址、证件这类具体事实\n"
                            "【人物】提到的人、称呼、关系变化\n"
                            "【禁忌】对方的喜好、雷区、要注意的地方\n\n"
                            "示例：\n"
                            "【约定】周末下午两点在美术馆看展\n"
                            "【待办】提前买票，当天带身份证\n"
                            "【信息】门票 80 元一人\n"
                            "【人物】她妈周日请我吃饭\n"
                            "【禁忌】别买太贵的酒，她妈不喜欢铺张\n\n"
                            "只输出这些行，不要前言、不要总结句、不要解释。"},
                {"role": "user", "content": body},
            ],
            "max_tokens": 300,
            "temperature": 0.3,
        }
        if self.disable_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        try:
            data = await asyncio.to_thread(
                self._http_json, f"{self.base_url}/chat/completions",
                payload, "POST")
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            text = (msg.get("content") or msg.get("reasoning_content") or "").strip()
            return text or None
        except Exception as e:
            print(f"[警告] 摘要生成失败（{chat_name}），改用规则式压缩: {e}")
            return None

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

    # ------------------------------------------------------------------
    # openai 模式：直连 OpenAI 兼容接口（llama.cpp / vLLM / Ollama / LM Studio）
    # ------------------------------------------------------------------

    def _http_json(self, url, payload=None, method="POST"):
        """
        同步发起一次 JSON 请求（由 asyncio.to_thread 包起来跑，不阻塞事件循环）

        显式使用「空 ProxyHandler」的 opener：系统里如果设置了 http_proxy
        （本机就出现过指向失效端口的代理变量），urllib 会尝试把
        127.0.0.1 的请求也丢给代理，导致连本地模型都失败。
        """
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(url, data=data, method=method, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        })
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=self.http_timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)

    async def _init_api(self):
        """探测本地/远端 OpenAI 兼容接口是否就绪（不启动浏览器）"""
        url = f"{self.base_url}/models"
        try:
            data = await asyncio.to_thread(self._http_json, url, None, "GET")
        except urllib.error.URLError as e:
            print(f"✗ AI 接口连接失败: {e}")
            print(f"  期望地址: {url}")
            print("  请先启动模型服务，例如双击 I:\\llama.cpp\\run_server.bat")
            return False
        except Exception as e:
            print(f"✗ AI 接口连接失败: {e}")
            return False

        model_ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
        if model_ids:
            # 配置里的 model 只是占位（默认 local）或与 GGUF 实际名不符时，
            # 自动对齐到服务端真正加载的那个模型，免得每次都手改 yaml
            if self.model in ("", "local", "default") or self.model not in model_ids:
                if self.model not in ("", "local", "default"):
                    print(f"[警告] 配置的模型 {self.model} 不在服务端列表中")
                self.model = model_ids[0]
            print(f"✓ AI 接口就绪: {self.base_url}  模型: {self.model}")
        else:
            print(f"✓ AI 接口就绪: {self.base_url}（服务端未返回模型列表）")
        return True

    async def _call_api(self, chat_name, user_message, system_prompt=None,
                        actor="user", forbid_words=None):
        """
        调用 /v1/chat/completions 拿回复

        与 web 模式的一个关键差异：这里每一轮都把本地历史作为 messages
        正常投喂给模型，不存在「页面自己保留上下文」的情况，所以不区分
        是否切换会话。
        """
        prompt = system_prompt or self.system_prompt

        # 熔断中：直接短路，不给模型发请求（省得每条消息都等满超时）
        if not self._breaker.allow():
            print(f"[熔断] 模型服务连续失败，冷却中（{self._breaker.cooldown:.0f}s），"
                  "本条不调用模型")
            return None

        # 历史过长先交班：把最老的若干条压成摘要，近期消息原样保留。
        # 放在构建 messages 之前，这样投喂的就是交班后的最新状态。
        await self._maybe_handoff(chat_name)

        # 摘要并进 system prompt，而不是单独发一条 system —— 只有一条
        # system 消息，不必依赖 chat template 对多条 system 的拼接行为。
        messages = [{"role": "system", "content": prompt}]
        summary = self._summaries.get(chat_name) or ""
        if summary:
            messages[0]["content"] = f"{prompt}\n\n【更早的对话摘要】\n{summary}"

        for m in self.chat_history.get(chat_name, []):
            text = (m.get("content") or "").strip()
            if not text:
                continue
            messages.append({
                "role": "assistant" if m.get("role") == "assistant" else "user",
                # 历史消息也必须带来源标记。早期实现只给「当前这条」加标注，
                # 而 system prompt 却要求「每条消息开头都有『我』/『对方』」，
                # 模型看到历史里没有标记就只能按内容瞎猜说话人——这是
                # 「对方的话被当成我自己发的」这类误判的根源之一。
                "content": tag_source(text, m.get("role")),
            })
        # 当前这条固定用 user 角色承载，靠文案标注真实说话方。
        # 原因是 llama.cpp 的 jinja 模板在 messages 末尾是 assistant 时，
        # 容易变成「续写我自己的话」而不是「回复对方」。
        messages.append({"role": "user",
                         "content": tag_source(user_message, actor)})

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "stream": False,
        }
        if self.presence_penalty:
            payload["presence_penalty"] = self.presence_penalty
        if self.frequency_penalty:
            payload["frequency_penalty"] = self.frequency_penalty
        if self.top_k:
            # llama.cpp 的扩展采样参数（OpenAI 标准里没有，llama-server 认）
            payload["top_k"] = self.top_k
        if self.min_p:
            payload["min_p"] = self.min_p
        if self.disable_thinking:
            # Qwen3.5 等思考型模型默认会把 max_tokens 全耗在思考链上，
            # 正文 content 直接为空。自动回复不需要思考过程，显式关掉。
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        url = f"{self.base_url}/chat/completions"
        reply = None
        # 生成重试：模型偶尔会「对着 A 说话却叫 B 的名字」。实测给晓彤的回复
        # 里出现过「老板，我负责哄，您负责点头」——这句话发到聊天窗口里，
        # 晓彤只会一头雾水。命中禁词就重新采样一次，两次都不行宁可不发。
        for gen in range(2):
            data = None
            for attempt in range(2):
                try:
                    data = await asyncio.to_thread(self._http_json, url, payload, "POST")
                    break
                except urllib.error.HTTPError as e:
                    detail = ""
                    try:
                        detail = e.read().decode("utf-8", errors="replace")[:500]
                    except Exception:
                        pass
                    print(f"[AI错误] HTTP {e.code}: {detail}")
                    self._breaker.record_failure()
                    return None
                except Exception as e:
                    if attempt == 0:
                        print(f"[重试] AI 请求失败({e})，1 秒后重试")
                        await asyncio.sleep(1)
                        continue
                    print(f"[AI错误] 请求模型失败: {e}")
                    print("  请确认 llama.cpp 服务仍在运行（默认 http://127.0.0.1:8080）")
                    self._breaker.record_failure()
                    return None

            if not data:
                return None

            # 请求成功，重置熔断计数
            self._breaker.record_success()

            choices = data.get("choices") or []
            if not choices:
                print(f"[AI错误] 返回中没有 choices: {str(data)[:300]}")
                return None

            msg = choices[0].get("message") or {}
            reply = (msg.get("content") or "").strip()
            if not reply and msg.get("reasoning_content"):
                # 关思考失败时正文为空，思考链里有内容，拿出来兜底总比不发好
                reply = (msg.get("reasoning_content") or "").strip()
            reply = self._sanitize(reply)
            if not reply:
                print("[AI错误] 模型返回空内容，本次不发送")
                return None

            reply = clean_reply(reply)
            bad = [w for w in (forbid_words or []) if w and w in reply]
            repeat = is_repeated_reply(reply, self.chat_history.get(chat_name))
            if not bad and not repeat:
                break

            if bad:
                print(f"[重试] 回复里误用了「{'、'.join(bad)}」，重新生成（{gen + 1}/2）")
            else:
                print(f"[重试] 回复和前面说过的重复了，重新生成（{gen + 1}/2）")
            if gen == 1:
                if bad:
                    print("[放弃] 连续两次说话对象串了，本次不发送")
                    return None
                # 只是重复：内容本身没错，发一句重复的也好过让对方干等
                print("[警告] 两次都撞上重复回复，仍按本次结果发送")
                break
            # 光靠重新采样常常还是错（历史里刚有一条「老板，…」把模型带偏了），
            # 第二次把硬约束直接贴到当前这条消息末尾，模型一定会看到。
            hints = []
            if bad:
                hints.append(f"句中绝对不能出现「{'、'.join(bad)}」")
            if repeat:
                hints.append("换一种说法，不要和你前面说过的任何一句重复")
            payload["messages"][-1]["content"] += (
                f"\n（提醒：上面这条是对方发来的，你要回的是对方本人；"
                + "；".join(hints) + "）")

        print(f"[AI回复] 生成完毕，长度: {len(reply)}")

        # 更新对话历史（走 _remember：落盘 + 归档，不再静默截断）
        self._remember(chat_name, actor, user_message)
        # 占位回复（如「[忽略]」）不会真正发出，只记录消息本身、不记录它
        if not is_ignore_reply(reply):
            self._remember(chat_name, "assistant", reply)

        return reply

    @staticmethod
    def _sanitize(text):
        """剥掉可能残留的思考块，避免把推理过程发到微信里"""
        if not text:
            return ""
        out = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", text,
                     flags=re.S | re.I)
        if "<｜end▁of▁thinking｜>" in out:
            # 流式截断时只剩后半段（没有开标签），把结束标记之前的内容一并丢掉
            out = out.split("<｜end▁of▁thinking｜>", 1)[1]
        return out.strip()

    async def init_browser(self):
        """初始化 AI 后端

        openai 模式：只做一次连通性探测，不启动浏览器；
        web    模式：启动持久化 Chromium 并打开大模型网页。
        """
        if self.provider == "openai":
            return await self._init_api()

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            print("✗ web 模式需要 playwright：pip install playwright && playwright install chromium")
            print("  如果只想用本地模型，把 config.yaml 的 ai.provider 改成 openai")
            return False

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

    async def get_response(self, chat_name, user_message, system_prompt=None,
                           actor="user", forbid_words=None):
        """
        获取AI回复

        Args:
            chat_name: 聊天窗口名称
            user_message: 用户消息
            system_prompt: 本次使用的提示词，为空时使用全局配置
            actor: "assistant" 表示这条是「我」自己发的，"user" 表示对方发来的
            forbid_words: 回复里不允许出现的词（用于校验说话对象有没有串），
                          命中会重新生成一次

        Returns:
            str: AI回复内容
        """
        # openai 模式走纯 HTTP，不依赖浏览器，也不会串会话
        if self.provider == "openai":
            return await self._call_api(chat_name, user_message,
                                        system_prompt, actor, forbid_words)

        if not self.page:
            print("[错误] 浏览器未初始化")
            return "抱歉，AI客户端未初始化。"

        try:
            # 构建完整的提示词（会话级提示词优先，否则用全局提示词）
            prompt = system_prompt or self.system_prompt
            # 只有切换会话时才会刷新页面，刷新后 qwen 页面的上下文被清空，
            # 需要把本地历史补进 prompt；同一会话内页面自己保留了上下文，
            # 再拼一次属于重复投喂，只会拉长 prompt 拖慢首 token 生成。
            switched = self._active_chat != chat_name
            history = self.chat_history.get(chat_name, [])
            if history and switched:
                # 与 openai 模式用同一套来源标记，避免两个后端行为不一致
                ctx = "\n".join(
                    tag_source(m.get("content", ""), m.get("role"))
                    for m in history
                )
                full_prompt = (f"{prompt}\n\n最近的聊天记录：\n{ctx}"
                               f"\n\n{tag_source(user_message, actor)}")
            else:
                full_prompt = f"{prompt}\n\n{tag_source(user_message, actor)}"

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

            # 更新对话历史（走 _remember：落盘 + 归档）
            self._remember(chat_name, actor, user_message)
            # 占位回复（如「[忽略]」）不会真正发出，只记录消息本身、不记录它
            if not is_ignore_reply(ai_reply):
                self._remember(chat_name, "assistant", ai_reply)

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
        """关闭浏览器（openai 模式下没有浏览器，直接返回）"""
        if self.provider != "web":
            return
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
        self._remember(chat_name, role, content)

    def clear_history(self, chat_name=None):
        """
        清除对话历史（含落盘文件）

        Args:
            chat_name: 聊天窗口名称，None表示清除所有
        """
        if chat_name:
            self.chat_history[chat_name] = []
            self._summaries.pop(chat_name, None)
            self.store.clear(chat_name)
            print(f"已清除 {chat_name} 的对话历史")
        else:
            self.chat_history.clear()
            self._summaries.clear()
            self.store.clear(None)
            print("已清除所有对话历史")