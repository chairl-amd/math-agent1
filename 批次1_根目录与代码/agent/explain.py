# -*- coding: utf-8 -*-
"""agent/explain.py — 可解释批改层（w11 工单1）。

设计边界（硬约束）：
1. 只在校验结论产生**之后**运行，只读 CheckResult，绝不改写 status / ok；
2. 仅对 ``not_equivalent`` 或 ``verdict_label == "CONFIRMED_ERROR"`` 生成说明；
3. 复用 ``llm.py`` 的客户端与 ``verifier_hetero.py`` 已验证的 JSON 强输出、
   正文/推理双通道提取逻辑，不另起一套 VLM 调用栈；
4. ``EXPLAIN_ENABLED=0`` 时本模块不会被调用，报告结构与改动前完全一致。

输出字段（结构化 JSON）：
    error_type / location / suggestion / confidence / needs_human_review
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any, Callable, Optional

try:  # 包模式
    from . import cache as result_cache
    from . import config
    from .llm import TextLLM, compact_cpp_context
    from .pde_analysis import is_pde
    from .verifier_hetero import (
        _extract_json_object,
        _read_reasoning_content,
        _read_response_content,
    )
except ImportError:  # pragma: no cover - 脚本模式
    import cache as result_cache  # type: ignore
    import config  # type: ignore
    from llm import TextLLM, compact_cpp_context  # type: ignore
    from pde_analysis import is_pde  # type: ignore
    from verifier_hetero import (  # type: ignore
        _extract_json_object,
        _read_reasoning_content,
        _read_response_content,
    )

logger = logging.getLogger(__name__)

# 提示词版本参与缓存键：提示词调整后旧解释自然失效。
EXPLAIN_PROMPT_VERSION = "w11-explain-20261116i"
MAX_PROMPT_CODE_CHARS = 7000
MAX_PROMPT_EVIDENCE_CHARS = 2400

SYSTEM_EXPLAINER = (
    "你是严格的数学与算法代码审校员。你只根据给定证据定位错误，"
    "不臆造证据中不存在的信息；证据不足时如实降低置信度。"
)

# ---------------------------------------------------------------------------
# 错误分类表：与 tests/benchmark/annotations.json 的根因词表对齐，
# 并映射到 output/failure_analysis.md 的 7 类根因层。
# layer 仅用于质量报告归因，不参与判定。
# ---------------------------------------------------------------------------
ERROR_TYPES: dict[str, dict[str, str]] = {
    "index_scope": {
        "label": "索引范围错误",
        "layer": "索引/求和边界",
    },
    "off_by_one": {
        "label": "索引边界差一",
        "layer": "索引/求和边界",
    },
    "transpose_error": {
        "label": "转置/行列写反",
        "layer": "索引/求和边界",
    },
    "sign_error": {
        "label": "符号相反",
        "layer": "运算/符号",
    },
    "variable_mapping": {
        "label": "变量映射错误",
        "layer": "翻译/变量映射",
    },
    "coefficient_error": {
        "label": "系数/常数因子错误",
        "layer": "运算/符号",
    },
    "missing_operation": {
        "label": "遗漏运算",
        "layer": "运算/符号",
    },
    "missing_term": {
        "label": "遗漏项/分量",
        "layer": "运算/符号",
    },
    "stride_error": {
        "label": "步长/采样错误",
        "layer": "索引/求和边界",
    },
    "norm_error": {
        "label": "范数/开方定义错误",
        "layer": "运算/符号",
    },
    "order_error": {
        "label": "运算顺序错误",
        "layer": "运算/符号",
    },
    "operator_error": {
        "label": "算子/函数用错",
        "layer": "运算/符号",
    },
    "notation_variant": {
        "label": "记号变体（表述差异）",
        "layer": "跨表示/记号",
    },
    "pde_capability": {
        "label": "PDE 能力边界",
        "layer": "能力边界（PDE）",
    },
    "symbolic_scope_limit": {
        "label": "符号证明能力边界",
        "layer": "能力边界（符号）",
    },
    "parser_limitation": {
        "label": "解析器局限",
        "layer": "能力边界（解析）",
    },
    "unknown": {
        "label": "证据不足/无法归类",
        "layer": "未归类",
    },
}

# 别名归一化：模型可能输出近义词、中文标签或带后缀的 id。
_ERROR_TYPE_ALIASES: dict[str, str] = {
    "index": "index_scope",
    "index_error": "index_scope",
    "index_boundary": "index_scope",
    "index_bounds": "index_scope",
    "index_range": "index_scope",
    "loop_bounds": "index_scope",
    "loop_bound": "index_scope",
    "scope_error": "index_scope",
    "索引范围": "index_scope",
    "索引范围错误": "index_scope",
    "off_by_one": "off_by_one",
    "offbyone": "off_by_one",
    "off_by_one_error": "off_by_one",
    "boundary_off_by_one": "off_by_one",
    "fencepost": "off_by_one",
    "差一": "off_by_one",
    "边界差一": "off_by_one",
    "sum_bounds": "index_scope",
    "sum_bound": "index_scope",
    "summation_bounds": "index_scope",
    "sum_limit": "index_scope",
    "upper_lower_bound": "index_scope",
    "求和上下界": "index_scope",
    "transpose": "transpose_error",
    "transpose_error": "transpose_error",
    "transposed_index": "transpose_error",
    "matrix_transpose": "transpose_error",
    "index_transpose": "transpose_error",
    "转置": "transpose_error",
    "transpose_error_": "transpose_error",
    "sign": "sign_error",
    "sign_error": "sign_error",
    "sign_flip": "sign_error",
    "wrong_sign": "sign_error",
    "符号": "sign_error",
    "符号错误": "sign_error",
    "符号相反": "sign_error",
    "variable": "variable_mapping",
    "variable_mapping": "variable_mapping",
    "symbol_mapping": "variable_mapping",
    "variable_scope": "variable_mapping",
    "变量映射": "variable_mapping",
    "coefficient": "coefficient_error",
    "coefficient_error": "coefficient_error",
    "missing_coefficient": "coefficient_error",
    "constant_factor": "coefficient_error",
    "系数": "coefficient_error",
    "missing_operation": "missing_operation",
    "missing_op": "missing_operation",
    "missing_step": "missing_operation",
    "遗漏运算": "missing_operation",
    "missing_term": "missing_term",
    "missing_component": "missing_term",
    "missing_element": "missing_term",
    "遗漏项": "missing_term",
    "stride": "stride_error",
    "stride_error": "stride_error",
    "step_error": "stride_error",
    "步长": "stride_error",
    "norm": "norm_error",
    "norm_error": "norm_error",
    "sqrt_error": "norm_error",
    "范数": "norm_error",
    "order": "order_error",
    "order_error": "order_error",
    "operation_order": "order_error",
    "associativity": "order_error",
    "顺序": "order_error",
    "operator": "operator_error",
    "operator_error": "operator_error",
    "function_error": "operator_error",
    "算子": "operator_error",
    "notation": "notation_variant",
    "notation_variant": "notation_variant",
    "representation": "notation_variant",
    "记号": "notation_variant",
    "pde": "pde_capability",
    "pde_capability": "pde_capability",
    "pde_boundary": "pde_capability",
    "capability_boundary": "pde_capability",
    "symbolic_scope_limit": "symbolic_scope_limit",
    "symbolic_limit": "symbolic_scope_limit",
    "symbolic_scope": "symbolic_scope_limit",
    "proof_limit": "symbolic_scope_limit",
    "parser": "parser_limitation",
    "parser_limitation": "parser_limitation",
    "parse_error": "parser_limitation",
    "parsing_limit": "parser_limitation",
    "unknown": "unknown",
}

_EXPLAINABLE_STATUSES = {"not_equivalent"}
_EXPLAINABLE_LABELS = {"CONFIRMED_ERROR"}

# 导出/展示用的默认建议（仅在模型建议缺失时兜底，不替代模型结论）。
_DEFAULT_SUGGESTIONS = {
    "sign_error": "把与公式相反的符号改回一致（检查 +/- 或减号方向）。",
    "off_by_one": "核对循环边界：`< n` 与 `<= n-1` 的项数必须与公式一致。",
    "index_scope": "核对循环/求和的索引依赖范围，确保遍历区间与公式定义一致。",
    "sum_bounds": "把求和上下界改成公式给出的起止范围。",
    "transpose_error": "交换行列下标（如 `a[i][j]` 与 `a[j][i]`）使矩阵位置与公式一致。",
    "missing_operation": "补上公式中存在但代码遗漏的运算（开方、除法、激活函数等）。",
    "coefficient_error": "补上缺失的系数/常数因子（如 1/2、1/n、步长）。",
    "missing_term": "补上遗漏的项或分量。",
    "stride_error": "核对循环步长与公式的采样间隔。",
    "norm_error": "核对范数定义：先平方求和再开方，不要漏掉开方或平方。",
    "order_error": "按公式的运算/乘法顺序重排代码。",
    "operator_error": "把用错的算子/函数替换为公式要求的那个。",
    "variable_mapping": "核对代码变量与公式符号的一一对应关系。",
    "notation_variant": "两侧可能只是记号差异，建议人工确认是否本当判等价。",
    "pde_capability": "连续 PDE 与离散实现之间不存在可自动证明的代数等价，建议人工核对离散格式。",
    "symbolic_scope_limit": "符号层无法完成证明，建议补充中间推导或人工核对。",
    "parser_limitation": "公式/代码存在解析器不支持的写法，建议改写为可解析形式后复核。",
    "unknown": "证据不足以定位唯一错误，请人工核对。",
}


# ---------------------------------------------------------------------------
# 开关与工具
# ---------------------------------------------------------------------------
def is_enabled() -> bool:
    """解释层总开关：关闭时调用方应完全跳过本模块。"""
    return bool(getattr(config, "EXPLAIN_ENABLED", False))


def _clamp(value: Any, low: float = 0.0, high: float = 1.0,
           default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _as_text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            value = str(value)
    text = value.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _canonical_error_type(raw: Any) -> str:
    """把模型返回的类型名归一化到闭集；无法识别则返回 unknown。"""
    text = str(raw or "").strip().lower()
    text = text.replace("-", "_").replace(" ", "_").strip("._")
    if text in ERROR_TYPES:
        return text
    if text in _ERROR_TYPE_ALIASES:
        return _ERROR_TYPE_ALIASES[text]
    # 去掉常见后缀再试一次，例如 index_scope_error。
    for suffix in ("_error", "_issue", "_type"):
        if text.endswith(suffix):
            candidate = text[: -len(suffix)]
            if candidate in ERROR_TYPES:
                return candidate
            if candidate in _ERROR_TYPE_ALIASES:
                return _ERROR_TYPE_ALIASES[candidate]
    for key, target in _ERROR_TYPE_ALIASES.items():
        if key and key in text:
            return target
    return "unknown"


def verdict_label(check: dict) -> str:
    meta = check.get("meta") if isinstance(check.get("meta"), dict) else {}
    label = str(meta.get("verdict_label") or "").strip()
    if label:
        return label
    if check.get("status") == "not_equivalent":
        return "CONFIRMED_ERROR"
    return "UNKNOWN"


def needs_explanation(check: Any, *, force: bool = False) -> bool:
    """只有确认错误（或显式 force）才进入解释层。"""
    if not isinstance(check, dict):
        return False
    status = str(check.get("status") or "")
    # 等价结论永远不需要批改说明；即使 meta 里残留了错误标签也不解释。
    if status in ("equivalent", "numeric_equivalent") or check.get("ok") is True:
        return False
    if status in _EXPLAINABLE_STATUSES:
        return True
    if verdict_label(check) in _EXPLAINABLE_LABELS:
        return True
    return bool(force)


# ---------------------------------------------------------------------------
# 证据收集
# ---------------------------------------------------------------------------
def _numbered_code(code: str, *, max_lines: int = 180) -> str:
    """给代码加行号，保证 location 可以引用具体行。"""
    if not isinstance(code, str) or not code.strip():
        return ""
    lines = code.splitlines()
    truncated = len(lines) > max_lines
    shown = lines[:max_lines]
    numbered = "\n".join(
        f"L{index}: {line}" for index, line in enumerate(shown, start=1))
    if truncated:
        numbered += f"\n…（其余 {len(lines) - max_lines} 行已截断）"
    return numbered


def _extract_vlm_evidence(check: dict) -> list[str]:
    """从异构校验结果中提取判定理由、差异与索引检查。"""
    meta = check.get("meta") if isinstance(check.get("meta"), dict) else {}
    hetero = meta.get("hetero_verdicts")
    if not isinstance(hetero, list):
        # 兼容单校验器（未聚合）时把 evidence 自身当作一条。
        evidence = meta.get("evidence")
        hetero = [check] if evidence else []
    items: list[str] = []
    for entry in hetero:
        if not isinstance(entry, dict):
            continue
        entry_meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
        verifier = str(entry_meta.get("verifier") or entry_meta.get("provider")
                       or "hetero")
        reason = str(entry.get("detail") or "").strip()
        diff = ""
        index_check = str(entry_meta.get("index_check") or "").strip()
        raw_evidence = entry_meta.get("evidence")
        if isinstance(raw_evidence, str) and raw_evidence.strip():
            try:
                payload = _extract_json_object(raw_evidence)
            except (TypeError, ValueError):
                payload = {}
            if isinstance(payload, dict):
                diff = str(payload.get("diff") or "").strip()
                if not index_check:
                    index_check = str(payload.get("index_check") or "").strip()
        line = f"[{verifier}] 结论={entry.get('status') or '?'}"
        if reason:
            line += f"；理由：{_as_text(reason, 260)}"
        if diff:
            line += f"；差异：{_as_text(diff, 220)}"
        if index_check:
            line += f"；索引检查：{_as_text(index_check, 180)}"
        items.append(line)
    return items[:4]


def _extract_sympy_evidence(check: dict) -> str:
    parts = [
        f"SymPy 状态：{check.get('status') or '?'}",
        f"左侧表达式：{_as_text(check.get('lhs'), 300)}",
        f"右侧表达式：{_as_text(check.get('rhs'), 300)}",
    ]
    detail = str(check.get("detail") or "").strip()
    if detail:
        parts.append(f"说明：{_as_text(detail, 400)}")
    points = check.get("diff_points")
    if isinstance(points, list) and points:
        rendered = []
        for point in points[:3]:
            if not isinstance(point, dict):
                continue
            rendered.append(
                f"采样 {point.get('point')}：左={point.get('lhs')} "
                f"右={point.get('rhs')} 相对误差={point.get('rel_delta')}")
        if rendered:
            parts.append("差异采样：" + "；".join(rendered))
    meta = check.get("meta") if isinstance(check.get("meta"), dict) else {}
    index_check = str(meta.get("index_check") or "").strip()
    if index_check:
        parts.append(f"索引检查：{_as_text(index_check, 200)}")
    return "\n".join(parts)


def _deterministic_observations(check: dict, latex_expr: str,
                                cpp_code: str) -> list[str]:
    """给出可复核的确定性观察，作为模型分类的辅助证据（不下结论）。"""
    observations: list[str] = []
    points = check.get("diff_points")
    if isinstance(points, list) and points:
        deltas = []
        for point in points[:4]:
            if isinstance(point, dict) and point.get("rel_delta") is not None:
                try:
                    deltas.append(float(point["rel_delta"]))
                except (TypeError, ValueError):
                    pass
        if deltas and all(abs(delta - 2.0) < 1e-6 for delta in deltas):
            observations.append(
                "数值证据：每个采样点两侧互为相反数（相对误差恒为 2.0），"
                "即符号相反。")

    code = cpp_code or ""
    if code:
        for index, line in enumerate(code.splitlines(), start=1):
            stripped = line.strip()
            if re.search(r"<\s*[A-Za-z_][\w\.\[\]]*\s*-\s*1\b", stripped):
                observations.append(
                    f"L{index} 的循环上界使用 `n-1` 形式：`{_as_text(stripped, 90)}`")
                break
        for index, line in enumerate(code.splitlines(), start=1):
            stripped = line.strip()
            if re.search(r"\bfor\s*\(", stripped) and (
                    re.search(r"\w+\s*\+=\s*[2-9]", stripped)
                    or re.search(r"\w+\s*=\s*\w+\s*\+\s*[A-Za-z_]", stripped)):
                observations.append(
                    f"L{index} 的循环使用非 1 步长：`{_as_text(stripped, 90)}`")
                break
        if re.search(r"\[(\w+)\]\s*\[(\w+)\]", code) and re.search(
                r"_\s*\{\s*[ij]\s*,\s*[ij]\s*\}|_\s*\{?\s*(ij|ji)\s*\}?", latex_expr or ""):
            pair = re.search(r"\[(\w+)\]\s*\[(\w+)\]", code)
            if pair and pair.group(1) in {"j", "col"} and pair.group(2) in {"i", "row"}:
                observations.append(
                    f"代码中出现转置式下标：`{_as_text(pair.group(0), 60)}`")

    latex = latex_expr or ""
    if "\\sqrt" in latex and code and not re.search(
            r"\bsqrt\b|\bpow\s*\([^)]*0?\.5", code):
        observations.append("公式含开方（\\sqrt），但代码中未出现 sqrt/pow(...,0.5)。")
    if "\\frac" in latex and code and "/" not in code:
        observations.append("公式含分式（\\frac），但代码中未出现除法 `/`。")
    if ("\\|" in latex or "\\left\\|" in latex) and code and not re.search(
            r"\bsqrt\b", code):
        observations.append("公式含范数，但代码中未出现开方运算。")
    return observations[:5]


def _has_strong_pde_ops(*texts: Any) -> bool:
    """区分真正的连续微分方程与优化里的 ∇L。

    只有出现时间/空间偏导或 ∇² 才认为"连续方程 vs 离散实现"的能力边界成立；
    单纯的 ∇L（梯度下降）或离散差分残差不在此列。
    """
    markers = ("partial", "\\partial", "∂", "nabla**2", "nabla^2",
               "\\nabla^2", "\\nabla^{2}", "nabla\\cdot", "\\nabla\\cdot")
    for text in texts:
        blob = str(text or "")
        if any(marker in blob for marker in markers):
            return True
    return False


def _build_evidence(check: dict, latex_expr: str, ref_latex: str,
                    cpp_code: str) -> dict[str, Any]:
    """汇总解释所需的全部证据，返回提示词与缓存都可复用的结构。"""
    code_source = cpp_code or ""
    if not code_source and ref_latex and any(
            marker in ref_latex for marker in (";", "for (", "std::", "#include")):
        code_source = ref_latex
    observations = _deterministic_observations(check, latex_expr, code_source)
    pde_flag = False
    try:
        pde_flag = bool(is_pde(latex_expr or "")) or bool(
            is_pde(ref_latex or ""))
    except Exception:  # pragma: no cover - 探测器故障不影响解释
        pde_flag = False
    return {
        "latex": _as_text(latex_expr, 1600),
        "ref_latex": _as_text(ref_latex, 1600),
        "code": _numbered_code(
            compact_cpp_context(code_source, max_lines=180, max_chars=MAX_PROMPT_CODE_CHARS)
            if code_source else ""),
        "sympy": _extract_sympy_evidence(check),
        "vlm": _extract_vlm_evidence(check),
        "observations": observations,
        "pde": pde_flag,
        "pde_strong": bool(pde_flag and _has_strong_pde_ops(
            latex_expr, ref_latex, check.get("lhs"), check.get("rhs"))),
        "label": verdict_label(check),
        "status": str(check.get("status") or ""),
    }


def _taxonomy_text() -> str:
    lines = []
    for name, info in ERROR_TYPES.items():
        lines.append(f"- {name}: {info['label']}（根因层：{info['layer']}）")
    return "\n".join(lines)


def _build_prompt(evidence: dict[str, Any]) -> str:
    vlm_text = "\n".join(evidence["vlm"]) or "（无异构校验器证据）"
    observations = "\n".join(f"- {item}" for item in evidence["observations"]) \
        or "- （无确定性观察）"
    return f"""下面是一个「参考公式 vs C++ 实现」自动校验任务中判为**不等价**的完整证据。
请只依据这些证据，指出最可能的一处错误，并给出可直接采用的修改建议。

<reference_latex>
{evidence['latex'] or '（空）'}
</reference_latex>

<translated_from_code>
{evidence['ref_latex'] or '（空）'}
</translated_from_code>

<code_with_line_numbers>
{evidence['code'] or '（无代码，仅有表达式）'}
</code_with_line_numbers>

<sympy_evidence>
{evidence['sympy']}
</sympy_evidence>

<vlm_evidence>
{vlm_text}
</vlm_evidence>

<deterministic_observations>
{observations}
</deterministic_observations>

<error_type_taxonomy>
{_taxonomy_text()}
</error_type_taxonomy>

<rules>
1. error_type 必须从上面分类表里选一个 id（不要自造）；
2. location 必须定位到具体代码行（写 L12 这种行号）或具体公式片段；代码只有几行时也要写行号；
3. suggestion 必须是可直接采用的修正：优先给改后的 C++ 片段（用 ```cpp 围栏）或正确公式写法；
4. 变量改名、`a_{{i,i}}` 与 `a_{{ii}}`、`\\mathbf{{x}}` 与 x 等只是记号差异，不得因此判 error_type 为错误；
5. 证据不足时不要猜：error_type 用 unknown，confidence 给低分，needs_human_review=true；
6. pde_capability 只用于「连续方程/连续场」与「离散实现/迭代格式」整体不可比的情况
   （证据或 VLM 明确说明这一点），此时 needs_human_review=true；
   注意：∇L 出现在梯度下降/优化更新中不算 PDE；若两侧都是离散残差、且能指出
   具体漏项或符号，按具体类型分类，不要用 pde_capability 覆盖；
7. confidence 是 0~1 的小数，表示"这条定位/建议可信"的程度；
8. 只输出一个 JSON 对象，不要输出任何解释性文字。
</rules>

<classify_order>
先按下面顺序判断，命中即停：
A. 能否指出一个**具体的、可用局部修改修正**的数学错误？
   - 整体矩阵/乘积被转置（如 (AB)^T、A_{{kj}}B_{{ki}}、C=B^T A^T）→ transpose_error；
     转置优先于 index_scope；但**单个元素的下标顺序写反**（如 U_{{jk}} 与 U_{{kj}}、
     a_{{ik}}b_{{kj}} 与 a_{{ik}}b_{{jk}}）按 index_scope 记，不升级为 transpose_error；
   - 其余循环/求和的上下界不符、收缩维缺失、下标变量 i/j/k 用错、
     卷积/滑窗索引偏移、元素位置错 → index_scope（本项目把求和上下界统一记入此类）；
   - 恰好是边界常量差一（n 与 n-1、`<` 与 `<=`）且其余下标表达式一致 → off_by_one；
     注意：off_by_one 只用于**一维序列/数组的求和边界**（如 Σ_{{i=0}}^{{n-1}} 与
     Σ_{{i=0}}^{{n-2}}、起点 Σ_{{i=0}} 与 Σ_{{i=1}}）；判断时只比较求和变量与
     起止边界，求和体内部再出现别的下标（如 a_j 与 a_i）不影响该判断；
     起点/终点本身差一即归 off_by_one；一旦涉及矩阵、卷积或多维下标
     （如 Σ_k a_{{ik}}b_{{kj}}、Σ_k h_k x_{{i-k}}、U_{{ij}} 的 k 范围），
     一律归 index_scope；
   - 正负号相反 → sign_error；
   - 代码变量与公式符号身份错位（把 b 当 a、把 U 当 L）→ variable_mapping；
     注意：用错下标变量仍属 index_scope，不是 variable_mapping；
   - 公式里显式写出的单个运算在代码中缺失（\\sqrt、除法、绝对值）→ missing_operation；
     而"目标就是算范数/单位化，但范数本身定义错"（L1/L2 混用、漏平方）→ norm_error；
     范数本身正确、只是与别的量用错算子（如 \\|a\\|+\\|b\\| 应为 \\|a\\|\\|b\\|）→ operator_error；
   - 其余系数/漏项/步长/顺序/算子错误 → 对应分类。
B. 指不出具体数学错误，失败来自解析器不支持的写法（\\displaystyle、环境词等）
   → parser_limitation。注意：只有当两侧数学写法都正确、纯粹是解析器读不了时才用它；
   rhs 里的 `\\displaystyle` 等噪声应先剥离再比较，不能因为有噪声就判 parser_limitation；
   一旦能指出具体下标/边界/符号错误，必须用 A 中的具体类型。
C. 指不出具体错误，且证据明确是「连续方程/连续场」与「离散实现/迭代格式」
   整体不可比（如 ∇²φ=ρ/ε₀ 对离散 Jacobi 更新、∂u/∂t=α∂²u/∂x² 对代数方程）
   → pde_capability。
D. 指不出具体错误，且是符号层无法完成整体证明（如 A=LU 的任意 n 归纳）
   → symbolic_scope_limit。
</classify_order>

<output_format>
{{"error_type":"索引范围错误等分类 id","location":"L12: for (int k = 0; k < j; ++k)","suggestion":"改后的代码或公式","confidence":0.82,"needs_human_review":false}}
</output_format>

现在直接输出 JSON："""


def _build_repair_prompt(original_prompt: str, attempt: int) -> str:
    """紧凑修复提示：只保留原始提示的节选 + 强制格式，避免再次超长推理。"""
    final_hint = "这是最后一次修复机会。" if attempt >= 2 else ""
    excerpt = str(original_prompt or "")[:2600]
    return f"""上一次回复不是可解析的 JSON。{final_hint}
请只输出一个 JSON 对象，禁止 Markdown 围栏、注释或任何 JSON 之外的文字。
如果你使用推理通道，请把推理压缩到 200 字以内，把预算留给最终 JSON。

<context_excerpt>
{excerpt}
</context_excerpt>

error_type 只能取：{", ".join(ERROR_TYPES)}。
输出格式：{{"error_type":"...","location":"...","suggestion":"...","confidence":0.0,"needs_human_review":true}}"""


# ---------------------------------------------------------------------------
# 模型调用（复用 llm.py 客户端 + verifier_hetero.py 的提取函数）
# ---------------------------------------------------------------------------
def _payload_from_response(response: Any) -> Optional[dict]:
    """正文优先、推理通道兜底的双通道 JSON 提取。"""
    for reader in (_read_response_content, _read_reasoning_content):
        try:
            text = reader(response)
        except (ValueError, TypeError, AttributeError):
            continue
        if not text or not str(text).strip():
            continue
        try:
            payload = _extract_json_object(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload:
            return payload
    return None


def _usage_of(response: Any) -> dict:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        key: getattr(usage, key)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if getattr(usage, key) is not None
    }


def _finish_reason(response: Any) -> str:
    try:
        return str(response.choices[0].finish_reason or "")
    except (AttributeError, IndexError, TypeError):
        return ""


def _default_model_call(prompt: str) -> tuple[Optional[dict], dict]:
    """默认调用：temperature=0 + response_format=json_object + 最多 2 次修复。"""
    meta: dict[str, Any] = {
        "model": getattr(config, "TEXT_MODEL", ""),
        "attempts": 0,
        "json_mode": True,
        "usage": {},
        "elapsed": 0.0,
        "error": "",
    }
    started = time.time()
    llm = TextLLM(timeout=getattr(config, "EXPLAIN_TIMEOUT", 60))
    try:
        client = llm._ensure_client()
    except Exception as exc:
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return None, meta

    messages = [
        {"role": "system", "content": SYSTEM_EXPLAINER},
        {"role": "user", "content": prompt},
    ]
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    base_budget = max(1024, int(getattr(config, "EXPLAIN_MAX_TOKENS", 8192)))
    # 推理模型可能把预算花在推理通道上：每修复一次就把正文预算翻倍。
    budgets = [base_budget, min(base_budget * 2, 32768), 32768]
    finish_reasons: list[str] = []
    meta["finish_reasons"] = finish_reasons
    use_json_mode = True
    for attempt in range(3):
        meta["attempts"] = attempt + 1
        kwargs = dict(
            model=llm.model,
            messages=messages,
            temperature=0.0,
            max_tokens=budgets[attempt],
        )
        if use_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:
            text = str(exc).lower()
            if use_json_mode and (
                    "response_format" in text or "json_object" in text):
                # 服务端不支持 JSON 模式时退回普通调用，其余逻辑不变。
                use_json_mode = False
                meta["json_mode"] = False
                try:
                    response = client.chat.completions.create(**kwargs)
                except Exception as retry_exc:
                    meta["error"] = (
                        f"{type(retry_exc).__name__}: {retry_exc}")[:400]
                    break
            else:
                meta["error"] = f"{type(exc).__name__}: {exc}"[:400]
                break

        usage = _usage_of(response)
        for key, value in usage.items():
            total_usage[key] = total_usage.get(key, 0) + int(value or 0)
        finish_reasons.append(_finish_reason(response))
        payload = _payload_from_response(response)
        if payload is not None:
            meta["usage"] = total_usage
            meta["elapsed"] = round(time.time() - started, 2)
            return payload, meta

        # 未拿到 JSON：用紧凑修复提示重问，最多 2 次。
        if attempt < 2:
            messages = [
                {"role": "system", "content": SYSTEM_EXPLAINER},
                {"role": "user", "content": _build_repair_prompt(
                    prompt, attempt + 1)},
            ]
    meta["usage"] = total_usage
    meta["elapsed"] = round(time.time() - started, 2)
    return None, meta


# ---------------------------------------------------------------------------
# 结果归一化
# ---------------------------------------------------------------------------
def _normalize_explanation(payload: dict, evidence: dict[str, Any],
                           source: str, meta: dict[str, Any]) -> dict:
    error_type = _canonical_error_type(payload.get("error_type"))
    location = _as_text(payload.get("location"), 300)
    suggestion = _as_text(payload.get("suggestion"), 1400)
    confidence = _clamp(payload.get("confidence"), default=0.0)
    needs_review = bool(payload.get("needs_human_review"))

    if not location:
        location = "未能精确定位（证据见校验记录）"
        confidence = min(confidence, 0.5)
        needs_review = True
    if not suggestion:
        suggestion = _DEFAULT_SUGGESTIONS.get(error_type, _DEFAULT_SUGGESTIONS["unknown"])
        confidence = min(confidence, 0.5)
        needs_review = True
    if error_type == "unknown":
        needs_review = True
    if evidence.get("pde"):
        # PDE 属刻意保留的能力边界：解释可以给，但必须标人工复核。
        needs_review = True
    if confidence < 0.6:
        needs_review = True

    info = ERROR_TYPES.get(error_type, ERROR_TYPES["unknown"])
    return {
        "error_type": error_type,
        "error_type_label": info["label"],
        "root_cause_layer": info["layer"],
        "location": location,
        "suggestion": suggestion,
        "confidence": round(confidence, 2),
        "needs_human_review": needs_review,
        "verdict": evidence.get("status") or "",
        "verdict_label": evidence.get("label") or "",
        "pde_boundary": bool(evidence.get("pde")),
        "source": source,
        "model": meta.get("model") or getattr(config, "TEXT_MODEL", ""),
        "elapsed": meta.get("elapsed", 0.0),
        "attempts": meta.get("attempts", 0),
        "usage": meta.get("usage") or {},
        "error": _as_text(meta.get("error"), 300),
    }


def _rule_based_explanation(evidence: dict[str, Any],
                            meta: dict[str, Any]) -> dict:
    """离线/调用失败时的确定性兜底：只给能由证据直接支撑的结论。"""
    error_type = "unknown"
    location = ""
    if evidence.get("pde_strong"):
        error_type = "pde_capability"
        location = "连续 PDE 与离散实现（能力边界）"
    elif any("互为相反数" in item for item in evidence["observations"]):
        error_type = "sign_error"
        location = evidence["observations"][0]
    else:
        for item in evidence["observations"]:
            if "非 1 步长" in item:
                error_type = "stride_error"
                location = item
                break
            if "转置式下标" in item:
                error_type = "transpose_error"
                location = item
                break
            if "n-1` 形式" in item:
                error_type = "off_by_one"
                location = item
                break
            if "开方" in item or "范数" in item:
                error_type = "missing_operation"
                location = item
                break
    payload = {
        "error_type": error_type,
        "location": location,
        "suggestion": _DEFAULT_SUGGESTIONS.get(error_type),
        "confidence": 0.45 if error_type != "unknown" else 0.2,
        "needs_human_review": True,
    }
    result = _normalize_explanation(payload, evidence, "rule", meta)
    result["needs_human_review"] = True
    return result


def _apply_pde_guard(result: dict, evidence: dict[str, Any]) -> dict:
    """强 PDE 证据下，避免模型把「连续 vs 离散」误判成局部可修错误。

    只影响解释层：判定结论仍是 unknown + needs_human_review。
    """
    if not evidence.get("pde_strong"):
        return result
    if result.get("error_type") in ("pde_capability", "unknown"):
        return result
    guarded = dict(result)
    guarded.update({
        "error_type": "pde_capability",
        "error_type_label": ERROR_TYPES["pde_capability"]["label"],
        "root_cause_layer": ERROR_TYPES["pde_capability"]["layer"],
        "location": "连续方程与离散实现之间（能力边界）",
        "suggestion": _DEFAULT_SUGGESTIONS["pde_capability"],
        "confidence": min(float(result.get("confidence") or 0.5), 0.6),
        "needs_human_review": True,
        "override": "pde_strong",
    })
    return guarded


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------
def explain_check(check: dict, *, latex_expr: str = "", ref_latex: str = "",
                  cpp_code: str = "", model_call: Optional[Callable] = None,
                  force: bool = False) -> Optional[dict]:
    """为单个 CheckResult 生成结构化解释；不该解释时返回 None。

    model_call 仅用于离线测试注入；生产环境走 _default_model_call。
    """
    if not isinstance(check, dict) or not needs_explanation(check, force=force):
        return None
    evidence = _build_evidence(check, latex_expr, ref_latex, cpp_code)
    evidence_digest = hashlib.sha256(
        json.dumps(evidence, ensure_ascii=False,
                   sort_keys=True).encode("utf-8")).hexdigest()[:16]
    scope = (
        f"explain|model={getattr(config, 'TEXT_MODEL', '')}"
        f"|prompt={EXPLAIN_PROMPT_VERSION}"
        f"|status={evidence['status']}|label={evidence['label']}"
        f"|pde={int(bool(evidence['pde']))}|ev={evidence_digest}"
    )
    cache_key_text = f"{latex_expr}\x00{ref_latex}"

    if getattr(config, "EXPLAIN_CACHE_ENABLED", True):
        cached = result_cache.get_explain(
            cache_key_text, str(check.get("lhs") or ""), scope)
        if isinstance(cached, dict) and cached.get("error_type"):
            hit = dict(cached)
            hit["source"] = "cache"
            hit["cache"] = "hit"
            return hit

    prompt = _build_prompt(evidence)
    caller = model_call or _default_model_call
    payload: Optional[dict] = None
    call_meta: dict[str, Any] = {}
    try:
        payload, call_meta = caller(prompt)
    except Exception as exc:
        call_meta = {
            "model": getattr(config, "TEXT_MODEL", ""),
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed": 0.0,
        }
        payload = None

    if isinstance(payload, dict) and payload:
        result = _normalize_explanation(payload, evidence, "llm", call_meta)
    else:
        logger.info("解释层模型调用失败，使用规则兜底：%s",
                    str(call_meta.get("error") or "")[:160])
        result = _rule_based_explanation(evidence, call_meta)
    result = _apply_pde_guard(result, evidence)
    result["cache"] = "miss"

    if getattr(config, "EXPLAIN_CACHE_ENABLED", True):
        result_cache.put_explain(
            cache_key_text, str(check.get("lhs") or ""), result, scope)
    return result


def attach_explanations(report: dict, *, latex_expr: str = "",
                        ref_latex: str = "", cpp_code: str = "",
                        model_call: Optional[Callable] = None,
                        force: bool = False) -> list[dict]:
    """给报告中的每条确认错误追加 explain 字段，不改动判定字段。"""
    if not isinstance(report, dict):
        return []
    steps = report.get("steps") if isinstance(report.get("steps"), dict) else {}
    checks = steps.get("checks")
    if not isinstance(checks, list):
        return []
    produced: list[dict] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        if check.get("name") not in (None, "latex_vs_cpp", "latex_vs_expect"):
            continue
        explanation = explain_check(
            check,
            latex_expr=latex_expr,
            ref_latex=ref_latex,
            cpp_code=cpp_code,
            model_call=model_call,
            force=force,
        )
        if explanation is None:
            continue
        check["explain"] = explanation
        produced.append(explanation)
    return produced


def explanation_summary(explanation: dict) -> str:
    """把解释压成一行，供 CSV/历史/日志使用。"""
    if not isinstance(explanation, dict):
        return ""
    label = explanation.get("error_type_label") or explanation.get("error_type")
    location = str(explanation.get("location") or "")[:80]
    suggestion = str(explanation.get("suggestion") or "")[:120]
    confidence = explanation.get("confidence")
    if not any((label, location, suggestion, confidence is not None)):
        return ""
    parts = [str(label or "")]
    if location:
        parts.append(f"位置：{location}")
    if suggestion:
        parts.append(f"建议：{suggestion}")
    if confidence is not None:
        parts.append(f"置信度：{confidence}")
    return "；".join(parts)


if __name__ == "__main__":  # pragma: no cover - 手工自测入口
    logging.basicConfig(level=logging.INFO)
    sample = {
        "status": "not_equivalent",
        "lhs": "eta*g",
        "rhs": "-eta*g",
        "detail": "数值不一致",
        "diff_points": [],
        "meta": {"verdict_label": "CONFIRMED_ERROR"},
    }
    print(json.dumps(explain_check(
        sample, latex_expr=r"w_{t+1}=w_t-\eta g_t",
        cpp_code="w = w + eta * g;"), ensure_ascii=False, indent=2))
