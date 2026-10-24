# -*- coding: utf-8 -*-
"""
停掉所有正在运行的 main.py 实例（restart.bat 的第一步）

为什么不用 PowerShell 的 Get-CimInstance：
    本机执行 `Get-CimInstance Win32_Process` 会报
    0x80070534「账户名与安全标识间无任何映射完成」，拿不到命令行；
    更糟的是原脚本报错后仍会打印 [OK]，让人以为已经停干净了，
    结果旧实例还占着微信窗口，新实例一启动就两个抢消息。
    psutil 走 NtQueryInformationProcess 读 PEB，不经过 WMI，
    且对无权访问的进程是单条跳过，不会整体失败。

用法:
    python tools/stop_instances.py            # 停掉所有实例
    python tools/stop_instances.py --dry-run  # 只列出，不杀

退出码:
    0 = 已确认停干净
    1 = 仍有残留
    2 = 环境问题（缺 psutil）
"""
import os
import sys
import time

try:
    import psutil
except ImportError:
    print("[错误] 未安装 psutil，无法精确识别 main.py 实例")
    print("       请执行: pip install psutil")
    sys.exit(2)

PROJECT_MARK = "aigcbd"     # 只杀本项目，避免误伤别的 main.py
MAX_ROUNDS = 12
ROUND_WAIT = 0.8


def find_instances():
    """找出命令行里跑着本项目 main.py 的 python 进程"""
    me = os.getpid()
    hits = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.info["pid"] == me:
                continue
            name = (proc.info["name"] or "").lower()
            if not name.startswith("python"):
                continue
            parts = proc.cmdline()
            if not parts:
                continue
            cmd = " ".join(parts).replace("\\", "/").lower()
            if "main.py" not in cmd:
                continue
            # 绝对路径（含项目目录）或相对路径 src/main.py 都算
            if PROJECT_MARK in cmd or cmd.rstrip().endswith("src/main.py"):
                hits.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            # 别的账户/SYSTEM 的进程读不到命令行，跳过即可。
            # 这正是原 PowerShell 方案整体失败的那类进程。
            continue
    return hits


def main():
    dry = "--dry-run" in sys.argv
    killed = 0

    for round_no in range(1, MAX_ROUNDS + 1):
        procs = find_instances()
        if not procs:
            break
        for proc in procs:
            try:
                pid = proc.pid
                if dry:
                    print(f"      would kill PID {pid}")
                    continue
                print(f"      killed PID {pid}")
                proc.terminate()
                killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if dry:
            break
        # 等这一批真正退出；terminate 不生效的（卡在 C 扩展里）再强杀
        _gone, alive = psutil.wait_procs(procs, timeout=ROUND_WAIT * 2)
        for proc in alive:
            try:
                print(f"      force kill PID {proc.pid}")
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        else:
            time.sleep(ROUND_WAIT)

    left = find_instances()
    if dry:
        print(f"      [DRY] {len(left)} instance(s) running")
        return 0
    if left:
        print(f"      [失败] 仍有 {len(left)} 个实例未能停止: "
              f"{[p.pid for p in left]}")
        print("             请手动在任务管理器结束这些进程后重试")
        return 1
    print(f"      [OK] 已停止 {killed} 个旧实例" if killed
          else "      [OK] 没有正在运行的实例")
    return 0


if __name__ == "__main__":
    sys.exit(main())
