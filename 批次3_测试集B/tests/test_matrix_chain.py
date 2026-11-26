#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单2 回归测试：矩阵连乘跨表示等价规则（全离线）。"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "agent"))

import matrix_chain as mc  # noqa: E402
from verify import verify_latex_vs_code  # noqa: E402

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")


CHAIN3_LHS = r"""\begin{aligned}
D &= A B C \\
d_{ij} &= \sum_{k} \sum_{l} a_{ik} b_{kl} c_{lj}
\end{aligned}"""

CHAIN3_RHS = (
    r"z_{i,j}=\sum_{k=0}^{\operatorname{size}(y)-1}x_{i,k}y_{k,j}"
    r",\quad 0\le i<\operatorname{size}(x),\ 0\le j<\operatorname{size}(y[0])"
    "\n"
    r"\operatorname{chain3}(a,b,c)=\operatorname{mul}(\operatorname{mul}(a,b),c)=(ab)c"
    "\n"
    r"\operatorname{chain3}(a,b,c)_{i,j}=\sum_{\ell=0}^{\operatorname{size}(c)-1}"
    r"\left(\sum_{k=0}^{\operatorname{size}(b)-1}a_{i,k}b_{k,\ell}\right)c_{\ell,j}"
)

CHAIN4_LHS = "E=ABCD"
CHAIN4_RHS = (
    "\\[\nZ=XY,\\quad z_{ij}=\\sum_{k}x_{ik}y_{kj}\n\\]\n"
    "\\[\n\\operatorname{chain4}(A,B,C,D)=(((AB)C)D)\n\\]"
)

ASSOC_LHS = "(AB)C=A(BC)"
ASSOC_RHS = (
    r"Z=XY,\qquad Z_{ij}=\sum_{k=0}^{n-1}X_{ik}Y_{kj}"
    "\n"
    r"L=(AB)C,\qquad L_{ij}=\sum_{k=0}^{p-1}"
    r"\left(\sum_{\ell=0}^{n-1}A_{i\ell}B_{\ell k}\right)C_{kj}"
    "\n"
    r"R=A(BC),\qquad R_{ij}=\sum_{\ell=0}^{n-1}A_{i\ell}"
    r"\left(\sum_{k=0}^{p-1}B_{\ell k}C_{kj}\right)"
)

WRONG_ORDER_LHS = "D = ABC"
WRONG_ORDER_RHS = (
    "\\[\nZ=XY,\\quad z_{ij}=\\sum_{k}x_{ik}y_{kj}\n\\]\n"
    "\\[\n\\operatorname{chainwrong}(A,B,C)=((BC)A)\n\\]"
)


def test_evidence_extraction() -> None:
    print("== 证据提取 ==")
    evidence = mc.extract_matrix_chain_evidence(CHAIN3_LHS)
    check("识别逐元素链乘", len(evidence["elementwise"]) == 1,
          str(evidence["elementwise"])[:80])
    check("识别乘积序列", ("A", "B", "C") in evidence["products"],
          str(evidence["products"]))

    chain4 = mc.extract_matrix_chain_evidence(CHAIN4_RHS)
    check("识别函数签名内的括号求值顺序",
          ("A", "B", "C", "D") in chain4["products"],
          str(chain4["products"]))

    # 二元矩阵乘不进入本规则，避免与既有 matmul 规则重叠。
    two_factor = mc.extract_matrix_chain_evidence("C=AB")
    check("二元矩阵乘不构成链乘证据",
          all(len(item) < 3 for item in two_factor["products"]),
          str(two_factor["products"]))


def test_equivalence() -> None:
    print("== 等价判定 ==")
    chain3 = verify_latex_vs_code(CHAIN3_LHS, CHAIN3_RHS)
    check("chain3 逐元素展开等价",
          chain3.ok and chain3.status == "equivalent"
          and chain3.meta.get("cross_representation_rule")
          == "matrix_chain_elementwise",
          f"ok={chain3.ok}, rule={chain3.meta.get('cross_representation_rule')}")

    chain4 = verify_latex_vs_code(CHAIN4_LHS, CHAIN4_RHS)
    check("chain4 结合律等价",
          chain4.ok and chain4.status == "equivalent"
          and chain4.meta.get("cross_representation_rule")
          == "matrix_chain_associativity",
          f"ok={chain4.ok}, rule={chain4.meta.get('cross_representation_rule')}")

    assoc = verify_latex_vs_code(ASSOC_LHS, ASSOC_RHS)
    check("assoc_paren 结合律等价",
          assoc.ok and assoc.status == "equivalent",
          f"ok={assoc.ok}, status={assoc.status}")


def test_negative() -> None:
    print("== 反例不得误判 ==")
    wrong_order = verify_latex_vs_code(WRONG_ORDER_LHS, WRONG_ORDER_RHS)
    check("(BC)A 与 ABC 判不等价",
          not wrong_order.ok and wrong_order.status == "not_equivalent",
          f"ok={wrong_order.ok}, status={wrong_order.status}")

    assoc_false = verify_latex_vs_code("(AB)C=A(CB)", "L=(AB)C")
    check("公式自身交换律断言不成立时判错",
          not assoc_false.ok and assoc_false.status == "not_equivalent",
          f"ok={assoc_false.ok}, status={assoc_false.status}")

    # 两侧只有二元证据时必须保持“不适用”，把判定权交回既有校验链。
    fallback = mc.try_matrix_chain_equivalence(
        r"c_{ij}=\sum_{k=0}^{n-1}a_{ik}b_{kj}", "C=AB")
    check("二元场景返回 None 交由既有链处理", fallback is None, str(fallback))

    # 错误下标收缩（a_ik b_lk c_lj）不得被判等价。
    bad_indices = mc.try_matrix_chain_equivalence(
        r"d_{ij}=\sum_{k}\sum_{l}a_{ik}b_{lk}c_{lj}",
        r"\operatorname{chain3}(a,b,c)_{i,j}=\sum_{\ell}"
        r"\left(\sum_{k}a_{i,k}b_{k,\ell}\right)c_{\ell,j}")
    check("收缩下标不一致时不得判等价",
          bad_indices is None or not bad_indices.ok,
          str(None if bad_indices is None else (bad_indices.ok, bad_indices.status)))


def main() -> int:
    test_evidence_extraction()
    test_equivalence()
    test_negative()
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
