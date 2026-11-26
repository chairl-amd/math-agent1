#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""异构校验器实测脚本。

用两个字符串用例比较 SymPy、Qwen-VL 与汇总链的结论。
没有 QWEN_API_KEY 时直接跳过，不发起网络请求。
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import types
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

BASE = Path(__file__).resolve().parent.parent
AGENT_DIR = BASE / "agent"


def install_temporary_ctypes_compat() -> str:
    """当前便携解释器无法加载 _ctypes 时，提供 SymPy 需要的最小定义。"""
    try:
        import ctypes  # noqa: F401
        return ""
    except Exception as exc:
        sys.modules.pop("ctypes", None)
        ctypes_module = types.ModuleType("ctypes")
        ctypes_module.c_long = int
        ctypes_module.sizeof = lambda value: struct.calcsize("l")
        sys.modules["ctypes"] = ctypes_module
        return f"{type(exc).__name__}: {exc}"


_NODE_HTTPS_CLIENT = r"""
const https = require("https");
const http = require("http");

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", () => {
  let requestData;
  try {
    requestData = JSON.parse(input);
  } catch (error) {
    process.stderr.write(`请求数据不是 JSON: ${error.message}`);
    process.exitCode = 2;
    return;
  }

  const url = new URL(requestData.url);
  const client = url.protocol === "https:" ? https : http;
  const body = JSON.stringify(requestData.body);
  const request = client.request(url, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${process.env.OPENAI_API_KEY}`,
      "Content-Type": "application/json",
      "Content-Length": Buffer.byteLength(body)
    },
    timeout: requestData.timeout_ms
  }, (response) => {
    let responseBody = "";
    response.setEncoding("utf8");
    response.on("data", (chunk) => { responseBody += chunk; });
    response.on("end", () => {
      if (response.statusCode < 200 || response.statusCode >= 300) {
        process.stderr.write(
          `HTTP ${response.statusCode}: ${responseBody.slice(0, 500)}`
        );
        process.exitCode = 3;
        return;
      }
      process.stdout.write(responseBody);
    });
  });
  request.on("timeout", () => request.destroy(new Error("请求超时")));
  request.on("error", (error) => {
    process.stderr.write(`${error.code || "NETWORK_ERROR"}: ${error.message}`);
    process.exitCode = 4;
  });
  request.write(body);
  request.end();
});
"""


def _response_namespace(payload: dict) -> SimpleNamespace:
    """把 Node 返回的原始 JSON 映射为 SDK 风格对象，保留截断诊断字段。"""
    try:
        choice_data = payload["choices"][0]
        message_data = choice_data["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"兼容层无法解析响应: {exc}") from exc

    usage_data = payload.get("usage") or {}
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content=message_data.get("content"),
                reasoning_content=message_data.get("reasoning_content"),
            ),
            finish_reason=choice_data.get("finish_reason"),
        )],
        usage=SimpleNamespace(**usage_data),
        id=payload.get("id"),
        model=payload.get("model"),
    )


class _NodeChatCompletions:
    """当 openai SDK 因 _ssl DLL 无法导入时，用 Node HTTPS 做临时调用。"""

    def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def create(self, **request_data):
        node_request = json.dumps({
            "url": self.base_url + "/chat/completions",
            "body": request_data,
            "timeout_ms": max(1000, int(self.timeout * 1000)),
        }, ensure_ascii=False)
        child_env = os.environ.copy()
        child_env["OPENAI_API_KEY"] = self.api_key
        completed = subprocess.run(
            ["node", "-e", _NODE_HTTPS_CLIENT],
            input=node_request,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=self.timeout + 5,
            check=False,
            env=child_env,
        )
        if completed.returncode != 0:
            error_text = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(
                f"Node HTTPS 请求失败({completed.returncode}): "
                f"{error_text[:500]}")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"兼容层无法解析响应: {exc}") from exc
        return _response_namespace(payload)


class _ProxyChatCompletions:
    """通过本机 Node HTTPS 转发器完成临时调用。"""

    def __init__(self, api_key: str, proxy_url: str,
                 base_url: str, timeout: float) -> None:
        self.api_key = api_key
        self.proxy_url = proxy_url
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def create(self, **request_data):
        parsed_proxy = urlparse(self.proxy_url)
        if parsed_proxy.scheme != "http" or not parsed_proxy.hostname:
            raise RuntimeError("HETERO_OPENAI_PROXY_URL 必须是本机 HTTP 地址")
        request_body = json.dumps({
            "url": self.base_url + "/chat/completions",
            "body": request_data,
            "timeout_ms": max(1000, int(self.timeout * 1000)),
        }, ensure_ascii=False)
        connection = HTTPConnection(
            parsed_proxy.hostname,
            parsed_proxy.port or 80,
            timeout=self.timeout + 5,
        )
        connection.request(
            "POST",
            parsed_proxy.path or "/",
            body=request_body.encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        response_body = response.read().decode("utf-8", errors="replace")
        connection.close()
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(
                f"本机代理请求失败({response.status}): {response_body[:500]}")
        try:
            payload = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"兼容层无法解析响应: {exc}") from exc
        return _response_namespace(payload)


class _NodeOpenAI:
    """模拟 `openai.OpenAI(...).chat.completions.create(...)` 的最小接口。"""

    def __init__(self, *, api_key: str, base_url: str, timeout: float,
                 **kwargs) -> None:
        proxy_url = os.environ.get("HETERO_OPENAI_PROXY_URL", "").strip()
        if proxy_url:
            completions = _ProxyChatCompletions(
                api_key, proxy_url, base_url, timeout)
        else:
            completions = _NodeChatCompletions(api_key, base_url, timeout)
        self.chat = SimpleNamespace(completions=completions)


class _CurlChatCompletions:
    """用系统 curl 发起 HTTPS 请求，绕开便携运行时的 SSL DLL 问题。"""

    def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def create(self, **request_data):
        body_path = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", suffix=".json",
                    prefix="math-agent-llm-", delete=False) as body_file:
                body_path = body_file.name
                json.dump(request_data, body_file, ensure_ascii=False)

            curl_config = (
                f'header = "Authorization: Bearer {self.api_key}"\n'
                'header = "Content-Type: application/json"\n'
            )
            completed = subprocess.run(
                [
                    "curl.exe",
                    "-sS",
                    "--fail-with-body",
                    "--max-time",
                    str(max(1, int(self.timeout))),
                    "--config",
                    "-",
                    "--data-binary",
                    f"@{body_path}",
                    self.base_url + "/chat/completions",
                ],
                input=curl_config,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=self.timeout + 10,
                check=False,
            )
        finally:
            if body_path:
                try:
                    Path(body_path).unlink()
                except OSError:
                    pass

        if completed.returncode != 0:
            error_text = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(
                f"curl HTTPS 请求失败({completed.returncode}): "
                f"{error_text[:500]}")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"兼容层无法解析响应: {exc}") from exc
        return _response_namespace(payload)


class _CurlOpenAI:
    """模拟 openai.OpenAI 的最小 curl 客户端。"""

    def __init__(self, *, api_key: str, base_url: str, timeout: float,
                 **kwargs) -> None:
        completions = _CurlChatCompletions(api_key, base_url, timeout)
        self.chat = SimpleNamespace(completions=completions)


def install_temporary_openai_compat() -> str:
    """仅当 openai SDK 无法导入时安装 Node、代理或 curl 兼容模块。"""
    try:
        import openai  # noqa: F401
        return ""
    except Exception as exc:
        for module_name in list(sys.modules):
            if module_name == "openai" or module_name.startswith("openai."):
                del sys.modules[module_name]
        openai_module = types.ModuleType("openai")
        if os.environ.get("HETERO_OPENAI_PROXY_URL", "").strip():
            openai_module.OpenAI = _NodeOpenAI
        elif shutil.which("node"):
            # 新增：Schannel 凭据不可用时优先使用 Node 自带的 HTTPS 实现。
            openai_module.OpenAI = _NodeOpenAI
        else:
            openai_module.OpenAI = _CurlOpenAI
        sys.modules["openai"] = openai_module
        return f"{type(exc).__name__}: {exc}"


def main() -> int:
    if not os.environ.get("QWEN_API_KEY", "").strip():
        print("SKIP: 未设置 QWEN_API_KEY")
        return 0

    # config.py 仍要求主模型 Key；这里只提供导入占位值，不覆盖已有环境变量。
    os.environ.setdefault("DEEPSEEK_API_KEY", "hetero-live-test-import-only")
    ctypes_compat_error = install_temporary_ctypes_compat()
    openai_compat_error = install_temporary_openai_compat()

    sys.path.insert(0, str(AGENT_DIR))
    from verifier_hetero import QwenVLVerifier, build_verifier_chain
    from verify import SymPyVerifier

    annotations = json.loads(
        (BASE / "tests" / "benchmark" / "annotations.json").read_text(
            encoding="utf-8")
    )
    cases_by_id = {
        item["id"]: item for item in annotations.get("cases", [])
        if isinstance(item, dict) and item.get("id")
    }
    cases = [
        {
            "name": "A 正确旋转矩阵",
            "latex": cases_by_id["image_02"]["latex"],
            "cpp_path": BASE / "tests" / "benchmark" / "correct" / "image_02.cpp",
            "expected_verdict": "equivalent",
            "expected_aggregate": None,
        },
        {
            "name": "B LU 索引范围错误",
            "latex": cases_by_id["image_03"]["latex"],
            "cpp_path": (
                BASE / "tests" / "benchmark" / "incorrect" / "image_03.cpp"),
            "expected_verdict": "not_equivalent",
            "expected_aggregate": "not_equivalent",
        },
    ]

    if ctypes_compat_error:
        print(f"[INFO] 已启用临时 ctypes 兼容层：{ctypes_compat_error[:160]}")
    if openai_compat_error:
        print(f"[INFO] 已启用临时 OpenAI/Node 兼容层："
              f"{openai_compat_error[:160]}")

    sympy_verifier = SymPyVerifier()
    qwen_verifier = QwenVLVerifier()
    failures = []
    for case in cases:
        cpp_code = case["cpp_path"].read_text(
            encoding="utf-8", errors="replace")

        sympy_result = sympy_verifier.verify(case["latex"], cpp_code)
        qwen_result = qwen_verifier.verify(case["latex"], cpp_code)
        aggregate_result = build_verifier_chain(mode="serial").verify(
            case["latex"], cpp_code)

        verifier_statuses = {
            "sympy": aggregate_result.meta.get("sympy_verdict"),
        }
        hetero_details = aggregate_result.meta.get("hetero_verdicts", [])
        for hetero_detail in hetero_details:
            hetero_name = hetero_detail.get("meta", {}).get(
                "verifier", "hetero")
            verifier_statuses[hetero_name] = hetero_detail.get("status")

        print(f"\n[{case['name']}]")
        print(f"SymPy status: {sympy_result.status}")
        print(f"Qwen-VL status: {qwen_result.status}")
        print(f"Qwen-VL detail: {qwen_result.detail[:300]}")
        index_check = str(qwen_result.meta.get("index_check", "") or "")
        index_scopes = qwen_result.meta.get("index_scopes", {})
        print(f"Qwen-VL index_check: {index_check[:300] or '空'}")
        print(
            "Qwen-VL index_scopes: "
            f"latex_sums={len(index_scopes.get('latex_sums', []))}, "
            f"cpp_loops={len(index_scopes.get('cpp_loops', []))}"
        )
        print(f"aggregate status: {aggregate_result.status}")
        print(
            "aggregate verdict_label: "
            f"{aggregate_result.meta.get('verdict_label')}")
        print(f"各校验器 status: {verifier_statuses}")

        if qwen_result.status != case["expected_verdict"]:
            failures.append(
                f"{case['name']} Qwen 状态为 {qwen_result.status}，"
                f"期望 {case['expected_verdict']}"
            )
        if (case["expected_aggregate"] is not None
                and aggregate_result.status != case["expected_aggregate"]):
            failures.append(
                f"{case['name']} 汇总状态为 {aggregate_result.status}，"
                f"期望 {case['expected_aggregate']}"
            )
        if case["expected_verdict"] == "not_equivalent" and not index_check:
            failures.append(f"{case['name']} 缺少 index_check")

    if failures:
        print("\n[FAIL]")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("\n[PASS] A=equivalent；B=not_equivalent 且 index_check 非空")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
