#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval.py — 自动化评估：C++ 样例编译运行 + 符号校验用例 + OCR 可用性。

用法：
    python eval.py            # 全量
    python eval.py --quick    # 跳过 math 用例，只测编译

产物：output/eval_report.json / output/eval_report.md
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
AGENT = BASE / "agent"
OUTPUT = BASE / "output"
BIN = OUTPUT / "bin"
sys.path.insert(0, str(AGENT))

import config  # noqa: E402
from verify import verify_latex_vs_code  # noqa: E402

CPP_CASES = ["matrix_mul", "rotate_y_bug", "lu_decomp", "conjugate_gradient"]

SYMBOL_CASES = [
    ("动能方程",      r"K = mc^2",                          "K - m*c**2",          True),
    ("三角恒等式",    r"\sin^2\theta + \cos^2\theta",        "1",                   True),
    ("分式",          r"\frac{a+b}{c-d}",                    "(a+b)/(c-d)",         True),
    ("勾股定理",      r"a^2 + b^2 = c^2",                    "a**2 + b**2 - c**2",  True),
    ("对数乘积",      r"\log(xy) = \log x + \log y",         "log(x*y) - (log(x)+log(y))", True),
    ("平方差",        r"x^2 - 1",                            "(x-1)*(x+1)",         True),
    ("余弦零值",      r"\cos(0)",                            "1",                   True),
    ("根式",          r"\sqrt{x^2 + y^2}",                   "sqrt(x**2 + y**2)",   True),
    ("和式展开",      r"\sum_{i=1}^{n} i",                   "n*(n+1)/2",           True),
    ("反例(不等)",    r"x + 1",                              "x + 2",               False),
]


def run_cpp_cases() -> list[dict]:
    BIN.mkdir(parents=True, exist_ok=True)
    glm = BASE / "third_party" / "glm"
    results = []
    for name in CPP_CASES:
        src = BASE / "tests" / "cpp" / f"{name}.cpp"
        exe = BIN / name
        t0 = time.time()
        comp = subprocess.run(
            ["g++", "-std=c++17", f"-I{glm}", "-O2", str(src), "-o", str(exe)],
            capture_output=True, text=True)
        if comp.returncode != 0:
            results.append({"name": name, "compile_ok": False,
                            "error": comp.stderr[-400:]})
            continue
        try:
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            results.append({"name": name, "compile_ok": True, "exit": run.returncode,
                            "stdout": run.stdout.strip(), "elapsed": round(time.time() - t0, 2)})
        except subprocess.TimeoutExpired:
            results.append({"name": name, "compile_ok": True, "exit": None,
                            "error": "timeout(30s)"})
    return results


def run_symbol_cases() -> list[dict]:
    results = []
    for name, latex, expect, want_ok in SYMBOL_CASES:
        t0 = time.time()
        r = verify_latex_vs_code(latex, expect)
        results.append({"name": name, "latex": latex, "expect": expect,
                        "ok": r.ok, "expected": want_ok, "pass": r.ok == want_ok,
                        "status": r.status, "detail": r.detail,
                        "elapsed": round(time.time() - t0, 3)})
    return results


def check_ocr() -> dict:
    info = {"pix2text_installed": False, "model_loadable": False, "error": ""}
    try:
        import pix2text  # noqa: F401
        info["pix2text_installed"] = True
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {e}"
        return info
    return info


def build_markdown(report: dict) -> str:
    cpp_ok = sum(1 for c in report["cpp"] if c.get("compile_ok"))
    sym_ok = sum(1 for c in report["symbol"] if c.get("pass"))
    lines = ["# math-agent 评估报告", "",
             f"- 时间：{report['timestamp']}",
             f"- C++ 样例编译成功：{cpp_ok}/{len(report['cpp'])}",
             f"- 符号校验通过：{sym_ok}/{len(report['symbol'])}",
             f"- 总耗时：{report['elapsed']}s", "",
             "## C++ 样例", "",
             "| 样例 | 编译 | 退出码 | 输出 |", "|---|---|---|---|"]
    for c in report["cpp"]:
        lines.append(f"| {c['name']} | {'✅' if c.get('compile_ok') else '❌'} | "
                     f"{c.get('exit', '-')} | `{c.get('stdout', c.get('error', ''))[:80]}` |")
    lines += ["", "## 符号校验", "",
              "| 用例 | 结果 | 状态 | 说明 |", "|---|---|---|---|"]
    for s in report["symbol"]:
        lines.append(f"| {s['name']} | {'✅' if s.get('pass') else '❌'} | {s['status']} | {s['detail'][:70]} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--quick", action="store_true", help="跳过符号用例")
    args = p.parse_args(argv)
    t0 = time.time()
    report = {"timestamp": datetime.now().isoformat(timespec="seconds"),
              "cpp": run_cpp_cases()}
    report["symbol"] = run_symbol_cases() if not args.quick else []
    report["ocr"] = check_ocr()
    report["elapsed"] = round(time.time() - t0, 2)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "eval_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = build_markdown(report)
    (OUTPUT / "eval_report.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())