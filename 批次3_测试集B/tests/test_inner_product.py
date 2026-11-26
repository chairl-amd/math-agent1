#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单2 回归：点积 / 矩阵向量乘语义规则（只证明等价，不误伤）。"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "agent"))

from verify import _try_inner_product_equivalence  # noqa: E402

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")


def main() -> int:
    positive = _try_inner_product_equivalence(
        r"\alpha_k = \frac{r_k \cdot r_k}{p_k \cdot A p_k}",
        r"\[\n\alpha=\frac{ r ^{\top} r }{ p ^{\top} A  p }"
        r"=\frac{\sum_i r_i^2}{\sum_i p_i\,(A p)_i}\n\]",
    )
    check("点积/矩阵向量乘 -> equivalent",
          positive is not None and positive.status == "equivalent",
          "" if positive is None else positive.detail)
    check("命中记录内积规则",
          positive is not None
          and (positive.meta or {}).get("cross_representation_rule")
          == "inner_product_expansion",
          "" if positive is None else str(positive.meta))

    negative = _try_inner_product_equivalence(
        r"\alpha_k = \frac{r_k \cdot r_k}{p_k \cdot A p_k}",
        r"\alpha = \frac{p_k \cdot r_k}{p_k \cdot A p_k}",
    )
    check("分子换成 p·r -> 不命中", negative is None,
          "" if negative is None else negative.status)

    softmax = _try_inner_product_equivalence(
        r"s_i = \frac{\exp(z_i)}{\sum_j \exp(z_j)}",
        r"s_i = \frac{e^{z_i}}{\sum_j e^{z_j}}",
    )
    check("指数/softmax 不进入规则", softmax is None,
          "" if softmax is None else softmax.status)

    ordinary = _try_inner_product_equivalence(
        r"x = \frac{a + b}{c + d}",
        r"x = \frac{a + b}{c + d}",
    )
    check("普通分式不进入规则", ordinary is None,
          "" if ordinary is None else ordinary.status)

    transpose = _try_inner_product_equivalence(
        r"\lambda = \frac{x^\top y}{x^\top x}",
        r"\lambda = \frac{\sum_i x_i y_i}{\sum_i x_i^2}",
    )
    check("转置点积写法可命中",
          transpose is not None and transpose.status == "equivalent",
          "" if transpose is None else transpose.detail)

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
