#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/compare_chains.py — 双链路对比（工单20/21/24）。

输入：output/vision_latex.json（链路A）、output/pix2text_latex.json（链路B）
输出：
- output/smoke_test_compare.txt   （工单20：逐条差异对比）
- output/smoke_test_report.md     （工单21：冒烟测试报告）
- output/ocr_comparison_report.md （工单24：10 组对比报告）
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "agent"))

from metrics import build_comparison, to_markdown  # noqa: E402


def main() -> int:
    va = BASE / "output" / "vision_latex.json"
    vb = BASE / "output" / "pix2text_latex.json"
    if not va.exists():
        print(f"ERROR: 缺少 {va}（先运行 tests/link_a.py）", file=sys.stderr)
        return 2
    if not vb.exists():
        print(f"ERROR: 缺少 {vb}（先运行链路B 采集）", file=sys.stderr)
        return 2
    a = json.loads(va.read_text(encoding="utf-8"))
    b = json.loads(vb.read_text(encoding="utf-8"))
    cmp = build_comparison(a, b)

    # 工单20：逐条对比（txt）
    lines = ["# 链路A（DeepSeek 视觉） vs 链路B（Pix2Text）", ""]
    for r in cmp["rows"]:
        lines.append(f"== {r['file']}  {r['desc']} ==")
        lines.append(f"  A: {r['latex_a']!r}")
        lines.append(f"  B: {r['latex_b']!r}")
        lines.append(f"  相似度: {r['similarity']:.4f}  完全一致: {r['exact_match']}")
        lines.append(f"  校验: A={r['check_a']}  B={r['check_b']}  一致={r['consistent']}")
        lines.append("")
    lines.append("## 汇总")
    s = cmp["summary"]
    for k, v in s.items():
        lines.append(f"  {k}: {v}")
    (BASE / "output" / "smoke_test_compare.txt").write_text("\n".join(lines), encoding="utf-8")

    # 工单24：对比报告（md）
    (BASE / "output" / "ocr_comparison_report.md").write_text(
        to_markdown(cmp), encoding="utf-8")

    # 工单21：冒烟测试报告
    md = ["# 冒烟测试报告（双链路）", "",
          f"- 用例数：{s['cases']}",
          f"- 链路A OCR 成功率：{s['ocr_success_a']:.0%}",
          f"- 链路B OCR 成功率：{s['ocr_success_b']:.0%}",
          f"- 平均 LaTeX 相似度：{s['avg_similarity']:.2%}",
          f"- 校验结论一致率：{('%.0f%%' % (s['chain_consistency'] * 100)) if s['chain_consistency'] is not None else 'N/A'}",
          "", "## 结论", ""]
    if s["ocr_success_a"] >= 0.9 and s["ocr_success_b"] >= 0.9:
        md.append("- ✅ 两条链路均可完成 OCR，冒烟通过。")
    else:
        md.append("- ⚠️ 存在 OCR 失败用例，需检查。")
    if s["chain_consistency"] is not None and s["chain_consistency"] >= 0.8:
        md.append("- ✅ 两链路校验结论高度一致。")
    else:
        md.append("- ⚠️ 两链路结论差异较大，建议人工复核差异用例。")
    md += ["", "详见 `ocr_comparison_report.md` 与 `smoke_test_compare.txt`。"]
    (BASE / "output" / "smoke_test_report.md").write_text("\n".join(md), encoding="utf-8")

    print(json.dumps(s, ensure_ascii=False, indent=2))
    print("已生成: output/smoke_test_compare.txt")
    print("已生成: output/smoke_test_report.md")
    print("已生成: output/ocr_comparison_report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())