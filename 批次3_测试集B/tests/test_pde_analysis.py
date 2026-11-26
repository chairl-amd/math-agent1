#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单3 回归：PDE 逐行残差结构对齐（纯本地，不联网）。"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "agent"))

import pde_analysis as pde  # noqa: E402

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")


ORIGINAL = (
    r"\begin{aligned}"
    r"\frac{\partial}{\partial t}(\rho u)+\nabla\cdot(\rho u u)"
    r"&=-\nabla p+\mu\nabla^2u+\rho g+\frac{\partial}{\partial x_i}(\tau_{ij}),\\"
    r"\nabla\cdot u&=0\\"
    r"\text{Energy: }\rho c_p\left(\frac{\partial T}{\partial t}"
    r"+u\cdot\nabla T\right)&=k\nabla^2T+\Phi"
    r"\end{aligned}"
)
REFERENCE = (
    "r _{ mom } = \\partial_t(\\rho u ) + \\nabla\\cdot(\\rho u \\otimes u ) "
    "+ \\nabla p - \\mu\\Delta u  - \\rho g  - \\nabla\\cdot \\tau \n\n"
    "r_{ cont } = \\nabla\\cdot u \n\n"
    "r_{ energy } = \\rho c_p\\left(\\partial_t T +  u \\cdot\\nabla T\\right) "
    "- k\\Delta T - \\Phi"
)


def main() -> int:
    rows = pde.split_pde_rows(ORIGINAL)
    check("多行 PDE 拆出 3 行", len(rows) == 3, f"rows={len(rows)}")

    terms = pde.split_signed_terms(r"-\nabla p+\mu\nabla^2u")
    check("前导负号保留为负项",
          len(terms) == 2 and terms[0][0] == -1 and terms[1][0] == 1,
          str(terms))

    warm = pde.row_residual_counter(
        r"\partial_t u - \nabla^2 u = 0")
    cold = pde.row_residual_counter(
        r"\nabla^2 u - \partial_t u = 0")
    check("整体符号翻转后残差等价", warm == cold, f"{dict(warm)} vs {dict(cold)}")

    result = pde.analyze(ORIGINAL, REFERENCE)
    check("整体算子结构对齐", result["structurally_aligned"] is True)
    check("逐行识别 3 行", result["row_count"] == 3,
          f"count={result['row_count']}")
    check("逐行残差 3/3 对齐", result["rows_aligned"] == 3,
          f"aligned={result['rows_aligned']}")
    check("仍然要求人工复核", result["needs_human_review"] is True)
    check("明细包含逐行证据", "3/3 行" in result["detail"], result["detail"])

    print(f"\n== 通过 {len(PASS)} / 失败 {len(FAIL)} ==")
    for name in FAIL:
        print(f"  - 失败：{name}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
