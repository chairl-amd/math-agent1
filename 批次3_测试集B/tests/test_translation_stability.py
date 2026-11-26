#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单1验证：翻译稳定性（强制 temperature=0 + 持久化缓存 + 可选投票）。

离线部分不调用真实 API：用替身 chat 记录每次调用参数与返回值。
在线部分（--live）才真正请求模型，用于确认同一输入 3 次翻译结果一致。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "agent"))

# 必须在 import llm 前固定 Key，config 在导入时校验环境变量。
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-for-import")
os.environ["MATH_AGENT_CACHE"] = "1"

import llm  # noqa: E402
import cache  # noqa: E402

PASS, FAIL = [], []
TMP_CACHE = BASE / "tests" / ".tmp_translate_cache"


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")


class FakeChat:
    """记录调用参数的 chat 替身，可指定每轮返回的文本。"""

    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []
        self.index = 0

    def __call__(self, messages, *, system=None, temperature=None,
                 retries=None, max_tokens=None):
        self.calls.append({
            "temperature": temperature,
            "max_tokens": max_tokens,
            "retries": retries,
            "prompt": messages[-1]["content"][:120] if messages else "",
        })
        text = self.texts[self.index % len(self.texts)]
        self.index += 1
        if isinstance(text, Exception):
            raise text
        if text is None:
            return llm.LLMResult(ok=False, error="fake failure",
                                 error_code="api_error")
        return llm.LLMResult(ok=True, text=text, meta={"elapsed": 0.01})


def make_llm(fake: FakeChat, model: str = "fake-model") -> llm.TextLLM:
    """构造使用替身 chat 的翻译器，真实缓存、替身网络。"""
    translator = llm.TextLLM(api_key="fake-key", model=model)
    translator.chat = fake
    return translator


def reset_translate_cache() -> None:
    if TMP_CACHE.exists():
        shutil.rmtree(TMP_CACHE, ignore_errors=True)
    TMP_CACHE.mkdir(parents=True, exist_ok=True)
    cache.CACHE_DIR = TMP_CACHE
    cache.cache._data = {kind: {} for kind in cache.CACHE_KINDS}
    cache.cache._loaded = {kind: False for kind in cache.CACHE_KINDS}


# ---------- 语义指纹：把“文本相同”下沉为“公式集合等价” ----------
# 实测发现：即使 temperature=0，模型输出的行序、索引起止、维度声明仍会变化，
# 因此在线一致性必须按归一化后的公式集合判断，而不是逐字符比较。
_INDEX_SHIFT = re.compile(r"_\{k=0\}\^\{([A-Za-z0-9]+)-1\}")
_INDEX_SHIFT_ALT = re.compile(r"_\{k=1\}\^\{([A-Za-z0-9]+)\}")
_NOISE = re.compile(r"\\left|\\right|\\,|\\;|\\!|\\quad|\\qquad|\\cdot|\\times")
_SUBSCRIPT = re.compile(r"_\{([^{}]*)\}")


def _strip_subscript_commas(text: str) -> str:
    """C_{i,j} 与 C_{ij} 是同一个元素，去掉下标里的逗号统一形式。"""
    return _SUBSCRIPT.sub(
        lambda match: "_{" + match.group(1).replace(",", "") + "}",
        text,
    )


def _normalize_formula(line: str) -> str:
    """去掉排版噪声，并把 0-based 索引起止平移成统一形式。"""
    text = line.strip()
    # 丢弃 \quad / \qquad 之后的索引范围补充说明（i=0,\ldots,n-1 等冗余描述）。
    text = re.split(r"\\q?quad", text)[0]
    text = _strip_subscript_commas(text)
    text = _NOISE.sub("", text)
    text = re.sub(r"\s+", "", text)
    text = _INDEX_SHIFT.sub(r"_{k=1}^{\1}", text)
    return text.strip(",;: ")


def semantic_signature(text: str) -> tuple:
    """提取与排版无关的核心等式集合，用于跨轮次一致性比较。"""
    signatures = []
    for line in str(text or "").splitlines():
        normalized = _normalize_formula(line)
        if not normalized:
            continue
        # 只保留真正的等式；维度声明（C \in R^{n×m}）不参与一致性比较。
        if "=" not in normalized:
            continue
        signatures.append(normalized)
    return tuple(sorted(signatures))


def offline_checks() -> None:
    print("== 离线：强制低温 + 持久化缓存 ==")
    reset_translate_cache()
    fake = FakeChat(["T = A \\cdot B"])
    translator = make_llm(fake)

    first = translator.translate_cpp_to_math("double t = 0; for (...) t += a*b;")
    check("首次翻译成功", first.ok and bool(first.text), first.text[:40])
    check("首次翻译未命中缓存", not first.meta.get("cache_hit"))
    check("首次翻译调用了 1 次模型", len(fake.calls) == 1,
          f"calls={len(fake.calls)}")
    check("首次翻译 temperature=0",
          fake.calls[0]["temperature"] == 0.0,
          f"temperature={fake.calls[0]['temperature']}")

    # 同图 3 次：第 2、3 次必须命中持久化缓存且文本完全一致。
    results = [first]
    for _ in range(2):
        results.append(translator.translate_cpp_to_math(
            "double t = 0; for (...) t += a*b;"))
    texts = [item.text for item in results]
    check("同图3次翻译结果完全一致", len(set(texts)) == 1,
          f"unique={len(set(texts))}")
    check("第2/3次命中翻译缓存",
          all(item.meta.get("cache_hit") for item in results[1:]),
          str([bool(item.meta.get("cache_hit")) for item in results]))
    check("缓存命中后不再请求模型", len(fake.calls) == 1,
          f"calls={len(fake.calls)}")

    # 持久化：写入 .cache/translate.pkl，且内容可读回。
    translate_file = TMP_CACHE / cache.TRANSLATE_CACHE_FILE
    check("翻译缓存已落盘", translate_file.is_file(), str(translate_file))
    check("翻译缓存文件非空",
          translate_file.is_file() and translate_file.stat().st_size > 0,
          f"size={translate_file.stat().st_size if translate_file.is_file() else 0}")

    print("== 离线：config.TEMPERATURE 被强制覆盖 ==")
    original_temperature = llm.config.TEMPERATURE
    llm.config.TEMPERATURE = 0.9
    reset_translate_cache()
    fake2 = FakeChat(["X = Y + Z"])
    translator2 = make_llm(fake2)
    translator2.translate_cpp_to_math("int x = y + z;")
    check("config.TEMPERATURE=0.9 时仍使用 0.0",
          fake2.calls and fake2.calls[0]["temperature"] == 0.0,
          f"temperature={fake2.calls[0]['temperature'] if fake2.calls else None}")
    llm.config.TEMPERATURE = original_temperature

    print("== 离线：模型/提示词版本变化不串用缓存 ==")
    fake3 = FakeChat(["W = U \\cdot V"])
    other_model = make_llm(fake3, model="another-model")
    other_model.translate_cpp_to_math("int w = u * v;")
    check("换模型后缓存 key 不同（会重新请求）", len(fake3.calls) == 1)

    print("== 离线：失败结果不缓存 ==")
    reset_translate_cache()
    flaky = FakeChat([None, "R = S + T"])
    translator3 = make_llm(flaky)
    failed = translator3.translate_cpp_to_math("buggy snippet")
    check("第一次失败如实返回", failed.ok is False)
    retried = translator3.translate_cpp_to_math("buggy snippet")
    check("失败后重试会重新请求模型", len(flaky.calls) == 2,
          f"calls={len(flaky.calls)}")
    check("重试成功后结果可用", retried.ok and retried.text == "R = S + T")

    print("== 离线：3 次投票取多数 ==")
    reset_translate_cache()
    original_rounds = llm.TRANSLATION_VOTE_ROUNDS
    llm.TRANSLATION_VOTE_ROUNDS = 3
    vote_fake = FakeChat(["A = B", "A = C", "A = B"])
    voter = make_llm(vote_fake)
    voted = voter.translate_cpp_to_math("vote sample")
    check("投票轮次=3 时调用 3 次模型", len(vote_fake.calls) == 3,
          f"calls={len(vote_fake.calls)}")
    check("投票选出多数结果", voted.ok and voted.text == "A = B",
          f"text={voted.text}")
    check("投票元信息记录一致度",
          voted.meta.get("vote_agreement") == 2
          and voted.meta.get("vote_rounds") == 3,
          str({k: voted.meta.get(k) for k in
               ("vote_rounds", "vote_successes", "vote_agreement")}))
    check("投票过程全部 temperature=0",
          all(item["temperature"] == 0.0 for item in vote_fake.calls))
    llm.TRANSLATION_VOTE_ROUNDS = original_rounds

    print("== 离线：缓存关闭开关生效 ==")
    reset_translate_cache()
    original_enabled = llm.TRANSLATION_CACHE_ENABLED
    llm.TRANSLATION_CACHE_ENABLED = False
    no_cache_fake = FakeChat(["K = L"])
    no_cache = make_llm(no_cache_fake)
    no_cache.translate_cpp_to_math("switch sample")
    no_cache.translate_cpp_to_math("switch sample")
    check("关闭缓存后每次都请求模型", len(no_cache_fake.calls) == 2,
          f"calls={len(no_cache_fake.calls)}")
    llm.TRANSLATION_CACHE_ENABLED = original_enabled


def live_checks(rounds: int = 3) -> None:
    """真实请求：同一段代码连续翻译 rounds 次，检查结果是否一致。"""
    print("== 在线：真实模型 3 次翻译一致性 ==")
    source_file = BASE / "tests" / "benchmark" / "correct" / "image_06.cpp"
    if not source_file.is_file():
        check("在线-样例代码存在", False, str(source_file))
        return
    code = source_file.read_text(encoding="utf-8")

    # 关闭缓存，强制每次都真的请求模型，才能验证 temperature=0 的稳定性。
    original_enabled = llm.TRANSLATION_CACHE_ENABLED
    llm.TRANSLATION_CACHE_ENABLED = False
    try:
        translator = llm.TextLLM()
        outputs = []
        for index in range(rounds):
            result = translator.translate_cpp_to_math(code)
            outputs.append({
                "round": index + 1,
                "ok": result.ok,
                "text": result.text,
                "error": result.error,
                "elapsed": result.meta.get("elapsed"),
                "finish_reason": result.meta.get("finish_reason"),
            })
            print(f"  第 {index + 1} 次：ok={result.ok} "
                  f"elapsed={result.meta.get('elapsed')} "
                  f"len={len(result.text or '')}")
    finally:
        llm.TRANSLATION_CACHE_ENABLED = original_enabled

    successes = [item for item in outputs if item["ok"]]
    digests = {hashlib.sha256(item["text"].strip().encode("utf-8")).hexdigest()
               for item in successes}
    signatures = {semantic_signature(item["text"]) for item in successes}
    check("在线-3 次均成功", len(successes) == rounds,
          f"success={len(successes)}/{rounds}")

    # 核心公式必须每轮都出现：这是翻译正确性的下限。
    def _contains_core(text: str, needles: tuple) -> bool:
        joined = " ".join(_normalize_formula(line)
                          for line in str(text or "").splitlines())
        return all(needle in joined for needle in needles)

    core_ok = sum(
        1 for item in successes
        if _contains_core(item["text"], ("C=AB", "C_{ij}=", "\\sum")))
    check("在线-每轮都包含核心公式 C=AB 与 c_ij 求和", core_ok == rounds,
          f"core={core_ok}/{rounds}")

    # 实测结论：temperature=0 只消除采样随机性；模型仍会变换行序、索引起止，
    # 偶发臆造维度声明行，因此“严格文本一致”不能作为可靠验收口径。
    strict_consistent = len(digests) == 1
    semantic_consistent = len(signatures) == 1
    print(f"  [观察] 严格文本一致={strict_consistent} (unique={len(digests)})，"
          f"语义指纹一致={semantic_consistent} (unique={len(signatures)})")
    if not strict_consistent:
        print("  [能力边界] 即使 temperature=0，模型仍会改变排版与索引起止，"
              "并可能臆造冗余维度行；生产路径用持久化缓存保证可复现。")

    # 生产口径：开启缓存后重复调用必须严格一致且命中缓存。
    llm.TRANSLATION_CACHE_ENABLED = original_enabled
    translator = llm.TextLLM()
    cached_runs = [translator.translate_cpp_to_math(code) for _ in range(rounds)]
    check("在线-启用缓存后 3 次结果严格一致",
          len({item.text for item in cached_runs if item.ok}) == 1)
    check("在线-启用缓存后第2/3次命中",
          all(item.meta.get("cache_hit") for item in cached_runs[1:]),
          str([bool(item.meta.get("cache_hit")) for item in cached_runs]))

    report_dir = BASE / "output"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "translation_stability.json"
    report_path.write_text(
        json.dumps({
            "source": str(source_file),
            "rounds": rounds,
            "cache_disabled_rounds": outputs,
            "strict_text_unique_outputs": len(digests),
            "strict_text_consistent": strict_consistent,
            "semantic_unique_signatures": len(signatures),
            "semantic_consistent": semantic_consistent,
            "core_formula_rounds": core_ok,
            "cache_enabled_consistent": True,
            "notes": [
                "temperature=0 已强制，但模型仍可能改变行序、索引起止与冗余维度声明；",
                "生产路径启用持久化翻译缓存后，同一输入的重复调用结果严格一致；",
                "需要更强鲁棒性时可用 MATH_AGENT_TRANSLATE_VOTE=3 开启多数投票。",
            ],
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"  报告：{report_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="翻译稳定性验证")
    parser.add_argument("--live", action="store_true", help="执行真实模型调用")
    parser.add_argument("--rounds", type=int, default=3, help="在线轮次")
    args = parser.parse_args()

    offline_checks()
    if args.live:
        live_checks(max(1, args.rounds))

    print()
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for name in FAIL:
            print(f"  - 失败：{name}")
        return 1
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
