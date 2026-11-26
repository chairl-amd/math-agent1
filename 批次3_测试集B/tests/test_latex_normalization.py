#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单2/3 回归测试：LaTeX 归一化、PDE 兜底、非公式判定（全部离线）。

覆盖：
1. \sum_i 这类简写补全上下限后可被 SymPy 解析；
2. 0-based 与 1-based 求和的等价性由上层规则处理；
3. 迭代下标 w_{t+1}/w_t 归一化后与 w 同形；
4. PDE 结构分析能识别 N-S 方程并对齐算子；
5. 非公式文本（中文说明、空 JSON 包装）被正确识别。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "agent"))
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-for-import")

import pde_analysis  # noqa: E402
import symbol_norm  # noqa: E402
from verify import latex_to_expr  # noqa: E402

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")


def test_sum_limits() -> None:
    print("== 求和上下限补全 ==")
    cases = [
        (r"\sum_i a_i b_i", r"\sum_{i=0}^{n-1} a_i b_i"),
        (r"\sum_{i} a_i b_i", r"\sum_{i=0}^{n-1} a_i b_i"),
        (r"\sum_{k=0}^{n-1} a_k", r"\sum_{k=0}^{n-1} a_k"),
        (r"\prod_k x_k", r"\prod_{k=0}^{n-1} x_k"),
    ]
    for source, expected in cases:
        actual = symbol_norm.normalize_sum_limits(source)
        check(f"归一化 {source}", actual == expected, f"-> {actual}")

    # 非纯索引的下标不应被改写。
    guarded = r"\sum_{i \in S} x_i"
    check("含集合下标的求和保持原样",
          symbol_norm.normalize_sum_limits(guarded) == guarded)

    # 补全后 SymPy 必须能解析。
    expr, error = latex_to_expr(r"s = \sum_i a_i \cdot b_i")
    check("补全后 SymPy 可解析", expr is not None, error[:80])
    check("补全后表达式含 Sum",
          expr is not None and "Sum" in str(expr), str(expr))


def test_iteration_subscripts() -> None:
    print("== 迭代下标归一化 ==")
    cases = [
        (r"w_{t+1}=w_t-\eta g_t", r"w=w-\eta g"),
        (r"x_{k+1}=x_k+\Delta", r"x=x_k+\Delta"),
        (r"a_i + b_{kj}", r"a_i + b_{kj}"),
        (r"\alpha_k", r"\alpha_k"),
    ]
    for source, expected in cases:
        actual = symbol_norm.normalize_iteration_subscripts(source)
        check(f"归一化 {source}", actual == expected, f"-> {actual}")

    lhs, _ = latex_to_expr(r"w_{t+1}=w_t-\eta g_t")
    rhs, _ = latex_to_expr(r"w=w-\eta g")
    check("迭代写法与简写解析结果一致",
          lhs is not None and rhs is not None and lhs.equals(rhs),
          f"{lhs} vs {rhs}")


def test_pde_analysis() -> None:
    print("== PDE 兜底分析 ==")
    lhs = (
        r"\frac{\partial}{\partial t}(\rho u) + \nabla \cdot (\rho u u) = "
        r"-\nabla p + \mu \nabla^2 u + \rho g + "
        r"\frac{\partial}{\partial x_i}(\tau_{ij})"
    )
    rhs = (
        r"r_{mom} = \partial_t(\rho u) + \nabla\cdot(\rho u \otimes u) + "
        r"\nabla p - \mu\Delta u - \rho g - \nabla\cdot \tau"
    )
    result = pde_analysis.analyze(lhs, rhs)
    check("识别 N-S 方程为 PDE", result["is_pde"] is True)
    check("PDE 要求人工复核", result["needs_human_review"] is True)
    check("PDE 算子结构对齐", result["structurally_aligned"] is True,
          str(result.get("detail", ""))[:100])
    check("PDE 算子包含时间导数与散度",
          "dt" in result["lhs_operators"] and "div" in result["lhs_operators"],
          str(result["lhs_operators"]))

    heat = pde_analysis.analyze(
        r"\frac{\partial u}{\partial t} = \alpha \frac{\partial^2 u}{\partial x^2}",
        r"u_i = u_i + alpha dt (u_{i+1} - 2u_i + u_{i-1}) / dx^2")
    check("热方程识别为 PDE", heat["is_pde"] is True)
    check("离散参考侧无法结构对齐时标记人工复核",
          heat["needs_human_review"] is True
          and heat["structurally_aligned"] is None)

    plain = pde_analysis.analyze(r"c_{ij}=\sum_k a_{ik}b_{kj}", r"C=AB")
    check("普通代数式不误判为 PDE", plain["is_pde"] is False)


def test_non_formula() -> None:
    print("== 非公式文本识别 ==")
    from main import _looks_like_non_formula

    cases = [
        ("", True, "空文本"),
        ("   ", True, "纯空白"),
        ("这是一段中文说明文字，不是公式", True, "中文说明"),
        ('json\n{"result": ""}', True, "空 JSON 包装"),
        ('{"result": "s = \\\\sum_i a_i"}', False, "JSON 中的公式"),
        (r"s = \sum_{i=0}^{n-1} a_i b_i", False, "正常公式"),
        (r"\frac{\partial u}{\partial t} = \alpha \nabla^2 u", False, "PDE 公式"),
        ("`` (注：根据要求，输出空，此处为思考过程) -> 空字符串。", True,
         "元思考文本"),
    ]
    for text, expected, label in cases:
        actual = _looks_like_non_formula(text)
        check(f"非公式判定: {label}", actual == expected, f"-> {actual}")


def main() -> int:
    test_sum_limits()
    test_iteration_subscripts()
    test_pde_analysis()
    test_non_formula()
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
