#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单2（w10）：新增规则层的离线回归测试（不联网）。

覆盖：
1. symbol_norm.normalize_notation_variants：\\over、\\operatorname/\\text 包装、
   \\mathsf{T} 转置记号的正规化；
2. symbol_norm.normalize_subscript_commas：a_{i,i} -> a_{ii}；
3. verify.latex_to_expr：\\left| ... \\right| 不再导致解析失败；
4. verifier_hetero.aggregate_verdicts：符号层能力缺失 + 异构一致等价的仲裁，
   以及 PDE 安全阀、异构不一致、开关关闭三种反向场景。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 测试不需要真实网络，但 config 要求存在 Key；这里给占位值，避免读用户环境。
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-not-used")

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from agent import config  # noqa: E402
from agent.symbol_norm import (  # noqa: E402
    normalize_notation_variants,
    normalize_subscript_commas,
)
from agent.verify import CheckResult, compare_expressions, latex_to_expr  # noqa: E402
from agent.verifier_hetero import aggregate_verdicts  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"[PASS] {name} {detail}")
    else:
        FAILED += 1
        print(f"[FAIL] {name} {detail}")


def test_notation_variants() -> None:
    cases = [
        (r"{a \over b}", r"\frac{a}{b}"),
        (r"\operatorname{tr}(A)", r"\mathrm{tr}(A)"),
        (r"\text{Trace}(A)", r"\mathrm{tr}(A)"),
        (r"\mathrm{Det}(A)", r"\mathrm{det}(A)"),
        (r"x^{\mathsf{T}}x", r"x^{\mathrm{T}}x"),
        (r"\alpha = {r \cdot r \over p \cdot A p}",
         r"\alpha = \frac{r \cdot r}{p \cdot A p}"),
    ]
    for source, expected in cases:
        got = normalize_notation_variants(source)
        check(f"记号变体 {source!r}", got == expected, f"-> {got!r}")

    # 未登记的算子只统一外层命令，不吞掉名字。
    check(
        "未登记算子保留名字",
        normalize_notation_variants(r"\operatorname{erf}(x)") == r"\mathrm{erf}(x)",
    )


def test_subscript_commas() -> None:
    check("下标逗号", normalize_subscript_commas(r"a_{i,i}") == r"a_{ii}")
    check("多指标逗号", normalize_subscript_commas(r"a_{i,j}") == r"a_{ij}")
    check("不误伤函数参数",
          normalize_subscript_commas(r"f(x,y)=x_{k}") == r"f(x,y)=x_{k}")
    check("不误伤求和条件",
          normalize_subscript_commas(r"\sum_{k<i} L_{ik}") == r"\sum_{k<i} L_{ik}")


def test_left_right_parse() -> None:
    expr, error = latex_to_expr(r"\|x\|_1=\sum_{i=0}^{n-1}\left|x_i\right|")
    check("\\left|...\\right| 可解析", expr is not None, f"err={error[:80]}")
    if expr is not None:
        other, _ = latex_to_expr(r"\left\|\mathbf{x}\right\|_1=\sum_{i=1}^{n}|x_i|")
        check("范数写法两侧等价", other is not None)
        if other is not None:
            result = compare_expressions(expr, other)
            check("范数两侧符号等价", result.ok, result.detail[:80])


def _hetero(status: str, *, network: bool = True) -> dict:
    return {
        "status": status,
        "meta": {"verifier": "qwen-vl", "network_used": network},
    }


def _sympy(status: str) -> CheckResult:
    return CheckResult(
        status == "equivalent", status, "lhs", "rhs", "sympy",
        meta={"verifier": "sympy"})


def _hetero_result(status: str) -> CheckResult:
    return CheckResult(
        status == "equivalent", status, "lhs", "rhs", "hetero",
        meta={"verifier": "qwen-vl", "network_used": True},
    )


def test_consensus_policy() -> None:
    original = config.HETERO_ACCEPT_CONSENSUS
    try:
        config.HETERO_ACCEPT_CONSENSUS = True

        # 1) 符号层未知 + 异构等价 + 非 PDE -> 采信等价
        result = aggregate_verdicts([
            _sympy("unknown"), _hetero_result("equivalent")])
        check("能力缺失时采信异构等价",
              result.status == "equivalent" and result.ok,
              f"{result.status}/{result.meta.get('accepted_by')}")
        check("仲裁结果带标注",
              result.meta.get("accepted_by") == "hetero_consensus")

        # 2) PDE -> 不采信（image_04 场景）
        pde_sympy = CheckResult(
            False, "unknown",
            r"\frac{\partial u}{\partial t} = \alpha \nabla^2 u",
            "rhs", "sympy", meta={"verifier": "sympy"})
        result = aggregate_verdicts([pde_sympy, _hetero_result("equivalent")])
        check("PDE 不升级为等价", result.status == "unknown", result.status)

        # 3) 异构不一致 -> 保持 not_equivalent
        result = aggregate_verdicts([
            _sympy("unknown"), _hetero_result("not_equivalent")])
        check("异构判错仍判错", result.status == "not_equivalent", result.status)

        # 4) 冲突 -> needs_human_review
        result = aggregate_verdicts([
            _sympy("equivalent"), _hetero_result("not_equivalent")])
        check("冲突需人工复核", result.status == "needs_human_review", result.status)

        # 5) 离线条目（network_used=False）不采信
        offline = CheckResult(
            True, "equivalent", "lhs", "rhs", "stub",
            meta={"verifier": "qwen-vl", "network_used": False})
        result = aggregate_verdicts([_sympy("unknown"), offline])
        check("离线结果不采信", result.status == "unknown", result.status)

        # 6) 开关关闭 -> 退回严格共识
        config.HETERO_ACCEPT_CONSENSUS = False
        result = aggregate_verdicts([
            _sympy("unknown"), _hetero_result("equivalent")])
        check("开关关闭退回 unknown", result.status == "unknown", result.status)
    finally:
        config.HETERO_ACCEPT_CONSENSUS = original


def main() -> int:
    test_notation_variants()
    test_subscript_commas()
    test_left_right_parse()
    test_consensus_policy()
    print(f"\n== 通过 {PASSED} / 失败 {FAILED} ==")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
