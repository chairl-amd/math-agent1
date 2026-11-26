#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单2（w10）：逐例诊断脚本。

从 benchmark 的 per-case 报告里抽取 OCR 结果、参考 LaTeX、校验状态、
仲裁标签与异构校验器意见，用于快速定位根因。

用法：
    python tests/benchmark/diagnose_w10.py --dir output/benchmark_w10_stress1
    python tests/benchmark/diagnose_w10.py --dir <dir> --case noise_l1
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path


def dump_case(path: str, verbose: bool) -> None:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    steps = report.get("steps", {})
    ocr = steps.get("ocr", {}) or {}
    tr = steps.get("translate_cpp", {}) or {}
    print("=" * 78)
    print(f"[{Path(path).parent.name}]  ok={report.get('ok')} elapsed={report.get('elapsed')}")
    print(f"  OCR   : {ocr.get('latex')!r}")
    print(f"  REF   : {tr.get('ref_latex')!r}")
    print(f"  engine: {ocr.get('engine')} / {ocr.get('error') or '-'}")
    for check in steps.get("checks", []) or []:
        meta = check.get("meta", {}) or {}
        print(f"  CHECK : {check.get('status')} | {check.get('detail')}")
        print(f"    kinds={meta.get('verdict_kinds')} label={meta.get('verdict_label')}")
        if verbose:
            print(f"    lhs={check.get('lhs')!r}")
            print(f"    rhs={check.get('rhs')!r}")
            for ev in meta.get("hetero_verdicts", []) or []:
                print(f"    hetero<{ev.get('meta', {}).get('verifier')}> {ev.get('status')}: "
                      f"{(ev.get('detail') or '')[:400]}")
    pde = steps.get("pde_analysis") or {}
    if pde:
        print(f"  PDE   : aligned={pde.get('structurally_aligned')} {str(pde.get('detail'))[:150]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="w10 压测逐例诊断")
    parser.add_argument("--dir", required=True, help="benchmark 输出目录")
    parser.add_argument("--case", default="", help="只看某个 case id")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    root = Path(args.dir)
    files = sorted(glob.glob(str(root / "_benchmark_tmp" / "*" / "*" / "report_*.json")))
    if not files:
        print(f"[错误] {root} 下没有找到 report_*.json", file=sys.stderr)
        return 2
    for path in files:
        if args.case and Path(path).parent.name != args.case:
            continue
        dump_case(path, args.verbose)
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
