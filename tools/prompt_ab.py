# -*- coding: utf-8 -*-
"""
提示词 A/B 对比：同一批消息，跑不同提示词 / 不同采样参数，输出并排对比。

用法：
    python tools/prompt_ab.py                 # 跑默认三组
    python tools/prompt_ab.py --group v1      # 只跑某组

为什么需要这个脚本：
「AI 说话没感情」这类问题，靠读提示词是看不出好坏的。同一个提示词在
temperature 0.7 和 0.85 下、有没有 presence_penalty 下，语气差别比改三百字
提示词还大。必须拿真实的话去跑，肉眼对比输出。

脚本直接读 config.yaml 里的 prompt_third 作为 v1（当前线上版本），
读 tools/prompt_v2_third.txt 作为 v2（候选版本），两边用完全相同的
messages 结构（system + 当前条带【对方】标记），只有提示词和参数不同。
"""
import argparse
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

BASE = "http://127.0.0.1:8080/v1"
CHAT = "魏晓彤"

# (说话人, 消息正文)  —— 覆盖日常、撒娇、赌气、开心、点名求助几类语气
CASES = [
    ("other", "今天上班好累啊"),
    ("other", "你是不是又忘了"),
    ("other", "我心情不太好"),
    ("other", "在吗"),
    ("other", "哈哈哈哈哈这个狗太逗了"),
    ("other", "算了，不说了"),
    ("self", "贾维斯，帮我想句怎么回她"),
    ("self", "贾维斯，在吗"),
]


def load_prompts():
    """读 config.yaml 里该会话的 prompt_third，以及全局采样参数"""
    import yaml
    with open(os.path.join(ROOT, "config", "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    prompt = ""
    for rule in cfg.get("chat_rules") or []:
        if rule.get("chat_name") == CHAT:
            prompt = rule.get("prompt_third") or ""
            break
    return prompt, (cfg.get("ai") or {})


def call(system_prompt, user_text, actor, temperature, presence, frequency,
         top_k=0, min_p=0.0, max_tokens=400):
    """一次干净的单轮调用：只有 system + 当前这条，不带历史"""
    tag = "【我】" if actor == "self" else "【对方】"
    payload = {
        "model": "local",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": tag + user_text},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": 0.92,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if presence:
        payload["presence_penalty"] = presence
    if frequency:
        payload["frequency_penalty"] = frequency
    if top_k:
        payload["top_k"] = top_k
    if min_p:
        payload["min_p"] = min_p

    # 空 ProxyHandler：系统里若设了 http_proxy，会把 127.0.0.1 的请求也
    # 丢给代理，导致连本地模型都失败
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        f"{BASE}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer not-needed"},
        method="POST",
    )
    with opener.open(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    return (msg.get("content") or "").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="all",
                    help="v1 / v2 / v2hot / all")
    args = ap.parse_args()

    v1, ai = load_prompts()
    if not v1:
        print(f"✗ config.yaml 里没找到 {CHAT} 的 prompt_third")
        return 1

    # cfg 组 = 当前 config.yaml 的提示词 + 当前采样参数，也就是真正上线跑的组合。
    # 改完 config 想知道效果，跑这一组就够了。
    # cool 组用同一份提示词，但把 temperature/惩罚降回旧值——用来单独观察
    # 「参数」对语气的影响到底有多大（实测比想象中大得多）。
    groups = {
        "cfg": ("线上组合（config.yaml 原样）", v1,
                float(ai.get("temperature") or 0.7),
                float(ai.get("presence_penalty") or 0),
                float(ai.get("frequency_penalty") or 0),
                int(ai.get("top_k") or 0),
                float(ai.get("min_p") or 0)),
        "cool": ("同一提示词 t=0.7 无惩罚（对照组）", v1, 0.70, 0, 0, 0, 0),
    }
    keys = list(groups) if args.group == "all" else [args.group]

    results = {k: [] for k in keys}
    for case_i, (actor, text) in enumerate(CASES, 1):
        print(f"\n[{case_i}/{len(CASES)}] {text}")
        for k in keys:
            label, prompt, temp, pres, freq, tk, mp = groups[k]
            t0 = time.time()
            try:
                out = call(prompt, text, actor, temp, pres, freq, tk, mp)
            except Exception as e:
                out = f"<调用失败: {e}>"
            cost = time.time() - t0
            results[k].append(out)
            print(f"   {k:6s} ({cost:.1f}s) {out}")

    # 输出 markdown 对比表
    lines = ["# 提示词 A/B 对比结果", "",
             f"模型：本地 Qwen3.5-9B Q4_K_M　会话：{CHAT}　无历史单轮", ""]
    for k in keys:
        label = groups[k][0]
        lines.append(f"- `{k}` = {label}")
    lines.append("")
    for i, (actor, text) in enumerate(CASES):
        who = "老板" if actor == "self" else "晓彤"
        lines.append(f"## {i+1}. 【{who}】{text}")
        lines.append("")
        lines.append("| 版本 | 回复 | 字数 |")
        lines.append("|---|---|---|")
        for k in keys:
            reply = results[k][i].replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {k} | {reply} | {len(results[k][i])} |")
        lines.append("")

    out_path = os.path.join(ROOT, "tools", "ab_result.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n✓ 对比结果已写入 {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
