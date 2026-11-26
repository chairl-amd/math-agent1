# -*- coding: utf-8 -*-
"""翻译层 LaTeX 符号归一化工具。

本模块只依赖 Python 标准库，供校验层和翻译层共用。
"""
from __future__ import annotations

import re


_SYMBOL_WRAPPER_PATTERN = re.compile(
    r"\\(?:mathbf|boldsymbol|bm|vec|hat|bar|overline|underline|"
    r"mathcal|mathrm|mathit|text)\b"
)
_TEXT_WRAPPER_COMMAND = "text"

_ITERATION_SUPERSCRIPT_PATTERN = re.compile(
    r"(?P<base>"
    r"(?:\\[A-Za-z]+|[A-Za-z][A-Za-z0-9]*)"
    r"(?:_(?:\{[^{}]*\}|[A-Za-z0-9]))?"
    r")"
    r"\s*\^\s*\{\s*"
    r"(?:"
    r"\(\s*k\s*(?:\+\s*1\s*)?\)"
    r"|\(\s*t\s*(?:\+\s*1\s*)?\)"
    r"|\(\s*n\s*(?:\+\s*1\s*)?\)"
    r"|new|next"
    r")"
    r"\s*\}"
)
_ASSIGN_ARROW_PATTERN = re.compile(
    r"\\(?:leftarrow|gets|longleftarrow)\b"
)


def _find_matching_brace(text: str, opening_index: int) -> int:
    """返回与 opening_index 处左花括号匹配的右花括号位置。"""
    if opening_index < 0 or opening_index >= len(text) or text[opening_index] != "{":
        return -1

    depth = 0
    for index in range(opening_index, len(text)):
        character = text[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _strip_symbol_wrappers_once(text: str) -> tuple[str, bool]:
    """单次扫描并去掉一层可识别的包装命令。"""
    output = []
    index = 0
    changed = False

    while index < len(text):
        match = _SYMBOL_WRAPPER_PATTERN.match(text, index)
        if match is None:
            output.append(text[index])
            index += 1
            continue

        argument_index = match.end()
        while argument_index < len(text) and text[argument_index].isspace():
            argument_index += 1
        closing_index = _find_matching_brace(text, argument_index)
        if closing_index < 0:
            # 新增：兼容 \mathbf r 这类不带花括号的单字符参数。
            single_argument = re.match(
                r"\s*([A-Za-z0-9])", text[argument_index:])
            if single_argument is None:
                output.append(text[index])
                index += 1
                continue
            command = match.group(0)[1:]
            if command != _TEXT_WRAPPER_COMMAND:
                output.append(" ")
                output.append(single_argument.group(1))
                output.append(" ")
            index = argument_index + single_argument.end()
            changed = True
            continue

        command = match.group(0)[1:]
        if command != _TEXT_WRAPPER_COMMAND:
            # 新增：包装去掉后补分隔空格，避免 \mathbf{A}\mathbf{p} 粘连成 Ap。
            output.append(" ")
            output.append(text[argument_index + 1:closing_index])
            output.append(" ")
        index = closing_index + 1
        changed = True

    return "".join(output), changed


def strip_symbol_wrappers(text: str) -> str:
    """递归去掉符号包装，并整体删除 \\text{} 条件文字。"""
    if not isinstance(text, str):
        return ""

    result = text
    # 最多处理 10 层，防止异常输入导致无限循环。
    for _ in range(10):
        result, changed = _strip_symbol_wrappers_once(result)
        if not changed:
            break
    return result


def normalize_iteration_superscripts(text: str) -> str:
    """删除迭代标记上标，保留普通数学幂不变。"""
    if not isinstance(text, str):
        return ""
    return _ITERATION_SUPERSCRIPT_PATTERN.sub(
        lambda match: match.group("base"), text
    )


def normalize_assign_arrows(text: str) -> str:
    """把赋值箭头统一为等号，其他方向箭头保持不变。"""
    if not isinstance(text, str):
        return ""
    return _ASSIGN_ARROW_PATTERN.sub("=", text)


# ---------- 工单2：求和/求积上下限补全 ----------
# OCR 常输出 \sum_i 这类不带花括号、甚至完全没有上下限的写法，
# SymPy 的 parse_latex 只接受"上下限齐全"的形式，因此这里做两件事：
# 1. 把 \sum_i 补成 \sum_{i}；
# 2. 对仍然只有下标、没有上标的求和/求积，补齐 i=0..n-1（全遍历的默认语义）。
_SUM_LIKE = r"sum|prod|coprod"
_UNBRACED_SUM_SUBSCRIPT = re.compile(
    r"\\(?P<cmd>" + _SUM_LIKE + r")\s*_\s*"
    r"(?P<low>\\[A-Za-z]+|[A-Za-z0-9])"
)
_UNBRACED_SUM_SUPERSCRIPT = re.compile(
    r"(\\(?:" + _SUM_LIKE + r")\s*_\s*"
    r"(?:\{[^{}]*\}|\\[A-Za-z]+|[A-Za-z0-9]))\s*\^\s*"
    r"(?P<up>\\[A-Za-z]+|[A-Za-z0-9])(?![A-Za-z0-9])"
)
_SUBSCRIPT_ONLY_LIMIT = re.compile(
    r"\\(?P<cmd>" + _SUM_LIKE + r")\s*_\s*\{(?P<low>[^{}]*)\}(?!\s*\^)"
)
# 只有纯索引名才自动补上界；含 =、\in、逗号等写法保持原样。
_PURE_INDEX = re.compile(r"(?:\\[A-Za-z]+|[A-Za-z0-9_]+)")


def _brace_first_pass(text: str) -> str:
    r"""第一步：\sum_i -> \sum_{i}，\sum_i^n -> \sum_{i}^{n}。"""
    braced = _UNBRACED_SUM_SUBSCRIPT.sub(
        lambda match: f"\\{match.group('cmd')}_{{{match.group('low')}}}", text)
    return _UNBRACED_SUM_SUPERSCRIPT.sub(
        lambda match: f"{match.group(1)}^{{{match.group('up')}}}", braced)


def _expand_subscript_only(match: re.Match) -> str:
    """第二步：只有下标的求和补成全遍历上界，已带下界的不动。"""
    low = match.group("low").strip()
    if not _PURE_INDEX.fullmatch(low):
        return match.group(0)
    return f"\\{match.group('cmd')}_{{{low}=0}}^{{n-1}}"


def normalize_sum_limits(text: str) -> str:
    """补全求和/求积的上下限，使 parse_latex 能接受 OCR 的简写形式。

    示例：
    - ``\\sum_i a_i b_i`` -> ``\\sum_{i=0}^{n-1} a_i b_i``
    - ``\\sum_{k=0}^{n-1} a_k`` 保持原样
    - ``\\sum_{i \\in S} x_i`` 保持原样（索引不是纯变量名）
    """
    if not isinstance(text, str) or not text:
        return ""
    braced = _brace_first_pass(text)
    return _SUBSCRIPT_ONLY_LIMIT.sub(_expand_subscript_only, braced)


# ---------- 工单2：迭代下标与时间标记归一化 ----------
# 翻译层常把 w_{t+1} 写成 w^{t+1} 或干脆省略下标，这里把三种写法统一。
_ITERATION_SUBSCRIPT = re.compile(
    r"(?P<base>(?:\\[A-Za-z]+|[A-Za-z][A-Za-z0-9]*))"
    r"\s*_\s*(?:\{\s*)?"
    r"(?:"
    r"\(\s*(?:k|t|n)\s*(?:\+\s*1\s*)?\)"
    r"|(?:k|t|n)\s*\+\s*1"
    r"|new|next"
    r")"
    r"\s*\}?"
)
# 单独的时间步下标 g_t / w_t：与 g / w 视为同一变量（k 不作此处理，避免破坏求和索引）。
_TIME_STEP_SUBSCRIPT = re.compile(
    r"(?P<base>(?:\\[A-Za-z]+|[A-Za-z]))\s*_\s*(?:\{\s*t\s*\}|t(?![A-Za-z0-9_]))"
)


def normalize_iteration_subscripts(text: str) -> str:
    """删除迭代/时间步下标，保留普通索引（如 a_i、b_{kj}）不变。"""
    if not isinstance(text, str) or not text:
        return ""
    result = _ITERATION_SUBSCRIPT.sub(lambda m: m.group("base"), text)
    return _TIME_STEP_SUBSCRIPT.sub(lambda m: m.group("base"), result)


# ---------- 工单2：范数符号归一化 ----------
# SymPy 的 parse_latex 不支持 \| ... \|，这里统一改写为绝对值竖线。
_NORM_DELIMITER = re.compile(
    r"\\left\s*(?:\\\||\\[lr]?Vert|\\vert|\\lvert|\\rvert|\\lVert|\\rVert)\s*"
    r"|\\right\s*(?:\\\||\\[lr]?Vert|\\vert|\\lvert|\\rvert|\\lVert|\\rVert)\s*"
    r"|\\[lr]?Vert(?![A-Za-z])|\\[lr]?vert(?![A-Za-z])")
_NORM_LABELED = re.compile(r"\|\s*_\s*(?:\{\s*[A-Za-z0-9]+\s*\}|[A-Za-z0-9])")
_NORM_BAR = re.compile(r"\\\|")


def normalize_norm_bars(text: str) -> str:
    """把 ``\\|x\\|_2`` / ``\\lVert x\\rVert_2`` 这类范数写法改写为 ``|x|``。

    顺序很关键：先处理 ``\\left\\|`` / ``\\right\\|`` / ``\\lVert`` 等定界符，
    再剥掉紧跟的范数下标（_2、_F 等），最后处理剩余的 ``\\|``。
    w10 补充：翻译层经常输出 ``\\lVert x\\rVert_2``、``\\Vert x\\Vert``；
    这些写法此前未被识别，会被解析成 ``lVert*(rVert_2*x)`` 这种伪符号。
    """
    if not isinstance(text, str) or not text:
        return ""
    result = _NORM_DELIMITER.sub("|", text)
    result = _NORM_LABELED.sub("|", result)
    return _NORM_BAR.sub("|", result)


# ---------- 工单1（w10）：记号变体归一化 ----------
# 弱点扫描发现三类高频表示差异会让 SymPy 直接解析失败或产生伪符号：
#   1. `{a \over b}` 这种老式分式；parse_latex 不支持 \over；
#   2. `\operatorname{tr}` / `\mathrm{Tr}` / `\text{tr}` 写法不统一，
#      多字母函数名会被拆成逐字母相乘（tr(A) -> t*r(A)）；
#   3. `x^{\mathsf{T}}x` 这类转置记号会被解析成含 mathsf 的伪符号。
# 这里只做「同义改写」，不改变任何数学含义。

# 常见算子名的规范写法：统一成小写，保证两侧写法一致。
_OPERATOR_ALIASES = {
    "tr": "tr",
    "trace": "tr",
    "det": "det",
    "determinant": "det",
    "rank": "rank",
    "diag": "diag",
    "argmax": "argmax",
    "argmin": "argmin",
    "relu": "relu",
    "softmax": "softmax",
    "sigmoid": "sigmoid",
}
# 只匹配纯字母名字，避免误吃 \text{if }、\mathrm{d}x 这类内容。
_WRAPPER_OPERATOR = re.compile(
    r"\\(?:mathrm|operatorname|text|mbox)\*?\s*\{\s*(?P<name>[A-Za-z]{2,})\s*\}"
)
_OVER_BRACED = re.compile(r"\{([^{}]*?)\s*\\over\s*([^{}]*?)\}")
_OVER_INLINE = re.compile(
    r"(?P<num>[A-Za-z0-9]+)\s*\\over\s*(?P<den>[A-Za-z0-9]+)")
_MATHSAF_WRAPPER = re.compile(r"\\mathsf\s*\{([^{}]*)\}")
_TRANSPOSE_COMMANDS = re.compile(
    r"\\(?:top|intercal|mathsf\s*\{\s*T\s*\}|mathsf\s*\{\s*t\s*\})")


def _canonical_operator(match: re.Match) -> str:
    """把已知算子名的各种包装统一成 ``\\mathrm{name}``。"""
    raw = match.group("name")
    alias = _OPERATOR_ALIASES.get(raw.lower())
    if alias is None:
        # 未登记的算子保持原样，只统一外层命令拼写。
        return "\\mathrm{" + raw + "}"
    return "\\mathrm{" + alias + "}"


def normalize_notation_variants(text: str) -> str:
    r"""归一化分式/算子/转置写法差异。

    示例：
    - ``{a \over b}``      -> ``\frac{a}{b}``
    - ``\operatorname{tr}`` -> ``\mathrm{tr}``
    - ``\text{Trace}``      -> ``\mathrm{tr}``
    - ``x^{\mathsf{T}}x``   -> ``x^{\mathrm{T}}x``
    """
    if not isinstance(text, str) or not text:
        return ""
    result = text
    # 1) 老式分式：先处理带花括号的整体，再处理简单的单 token 形式。
    for _ in range(5):
        new_result = _OVER_BRACED.sub(
            lambda m: "\\frac{%s}{%s}" % (m.group(1), m.group(2)), result)
        if new_result == result:
            break
        result = new_result
    result = _OVER_INLINE.sub(
        lambda m: "\\frac{%s}{%s}" % (m.group("num"), m.group("den")), result)
    # 2) 算子包装拼写统一（避免 tr(A) 被拆成 t*r(A) 且两侧写法不同）。
    result = _WRAPPER_OPERATOR.sub(_canonical_operator, result)
    # 3) \mathsf{T} / \top 这类转置记号统一成 \mathrm{T}。
    result = _MATHSAF_WRAPPER.sub(
        lambda m: "\\mathrm{%s}" % m.group(1).strip(), result)
    result = _TRANSPOSE_COMMANDS.sub(lambda m: "\\mathrm{T}", result)
    return result


def normalize_subscript_commas(text: str) -> str:
    r"""去掉下标里的逗号：``a_{i,i}`` 与 ``a_{ii}`` 表示同一元素。

    只处理下标花括号内部的逗号和空格，避免影响函数参数与求和条件。
    """
    if not isinstance(text, str) or not text:
        return ""
    pattern = re.compile(r"_\s*\{([^{}]*)\}")

    def _clean(match: re.Match) -> str:
        body = match.group(1)
        cleaned = body.replace(",", "").replace(" ", "")
        return "_{" + (cleaned or body) + "}"

    return pattern.sub(_clean, text)


def normalize_latex_symbols(text: str) -> str:
    """组合执行符号包装、记号变体、迭代上标和赋值箭头归一化。

    w10 调整：先统一 ``\\operatorname`` / ``\\text`` / ``\\mathsf`` 等写法，
    再剥掉包装命令。这样两侧无论原本写成哪种包装，都会落到同一个等价形式
    （例如 ``\\operatorname{tr}(A)`` 与 ``\\mathrm{tr}(A)`` 都变成 ``tr(A)``），
    不会再出现一侧留 ``mathrm`` 伪符号、另一侧被拆成 t*r 的不对称结果。
    """
    if not isinstance(text, str):
        return ""

    normalized = normalize_notation_variants(text)
    normalized = strip_symbol_wrappers(normalized)
    normalized = normalize_notation_variants(normalized)
    normalized = normalize_iteration_superscripts(normalized)
    return normalize_assign_arrows(normalized)
