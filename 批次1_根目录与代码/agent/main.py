#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/main.py — 三层流水线编排 + CLI 入口（工单12/13）。

链路：输入(图片/LaTeX/C++代码)
        -> OCR层(视觉优先，Pix2Text 兜底)
        -> 翻译层(LLM: C++ -> 数学公式 / LaTeX 规范化)
        -> 校验层(SymPy 等价性 + 差异定位)
        -> 输出层(JSON + Markdown 报告)
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import sympy as sp

try:
    from . import config
    from . import cache  # 新增：接入 OCR 与校验结果缓存
    from .llm import TextLLM, strip_code_fence
    from .ocr_pix import OcrResult, PixOcr
    from .ocr_vision import VISION_PROMPT, VisionOcr
    from .verifier_hetero import build_verifier_chain  # 修改：接入可插拔校验链
    from .verify import (CheckResult, SymPyVerifier, compare_expressions,
                           format_report, latex_to_expr, parse_python_expr,
                           save_report, verify_latex_vs_code,
                           _formula_tokens, _split_multiline_latex)
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import config  # type: ignore
    import cache  # type: ignore  # 新增：兼容直接脚本运行
    from llm import TextLLM, strip_code_fence  # type: ignore
    from ocr_pix import OcrResult, PixOcr  # type: ignore
    from ocr_vision import VISION_PROMPT, VisionOcr  # type: ignore
    from verifier_hetero import build_verifier_chain  # type: ignore  # 修改：接入可插拔校验链
    from verify import (CheckResult, SymPyVerifier, compare_expressions,  # type: ignore
                          format_report, latex_to_expr, parse_python_expr,
                          save_report, verify_latex_vs_code,
                          _formula_tokens, _split_multiline_latex)

logger = logging.getLogger("math-agent")

# 工单2：校验逻辑版本号。每次调整 LaTeX 归一化规则都要递增，
# 让旧的校验缓存自然失效，避免"改了代码却仍读到旧结论"。
# w5：新增参考公式归一化（中间变量代入、函数签名剥离、右端配对）。
VERIFIER_LOGIC_VERSION = "20261115-w10i"

# 新增：code2formula 方向用于代码图片的专用识别提示词。
CODE_OCR_PROMPT = """你是 C++ 源码 OCR 引擎。请把图片中的代码转写为 C++ 源码。
要求：
1. 只输出 C++ 源码，不要解释，不要 Markdown 围栏；
2. 保留原有标识符、运算符和缩进；
3. 若图片中没有可识别代码，输出空字符串。
"""


def _to_dict(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_dict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_dict(v) for v in obj]
    return obj


_MATRIX_BLOCK_ENVIRONMENTS = {
    "array", "bmatrix", "Bmatrix", "matrix", "pmatrix", "vmatrix", "Vmatrix",
}
_LATEX_ENV_TOKEN_PATTERN = re.compile(
    r"\\(?P<kind>begin|end)\s*\{(?P<env>[^{}]+)\}",
    flags=re.IGNORECASE,
)


def _extract_latex_math_blocks(text: str) -> list[tuple[str, int, int]]:
    """新增：提取完整矩阵环境块，并保留块在原文本中的起止位置。"""
    if not isinstance(text, str) or not text:
        return []

    blocks: list[tuple[str, int, int]] = []
    search_start = 0
    allowed_environments = {
        name.lower() for name in _MATRIX_BLOCK_ENVIRONMENTS
    }
    while search_start < len(text):
        outer_match = _LATEX_ENV_TOKEN_PATTERN.search(text, search_start)
        if outer_match is None:
            break
        if outer_match.group("kind").lower() != "begin":
            search_start = outer_match.end()
            continue

        outer_env = outer_match.group("env").strip()
        if outer_env.lower() not in allowed_environments:
            search_start = outer_match.end()
            continue

        stack = [outer_env]
        token_start = outer_match.end()
        block_end = -1
        while token_start < len(text):
            token_match = _LATEX_ENV_TOKEN_PATTERN.search(text, token_start)
            if token_match is None:
                break
            kind = token_match.group("kind").lower()
            env_name = token_match.group("env").strip()
            if kind == "begin":
                stack.append(env_name)
            elif stack and env_name.lower() == stack[-1].lower():
                stack.pop()
                if not stack:
                    block_end = token_match.end()
                    break
            else:
                # 环境不配对时放弃当前起点，继续寻找后续完整矩阵块。
                break
            token_start = token_match.end()

        if block_end > outer_match.start():
            blocks.append((
                text[outer_match.start():block_end],
                outer_match.start(),
                block_end,
            ))
            search_start = block_end
        else:
            search_start = outer_match.end()
    return blocks


def _parse_reference_latex_candidates(ref_latex: str):
    """新增：优先整体解析参考 LaTeX，失败后再按行回退。

    修改：逐行回退时过滤排版噪声（cases/矩阵环境/箭头等）和非表达式结果，
    避免把 ``\\begin{cases}`` 之类解析出的 tuple 送进 SymPy 比较。
    工单1：先做表示归一化（剥函数签名、代入中间变量），并追加“只比右端”
    的候选，覆盖 ``d_{ij}=...`` 与图片侧命名不一致的情况。
    """
    try:  # 兼容包导入与脚本直跑
        from . import ref_normalize
    except ImportError:  # pragma: no cover
        import ref_normalize  # type: ignore

    ref_latex = ref_normalize.prepare_reference(ref_latex)

    whole_expr, _ = latex_to_expr(ref_latex)
    if whole_expr is not None and hasattr(whole_expr, "free_symbols"):
        return [(whole_expr, ref_latex, "whole")], 1

    noise_tokens = ("\\begin{", "\\end{", "\\leftarrow", "\\mathbf",
                    "\\vdots", "\\cdots", "\\ldots")
    lines = [
        (line_number, line.strip())
        for line_number, line in enumerate(ref_latex.splitlines(), start=1)
        if line.strip()
    ]
    candidates = []
    for line_number, line in lines:
        if any(token in line for token in noise_tokens):
            continue
        # 单行候选必须是一条等式，否则多半是矩阵行或说明文字。
        if "=" not in line:
            continue
        expr, _ = latex_to_expr(line)
        if expr is not None and hasattr(expr, "free_symbols"):
            candidates.append((expr, line, f"line:{line_number}"))

    # 工单1：追加“逐式子式”候选。翻译层常在公式后跟 ``,\quad i=1,\dots,n``
    # 这类定义域说明，逐行解析会整行失败；_split_multiline_latex 会把说明
    # 拆成独立的 condition，只留真正的公式本体参与比较（softmax 的 s_i 行）。
    for row in _split_multiline_latex(ref_latex):
        row_text = str(row.get("expr") or "").strip()
        if not row_text or "=" not in row_text:
            continue
        row_expr, _ = latex_to_expr(row_text)
        if row_expr is not None and hasattr(row_expr, "free_symbols"):
            candidates.append((row_expr, row_text, f"row:{row.get('row')}"))

    # 工单1：追加“只比右端”的候选。左端往往是代码里的临时命名
    # （d_ij、z_ij、L、R），右端才是可比的数学表达式。
    for line_number, right in ref_normalize.right_sides(ref_latex):
        expr, _ = latex_to_expr(right)
        if expr is not None and hasattr(expr, "free_symbols"):
            candidates.append((expr, right, f"rhs:{line_number}"))

    # 修改：整段和逐行解析都失败后，再尝试完整矩阵块，保持原有优先级。
    for block_text, block_start, _ in _extract_latex_math_blocks(ref_latex):
        expr, _ = latex_to_expr(block_text)
        if expr is not None and hasattr(expr, "free_symbols"):
            candidates.append((expr, block_text, f"matrix_block:{block_start}"))
    return candidates, len(lines)


_CJK_CHAR_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_MATH_TELEGRAPH_PATTERN = re.compile(r"[\\^_=<>+\-*/()\[\]{}±×÷∑∏∫∂∇√≤≥≠≈]|\d")
# 视觉模型面对非公式图片时，可能把“思考过程”当正文返回。
_META_THINKING_MARKERS = (
    "实际输出必须", "此处为", "思考过程", "输出空", "根据要求",
    "空字符串", "无法识别", "不是公式", "非公式",
)


_SINGLE_SYMBOL_PATTERN = re.compile(r"^\s*([A-Za-z])\s*$")


def _left_symbol_of(latex: str) -> str:
    """返回 ``E = ABCD`` 形式里的左端单符号；不满足时返回空串。"""
    text = str(latex or "")
    if "=" not in text:
        return ""
    left = text.split("=", 1)[0]
    match = _SINGLE_SYMBOL_PATTERN.match(left)
    return match.group(1) if match else ""


def _looks_like_non_formula(text: str) -> bool:
    """新增：判断 OCR 正文是否其实是“非公式”说明文字。

    真实公式必然包含运算符、上下标、分式等数学记号；若正文是中文说明且
    不含任何数学记号，或命中明显的元思考关键词，则判为非公式。
    另外兼容视觉模型按 JSON 协议返回 {"result": ""} 的情况。
    """
    raw = "" if text is None else str(text)
    core = raw.strip().strip("`'\" \t\r\n")
    if not core:
        return True
    # 兼容 ```json {"result": "..."} ``` 这类协议包装，递归判断内部正文。
    stripped = core
    if stripped.lower().startswith("json"):
        stripped = stripped[4:].strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            payload = json.loads(stripped)
        except (TypeError, ValueError):
            return True
        if isinstance(payload, dict):
            for key in ("result", "latex", "formula", "math", "content"):
                if key in payload:
                    return _looks_like_non_formula(str(payload.get(key) or ""))
            return True
    if any(marker in core for marker in _META_THINKING_MARKERS):
        return True
    has_cjk = bool(_CJK_CHAR_PATTERN.search(core))
    has_math = bool(_MATH_TELEGRAPH_PATTERN.search(core))
    return has_cjk and not has_math


def _candidate_priority(check: CheckResult, work_latex: str,
                        rhs_text: str) -> float:
    """新增（w10）：给「未通过」的参考候选打分，选出最像同一公式的那一行。

    评分以**符号重叠度**为主、状态为辅。早期版本把状态放在首位
    （not_equivalent > unknown），结果 layout_matrix_frac 这类用例会挑中
    某个矩阵元素（比较结果必然 not_equivalent）而误判为错；反过来，纯按
    顺序取第一行又会让 blur_cosine 挑中辅助定义行。重叠度能同时避开这两个坑。
    """
    try:
        left_tokens = _formula_tokens(work_latex)
        right_tokens = _formula_tokens(rhs_text)
        union = left_tokens | right_tokens
        overlap = len(left_tokens & right_tokens) / len(union) if union else 0.0
    except Exception:
        overlap = 0.0
    status_weight = {
        "not_equivalent": 3.0,
        "shape_mismatch": 2.8,
        "needs_human_review": 2.0,
        "incomplete": 0.6,
        "unknown": 1.0,
        "parse_error": 0.2,
        "translation_failed": 0.1,
        "error": 0.0,
    }.get(str(getattr(check, "status", "") or ""), 1.0)
    # 主排序键是重叠度；状态只用来打破近似平局（0.01 的权重）。
    return overlap + 0.01 * status_weight


def _select_reference_candidate(lhs: Any, candidates: list[tuple]):
    """新增：矩阵左侧优先选择矩阵右侧，标量候选仅作为回退记录。"""
    candidate_list = list(candidates)
    if not isinstance(lhs, sp.MatrixBase):
        return candidate_list, []

    matrix_candidates = [
        candidate for candidate in candidate_list
        if isinstance(candidate[0], sp.MatrixBase)
    ]
    scalar_candidates = [
        candidate for candidate in candidate_list
        if not isinstance(candidate[0], sp.MatrixBase)
    ]
    return matrix_candidates, scalar_candidates


class Pipeline:
    """三层流水线编排器。"""

    def __init__(self, engine: str = "auto", use_llm: bool = True,
                 verbose: bool = False, direction: str = "formula2code",
                 verifier_mode: str = "auto"):
        self.engine = engine
        # 修改：使用 config 中的统一 API Key 名称。
        self.use_llm = use_llm and bool(config.API_KEY)
        # 修改：保存 CLI 指定方向，run() 未显式传入时沿用。
        self.direction = direction
        # 校验模式 auto / sympy-only / hybrid，同时作为校验缓存命名空间。
        if verifier_mode not in {"auto", "sympy-only", "hybrid"}:
            raise ValueError("verifier_mode 只能是 auto、sympy-only 或 hybrid")
        self.verifier_mode = verifier_mode
        # 工单2：scope 里带上校验逻辑版本号。归一化规则升级后，旧缓存必须
        # 自然失效，否则会继续返回升级前的结论（例如范数解析失败）。
        # 工单3：scope 里再带上异构校验器的实际构成，避免「加了 Qwen-VL
        # 之后仍复用没有它的旧结论」。
        self._verify_scope = (
            f"verifier:{verifier_mode}:{VERIFIER_LOGIC_VERSION}"
            f"|{self._hetero_chain_tag()}")
        self._vision: Optional[VisionOcr] = None
        self._pix: Optional[PixOcr] = None
        self._llm: Optional[TextLLM] = None
        self._verifier: Optional[Any] = None  # 新增：复用当前校验链
        if verbose:
            logging.basicConfig(level=logging.INFO,
                                format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @staticmethod
    def _hetero_chain_tag() -> str:
        """新增（工单3）：把当前会真正调用的异构校验器与模型压成短标签。"""
        tags: list[str] = []
        if getattr(config, "HETERO_VERIFY_ENABLED", True) is False:
            return "hetero=off"
        if getattr(config, "API_KEY", ""):
            tags.append(f"deepseek@{config.TEXT_MODEL}")
        if getattr(config, "GLM_API_KEY", ""):
            tags.append(f"glm@{config.GLM_VERIFIER_MODEL}")
        if getattr(config, "QWEN_API_KEY", ""):
            tags.append(f"qwen@{config.QWEN_VERIFIER_MODEL}")
        return "hetero=" + ",".join(tags) if tags else "hetero=none"

    # ---------- OCR 层 ----------
    def ocr(self, image_path: str, *, prompt: Optional[str] = None) -> OcrResult:
        # 新增：优先读取成功 OCR 缓存，未命中时执行原有识别逻辑。
        # 修改：缓存键纳入识别提示词与引擎模式，避免公式图/代码图串用结果。
        # 修改：图注结果使用独立的低优缓存范围，禁止占用干净结果的基础范围。
        # 修改（w10）：默认提示词也要进缓存键。此前 prompt 为 None 时键固定为
        # "auto|"，导致 VISION_PROMPT 升级后仍命中旧 OCR 结论（撇号丢失等）。
        effective_prompt = prompt or VISION_PROMPT
        base_scope = f"{self.engine}|{effective_prompt}"
        # 新增：只读取干净高优缓存；caption 低优缓存只写不读，避免长期串用。
        cached_result = cache.get_ocr(image_path, base_scope)
        if cached_result is not None:
            cached_result.meta = dict(cached_result.meta or {})
            cached_result.meta["cache"] = "hit"
            return cached_result

        result = self._ocr_uncached(image_path, prompt=prompt)
        if result.ok:
            result.meta = dict(result.meta or {})
            # 新增：图注或低置信度结果只进入 caption 范围，干净高优结果进 base。
            confidence = result.meta.get("confidence")
            try:
                low_confidence = (
                    confidence is not None and float(confidence) < 0.6)
            except (TypeError, ValueError):
                low_confidence = False
            use_caption_scope = bool(
                result.meta.get("caption_detected")) or low_confidence
            if use_caption_scope:
                ocr_scope = f"{base_scope}|caption"
                result.meta["cache"] = "miss_low_confidence"
            else:
                ocr_scope = base_scope
                result.meta["cache"] = "miss"
            result.meta["cache_scope"] = ocr_scope
            cache.put_ocr(image_path, result, ocr_scope)
        return result

    def _ocr_uncached(self, image_path: str, *,
                      prompt: Optional[str] = None) -> OcrResult:
        # 修改：原 OCR 分支整体保留，只在 ocr() 外层增加缓存包装。
        if self.engine == "vision":
            if prompt is None:
                return self._get_vision().recognize(image_path)
            return self._get_vision().recognize(image_path, prompt=prompt)
        if self.engine == "pix":
            return self._get_pix().recognize(image_path)
        if self.engine == "both":
            # 新增：双链路都执行，保留各自结果供报告对比。
            if prompt is None:
                vision_result = self._get_vision().recognize(image_path)
            else:
                vision_result = self._get_vision().recognize(
                    image_path, prompt=prompt)
            pix_result = self._get_pix().recognize(image_path)
            comparison = {
                "vision": _to_dict(vision_result),
                "pix2text": _to_dict(pix_result),
            }
            if vision_result.ok:
                chosen = vision_result
            elif pix_result.ok:
                chosen = pix_result
            else:
                chosen = vision_result
                chosen.error = (f"vision: {vision_result.error} | "
                                f"pix2text: {pix_result.error}")
            chosen.meta = dict(chosen.meta)
            chosen.meta["comparison"] = comparison
            chosen.meta["engine_chain"] = "vision+pix2text"
            return chosen
        # auto：视觉主力，失败回退本地 OCR
        if prompt is None:
            r = self._get_vision().recognize(image_path)
        else:
            r = self._get_vision().recognize(image_path, prompt=prompt)
        if r.ok:
            r.meta["engine_chain"] = "vision"
            return r
        r2 = self._get_pix().recognize(image_path)
        if r2.ok:
            r2.meta["fallback_from"] = f"vision: {r.error}"
            r2.meta["engine_chain"] = "vision>pix2text"
            return r2
        r.error = f"vision: {r.error} | pix2text: {r2.error}"
        r.meta["engine_chain"] = "vision>pix2text(failed)"
        return r

    def _get_vision(self) -> VisionOcr:
        if self._vision is None:
            self._vision = VisionOcr()
        return self._vision

    def _get_pix(self) -> PixOcr:
        if self._pix is None:
            self._pix = PixOcr()
        return self._pix

    def _get_llm(self) -> TextLLM:
        if self._llm is None:
            self._llm = TextLLM()
        return self._llm

    def _verify(self, latex: str, code: str,
                context: Optional[dict] = None) -> CheckResult:
        # 新增：先查缓存；context 只影响校验过程，不进入缓存键。
        # 修改：携带已解析表达式的调用也复用缓存结论，避免矩阵用例永远不命中。
        cached_result = cache.get_verify(latex, code, self._verify_scope)
        if cached_result is not None:
            cached_result.meta = dict(cached_result.meta or {})
            cached_result.meta["cache"] = "hit"
            return cached_result

        result = self._get_verifier().verify(latex, code, context)
        result.meta = dict(result.meta or {})
        result.meta["cache"] = "miss"
        cache.put_verify(latex, code, result, self._verify_scope)
        return result

    def _get_verifier(self) -> Any:
        # run() 会复用同一个校验链，单独调用时按指定模式懒加载。
        if self._verifier is None:
            if self.verifier_mode == "sympy-only":
                self._verifier = SymPyVerifier()
            elif self.verifier_mode == "hybrid":
                self._verifier = build_verifier_chain(
                    mode="serial", include_deepseek=True)
            else:
                self._verifier = build_verifier_chain(mode="serial")
        return self._verifier

    def _finish_report(self, report: dict, started: float) -> dict:
        # 新增：统一补齐耗时和缓存统计，保持既有报告字段与返回结构不变。
        report["elapsed"] = round(time.time() - started, 2)
        report["cache"] = cache.stats()
        return report

    # ---------- 翻译层 ----------
    def cpp_to_math(self, code: str) -> tuple[str, dict]:
        """C++ -> LaTeX；LLM 不可用时返回空串并说明原因。"""
        if not self.use_llm:
            return "", {"ok": False, "reason": "未启用 LLM（--no-llm 或缺少 API Key）"}
        res = self._get_llm().translate_cpp_to_math(code)
        if not res.ok:
            # 修改：失败时保留尝试次数、finish_reason 与 usage。
            return "", {"ok": False, "reason": res.error, "meta": res.meta}
        return strip_code_fence(res.text), {"ok": True, "meta": res.meta}

    def math_to_cpp(self, latex: str) -> tuple[str, dict]:
        # 新增：接通已有的公式 -> C++ 翻译接口。
        """LaTeX -> C++；仅在显式选择 formula2code 时调用。"""
        if not self.use_llm:
            return "", {"ok": False, "reason": "未启用 LLM（--no-llm 或缺少 API Key）"}
        res = self._get_llm().translate_math_to_cpp(latex)
        if not res.ok:
            # 修改：失败时保留尝试次数、finish_reason 与 usage。
            return "", {"ok": False, "reason": res.error, "meta": res.meta}
        return strip_code_fence(res.text), {"ok": True, "meta": res.meta}

    # ---------- 主流程 ----------
    def run(self, *, image: Optional[str] = None, latex: Optional[str] = None,
            cpp_path: Optional[str] = None, expect: Optional[str] = None,
            direction: Optional[str] = None,
            translate_formula: bool = False) -> dict:
        started = time.time()
        effective_direction = direction or self.direction
        report: dict = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "engine": self.engine,
            "verifier_mode": self.verifier_mode,
            "use_llm": self.use_llm,
            "direction": effective_direction,
            "inputs": {"image": image, "latex": latex, "cpp": cpp_path, "expect": expect},
            "steps": {},
            "ok": False,
        }

        # ---- 新增：代码 -> 公式方向 ----
        if effective_direction == "code2formula":
            code = ""
            code_source = "none"
            if cpp_path:
                code = Path(cpp_path).read_text(
                    encoding="utf-8", errors="replace")
                code_source = "cpp_file"
            elif image:
                ocr_res = self.ocr(image, prompt=CODE_OCR_PROMPT)
                report["steps"]["ocr"] = _to_dict(ocr_res)
                if ocr_res.ok:
                    code = strip_code_fence(ocr_res.latex)
                    code_source = "image_ocr"

            if not code.strip():
                report["error"] = (
                    "code2formula 未获得可用源码。请用 --cpp 提供代码文件，"
                    "或用 --image 提供可识别的代码图片")
                return self._finish_report(report, started)

            ref_latex, meta = self.cpp_to_math(code)
            report["steps"]["translate_cpp"] = {
                "ok": meta.get("ok", False),
                "source": code_source,
                "ref_latex": ref_latex,
                "thought": "从代码的控制流与赋值关系提取核心数学表达式",
            }
            if meta.get("meta"):
                report["steps"]["translate_cpp"]["meta"] = meta["meta"]
            if meta.get("reason"):
                report["steps"]["translate_cpp"]["reason"] = meta["reason"]

            if not ref_latex:
                report["error"] = meta.get("reason", "代码转公式失败")
                return self._finish_report(report, started)

            report["steps"]["latex"] = {"text": ref_latex,
                                        "source": "translation"}
            checks = []
            if latex:
                lhs, first_error = latex_to_expr(latex)
                if lhs is None:
                    checks.append(("latex_vs_cpp", CheckResult(
                        False, "parse_error", latex, ref_latex,
                        f"解析失败: {first_error}")))
                else:
                    # 修改：矩阵或多行公式先整体解析，失败后才逐行回退。
                    candidates, _ = _parse_reference_latex_candidates(
                        ref_latex)
                    best = None
                    for rhs, _, _ in candidates:
                        check = compare_expressions(lhs, rhs)
                        if check.ok:
                            best = check
                            break
                        if best is None:
                            best = check
                    if best is None:
                        checks.append(("latex_vs_cpp", CheckResult(
                            False, "parse_error", latex, ref_latex,
                            "翻译结果无可用公式行")))
                    else:
                        checks.append(("latex_vs_cpp", best))
            else:
                # 新增：没有独立公式时，至少确认翻译结果可被 SymPy 解析。
                parsed = None
                parse_error = "翻译结果为空"
                candidates, _ = _parse_reference_latex_candidates(ref_latex)
                if candidates:
                    parsed = candidates[0][0]
                else:
                    lines = [
                        line.strip()
                        for line in ref_latex.splitlines()
                        if line.strip()
                    ]
                    if lines:
                        _, parse_error = latex_to_expr(lines[0])
                checks.append(("translated_latex_syntax", CheckResult(
                    parsed is not None,
                    "parsed" if parsed is not None else "parse_error",
                    ref_latex,
                    "",
                    "翻译结果可通过 SymPy 语法解析"
                    if parsed is not None else f"解析失败: {parse_error}")))

            report["steps"]["checks"] = [
                {"name": name, **check.to_dict()} for name, check in checks
            ]
            report["ok"] = bool(checks) and all(c.ok for _, c in checks)
            return self._finish_report(report, started)

        # ---- 1. 输入 -> LaTeX ----
        latex_from_ocr = ""
        if image:
            ocr_res = self.ocr(image)
            report["steps"]["ocr"] = _to_dict(ocr_res)
            if ocr_res.ok:
                latex_from_ocr = ocr_res.latex
            # 新增：视觉模型可能把“这是空图/非公式”的思考文字当正文返回，
            # 这里统一判为非公式并提前结束，避免落到 translation_failed。
            if ocr_res.ok and _looks_like_non_formula(latex_from_ocr):
                report["steps"]["ocr"]["latex"] = ""
                report["steps"]["ocr"]["non_formula"] = True
                report["error"] = "图片内容不是数学公式，已跳过"
                return self._finish_report(report, started)
        work_latex = latex or latex_from_ocr
        report["steps"]["latex"] = {"text": work_latex,
                                    "source": "direct" if latex else ("ocr" if latex_from_ocr else "none"),
                                    "thought": "识别公式主体并保留上下标、分式和括号结构"}
        if not work_latex:
            report["error"] = "没有获得可用 LaTeX（图片 OCR 失败且未直接提供 --latex）"
            return self._finish_report(report, started)

        # ---- 新增：显式 formula2code 时生成对应 C++ ----
        if translate_formula:
            generated_cpp, math_meta = self.math_to_cpp(work_latex)
            report["steps"]["translate_math"] = {
                "ok": math_meta.get("ok", False),
                "cpp": generated_cpp,
            }
            if math_meta.get("meta"):
                report["steps"]["translate_math"]["meta"] = math_meta["meta"]
            if math_meta.get("reason"):
                report["steps"]["translate_math"]["reason"] = (
                    math_meta["reason"])

        # ---- 2. 翻译层：C++ -> 数学 ----
        ref_latex = ""
        raw_cpp_code = ""
        if cpp_path:
            code = Path(cpp_path).read_text(encoding="utf-8", errors="replace")
            raw_cpp_code = code
            ref_latex, meta = self.cpp_to_math(code)
            report["steps"]["translate_cpp"] = {"ok": meta.get("ok", False),
                                                "ref_latex": ref_latex,
                                                **({"meta": meta.get("meta")} if meta.get("meta") else {}),
                                                **({"reason": meta.get("reason")} if meta.get("reason") else {})}

        # ---- 3. 校验层 ----
        checks = []
        # 按 verifier_mode 构建串行链，复用当前实例。
        self._verifier = self._get_verifier()
        cpp_context = (
            {"cpp_code": raw_cpp_code}
            if self.verifier_mode == "hybrid" and raw_cpp_code
            else {}
        )
        if expect:
            checks.append(("latex_vs_expect", self._verify(work_latex, expect)))
        if ref_latex:
            lhs, e1 = latex_to_expr(work_latex)
            if lhs is None:
                matrix_blocks = _extract_latex_math_blocks(work_latex)
                if len(matrix_blocks) == 1:
                    # 新增：OCR 注记导致整段解析失败时，仅用唯一矩阵块重试。
                    matrix_lhs, matrix_error = latex_to_expr(
                        matrix_blocks[0][0])
                    report["steps"]["latex"]["extracted"] = "matrix_block"
                    if matrix_lhs is not None:
                        lhs = matrix_lhs
                        e1 = ""
                    else:
                        e1 = matrix_error
            if lhs is None:
                # 修改：SymPy 解析失败时交给校验链兜底，异构 VLM 仍可给出判定。
                checks.append(("latex_vs_cpp", self._verify(
                    work_latex, ref_latex, cpp_context or None)))
            else:
                # 修改：先整体解析矩阵或多行公式，失败后才逐行回退。
                candidates, ref_lines = _parse_reference_latex_candidates(
                    ref_latex)
                # 新增：矩阵左侧只比较矩阵候选，避免矩阵与标量误配。
                selected_candidates, scalar_fallbacks = (
                    _select_reference_candidate(lhs, candidates))
                best = None
                best_score: Optional[float] = None
                best_source = ""
                for rhs, rhs_text, rhs_source in selected_candidates:
                    # 修改：复用已解析表达式，保证无 Key 时行为与旧 compare_expressions 一致。
                    # 工单1：左侧变体策略——
                    #   1) rhs 候选来自“右端”时，用工作公式每一行的右端去配对；
                    #   2) 否则保留原有“单符号左端可丢弃”的对齐规则。
                    variants = [(work_latex, lhs)]
                    if str(rhs_source).startswith("rhs:"):
                        try:
                            from . import ref_normalize as _ref_norm
                        except ImportError:  # pragma: no cover
                            import ref_normalize as _ref_norm  # type: ignore
                        for _, right_text in _ref_norm.right_sides(work_latex):
                            right_expr, _ = latex_to_expr(right_text)
                            if right_expr is not None:
                                variants.append((right_text, right_expr))
                    else:
                        left_symbol = _left_symbol_of(work_latex)
                        if left_symbol and "=" not in rhs_text:
                            tail = work_latex.split("=", 1)[1].strip()
                            tail_expr, _ = latex_to_expr(tail)
                            if tail_expr is not None:
                                variants.append((tail, tail_expr))
                    chk = None
                    for target_text, target_expr in variants:
                        chk = self._verify(
                            target_text, rhs_text,
                            {**cpp_context,
                             "preparsed_lhs_expr": target_expr,
                             "preparsed_rhs_expr": rhs})
                        if chk.ok:
                            break
                    if chk is None:  # pragma: no cover - variants 至少有一个
                        continue
                    if chk.ok:
                        best = chk
                        best_source = rhs_source
                        break
                    # 新增（w10）：没有等价候选时，不再一味保留第一行。
                    # 翻译层常多给一行辅助定义（如 n = dim(a)），第一行往往
                    # 解析失败（unknown），会把真正可判定的那一行挤掉。
                    score = _candidate_priority(chk, work_latex, rhs_text)
                    if best_score is None or score > best_score:
                        best = chk
                        best_score = score
                        best_source = rhs_source
                if best is None:
                    # 修改：逐行解析全部失败时交给校验链兜底，异构 VLM 可判定多行方程组。
                    full_check = self._verify(
                        work_latex, ref_latex, cpp_context or None)
                    if scalar_fallbacks:
                        full_check.meta["scalar_candidate_fallbacks"] = [
                            candidate[2] for candidate in scalar_fallbacks
                        ]
                    checks.append(("latex_vs_cpp", full_check))
                else:
                    best.meta = dict(best.meta or {})
                    best.meta["ref_lines"] = ref_lines
                    best.meta["rhs_source"] = best_source or rhs_source
                    if scalar_fallbacks:
                        best.meta["scalar_candidate_fallbacks"] = [
                            candidate[2] for candidate in scalar_fallbacks
                        ]
                    checks.append(("latex_vs_cpp", best))
        elif cpp_path:
            # 修改：C++ 翻译为空时给出明确结论，避免报告只显示未完成而无原因。
            checks.append(("latex_vs_cpp", CheckResult(
                False, "translation_failed", work_latex, "",
                "C++ 翻译结果为空（LLM 未产出公式）")))
            translate_cpp_info = report["steps"].get("translate_cpp", {})
            translate_reason = translate_cpp_info.get("reason") or "未知原因"
            report["error"] = f"C++ 翻译结果为空：{translate_reason}"
        if not checks and translate_formula:
            translated = report["steps"].get("translate_math", {})
            parsed, parse_error = latex_to_expr(work_latex)
            translation_ok = bool(translated.get("cpp")) and parsed is not None
            checks.append(("formula2code_translation", CheckResult(
                translation_ok,
                "translated" if translation_ok else "translation_failed",
                work_latex,
                "generated C++",
                "已生成 C++；未提供独立对照，因此未声明数学等价"
                if translation_ok else
                f"公式解析或代码生成失败: {parse_error}")))
        # ---- 工单3：PDE 兜底标记 ----
        # 偏微分方程无法用 SymPy 完成严格符号证明；这里补一层结构化分析，
        # 把结论显式标成“需人工确认”，并记录可对比的算子证据。
        try:
            from . import pde_analysis
        except ImportError:  # pragma: no cover - 脚本直跑
            import pde_analysis  # type: ignore
        if pde_analysis.is_pde(work_latex) or pde_analysis.is_pde(ref_latex):
            pde_report = pde_analysis.analyze(work_latex, ref_latex)
            report["steps"]["pde_analysis"] = pde_report
            for name, check in checks:
                if name != "latex_vs_cpp":
                    continue
                # 工单3：只要涉及 PDE，一律标记“需人工确认”，
                # 因为连续方程与离散实现之间不存在可自动证明的等价关系。
                check.meta = dict(check.meta or {})
                check.meta.update({
                    "pde_detected": True,
                    "needs_human_review": True,
                    "pde_structurally_aligned": pde_report.get(
                        "structurally_aligned"),
                    "pde_capability_limit": (
                        "连续 PDE 与离散实现之间不存在 SymPy 可判定的"
                        "代数等价关系"),
                })
                if check.status in {"unknown", "parse_error",
                                    "translation_failed"}:
                    check.detail = (
                        f"{pde_report.get('detail') or 'PDE 需人工确认'} "
                        f"[原判定: {check.status}]"
                    )
                elif check.status == "not_equivalent":
                    check.detail = (
                        f"{check.detail} | PDE 提示：{pde_report.get('detail')}"
                    )
        report["steps"]["checks"] = [
            {"name": name, **check.to_dict()} for name, check in checks
        ]
        report["ok"] = bool(checks) and all(c.ok for _, c in checks)

        # ---- 工单1（w11）：可解释批改层 ----
        # 硬约束：只读上面已经生成的结论，在报告里追加 explain 字段；
        # 关闭 EXPLAIN_ENABLED 时这里完全不执行，报告结构与改动前一致。
        if getattr(config, "EXPLAIN_ENABLED", False):
            try:
                try:
                    from .explain import attach_explanations
                except ImportError:  # pragma: no cover - 脚本直跑
                    from explain import attach_explanations  # type: ignore
                attach_explanations(
                    report,
                    latex_expr=work_latex,
                    ref_latex=ref_latex,
                    cpp_code=raw_cpp_code,
                )
            except Exception as exc:  # 解释层故障不得影响判定与报告返回
                logger.warning("解释层执行失败（判定结论不变）：%s", exc)

        # ---- 4. 输出层 ----
        return self._finish_report(report, started)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="math-agent",
        description="数学手写公式 -> LaTeX -> C++ 对照校验流水线（视觉/本地 OCR 双链路）")
    p.add_argument("-i", "--image", help="公式或代码图片路径")
    p.add_argument("-l", "--latex", help="直接提供 LaTeX（跳过 OCR）")
    p.add_argument("-c", "--cpp", help="参考 C++ 源码路径（由 LLM 提取数学公式）")
    p.add_argument("-e", "--expect", help="期望的数学表达式（Python/SymPy 语法）")
    p.add_argument("--verifier-mode",
                   choices=["sympy-only", "hybrid"],
                   default=None,
                   help="校验模式：sympy-only=仅符号校验，hybrid=符号校验+DeepSeek；"
                        "不指定时沿用自动链")
    # 新增：mode 是面向演示的链路选择，engine 继续保留兼容旧命令。
    p.add_argument("--mode", choices=["vision", "pix", "both"], default=None,
                   help="OCR 链路：vision=DeepSeek Vision，pix=Pix2Text，"
                        "both=双链路对比；不指定时沿用 --engine")
    p.add_argument("--direction",
                   choices=["formula2code", "code2formula"],
                   default="formula2code",
                   help="翻译方向：formula2code=公式到代码（默认），"
                        "code2formula=代码到公式")
    p.add_argument("--engine", choices=["auto", "vision", "pix"], default="auto",
                   help="兼容旧参数：auto=视觉优先+本地兜底（默认）")
    p.add_argument("--out", default=str(config.OUTPUT_DIR), help="报告输出目录")
    p.add_argument("--no-llm", action="store_true", help="禁用 LLM 翻译层")
    p.add_argument("--json", action="store_true", help="stdout 输出 JSON 报告")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="打印三段式中间结果并启用详细日志")
    return p


def build_markdown(report: dict) -> str:
    cache_stats = report.get("cache") or cache.stats()
    lines = ["# 数学公式校验报告", "",
             f"- 时间：{report.get('timestamp')}",
             f"- 翻译方向：`{report.get('direction', 'formula2code')}`",
             f"- OCR 引擎：`{report.get('engine')}`",
             f"- LLM 翻译：{'启用' if report.get('use_llm') else '禁用'}",
             f"- 总耗时：{report.get('elapsed', '?')}s",
             f"- 缓存命中：{cache.format_stats(cache_stats)}",
             f"- 结论：{'✅ 通过' if report.get('ok') else '❌ 未通过'}", ""]
    ocr = report.get("steps", {}).get("ocr")
    if ocr:
        lines += ["## OCR 结果", "",
                  f"- 引擎链：`{ocr.get('meta', {}).get('engine_chain', ocr.get('engine'))}`",
                  f"- 状态：{'成功' if ocr.get('ok') else '失败'}",
                  f"- LaTeX：`{ocr.get('latex', '')}`"]
        if ocr.get("error"):
            lines.append(f"- 错误：`{ocr['error']}`")
        comparison = ocr.get("meta", {}).get("comparison", {})
        if comparison:
            lines += ["", "### 双链路对比", "",
                      "| 链路 | 状态 | 识别结果 |",
                      "|---|---|---|"]
            for name, item in comparison.items():
                lines.append(
                    f"| {name} | {'成功' if item.get('ok') else '失败'} | "
                    f"`{item.get('latex', '')}` |")
        lines.append("")
    lat = report.get("steps", {}).get("latex")
    if lat:
        lines += ["## 待校验公式", "",
                  f"- 来源：{lat.get('source')}",
                  f"- LaTeX：`{lat.get('text', '')}`"]
        if lat.get("thought"):
            lines.append(f"- 翻译思路：{lat['thought']}")
        lines.append("")
    tm = report.get("steps", {}).get("translate_math")
    if tm:
        lines += ["## 数学 -> C++（LLM）", "",
                  f"- 状态：{'成功' if tm.get('ok') else '失败'}"]
        if tm.get("cpp"):
            lines += ["", "```cpp", tm["cpp"], "```"]
        if tm.get("reason"):
            lines.append(f"- 原因：`{tm['reason']}`")
        lines.append("")
    tr = report.get("steps", {}).get("translate_cpp")
    if tr:
        lines += ["## C++ -> 数学（LLM）", "",
                  f"- 状态：{'成功' if tr.get('ok') else '失败'}"]
        if tr.get("source"):
            lines.append(f"- 输入来源：{tr.get('source')}")
        if tr.get("thought"):
            lines.append(f"- 翻译思路：{tr.get('thought')}")
        if tr.get("ref_latex"):
            lines.append(f"- 参考 LaTeX：`{tr['ref_latex']}`")
        if tr.get("reason"):
            lines.append(f"- 原因：`{tr['reason']}`")
        lines.append("")
    checks = report.get("steps", {}).get("checks", [])
    if checks:
        lines += ["## 数学校验", ""]
        for c in checks:
            icon = "✅" if c.get("ok") else "❌"
            lines.append(f"### {icon} {c.get('name')} — {c.get('status')}")
            lines.append(f"- 左：`{c.get('lhs', '')}`")
            lines.append(f"- 右：`{c.get('rhs', '')}`")
            if c.get("detail"):
                lines.append(f"- 说明：{c.get('detail')}")
            for d in c.get("diff_points", [])[:3]:
                lines.append(f"  - 差异点 {d.get('point')}: 左={d.get('lhs')} 右={d.get('rhs')} 相对误差={d.get('rel_delta')}")
            lines.append("")
    return "\n".join(lines)


def build_verbose_sections(report: dict) -> str:
    # 新增：统一生成需求指定的三段式终端展示。
    """把中间结果整理成固定的三段式终端输出。"""
    steps = report.get("steps", {})
    ocr = steps.get("ocr", {})
    latex_step = steps.get("latex", {})
    translated_cpp = steps.get("translate_math", {})
    translated_math = steps.get("translate_cpp", {})
    checks = steps.get("checks", [])

    lines = ["【1. 数学推导】",
             f"- 翻译方向：{report.get('direction', 'formula2code')}",
             f"- OCR 链路：{report.get('engine', 'auto')}"]
    if ocr:
        lines.append(f"- OCR 结果：{ocr.get('latex', '') or '未识别到内容'}")
    if latex_step.get("text"):
        lines.append(f"- 当前 LaTeX：{latex_step['text']}")
    if latex_step.get("thought"):
        lines.append(f"- 翻译思路：{latex_step['thought']}")
    if translated_cpp:
        lines.append(
            f"- 公式转代码：{'成功' if translated_cpp.get('ok') else '失败'}")
    if translated_math:
        lines.append(
            f"- 代码转公式：{'成功' if translated_math.get('ok') else '失败'}")
        if translated_math.get("ref_latex"):
            lines.append(f"- 翻译 LaTeX：{translated_math['ref_latex']}")
        if translated_math.get("thought"):
            lines.append(f"- 翻译思路：{translated_math['thought']}")
    if report.get("error"):
        lines.append(f"- 中断原因：{report['error']}")

    lines += ["", "【2. 校验结果】"]
    if checks:
        for check in checks:
            conclusion = "通过" if check.get("ok") else "未通过"
            lines.append(
                f"- {check.get('name')}：{conclusion}，"
                f"状态 {check.get('status')}")
            if check.get("detail"):
                lines.append(f"  说明：{check['detail']}")
            for diff in check.get("diff_points", [])[:3]:
                lines.append(
                    f"  差异点 {diff.get('point')}："
                    f"左={diff.get('lhs')}，右={diff.get('rhs')}，"
                    f"相对误差={diff.get('rel_delta')}")
    elif report.get("error"):
        lines.append("- SymPy 一致性结论：未执行，流水线在准备阶段中断")
    else:
        lines.append("- SymPy 一致性结论：未执行（未提供 --expect 或 --cpp 对照）")

    latex_text = " ".join(str(latex_step.get("text", "")).split())
    knowledge_notes = []
    if any(token in latex_text for token in ("\\sin", "\\cos", "\\tan")):
        knowledge_notes.append("三角函数题要关注角制与恒等式，如 sin²θ+cos²θ=1。")
    if "\\frac" in latex_text:
        knowledge_notes.append("分式校验时要同时关注分母不能为零的定义域。")
    if "\\sqrt" in latex_text:
        knowledge_notes.append("根式在实数范围内要求被开方数有意义。")
    if "\\sum" in latex_text:
        knowledge_notes.append("求和式应先确认求和变量与上下界，再看闭式结果。")
    if "matrix" in latex_text.lower() or "矩阵" in report.get("error", ""):
        knowledge_notes.append("矩阵乘法的每个元素都遵循“行乘列再求和”。")
    if not knowledge_notes:
        knowledge_notes.append(
            "符号校验比字符串对比更可靠，它会把等价变形化简到同一形式。")
    lines += ["", "【3. 知识点备注】"]
    lines.extend(f"- {note}" for note in knowledge_notes)
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    direction_is_explicit = any(
        item == "--direction" or item.startswith("--direction=")
        for item in raw_args)
    args = build_parser().parse_args(argv)
    if args.verbose:
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not any([args.image, args.latex, args.cpp]):
        build_parser().print_help()
        print("\n[错误] 至少提供 --image / --latex / --cpp 之一", file=sys.stderr)
        return 2
    # 新增：在进入 OCR/API 前检查输入路径，避免无效调用。
    if args.image and not Path(args.image).is_file():
        print(f"\n[错误] 图片路径不存在或不是文件：{args.image}",
              file=sys.stderr)
        return 2
    if args.cpp and not Path(args.cpp).is_file():
        print(f"\n[错误] C++ 源码路径不存在或不是文件：{args.cpp}",
              file=sys.stderr)
        return 2
    if args.direction == "code2formula" and not (args.image or args.cpp):
        print("\n[错误] code2formula 需要 --cpp 代码文件或 --image 代码图片",
              file=sys.stderr)
        return 2
    if args.mode and args.engine != "auto" and args.mode != args.engine:
        print(f"\n[错误] --mode {args.mode} 与 --engine {args.engine} 冲突；"
              "请只保留其中一个。", file=sys.stderr)
        return 2

    engine = args.mode or args.engine
    pipe = Pipeline(engine=engine, use_llm=not args.no_llm,
                    verbose=args.verbose, direction=args.direction,
                    verifier_mode=args.verifier_mode or "auto")
    try:
        report = pipe.run(
            image=args.image,
            latex=args.latex,
            cpp_path=args.cpp,
            expect=args.expect,
            direction=args.direction if direction_is_explicit else None,
            translate_formula=(
                direction_is_explicit and args.direction == "formula2code"))
    # 新增：中断信号不打印 traceback，并遵循 130 约定。
    except KeyboardInterrupt:
        print("\n[已取消] 收到中断信号", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"\n[错误] 流水线执行失败：{type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"\n[错误] 无法创建报告目录 {out_dir}：{exc}", file=sys.stderr)
        return 2
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"report_{ts}.json"
    md_path = out_dir / f"report_{ts}.md"
    try:
        json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8")
        md_path.write_text(build_markdown(report), encoding="utf-8")
    except OSError as exc:
        print(f"\n[错误] 报告写入失败：{exc}", file=sys.stderr)
        return 2

    if args.verbose:
        print(build_verbose_sections(report),
              file=sys.stderr if args.json else sys.stdout)
        print("", file=sys.stderr if args.json else sys.stdout)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(build_markdown(report))
        print(f"\n[报告已保存] {json_path} | {md_path}", file=sys.stderr)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
