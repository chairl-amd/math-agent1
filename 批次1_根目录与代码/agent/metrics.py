#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/metrics.py — OCR 双链路对比评估指标（工单22）。

指标定义：
1. latex_exact_match     两链路 LaTeX 字符串完全一致（严格）；
2. latex_similarity      字符级相似度（difflib，0~1），衡量表面一致性；
3. ocr_success_rate      链路产出非空 LaTeX 的比例；
4. verify_pass_rate      可校验用例中校验通过的比例；
5. chain_consistency     两链路对同一图片的校验结论是否一致；
6. normalized_match      规范化（去空格/统一 \\operatorname）后是否一致。
"""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Optional


def normalize_latex(s: str) -> str:
    """规范化：去多余空白、统一 \\operatorname{x y} -> x y、统一函数名。"""
    if not s:
        return ""
    s = s.strip()
    s = re.sub(r"\\operatorname\{([^}]*)\}", lambda m: m.group(1).replace(" ", ""), s)
    s = re.sub(r"\s+", "", s)
    return s


def latex_similarity(a: str, b: str) -> float:
    """字符级相似度 0~1。"""
    a, b = normalize_latex(a), normalize_latex(b)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def latex_exact_match(a: str, b: str) -> bool:
    return normalize_latex(a) == normalize_latex(b)


def ocr_success_rate(rows: list[dict], key: str = "ok") -> float:
    """OCR 成功率：优先用 ok 字段；缺失时以 latex 非空判定。"""
    if not rows:
        return 0.0

    def good(r: dict) -> bool:
        if key in r and r[key] is not None:
            return bool(r[key])
        return bool((r.get("latex") or "").strip())

    return sum(1 for r in rows if good(r)) / len(rows)


def verify_pass_rate(rows: list[dict]) -> float:
    checked = [r for r in rows if r.get("check_ok") is not None]
    if not checked:
        return 0.0
    return sum(1 for r in checked if r["check_ok"]) / len(checked)


def chain_consistency(rows_a: list[dict], rows_b: list[dict]) -> Optional[float]:
    """两链路在同一组图片上的校验结论一致率。"""
    by_file_b = {r["file"]: r for r in rows_b}
    pairs = []
    for ra in rows_a:
        rb = by_file_b.get(ra.get("file"))
        if rb is None:
            continue
        ca, cb = ra.get("check_ok"), rb.get("check_ok")
        if ca is None or cb is None:
            continue
        pairs.append(ca == cb)
    return (sum(pairs) / len(pairs)) if pairs else None


def build_comparison(rows_a: list[dict], rows_b: list[dict]) -> dict:
    """汇总双链路对比。rows_* 需含: file, ok, latex, check_ok(可选)。"""
    by_file_b = {r["file"]: r for r in rows_b}
    rows = []
    for ra in rows_a:
        rb = by_file_b.get(ra["file"], {})
        rows.append({
            "file": ra["file"],
            "desc": ra.get("desc", ""),
            "latex_a": ra.get("latex", ""),
            "latex_b": rb.get("latex", ""),
            "similarity": round(latex_similarity(ra.get("latex", ""), rb.get("latex", "")), 4),
            "exact_match": latex_exact_match(ra.get("latex", ""), rb.get("latex", "")),
            "check_a": ra.get("check_status"),
            "check_b": rb.get("check_status"),
            "consistent": (ra.get("check_ok") == rb.get("check_ok")
                           if ra.get("check_ok") is not None and rb.get("check_ok") is not None
                           else None),
        })
    sims = [r["similarity"] for r in rows]
    return {
        "rows": rows,
        "summary": {
            "cases": len(rows),
            "ocr_success_a": round(ocr_success_rate(rows_a), 4),
            "ocr_success_b": round(ocr_success_rate(rows_b), 4),
            "verify_pass_a": round(verify_pass_rate(rows_a), 4),
            "verify_pass_b": round(verify_pass_rate(rows_b), 4),
            "avg_similarity": round(sum(sims) / len(sims), 4) if sims else 0.0,
            "exact_match_rate": round(sum(1 for r in rows if r["exact_match"]) / len(rows), 4) if rows else 0.0,
            "chain_consistency": chain_consistency(rows_a, rows_b),
        },
    }


def to_markdown(cmp: dict, name_a: str = "DeepSeek 视觉", name_b: str = "Pix2Text") -> str:
    s = cmp["summary"]
    lines = ["# OCR 双链路对比报告", "",
             f"- 链路A（{name_a}）OCR 成功率：{s['ocr_success_a']:.0%}",
             f"- 链路B（{name_b}）OCR 成功率：{s['ocr_success_b']:.0%}",
             f"- 链路A 校验通过率：{s['verify_pass_a']:.0%}",
             f"- 链路B 校验通过率：{s['verify_pass_b']:.0%}",
             f"- 平均 LaTeX 相似度：{s['avg_similarity']:.2%}",
             f"- 完全一致率：{s['exact_match_rate']:.0%}",
             f"- 校验结论一致率：{('%.0f%%' % (s['chain_consistency'] * 100)) if s['chain_consistency'] is not None else 'N/A'}",
             "",
             "| 图片 | 相似度 | 完全一致 | A 校验 | B 校验 | 结论一致 |",
             "|---|---|---|---|---|---|"]
    for r in cmp["rows"]:
        lines.append(f"| {r['file']} | {r['similarity']:.2f} | {'✅' if r['exact_match'] else '❌'} | "
                     f"{r['check_a'] or '-'} | {r['check_b'] or '-'} | "
                     f"{'✅' if r['consistent'] else ('❌' if r['consistent'] is False else '-')} |")
    return "\n".join(lines)


if __name__ == "__main__":
    # 自测
    a = [{"file": "f1.png", "ok": True, "latex": r"\\sin^{2}\\theta", "check_ok": True, "check_status": "equivalent"},
         {"file": "f2.png", "ok": True, "latex": "x+1", "check_ok": False, "check_status": "not_equivalent"}]
    b = [{"file": "f1.png", "ok": True, "latex": r"\\operatorname{s i n}^{2}\\theta", "check_ok": True, "check_status": "equivalent"},
         {"file": "f2.png", "ok": True, "latex": "x+2", "check_ok": False, "check_status": "not_equivalent"}]
    cmp = build_comparison(a, b)
    print(json.dumps(cmp["summary"], ensure_ascii=False, indent=2))
    print(to_markdown(cmp))