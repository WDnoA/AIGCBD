# -*- coding: utf-8 -*-
"""
身份模式选择弹窗（tkinter，Python 内置，零额外依赖）

启动时用一个窗口同时为所有配置了双模式提示词（prompt_third / prompt_opponent）
的监听对象选择 AI 回复身份：先在顶部选「回复身份模式」，再在下面勾选要套用该
模式的对象（默认全选），未勾选的对象自动使用另一种模式。点「确定」才生效。
"""
import tkinter as tk

MAX_LIST_HEIGHT = 240   # 对象列表最大高度（像素），超过则出现滚动条


def choose_identities(chat_names):
    """
    弹出身份模式选择窗口，一次为所有监听对象定好身份模式（阻塞直到点「确定」）

    Args:
        chat_names: 需要选择身份的聊天对象名称列表

    Returns:
        dict: {聊天对象名称: "third" | "opponent"}
              勾选的对象用顶部所选模式，未勾选的对象用另一种模式
    """
    result = {}

    root = tk.Tk()
    root.title("身份模式")
    root.resizable(False, False)
    root.attributes("-topmost", True)   # 置顶，避免被 Qwen 浏览器/控制台盖住
    root.lift()                         # 抬到 Z 序最顶
    root.focus_force()                  # 抢占焦点
    root.bell()                         # 提示音，避免盯着日志时不知道弹窗已出现
    # 点 X 不关窗（关掉拿不到结果），只响铃并把焦点拉回来，必须点「确定」
    root.protocol("WM_DELETE_WINDOW", lambda: (root.bell(), root.focus_force()))

    # 顶部：回复身份模式（默认「只回答聊天对象」）
    mode_var = tk.StringVar(value="opponent")
    tk.Label(root, text="请选择 AI 的回复身份模式：",
             font=("Microsoft YaHei", 11)).pack(anchor="w", padx=20, pady=(14, 4))
    for text, value in (("第三方（可回答双方信息）", "third"),
                        ("只回答聊天对象", "opponent")):
        tk.Radiobutton(root, text=text, value=value, variable=mode_var,
                       font=("Microsoft YaHei", 10)).pack(anchor="w", padx=34)

    # 中部：勾选要套用该模式的对象（默认全选，对象多时出现滚动条）
    tk.Label(root, text="勾选要套用该模式的对象（未勾选的用另一种模式）：",
             font=("Microsoft YaHei", 11)).pack(anchor="w", padx=20, pady=(12, 4))

    holder = tk.Frame(root)
    holder.pack(fill="x", padx=20)
    canvas = tk.Canvas(holder, highlightthickness=0, width=340)
    inner = tk.Frame(canvas)
    win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
    bar = tk.Scrollbar(holder, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=bar.set)
    canvas.pack(side="left", fill="both", expand=True)
    canvas.bind("<Configure>",
                lambda e: canvas.itemconfigure(win_id, width=e.width))
    inner.bind("<Configure>",
               lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

    checks = []     # [(聊天对象名称, 勾选变量)]
    for name in chat_names:
        var = tk.BooleanVar(value=True)     # 默认全选
        tk.Checkbutton(inner, text=name, variable=var, anchor="w",
                       font=("Microsoft YaHei", 10)).pack(fill="x")
        checks.append((name, var))

    # 先量出列表真实高度，装不下时才启用滚动条
    root.update_idletasks()
    total_h = inner.winfo_reqheight()
    canvas.configure(height=min(total_h, MAX_LIST_HEIGHT))
    if total_h > MAX_LIST_HEIGHT:
        bar.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(-e.delta // 120, "units"))

    # 底部：全选 / 全不选 / 确定
    def set_all(value):
        for _, var in checks:
            var.set(value)

    def confirm():
        mode = mode_var.get()
        other = "third" if mode == "opponent" else "opponent"
        for name, var in checks:
            result[name] = mode if var.get() else other
        root.destroy()

    footer = tk.Frame(root)
    footer.pack(fill="x", padx=20, pady=14)
    tk.Button(footer, text="全选", width=8, font=("Microsoft YaHei", 10),
              command=lambda: set_all(True)).pack(side="left")
    tk.Button(footer, text="全不选", width=8, font=("Microsoft YaHei", 10),
              command=lambda: set_all(False)).pack(side="left", padx=6)
    tk.Button(footer, text="确定", width=10, font=("Microsoft YaHei", 10),
              command=confirm).pack(side="right")

    # 居中显示
    root.update_idletasks()
    w = 400
    h = root.winfo_reqheight()
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")

    root.mainloop()
    return result
