# -*- coding: utf-8 -*-
"""工单3：偏微分方程（PDE）兜底分析。

背景：SymPy 的 parse_latex 面向代数/微积分表达式，遇到 N-S 方程这类
含 ∂/∇/Δ 与张量并矢的多行 PDE 时无法给出严格符号等价结论。此时不应该
直接丢一个 "unknown"，而应：

1. 识别这是 PDE（而不是普通代数式解析失败）；
2. 把等式两侧按加减号拆项，提取每项的"微分算子签名"（时间导数、
   梯度、散度、拉普拉斯、张量散度等）；
3. 比较两侧签名多重集：一致说明"结构对齐"，可用于人工复核；
4. 无论结论如何，都标记 needs_human_review，把能力边界写清楚。

本模块只做纯文本分析，不依赖 SymPy，保证在解析失败时依然可用。
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional

# PDE 的典型算子记号。
PDE_MARKERS = re.compile(
    r"\\partial|\\nabla|\\Delta\b|\\frac\s*\{\s*\\partial|\\mathrm\s*\{\s*d\s*\}"
)

# 常见二阶/一阶微分结构的归一化签名。
_DIFF_SIGNATURES = [
    # \frac{\partial u}{\partial t} / \frac{\partial}{\partial t} -> dt
    (re.compile(
        r"\\frac\s*\{\s*\\partial[^{}]*\}\s*\{\s*\\partial\s*([A-Za-z])"
        r"(?:_\s*\{?\s*[A-Za-z0-9]+\s*\}?)?\s*\}"), r"d\1"),
    # \partial_t -> dt
    (re.compile(r"\\partial\s*_\s*\{?\s*([A-Za-z])\s*\}?"), r"d\1"),
    # \frac{\partial^2 u}{\partial x^2} -> dxx
    (re.compile(
        r"\\frac\s*\{\s*\\partial\s*\^\s*2[^{}]*\}\s*\{\s*\\partial\s*([A-Za-z])"
        r"\s*\^\s*2\s*\}"), r"d\1\1"),
    # \nabla^2 / \Delta -> lap
    (re.compile(r"\\nabla\s*\^\s*2"), "lap"),
    (re.compile(r"\\Delta(?![A-Za-z])"), "lap"),
    # \nabla\cdot -> div
    (re.compile(r"\\nabla\s*\\cdot"), "div"),
    (re.compile(r"\\nabla\s*\u00b7"), "div"),
    # \nabla -> grad
    (re.compile(r"\\nabla(?![A-Za-z])"), "grad"),
]

# 项内会被忽略的排版噪声（保留 \cdot，它参与 div 判定）。
_NOISE = re.compile(
    r"\\left|\\right|\\,|\\;|\\!|\\quad|\\qquad|"
    r"\\text\{[^{}]*\}|\\mathrm\{([^{}]*)\}|\s+"
)


def is_pde(latex: str) -> bool:
    """判断文本是否包含偏微分方程算子。"""
    return bool(PDE_MARKERS.search(str(latex or "")))


def _strip_noise(text: str) -> str:
    return _NOISE.sub(lambda m: m.group(1) or "", text)


def _split_terms(expression: str) -> list[str]:
    """按顶层 + / - 拆项，跳过括号与花括号内部。"""
    terms: list[str] = []
    depth = 0
    current: list[str] = []
    for index, char in enumerate(expression):
        if char in "({[":
            depth += 1
        elif char in ")}]":
            depth = max(0, depth - 1)
        if depth == 0 and char in "+-" and index > 0:
            text = "".join(current).strip()
            if text:
                terms.append(text)
            current = [char] if char == "-" else []
            continue
        current.append(char)
    text = "".join(current).strip()
    if text:
        terms.append(text)
    return terms


def term_signature(term: str) -> tuple[list[str], str]:
    """返回 (微分算子序列, 归一化后的项体)。

    算子序列只保留微分操作名（dt / dxx / div / lap / grad），按出现顺序；
    项体用于人工核对，不参与严格判定。
    """
    body = term
    operators: list[str] = []
    for pattern, replacement in _DIFF_SIGNATURES:
        def _record(match, pattern=pattern, replacement=replacement):
            operators.append(re.sub(r"\s+", "", match.expand(replacement)))
            return " "

        body = pattern.sub(_record, body)
    body = _strip_noise(body)
    variables = re.findall(r"(?<![A-Za-z])[A-Za-z](?![A-Za-z])", body)
    token = "".join(sorted(set(variables)))
    return operators, token


def residual_signature(latex: str) -> Optional[dict]:
    """把等式两侧合并成残差，返回两侧的算子序列与变量集合。

    lhs = rhs 视为 lhs - rhs = 0；右侧各项移到左侧只改变符号，不改变
    微分算子，因此直接比较两侧算子计数即可反映结构是否一致。
    """
    text = str(latex or "").strip()
    if not text or "=" not in text:
        return None
    # 只取第一个顶层等号，避免把多个等式混在一起。
    left, _, right = text.partition("=")
    left_terms = _split_terms(left)
    right_terms = _split_terms(right)
    if not left_terms and not right_terms:
        return None
    left_ops: list[str] = []
    right_ops: list[str] = []
    for term in left_terms:
        left_ops.extend(term_signature(term)[0])
    for term in right_terms:
        right_ops.extend(term_signature(term)[0])
    return {
        "left_terms": left_terms,
        "right_terms": right_terms,
        "left_operators": left_ops,
        "right_operators": right_ops,
        "term_count": len(left_terms) + len(right_terms),
    }


# ---------- 工单3：逐行残差结构对齐 ----------
_ROW_ENV = re.compile(
    r"\\begin\s*\{(?:aligned|align|gathered|split)\}(.*?)"
    r"\\end\s*\{(?:aligned|align|gathered|split)\}",
    flags=re.DOTALL,
)


def split_pde_rows(latex: str) -> list[tuple[str, str]]:
    """把多行 PDE 拆成 (左端, 右端) 列表，跳过无等号行。"""
    value = str(latex or "")
    match = _ROW_ENV.search(value)
    if match is not None:
        value = match.group(1)
    rows = []
    for raw in re.split(r"\\\\|\r?\n", value):
        row = raw.replace("&", " ")
        row = re.sub(r"\\text\s*\{[^{}]*\}", " ", row)
        row = row.strip(" \t,;")
        if "=" not in row:
            continue
        left, _, right = row.partition("=")
        if left.strip():
            rows.append((left.strip(), right.strip()))
    return rows


def split_signed_terms(expression: str) -> list[tuple[int, str]]:
    """按顶层 +/- 拆项并保留符号，括号内部不拆。"""
    terms: list[tuple[int, str]] = []
    current: list[str] = []
    depth = 0
    sign = 1
    for char in str(expression or ""):
        if char in "({[":
            depth += 1
        elif char in ")}]":
            depth = max(0, depth - 1)
        if depth == 0 and char in "+-":
            text = "".join(current).strip()
            if text:
                terms.append((sign, text))
                current = []
            sign = -1 if char == "-" else 1
            continue
        current.append(char)
    text = "".join(current).strip()
    if text:
        terms.append((sign, text))
    return terms


def _canonical_row_counter(counter: Counter) -> Counter:
    """把整体符号规范化：残差 R 与 -R 视为同一条方程。

    单纯比较正负项数量在 1:1 时无法定符号，因此同时构造取反后的计数，
    选字典序更小的一种表示，保证 R 与 -R 落到同一规范形。
    """
    if not counter:
        return counter
    negated = Counter({
        (operator, -sign): value
        for (operator, sign), value in counter.items()
    })

    def sort_key(items: Counter) -> list[tuple[str, int, int]]:
        return sorted(
            (operator, sign, value)
            for (operator, sign), value in items.items()
        )

    return counter if sort_key(counter) <= sort_key(negated) else negated


def row_residual_counter(equation: str) -> Counter:
    """把一行等式化为残差 lhs-rhs 的带符号算子多重集。"""
    left, _, right = str(equation).partition("=")
    counter: Counter = Counter()
    for sign, term in split_signed_terms(left):
        for operator in term_signature(term)[0]:
            canonical_operator = (
                "div" if operator.startswith("dx") else operator)
            counter[(canonical_operator, sign)] += 1
    for sign, term in split_signed_terms(right):
        for operator in term_signature(term)[0]:
            canonical_operator = (
                "div" if operator.startswith("dx") else operator)
            counter[(canonical_operator, -sign)] += 1
    return _canonical_row_counter(counter)


def compare_pde_rows(latex: str, ref_latex: str) -> list[dict]:
    """逐行比较两侧的残差算子结构，返回只含证据的结果列表。"""
    left_rows = split_pde_rows(latex)
    right_rows = split_pde_rows(ref_latex)
    if not left_rows or not right_rows:
        return []
    results = []
    for index, (left_row, right_row) in enumerate(
            zip(left_rows, right_rows), start=1):
        left_counter = row_residual_counter("=".join(left_row))
        right_counter = row_residual_counter("=".join(right_row))
        results.append({
            "row": index,
            "left_residual": {
                f"{operator}{'+' if sign > 0 else '-'}": count
                for (operator, sign), count in sorted(left_counter.items())
            },
            "right_residual": {
                f"{operator}{'+' if sign > 0 else '-'}": count
                for (operator, sign), count in sorted(right_counter.items())
            },
            "aligned": left_counter == right_counter,
        })
    return results


def analyze(latex: str, ref_latex: str = "") -> dict:
    """对 PDE 用例给出结构化分析与人工复核建议。"""
    result = {
        "is_pde": is_pde(latex) or is_pde(ref_latex),
        "needs_human_review": False,
        "structurally_aligned": None,
        "detail": "",
        "lhs_operators": [],
        "rhs_operators": [],
        # 工单3：逐行残差结构证据，供人工按方程逐条复核。
        "row_alignment": [],
        "row_count": 0,
        "rows_aligned": 0,
    }
    if not result["is_pde"]:
        return result

    result["is_pde"] = True
    result["needs_human_review"] = True
    lhs_info = residual_signature(latex)
    rhs_info = residual_signature(ref_latex)
    lhs_all: list[str] = []
    rhs_all: list[str] = []
    if lhs_info:
        lhs_all = lhs_info["left_operators"] + lhs_info["right_operators"]
        result["lhs_operators"] = lhs_all
    if rhs_info:
        rhs_all = rhs_info["left_operators"] + rhs_info["right_operators"]
        result["rhs_operators"] = rhs_all

    # 张量散度 ∂_{x_i} τ_{ij} 与 \nabla\cdot\tau 视为同一种算子。
    def _canon(operators: list[str]) -> Counter:
        return Counter(
            "div" if item.startswith("dx") else item
            for item in operators
            if item
        )

    lhs_ops = _canon(lhs_all)
    rhs_ops = _canon(rhs_all)
    if lhs_ops and rhs_ops:
        aligned = lhs_ops == rhs_ops
        result["structurally_aligned"] = aligned
        if aligned:
            result["detail"] = (
                "PDE 结构对齐：两侧微分算子集合一致 "
                f"({dict(lhs_ops)})，但连续方程与离散残差之间不存在"
                " SymPy 可判定的代数等价关系，需人工确认。"
            )
        else:
            result["detail"] = (
                "PDE 结构不一致：左侧算子 "
                f"{dict(lhs_ops)}，参考侧算子 {dict(rhs_ops)}，需人工确认。"
            )
    elif lhs_ops:
        result["structurally_aligned"] = None
        result["detail"] = (
            "PDE 可识别算子 "
            f"{dict(lhs_ops)}，但参考侧是离散代码、没有可对比的微分记号，"
            "只能人工确认。"
        )
    else:
        result["structurally_aligned"] = None
        result["detail"] = "PDE 无法完成符号判定（缺少可解析的等式两侧），需人工确认。"

    # 工单3：在整体算子对比之外，再按方程逐行比较“等式 -> 残差=0”的
    # 带符号算子结构。这能定位到具体哪一行对齐，但不改变“连续 PDE 与
    # 离散实现不可符号证明”的能力边界。
    try:
        row_alignment = compare_pde_rows(latex, ref_latex)
    except Exception:  # pragma: no cover - 诊断信息失败不影响主结论
        row_alignment = []
    result["row_alignment"] = row_alignment
    result["row_count"] = len(row_alignment)
    result["rows_aligned"] = sum(
        1 for item in row_alignment if item.get("aligned"))
    if row_alignment:
        aligned_text = (
            f"逐行残差结构：{result['rows_aligned']}/{result['row_count']} "
            "行带符号算子一致")
        result["detail"] = f"{result['detail']} {aligned_text}。"
    return result


__all__ = [
    "analyze",
    "compare_pde_rows",
    "is_pde",
    "residual_signature",
    "row_residual_counter",
    "split_pde_rows",
    "split_signed_terms",
    "term_signature",
]
