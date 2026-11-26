#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_demo.py — 可移植启动器（本轮新增，不改动任何现有源码）。

它做的事只有四件：
1. 把自己所在目录当成项目根（可用 MATH_AGENT_ROOT 覆盖），并 chdir 过去；
2. 读取同级 .env，把 KEY=VALUE 注入 os.environ（不依赖 python-dotenv）；
3. 检查解释器与依赖是否就位，给出人话提示；
4. 用当前解释器拉起 web/app.py（Gradio 界面）。

用法：
    python run_demo.py                # 启动，默认 127.0.0.1:7860
    python run_demo.py --check        # 只体检，不启动
    python run_demo.py --open         # 启动并自动打开浏览器
    python run_demo.py --port 7861    # 换端口
    python run_demo.py --no-env       # 忽略 .env，只用系统环境变量

退出码：0 正常；2 环境不满足；web 进程的退出码原样透传。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

REQUIRED_MODULES = [
    ("gradio", "gradio"),
    ("openai", "openai"),
    ("sympy", "sympy"),
    ("PIL", "pillow"),
]


def project_root() -> Path:
    """项目根：优先 MATH_AGENT_ROOT，否则脚本所在目录。"""
    env_root = os.environ.get("MATH_AGENT_ROOT", "").strip()
    if env_root:
        p = Path(env_root).expanduser()
        if p.is_dir():
            return p.resolve()
        print(f"[WARN] MATH_AGENT_ROOT 不存在，忽略：{p}")
    return Path(__file__).resolve().parent


def load_env(path: Path) -> int:
    """把 .env 里的 KEY=VALUE 注入 os.environ，返回注入条目数。

    规则与 start.bat 保持一致：忽略空行与 # 注释；去掉值两侧空白和引号；
    已存在的系统环境变量优先（不覆盖）。
    """
    if not path.is_file():
        return 0
    count = 0
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or not value:
            continue
        if key in os.environ:
            continue
        os.environ[key] = value
        count += 1
    return count


def check_deps() -> list[str]:
    """返回缺失的第三方包名列表。"""
    missing: list[str] = []
    for module, dist in REQUIRED_MODULES:
        try:
            __import__(module)
        except Exception:
            missing.append(dist)
    return missing


def print_status(root: Path, app: Path, host: str, port: int, env_count: int) -> None:
    print(f"[CHECK] root      = {root}")
    print(f"[CHECK] python    = {sys.executable}")
    print(f"[CHECK] version   = {sys.version.split()[0]}")
    print(f"[CHECK] app       = {app} ({'ok' if app.is_file() else 'MISSING'})")
    print(f"[CHECK] .env      = {env_count} 项注入")
    print(f"[CHECK] 主模型Key = {'已配置' if os.environ.get('DEEPSEEK_API_KEY', '').strip() else '缺失'}")
    print(f"[CHECK] Qwen Key  = {'已配置' if (os.environ.get('QWEN_API_KEY') or os.environ.get('DASHSCOPE_API_KEY')) else '缺失（可回退）'}")
    print(f"[CHECK] 地址      = http://{host}:{port}")
    missing = check_deps()
    print(f"[CHECK] 依赖      = {'全部就位' if not missing else '缺失: ' + ', '.join(missing)}")
    if missing:
        print("        安装：pip install -r requirements.txt")


def wait_http(url: str, timeout: float = 20.0) -> bool:
    """轮询直到 Web 返回任意 HTTP 响应（用来给 --open 找时机）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except urllib.error.HTTPError:
            return True  # 有 HTTP 响应就算起来了
        except Exception:
            time.sleep(0.5)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="math-agent 可移植启动器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("MATH_AGENT_PORT", "7860")))
    parser.add_argument("--check", action="store_true", help="只体检，不启动")
    parser.add_argument("--open", action="store_true", help="启动后自动开浏览器")
    parser.add_argument("--no-env", action="store_true", help="忽略 .env")
    args = parser.parse_args(argv)

    root = project_root()
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    env_count = 0 if args.no_env else load_env(root / ".env")
    app = root / "web" / "app.py"

    print_status(root, app, args.host, args.port, env_count)
    if args.check:
        ok = app.is_file() and not check_deps()
        print("[CHECK] 结论：" + ("可以启动" if ok else "还差东西，见上面"))
        return 0 if ok else 2

    if not app.is_file():
        print(f"[ERROR] 找不到 {app}")
        print("        请把 run_demo.py 放在 math-agent 项目根目录下。")
        return 2
    missing = check_deps()
    if missing:
        print(f"[ERROR] 缺少依赖：{', '.join(missing)}")
        print("        请先执行：pip install -r requirements.txt")
        return 2
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        print("[WARN] 没有 DEEPSEEK_API_KEY：界面能开，但点校验会报模型错误。")
        print("       把 .env.example 复制成 .env，填上你自己的 Key。")
    if not os.environ.get("DASHSCOPE_API_KEY", "").strip() and \
            not os.environ.get("QWEN_API_KEY", "").strip():
        print("[INFO] 没有 Qwen Key：跳过第二意见，只走 SymPy 主链。")

    cmd = [sys.executable, str(app), "--host", args.host, "--port", str(args.port)]
    print(f"[INFO] 启动：{' '.join(cmd)}")
    print(f"[INFO] 地址：http://{args.host}:{args.port}   （Ctrl+C 停止）")
    print(flush=True)

    if args.open:
        proc = subprocess.Popen(cmd)
        if wait_http(f"http://{args.host}:{args.port}/"):
            webbrowser.open(f"http://{args.host}:{args.port}/")
        return proc.wait()
    return subprocess.call(cmd)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
