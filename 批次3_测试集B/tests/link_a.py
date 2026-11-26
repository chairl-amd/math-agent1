#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/link_a.py — 链路A（DeepSeek 视觉）批量跑 10 组公式，产出 output/vision_latex.json。

用法（需先 export DEEPSEEK_API_KEY）：
    python tests/link_a.py
"""
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "agent"))

from ocr_vision import VisionOcr  # noqa: E402
from verify import verify_latex_vs_code  # noqa: E402

CASES = [
    ("formula_01.png", "E = mc^2",                 None),
    ("formula_02.png", "定积分 int_0^1 x^2 = 1/3",  None),
    ("formula_03.png", "sin^2+cos^2=1",            "sin(theta)**2 + cos(theta)**2 - 1"),
    ("formula_04.png", "(a+b)/(c-d)",              "(a+b)/(c-d)"),
    ("formula_05.png", "sqrt(x^2+y^2)",            "sqrt(x**2+y**2)"),
    ("formula_06.png", "sum i = n(n+1)/2",         None),
    ("formula_07.png", "d/dx e^x = e^x",           None),
    ("formula_08.png", "a^2+b^2=c^2",              "a**2 + b**2 - c**2"),
    ("formula_09.png", "log(xy)=logx+logy",        "log(x*y) - (log(x)+log(y))"),
    ("formula_10.png", "int x dx = x^2/2 + C",     None),
]


def main() -> int:
    vo = VisionOcr()
    if not (vo.api_key or "").strip():
        print("ERROR: DEEPSEEK_API_KEY 未设置", file=sys.stderr)
        return 2
    rows = []
    t0 = time.time()
    for f, desc, expect in CASES:
        path = BASE / "tests" / "formula" / f
        r = vo.recognize(str(path))
        row = {"file": f, "desc": desc, "ok": r.ok, "latex": r.latex,
               "error": r.error, "meta": r.meta, "expect": expect}
        if r.ok and expect:
            chk = verify_latex_vs_code(r.latex, expect)
            row.update({"check_status": chk.status, "check_ok": chk.ok,
                        "detail": chk.detail[:150]})
        rows.append(row)
        print(f"[{f}] ok={r.ok} latex={r.latex!r} "
              f"check={row.get('check_status', 'SKIP')}", flush=True)
    out = BASE / "output" / "vision_latex.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    ok_n = sum(1 for r in rows if r["ok"])
    chk_rows = [r for r in rows if r.get("check_ok") is not None]
    pass_n = sum(1 for r in chk_rows if r.get("check_ok"))
    print(f"\nOCR 成功 {ok_n}/{len(rows)}，可校验 {len(chk_rows)} 组通过 {pass_n} 组，"
          f"耗时 {round(time.time()-t0,1)}s")
    print(f"已保存 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())