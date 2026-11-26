# -*- coding: utf-8 -*-
"""agent/verify.py — SymPy 符号校验核心（工单9）+ 差异定位与报告（工单10）。

校验策略（由严到宽）：
1. simplify(a - b) == 0  -> 严格等价；
2. expand / trigsimp / factor 归一后为 0 -> 代数等价；
3. 数值采样比较（相对误差 < tol）-> 数值等价，适用于无法化简的复杂式；
4. 矩阵按 shape + 逐元素比较。

所有对外函数都返回结构化 CheckResult，不抛异常。
"""
from __future__ import annotations

import logging
import math
import random
import re
from abc import ABC, abstractmethod  # 新增：校验器统一抽象接口
from dataclasses import dataclass, field
from typing import Any, Optional

import sympy as sp
from sympy import Eq
from sympy.parsing.sympy_parser import (implicit_multiplication_application,
                                        parse_expr, standard_transformations)

logger = logging.getLogger(__name__)

TRANSFORMS = standard_transformations + (implicit_multiplication_application,)

try:  # 新增：兼容包导入和脚本直接运行。
    from .symbol_norm import normalize_latex_symbols
    from .symbol_norm import (  # 工单2：求和上下限、迭代下标、范数括号
        normalize_iteration_subscripts, normalize_norm_bars,
        normalize_sum_limits)
    # 新增（w10）：a_{i,i} 与 a_{ii} 的下标写法归一化。
    from .symbol_norm import normalize_subscript_commas
except ImportError:  # pragma: no cover
    from symbol_norm import normalize_latex_symbols  # type: ignore
    from symbol_norm import (  # type: ignore
        normalize_iteration_subscripts, normalize_norm_bars,
        normalize_sum_limits)
    from symbol_norm import normalize_subscript_commas  # type: ignore

try:
    from sympy.parsing.latex import parse_latex as _parse_latex
    _LATEX_OK = True
    _LATEX_ERR = ""
except Exception as _e:  # antlr4 缺失等
    _parse_latex = None
    _LATEX_OK = False
    _LATEX_ERR = f"{type(_e).__name__}: {_e}"

# 工单2：矩阵连乘跨表示规则（结合律 + 逐元素索引收缩）。
try:  # 新增：兼容包导入和脚本直接运行。
    from .matrix_chain import try_matrix_chain_equivalence
except ImportError:  # pragma: no cover
    from matrix_chain import try_matrix_chain_equivalence  # type: ignore


# ---------------------------------------------------------------- 数据结构
@dataclass
class DiffPoint:
    point: dict
    lhs: Any
    rhs: Any
    rel_delta: Optional[float]

    def as_dict(self) -> dict:
        return {"point": self.point, "lhs": str(self.lhs), "rhs": str(self.rhs),
                "rel_delta": self.rel_delta}


@dataclass
class CheckResult:
    ok: bool
    status: str                       # equivalent | numeric_equivalent | not_equivalent
                                      # | parse_error | shape_mismatch | unknown | error
                                      # 新增：| needs_human_review（多校验器冲突）
    lhs: str = ""
    rhs: str = ""
    detail: str = ""
    diff_points: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "status": self.status, "lhs": self.lhs, "rhs": self.rhs,
                "detail": self.detail,
                "diff_points": [d.as_dict() if isinstance(d, DiffPoint) else d
                                for d in self.diff_points],
                "meta": self.meta}

    def to_markdown(self) -> str:
        icon = "✅" if self.ok else "❌"
        lines = [f"### {icon} 校验结果：{self.status}", "",
                 f"- **左侧**：`{self.lhs}`", f"- **右侧**：`{self.rhs}`"]
        if self.detail:
            lines.append(f"- **说明**：{self.detail}")
        if self.diff_points:
            lines += ["", "| 采样点 | 左侧值 | 右侧值 | 相对误差 |", "|---|---|---|---|"]
            for d in self.diff_points:
                dd = d.as_dict() if isinstance(d, DiffPoint) else d
                lines.append(f"| {dd['point']} | {dd['lhs']} | {dd['rhs']} | {dd['rel_delta']} |")
        if self.meta:
            lines += ["", f"- **元信息**：`{self.meta}`"]
        return "\n".join(lines)



# 新增：统一校验结果类型别名，保持与现有 CheckResult 完全兼容。
Verdict = CheckResult


class VerifierBase(ABC):
    """校验器统一接口；现有调用方仍可继续直接使用模块级函数。"""

    name = "verifier"

    @abstractmethod
    def verify(self, latex_expr: str, cpp_expr_or_code: str,
               context: Optional[dict] = None) -> Verdict:
        """返回一致、不一致或无法判定，并携带差异证据与置信度。"""
        raise NotImplementedError


# ---------------------------------------------------------------- LaTeX -> SymPy
_GREEK = {"alpha": "alpha", "beta": "beta", "gamma": "gamma", "delta": "delta",
          "theta": "theta", "lambda": "lambda", "mu": "mu", "pi": "pi",
          "sigma": "sigma", "omega": "omega", "phi": "phi", "epsilon": "epsilon"}


def _match_brace(s: str, i: int) -> int:
    """s[i] == '{'，返回匹配的 '}' 下标；失败返回 -1。"""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return -1


def _convert_latex(src: str) -> str:
    """把常见 LaTeX 子集转成 sympy 可解析的 Python 表达式（递归处理嵌套）。"""
    s = src.strip()
    # 去掉空白控制符
    s = re.sub(r"\\[!,;: ]", "", s)
    s = s.replace("\\left", "").replace("\\right", "")
    # \frac{A}{B} / \dfrac
    while True:
        m = re.search(r"\\(?:d)?frac\s*\{", s)
        if not m:
            break
        i = m.end() - 1
        j = _match_brace(s, i)
        if j < 0:
            break
        num = s[i + 1:j]
        if j + 1 >= len(s) or s[j + 1] != "{":
            break
        k = _match_brace(s, j + 1)
        if k < 0:
            break
        den = s[j + 2:k]
        s = s[:m.start()] + "((%s)/(%s))" % (_convert_latex(num), _convert_latex(den)) + s[k + 1:]
    # \sqrt{A}
    while True:
        m = re.search(r"\\sqrt\s*\{", s)
        if not m:
            break
        i = m.end() - 1
        j = _match_brace(s, i)
        if j < 0:
            break
        s = s[:m.start()] + "sqrt(%s)" % _convert_latex(s[i + 1:j]) + s[j + 1:]
    # ^{A} / _{A}
    s = re.sub(r"\^\{([^{}]*)\}", r"**(\1)", s)
    s = re.sub(r"_\{([^{}]*)\}", r"_\1", s)
    # 常见运算符
    # 新增：先转换 PDE 符号和运算符，保证轻量兜底解析能处理 \nabla p 等写法。
    s = s.replace("\\otimes", "*")
    s = s.replace("\\cdot", "*").replace("\\times", "*").replace("\\div", "/")
    s = s.replace("\\nabla", "nabla")
    s = s.replace("\\partial", "partial")
    s = s.replace("\\Delta", "Delta")
    # 新增：给 \nabla 后直接跟变量或左括号的情况补乘号，避免解析成一元运算。
    s = re.sub(r"\bnabla\s*(?=[A-Za-z0-9(])", "nabla*", s)
    s = s.replace("\\pi", "pi").replace("\\infty", "oo")
    for g in _GREEK.values():
        s = s.replace("\\" + g, g)
    # 函数名
    for fn in ("sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "exp",
               "arcsin", "arccos", "arctan"):
        s = re.sub(r"\\" + fn + r"\b", "ln" if fn == "ln" else fn, s)
    s = s.replace("^", "**")
    return s


_MATRIX_ENV_PATTERN = re.compile(
    r"\\begin\s*\{(?P<env>bmatrix|pmatrix|matrix|vmatrix|Bmatrix|array)\}"
    r"(?P<body>.*?)\\end\s*\{(?P=env)\}",
    flags=re.IGNORECASE | re.DOTALL,
)

# 新增：识别需要逐式校验的常见多行环境。
_MULTILINE_ENV_PATTERN = re.compile(
    r"\\begin\s*\{(?P<env>aligned|align|alignedat|gathered|gather|split|"
    r"cases|dcases|rcases)\}"
    r"(?P<body>.*?)\\end\s*\{(?P=env)\}",
    flags=re.IGNORECASE | re.DOTALL,
)

# 新增：匹配两个及以上独立的 \[...\] 展示公式段。
_DISPLAY_MATH_PATTERN = re.compile(r"\\\[(.*?)\\\]", flags=re.DOTALL)


def _parse_matrix_latex(tex: str):
    """新增：手写解析常见 LaTeX 矩阵环境，任一单元格失败则整体失败。"""
    if not isinstance(tex, str):
        return None
    # 新增：矩阵独立解析入口同样先做翻译层符号归一化。
    tex = normalize_latex_symbols(tex)
    if not tex.strip():
        return None
    match = _MATRIX_ENV_PATTERN.search(tex)
    if match is None:
        return None

    body = match.group("body").strip()
    if match.group("env").lower() == "array":
        # array 的列格式参数（如 {ccc}）不属于单元格内容。
        body = re.sub(r"^\s*\{[^{}]*\}", "", body, count=1).strip()
    if not body:
        return None

    row_texts = [
        row.strip()
        for row in re.split(r"\\\\", body)
        if row.strip()
    ]
    if not row_texts:
        return None

    rows = []
    expected_columns = None
    for row_text in row_texts:
        cells = row_text.split("&")
        if expected_columns is None:
            expected_columns = len(cells)
        elif len(cells) != expected_columns:
            return None

        row = []
        for raw_cell in cells:
            cell = raw_cell.strip()
            # 删除仅用于排版的文字包装和分隔命令。
            cell = re.sub(
                r"\\(?:text|mathrm)\s*\{[^{}]*\}", "", cell,
                flags=re.IGNORECASE,
            )
            cell = re.sub(r"\\(?:quad|;|,|!)", "", cell)
            cell = cell.replace("\\ ", " ").strip()
            if not cell:
                return None

            expr, _ = latex_to_expr(cell)
            if expr is None:
                try:
                    expr = parse_expr(
                        _convert_latex(cell), transformations=TRANSFORMS)
                except Exception:
                    return None
            if expr is None:
                return None
            row.append(expr)
        rows.append(row)

    try:
        return sp.Matrix(rows)
    except Exception:
        return None


def _clean_latex_for_parse(tex: str) -> str:
    """新增：去掉 LLM/OCR 常见的展示性包装，降低解析失败率。

    只处理不承载数学语义的包装：\\(...\\)、\\[...\\]、\\text{}、
    \\xrightarrow{}{} 注释；不改写数学符号本身。
    """
    text = str(tex or "").strip()
    wrapped = re.match(r"^\\\((.*)\\\)$", text, re.S)
    if wrapped:
        text = wrapped.group(1).strip()
    wrapped = re.match(r"^\\\[(.*)\\\]$", text, re.S)
    if wrapped:
        text = wrapped.group(1).strip()
    text = re.sub(r"\\xrightarrow\{[^{}]*\}\{[^{}]*\}", "", text)
    text = re.sub(r"\\xrightarrow\{[^{}]*\}", "", text)
    text = re.sub(r"\\text\{[^{}]*\}", "", text)
    # 新增（w10）：\left / \right 只控制括号排版高度，不承载数学语义。
    # parse_latex 对 \left|x_i\right| 这类绝对值写法会报错，这里先行剥掉。
    text = re.sub(r"\\(?:left|right)\b\s*", "", text)
    # 新增：删除只影响排版位置的求和上下限命令，避免阻断兜底解析。
    text = re.sub(r"\\(?:limits|nolimits)(?=\b|_)", "", text)
    return text.strip()


def _detect_multiline_latex(tex: str, *, normalized: bool = False):
    """新增：判断文本是否包含需要逐式处理的多行公式。"""
    if not isinstance(tex, str):
        return False, ""
    source = tex.strip()
    if not source:
        return False, ""
    if not normalized:
        source = normalize_latex_symbols(source)

    environment_match = _MULTILINE_ENV_PATTERN.search(source)
    if environment_match is not None:
        environment = environment_match.group("env").lower()
        return True, f"多行环境需逐式校验: {environment}"

    # 修改：矩阵和数组内部的 \\ 是行列分隔符；但 array 里装的是公式（含 =）
    # 时按多行公式处理，避免把 3x1 公式数组误当数值矩阵。
    matrix_match = _MATRIX_ENV_PATTERN.search(source)
    if matrix_match is not None:
        if "=" in matrix_match.group("body"):
            return True, "多行公式需逐段校验"
        return False, ""

    if len(_DISPLAY_MATH_PATTERN.findall(source)) >= 2:
        return True, "多行公式需逐段校验"

    nonblank_lines = [
        line.strip() for line in source.splitlines() if line.strip()
    ]
    linebreak_parts = [
        part.strip()
        for part in re.split(r"\\\\", "\n".join(nonblank_lines))
        if part.strip()
    ]
    if len(linebreak_parts) >= 2:
        return True, "多行公式需逐段校验"

    # 翻译层可能直接使用真实换行而不是 LaTeX 的 \\；
    # 仅当至少两行都像等式时拦截，避免误伤普通的表达式折行。
    if len(nonblank_lines) >= 2:
        equation_line_count = sum(
            bool(re.search(r"(?<![<>=])=(?!=)", line))
            for line in nonblank_lines
        )
        if equation_line_count >= 2:
            return True, "多行公式需逐段校验"

    return False, ""


def _clean_multiline_piece(piece: str) -> str:
    """新增：清理多行子式中只影响排版的 LaTeX 命令。"""
    cleaned = str(piece or "").strip()
    cleaned = cleaned.replace("@@IF@@", " ")
    cleaned = re.sub(r"\\begin\s*\{[^{}]+\}|\\end\s*\{[^{}]+\}", "", cleaned)
    cleaned = cleaned.replace("&", "")
    cleaned = re.sub(r"\\(?:qquad|quad)\b", " ", cleaned)
    # 新增：删除求和排版修饰，避免 \sum\limits 阻断解析。
    cleaned = re.sub(r"\\(?:limits|nolimits)\b", "", cleaned)
    cleaned = cleaned.replace("\\left", "").replace("\\right", "")
    # 兼容 \text{if } 写法；普通 \text{...} 已在符号归一化阶段删除。
    cleaned = re.sub(
        r"\\text\s*\{\s*if\b[^{}]*\}", " ", cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\\text\s*\{[^{}]*\}", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" \t\r\n,;")


def _looks_like_multiline_condition(text: str) -> bool:
    """新增：判断单独一行是否只是条件或范围说明。"""
    compact = _clean_multiline_piece(text)
    if not compact or "=" in compact:
        return False
    return bool(re.search(r"(?:<=|>=|<|>)", compact))


def _looks_like_explanation_line(text: str) -> bool:
    """新增：过滤环境内不参与比较的纯说明文字。"""
    compact = _clean_multiline_piece(text)
    if not compact:
        return True
    # 新增：纯括号说明如 "(i,j fixed)" 不参与公式比较。
    if re.fullmatch(r"\([^=]*\)", compact):
        return True
    if re.search(r"[=+\-*/^_(){}\[\]<>]", compact):
        return False
    words = re.findall(r"[A-Za-z]{2,}", compact)
    return len(words) >= 2 and bool(re.search(r"\s", compact))


def _split_cases_row(row_text: str):
    """新增：把 cases 行拆成表达式和条件。"""
    row_text = row_text.strip()
    if "&" in row_text:
        expression, condition = row_text.split("&", 1)
        return _clean_multiline_piece(expression), _clean_multiline_piece(condition)

    if "@@IF@@" in row_text:
        expression, condition = row_text.split("@@IF@@", 1)
        return _clean_multiline_piece(expression), _clean_multiline_piece(condition)

    text_if_match = re.search(
        r"\\text\s*\{\s*if\b[^{}]*\}", row_text,
        flags=re.IGNORECASE,
    )
    if text_if_match is not None:
        expression = row_text[:text_if_match.start()]
        condition = row_text[text_if_match.end():]
        return _clean_multiline_piece(expression), _clean_multiline_piece(condition)

    if "," in row_text:
        expression, condition = row_text.split(",", 1)
        return _clean_multiline_piece(expression), _clean_multiline_piece(condition)

    return _clean_multiline_piece(row_text), ""


def _split_multiline_latex(tex: str) -> list[dict]:
    """新增：把多行环境或普通多行公式拆成逐式子式。"""
    if not isinstance(tex, str) or not tex.strip():
        return []
    # 新增：先保护 cases 的 \text{if} 分隔点，避免被符号归一化整体删除。
    protected_source = re.sub(
        r"\\text\s*\{\s*if\b[^{}]*\}", " @@IF@@ ",
        tex.strip(), flags=re.IGNORECASE,
    )
    source = normalize_latex_symbols(protected_source).strip()
    if not source:
        return []

    environment_match = _MULTILINE_ENV_PATTERN.search(source)
    environment = ""
    if environment_match is not None:
        environment = environment_match.group("env").lower()
        source = environment_match.group("body").strip()
    else:
        # 新增：公式型矩阵/array（body 含 =）按多行公式拆，而不是数值矩阵。
        matrix_match = _MATRIX_ENV_PATTERN.search(source)
        if matrix_match is not None and "=" in matrix_match.group("body"):
            source = matrix_match.group("body").strip()
            if matrix_match.group("env").lower() == "array":
                source = re.sub(
                    r"^\s*\{[^{}]*\}", "", source, count=1).strip()
        else:
            display_segments = _DISPLAY_MATH_PATTERN.findall(source)
            if len(display_segments) >= 2:
                source = "\n".join(display_segments)
            else:
                source = re.sub(
                    r"^\\\[(.*)\\\]$", r"\1", source, flags=re.DOTALL)

    raw_rows = re.split(r"\\\\|\r?\n", source)
    rows = []
    for raw_row in raw_rows:
        if not raw_row.strip():
            continue

        if environment in {"cases", "dcases", "rcases"}:
            expression, condition = _split_cases_row(raw_row)
        else:
            expression = _clean_multiline_piece(raw_row)
            condition = ""

        if not expression and not condition:
            continue
        if _looks_like_explanation_line(expression):
            continue
        if expression and _looks_like_multiline_condition(expression):
            condition = expression if not condition else condition
            expression = ""
        # 新增：非 cases 的多行环境只比较等式，无等号行视为条件/说明。
        if (environment not in {"cases", "dcases", "rcases"}
                and expression and "=" not in expression):
            continue
        if not expression and not condition:
            continue

        rows.append({
            "expr": expression,
            "condition": condition,
            "row": len(rows) + 1,
        })
    return rows


def latex_to_expr(tex: str):
    """LaTeX -> (sympy Expr | None, error)。优先 parse_latex，失败走轻量转换。"""
    # 新增：所有 LaTeX 在原有清洗前先统一包装、迭代上标和赋值箭头。
    tex = normalize_latex_symbols(tex)
    # 新增（w10）：a_{i,i} 与 a_{ii} 视为同一元素，避免下标写法差异阻断解析。
    tex = normalize_subscript_commas(tex)
    # 工单2：OCR 常输出 \sum_i 这类简写，先补全上下限再交给 parse_latex。
    tex = normalize_sum_limits(tex)
    # 工单2：w_{t+1}/w_t 与 w 视为同一变量，消除迭代写法的表示差异。
    tex = normalize_iteration_subscripts(tex)
    # 工单2：\|x\|_2 这类范数写法改写为 |x|，否则 parse_latex 直接报错。
    tex = normalize_norm_bars(tex)
    if not tex:
        return None, "空表达式"
    # 新增：多行环境必须在矩阵解析和 parse_latex 之前拦截，交给逐式校验。
    is_multiline, multiline_reason = _detect_multiline_latex(
        tex.strip(), normalized=True)
    if is_multiline:
        return None, multiline_reason
    # 新增：统一清理展示性包装，避免 \(...\)、\text{} 直接打断解析。
    t = _clean_latex_for_parse(tex.strip().strip("$").strip())
    if t.startswith("$$") and t.endswith("$$"):
        t = t[2:-2].strip()

    # 新增：矩阵环境先整体解析，失败后继续沿用原有解析与兜底逻辑。
    if _MATRIX_ENV_PATTERN.search(t):
        matrix_tex = t.rsplit("=", 1)[-1].strip() if "=" in t else t
        matrix = _parse_matrix_latex(matrix_tex)
        if matrix is not None:
            return matrix, ""

    if _LATEX_OK:
        try:
            expr = _parse_latex(t)
            if isinstance(expr, Eq):
                expr = expr.lhs - expr.rhs
            return expr, ""
        except Exception as e:
            first_err = f"{type(e).__name__}: {e}"
    else:
        first_err = f"parse_latex 不可用({_LATEX_ERR})"
    try:
        return parse_expr(_convert_latex(t), transformations=TRANSFORMS), ""
    except Exception as e2:
        return None, f"{first_err} | fallback: {type(e2).__name__}: {e2}"


# 新增：提取公式和循环的索引范围，仅作为异构校验器的辅助证据。
def _compact_scope_text(value: str) -> str:
    """去掉索引范围两端的空白并合并连续空白。"""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _line_containing_offset(text: str, offset: int) -> str:
    """返回文本中指定位置所在行的去空白内容。"""
    if not text:
        return ""
    safe_offset = max(0, min(offset, len(text)))
    line_start = text.rfind("\n", 0, safe_offset) + 1
    line_end = text.find("\n", safe_offset)
    if line_end < 0:
        line_end = len(text)
    return _compact_scope_text(text[line_start:line_end])


def _consume_latex_argument(text: str, start: int) -> tuple[str, int]:
    """读取 LaTeX 参数，支持 {...}、单个命令或单个普通记号。"""
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text):
        return "", index
    if text[index] == "{":
        end = _match_brace(text, index)
        if end < 0:
            return "", index
        return text[index + 1:end], end + 1
    command = re.match(r"\\[A-Za-z]+|\\.", text[index:])
    if command:
        return command.group(0), index + len(command.group(0))
    token = re.match(r"[A-Za-z0-9]+", text[index:])
    if token:
        return token.group(0), index + len(token.group(0))
    return text[index], index + 1


def _parse_latex_sum_bounds(lower_text: str, upper_text: str) -> dict:
    """把求和下标拆成变量、下界和上界。"""
    lower_text = _compact_scope_text(lower_text)
    upper_text = _compact_scope_text(upper_text)
    relation = re.match(
        r"^(?P<var>[A-Za-z][A-Za-z0-9_]*)\s*"
        r"(?P<op><=|>=|<|>|=)\s*(?P<bound>.+)$",
        lower_text,
    )
    if relation:
        variable = relation.group("var")
        operator = relation.group("op")
        bound = _compact_scope_text(relation.group("bound"))
        if operator == "=":
            return {"var": variable, "lower": bound,
                    "upper": upper_text}
        if operator in {"<", "<="}:
            return {"var": variable, "lower": "",
                    "upper": bound or upper_text}
        return {"var": variable, "lower": bound or upper_text,
                "upper": ""}

    variable_match = re.match(
        r"^(?P<var>[A-Za-z][A-Za-z0-9_]*)", lower_text)
    if variable_match:
        return {
            "var": variable_match.group("var"),
            "lower": "",
            "upper": upper_text,
        }
    return {"var": "", "lower": "", "upper": upper_text}


def _find_latex_scope_hints(latex_text: str) -> list[dict]:
    """提取公式正文中单独声明的索引约束，例如 k<j。"""
    hints = []
    relation_pattern = re.compile(
        r"(?<![A-Za-z0-9_])"
        r"(?P<var>[A-Za-z][A-Za-z0-9_]*)\s*"
        r"(?P<op><=|>=|<|>)\s*"
        r"(?P<bound>[A-Za-z0-9_+\-]+)"
        r"(?![A-Za-z0-9_])"
    )
    for match in relation_pattern.finditer(latex_text):
        operator = match.group("op")
        bound = _compact_scope_text(match.group("bound"))
        hints.append({
            "var": match.group("var"),
            "lower": bound if operator in {">", ">="} else "",
            "upper": bound if operator in {"<", "<="} else "",
        })
    return hints


def _extract_latex_sums(latex_text: str) -> list[dict]:
    """提取全部 LaTeX 求和表达式；单条格式异常时跳过该条。"""
    sums = []
    scope_hints = _find_latex_scope_hints(latex_text)
    sum_start = re.compile(
        r"\\sum(?![A-Za-z])(?:\s*\\(?:limits|nolimits))?\s*",
        flags=re.IGNORECASE)
    cursor = 0
    while cursor < len(latex_text):
        match = sum_start.search(latex_text, cursor)
        if match is None:
            break

        raw_end = match.end()
        lower_text = ""
        upper_text = ""
        has_explicit_subscript = False
        try:
            index = match.end()
            while index < len(latex_text) and latex_text[index].isspace():
                index += 1
            if index < len(latex_text) and latex_text[index] == "_":
                has_explicit_subscript = True
                lower_text, raw_end = _consume_latex_argument(
                    latex_text, index + 1)
                index = raw_end
                while index < len(latex_text) and latex_text[index].isspace():
                    index += 1
                if index < len(latex_text) and latex_text[index] == "^":
                    upper_text, raw_end = _consume_latex_argument(
                        latex_text, index + 1)

            if not has_explicit_subscript and scope_hints:
                following_text = latex_text[raw_end:raw_end + 120]
                matching_hints = [
                    hint for hint in scope_hints
                    if hint["var"] in following_text
                ]
                if matching_hints:
                    parsed = dict(matching_hints[0])
                else:
                    parsed = _parse_latex_sum_bounds(lower_text, upper_text)
            else:
                parsed = _parse_latex_sum_bounds(lower_text, upper_text)
            if parsed["var"]:
                raw = _compact_scope_text(latex_text[match.start():raw_end])
                parsed["raw"] = raw
                sums.append(parsed)
        except (IndexError, TypeError, ValueError):
            pass
        cursor = max(raw_end, match.end())
    return sums


def _extract_cpp_loops(cpp_code: str) -> list[dict]:
    """提取 C++ for 循环的变量、初始下界和条件边界。"""
    loops = []
    for_match = re.compile(
        r"\bfor\s*\((?P<header>[^)]*)\)", flags=re.DOTALL)
    for match in for_match.finditer(cpp_code):
        try:
            header = match.group("header")
            header_parts = header.split(";")
            if len(header_parts) < 2:
                continue
            init_text = header_parts[0].strip()
            condition_text = header_parts[1].strip()
            initialized = re.search(
                r"\b(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
                r"(?P<lower>[^=;]+)",
                init_text,
            )
            if initialized is None:
                continue
            variable = initialized.group("var")
            lower = _compact_scope_text(initialized.group("lower"))
            condition = re.search(
                rf"\b{re.escape(variable)}\s*"
                r"(?P<op><=|>=|==|!=|<|>)\s*(?P<bound>.+)",
                condition_text,
            )
            if condition is None:
                continue
            operator = condition.group("op")
            bound = _compact_scope_text(condition.group("bound"))
            upper = bound
            if operator in {">", ">="}:
                lower, upper = bound, ""
            loops.append({
                "var": variable,
                "lower": lower,
                "upper": upper,
                "op": operator,
                "raw": _compact_scope_text(match.group(0)),
            })
        except (IndexError, TypeError, ValueError):
            continue
    return loops


def extract_index_scopes(latex_text: str, cpp_code: str) -> dict:
    """提取 LaTeX 求和与 C++ 循环范围，解析失败时返回空证据。"""
    empty_result = {
        "latex_sums": [],
        "cpp_loops": [],
        "context": {"latex_line": "", "cpp_line": ""},
    }
    if not isinstance(latex_text, str) or not isinstance(cpp_code, str):
        return empty_result
    try:
        latex_sums = _extract_latex_sums(latex_text)
        cpp_loops = _extract_cpp_loops(cpp_code)
        latex_sum_match = re.search(r"\\sum(?![A-Za-z])", latex_text)
        latex_variables = {
            item["var"] for item in latex_sums if item.get("var")}
        selected_loop = next(
            (item for item in cpp_loops if item.get("var") in latex_variables),
            cpp_loops[0] if cpp_loops else None,
        )
        cpp_loop_match = None
        if selected_loop is not None:
            for loop_match in re.finditer(r"\bfor\s*\([^)]*\)", cpp_code):
                raw_loop = _compact_scope_text(loop_match.group(0))
                if raw_loop == selected_loop["raw"]:
                    cpp_loop_match = loop_match
                    break
        if cpp_loop_match is None:
            cpp_loop_match = re.search(r"\bfor\s*\(", cpp_code)
        return {
            "latex_sums": latex_sums,
            "cpp_loops": cpp_loops,
            "context": {
                "latex_line": (
                    _line_containing_offset(latex_text, latex_sum_match.start())
                    if latex_sum_match else ""
                ),
                "cpp_line": (
                    _line_containing_offset(cpp_code, cpp_loop_match.start())
                    if cpp_loop_match else ""
                ),
            },
        }
    except Exception:
        return empty_result


def parse_python_expr(code: str, symbols: Optional[list] = None):
    """C++/Python 风格表达式 -> sympy；未知标识符自动建符号。"""
    if not code:
        return None, "空表达式"
    try:
        local = {str(s): s for s in (symbols or [])}
        return parse_expr(code.strip(), local_dict=local, transformations=TRANSFORMS), ""
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------- 数值比较
def _numeric_at(expr, subs, tol=1e-12):
    try:
        v = sp.N(expr.subs(subs))
        return complex(v)
    except Exception:
        return None


def numeric_compare(a, b, symbols=None, *, samples: int = 24, tol: float = 1e-8,
                    seed: int = 0):
    """随机采样数值比较，返回 (ok, diff_points, detail)。"""
    symbols = list(symbols or sorted(a.free_symbols | b.free_symbols, key=str))
    rnd = random.Random(seed)
    diffs = []
    checked = 0
    for _ in range(samples * 4):
        if checked >= samples:
            break
        subs = {s: sp.Float(round(rnd.uniform(-3.0, 3.0), 6)) for s in symbols}
        va = _numeric_at(a, subs)
        vb = _numeric_at(b, subs)
        if va is None or vb is None or not (math.isfinite(va.real) and math.isfinite(vb.real)):
            continue
        if abs(va.imag) > 1e-9 or abs(vb.imag) > 1e-9:
            continue
        checked += 1
        denom = max(abs(va), abs(vb), 1e-12)
        rel = abs(va - vb) / denom
        if rel > tol:
            diffs.append(DiffPoint(point={str(k): float(v) for k, v in subs.items()},
                                   lhs=sp.N(va.real, 8), rhs=sp.N(vb.real, 8),
                                   rel_delta=float(rel)))
            if len(diffs) >= 5:
                break
    if checked == 0:
        return None, diffs, "采样点全部无效（可能含定义域外取值）"
    if diffs:
        worst = max(diffs, key=lambda d: d.rel_delta)
        return False, diffs, f"数值不一致：最差点相对误差 {worst.rel_delta:.3e}"
    return True, diffs, f"{checked} 个采样点数值一致（tol={tol}）"


# ---------------------------------------------------------------- 核心比较
def compare_expressions(a, b, symbols=None, *, tol: float = 1e-8,
                        samples: int = 24) -> CheckResult:
    """比较两个 sympy 表达式；返回结构化结果。"""
    # 修改：非法解析结果（如排版噪声产生的 tuple）统一归为 unknown，
    # 不再因缺少 free_symbols 抛 AttributeError 而中断校验链。
    if not hasattr(a, "free_symbols") or not hasattr(b, "free_symbols"):
        return CheckResult(
            False, "unknown", str(a), str(b),
            "输入不是可比较的 SymPy 表达式，无法判定")
    try:
        if isinstance(a, Eq):
            a = a.lhs - a.rhs
        if isinstance(b, Eq):
            b = b.lhs - b.rhs
        if isinstance(a, sp.MatrixBase) or isinstance(b, sp.MatrixBase):
            return compare_matrices(a, b, symbols, tol=tol, samples=samples)
        # 1) 严格化简
        try:
            d = sp.simplify(a - b)
            if d == 0:
                return CheckResult(True, "equivalent", str(a), str(b), "simplify(a-b)=0")
        except Exception:
            pass
        # 2) 归一化
        for fn in (sp.expand, sp.trigsimp, sp.factor, sp.cancel):
            try:
                if fn(a - b) == 0:
                    return CheckResult(True, "equivalent", str(a), str(b),
                                       f"{fn.__name__}(a-b)=0")
            except Exception:
                continue
        # 3) 数值比较
        ok, diffs, detail = numeric_compare(a, b, symbols, samples=samples, tol=tol)
        if ok is True:
            return CheckResult(True, "numeric_equivalent", str(a), str(b), detail)
        if ok is False:
            return CheckResult(False, "not_equivalent", str(a), str(b), detail,
                               diff_points=diffs)
        return CheckResult(False, "unknown", str(a), str(b), detail)
    except Exception as e:
        return CheckResult(False, "error", str(a), str(b), f"{type(e).__name__}: {e}")


def compare_matrices(a: sp.MatrixBase, b: sp.MatrixBase, symbols=None, *,
                     tol: float = 1e-8, samples: int = 12) -> CheckResult:
    """矩阵比较：shape 检查 + 逐元素 compare_expressions。"""
    if not isinstance(a, sp.MatrixBase) or not isinstance(b, sp.MatrixBase):
        return CheckResult(False, "error", str(a), str(b), "一侧不是矩阵")
    if a.shape != b.shape:
        return CheckResult(False, "shape_mismatch", f"{a.shape}", f"{b.shape}",
                           "矩阵维度不一致")
    for i in range(a.rows):
        for j in range(a.cols):
            r = compare_expressions(sp.expand(a[i, j]), sp.expand(b[i, j]),
                                    symbols, tol=tol, samples=samples)
            if not r.ok:
                r.meta.update({"element": (i, j), "shape": a.shape})
                return r
    return CheckResult(True, "equivalent", str(a.shape), str(b.shape),
                       "全部元素等价", meta={"elements": a.rows * a.cols})


# 新增：矩阵乘与逐元素定义之间的跨表示等价规则层。
_ELEMENTWISE_MATMUL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?P<target_base>[A-Za-z])\s*_\s*"
    r"(?:\{\s*(?P<target_indices>[^{}]+)\s*\}|"
    r"(?P<target_indices_plain>[A-Za-z]{1,2}))"
    r"\s*=\s*\\sum\s*(?:\\(?:limits|nolimits)(?![A-Za-z])\s*)?"
    r"(?:_\s*(?:\{\s*(?P<lower>[^{}]+)\s*\}|"
    r"(?P<lower_plain>[A-Za-z0-9])))?"
    r"(?:\^\s*(?:\{\s*(?P<upper>[^{}]+)\s*\}|"
    r"(?P<upper_plain>[A-Za-z0-9])))?"
    r"\s*(?P<factor1>[A-Za-z])\s*_\s*"
    r"(?:\{\s*(?P<factor1_indices>[^{}]+)\s*\}|"
    r"(?P<factor1_indices_plain>[A-Za-z]{1,2}))"
    r"\s*(?:\\(?:cdot|times)\b|[*])?\s*"
    r"(?P<factor2>[A-Za-z])\s*_\s*"
    r"(?:\{\s*(?P<factor2_indices>[^{}]+)\s*\}|"
    r"(?P<factor2_indices_plain>[A-Za-z]{1,2}))"
    r"(?![A-Za-z0-9_])"
)

_MATRIX_PRODUCT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?P<target>[A-Za-z])\s*=\s*"
    r"(?P<factor1>[A-Za-z])\s*"
    r"(?:(?:\\cdot|\\times)\s*)?"
    r"(?P<factor2>[A-Za-z])(?![A-Za-z0-9_])"
)


def _normalize_cross_representation_text(text: str) -> str:
    """归一化矩阵公式中的排版包装，保留下标与数学结构供规则识别。"""
    value = str(text or "")
    value = re.sub(r"\\\\\s*(?:\[[^\]]*\])?", "\n", value)
    value = value.replace("\\left", "").replace("\\right", "")
    value = re.sub(r"\\(?:begin|end)\s*\{[^{}]*\}", "", value)
    value = re.sub(r"\\text\s*\{[^{}]*\}", "", value)
    value = re.sub(r"\\(?:qquad|quad)\b", " ", value)
    value = value.replace("&", " ")
    return re.sub(r"[ \t]+", " ", value).strip()


def _parse_matmul_indices(raw_indices: str) -> Optional[tuple[str, ...]]:
    """把 ``ij``、``i,j`` 或 ``i, j`` 解析成索引元组，非法输入返回 None。"""
    value = re.sub(r"\s+", "", str(raw_indices or ""))
    if not value:
        return None
    if "," in value:
        parts = tuple(part for part in value.split(",") if part)
        if not parts or any(not re.fullmatch(r"[A-Za-z]", part) for part in parts):
            return None
        return parts
    if re.fullmatch(r"[A-Za-z]{1,2}", value):
        return tuple(value)
    return None


def _parse_elementwise_sum_bounds(
        lower_text: Optional[str],
        upper_text: Optional[str],
) -> tuple[str, Optional[str], Optional[str]]:
    """拆分求和变量、下界和上界；无变量的无界求和返回空变量名。"""
    lower = str(lower_text or "").strip()
    upper = str(upper_text or "").strip() or None
    if not lower:
        return "", None, upper

    relation = re.match(
        r"^(?P<var>[A-Za-z])\s*(?P<op><=|>=|<|>|=)\s*(?P<bound>.+)$",
        lower,
    )
    if relation is not None:
        variable = relation.group("var")
        operator = relation.group("op")
        bound = relation.group("bound").strip()
        if operator == "=":
            return variable, bound or None, upper
        if operator in {"<", "<="}:
            return variable, None, upper or bound or None
        return variable, bound or None, upper

    variable_match = re.match(r"^(?P<var>[A-Za-z])", lower)
    if variable_match is not None:
        return variable_match.group("var"), None, upper
    return "", None, upper


def _extract_elementwise_matmul_claims(text: str) -> list[dict]:
    """提取“矩阵元等于两个矩阵元乘积之和”的逐元素矩阵乘声明。"""
    normalized = _normalize_cross_representation_text(text)
    claims = []
    seen = set()
    for match in _ELEMENTWISE_MATMUL_PATTERN.finditer(normalized):
        target_indices = _parse_matmul_indices(
            match.group("target_indices")
            or match.group("target_indices_plain")
        )
        factor1_indices = _parse_matmul_indices(
            match.group("factor1_indices")
            or match.group("factor1_indices_plain")
        )
        factor2_indices = _parse_matmul_indices(
            match.group("factor2_indices")
            or match.group("factor2_indices_plain")
        )
        if target_indices is None or factor1_indices is None or factor2_indices is None:
            continue

        term_tail = normalized[match.end():]
        boundary = re.search(r"[,;\n]", term_tail)
        if boundary is not None:
            term_tail = term_tail[:boundary.start()]
        # 求和项必须恰好是两个矩阵元之积，额外加减项一律不命中。
        if re.search(r"[+\-]", term_tail):
            continue

        sum_var, lower, upper = _parse_elementwise_sum_bounds(
            match.group("lower") or match.group("lower_plain"),
            match.group("upper") or match.group("upper_plain"),
        )
        if not sum_var:
            shared_indices = [
                index for index in factor1_indices
                if index in factor2_indices and index not in target_indices
            ]
            if shared_indices:
                sum_var = shared_indices[0]

        raw = match.group(0).strip()
        claim_key = (
            match.group("target_base").casefold(),
            target_indices,
            match.group("factor1").casefold(),
            factor1_indices,
            match.group("factor2").casefold(),
            factor2_indices,
            raw,
        )
        if claim_key in seen:
            continue
        seen.add(claim_key)
        claims.append({
            "target_base": match.group("target_base"),
            "free_indices": target_indices,
            "sum_var": sum_var,
            "lower": lower,
            "upper": upper,
            "factor_bases": (
                match.group("factor1"),
                match.group("factor2"),
            ),
            "factor_indices": (factor1_indices, factor2_indices),
            "raw": raw,
        })
    return claims


def _extract_matrix_product_claims(text: str) -> list[dict]:
    """提取 ``C=AB`` 一类矩阵乘法声明，并返回目标与因子基名。"""
    normalized = _normalize_cross_representation_text(text)
    claims = []
    seen = set()
    for match in _MATRIX_PRODUCT_PATTERN.finditer(normalized):
        claim = {
            "target_base": match.group("target"),
            "factor_bases": (
                match.group("factor1"),
                match.group("factor2"),
            ),
            "raw": match.group(0).strip(),
        }
        claim_key = (
            claim["target_base"].casefold(),
            tuple(base.casefold() for base in claim["factor_bases"]),
        )
        if claim_key in seen:
            continue
        seen.add(claim_key)
        claims.append(claim)
    return claims


def _matmul_bounds_full_range(
        lower: Optional[str],
        upper: Optional[str],
) -> tuple[bool, list[str]]:
    """验证求和范围是否覆盖长度为 3 的完整收缩维，并返回所用假设。"""
    lower_text = str(lower or "").strip()
    upper_text = str(upper or "").strip()
    normalized_lower = re.sub(
        r"^[A-Za-z]\s*=\s*", "", lower_text
    ).strip()
    if normalized_lower not in {"", "0", "1"}:
        return False, []

    if not upper_text:
        return True, ["求和上界缺失：按完整收缩维长度 3 假设"]

    try:
        identifiers = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", upper_text)
        local_symbols = {
            identifier: sp.Symbol(identifier) for identifier in identifiers
        }
        upper_expr = parse_expr(
            upper_text,
            local_dict=local_symbols,
            transformations=standard_transformations,
        )
        substitutions = {
            symbol: sp.Integer(3) for symbol in upper_expr.free_symbols
        }
        resolved_upper = sp.simplify(upper_expr.subs(substitutions))
        expected_upper = 3 if normalized_lower == "1" else 2
        if (
            resolved_upper.free_symbols
            or not bool(resolved_upper == expected_upper)
        ):
            return False, []
    except Exception:
        return True, [
            f"求和上界无法解析：{upper_text}；按完整收缩维长度 3 假设"
        ]
    assumptions = []
    if normalized_lower == "1":
        assumptions.append(
            "求和采用一基下标：按完整收缩维长度 3 解释"
        )
    return True, assumptions


def _numeric_matmul_witness(claim: dict) -> tuple[bool, float]:
    """用固定种子构造 2x3、3x2 矩阵，见证索引模式是否等于矩阵乘法。"""
    try:
        free_indices = tuple(claim.get("free_indices") or ())
        factor_indices = tuple(claim.get("factor_indices") or ())
        sum_var = str(claim.get("sum_var") or "")
        if len(free_indices) != 2 or len(factor_indices) != 2:
            return False, float("inf")
        if (
            len(factor_indices[0]) != 2
            or len(factor_indices[1]) != 2
            or factor_indices[0][0] != free_indices[0]
            or factor_indices[0][1] != sum_var
            or factor_indices[1][0] != sum_var
            or factor_indices[1][1] != free_indices[1]
        ):
            return False, float("inf")

        rnd = random.Random(20261115)
        matrix_a = [
            [round(rnd.uniform(-2.0, 2.0), 12) for _ in range(3)]
            for _ in range(2)
        ]
        matrix_b = [
            [round(rnd.uniform(-2.0, 2.0), 12) for _ in range(2)]
            for _ in range(3)
        ]
        expected = [
            [
                sum(matrix_a[row][inner] * matrix_b[inner][column]
                    for inner in range(3))
                for column in range(2)
            ]
            for row in range(2)
        ]
        max_abs_diff = 0.0
        for row, row_index in enumerate(free_indices[0]):
            for column, column_index in enumerate(free_indices[1]):
                index_values = {row_index: row, column_index: column}
                witness_value = 0.0
                for inner in range(3):
                    index_values[sum_var] = inner
                    factor1_value = matrix_a[
                        index_values[factor_indices[0][0]]
                    ][
                        index_values[factor_indices[0][1]]
                    ]
                    factor2_value = matrix_b[
                        index_values[factor_indices[1][0]]
                    ][
                        index_values[factor_indices[1][1]]
                    ]
                    witness_value += factor1_value * factor2_value
                max_abs_diff = max(
                    max_abs_diff,
                    abs(witness_value - expected[row][column]),
                )
        return max_abs_diff <= 1e-12, max_abs_diff
    except Exception:
        return False, float("inf")


def _matmul_claim_signature(claim: dict) -> Optional[tuple]:
    """生成忽略基名大小写的矩阵乘结构签名，用于判断两表示是否同型。"""
    try:
        free_indices = tuple(claim.get("free_indices") or ())
        factor_indices = tuple(claim.get("factor_indices") or ())
        factor_bases = tuple(claim.get("factor_bases") or ())
        sum_var = str(claim.get("sum_var") or "")
        if (
            len(free_indices) != 2
            or len(factor_indices) != 2
            or len(factor_bases) != 2
            or not sum_var
        ):
            return None
        if (
            len(factor_indices[0]) != 2
            or len(factor_indices[1]) != 2
            or factor_indices[0][0] != free_indices[0]
            or factor_indices[0][1] != sum_var
            or factor_indices[1][0] != sum_var
            or factor_indices[1][1] != free_indices[1]
        ):
            return None
        return (
            str(claim.get("target_base") or "").casefold(),
            tuple(base.casefold() for base in factor_bases),
        )
    except Exception:
        return None


def _matmul_partial_result(
        lhs_text: str,
        rhs_text: str,
        detail: str,
        assumptions: list[str],
) -> CheckResult:
    """构造矩阵乘规则无法完整确认时的保守未知结果。"""
    return CheckResult(
        False,
        "unknown",
        str(lhs_text),
        str(rhs_text),
        detail=detail,
        meta={
            "verdict_label": "NEEDS_HUMAN_REVIEW",
            "cross_representation_rule": "matmul_elementwise_partial",
            "assumptions": assumptions,
        },
    )


def _validate_matmul_claim(claim: dict) -> tuple[bool, list[str], float, str]:
    """校验单个逐元素声明，返回边界、见证与失败说明。"""
    if _matmul_claim_signature(claim) is None:
        return False, [], float("inf"), f"索引模式不是标准二维矩阵乘：{claim.get('raw', '')}"

    bounds_ok, assumptions = _matmul_bounds_full_range(
        claim.get("lower"), claim.get("upper"))
    if not bounds_ok:
        return False, assumptions, float("inf"), f"求和范围未覆盖完整收缩维：{claim.get('raw', '')}"

    witness_ok, max_abs_diff = _numeric_matmul_witness(claim)
    if not witness_ok:
        return False, assumptions, max_abs_diff, f"索引见证未通过：{claim.get('raw', '')}"
    return True, assumptions, max_abs_diff, ""


def _try_cross_representation_equivalence(
        lhs_text: str,
        rhs_text: str,
) -> Optional[CheckResult]:
    """尝试用受限矩阵乘规则判定两种表示是否等价，未命中时返回 None。"""
    try:
        lhs_claims = _extract_elementwise_matmul_claims(lhs_text)
        rhs_claims = _extract_elementwise_matmul_claims(rhs_text)
        lhs_products = _extract_matrix_product_claims(lhs_text)
        rhs_products = _extract_matrix_product_claims(rhs_text)
    except Exception:
        return None

    # 修改：单侧逐元素声明或仅有矩阵乘声明时不参与跨表示规则。
    if not lhs_claims or not rhs_claims:
        return None

    lhs_signatures = [_matmul_claim_signature(claim) for claim in lhs_claims]
    rhs_signatures = [_matmul_claim_signature(claim) for claim in rhs_claims]
    if (
        any(signature is None for signature in lhs_signatures)
        or any(signature is None for signature in rhs_signatures)
        or sorted(lhs_signatures, key=str) != sorted(rhs_signatures, key=str)
    ):
        return None

    # 修改：矩阵乘声明必须与逐元素声明的目标基名和因子基名严格配对。
    known_signatures = set(lhs_signatures)
    for product in lhs_products + rhs_products:
        product_factors = tuple(product.get("factor_bases") or ())
        if len(product_factors) != 2:
            return None
        product_signature = (
            str(product.get("target_base") or "").casefold(),
            tuple(str(base).casefold() for base in product_factors),
        )
        if product_signature not in known_signatures:
            return None

    assumptions = []
    witness_max_abs_diff = 0.0
    try:
        for claim in lhs_claims + rhs_claims:
            ok, claim_assumptions, claim_diff, detail = _validate_matmul_claim(claim)
            if not ok:
                return _matmul_partial_result(
                    lhs_text,
                    rhs_text,
                    detail,
                    assumptions + claim_assumptions,
                )
            assumptions.extend(
                item for item in claim_assumptions if item not in assumptions)
            witness_max_abs_diff = max(witness_max_abs_diff, claim_diff)
    except Exception:
        return None

    return CheckResult(
        True,
        "equivalent",
        str(lhs_text),
        str(rhs_text),
        "矩阵乘声明与逐元素求和定义在索引、边界和数值见证上一致",
        meta={
            "verdict_label": "HIGH_CONFIDENCE",
            "cross_representation_rule": "matmul_elementwise",
            "assumptions": assumptions,
            "witness_max_abs_diff": witness_max_abs_diff,
        },
    )


# ---------------------------------------------------------------------------
# 工单2：点积 / 矩阵向量乘语义规则
# LaTeX 常写成 r·r、r^T r、Σ_i r_i² 三种等价形式；p·Ap、p^T A p、
# Σ_i p_i (Ap)_i 同理。SymPy 把它们分别解析成不同表达式，导致本可判定的
# 用例落到 unknown。这里把三种写法都归一化为“乘积链”，再做结构比较。
# ---------------------------------------------------------------------------
_INNER_TRANSPOSE = re.compile(
    r"\s*\^\s*\{?\s*(?:\\top|\\intercal|"
    r"\\mathsf\s*\{\s*T\s*\}|T|\\mathrm\s*\{\s*T\s*\})\s*\}?")
_INNER_SUM_WRAPPER = re.compile(
    r"\\sum(?:\s*_\s*\{[^{}]*\}|\s*_\s*\\?[A-Za-z]+)?"
    r"(?:\s*\^\s*\{[^{}]*\}|\s*\^\s*\\?[A-Za-z0-9]+)?")
_INNER_INDEX = re.compile(
    r"\s*_\s*(?:\{[^{}]*\}|\\[A-Za-z]+|[A-Za-z0-9])")
_INNER_SQUARE = re.compile(
    r"(?P<base>\\?[A-Za-z]+|[A-Za-z])\s*_\s*(?:\{[^{}]*\}|[A-Za-z0-9])"
    r"\s*\^\s*2")
_INNER_FUNCTION_MARKERS = re.compile(
    r"\\(?:exp|log|ln|sin|cos|tan|cot|sec|csc|sqrt|operatorname|"
    r"max|min|det|abs|partial|nabla)\b")


def _inner_strip_wrappers(text: str) -> str:
    """去掉展示性包装，保留分子/分母与乘积结构。"""
    value = str(text or "")
    value = value.replace("\\[", " ").replace("\\]", " ")
    value = re.sub(r"\\(?:left|right)",
                   "", value)
    value = re.sub(r"\\(?:,|;|!|quad|qquad|displaystyle)",
                   " ", value)
    value = re.sub(
        r"\\begin\s*\{[^{}]*\}|\\end\s*\{[^{}]*\}", " ", value)
    value = value.replace("&", " ")
    return re.sub(r"\s+", " ", value).strip()


def _inner_split_top_level(text: str, separator: str = "=") -> list[str]:
    """按顶层分隔符切分，跳过括号/花括号内部。"""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in str(text or ""):
        if char in "({[":
            depth += 1
        elif char in ")}]":
            depth = max(0, depth - 1)
        if char == separator and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    parts.append("".join(current).strip())
    return [part for part in parts if part]


def _inner_match_pair(text: str, start: int,
                      opening: str, closing: str) -> int:
    """返回与 start 处 opening 匹配的 closing 位置。"""
    if start < 0 or start >= len(text) or text[start] != opening:
        return -1
    depth = 0
    for index in range(start, len(text)):
        if text[index] == opening:
            depth += 1
        elif text[index] == closing:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _inner_extract_fraction(text: str) -> tuple[Optional[str], Optional[str]]:
    """返回 (分子, 分母)；支持 \\frac 与顶层 /。"""
    value = str(text or "").strip()
    match = re.search(r"\\[dt]?frac\s*\{", value)
    if match is not None:
        start = match.end() - 1
        end = _inner_match_pair(value, start, "{", "}")
        if end > 0 and end + 1 < len(value) and value[end + 1] == "{":
            end2 = _inner_match_pair(value, end + 1, "{", "}")
            if end2 > 0:
                return value[start + 1:end], value[end + 2:end2]
    if "/" in value:
        left, _, right = value.partition("/")
        return left, right
    return None, None


def _inner_product_tokens(expr: str) -> Optional[tuple[str, ...]]:
    """把点积/矩阵向量乘展开成基名乘积链，例如 p^T A p -> (p, a, p)。"""
    text = _INNER_TRANSPOSE.sub(" ", _inner_strip_wrappers(expr))
    text = _INNER_SUM_WRAPPER.sub(" ", text)
    text = text.replace("\\cdot", " ").replace("*", " ")
    text = _INNER_SQUARE.sub(
        lambda match: f"{match.group('base')} {match.group('base')}", text)
    text = _INNER_INDEX.sub("", text)
    text = text.replace("\\mathbf", "").replace("\\mathrm", "")
    text = re.sub(r"[{}$]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    tokens: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char == "(":
            end = _inner_match_pair(text, index, "(", ")")
            if end < 0:
                return None
            inner = _inner_product_tokens(text[index + 1:end])
            if inner is None:
                return None
            tokens.extend(inner)
            index = end + 1
            continue
        if char == ")":
            index += 1
            continue
        if char.isalpha():
            tokens.append(char.lower())
            index += 1
            continue
        if char == "\\":
            match = re.match(r"\\([A-Za-z]+)", text[index:])
            if match is not None:
                name = match.group(1)
                if name in {"mathbf", "mathrm", "text", "operatorname"}:
                    index += len(match.group(0))
                    continue
                tokens.append(name.lower())
                index += len(match.group(0))
                continue
        if char.isdigit() or char in "+-=^_":
            index += 1
            continue
        return None
    if not tokens:
        return None
    return tuple(tokens)


def _inner_coefficient_name(segment: str) -> str:
    """提取等式左端的标量名（α_k / α^{(k)} / α 都归一化为 α）。"""
    text = _inner_strip_wrappers(segment)
    if _inner_extract_fraction(text)[0] is not None:
        return ""
    text = _INNER_TRANSPOSE.sub("", text)
    text = _INNER_INDEX.sub("", text)
    text = re.sub(r"\^\s*\(?\s*[A-Za-z0-9+\s]*\)?", "", text)
    text = re.sub(r"[{}]", "", text).strip()
    match = re.fullmatch(r"(\\[A-Za-z]+|[A-Za-z])", text)
    return match.group(1).lower() if match is not None else ""


def _inner_ratio_candidates(text: str) -> list[tuple[str, tuple, tuple]]:
    """提取文本中所有分式候选：(系数名, 分子链, 分母链)。"""
    segments = _inner_split_top_level(_inner_strip_wrappers(text))
    if not segments:
        return []
    coefficient = _inner_coefficient_name(segments[0])
    candidates = []
    for segment in segments:
        numerator, denominator = _inner_extract_fraction(segment)
        if numerator is None:
            continue
        num_tokens = _inner_product_tokens(numerator)
        den_tokens = _inner_product_tokens(denominator)
        if not num_tokens or not den_tokens:
            continue
        candidates.append((coefficient, num_tokens, den_tokens))
    return candidates


def _has_inner_product_notation(text: str) -> bool:
    """识别点积/转置/平方和写法，避免规则误伤普通分式。"""
    value = str(text or "")
    if "\\cdot" in value:
        return True
    if re.search(
            r"\^\s*\{?\s*(?:\\top|\\intercal|\\mathsf\s*\{\s*T\s*\}|T)\b",
            value):
        return True
    if "\\sum" in value and re.search(r"\^\s*2\b", value):
        return True
    return False


def _inner_ratio_compatible(
        lhs: tuple[str, tuple, tuple],
        rhs: tuple[str, tuple, tuple]) -> bool:
    """比较两个分式候选；系数名允许一侧缺省。"""
    name_l, num_l, den_l = lhs
    name_r, num_r, den_r = rhs
    if not num_l or not den_l or not num_r or not den_r:
        return False
    if name_l and name_r and name_l != name_r:
        return False
    return num_l == num_r and den_l == den_r


def _try_inner_product_equivalence(
        lhs_text: str,
        rhs_text: str) -> Optional[CheckResult]:
    """工单2：点积/矩阵向量乘三种写法之间的受限等价规则。

    只证明等价，不主动判错：未命中时返回 None，交回既有判定链。
    """
    lhs_value = str(lhs_text or "")
    rhs_value = str(rhs_text or "")
    if not (_has_inner_product_notation(lhs_value)
            or _has_inner_product_notation(rhs_value)):
        return None
    if _INNER_FUNCTION_MARKERS.search(lhs_value) or _INNER_FUNCTION_MARKERS.search(
            rhs_value):
        return None
    if ("frac" not in lhs_value and "/" not in lhs_value) or (
            "frac" not in rhs_value and "/" not in rhs_value):
        return None

    try:
        lhs_candidates = _inner_ratio_candidates(lhs_value)
        rhs_candidates = _inner_ratio_candidates(rhs_value)
    except Exception:  # pragma: no cover - 规则异常不应阻断主链
        return None
    if not lhs_candidates or not rhs_candidates:
        return None

    for lhs_candidate in lhs_candidates:
        for rhs_candidate in rhs_candidates:
            if not _inner_ratio_compatible(lhs_candidate, rhs_candidate):
                continue
            return CheckResult(
                True,
                "equivalent",
                lhs_value,
                rhs_value,
                "点积/矩阵向量乘的三种写法（·、转置、Σ 展开）归一化后一致",
                meta={
                    "verdict_label": "HIGH_CONFIDENCE",
                    "cross_representation_rule": "inner_product_expansion",
                    "assumptions": [
                        "点积按 Σ_i 展开，共享同一收缩索引",
                        "矩阵向量积 (Ap)_i 视为形式符号 A·p 的乘积链",
                        "迭代下标 k 只表示同一向量的第 k 步，不改变数学结构",
                    ],
                    "lhs_chain": {
                        "name": lhs_candidate[0],
                        "numerator": list(lhs_candidate[1]),
                        "denominator": list(lhs_candidate[2]),
                    },
                    "rhs_chain": {
                        "name": rhs_candidate[0],
                        "numerator": list(rhs_candidate[1]),
                        "denominator": list(rhs_candidate[2]),
                    },
                },
            )
    return None


def _multiline_lhs_key(row: dict) -> str:
    """新增：提取等式左侧的基础符号名，用于不同行数的匹配。"""
    expression = str(row.get("expr", "") or "")
    if "=" not in expression:
        return ""
    left_text = normalize_latex_symbols(expression.split("=", 1)[0])
    left_text = re.sub(r"\\[A-Za-z]+", " ", left_text)
    left_text = re.sub(r"_\{([^{}]+)\}", r"_\1", left_text)
    match = re.search(
        r"(?<![A-Za-z0-9_])([A-Za-z](?:_[A-Za-z0-9]+)?)",
        left_text,
    )
    return match.group(1) if match is not None else ""


def _pair_multiline_formula_rows(left_rows: list[dict],
                                 right_rows: list[dict]) -> list[tuple]:
    """新增：按同序、左侧基础符号或单行多候选规则配对子式。"""
    left_formulas = [row for row in left_rows if row.get("expr")]
    right_formulas = [row for row in right_rows if row.get("expr")]
    if not left_formulas or not right_formulas:
        return []

    left_count = len(left_formulas)
    right_count = len(right_formulas)
    if left_count == right_count:
        return list(zip(left_formulas, right_formulas))

    # 翻译层常给多个候选行，此时单行与每个候选分别比较。
    if left_count == 1:
        return [(left_formulas[0], right_row) for right_row in right_formulas]
    if right_count == 1:
        return [(left_row, right_formulas[0]) for left_row in left_formulas]

    unused_right = set(range(right_count))
    pairs = []
    for left_row in left_formulas:
        left_key = _multiline_lhs_key(left_row)
        matched_index = None
        if left_key:
            for right_index in sorted(unused_right):
                right_key = _multiline_lhs_key(right_formulas[right_index])
                if right_key == left_key:
                    matched_index = right_index
                    break
        if matched_index is not None:
            pairs.append((left_row, right_formulas[matched_index]))
            unused_right.remove(matched_index)

    paired_left_rows = {id(left_row) for left_row, _ in pairs}
    remaining_left = [
        row for row in left_formulas if id(row) not in paired_left_rows
    ]
    remaining_right = [
        right_formulas[index] for index in sorted(unused_right)
    ]
    # 新增（w10）：剩余行按「符号/函数名重叠度」贪心配对，而不是直接按顺序。
    # 触发场景：翻译层多输出一行辅助定义（如 n = dim(a)），顺序配对会让
    # cosθ 公式与这一行错配，进而把可判定的比较变成 unknown。
    if remaining_left and remaining_right:
        scored = []
        for left_index, left_row in enumerate(remaining_left):
            for right_index, right_row in enumerate(remaining_right):
                scored.append((
                    _row_similarity(left_row, right_row),
                    left_index, right_index))
        scored.sort(key=lambda item: item[0], reverse=True)
        used_left: set[int] = set()
        used_right: set[int] = set()
        for score, left_index, right_index in scored:
            if score <= 0:
                break
            if left_index in used_left or right_index in used_right:
                continue
            pairs.append((remaining_left[left_index],
                          remaining_right[right_index]))
            used_left.add(left_index)
            used_right.add(right_index)
        remaining_left = [
            row for index, row in enumerate(remaining_left)
            if index not in used_left
        ]
        remaining_right = [
            row for index, row in enumerate(remaining_right)
            if index not in used_right
        ]
    pairs.extend(zip(remaining_left, remaining_right))
    return pairs


def _row_similarity(left_row: dict, right_row: dict) -> float:
    """新增（w10）：两行子式的符号/函数名 Jaccard 相似度（0~1）。"""
    try:
        left_tokens = _formula_tokens(left_row.get("expr", ""))
        right_tokens = _formula_tokens(right_row.get("expr", ""))
    except Exception:
        return 0.0
    if not left_tokens or not right_tokens:
        return 0.0
    shared = left_tokens & right_tokens
    return len(shared) / len(left_tokens | right_tokens)


_TOKEN_NOISE = {
    "frac", "dfrac", "sqrt", "left", "right", "cdot", "times", "sum",
    "prod", "text", "mathrm", "mathbf", "operatorname", "begin", "end",
    "bmatrix", "pmatrix", "matrix", "aligned", "quad", "partial", "nabla",
}


def _formula_tokens(text: str) -> set[str]:
    """把子式拆成字母 token（去掉 LaTeX 命令名与常见排版词）。"""
    raw = re.findall(r"[A-Za-z]{1,}", str(text or ""))
    return {
        token.lower() for token in raw
        if token.lower() not in _TOKEN_NOISE
    }


def _collect_multiline_conditions(row_groups: list[list[dict]]) -> tuple:
    """新增：收集去重后的条件及带行号的证据。"""
    conditions = []
    evidence = []
    for group_index, rows in enumerate(row_groups):
        side = "left" if group_index == 0 else "right"
        for row in rows:
            condition = str(row.get("condition", "") or "").strip()
            if not condition:
                continue
            evidence.append({
                "side": side,
                "row": row.get("row"),
                "condition": condition,
            })
            if condition not in conditions:
                conditions.append(condition)
    return conditions, evidence


def _compare_multiline(lhs_rows: list[dict], rhs_rows: list[dict],
                       lhs_text: str = "", rhs_text: str = "",
                       symbols=None, *, tol: float = 1e-8,
                       samples: int = 24) -> CheckResult:
    """新增：逐式解析并比较多行公式，聚合为结构化结论。"""
    conditions, condition_evidence = _collect_multiline_conditions(
        [lhs_rows, rhs_rows])
    base_meta = {
        "multiline": True,
        "conditions": conditions,
        "condition_evidence": condition_evidence,
    }

    try:
        pairs = _pair_multiline_formula_rows(lhs_rows, rhs_rows)
        if not pairs:
            base_meta.update({
                "rows": 0,
                "row_results": [],
                "verdict_label": "UNKNOWN",
            })
            return CheckResult(
                False, "unknown", str(lhs_text), str(rhs_text),
                "多行公式没有可参与 SymPy 比较的子式",
                meta=base_meta,
            )

        row_results = []
        equivalent_count = 0
        failed_pair = None
        unknown_messages = []
        for pair_index, (left_row, right_row) in enumerate(pairs, start=1):
            left_expression, left_error = latex_to_expr(left_row.get("expr", ""))
            right_expression, right_error = latex_to_expr(
                right_row.get("expr", ""))
            result_data = {
                "pair": pair_index,
                "left_row": left_row.get("row"),
                "right_row": right_row.get("row"),
                "left_expr": left_row.get("expr", ""),
                "right_expr": right_row.get("expr", ""),
                "condition": (
                    left_row.get("condition", "")
                    or right_row.get("condition", "")
                ),
            }

            if left_expression is None or right_expression is None:
                result_data.update({
                    "status": "unknown",
                    "detail": (
                        f"左侧解析失败: {left_error}"
                        if left_expression is None
                        else f"右侧解析失败: {right_error}"
                    ),
                })
                unknown_messages.append(
                    f"第 {pair_index} 子式{result_data['detail']}")
                row_results.append(result_data)
                continue

            comparison = compare_expressions(
                left_expression,
                right_expression,
                symbols,
                tol=tol,
                samples=samples,
            )
            # 新增：只有左侧符号键一致的配对才允许下 CONFIRMED_ERROR；
            # 兜底顺序配对结论不可靠，降级为 unknown，避免假阳性。
            left_key = _multiline_lhs_key(left_row)
            right_key = _multiline_lhs_key(right_row)
            trusted_pair = bool(
                left_key and right_key and left_key == right_key)
            if comparison.ok:
                result_data.update({
                    "status": "equivalent",
                    "detail": comparison.detail,
                    "trusted_pair": trusted_pair,
                })
                equivalent_count += 1
            elif comparison.status in {"not_equivalent", "shape_mismatch"}:
                if trusted_pair:
                    result_data.update({
                        "status": "not_equivalent",
                        "detail": comparison.detail,
                        "trusted_pair": True,
                    })
                    if failed_pair is None:
                        failed_pair = (left_row, right_row, comparison)
                else:
                    result_data.update({
                        "status": "unknown",
                        "detail": (
                            f"兜底配对（{left_key or '?'} vs "
                            f"{right_key or '?'}）无法确认不等价："
                            f"{comparison.detail}"
                        ),
                        "trusted_pair": False,
                    })
                    unknown_messages.append(
                        f"第 {pair_index} 子式兜底配对无法确认")
            else:
                result_data.update({
                    "status": "unknown",
                    "detail": comparison.detail,
                })
                unknown_messages.append(
                    f"第 {pair_index} 子式无法判定: {comparison.detail}")
            row_results.append(result_data)

        # 条件按证据保留，但不参与 SymPy 数值或代数运算。
        for row in lhs_rows + rhs_rows:
            condition = str(row.get("condition", "") or "").strip()
            if condition:
                row_results.append({
                    "left_row": row.get("row"),
                    "right_row": "",
                    "left_expr": "",
                    "right_expr": "",
                    "condition": condition,
                    "status": "condition",
                    "detail": "条件仅作为证据，不参与 SymPy 运算",
                })

        formula_count = max(
            len([row for row in lhs_rows if row.get("expr")]),
            len([row for row in rhs_rows if row.get("expr")]),
        )
        base_meta.update({
            "rows": formula_count,
            "row_results": row_results,
        })

        has_single_side_candidates = (
            len([row for row in lhs_rows if row.get("expr")]) == 1
            or len([row for row in rhs_rows if row.get("expr")]) == 1
        )
        if (
            equivalent_count == len(pairs)
            or (has_single_side_candidates and equivalent_count > 0)
        ):
            base_meta["verdict_label"] = "HIGH_CONFIDENCE"
            detail_total = (
                equivalent_count
                if has_single_side_candidates
                else formula_count
            )
            return CheckResult(
                True,
                "equivalent",
                str(lhs_text),
                str(rhs_text),
                f"多行公式 {equivalent_count}/{detail_total} 子式全部等价",
                meta=base_meta,
            )

        if failed_pair is not None:
            left_row, right_row, comparison = failed_pair
            failed_row = (
                left_row.get("row")
                if left_row.get("row") is not None
                else right_row.get("row")
            )
            base_meta.update({
                "failed_row": failed_row,
                "verdict_label": "CONFIRMED_ERROR",
            })
            return CheckResult(
                False,
                "not_equivalent",
                str(lhs_text),
                str(rhs_text),
                f"多行公式第 {failed_row} 子式不等价: {comparison.detail}",
                diff_points=comparison.diff_points,
                meta=base_meta,
            )

        base_meta["verdict_label"] = "UNKNOWN"
        detail = "多行公式无法完成逐式判定"
        if unknown_messages:
            detail = f"{detail}: {'; '.join(unknown_messages[:3])}"
        return CheckResult(
            False,
            "unknown",
            str(lhs_text),
            str(rhs_text),
            detail,
            meta=base_meta,
        )
    except Exception as exc:
        base_meta.update({
            "rows": 0,
            "row_results": [],
            "verdict_label": "UNKNOWN",
            "error": f"{type(exc).__name__}: {exc}",
        })
        return CheckResult(
            False,
            "unknown",
            str(lhs_text),
            str(rhs_text),
            f"多行公式处理失败: {type(exc).__name__}: {exc}",
            meta=base_meta,
        )


def _try_matrix_chain_rule(
        lhs_text: str,
        rhs_text: str,
) -> Optional[CheckResult]:
    """工单2：调用矩阵连乘规则，异常时退回既有校验链。"""
    try:
        return try_matrix_chain_equivalence(lhs_text, rhs_text)
    except Exception:  # pragma: no cover - 规则异常不应阻断主链
        return None


def verify_latex_vs_code(latex: str, code_expr: str, symbols=None, *,
                         tol: float = 1e-8, samples: int = 24) -> CheckResult:
    """端到端：LaTeX（OCR/手写） 与 C++/数学表达式字符串 比较。"""
    # 工单2：矩阵连乘（ABC / 逐元素展开 / 带括号求值顺序）先走专项规则。
    matrix_chain_result = _try_matrix_chain_rule(latex, code_expr)
    if matrix_chain_result is not None:
        return matrix_chain_result
    # 工单2：点积/矩阵向量乘的 ·、转置、Σ 展开三种写法先做语义归一化。
    inner_product_result = _try_inner_product_equivalence(latex, code_expr)
    if inner_product_result is not None:
        return inner_product_result
    # 新增：任一侧是确定的多行公式时，先拆分再逐式比较。
    # 新增：先尝试矩阵乘跨表示规则，命中后不再进入通用多行解析。
    cross_representation = _try_cross_representation_equivalence(
        latex, code_expr)
    if cross_representation is not None:
        return cross_representation
    latex_is_multiline, latex_multiline_reason = _detect_multiline_latex(
        latex)
    code_is_multiline, code_multiline_reason = _detect_multiline_latex(
        code_expr)
    if latex_is_multiline or code_is_multiline:
        lhs_rows = _split_multiline_latex(latex)
        rhs_rows = _split_multiline_latex(code_expr)
        if not lhs_rows or not rhs_rows:
            reason = (
                latex_multiline_reason
                if latex_is_multiline
                else code_multiline_reason
            )
            return CheckResult(
                False,
                "unknown",
                str(latex),
                str(code_expr),
                f"无法判定：{reason}，但未能拆出有效子式",
                meta={
                    "multiline": True,
                    "conditions": [],
                    "row_results": [],
                    "verdict_label": "UNKNOWN",
                },
            )
        return _compare_multiline(
            lhs_rows,
            rhs_rows,
            lhs_text=str(latex),
            rhs_text=str(code_expr),
            symbols=symbols,
            tol=tol,
            samples=samples,
        )

    lhs, e1 = latex_to_expr(latex)
    if lhs is None:
        # 修改：解析失败表示无法判定，不等同于已证明错误。
        return CheckResult(
            False,
            "unknown",
            latex,
            code_expr,
            f"无法判定：LaTeX 解析失败: {e1}",
        )
    rhs, e2 = parse_python_expr(code_expr, symbols)
    if rhs is None:
        # 修改：表达式解析失败同样归入 unknown。
        return CheckResult(
            False,
            "unknown",
            latex,
            code_expr,
            f"无法判定：表达式解析失败: {e2}",
        )
    result = compare_expressions(lhs, rhs, symbols, tol=tol, samples=samples)
    if result.ok:
        return result
    # 新增（w10）：大小写不敏感的单字母符号回退。
    # 手写体 OCR 常把 x 读成 X；当严格比较失败、而两侧唯一差异是单字母
    # 符号的大小写时，用统一小写后的表达式重试一次。该回退只在严格比较
    # 未通过时生效，不改变原有判定路径。
    mapped = _try_case_insensitive_symbols(
        lhs, rhs, symbols, tol=tol, samples=samples)
    if mapped is not None:
        mapped.meta = dict(mapped.meta or {})
        mapped.meta.update({
            "cross_representation_rule": "case_insensitive_symbols",
            "assumptions": [
                "单字母变量的大小写差异视为同一变量（OCR 大小写不可靠）",
            ],
        })
        return mapped
    return result


# 新增（w10）：单字母符号大小写不敏感回退。
_SINGLE_LETTER = re.compile(r"^[A-Za-z]$")


def _case_insensitive_mapping(lhs, rhs) -> dict:
    """构造「大小写不同但同形」的单字母符号映射；有歧义时返回空。"""
    try:
        left = {
            symbol.name for symbol in lhs.free_symbols
            if _SINGLE_LETTER.match(symbol.name)
        }
        right = {
            symbol.name for symbol in rhs.free_symbols
            if _SINGLE_LETTER.match(symbol.name)
        }
    except Exception:
        return {}
    mapping: dict = {}
    for name in sorted(left):
        # 同一侧不允许出现 "X 与 x 都被映射到 x" 的歧义。
        candidates = [
            other for other in right
            if other != name and other.lower() == name.lower()
        ]
        if len(candidates) != 1:
            continue
        other = candidates[0]
        if other.lower() in left:
            # 左侧本身已经使用该小写字母，说明大小写是刻意区分的，跳过。
            continue
        target = sp.Symbol(name.lower())
        mapping[sp.Symbol(name)] = target
        mapping[sp.Symbol(other)] = target
    return mapping


def _try_case_insensitive_symbols(lhs, rhs, symbols=None, *,
                                  tol: float = 1e-8,
                                  samples: int = 24):
    """大小写归一化后再比较；无映射或仍不等价时返回 None。"""
    mapping = _case_insensitive_mapping(lhs, rhs)
    if not mapping:
        return None
    try:
        mapped_lhs = lhs.subs(mapping)
        mapped_rhs = rhs.subs(mapping)
    except Exception:
        return None
    if mapped_lhs == lhs and mapped_rhs == rhs:
        return None
    retry = compare_expressions(
        mapped_lhs, mapped_rhs, symbols, tol=tol, samples=samples)
    if not retry.ok:
        return None
    retry.detail = (
        f"{retry.detail}（大小写归一化后等价："
        f"{', '.join(f'{k}->{v}' for k, v in mapping.items())}）")
    return retry



# 新增：把原有 SymPy 校验包装为可插拔实现，数学逻辑保持不变。
class SymPyVerifier(VerifierBase):
    """现有符号校验逻辑的适配器。"""

    name = "sympy"

    def verify(self, latex_expr: str, cpp_expr_or_code: str,
               context: Optional[dict] = None) -> Verdict:
        # 工单2：矩阵连乘专项规则优先于其它判定。
        matrix_chain_result = _try_matrix_chain_rule(
            latex_expr, cpp_expr_or_code)
        if matrix_chain_result is not None:
            return matrix_chain_result
        # 工单2：点积/矩阵向量乘规则必须先于已解析表达式复用，
        # 否则 α_k 与 α 的下标差异会绕过语义归一化。
        inner_product_result = _try_inner_product_equivalence(
            latex_expr, cpp_expr_or_code)
        if inner_product_result is not None:
            return inner_product_result
        # 修改：允许主链路复用已解析表达式，避免 LaTeX/矩阵被二次解析。
        # 新增：即使已有预解析表达式，也先用原始文本尝试矩阵乘跨表示规则。
        cross_representation = _try_cross_representation_equivalence(
            latex_expr, cpp_expr_or_code)
        if cross_representation is not None:
            return cross_representation
        if context:
            prepared_lhs = context.get("preparsed_lhs_expr")
            prepared_rhs = context.get("preparsed_rhs_expr")
            if prepared_lhs is not None and prepared_rhs is not None:
                return compare_expressions(prepared_lhs, prepared_rhs)

        # 兼容包装：直接调用原入口，不复制、不改写既有比较逻辑。
        return verify_latex_vs_code(latex_expr, cpp_expr_or_code)


# ---------------------------------------------------------------- 报告生成
def format_report(results: list) -> str:
    """把多个 CheckResult 汇总成 Markdown 报告。"""
    total = len(results)
    passed = sum(1 for r in results if r.ok)
    lines = ["# 数学校验报告", "",
             f"- 用例总数：**{total}**",
             f"- 通过：**{passed}**",
             f"- 失败：**{total - passed}**",
             f"- 通过率：**{(passed / total * 100 if total else 0):.1f}%**", ""]
    for idx, r in enumerate(results, 1):
        lines += [f"## 用例 {idx}", r.to_markdown(), ""]
    return "\n".join(lines)


def save_report(results: list, path: str) -> str:
    text = format_report(results)
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return str(p)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # 自测
    r1 = verify_latex_vs_code(r"\frac{a}{b} + \sqrt{x}", "a/b + sqrt(x)")
    print("case1:", r1.status, r1.ok)
    r2 = verify_latex_vs_code(r"\sin^2\theta + \cos^2\theta", "1")
    print("case2:", r2.status, r2.ok)
    r3 = verify_latex_vs_code(r"x + 1", "x + 2")
    print("case3:", r3.status, r3.ok, r3.detail)
