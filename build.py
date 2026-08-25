# -*- coding: utf-8 -*-
"""本地一键打包 —— PyInstaller 打包为单 exe。

用法: python build.py
产物: dist/md2feishu2md.exe（--onefile --windowed）
"""
import os
import shutil
import subprocess
import sys


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    # 清理旧产物（ignore_errors：若旧 exe 仍被运行占用则跳过，避免打包中断）
    for d in ("build", "dist"):
        p = os.path.join(root, d)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
    spec = os.path.join(root, "md2feishu2md.spec")
    if os.path.isfile(spec):
        os.remove(spec)

    # Windows 下 --add-data 用分号分隔源:目标；其它平台用冒号
    sep = ";" if os.name == "nt" else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--windowed",
        "--name", "md2feishu2md",
        "--add-data", f"ui{sep}ui",
        # clr_loader（pythonnet）加载 .NET 依赖 cffi/pycparser，PyInstaller 静态分析易漏，显式收集
        "--collect-all", "cffi",
        "--collect-all", "pycparser",
        "--clean", "--noconfirm",
        os.path.join(root, "app.py"),
    ]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True, cwd=root)
    exe = os.path.join(root, "dist", "md2feishu2md.exe")
    print(f"\n打包完成: {exe}")


if __name__ == "__main__":
    main()
