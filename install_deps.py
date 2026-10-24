# -*- coding: utf-8 -*-
"""
AIGCBD 依赖安装脚本

为什么用 Python 而不是纯 bat：
    中文 Windows 的 cmd 按 GBK(936) 代码页解析批处理文件。若 .bat 本身以
    UTF-8 保存，里面的中文会被当成命令逐行执行，报一堆
    「'緷璧?' 不是内部或外部命令」，且 chcp 65001 救不回来（cmd 是按字节
    偏移读文件的，中途换代码页会让偏移错位）。
    Python 源码恒为 UTF-8，输出时按控制台码页自动转码，中文不会乱。

用法：
    python install_deps.py            # 安装
    python install_deps.py --check    # 只检查，不安装
"""
import importlib.util
import subprocess
import sys

# ---------------------------------------------------------------- 依赖清单
# (批次名, [包名...], 是否可选)
BATCHES = [
    ("本项目直接依赖", ["pyyaml", "pyautogui", "pyperclip"], False),
    ("wechatauto 运行依赖",
     ["cryptography", "pywin32", "zstandard", "psutil", "Pillow",
      "colorama", "uiautomation", "opencv-python", "pypinyin"], False),
    ("winsdk（可选，py3.13 可能无 wheel）", ["winsdk"], True),
    ("playwright（仅 provider=web 需要）", ["playwright"], True),
]

# 自检用的 import 名 -> 显示名
CHECK = [
    ("yaml", "pyyaml", False),
    ("pyautogui", "pyautogui", False),
    ("pyperclip", "pyperclip", False),
    ("cryptography", "cryptography", False),
    ("win32api", "pywin32", False),
    ("zstandard", "zstandard", False),
    ("psutil", "psutil", False),
    ("PIL", "Pillow", False),
    ("colorama", "colorama", False),
    ("uiautomation", "uiautomation", False),
    ("cv2", "opencv-python", False),
    ("pypinyin", "pypinyin", False),
    ("winsdk", "winsdk", True),
    ("playwright", "playwright", True),
]


def line(char="=", width=62):
    print(char * width)


def installed(module_name):
    """只查能否找到模块，不真的 import（避免 winsdk 之类有副作用的包被触发）"""
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def pip_install(packages):
    cmd = [sys.executable, "-m", "pip", "install",
           "--disable-pip-version-check", *packages]
    try:
        return subprocess.call(cmd)
    except Exception as exc:            # noqa: BLE001
        print(f"  [ERROR] 调用 pip 失败: {exc}")
        return 1


def print_status():
    print()
    line()
    print(" 依赖自检")
    line()
    missing_required, missing_optional = [], []
    for module_name, display, optional in CHECK:
        ok = installed(module_name)
        flag = "[OK]  " if ok else ("[MISS]" if not optional else "[ -- ]")
        print(f"  {flag} {display:<16} ({module_name})")
        if not ok:
            (missing_optional if optional else missing_required).append(display)
    line()
    if missing_required:
        print(f"  仍缺失（必需）: {', '.join(missing_required)}")
    if missing_optional:
        print(f"  仍缺失（可选）: {', '.join(missing_optional)}")
    if not missing_required:
        print("  必需依赖已全部就绪")
    print()
    return not missing_required


def main():
    check_only = "--check" in sys.argv

    line()
    print("  AIGCBD 依赖安装")
    print(f"  解释器: {sys.executable}")
    print(f"  版本  : {sys.version.split()[0]}")
    line()

    if not check_only:
        print()
        print("  升级 pip ...")
        subprocess.call([sys.executable, "-m", "pip", "install",
                         "--disable-pip-version-check", "--upgrade", "pip"])

        failed = []
        for idx, (title, packages, optional) in enumerate(BATCHES, 1):
            print()
            line("-")
            tag = "可选" if optional else "必需"
            print(f"  [{idx}/{len(BATCHES)}] {title}  ({tag})")
            line("-")
            rc = pip_install(packages)
            if rc != 0:
                failed.append((title, optional))
                if optional:
                    print(f"  [WARN] 本批安装失败，不影响主流程：{title}")
                else:
                    print(f"  [WARN] 本批安装失败：{title}")

        print()
        if failed:
            print("  安装结束，以下批次有问题：")
            for title, optional in failed:
                print(f"    - {title} ({'可选' if optional else '必需'})")
            print("    常见原因：网络/代理不通，或该包在 py3.13 上暂无预编译 wheel")
        else:
            print("  安装结束，全部成功")

    ok = print_status()

    print(" 下一步：")
    print("   1) 把 wechatauto-replica-main 目录放到项目根目录")
    print("   2) 启动本地模型服务: I:\\llama.cpp\\run_server.bat")
    print("   3) 运行: python src\\main.py")
    print("   4) web 模式才需要: python -m playwright install chromium")
    print()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
