#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单4 回归测试：批量进度条、历史持久化、JSON/CSV 导出（离线）。"""
from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from web import app  # noqa: E402

PASS, FAIL = [], []
WORKDIR = BASE / ".tmp" / "wo4_web_batch_test"


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")


class _FakePipeline:
    """确定性替身：奇数张判等价，偶数张判不等价。"""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, *, image=None, cpp_path=None, **kwargs):
        self.calls += 1
        equivalent = self.calls % 2 == 1
        status = "equivalent" if equivalent else "not_equivalent"
        check = {
            "name": "latex_vs_cpp",
            "status": status,
            "ok": equivalent,
            "detail": f"替身判定 {status}",
            "meta": {},
        }
        if not equivalent:
            # 工单2（w11）：错例附带结构化批改说明，用于验证导出字段。
            check["explain"] = {
                "error_type": "sign_error",
                "error_type_label": "符号相反",
                "root_cause_layer": "运算/符号",
                "location": "L6: w[i] += eta * g[i];",
                "suggestion": "改为 w[i] -= eta * g[i];",
                "confidence": 0.9,
                "needs_human_review": False,
                "source": "llm",
            }
        return {
            "ok": equivalent,
            "elapsed": round(0.01 * self.calls, 3),
            "engine": "vision",
            "verifier_mode": "hybrid",
            "steps": {
                "latex": {"text": f"x_{self.calls}=1"},
                "translate_cpp": {"ref_latex": f"x_{self.calls}=1"},
                "ocr": {"meta": {"engine_chain": "vision"}},
                "checks": [check],
            },
            "cache": {},
        }


def main() -> int:
    if WORKDIR.exists():
        shutil.rmtree(WORKDIR)
    WORKDIR.mkdir(parents=True)

    app.HISTORY_PATH = WORKDIR / "web_history.json"
    app.EXPORT_DIR = WORKDIR / "exports"
    with app.HISTORY_LOCK:
        app._HISTORY.clear()
        app._BATCH_RESULTS.clear()

    images = sorted((BASE / "tests" / "benchmark" / "correct").glob("*.png"))[:5]
    check("使用 5 张真实图片", len(images) == 5,
          ", ".join(image.name for image in images))

    print("== 进度条 ==")
    check("0/0 不崩溃且为 0%",
          "0/0（0%）" in app.progress_bar_html(0, 0, "等待"))
    check("3/5 显示 60%",
          "3/5（60%）" in app.progress_bar_html(3, 5, "处理中"))
    check("5/5 显示 100%",
          "5/5（100%）" in app.progress_bar_html(5, 5, "完成"))

    print("== 批量执行 ==")
    fake_pipeline = _FakePipeline()
    app.get_pipeline = lambda engine, verifier: fake_pipeline
    snapshots = list(app.run_batch(
        files=[str(image) for image in images],
        cpp_path=None,
        engine="auto",
        verifier="hybrid",
        progress=lambda *args, **kwargs: None,
    ))
    check("批量产出快照", len(snapshots) >= 7, f"len={len(snapshots)}")
    check("每次快照含 5 个输出", all(len(item) == 5 for item in snapshots))
    final = snapshots[-1]
    check("最终进度条 5/5（100%）",
          "5/5（100%）" in final[0], final[0][:120])
    check("最终状态提示完成", "完成" in final[1], final[1])
    check("批量结果 5 行", len(final[2]) == 5, f"rows={len(final[2])}")
    check("历史记录同步 5 行", len(final[3]) == 5, f"rows={len(final[3])}")
    check("批量汇总包含通过/不通过",
          "通过 3" in final[4] and "不通过 2" in final[4], final[4])

    print("== 历史持久化 ==")
    persisted = json.loads(app.HISTORY_PATH.read_text(encoding="utf-8"))
    check("历史文件落盘 5 条", len(persisted) == 5, f"len={len(persisted)}")
    reloaded = app.load_history_records(app.HISTORY_PATH)
    check("重启后恢复 5 条", len(reloaded) == 5, f"len={len(reloaded)}")
    check("恢复顺序为最新在前",
          reloaded[0]["image_name"] == images[-1].name,
          f"{reloaded[0]['image_name']} vs {images[-1].name}")

    print("== 导出 ==")
    message, json_path = app.export_current_json()
    check("JSON 导出成功", bool(json_path) and Path(json_path).is_file(), message)
    check("JSON 导出到配置目录",
          Path(json_path).parent == Path(app.EXPORT_DIR),
          f"{Path(json_path).parent}")
    exported = json.loads(Path(json_path).read_text(encoding="utf-8"))
    check("JSON 导出 5 行 11 字段",
          len(exported) == 5 and len(exported[0]) == 11,
          f"rows={len(exported)}")
    fail_rows = [row for row in exported if row.get("状态") == "not_equivalent"]
    check("JSON 导出的 explain 为结构化对象",
          bool(fail_rows) and isinstance(fail_rows[0].get("explain"), dict) and
          fail_rows[0]["explain"].get("error_type") == "sign_error",
          f"explain={fail_rows[0].get('explain') if fail_rows else None}")
    check("通过行的 explain 为空对象",
          all(row.get("explain") == {} for row in exported
              if row.get("状态") == "equivalent"))

    message, csv_path = app.export_current_csv()
    check("CSV 导出成功", bool(csv_path) and Path(csv_path).is_file(), message)
    check("CSV 导出到配置目录",
          Path(csv_path).parent == Path(app.EXPORT_DIR),
          f"{Path(csv_path).parent}")
    with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.reader(handle))
    check("CSV 表头 + 5 行、11 列",
          len(csv_rows) == 6 and all(len(row) == 11 for row in csv_rows),
          f"rows={len(csv_rows)}")
    check("CSV 表头含 explain 列", "explain" in csv_rows[0], str(csv_rows[0]))

    print("== 界面构建 ==")
    sample_explain = fail_rows[0]["explain"] if fail_rows else {}
    panel = app._explanation_markdown(sample_explain)
    check("批改说明面板含类型徽标",
          "符号相反" in panel and "sign_error" in panel, panel[:120])
    check("批改说明面板含定位与建议",
          "L6" in panel and "w[i] -= eta * g[i];" in panel)
    check("批改说明面板含置信度与复核标记",
          "90%" in panel and "可自动采纳" in panel)
    check("无解释时给出占位说明",
          "没有批改说明" in app._explanation_markdown({}))
    demo = app.build_demo()
    check("build_demo 返回 Blocks", type(demo).__name__ == "Blocks")

    print(f"\n== 通过 {len(PASS)} / 失败 {len(FAIL)} ==")
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
