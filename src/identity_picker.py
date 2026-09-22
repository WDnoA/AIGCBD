# -*- coding: utf-8 -*-
"""
身份模式选择弹窗（tkinter，Python 内置，零额外依赖）

启动时为每个配置了双模式提示词（prompt_third / prompt_opponent）的监听对象
弹出窗口，让使用者选择 AI 以哪种身份回复，窗口标题与正文都显示聊天对象名。
"""
import tkinter as tk


def choose_identity(chat_name):
    """
    弹出身份模式选择窗口（阻塞直到用户点击任一按钮）

    Args:
        chat_name: 聊天对象名称（显示在标题与正文）

    Returns:
        str: "third"（第三方，可回答双方信息）或 "opponent"（只回答聊天对象信息）
    """
    result = {}

    root = tk.Tk()
    root.title(f"身份模式 - {chat_name}")
    root.resizable(False, False)

    # 居中显示
    root.update_idletasks()
    w, h = 380, 180
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")

    tk.Label(
        root,
        text=f"聊天对象：{chat_name}\n请选择 AI 的回复身份模式：",
        justify="left",
        font=("Microsoft YaHei", 11),
    ).pack(padx=24, pady=16)

    def select(mode):
        result["mode"] = mode
        root.destroy()

    tk.Button(
        root, text="第三方（可回答双方信息）", width=26,
        font=("Microsoft YaHei", 10),
        command=lambda: select("third"),
    ).pack(padx=20, pady=4)

    tk.Button(
        root, text="只回答聊天对象信息", width=26,
        font=("Microsoft YaHei", 10),
        command=lambda: select("opponent"),
    ).pack(padx=20, pady=4)

    root.mainloop()
    return result.get("mode")