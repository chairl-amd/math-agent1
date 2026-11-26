# -*- coding: utf-8 -*-
"""工单2：矩阵连乘（matrix chain）跨表示等价规则。

问题：同一段矩阵连乘，图片侧与 C++ 翻译侧常用不同表示：

    乘积记号        D = A B C
    逐元素展开      d_{ij} = \\sum_k \\sum_l a_{ik} b_{kl} c_{lj}
    求值顺序        ((AB)C)D  /  (AB)C = A(BC)
    函数签名        chain4(A,B,C,D) = (((AB)C)D)

SymPy 的 ``parse_latex`` 既不会把并列字母 ``ABC`` 当作矩阵连乘，也无法
直接证明 ``(AB)C = A(BC)``；而逐元素展开里 ``\\sum`` 的先后顺序、求和变量
命名又各不相同。本模块因此用**受限规则**单独判定：

1. 乘积证据：把只由单字母因子、括号、乘法号组成的式子解析成有序因子序列；
   序列一致即表示“同一条连乘链”（矩阵乘法结合律），序列只是顺序不同则判不等；
2. 逐元素证据：把逐元素展开式解析成 (自由下标, 因子序列及其下标元组)，
   求和变量按首次出现顺序规范化后比较；一致即表示同一索引收缩。

规则只在两侧都拿到“链长 >= 3”的证据时生效，且不涉及数值采样，避免误伤
既有的二元矩阵乘规则（``verify._try_cross_representation_equivalence``）。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

# 语义上等价于矩阵乘法的辅助函数名；其余多字母名一律视为不可解释的原子。
_MUL_FUNCTIONS = frozenset({
    "mul", "matmul", "mm", "dot", "multiply", "times", "gemm",
})
# 规范化阶段把 \\qquad 等排版命令变成换行，便于按“一行一个公式”处理。
_QUAD_COMMANDS = re.compile(r"\\(?:qquad|quad|hspace\*?\{[^{}]*\})")
_ENV_COMMANDS = re.compile(r"\\(?:begin|end)\s*\{[^{}]+\}")
_TEXT_WRAPPERS = re.compile(
    r"\\(?:operatorname|mathrm|mathbf|mathit|text|boldsymbol|bm)\s*"
    r"\{([^{}]*)\}"
)
_SPACING_COMMANDS = re.compile(r"\\[,;:! ]|~")
_SUM_TOKEN = re.compile(
    r"\\sum\s*(?:_\s*(?:\{[^{}]*\}|[A-Za-z0-9]))?"
    r"\s*(?:\^\s*(?:\{[^{}]*\}|[A-Za-z0-9]))?"
)
_INDEX_LHS = re.compile(
    r"_\s*\{\s*([A-Za-z](?:\s*,?\s*[A-Za-z])*)\s*\}\s*$"
)
_TERM = re.compile(
    r"([A-Za-z])\s*_\s*\{\s*([A-Za-z](?:\s*,?\s*[A-Za-z])*)\s*\}"
)


def normalize_text(text: str) -> str:
    """去掉排版包装，把乘法号、矩阵环境与并列公式转成可解析的纯文本。

    ``\\operatorname{name}`` 这类“代码标识符”会被包成 ``@name@``，以便
    与数学里的并列单字母变量（如 ``ABCD`` 表示 A·B·C·D）区分开。
    """
    value = str(text or "")
    value = value.replace("\\left", " ").replace("\\right", " ")
    # 下标常用 \ell / \imath / \jmath 表示单字母索引，统一成普通字母。
    value = value.replace("\\ell", "l").replace("\\imath", "i")
    value = value.replace("\\jmath", "j")
    value = _TEXT_WRAPPERS.sub(lambda m: " @" + m.group(1) + "@ ", value)
    value = _QUAD_COMMANDS.sub("\n", value)
    value = _ENV_COMMANDS.sub("\n", value)
    value = value.replace("\\cdot", "*").replace("\\times", "*")
    value = value.replace("\\\\", "\n").replace("&", " ")
    value = _SPACING_COMMANDS.sub(" ", value)
    value = value.replace("\\[", " ").replace("\\]", " ")
    value = value.replace("$", " ")
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()


def _split_top_level(text: str) -> List[str]:
    """按顶层（不在括号/花括号内）的等号切分等式片段。"""
    segments: List[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(text):
        if char in "({[":
            depth += 1
        elif char in ")}]":
            depth = max(0, depth - 1)
        elif char == "=" and depth == 0:
            segments.append(text[start:index])
            start = index + 1
    segments.append(text[start:])
    return [segment.strip() for segment in segments]


class _ProductParser:
    """把“单字母因子 + 括号 + 乘法号”的表达式解析成有序因子序列。"""

    def __init__(self, expression: str):
        self.expression = expression
        self.length = len(expression)

    def parse(self) -> Optional[Tuple[str, ...]]:
        factors, index = self._product(0, "")
        if factors is None:
            return None
        while index < self.length and self.expression[index].isspace():
            index += 1
        if index != self.length:
            return None
        if not factors:
            return None
        return tuple(factors)

    def _product(self, index: int, stop: str
                 ) -> Tuple[Optional[List[str]], int]:
        factors: List[str] = []
        while index < self.length:
            char = self.expression[index]
            if stop and char == stop:
                break
            if char.isspace() or char in "*,":
                index += 1
                continue
            factor, index = self._factor(index)
            if factor is None:
                return None, index
            factors.extend(factor)
        return factors, index

    def _factor(self, index: int) -> Tuple[Optional[List[str]], int]:
        char = self.expression[index]
        if char == "(":
            inner, index = self._product(index + 1, ")")
            if inner is None or index >= self.length or self.expression[index] != ")":
                return None, index
            return inner, index + 1
        if char == "@":
            # \operatorname{name} 标记：只认矩阵乘辅助函数，其余视为不可解释。
            closing = self.expression.find("@", index + 1)
            if closing < 0:
                return None, index
            name = self.expression[index + 1:closing]
            lookahead = closing + 1
            while lookahead < self.length and self.expression[lookahead].isspace():
                lookahead += 1
            if (
                lookahead < self.length
                and self.expression[lookahead] == "("
                and name.lower() in _MUL_FUNCTIONS
            ):
                return self._call_arguments(lookahead)
            return None, lookahead
        if not char.isalpha():
            return None, index
        start = index
        while index < self.length and self.expression[index].isalpha():
            index += 1
        name = self.expression[start:index]
        lookahead = index
        while lookahead < self.length and self.expression[lookahead].isspace():
            lookahead += 1
        has_call = lookahead < self.length and self.expression[lookahead] == "("
        if len(name) == 1:
            if not has_call:
                return [name], index
            # 单字母后接括号：矩阵与括号因子组的隐式乘法，如 A(BC)。
            inner, index = self._factor(lookahead)
            if inner is None:
                return None, index
            return [name] + inner, index
        if has_call:
            if name.lower() in _MUL_FUNCTIONS:
                return self._call_arguments(lookahead)
            # size(y)、same(...) 之类不是纯乘积的调用，拒绝解析。
            return None, index
        # 无调用的连续字母：LaTeX 数学里的并列单字母变量，按逐字母因子切分。
        if 2 <= len(name) <= 4:
            return list(name), index
        return None, index

    def _call_arguments(self, index: int) -> Tuple[Optional[List[str]], int]:
        """解析乘法辅助函数的实参并展平成因子序列。"""
        args, index = self._arguments(index)
        if args is None:
            return None, index
        flattened: List[str] = []
        for argument in args:
            if not argument.strip():
                continue
            parsed = _ProductParser(argument).parse()
            if parsed is None:
                return None, index
            flattened.extend(parsed)
        if not flattened:
            return None, index
        return flattened, index

    def _arguments(self, index: int) -> Tuple[Optional[List[str]], int]:
        """解析函数实参列表，返回实参文本列表与新游标。"""
        if index >= self.length or self.expression[index] != "(":
            return None, index
        depth = 0
        start = index + 1
        arguments: List[str] = []
        while index < self.length:
            char = self.expression[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    arguments.append(self.expression[start:index])
                    return arguments, index + 1
            elif char == "," and depth == 1:
                arguments.append(self.expression[start:index])
                start = index + 1
            index += 1
        return None, index


def _parse_product_expression(expression: str) -> Optional[Tuple[str, ...]]:
    text = expression.strip().strip(",;")
    if not text:
        return None
    if re.search(r"[0-9^_{}\\]", text):
        # 含数字、上下标或残留命令的式子不是纯矩阵乘积。
        return None
    return _ProductParser(text).parse()


def _split_indices(raw: str) -> Optional[Tuple[str, ...]]:
    letters = re.findall(r"[A-Za-z]", str(raw or ""))
    if not letters or len(letters) > len(str(raw or "")):
        return None
    return tuple(letters)


def _parse_elementwise_line(line: str) -> Optional[dict]:
    """解析 ``d_{ij} = Σ_k Σ_l a_{ik} b_{kl} c_{lj}`` 形式的逐元素展开。"""
    segments = _split_top_level(line)
    if len(segments) != 2:
        return None
    left_text, right_text = segments
    if not left_text or not right_text:
        return None

    index_match = _INDEX_LHS.search(left_text)
    if index_match is None:
        return None
    free_indices = _split_indices(index_match.group(1))
    base_text = left_text[:index_match.start()].strip()
    if not free_indices or not base_text:
        return None
    # 左端只允许“字母/函数名(实参)”这类基名，含运算符即不是逐元素定义。
    if re.search(r"[+\-*/^=]", base_text):
        return None

    body = _SUM_TOKEN.sub(" ", right_text)
    body = re.sub(r"[()\[\]]", " ", body)
    body = body.replace("*", " ").replace(",", " ")
    body = re.sub(r"\s+", "", body)
    if not body:
        return None

    terms: List[Tuple[str, Tuple[str, ...]]] = []
    position = 0
    while position < len(body):
        match = _TERM.match(body, position)
        if match is None:
            return None
        indices = _split_indices(match.group(2))
        if indices is None:
            return None
        terms.append((match.group(1), indices))
        position = match.end()
    if not terms:
        return None
    return {
        "base": base_text,
        "free_indices": free_indices,
        "terms": terms,
        "raw": line.strip(),
    }


def _chain_signature(claim: dict) -> Optional[tuple]:
    """把逐元素展开规范化为“自由下标 + 因子/下标元组”的结构签名。"""
    terms = list(claim.get("terms") or [])
    free_indices = tuple(claim.get("free_indices") or ())
    if not terms or not free_indices:
        return None
    canonical: Dict[str, str] = {}

    def rename(index: str) -> str:
        if index not in canonical:
            canonical[index] = f"I{len(canonical)}"
        return canonical[index]

    mapped_terms = []
    for base, indices in terms:
        if not indices:
            return None
        mapped_terms.append(
            (str(base).casefold(), tuple(rename(item) for item in indices)))
        if len(canonical) > 8:
            return None
    mapped_free = []
    for index in free_indices:
        if index not in canonical:
            # 自由下标必须出现在某个因子中，否则不是合法的逐元素展开。
            return None
        mapped_free.append(canonical[index])
    return tuple(mapped_free), tuple(mapped_terms)


def extract_matrix_chain_evidence(text: str) -> dict:
    """提取文本里的乘积序列证据、逐元素展开证据与等式两端的连乘关系。"""
    normalized = normalize_text(text)
    products: List[Tuple[str, ...]] = []
    elementwise: List[dict] = []
    relations: List[Tuple[Tuple[str, ...], Tuple[str, ...]]] = []

    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        sequences: List[Optional[Tuple[str, ...]]] = []
        for segment in _split_top_level(line):
            sequence = _parse_product_expression(segment)
            sequences.append(sequence)
            if sequence and len(sequence) >= 2 and sequence not in products:
                products.append(sequence)
        # 同一行内相邻等号两端都是连乘时，记录“结合律/交换律断言”。
        for index in range(len(sequences) - 1):
            first, second = sequences[index], sequences[index + 1]
            if (
                first and second
                and len(first) >= 3
                and len(second) >= 3
                and (first, second) not in relations
            ):
                relations.append((first, second))
        claim = _parse_elementwise_line(line)
        if claim is not None:
            elementwise.append(claim)

    return {
        "products": products,
        "elementwise": elementwise,
        "relations": relations,
        "signatures": [
            signature
            for signature in (_chain_signature(claim) for claim in elementwise)
            if signature is not None
        ],
    }


def _same_multiset(first: Sequence[str], second: Sequence[str]) -> bool:
    return len(first) == len(second) and sorted(first) == sorted(second)


def _verdict(ok: bool, status: str, lhs_text: str, rhs_text: str,
             detail: str, *, ok_label: str, rule: str,
             assumptions: Optional[List[str]] = None) -> object:
    # 延迟导入，避免与 verify.py 循环依赖。
    try:
        from .verify import CheckResult
    except ImportError:  # pragma: no cover
        from verify import CheckResult  # type: ignore
    return CheckResult(
        ok,
        status,
        str(lhs_text),
        str(rhs_text),
        detail,
        meta={
            "verdict_label": ok_label,
            "cross_representation_rule": rule,
            "assumptions": list(assumptions or []),
        },
    )


def try_matrix_chain_equivalence(lhs_text: str, rhs_text: str):
    """用矩阵连乘规则判定两种表示；证据不足时返回 None。

    返回 ``CheckResult``（equivalent / not_equivalent），或 None 表示本规则
    不适用，交由既有校验链继续处理。
    """
    try:
        left = extract_matrix_chain_evidence(lhs_text)
        right = extract_matrix_chain_evidence(rhs_text)
    except Exception:
        return None
    if not left or not right:
        return None

    # 先看图片侧自身给出的等式：``(AB)C = A(CB)`` 这类交换律断言一般不成立。
    for first, second in left.get("relations") or []:
        if first != second and _same_multiset(first, second):
            return _verdict(
                False, "not_equivalent", lhs_text, rhs_text,
                "公式自身的两端因子顺序不同，矩阵乘法一般不满足交换律",
                ok_label="CONFIRMED_ERROR",
                rule="matrix_chain_associativity",
            )

    left_signatures = {
        signature for signature in left["signatures"] if len(signature[1]) >= 3}
    right_signatures = {
        signature for signature in right["signatures"] if len(signature[1]) >= 3}
    if left_signatures and right_signatures:
        if left_signatures & right_signatures:
            return _verdict(
                True, "equivalent", lhs_text, rhs_text,
                "矩阵连乘逐元素展开的自由下标与因子收缩顺序完全一致",
                ok_label="HIGH_CONFIDENCE",
                rule="matrix_chain_elementwise",
                assumptions=["逐元素展开按自由下标 + 收缩下标的结构签名比较"],
            )
        left_multisets = sorted(
            tuple(sorted(base for base, _ in signature[1]))
            for signature in left_signatures)
        right_multisets = sorted(
            tuple(sorted(base for base, _ in signature[1]))
            for signature in right_signatures)
        if left_multisets == right_multisets:
            return _verdict(
                False, "not_equivalent", lhs_text, rhs_text,
                "矩阵连乘的因子顺序或收缩下标不一致，索引收缩不等价",
                ok_label="CONFIRMED_ERROR",
                rule="matrix_chain_elementwise",
            )

    left_products = [item for item in left["products"] if len(item) >= 3]
    right_products = [item for item in right["products"] if len(item) >= 3]
    if left_products and right_products:
        if any(item in right_products for item in left_products):
            return _verdict(
                True, "equivalent", lhs_text, rhs_text,
                "两侧给出同一有序因子序列的矩阵连乘，按矩阵乘法结合律等价",
                ok_label="HIGH_CONFIDENCE",
                rule="matrix_chain_associativity",
                assumptions=["矩阵乘法满足结合律；(AB)C = A(BC) = ABC"],
            )
        # 只有在两侧各只有一条连乘证据、且因子集合相同而顺序不同时，
        # 才敢确认不等价；多候选场景继续交给后续校验链。
        if len(left_products) == 1 and len(right_products) == 1:
            left_sequence = left_products[0]
            right_sequence = right_products[0]
            if _same_multiset(left_sequence, right_sequence):
                return _verdict(
                    False, "not_equivalent", lhs_text, rhs_text,
                    "两侧矩阵连乘的因子顺序不同，一般不满足交换律",
                    ok_label="CONFIRMED_ERROR",
                    rule="matrix_chain_associativity",
                )
    return None


__all__ = [
    "extract_matrix_chain_evidence",
    "normalize_text",
    "try_matrix_chain_equivalence",
]
