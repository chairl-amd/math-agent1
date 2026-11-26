# -*- coding: utf-8 -*-
"""agent/config.py — 全局配置：模型、API Key、路径常量。

设计原则：
1. 密钥只从环境变量读取，绝不硬编码；
2. 路径统一由 BASE_DIR 派生，保证可移植；
3. 模型名集中管理，便于后续切换/扩展 verifier。
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------- 路径常量 ----------
BASE_DIR = Path(__file__).resolve().parent.parent
AGENT_DIR = BASE_DIR / "agent"
OUTPUT_DIR = BASE_DIR / "output"
DOCS_DIR = BASE_DIR / "docs"
TESTS_DIR = BASE_DIR / "tests"
TESTS_CPP_DIR = TESTS_DIR / "cpp"
TESTS_FORMULA_DIR = TESTS_DIR / "formula"
THIRD_PARTY_DIR = BASE_DIR / "third_party"
GLM_DIR = THIRD_PARTY_DIR / "glm"
VENV_DIR = BASE_DIR / "venv"

# ---------- API 配置 ----------
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai-next.com/v1").strip().rstrip("/")

# 新增：缺少 API Key 时在配置加载阶段立即失败。
if not API_KEY:
    raise RuntimeError("请设置 DEEPSEEK_API_KEY")

# 修改：保留旧变量名，兼容仍在使用这些名称的模块。
DEEPSEEK_API_KEY = API_KEY
DEEPSEEK_BASE_URL = BASE_URL

# 新增：异构校验器的 Key 与模型名；Key 仍只从环境变量读取。
GLM_API_KEY = os.environ.get("GLM_API_KEY", "").strip()
# 修改（工单3）：Qwen 同时接受 DashScope 官方变量名 DASHSCOPE_API_KEY。
QWEN_API_KEY = (
    os.environ.get("QWEN_API_KEY", "").strip()
    or os.environ.get("DASHSCOPE_API_KEY", "").strip()
)
GLM_VERIFIER_MODEL = os.environ.get("GLM_VERIFIER_MODEL", "glm-4v").strip()
# 修改（工单3）：默认改用视觉模型 qwen3-vl-plus（原 qwen-max 只能读文本）。
QWEN_VERIFIER_MODEL = os.environ.get(
    "QWEN_VERIFIER_MODEL", "qwen3-vl-plus").strip()
# 新增：异构校验器使用 OpenAI Chat Completions 兼容协议，可分别配置服务地址。
QWEN_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
# 修改（w12 / 工单1 配置补齐）：默认仍指向 DashScope 官方端点，保持既有语义；
# 但把「一条 Key 同时驱动主网关与 Qwen」的实测结论固化在这里，便于一键切换：
#   * 本机实测：DashScope 官方端点对当前 Key 返回 401 invalid_api_key；
#     同名模型 qwen3-vl-plus 经主网关 BASE_URL 的 OpenAI 兼容接口可以正常返回。
#   * 直接默认改走主网关会带来副作用：SymPy 判 unknown 的 PDE 用例（image_04）
#     会被异构「不一致」票升级为 CONFIRMED_ERROR，突破「PDE 恒为 unknown +
#     人工复核」的既定边界。因此主网关改为**显式开关**，不设为默认。
# 切换方式（不写死任何密钥）：$env:QWEN_BASE_URL="https://api.openai-next.com/v1"
QWEN_BASE_URL = (
    os.environ.get("QWEN_BASE_URL", "").strip().rstrip("/")
    or QWEN_DASHSCOPE_BASE_URL
)
GLM_BASE_URL = os.environ.get(
    "GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"
).strip().rstrip("/")
# 新增：全局开关关闭时，校验链只使用纯 SymPy。
HETERO_VERIFY_ENABLED = os.environ.get(
    "HETERO_VERIFY_ENABLED", "1"
).strip() not in ("0", "false", "False")
# 新增（w10）：符号层无法判定（能力边界）时，是否采信异构校验器的一致等价结论。
# 设为 0 可退回「全一致才等价」的严格共识策略（PDE 用例始终不采信）。
HETERO_ACCEPT_CONSENSUS = os.environ.get(
    "HETERO_ACCEPT_CONSENSUS", "1"
).strip() not in ("0", "false", "False")
# 新增：单次异构校验的超时时间，单位为秒。
HETERO_VERIFY_TIMEOUT = int(os.environ.get("HETERO_VERIFY_TIMEOUT", "60"))
# 新增：给异构模型留足推理与最终 JSON 的输出预算，避免正文被推理耗尽。
HETERO_MAX_TOKENS = int(os.environ.get("HETERO_MAX_TOKENS", "8192"))
# 工单1：JSON 修复重试使用独立预算，保证推理模型还能输出最终 JSON。
HETERO_JSON_MAX_TOKENS = int(
    os.environ.get("HETERO_JSON_MAX_TOKENS", "8192"))

# ---------- 可解释批改（工单1 / w11） ----------
# 总开关：关闭时解释层不做任何模型调用，报告结构与现状完全一致。
EXPLAIN_ENABLED = os.environ.get(
    "EXPLAIN_ENABLED", "1").strip().lower() not in (
        "0", "false", "off", "no")
# 解释层只做一次结构化输出；预算与超时独立于校验链，便于单独排查。
# 修改（实测）：推理模型 2048 token 会被推理耗尽，正文变空；首轮提高到 8192，
# 失败修复时再逐次翻倍（见 explain._default_model_call）。
EXPLAIN_MAX_TOKENS = int(os.environ.get("EXPLAIN_MAX_TOKENS", "8192"))
EXPLAIN_TIMEOUT = int(os.environ.get("EXPLAIN_TIMEOUT", "90"))
# 解释结果持久化缓存：同一 (公式, 代码, 证据, 结论) 只调用一次模型。
EXPLAIN_CACHE_ENABLED = os.environ.get(
    "EXPLAIN_CACHE_ENABLED", "1").strip() != "0"

# ---------- 模型配置 ----------
TEXT_MODEL = "deepseek-v4-1-flash-261017"
VISION_MODEL = "deepseek-v4-1-flash-261017"

# 修改：保留旧变量名，兼容仍在使用这些名称的模块。
MODEL_TEXT = TEXT_MODEL
MODEL_VISION = VISION_MODEL

MODELS = {
    "vision": VISION_MODEL,
    "text": TEXT_MODEL,
    "planner": TEXT_MODEL,
    "verifier": None,
}

# ---------- 运行参数 ----------
REQUEST_TIMEOUT = int(os.environ.get("DEEPSEEK_TIMEOUT", "120"))
MAX_RETRIES = int(os.environ.get("DEEPSEEK_MAX_RETRIES", "3"))
TEMPERATURE = 0.0

# ---------- 工具函数 ----------
def ensure_dirs() -> None:
    """确保所有输出目录存在。"""
    for d in (OUTPUT_DIR, DOCS_DIR, TESTS_CPP_DIR, TESTS_FORMULA_DIR, GLM_DIR):
        d.mkdir(parents=True, exist_ok=True)


def get_api_key(required: bool = True) -> str:
    """返回配置加载时读取的 API Key。"""
    return API_KEY


def get_base_url() -> str:
    return BASE_URL


def qwen_vl_ready() -> bool:
    """新增（w12）：Qwen-VL 异构校验是否具备调用条件。

    只做配置层面的判断（Key 非空 + 模型名非空），不发起网络请求，
    便于基准报告与网页端在启动时如实标注「Qwen 是否可用」。
    """
    return bool(QWEN_API_KEY) and bool(QWEN_VERIFIER_MODEL)


if __name__ == "__main__":
    print("BASE_DIR   =", BASE_DIR)
    print("OUTPUT_DIR =", OUTPUT_DIR)
    print("BASE_URL   =", BASE_URL)
    print("TEXT_MODEL =", TEXT_MODEL)
    print("VISION_MODEL =", VISION_MODEL)
    print("API_KEY    =", "已配置" if API_KEY else "未配置")
    print("QWEN_MODEL =", QWEN_VERIFIER_MODEL)
    print("QWEN_BASE  =", QWEN_BASE_URL)
    print("QWEN_READY =", qwen_vl_ready())
