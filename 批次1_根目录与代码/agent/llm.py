# -*- coding: utf-8 -*-
"""agent/llm.py — DeepSeek 文本模型封装（C++ 代码 <-> 数学公式 双向翻译）。

统一职责：
- chat(): 最小可用的 OpenAI 兼容调用（重试/超时/用量统计）；
- translate_cpp_to_math(): 从 C++ 代码提取核心数学表达式（LaTeX）；
- translate_math_to_cpp(): 数学公式 -> 可编译的 C++ 片段；
- normalize_latex(): 修复 OCR 噪声造成的 LaTeX 语法错误；
- explain(): 生成数学解释。
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

try:
    from . import config
except ImportError:  # pragma: no cover
    import config  # type: ignore

try:  # 新增：兼容包导入和脚本直接运行。
    from .symbol_norm import normalize_latex_symbols
except ImportError:  # pragma: no cover
    from symbol_norm import normalize_latex_symbols  # type: ignore

try:  # 工单1：翻译结果持久化缓存，防止同一输入多次调用结果漂移。
    from . import cache as result_cache
except ImportError:  # pragma: no cover
    import cache as result_cache  # type: ignore

logger = logging.getLogger(__name__)

# 修改：提高首次翻译预算，减少 reasoning 占满后正文为空的情况。
TRANSLATION_MAX_TOKENS = 8192
TRANSLATION_RETRY_MAX_TOKENS = 16384
TRANSLATION_SEGMENT_LINES = 120
TRANSLATION_MAX_SEGMENTS = 8

# ---------- 工单1：翻译稳定性 ----------
# 翻译一律使用 temperature=0，杜绝采样随机性带来的结果漂移。
TRANSLATION_TEMPERATURE = 0.0
# 提示词版本号参与缓存 key：提示词变化后旧缓存自然失效。
TRANSLATION_PROMPT_VERSION = "20261115-w10e"
# 可选的多轮投票：默认 1（关闭），设为 3 时对同一输入取多数结果。
TRANSLATION_VOTE_ROUNDS = max(
    1, min(5, int(os.environ.get("MATH_AGENT_TRANSLATE_VOTE", "1") or 1)))
# 翻译缓存开关：MATH_AGENT_TRANSLATE_CACHE=0 可临时关闭。
TRANSLATION_CACHE_ENABLED = os.environ.get(
    "MATH_AGENT_TRANSLATE_CACHE", "1").strip() != "0"

_CODE_KEYWORDS = frozenset({
    "include", "define", "const", "static", "return", "for", "while", "if",
    "else", "int", "double", "float", "void", "bool", "char", "size_t",
    "std", "vector", "auto", "true", "false", "nullptr", "namespace",
    "using", "template", "typename", "class", "struct", "public", "private",
    "protected", "unsigned", "long", "short", "inline", "sizeof", "new",
    "delete", "switch", "case", "break", "continue", "do", "try", "catch",
})


def _code_identifiers(code: str) -> set[str]:
    """新增（w10）：提取代码中真正承载数学含义的标识符。"""
    try:
        raw = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str(code or ""))
    except Exception:
        return set()
    names: set[str] = set()
    for name in raw:
        if name.lower() in _CODE_KEYWORDS:
            continue
        if len(name) == 1:
            # 单字母变量是主力（a/b/c/x/w/…），但避开循环计数 i/j/k？
            # 不避开：它们同样可能出现在公式里。
            names.add(name)
            continue
        if len(name) <= 24 and not name.startswith("_"):
            names.add(name)
    return names


SYSTEM_TRANSLATOR = (
    "你是同时精通数学与 C++ 的专家。你只做精确转写，不做发挥；"
    "如果信息不足，如实指出缺失，不臆造。"
)

SYSTEM_NORMALIZER = (
    "你是 LaTeX 语法修复器。输入是 OCR 得到的可能含噪声的 LaTeX，"
    "你只修复语法错误与明显的漏字符，不改变数学含义。"
)


@dataclass
class LLMResult:
    text: str = ""
    ok: bool = False
    error: str = ""
    meta: dict = field(default_factory=dict)
    # 新增：统一结构化错误码，放在末尾以保持位置参数兼容。
    error_code: str = ""


def _normalize_translated_result(result: LLMResult) -> LLMResult:
    """新增：翻译成功时归一化文本，并保留原有元信息。"""
    if (
        not isinstance(result, LLMResult)
        or not result.ok
        or not result.text.strip()
    ):
        return result
    return LLMResult(
        ok=True,
        text=normalize_latex_symbols(result.text),
        meta=result.meta,
        error_code=result.error_code,
    )


def _response_value(obj, name):
    """兼容 SDK 顶层字段与 model_extra 扩展字段。"""
    value = getattr(obj, name, None)
    if value is None:
        value = (getattr(obj, "model_extra", None) or {}).get(name)
    return value


def _usage_meta(resp, elapsed, attempt=1):
    """新增：先保存响应诊断信息，保证空正文时 usage 也不丢失。"""
    usage = getattr(resp, "usage", None)
    choice = resp.choices[0] if getattr(resp, "choices", None) else None
    message = getattr(choice, "message", None)
    content = (_response_value(message, "content") or "").strip()
    reasoning_content = _response_value(message, "reasoning_content") or ""
    finish_reason = getattr(choice, "finish_reason", None)
    meta = {
        "elapsed": round(elapsed, 2),
        "attempt": attempt,
        "usage_available": usage is not None,
        "finish_reason": finish_reason,
        "content_length": len(content),
        "content_empty": not bool(content),
        "reasoning_content_length": len(reasoning_content),
        "response_id": getattr(resp, "id", None),
        "response_model": getattr(resp, "model", None),
    }
    if usage is not None:
        meta["tokens"] = {
            "prompt": getattr(usage, "prompt_tokens", None),
            "completion": getattr(usage, "completion_tokens", None),
            "total": getattr(usage, "total_tokens", None),
        }
    if not content:
        if finish_reason == "length":
            meta["empty_cause"] = "max_tokens_exhausted"
        elif reasoning_content:
            meta["empty_cause"] = "reasoning_content_only"
        elif finish_reason == "content_filter":
            meta["empty_cause"] = "content_filter"
        else:
            meta["empty_cause"] = "server_empty_content"
    return meta


def _finalize_meta(last_meta, attempt_details):
    """新增：汇总多次尝试的 token，部分失败时明确标记 usage 不完整。"""
    meta = dict(last_meta)
    meta["attempts"] = len(attempt_details)
    meta["attempts_detail"] = attempt_details
    captured = [
        item["tokens"] for item in attempt_details
        if item.get("usage_available") and isinstance(item.get("tokens"), dict)
    ]
    if captured:
        meta["tokens"] = {
            key: sum(int(item.get(key) or 0) for item in captured)
            for key in ("prompt", "completion", "total")
        }
        if len(captured) == len(attempt_details):
            meta["usage_capture"] = "complete"
        else:
            meta["usage_capture"] = "partial"
    else:
        meta["usage_capture"] = "unavailable"
    meta["usage_available"] = (
        bool(attempt_details) and len(captured) == len(attempt_details)
    )
    return meta


def _split_code_for_translation(code: str, target_lines: int,
                                max_segments: int) -> list[str]:
    """新增：按约 120 行切块，并尽量落在函数结束的右花括号处。"""
    lines = code.splitlines()
    if not lines:
        return []

    target_lines = max(1, int(target_lines))
    max_segments = max(1, int(max_segments))
    chunk_size = max(
        target_lines, (len(lines) + max_segments - 1) // max_segments)
    segments = []
    start = 0
    while start < len(lines) and len(segments) < max_segments:
        if len(segments) == max_segments - 1:
            end = len(lines)
        else:
            end = min(len(lines), start + chunk_size)
            if end < len(lines):
                preferred_start = start + max(1, chunk_size // 2)
                for index in range(end - 1, preferred_start - 1, -1):
                    if lines[index].lstrip().startswith("}"):
                        end = index + 1
                        break
        segments.append("\n".join(lines[start:end]))
        start = end
    return segments


class TextLLM:
    """DeepSeek 文本对话封装。"""

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None,
                 base_url: Optional[str] = None, timeout: Optional[int] = None):
        # 修改：模型、密钥和接口地址统一从 config 读取。
        self.model = model or config.TEXT_MODEL
        self.api_key = api_key if api_key is not None else config.API_KEY
        self.base_url = base_url or config.BASE_URL
        self.timeout = timeout or config.REQUEST_TIMEOUT
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        key = (self.api_key or "").strip()
        if not key:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY（请 export 后重试）")
        from openai import OpenAI
        self._client = OpenAI(api_key=key, base_url=self.base_url,
                              timeout=self.timeout, max_retries=0)
        return self._client

    def chat(self, messages, *, system: Optional[str] = None,
             temperature: Optional[float] = None, retries: Optional[int] = None,
             max_tokens: Optional[int] = None) -> LLMResult:
        retries = config.MAX_RETRIES if retries is None else retries
        temperature = config.TEMPERATURE if temperature is None else temperature
        try:
            client = self._ensure_client()
        except Exception as e:
            # 修改：缺少 Key 等客户端初始化失败统一归类。
            return LLMResult(
                ok=False,
                error=str(e),
                error_code="missing_api_key",
            )
        msgs = list(messages)
        if system:
            msgs = [{"role": "system", "content": system}] + msgs
        last_err = ""
        last_error_code = ""
        last_meta = {}
        attempt_details = []
        for attempt in range(retries + 1):
            t0 = time.time()
            try:
                kwargs = dict(model=self.model, messages=msgs, temperature=temperature)
                kwargs["max_tokens"] = max_tokens or 2048
                resp = client.chat.completions.create(**kwargs)
                meta = _usage_meta(resp, time.time() - t0, attempt + 1)
                attempt_details.append(meta)
                text = (_response_value(resp.choices[0].message, "content") or "").strip()
                if not text:
                    # 修改：空正文保留重试，最终返回 empty_content。
                    last_err = "模型返回空内容"
                    last_error_code = "empty_content"
                    last_meta = meta
                    if attempt < retries:
                        time.sleep(min(2 ** attempt, 6))
                        continue
                    break
                return LLMResult(
                    text=text, ok=True,
                    meta=_finalize_meta(meta, attempt_details),
                )
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                # 修改：区分超时与其他 API 异常。
                is_timeout = (
                    "timeout" in type(e).__name__.lower()
                    or "timeout" in str(e).lower()
                )
                last_error_code = "timeout" if is_timeout else "api_error"
                logger.warning("LLM 调用失败(第%s次): %s", attempt + 1, last_err)
                # 新增：异常也保留尝试记录，避免预算台账把未知 usage 当作 0。
                attempt_details.append({
                    "attempt": attempt + 1,
                    "elapsed": round(time.time() - t0, 2),
                    "usage_available": False,
                    "error": last_err,
                })
                if is_timeout and attempt >= min(retries, 2):
                    break
                if attempt < retries:
                    time.sleep(min(2 ** attempt, 6))
        return LLMResult(
            ok=False,
            error=last_err,
            error_code=last_error_code,
            meta=_finalize_meta(last_meta, attempt_details),
        )

    # ---------- 工单1：翻译稳定性支撑 ----------
    def _translation_scope(self, direction: str) -> str:
        """缓存 scope：方向 + 模型 + 提示词版本，任一变化即换 key。"""
        return (
            f"{direction}|model={self.model}"
            f"|prompt={TRANSLATION_PROMPT_VERSION}"
            f"|temp={TRANSLATION_TEMPERATURE}"
        )

    def _load_translation_cache(self, source_text: str,
                                direction: str) -> Optional[LLMResult]:
        """命中翻译缓存时返回带 cache_hit 标记的结果。"""
        if not TRANSLATION_CACHE_ENABLED or not source_text:
            return None
        try:
            payload = result_cache.get_translate(
                source_text, direction, self._translation_scope(direction))
        except Exception:  # pragma: no cover - 缓存故障不影响翻译
            return None
        if not isinstance(payload, dict):
            return None
        text = payload.get("text") or ""
        if not payload.get("ok") or not str(text).strip():
            return None
        previous_meta = payload.get("meta")
        meta = {"cache_hit": True, "translate_cache": "hit"}
        if isinstance(previous_meta, dict):
            # 保留首次调用时的用量，便于成本台账区分“本次未调用模型”。
            meta["cached_meta"] = previous_meta
        return LLMResult(ok=True, text=str(text), meta=meta)

    def _store_translation_cache(self, source_text: str, direction: str,
                                 result: LLMResult) -> None:
        """只缓存成功且非空的结果；失败结果下一次仍会重新请求。"""
        if not TRANSLATION_CACHE_ENABLED or not isinstance(result, LLMResult):
            return
        if not result.ok or not str(result.text or "").strip():
            return
        try:
            result_cache.put_translate(
                source_text,
                {
                    "ok": True,
                    "text": result.text,
                    "error": "",
                    "error_code": result.error_code,
                    "meta": result.meta,
                },
                direction,
                self._translation_scope(direction),
            )
        except Exception:  # pragma: no cover
            pass

    def _translation_chat(self, messages, *, system: Optional[str] = None,
                          max_tokens: Optional[int] = None,
                          retries: int = 1) -> LLMResult:
        """工单1：强制 temperature=0 的翻译调用，可选用多轮投票。

        - 单轮（默认）：一次低温调用，配合持久化缓存保证可复现；
        - 多轮（MATH_AGENT_TRANSLATE_VOTE=N，N>=2）：串行调用 N 次，
          取出现次数最多的正文（并列取最早出现者），提升抗抖动能力。
        """
        rounds = max(1, min(5, int(TRANSLATION_VOTE_ROUNDS)))
        if rounds == 1:
            return self.chat(
                messages,
                system=system,
                temperature=TRANSLATION_TEMPERATURE,
                retries=retries,
                max_tokens=max_tokens,
            )

        collected: list[LLMResult] = []
        for _ in range(rounds):
            collected.append(self.chat(
                messages,
                system=system,
                temperature=TRANSLATION_TEMPERATURE,
                retries=retries,
                max_tokens=max_tokens,
            ))
        successes = [
            item for item in collected
            if item.ok and str(item.text or "").strip()
        ]
        if not successes:
            last = collected[-1]
            return LLMResult(
                ok=False,
                error=last.error or "翻译全部失败",
                error_code=last.error_code,
                meta={"vote_rounds": rounds,
                      "vote_failures": len(collected)},
            )
        counter: dict[str, int] = {}
        first_seen: dict[str, LLMResult] = {}
        for item in successes:
            key = " ".join(str(item.text).split())
            counter[key] = counter.get(key, 0) + 1
            first_seen.setdefault(key, item)
        best_key = max(
            counter,
            key=lambda k: (counter[k], -min(
                i for i, item in enumerate(successes)
                if " ".join(str(item.text).split()) == k)),
        )
        winner = first_seen[best_key]
        meta = dict(winner.meta)
        meta.update({
            "vote_rounds": rounds,
            "vote_successes": len(successes),
            "vote_agreement": counter[best_key],
            "vote_consistent": counter[best_key] == len(successes),
        })
        return LLMResult(
            ok=True, text=winner.text, meta=meta,
            error_code=winner.error_code,
        )

    # ---------- 业务封装 ----------
    def translate_cpp_to_math(self, code: str, language: str = "cpp") -> LLMResult:
        """入口包装：先查持久化缓存，未命中再走原有翻译流程。"""
        source_text = (code or "")[:20000]
        cached = self._load_translation_cache(source_text, "cpp2math")
        if cached is not None:
            return cached
        result = self._translate_cpp_to_math_uncached(
            source_text, language=language)
        self._store_translation_cache(source_text, "cpp2math", result)
        return result

    def _ensure_variable_alignment(self, code: str, result: LLMResult,
                                   build_prompt) -> LLMResult:
        """新增（w10）：译文与代码标识符完全对不上时，定向重试一次。"""
        if not result.ok:
            return result
        text = strip_code_fence(result.text or "")
        if not text.strip():
            return result
        identifiers = _code_identifiers(code)
        if not identifiers:
            return result
        # 只在「代码里有单字母变量，但译文一个都没用到」时重试：
        # 这能精确抓住 w→x 之类的改名，又不会对纯注释/无变量代码乱触发。
        code_vars = {name for name in identifiers if len(name) == 1}
        if not code_vars:
            return result
        text_vars = {m.group(0).lower() for m in
                     re.finditer(r"(?<![A-Za-z\\])[A-Za-z](?![A-Za-z])", text)}
        if {name.lower() for name in code_vars} & text_vars:
            return result
        if result.meta.get("variable_alignment_retry"):
            return result
        hint = "、".join(sorted(identifiers)[:12])
        retry_prompt = (
            build_prompt(code)
            + f"\n\n注意：上一次转写没有使用代码里的变量名。"
              f"这次必须沿用代码中的标识符（例如 {hint}），"
              f"不要把它们改写成教科书记号。"
        )
        retry = self._translation_chat(
            [{"role": "user", "content": retry_prompt}],
            system=SYSTEM_TRANSLATOR,
            retries=1,
            max_tokens=TRANSLATION_MAX_TOKENS,
        )
        if not retry.ok or not strip_code_fence(retry.text or "").strip():
            return result
        retry_meta = dict(retry.meta or {})
        retry_meta["variable_alignment_retry"] = True
        retry_meta["variable_alignment_previous"] = text[:200]
        return _normalize_translated_result(
            LLMResult(ok=True, text=retry.text, meta=retry_meta))

    def _translate_cpp_to_math_uncached(self, code: str,
                                        language: str = "cpp") -> LLMResult:
        code = code[:20000]
        def build_prompt(segment_code: str) -> str:
            return (
                "请提取下面代码实现的核心数学表达式，用 LaTeX 表示。\n"
                "要求：\n"
                "1. 只输出 LaTeX 公式本体（可多行，每行一个），不要解释；\n"
                "2. 若代码实现的是矩阵/向量运算，用矩阵/向量形式写出；\n"
                "3. 若代码含 bug（如行列转置错误），仍如实转写其当前实际逻辑。\n\n"
                f"```{language}\n{segment_code}\n```"
            )

        prompt = build_prompt(code)
        # 修改：每图最多 2 次，并给 reasoning 与最终 C++ 正文留足预算。
        # 工单1：统一走 _translation_chat，强制 temperature=0（可投票）。
        result = self._translation_chat(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_TRANSLATOR,
            retries=1,
            max_tokens=TRANSLATION_MAX_TOKENS,
        )
        # 新增（w10）：变量对齐校验与定向重试。
        # 实测同一段代码在 temperature=0 下仍可能把 w 改写成 x、或只回一句
        # "z = xy"，导致与图片公式完全对不上。这里做一次确定性检查：
        # 若译文里一个代码标识符都没出现，就带着"必须使用这些变量名"再问一次。
        result = self._ensure_variable_alignment(code, result, build_prompt)
        # 新增：围栏内无正文时不再视为翻译成功。
        if result.ok and not strip_code_fence(result.text):
            return LLMResult(
                ok=False,
                error="翻译结果为空",
                error_code="empty_translation",
                meta=result.meta,
            )

        finish_reason = result.meta.get("finish_reason")
        truncated = (
            finish_reason == "length"
            or (not result.ok and result.error_code == "empty_content")
        )
        if not truncated:
            return _normalize_translated_result(result)  # 新增：成功路径归一化。

        # 修改：截断后先提高预算重试一次，并明确要求直接输出最终 LaTeX。
        retry_prompt = (
            prompt
            + "\n\n请直接输出最终 LaTeX，不要输出推理过程，务必在预算内完成"
        )
        retry_result = self._translation_chat(
            [{"role": "user", "content": retry_prompt}],
            system=SYSTEM_TRANSLATOR,
            retries=0,
            max_tokens=TRANSLATION_RETRY_MAX_TOKENS,
        )
        if retry_result.ok and strip_code_fence(retry_result.text):
            retry_meta = dict(retry_result.meta)
            retry_meta["recovered_from_truncation"] = True
            return _normalize_translated_result(  # 新增：重试成功路径归一化。
                LLMResult(
                    ok=True,
                    text=retry_result.text,
                    meta=retry_meta,
                )
            )

        # 修改：仍失败时按代码块翻译，合并非空、去重且去掉空行。
        segments = _split_code_for_translation(
            code, TRANSLATION_SEGMENT_LINES, TRANSLATION_MAX_SEGMENTS)
        merged_lines = []
        seen_lines = set()
        segment_details = []
        successful_segments = 0
        for index, segment_code in enumerate(segments, start=1):
            segment_prompt = (
                build_prompt(segment_code)
                + "\n\n请直接输出最终 LaTeX，不要输出推理过程，务必在预算内完成"
            )
            segment_result = self._translation_chat(
                [{"role": "user", "content": segment_prompt}],
                system=SYSTEM_TRANSLATOR,
                retries=0,
                max_tokens=TRANSLATION_MAX_TOKENS,
            )
            segment_text = ""
            if segment_result.ok:
                segment_text = strip_code_fence(segment_result.text)
            if segment_text:
                successful_segments += 1
                for line in segment_text.splitlines():
                    normalized_line = line.strip()
                    if not normalized_line or normalized_line in seen_lines:
                        continue
                    seen_lines.add(normalized_line)
                    merged_lines.append(normalized_line)
            segment_details.append({
                "index": index,
                "ok": bool(segment_text),
                "finish_reason": segment_result.meta.get("finish_reason"),
                "error_code": segment_result.error_code,
                "error": segment_result.error,
            })

        merged_text = "\n".join(merged_lines)
        recovery_meta = {
            "segmented": True,
            "segments": len(segments),
            "segment_successes": successful_segments,
            "recovered_from_truncation": False,
            "initial_finish_reason": finish_reason,
            "retry_finish_reason": retry_result.meta.get("finish_reason"),
            "retry_error_code": retry_result.error_code,
            "segment_details": segment_details,
        }
        if not merged_text:
            return LLMResult(
                ok=False,
                error="长代码分段翻译后仍为空",
                error_code="length_truncated",
                meta=recovery_meta,
            )
        recovery_meta["recovered_from_truncation"] = True
        return _normalize_translated_result(  # 新增：分段合并成功路径归一化。
            LLMResult(
                ok=True,
                text=merged_text,
                meta=recovery_meta,
            )
        )

    def translate_math_to_cpp(self, latex: str) -> LLMResult:
        """入口包装：先查持久化缓存，未命中再走原有翻译流程。"""
        source_text = latex or ""
        cached = self._load_translation_cache(source_text, "math2cpp")
        if cached is not None:
            return cached
        result = self._translate_math_to_cpp_uncached(source_text)
        self._store_translation_cache(source_text, "math2cpp", result)
        return result

    def _translate_math_to_cpp_uncached(self, latex: str) -> LLMResult:
        prompt = (
            "请把下面的数学公式实现为简洁的 C++ 函数（可用 <cmath> 与 std::vector）。\n"
            "只输出 C++ 代码，不要解释。\n\n"
            f"公式：\n{latex}"
        )
        # 修改：每图最多 2 次，并给 reasoning 与最终 C++ 正文留足预算。
        # 工单1：统一走 _translation_chat，强制 temperature=0（可投票）。
        result = self._translation_chat(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_TRANSLATOR,
            retries=1,
            max_tokens=TRANSLATION_MAX_TOKENS,
        )
        # 新增：围栏内无正文时不再视为翻译成功。
        if result.ok and not strip_code_fence(result.text):
            return LLMResult(
                ok=False,
                error="翻译结果为空",
                error_code="empty_translation",
                meta=result.meta,
            )
        return result

    def normalize_latex(self, latex: str) -> LLMResult:
        prompt = (
            "修复下面的 LaTeX 使其可被标准解析器解析；保持数学含义不变；"
            "只输出修复后的 LaTeX 本体。\n\n" + latex
        )
        return self.chat([{"role": "user", "content": prompt}], system=SYSTEM_NORMALIZER)

    def explain(self, latex: str, style: str = "面试") -> LLMResult:
        prompt = (
            f"请用{style}场景下合适的语言解释下面公式的含义、关键性质与常见坑点，"
            "控制在 200 字内。\n\n" + latex
        )
        return self.chat([{"role": "user", "content": prompt}], system=SYSTEM_TRANSLATOR)


def strip_code_fence(text: str) -> str:
    """去掉 ```lang ... ``` 围栏。"""
    if not text:
        return ""
    t = text.strip()
    m = re.match(r"^```[A-Za-z0-9_+-]*\s*\n?(.*?)\n?```$", t, re.DOTALL)
    return m.group(1).strip() if m else t


def compact_cpp_context(code: str, *, max_lines: int = 160,
                        max_chars: int = 6000) -> str:
    """新增：清洗并裁剪 C++ 上下文，优先保留控制流和计算行。"""
    if not isinstance(code, str) or not code:
        return ""

    try:
        line_limit = max(0, int(max_lines))
    except (TypeError, ValueError):
        line_limit = 160
    try:
        char_limit = max(0, int(max_chars))
    except (TypeError, ValueError):
        char_limit = 6000
    if line_limit == 0 or char_limit == 0:
        return ""

    # 新增：先删除跨行块注释，再逐行删除普通行注释。
    without_blocks = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    cleaned_lines = []
    for raw_line in without_blocks.splitlines():
        line = raw_line
        search_start = 0
        while True:
            comment_index = line.find("//", search_start)
            if comment_index < 0:
                break
            previous_char = next(
                (char for char in reversed(line[:comment_index])
                 if not char.isspace()),
                "",
            )
            if previous_char == ":":
                search_start = comment_index + 1
                continue
            line = line[:comment_index]
            break
        line = line.rstrip()
        if line.strip():
            cleaned_lines.append(line)

    if not cleaned_lines:
        return ""
    if len(cleaned_lines) <= line_limit and len(
            "\n".join(cleaned_lines)) <= char_limit:
        return "\n".join(cleaned_lines)

    # 新增：高优先级行优先占用预算，最终仍按原顺序输出。
    ranked_lines = []
    for index, line in enumerate(cleaned_lines):
        stripped = line.strip()
        is_declaration = bool(re.match(
            r"^(?:(?:const|static|volatile|unsigned|signed)\s+)*"
            r"(?:[\w:<>]+(?:\s*[*&])?\s+)+[\w:]+"
            r"(?:\[[^\]]*\])?\s*;$",
            stripped,
        )) and "=" not in stripped and not re.match(
            r"^(?:return|throw|break|continue|delete)\b", stripped)
        is_print = bool(re.search(
            r"\b(?:std::)?(?:cout|cerr|clog)\b|"
            r"\b(?:printf|puts)\s*\(",
            stripped,
        ))
        if is_declaration or is_print:
            priority = 0
        elif (
            re.search(r"\b(?:for|while|if|return)\b", stripped)
            or "=" in stripped
            or ("(" in stripped and ")" in stripped)
        ):
            priority = 2
        else:
            priority = 1
        ranked_lines.append((-priority, index, line))

    marker = "// ...[context trimmed]"
    available_lines = max(0, line_limit - 1)
    content_char_limit = max(0, char_limit - len(marker) - 1)
    selected_indexes = []
    for negative_priority, index, line in sorted(ranked_lines):
        if len(selected_indexes) >= available_lines:
            break
        if negative_priority == 0:
            continue
        candidate_lines = [
            cleaned_lines[item] for item in selected_indexes + [index]
        ]
        if len("\n".join(candidate_lines)) <= content_char_limit:
            selected_indexes.append(index)

    selected_text = "\n".join(
        cleaned_lines[index] for index in sorted(selected_indexes))
    if selected_text:
        return f"{selected_text}\n{marker}"
    return marker[:char_limit]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    r = TextLLM().translate_cpp_to_math("float y = a*x + b;")
    print("ok:", r.ok, "| text:", r.text[:200], "| err:", r.error)
