#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单2/3 诊断：汇总 benchmark 未命中用例的 OCR、翻译与校验证据。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
RESULT = BASE / "output" / "benchmark" / "benchmark_result.json"
TMP_ROOT = BASE / "output" / "benchmark" / "_benchmark_tmp"


def latest_report(case_id: str) -> dict:
    """取该用例最近一次子进程报告（run_benchmark 在 _benchmark_tmp/<时间戳>/ 下留档）。"""
    candidates = list(TMP_ROOT.glob(f"*/{case_id}/report_*.json"))
    if not candidates:
        candidates = list(TMP_ROOT.glob(f"{case_id}/report_*.json"))
    if not candidates:
        return {}
    try:
        return json.loads(
            sorted(candidates, key=lambda p: p.stat().st_mtime)[-1]
            .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def summarize(report: dict) -> dict:
    steps = report.get("steps") or {}
    latex_step = steps.get("latex") or {}
    ocr_step = steps.get("ocr") or {}
    translate = steps.get("translate_cpp") or {}
    checks = steps.get("checks") or []
    check = next((c for c in checks if c.get("name") == "latex_vs_cpp"), {})
    meta = check.get("meta") or {}
    return {
        "ocr_latex": latex_step.get("text") or ocr_step.get("latex") or "",
        "ocr_source": latex_step.get("source") or ocr_step.get("engine") or "",
        "ref_latex": translate.get("ref_latex") or "",
        "status": check.get("status") or "",
        "detail": (check.get("detail") or "")[:160],
        "lhs": (check.get("lhs") or "")[:160],
        "rhs": (check.get("rhs") or "")[:160],
        "verdict_label": meta.get("verdict_label") or "",
        "cache": (check.get("meta") or {}).get("cache") or "",
        "error": report.get("error") or "",
    }


def main() -> int:
    if not RESULT.is_file():
        print(f"缺少结果文件：{RESULT}", file=sys.stderr)
        return 2
    data = json.loads(RESULT.read_text(encoding="utf-8"))
    cases = data.get("cases") or []
    misses = [case for case in cases if not case.get("hit")]
    print(f"总用例 {len(cases)}，未命中 {len(misses)}")
    print("=" * 100)
    for case in misses:
        report = latest_report(case["id"])
        info = summarize(report)
        print(f"[{case['id']}] 预期={case['expected']} 实际={case['actual']}")
        print(f"  OCR  : {info['ocr_latex']!r}  (source={info['ocr_source']})")
        print(f"  参考 : {info['ref_latex']!r}")
        print(f"  状态 : {info['status']}  cache={info['cache']}")
        print(f"  详情 : {info['detail']}")
        if info["lhs"] or info["rhs"]:
            print(f"  lhs  : {info['lhs']}")
            print(f"  rhs  : {info['rhs']}")
        print("-" * 100)
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
