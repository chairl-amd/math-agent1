# -*- coding: utf-8 -*-
"""agent/ocr_vision.py — DeepSeek 视觉 API 封装（公式图片 -> LaTeX）。

职责：
- encode_image_base64(): 图片预处理 + base64 data URI（控制体积、降 token）；
- VisionOcr.recognize(): 调用 OpenAI 兼容接口，返回结构化 OcrResult；
- clean_latex(): 清理 markdown 围栏 / $$ 包裹 / 多余说明。
"""
from __future__ import annotations

import base64
import io
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

try:  # 兼容包内导入与脚本直跑
    from . import config
    # 修改：新增导入图注清洗与置信度估算纯函数。
    from .ocr_pix import (
        OcrResult,
        audit_sum_bounds,
        build_ocr_candidates,
        detect_caption,
        estimate_ocr_confidence,
        preprocess_image,
        strip_caption_lines,
        to_image,
    )
except ImportError:  # pragma: no cover
    import config  # type: ignore
    # 修改：脚本直跑时同步导入图注清洗与置信度估算纯函数。
    from ocr_pix import (  # type: ignore
        OcrResult,
        audit_sum_bounds,
        build_ocr_candidates,
        detect_caption,
        estimate_ocr_confidence,
        preprocess_image,
        strip_caption_lines,
        to_image,
    )

logger = logging.getLogger(__name__)

VISION_PROMPT = """你是一个数学公式 OCR 引擎。请把图片中的数学公式转写为 LaTeX。
要求：
1. 只输出 LaTeX 公式本身，不输出解释、不要 markdown 围栏；
2. 不要额外添加 $ 或 $$ 包裹；
3. 若图片中没有可识别公式或是普通文字，不要猜测或编造，输出空字符串。
"""

# 新增：仅提高 Vision 返回上限，避免长公式在 1024 token 处被截断。
VISION_MAX_TOKENS = 4096

# 新增：要求视觉模型完整保留上下界，同时禁止对无上下界符号进行臆造。
VISION_PROMPT += """
4. 完整保留 \\sum、\\prod、\\int 的上下界与 \\limits 写法；若图中确实没有画上下界，不要编造，保留 \\sum_{k} 这种形式；矩阵环境、分段(cases)、括号层级必须完整保留。
5. 导数与重音记号必须保留：f'(x)、f''(x)、\\dot{x}、\\bar{x}、\\hat{x}、\\tilde{x}、\\vec{x}。撇号属于公式内容，绝不能省略、也不能把 f'(x) 写成 f(x)。
6. 变量大小写必须与图中一致（x 与 X 是不同符号）；下标里的多指标按图中原样写出，例如 a_{i,i}、a_{ij}。
7. 图中的中文/英文说明文字不要写进公式；只转写公式本体。"""


def encode_image_base64(src, *, max_side: int = 1800, fmt: str = "JPEG",
                        quality: int = 88) -> str:
    """图片 -> data URI；统一缩放以控制上传体积与 token 成本。"""
    img = to_image(src)
    img = preprocess_image(img, min_side=400, max_side=max_side, denoise=False)
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    if fmt.upper() == "JPEG":
        img.save(buf, format="JPEG", quality=quality)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def clean_latex(text: str) -> str:
    """去掉 markdown 围栏、$$/\\[ \\]/$ 包裹与首尾空白。"""
    if not text:
        return ""
    t = text.strip()
    # 新增：识别视觉模型返回的空占位文本，避免被当作 LaTeX 送入校验。
    if t.lower() in {'""', "''", '``', '````', '$', '$$', '无', 'none', 'null', 'empty'}:
        return ""
    t = re.sub(r"^```(?:latex|math)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```$", "", t)
    t = t.strip()
    if len(t) > 4 and t.startswith("$$") and t.endswith("$$"):
        t = t[2:-2]
    elif len(t) > 4 and t.startswith("\\[") and t.endswith("\\]"):
        t = t[2:-2]
    elif len(t) > 2 and t.startswith("$") and t.endswith("$"):
        t = t[1:-1]
    return t.strip()


# 新增：纯中文文本不再被误判为公式。
def looks_like_latex(text: str) -> bool:
    """判断文本是否具备公式特征；中文纯文本不作为 LaTeX 接受。"""
    if not text:
        return False
    latex_features = set("\\^_{}=+-*/<>")
    if any(char in latex_features for char in text):
        return True
    if re.search(r"[\u3000-\u303f\u4e00-\u9fff]", text):
        return False
    return True


class VisionOcr:
    """DeepSeek 视觉识别器（OpenAI 兼容接口）。"""

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None,
                 base_url: Optional[str] = None, timeout: Optional[int] = None,
                 temperature: Optional[float] = None):
        # 修改：模型、密钥和接口地址统一从 config 读取。
        self.model = model or config.VISION_MODEL
        self.api_key = api_key if api_key is not None else config.API_KEY
        self.base_url = base_url or config.BASE_URL
        self.timeout = timeout or config.REQUEST_TIMEOUT
        self.temperature = config.TEMPERATURE if temperature is None else temperature
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

    def recognize(self, src, *, prompt: str = VISION_PROMPT,
                  retries: Optional[int] = None) -> OcrResult:
        """识别单张图片；任何异常都收敛为 ok=False 的 OcrResult。"""
        retries = config.MAX_RETRIES if retries is None else retries
        try:
            client = self._ensure_client()
        except Exception as e:
            # 修改：缺少 Key 等客户端初始化失败统一归类。
            return OcrResult(
                ok=False,
                engine="vision",
                error=str(e),
                error_code="missing_api_key",
            )
        try:
            data_uri = encode_image_base64(src)
        except Exception as e:
            # 修改：编码失败返回结构化错误。
            return OcrResult(
                ok=False,
                engine="vision",
                error=f"图片编码失败: {e}",
                error_code="encode_failed",
            )

        last_err = ""
        last_error_code = ""
        last_meta = {}
        for attempt in range(retries + 1):
            try:
                t0 = time.time()
                resp = client.chat.completions.create(
                    model=self.model,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    }],
                    temperature=self.temperature,
                    max_tokens=VISION_MAX_TOKENS,
                )
                raw = (resp.choices[0].message.content or "").strip()
                latex = clean_latex(raw)
                meta = {"attempt": attempt + 1, "elapsed": round(time.time() - t0, 2),
                        "raw_len": len(raw)}
                usage = getattr(resp, "usage", None)
                if usage is not None:
                    meta["tokens"] = {
                        "prompt": getattr(usage, "prompt_tokens", None),
                        "completion": getattr(usage, "completion_tokens", None),
                    }
                if not latex:
                    # 修改：空正文按原重试策略继续，最终返回 empty_result。
                    last_err = "模型返回空内容"
                    last_error_code = "empty_result"
                    last_meta = meta
                    if attempt < retries:
                        time.sleep(min(2 ** attempt, 5))
                    continue
                # 新增：先检测并清洗图注，再做 LaTeX 形态判断。
                original_latex = latex
                caption_info = detect_caption(original_latex)
                candidate_cleaned = original_latex
                if caption_info["caption_detected"]:
                    stripped_latex, caption_info = strip_caption_lines(
                        original_latex)
                    candidate_cleaned = stripped_latex
                    latex = stripped_latex or original_latex

                if not looks_like_latex(latex):
                    # 修改：非 LaTeX 立即返回，避免无意义重试。
                    return OcrResult(
                        ok=False,
                        engine="vision",
                        error=f"识别结果不是 LaTeX：{latex[:50]}",
                        error_code="not_latex",
                        meta=meta,
                    )
                confidence = estimate_ocr_confidence(
                    original_latex, caption_info)
                meta.update({
                    "caption_detected": caption_info["caption_detected"],
                    "caption_markers": caption_info["markers"],
                    "caption_text": "\n".join(caption_info["caption_lines"]),
                    "confidence": confidence,
                })
                # 新增：成功识别时记录上下界审计与是否有无界求和。
                sum_bounds = audit_sum_bounds(latex)
                meta["sum_bounds"] = sum_bounds
                meta["sum_unbounded"] = bool(sum_bounds["unbounded_sums"])
                if confidence < 0.6:
                    meta["candidates"] = build_ocr_candidates(
                        candidate_cleaned, original_latex)
                return OcrResult(latex=latex, ok=True, engine="vision", meta=meta)
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                # 修改：区分超时与其他 API 异常。
                is_timeout = (
                    "timeout" in type(e).__name__.lower()
                    or "timeout" in str(e).lower()
                )
                last_error_code = "timeout" if is_timeout else "api_error"
                logger.warning("视觉识别失败(第%s次): %s", attempt + 1, last_err)
                if is_timeout and attempt >= min(retries, 2):
                    break
                if attempt < retries:
                    time.sleep(min(2 ** attempt, 5))
        return OcrResult(
            ok=False,
            engine="vision",
            error=last_err,
            error_code=last_error_code,
            meta=last_meta,
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    if len(sys.argv) > 1:
        r = VisionOcr().recognize(sys.argv[1])
        print("ok:", r.ok, "| latex:", r.latex, "| error:", r.error, "| meta:", r.meta)
    else:
        print("usage: python ocr_vision.py <image>")
