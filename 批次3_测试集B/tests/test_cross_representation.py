#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""矩阵乘跨表示等价规则与 OCR 求和边界审计的离线回归测试。"""
from __future__ import annotations

import sys
import struct
import types
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "agent"))


def install_temporary_ctypes_compat() -> None:
    """当前便携运行时的 _ctypes 不可用时，补足 SymPy 所需定义。"""
    try:
        import ctypes  # noqa: F401
        return
    except Exception:
        sys.modules.pop("ctypes", None)
        ctypes_module = types.ModuleType("ctypes")
        ctypes_module.c_long = int
        ctypes_module.sizeof = lambda value: struct.calcsize("l")
        sys.modules["ctypes"] = ctypes_module


install_temporary_ctypes_compat()

from verify import (  # noqa: E402
    _try_cross_representation_equivalence,
    verify_latex_vs_code,
)


PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")


IMAGE_06_LHS = r"""\left(
\begin{array}{l}
C=AB\\[2pt]
c_{ij}=\sum\limits_{k} a_{ik}b_{kj}\\[2pt]
(i,j\ \text{fixed})
\end{array}
\right)"""

IMAGE_06_RHS = r"""n=\#\operatorname{rows}(A),\quad m=\begin{cases}\#\operatorname{cols}(B),&B\neq\varnothing\\0,&B=\varnothing\end{cases},\quad p=\#\operatorname{rows}(B)
C_{ij}=\sum_{k=0}^{p-1} A_{ik}B_{kj},\quad 0\le i<n,\ 0\le j<m"""

IMAGE_03_LHS = r"""\begin{aligned}
A &= LU\\
L_{ii} &= 1\\
U_{ij} &= A_{ij}-\sum L_{ik}U_{kj}\\
L_{ij} &= \frac{A_{ij}-\sum L_{ik}U_{kj}}{U_{jj}}\\
k &< j
\end{aligned}"""

IMAGE_03_RHS = r"""0\le i,j<n
L_{ii}=1,\quad L_{ij}=0\ (i<j),\quad U_{ij}=0\ (i>j)
s_{ij}=\sum_{k=0}^{j-1}L_{ik}U_{kj}
U_{ij}= A_{ij}-s_{ij}\quad(i\le j)
L_{ij}=\frac{A_{ij}-s_{ij}}{U_{jj}}\quad(i>j)
U_{jj}\neq0\quad(i>j)
A=LU"""

CONV_SHIFT_LHS = r"""y_i=\sum_{k=0}^{i-1}x_{i-k}h_k"""

CONV_SHIFT_RHS = r"""N=\operatorname{output\_size}
y_i=\sum_{k=0}^{N-1} 1 _{k+1<i}\,x_{i-k}h_k,\quad i=0,\ldots,N-1
A_{i,k}= 1 _{k+1<i}\,x_{i-k},\quad i,k=0,\ldots,N-1
y = A  h ,\qquad y_i=\sum_{k=0}^{N-1}A_{i,k}h_k"""


def main() -> int:
    image_06 = verify_latex_vs_code(IMAGE_06_LHS, IMAGE_06_RHS)
    check(
        "image_06 跨表示等高置信度等价",
        image_06.ok
        and image_06.status == "equivalent"
        and image_06.meta.get("cross_representation_rule") == "matmul_elementwise"
        and image_06.meta.get("verdict_label") == "HIGH_CONFIDENCE",
        f"ok={image_06.ok}, status={image_06.status}, meta={image_06.meta}",
    )

    incomplete_bounds = verify_latex_vs_code(
        r"c_{ij}=\sum_{k=0}^{n-1}a_{ik}b_{kj}",
        r"C_{ij}=\sum_{k=0}^{n-2} A_{ik}B_{kj}",
    )
    check(
        "收缩维上界缺失时不得判等价",
        not incomplete_bounds.ok,
        f"ok={incomplete_bounds.ok}, status={incomplete_bounds.status}",
    )

    image_03 = verify_latex_vs_code(IMAGE_03_LHS, IMAGE_03_RHS)
    check(
        "image_03 保持不等价",
        image_03.status == "not_equivalent",
        f"ok={image_03.ok}, status={image_03.status}",
    )

    conv_shift = verify_latex_vs_code(CONV_SHIFT_LHS, CONV_SHIFT_RHS)
    check(
        "conv_shift 保持不等价",
        conv_shift.status == "not_equivalent",
        f"ok={conv_shift.ok}, status={conv_shift.status}",
    )

    one_sided = _try_cross_representation_equivalence(
        r"c_{ij}=\sum_{k=0}^{p-1} a_{ik}b_{kj}",
        r"Z=WX",
    )
    check(
        "单侧逐元素声明不进入跨表示规则",
        one_sided is None,
        f"result={one_sided}",
    )

    print(f"\n== 通过 {len(PASS)} / 失败 {len(FAIL)} ==")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
