# -*- coding: utf-8 -*-
"""agent/ocr_pix.py — Pix2Text 本地 OCR 封装（手写公式图片 -> LaTeX）。

分层设计：
- preprocess_image(): 轻量图像预处理（去白边/缩放/去噪），提升手写公式识别率；
- PixOcr 类：懒加载模型，单实例复用；
- 降级：模型不可用 -> OcrUnavailableError；单图失败 -> 重试后返回 ok=False，不抛异常。
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps, ImageFilter

logger = logging.getLogger(__name__)

# 新增：图注标记统一定义在这里，供检测、清洗和置信度估算共同使用。
_CAPTION_MARKER_PATTERN = re.compile(
    r"\bFigure\b|\bFig\.|图\s*\d|图注|注[:：]|"
    r"\bTable\b|表\s*\d|\bSource\s*:|\bCaption\b",
    flags=re.IGNORECASE,
)

try:
    from pix2text import Pix2Text as _Pix2Text
    _IMPORT_ERROR = ""
except Exception as _e:  # 未安装或依赖损坏
    _Pix2Text = None
    _IMPORT_ERROR = f"{type(_e).__name__}: {_e}"


class OcrUnavailableError(RuntimeError):
    """Pix2Text 不可用（未安装 / 模型加载失败）。"""


@dataclass
class OcrResult:
    latex: str = ""
    ok: bool = False
    engine: str = "pix2text"
    error: str = ""
    meta: dict = field(default_factory=dict)
    # 新增：统一结构化错误码，放在末尾以保持位置参数兼容。
    error_code: str = ""


# 新增：按行识别图注，返回布尔值、图注行和实际命中的标记。
def detect_caption(text) -> dict:
    """检测 OCR 文本中的图注；纯函数，不依赖 OCR 模型。"""
    if not isinstance(text, str) or not text:
        return {
            "caption_detected": False,
            "caption_lines": [],
            "markers": [],
        }

    caption_lines = []
    markers = []
    seen_markers = set()
    for line in text.splitlines():
        line_markers = [match.group(0) for match in
                        _CAPTION_MARKER_PATTERN.finditer(line)]
        if not line_markers:
            continue
        caption_lines.append(line.strip())
        for marker in line_markers:
            marker_key = marker.casefold()
            if marker_key not in seen_markers:
                seen_markers.add(marker_key)
                markers.append(marker)

    return {
        "caption_detected": bool(caption_lines),
        "caption_lines": caption_lines,
        "markers": markers,
    }


# 新增：删除命中图注标记的整行，保留其余行及原有先后顺序。
def strip_caption_lines(text) -> tuple[str, dict]:
    """删除图注行，并返回清洗文本与 detect_caption 检测结果。"""
    if not isinstance(text, str) or not text:
        return "", detect_caption("")

    caption_info = detect_caption(text)
    kept_lines = [
        line for line in text.splitlines()
        if _CAPTION_MARKER_PATTERN.search(line) is None
    ]
    return "\n".join(kept_lines), caption_info


# 新增：根据图注、LaTeX 噪声和结构完整度估算 OCR 置信度。
def estimate_ocr_confidence(text, caption_info=None) -> float:
    """返回 0.05 到 1 之间的启发式置信度，不调用任何模型。"""
    value = text if isinstance(text, str) else ""
    info = detect_caption(value) if caption_info is None else caption_info

    confidence = 0.9
    if isinstance(info, dict) and info.get("caption_detected"):
        confidence -= 0.45
    if re.search(r"\\text\s*\{", value):
        confidence -= 0.15

    effective_text = re.sub(r"\s+", "", value)
    if len(effective_text) < 3:
        confidence -= 0.3

    pairs = (("{", "}"), ("(", ")"), ("[", "]"))
    if any(value.count(left) != value.count(right)
           for left, right in pairs):
        confidence -= 0.2

    return round(max(0.05, min(1.0, confidence)), 2)


# 新增：审计求和/求积符号是否保留上下界，供 OCR 结果追踪。
def _consume_audit_bound_argument(latex: str, start: int) -> int:
    """读取一个 LaTeX 上下界参数，返回参数结束后的位置。"""
    index = start
    while index < len(latex) and latex[index].isspace():
        index += 1
    if index >= len(latex):
        return index
    if latex[index] == "{":
        depth = 0
        for cursor in range(index, len(latex)):
            if latex[cursor] == "{":
                depth += 1
            elif latex[cursor] == "}":
                depth -= 1
                if depth == 0:
                    return cursor + 1
        return len(latex)
    command = re.match(r"\\[A-Za-z]+|\\.", latex[index:])
    if command:
        return index + len(command.group(0))
    return min(index + 1, len(latex))


def audit_sum_bounds(latex) -> dict:
    """统计求和/求积符号，并区分有上下界与完全无界的原始片段。"""
    text = str(latex or "")
    operator_pattern = re.compile(
        r"\\(?P<operator>sum|prod)(?![A-Za-z])"
        r"(?:\s*\\(?:limits|nolimits)(?![A-Za-z]))?"
    )
    sum_count = 0
    bounded_sums = []
    lower_only_sums = []
    bare_sums = []
    for match in operator_pattern.finditer(text):
        if match.group("operator").lower() == "sum":
            sum_count += 1
        fragment_end = match.end()
        lower_value = None
        has_upper_bound = False
        for marker in ("_", "^"):
            cursor = fragment_end
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if cursor < len(text) and text[cursor] == marker:
                fragment_end = _consume_audit_bound_argument(text, cursor + 1)
                bound_value = text[cursor + 1:fragment_end].strip()
                if marker == "_":
                    lower_value = bound_value
                else:
                    has_upper_bound = True
        fragment = text[match.start():fragment_end].strip()
        normalized_lower = str(lower_value or "").strip()
        if (
            len(normalized_lower) >= 2
            and normalized_lower.startswith("{")
            and normalized_lower.endswith("}")
        ):
            normalized_lower = normalized_lower[1:-1].strip()
        has_relation = re.match(
            r"^[A-Za-z]\s*(?:=|<=?|>=?|<|>|\\le|\\leq|\\lt|\\ge|\\geq|\\gt)\s*\S",
            normalized_lower,
        ) is not None
        if has_upper_bound or has_relation:
            bounded_sums.append(fragment)
        elif lower_value is not None:
            lower_only_sums.append(fragment)
        else:
            bare_sums.append(fragment)
    unbounded_sums = lower_only_sums + bare_sums
    return {
        "sum_count": sum_count,
        "unbounded_sums": unbounded_sums,
        "bounded_sums": bounded_sums,
        "lower_only_sums": lower_only_sums,
        "bare_sums": bare_sums,
        "audit_ok": not bool(unbounded_sums),
    }


# 新增：生成低置信度 OCR 候选，优先使用清洗后的文本。
def build_ocr_candidates(cleaned, original, limit=3) -> list[str]:
    """去空、去重并限制数量；清洗文本始终排在原始文本之前。"""
    try:
        safe_limit = max(0, int(limit))
    except (TypeError, ValueError):
        safe_limit = 0
    if safe_limit == 0:
        return []

    candidates = []
    seen = set()
    for value in (cleaned, original):
        if not isinstance(value, str):
            continue
        candidate = value.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)
        if len(candidates) >= safe_limit:
            break
    return candidates


def to_image(src) -> Image.Image:
    """把 路径/bytes/PIL.Image 统一成 RGB 图像。"""
    if isinstance(src, Image.Image):
        return src.convert("RGB")
    if isinstance(src, (bytes, bytearray)):
        return Image.open(io.BytesIO(src)).convert("RGB")
    return Image.open(src).convert("RGB")


def preprocess_image(img, *, min_side: int = 800, max_side: int = 2200,
                     denoise: bool = True) -> Image.Image:
    """手写公式预处理：去白边 -> 缩放 -> 轻度去噪。"""
    img = to_image(img)
    # 1) 裁掉大片白边（以非白像素的包围盒为准）
    gray = img.convert("L")
    bbox = ImageOps.invert(gray).getbbox()
    if bbox:
        pad = 12
        l, t, r, b = bbox
        l, t = max(0, l - pad), max(0, t - pad)
        r, b = min(img.width, r + pad), min(img.height, b + pad)
        img = img.crop((l, t, r, b))
    # 2) 缩放到合理边长
    w, h = img.size
    side = max(w, h)
    scale = 1.0
    if side and side < min_side:
        scale = min_side / side
    elif side > max_side:
        scale = max_side / side
    if abs(scale - 1.0) > 0.05:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    # 3) 轻度中值去噪，保住细笔画
    if denoise:
        img = img.filter(ImageFilter.MedianFilter(3))
    return img


class PixOcr:
    """Pix2Text 封装（懒加载，单例复用模型）。"""

    def __init__(self, device: Optional[str] = None):
        self._p2t = None
        self._device = device
        self._load_error = ""

    def _ensure_model(self):
        if self._p2t is not None:
            return self._p2t
        if _Pix2Text is None:
            raise OcrUnavailableError(f"pix2text 未安装: {_IMPORT_ERROR}")
        try:
            kwargs = {}
            if self._device:
                kwargs["device"] = self._device
            self._p2t = _Pix2Text.from_config(**kwargs)
            return self._p2t
        except Exception as e:
            self._load_error = f"{type(e).__name__}: {e}"
            raise OcrUnavailableError(f"Pix2Text 模型加载失败: {self._load_error}") from e

    def available(self) -> bool:
        try:
            self._ensure_model()
            return True
        except OcrUnavailableError:
            return False

    def recognize(self, src, *, retries: int = 1, preprocess: bool = True) -> OcrResult:
        """识别公式图片为 LaTeX；失败返回 ok=False，不抛异常。"""
        try:
            p2t = self._ensure_model()
        except OcrUnavailableError as e:
            # 修改：模型缺失或加载失败使用统一错误码。
            return OcrResult(ok=False, error=str(e),
                             error_code="model_unavailable")
        try:
            img = to_image(src)
        except Exception as e:
            # 修改：图片读取失败不能穿透到调用方。
            return OcrResult(
                ok=False,
                error=f"图片读取失败: {type(e).__name__}: {e}",
                error_code="image_unreadable",
            )
        last_err = ""
        last_error_code = "model_error"
        use_pre = preprocess
        for attempt in range(retries + 1):
            try:
                use = preprocess_image(img) if use_pre else img
                latex = p2t.recognize_formula(use)
                latex = (latex or "").strip()
                if latex.startswith("$$") and latex.endswith("$$"):
                    latex = latex[2:-2].strip()
                if not latex:
                    # 修改：空结果保留可回退提示。
                    last_err = "Pix2Text 未识别到公式（已降级：可回退 Vision）"
                    last_error_code = "empty_result"
                    use_pre = False  # 换一条路：不预处理再试一次
                    continue
                # 新增：成功结果统一检测图注、清洗并补充置信度元数据。
                original_latex = latex
                caption_info = detect_caption(original_latex)
                candidate_cleaned = original_latex
                if caption_info["caption_detected"]:
                    stripped_latex, caption_info = strip_caption_lines(
                        original_latex)
                    candidate_cleaned = stripped_latex
                    latex = stripped_latex or original_latex

                confidence = estimate_ocr_confidence(
                    original_latex, caption_info)
                meta = {
                    "attempt": attempt + 1,
                    "preprocess": use_pre,
                    "caption_detected": caption_info["caption_detected"],
                    "caption_markers": caption_info["markers"],
                    "caption_text": "\n".join(caption_info["caption_lines"]),
                    "confidence": confidence,
                }
                # 新增：成功识别时记录求和/求积上下界审计结果。
                sum_bounds = audit_sum_bounds(latex)
                meta["sum_bounds"] = sum_bounds
                meta["sum_unbounded"] = bool(sum_bounds["unbounded_sums"])
                if confidence < 0.6:
                    meta["candidates"] = build_ocr_candidates(
                        candidate_cleaned, original_latex)
                return OcrResult(latex=latex, ok=True, meta=meta)
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                last_error_code = "model_error"
                logger.warning("Pix2Text 识别失败(第%s次): %s", attempt + 1, last_err)
                use_pre = False
        return OcrResult(ok=False, error=last_err,
                         error_code=last_error_code)


def recognize_with_fallback(src, vision_recognizer=None) -> OcrResult:
    """降级编排：本地 Pix2Text 优先，失败且给了视觉识别器时回退。

    vision_recognizer: 可调用对象 (src) -> OcrResult 或 str。
    """
    res = PixOcr().recognize(src)
    if res.ok:
        return res
    if vision_recognizer is None:
        return res
    try:
        vres = vision_recognizer(src)
        if isinstance(vres, OcrResult):
            if vres.ok:
                vres.meta["fallback_from"] = f"pix2text: {res.error}"
            return vres
        text = (vres or "").strip()
        if text:
            return OcrResult(latex=text, ok=True, engine="vision",
                             meta={"fallback_from": f"pix2text: {res.error}"})
        return res
    except Exception as e:
        res.error += f" | vision fallback 失败: {type(e).__name__}: {e}"
        return res


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ocr = PixOcr()
    print("pix2text available:", ocr.available())
    import sys
    if len(sys.argv) > 1:
        r = ocr.recognize(sys.argv[1])
        print("ok:", r.ok, "latex:", r.latex, "error:", r.error)
