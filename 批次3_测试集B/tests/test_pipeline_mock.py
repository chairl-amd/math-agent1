#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_pipeline_mock.py — 链路A（视觉）代码路径验证（不依赖真实 API）。

用 mock 替换 VisionOcr.recognize：
1. 视觉成功 -> Pipeline 走 vision 链路并完成校验；
2. 视觉失败 -> 自动回退 Pix2Text 并记录 engine_chain。
"""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "agent"))

from ocr_vision import OcrResult  # noqa: E402
from main import Pipeline  # noqa: E402

PASS, FAIL = [], []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")

def fake_ok(self, src, **kw):
    return OcrResult(latex=r"\sin^{2}\theta+\cos^{2}\theta=1", ok=True,
                     engine="vision", meta={"engine_chain": "vision", "mocked": True})

def fake_fail(self, src, **kw):
    return OcrResult(ok=False, engine="vision", error="mocked timeout")

def main() -> int:
    img = str(BASE / "tests" / "formula" / "formula_03.png")

    # 1) 视觉成功链路
    with patch("ocr_vision.VisionOcr.recognize", fake_ok):
        pipe = Pipeline(engine="vision", use_llm=False)
        rep = pipe.run(image=img, expect="sin(theta)**2+cos(theta)**2-1")
    check("链路A 视觉成功 -> 校验通过", rep["ok"] is True,
          f"status={rep['steps']['checks'][0]['status'] if rep['steps'].get('checks') else 'NA'}")
    check("链路A 记录 engine_chain=vision",
          rep["steps"]["ocr"]["meta"].get("engine_chain") == "vision")

    # 2) 视觉失败 -> 回退 Pix2Text
    with patch("ocr_vision.VisionOcr.recognize", fake_fail):
        pipe = Pipeline(engine="auto", use_llm=False)
        rep = pipe.run(image=img, expect="sin(theta)**2+cos(theta)**2-1")
    chain = rep["steps"]["ocr"]["meta"].get("engine_chain", "")
    check("视觉失败 -> 回退 Pix2Text", "pix2text" in chain, f"chain={chain}")
    # 修改：Pix2Text 未安装时回退必然失败，此时只校验“链路被尝试”，
    # 不把缺少本地依赖误判成流水线缺陷。
    pix_available = importlib.util.find_spec("pix2text") is not None
    if pix_available:
        check("回退链路命中（OCR 成功）", rep["steps"]["ocr"]["ok"] is True,
              f"latex={rep['steps']['ocr'].get('latex', '')[:40]}")
    else:
        print("[SKIP] 回退链路命中（OCR 成功）：当前环境未安装 Pix2Text")

    print(f"\n== 通过 {len(PASS)} / 失败 {len(FAIL)} ==")
    return 0 if not FAIL else 1

if __name__ == "__main__":
    raise SystemExit(main())