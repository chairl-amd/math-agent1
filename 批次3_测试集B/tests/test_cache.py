#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单1离线自测：验证缓存核心行为，不调用真实 API。"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "agent"))

import cache  # noqa: E402


@dataclass
class OcrResult:
    """等价的最小结果对象，避免单测加载 Pillow 的二进制依赖。"""
    latex: str = ""
    ok: bool = False
    engine: str = "test"
    error: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class CheckResult:
    """等价的最小校验结果对象，字段顺序与项目 CheckResult 保持一致。"""
    ok: bool
    status: str
    lhs: str = ""
    rhs: str = ""
    detail: str = ""
    diff_points: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")


def count_delta(before: dict, after: dict, cache_kind: str, field: str) -> int:
    return after[cache_kind][field] - before[cache_kind][field]


def main() -> int:
    old_switch = os.environ.get("MATH_AGENT_CACHE")
    os.environ.pop("MATH_AGENT_CACHE", None)
    test_cache_dir = BASE / ".tmp"
    unique_name = f"cache_test_{os.getpid()}_{time.time_ns()}"
    image_one = test_cache_dir / f"{unique_name}_one.png"
    image_two = test_cache_dir / f"{unique_name}_two.png"
    try:
        # 使用项目已有 .tmp，避免沙箱中新目录 ACL 不可写的问题。
        test_cache_dir.mkdir(parents=True, exist_ok=True)
        with patch.object(cache, "CACHE_DIR", test_cache_dir):
            cache.clear()

            # 1) 图片内容相同但路径不同，应得到同一个内容 key。
            image_bytes = b"same-image-content-for-cache-test"
            image_one.write_bytes(image_bytes)
            image_two.write_bytes(image_bytes)
            before = cache.stats()
            check("首次 OCR 查询未命中",
                  cache.get_ocr(image_one) is None)
            original_ocr = OcrResult(
                latex=r"x+1",
                ok=True,
                engine="test",
                meta={"engine_chain": "vision", "nested": {"value": 1}},
            )
            cache.put_ocr(image_one, original_ocr)
            found_by_other_path = cache.get_ocr(image_two)
            after = cache.stats()
            check("同内容不同路径共享 OCR 缓存",
                  found_by_other_path is not None and
                  found_by_other_path.latex == r"x+1")
            check("OCR 命中/未命中/写入计数正确",
                  count_delta(before, after, "ocr", "hits") == 1 and
                  count_delta(before, after, "ocr", "misses") == 1 and
                  count_delta(before, after, "ocr", "writes") == 1)

            # 2) 返回对象必须是深拷贝，调用方修改不能污染下一次命中。
            found_by_other_path.meta["nested"]["value"] = 99
            original_ocr.meta["nested"]["value"] = 88
            isolated_result = cache.get_ocr(image_two)
            check("OCR 缓存读取使用 deepcopy 隔离",
                  isolated_result is not None and
                  isolated_result.meta["nested"]["value"] == 1)

            # 3) 禁用开关必须同时绕过读写，并标记 disabled。
            disabled_before = cache.stats()
            os.environ["MATH_AGENT_CACHE"] = "0"
            disabled_get = cache.get_ocr(image_two)
            disabled_put = cache.put_ocr(image_one, original_ocr)
            disabled_stats = cache.stats()
            check("MATH_AGENT_CACHE=0 绕过读写",
                  disabled_get is None and disabled_put is False)
            check("禁用状态计数不增加且 stats 标记 disabled",
                  disabled_stats["disabled"] is True and
                  disabled_stats["ocr"] == disabled_before["ocr"])
            os.environ.pop("MATH_AGENT_CACHE", None)

            # 4) 损坏 pickle 应备份、计数错误并重建，不得抛出异常。
            cache.clear()
            corrupt_file = test_cache_dir / "ocr.pkl"
            corrupt_file.write_bytes(b"not-a-valid-pickle")
            corrupt_before = cache.stats()
            corrupt_result = cache.get_ocr(image_one)
            corrupt_after = cache.stats()
            corrupt_backups = list(test_cache_dir.glob("ocr.pkl.corrupt.*"))
            check("损坏 OCR pickle 自动恢复为未命中",
                  corrupt_result is None and bool(corrupt_backups))
            check("损坏 OCR pickle 错误计数增加",
                  count_delta(corrupt_before, corrupt_after, "ocr", "errors") == 1)
            cache.put_ocr(image_one, original_ocr)
            check("损坏文件备份后可重新写入并命中",
                  cache.get_ocr(image_one) is not None)

            # 5) 校验 key 只由纯文本稳定生成，并能区分分隔符拼接碰撞。
            cache.clear()
            verify_before = cache.stats()
            latex_text = r"\frac{a}{b}"
            code_text = "a / b"
            check("首次校验查询未命中",
                  cache.get_verify(latex_text, code_text) is None)
            expected_check = CheckResult(
                True,
                "equivalent",
                latex_text,
                code_text,
                "离线测试结果",
                meta={"nested": {"value": 7}},
            )
            cache.put_verify(latex_text, code_text, expected_check)
            verify_hit = cache.get_verify(latex_text, code_text)
            verify_after = cache.stats()
            check("校验 key 对相同纯文本稳定命中",
                  verify_hit is not None and verify_hit.status == "equivalent")
            check("校验命中/未命中/写入计数正确",
                  count_delta(verify_before, verify_after, "verify", "hits") == 1 and
                  count_delta(verify_before, verify_after, "verify", "misses") == 1 and
                  count_delta(verify_before, verify_after, "verify", "writes") == 1)
            verify_hit.meta["nested"]["value"] = 100
            verify_again = cache.get_verify(latex_text, code_text)
            check("校验缓存读取使用 deepcopy 隔离",
                  verify_again is not None and
                  verify_again.meta["nested"]["value"] == 7)
            check("校验 key 不因空字符拼接产生碰撞",
                  cache.get_verify("a", "b\x00c") is None and
                  cache.get_verify("a\x00b", "c") is None)

            # 6) scope 隔离：同图不同识别提示词/引擎、同文本不同校验模式互不串用。
            cache.clear()
            cache.put_ocr(image_one, original_ocr, "vision|")
            check("OCR 缓存 scope 隔离",
                  cache.get_ocr(image_one) is None and
                  cache.get_ocr(image_one, "vision|") is not None and
                  cache.get_ocr(image_one, "pix|") is None)
            cache.put_verify(latex_text, code_text, expected_check,
                             "verifier:sympy-only")
            check("校验缓存 scope 隔离",
                  cache.get_verify(
                      latex_text, code_text, "verifier:hybrid") is None and
                  cache.get_verify(
                      latex_text, code_text, "verifier:sympy-only") is not None)
            check("模块级 format_stats 支持传入统计快照",
                  "OCR" in cache.format_stats(cache.stats()))
    finally:
        if old_switch is None:
            os.environ.pop("MATH_AGENT_CACHE", None)
        else:
            os.environ["MATH_AGENT_CACHE"] = old_switch
        for temp_file in (image_one, image_two,
                          test_cache_dir / "ocr.pkl",
                          test_cache_dir / "verify.pkl"):
            try:
                temp_file.unlink(missing_ok=True)
            except OSError:
                pass
        for backup_file in test_cache_dir.glob("ocr.pkl.corrupt.*"):
            try:
                backup_file.unlink(missing_ok=True)
            except OSError:
                pass

    print(f"\n== PASS {len(PASS)} / FAIL {len(FAIL)} ==")
    if FAIL:
        for name in FAIL:
            print(f"FAILED: {name}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
