#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单1验证：熔断状态跨进程持久化 + 并发安全（不调用真实 API）。

覆盖场景：
1. 进程 A 记一次超时，进程 B 记一次超时 —— 累计达到阈值后熔断打开；
2. 进程 C 只读，能看到 A/B 共同写出的 open=true（跨进程共享）；
3. 熔断打开时 build_verifier_chain 直接降级为纯 SymPy；
4. 进程 D 记录成功，进程 E 立即看到熔断关闭；
5. 8 个进程并发记录超时，计数不丢失、JSON 不损坏；
6. 状态文件损坏 / 目录缺失时熔断器不抛异常，按未熔断处理。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
AGENT_DIR = BASE / "agent"
STATE_DIR = BASE / "tests" / ".tmp_circuit"
STATE_FILE = STATE_DIR / "circuit_state.json"

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    """记录并打印单条断言结果。"""
    (PASS if condition else FAIL).append(name)
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")


def child_env() -> dict:
    """构造子进程环境：指向隔离的状态文件，并带上占位 Key。"""
    env = dict(os.environ)
    env["MATH_AGENT_CIRCUIT_STATE"] = str(STATE_FILE)
    env["HETERO_CIRCUIT_BREAKER_THRESHOLD"] = "2"
    env.setdefault("DEEPSEEK_API_KEY", "test-key-for-import")
    env["PYTHONPATH"] = str(AGENT_DIR)
    env["PYTHONUTF8"] = "1"
    return env


def run_child(body: str, timeout: int = 120) -> dict:
    """在独立进程中执行熔断操作，结果通过文件回传（不依赖管道解析）。"""
    result_file = STATE_DIR / f"child_{os.getpid()}_{abs(hash(body)) % 100000}.json"
    script = f"""
import json, os, sys
sys.path.insert(0, r"{AGENT_DIR}")
import circuit_breaker, verifier_hetero
breaker = circuit_breaker.PersistentCircuitBreaker()
def out(payload):
    with open(r"{result_file}", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
{body}
"""
    process = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=child_env(), cwd=str(BASE),
    )
    if process.returncode != 0:
        return {"error": process.stderr.strip()[-800:] or "子进程失败"}
    if not result_file.is_file():
        return {"error": "子进程未写出结果文件"}
    try:
        return json.loads(result_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"error": f"结果文件不可读：{exc}"}


def reset_state() -> None:
    """清空隔离状态，保证每个场景从零开始。"""
    for path in (STATE_FILE, STATE_FILE.with_suffix(".json.lock")):
        try:
            path.unlink()
        except OSError:
            pass


def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    reset_state()

    # ---------- 场景1：两个进程各记一次超时 ----------
    first = run_child("""
breaker.record_timeout()
out({"consecutive": breaker.snapshot()["consecutive_timeouts"],
     "open": breaker.is_open(), "threshold": breaker.threshold})
""")
    check("跨进程-进程A记超时成功", "error" not in first, str(first.get("error", "")))
    check("跨进程-单次超时不熔断", first.get("open") is False, f"open={first.get('open')}")

    second = run_child("""
breaker.record_timeout()
snapshot = breaker.snapshot()
out({"consecutive": snapshot["consecutive_timeouts"],
     "open": snapshot["open"],
     "history_tail": snapshot.get("history_tail", [])})
""")
    check("跨进程-进程B累计读到A的计数",
          second.get("consecutive") == 2,
          f"consecutive={second.get('consecutive')}")
    check("跨进程-达到阈值后熔断打开", second.get("open") is True)

    persisted = read_state()
    check("持久化-状态文件记录 open=true", persisted.get("open") is True)
    check("持久化-状态文件记录连续超时次数",
          persisted.get("consecutive_timeouts") == 2,
          f"file={persisted.get('consecutive_timeouts')}")

    # ---------- 场景2：只读进程能看到熔断 ----------
    observer = run_child("""
out({"open": breaker.is_open(), "snapshot": breaker.snapshot()})
""")
    check("跨进程-只读进程看到熔断已打开", observer.get("open") is True)

    # ---------- 场景3：熔断时校验链降级为纯 SymPy ----------
    chain = run_child("""
chain = verifier_hetero.build_verifier_chain(mode="serial")
out({"type": type(chain).__name__, "open": breaker.is_open()})
""")
    check("降级-熔断打开时校验链为 SymPyVerifier",
          chain.get("type") == "SymPyVerifier",
          f"type={chain.get('type')}")

    # ---------- 场景4：成功后跨进程恢复 ----------
    recovered = run_child("""
breaker.record_success()
out({"open": breaker.is_open(),
     "consecutive": breaker.snapshot()["consecutive_timeouts"]})
""")
    check("恢复-成功调用后熔断关闭", recovered.get("open") is False)
    check("恢复-连续超时计数清零", recovered.get("consecutive") == 0,
          f"consecutive={recovered.get('consecutive')}")
    check("恢复-成功事件写入历史",
          any(item.get("event") == "recovered"
              for item in read_state().get("history", [])),
          str(read_state().get("history", [])[-2:]))

    # ---------- 场景5：并发写入不丢计数 ----------
    reset_state()
    workers = 8
    processes = []
    for _ in range(workers):
        script = f"""
import sys
sys.path.insert(0, r"{AGENT_DIR}")
import circuit_breaker
circuit_breaker.PersistentCircuitBreaker().record_timeout()
"""
        processes.append(subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=child_env(), cwd=str(BASE),
        ))
    for process in processes:
        process.wait(timeout=120)
    concurrent_state = read_state()
    check("并发-8 进程超时计数不丢失",
          concurrent_state.get("consecutive_timeouts") == workers,
          f"file={concurrent_state.get('consecutive_timeouts')} expect={workers}")
    check("并发-状态文件仍是合法 JSON", bool(concurrent_state))
    check("并发-熔断保持打开", concurrent_state.get("open") is True)

    # ---------- 场景6：损坏文件与不可写目录的容错 ----------
    STATE_FILE.write_text("{ 这不是合法 JSON", encoding="utf-8")
    broken = run_child("""
try:
    out({"open": breaker.is_open(), "ok": True})
except Exception as exc:
    out({"ok": False, "error": str(exc)})
""")
    check("容错-损坏状态文件不抛异常", broken.get("ok") is True, str(broken))
    check("容错-损坏状态按未熔断处理", broken.get("open") is False)

    missing_dir = STATE_DIR / "missing_sub_dir"
    shutil.rmtree(missing_dir, ignore_errors=True)
    env = child_env()
    env["MATH_AGENT_CIRCUIT_STATE"] = str(missing_dir / "nested" / "state.json")
    forgiving = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, r'%s');"
         "import circuit_breaker;"
         "b = circuit_breaker.PersistentCircuitBreaker();"
         "b.record_timeout(); print('OPEN', b.is_open())" % AGENT_DIR],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env, cwd=str(BASE),
    )
    check("容错-状态目录自动创建且不崩溃",
          forgiving.returncode == 0 and "OPEN" in forgiving.stdout,
          forgiving.stderr.strip()[-300:])

    reset_state()
    print()
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for name in FAIL:
            print(f"  - 失败：{name}")
        return 1
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
