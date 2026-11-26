#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单2（w11）：可解释批改 Web 交互在线验收。

对 3 张已知错图真实调用 Pipeline（OCR → 翻译 → 校验 → 解释），
走 Web 的 run_single 事件处理函数，检查右侧「批改说明」面板是否渲染出：
错误类型徽标、定位片段、修改建议、置信度、人工复核提示；并检查 JSON/CSV
导出是否带 explain 字段。

用法（Key 只从环境变量注入，严禁写进源码）：
    $env:DEEPSEEK_API_KEY="..."; python scripts/verify_explain_web.py

可选：--serve-check 额外启动本地 Gradio 服务并请求首页，验证页面可加载。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from web import app  # noqa: E402

CASES = [
    ("image_03", "incorrect/image_03.png", "incorrect/image_03.cpp"),
    ("image_07", "incorrect/image_07.png", "incorrect/image_07.cpp"),
    ("sign_flip", "incorrect/sign_flip.png", "incorrect/sign_flip.cpp"),
]
PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")


def _noop_progress(*args, **kwargs) -> None:
    return None


def run_case(case_id: str, image_rel: str, cpp_rel: str) -> dict:
    image_path = BASE / "tests" / "benchmark" / image_rel
    cpp_path = BASE / "tests" / "benchmark" / cpp_rel
    started = time.time()
    snapshots = list(app.run_single(
        str(image_path), str(cpp_path), "auto", "auto",
        progress=_noop_progress))
    final = snapshots[-1]
    panel = final[-1]
    record = app._HISTORY[0] if app._HISTORY else {}
    explanation = record.get("explain_detail") or {}
    result = {
        "case": case_id,
        "elapsed": round(time.time() - started, 2),
        "snapshots": len(snapshots),
        "outputs": len(final),
        "verdict": record.get("verdict"),
        "status": record.get("status"),
        "explanation": explanation,
        "panel": panel,
        "checks": {
            "has_badge": bool(explanation.get("error_type_label"))
            and str(explanation.get("error_type_label")) in panel,
            "has_location": "定位" in panel and bool(explanation.get("location")),
            "has_suggestion": "修改建议" in panel
            and bool(explanation.get("suggestion")),
            "has_confidence": "置信度" in panel,
            "has_review_flag": ("需人工复核" in panel)
            or ("可自动采纳" in panel),
        },
    }
    print(f"  {case_id}: verdict={result['verdict']} "
          f"status={result['status']} "
          f"error_type={explanation.get('error_type')} "
          f"confidence={explanation.get('confidence')} "
          f"{result['elapsed']}s")
    return result


def serve_check() -> bool:
    """启动本地 Gradio（阻塞线程模式）并请求首页，验证 UI 能正常加载。"""
    demo = app.build_demo()
    demo.queue(default_concurrency_limit=1, max_size=4)
    demo.launch(server_name="127.0.0.1", server_port=7867,
                inbrowser=False, quiet=True, prevent_thread_lock=True)
    try:
        deadline = time.time() + 30
        body = ""
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:7867/", timeout=5) as response:
                    body = response.read().decode("utf-8", "replace")
                break
            except Exception:
                time.sleep(0.5)
        ok = "数学公式" in body and "gradio" in body.lower()
        check("Gradio 首页可加载（HTTP 200 + 标题）", bool(body) and ok,
              f"bytes={len(body)}")
        return ok
    finally:
        try:
            demo.close()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="可解释批改 Web 在线验收")
    parser.add_argument("--serve-check", action="store_true",
                        help="额外启动本地 Gradio 并请求首页")
    args = parser.parse_args(argv)

    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        print("[SKIP] 未设置 DEEPSEEK_API_KEY，在线验收需要真实模型。")
        return 2

    workdir = BASE / ".tmp" / "w11_explain_web"
    workdir.mkdir(parents=True, exist_ok=True)
    app.HISTORY_PATH = workdir / "web_history.json"
    app.EXPORT_DIR = workdir / "exports"
    with app.HISTORY_LOCK:
        app._HISTORY.clear()
        app._BATCH_RESULTS.clear()

    results = []
    for case_id, image_rel, cpp_rel in CASES:
        result = run_case(case_id, image_rel, cpp_rel)
        results.append(result)
        for key, value in result["checks"].items():
            check(f"{case_id} 面板 {key}", bool(value))

    print("== 导出字段 ==")
    message, json_path = app.export_current_json()
    exported = json.loads(Path(json_path).read_text(encoding="utf-8"))
    check("JSON 导出行数=3", len(exported) == 3, message)
    check("JSON 导出含 explain 字段",
          all("explain" in row for row in exported))
    check("JSON explain 为结构化对象",
          all(isinstance(row.get("explain"), dict) and row["explain"]
              for row in exported))
    check("JSON explain 字段齐全",
          all({"error_type", "location", "suggestion", "confidence",
               "needs_human_review"} <= set(row["explain"])
              for row in exported))
    message, csv_path = app.export_current_csv()
    with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as handle:
        header = handle.readline().strip().split(",")
    check("CSV 表头含 explain", "explain" in header, message)

    if args.serve_check:
        serve_check()

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cases": results,
        "pass": len(PASS),
        "fail": len(FAIL),
    }
    target = BASE / "output" / "w11_web_explain_check.json"
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(f"\n== 通过 {len(PASS)} / 失败 {len(FAIL)} ==  |  明细：{target}")
    for name in FAIL:
        print(f"  - 失败：{name}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
