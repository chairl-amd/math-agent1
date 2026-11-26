# -*- coding: utf-8 -*-
"""工单1：参考公式（C++ -> LaTeX）的表示归一化。

问题：翻译层经常输出“代码结构说明”而不是数学表达式，典型三种形态：

1. 引入中间变量：
       T = \\sum_{j=1}^{n}\\exp(z_j)
       s_i = \\frac{\\exp(z_i)}{T}
   图片侧却是单行 ``s_i = exp(z_i)/Σexp(z_j)``，直接比较必然不等。

2. 输出函数签名（把函数调用当成等式左端）：
       \\operatorname{chain4}(A,B,C,D) = (((AB)C)D)
   SymPy 无法把 ``\\operatorname{...}(...)`` 当作矩阵表达式。

3. 多行等式，真正可比的是右端：
       d_{ij} = \\sum_k\\sum_l a_{ik}b_{kl}c_{lj}
   与图片侧另一行的右端比较才有意义。

本模块只做纯文本变换，不调用模型、不依赖 SymPy。
"""

from __future__ import annotations

import re
from typing import List, Tuple

# 单符号定义行：T = ...
_DEFINITION = re.compile(r"^\s*([A-Za-z])\s*=\s*(.+?)\s*$")
# 代码函数签名：\operatorname{name}(args) 或 \operatorname{name}(args)_{i,j}
_FUNCTION_SIGNATURE = re.compile(
    r"^\s*\\operatorname\{[^{}]+\}"
    r"\s*(?:\([^{}]*\))?"
    r"(?:\s*_\s*(?:\{[^{}]*\}|[A-Za-z0-9]+))?"
    r"\s*=\s*"
)
# 行内噪声（对齐标记、公式环境包裹）。
_LINE_NOISE = re.compile(
    r"^\s*&|&(?=\s)|\\\\\s*$|^\s*\\\[|\\\]\s*$|"
    r"^\s*\\begin\{[^{}]+\}|^\s*\\end\{[^{}]+\}"
)


def _clean_line(line: str) -> str:
    text = _LINE_NOISE.sub("", line).strip()
    return text.strip("&").strip()


def _top_level_equals(text: str) -> List[int]:
    """返回不在 ``{}`` 内部的等号位置（``\\sum_{j=1}^{n}`` 里的等号不算）。"""
    positions: List[int] = []
    depth = 0
    for index, char in enumerate(text):
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif char == "=" and depth == 0:
            positions.append(index)
    return positions


def _has_top_level_equals(text: str) -> bool:
    return bool(_top_level_equals(text))


def split_math_lines(text: str) -> List[str]:
    """把多行 LaTeX 拆成非空、去噪后的逻辑行。"""
    lines: List[str] = []
    for raw in str(text or "").splitlines():
        cleaned = _clean_line(raw)
        if not cleaned:
            continue
        # 去掉行尾逗号/句点这类分隔符。
        cleaned = cleaned.rstrip(",;")
        if cleaned:
            lines.append(cleaned)
    return lines


def _is_plain_symbol(expression: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z]", expression.strip()))


def expand_intermediate_variables(text: str, max_rounds: int = 3) -> str:
    """把“单符号 = 表达式”的定义代入后续引用它的行。

    只处理单字母、右端不含等号、且右端不是纯符号的定义，避免误伤
    ``A = LU`` 这类双边都有语义的等式。原始行结构（``\\[``、``\\]``、
    环境标记）保持不变，只替换被引用的符号。
    """
    if not isinstance(text, str) or not text.strip():
        return ""

    raw_lines = text.splitlines()
    definitions = {}
    for raw in raw_lines:
        cleaned = _clean_line(raw)
        positions = _top_level_equals(cleaned)
        # 定义行必须恰好一个顶层等号，且左侧是单字母符号。
        if len(positions) != 1:
            continue
        split_at = positions[0]
        name = cleaned[:split_at].strip()
        value = cleaned[split_at + 1:].strip()
        if not re.fullmatch(r"[A-Za-z]", name):
            continue
        # 右端再出现顶层等号（链式等式）或只有纯符号时都不代入。
        if _has_top_level_equals(value) or _is_plain_symbol(value) or not value:
            continue
        definitions[name] = value

    if not definitions:
        return text

    lines = list(raw_lines)
    patterns = {
        name: re.compile(rf"(?<![A-Za-z\\]){re.escape(name)}(?![A-Za-z])")
        for name in definitions
    }
    for _ in range(max(1, max_rounds)):
        changed = False
        for index, line in enumerate(lines):
            if len(_top_level_equals(_clean_line(line))) == 1 and re.fullmatch(
                    r"[A-Za-z]", _clean_line(line).split("=")[0].strip()):
                continue
            updated = line
            for name, value in definitions.items():
                if patterns[name].search(updated):
                    # 用函数式替换，避免 LaTeX 里的 \s、\e 被当成转义序列。
                    updated = patterns[name].sub(
                        lambda _match, replacement="{" + value + "}": replacement,
                        updated)
                    changed = True
            lines[index] = updated
        if not changed:
            break
    return "\n".join(lines)


def strip_function_signatures(text: str) -> str:
    """剥离 ``\\operatorname{f}(...)=`` 前缀，只保留数学右侧。

    链式等式（``f(a)=g(a)=expr``）取最后一个等号右侧，因为那才是最终表达式。
    """
    if not isinstance(text, str) or not text.strip():
        return ""
    output = []
    for line in split_math_lines(text):
        stripped = _FUNCTION_SIGNATURE.sub("", line).strip()
        if not stripped:
            output.append(line)
            continue
        if "=" in stripped:
            # 链式等式：``f(a)=g(a)=expr`` 的最终表达式在最后一个等号右侧。
            head, _, tail = stripped.rpartition("=")
            if tail.strip() and head.strip().startswith("\\operatorname"):
                stripped = tail.strip()
        output.append(stripped)
    return "\n".join(output)


def right_sides(text: str) -> List[Tuple[int, str]]:
    """返回每行等式的右端（仅当该行恰好只有一个顶层等号）。"""
    results: List[Tuple[int, str]] = []
    for index, line in enumerate(split_math_lines(text), start=1):
        positions = _top_level_equals(line)
        if len(positions) != 1:
            continue
        split_at = positions[0]
        left = line[:split_at].strip()
        right = strip_trailing_conditions(line[split_at + 1:].strip())
        if not left or not right:
            continue
        results.append((index, right))
    return results


# 定义域/取值说明的常见记号：``,\quad i=1,\dots,n``、``,0\le i<n``。
_CONDITION_MARKERS = re.compile(
    r"\\(?:quad|qquad|le|leq|ge|geq|in|text)\b|[<>]")
# 顶层（不在括号/花括号内）的逗号与分号。
_TOP_LEVEL_SEPARATORS = re.compile(r"[,;]")


def strip_trailing_conditions(text: str) -> str:
    """删除公式尾部与数学含义无关的定义域说明，只保留公式本体。

    例：``\\frac{a}{b},\\quad i=1,\\dots,n`` -> ``\\frac{a}{b}``。
    尾部若不含定义域记号（如 ``x, y`` 这类并列项）则保持原样。
    """
    if not isinstance(text, str) or not text.strip():
        return ""
    value = text.strip()
    depth = 0
    candidates: List[int] = []
    for index, char in enumerate(value):
        if char in "({[":
            depth += 1
        elif char in ")}]":
            depth = max(0, depth - 1)
        elif char in ",;" and depth == 0:
            candidates.append(index)
    for position in reversed(candidates):
        tail = value[position + 1:].strip()
        if tail and _CONDITION_MARKERS.search(tail):
            return value[:position].strip()
    return value


def prepare_reference(text: str) -> str:
    """参考公式的一站式预处理：先剥函数签名，再代入中间变量。"""
    if not isinstance(text, str):
        return ""
    return expand_intermediate_variables(strip_function_signatures(text))


__all__ = [
    "expand_intermediate_variables",
    "prepare_reference",
    "right_sides",
    "split_math_lines",
    "strip_function_signatures",
    "strip_trailing_conditions",
]
