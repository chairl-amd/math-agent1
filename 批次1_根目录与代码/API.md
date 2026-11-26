# math-agent API 参考

本文档按当前源码记录公开接口。除特别说明外，所有路径均位于
`D:\math-agent\`，Python 版本以项目便携解释器
`D:\math-agent\.python312\python.exe` 为准。

> 前置条件：`agent/config.py` 在导入时就会检查
> `DEEPSEEK_API_KEY`。只要模块直接或间接导入了 `config`，缺少该环境变量
> 就会抛出 `RuntimeError("请设置 DEEPSEEK_API_KEY")`。即使只使用
> `--mode pix` 或 `--no-llm`，当前 `main.py` 仍受这一检查影响。
> `agent/cache.py` 不依赖 `config`，可以在没有 Key 时单独测试。

## 1. `agent/config.py`

### 1.1 路径常量

| 常量 | 当前值或来源 | 含义 |
|---|---|---|
| `BASE_DIR` | `Path(config.py).resolve().parent.parent` | 项目根目录 `D:\math-agent` |
| `AGENT_DIR` | `BASE_DIR / "agent"` | 核心代码目录 |
| `OUTPUT_DIR` | `BASE_DIR / "output"` | 报告与运行产物目录 |
| `DOCS_DIR` | `BASE_DIR / "docs"` | 文档目录 |
| `TESTS_DIR` | `BASE_DIR / "tests"` | 测试目录 |
| `TESTS_CPP_DIR` | `BASE_DIR / "tests" / "cpp"` | C++ 样例目录 |
| `TESTS_FORMULA_DIR` | `BASE_DIR / "tests" / "formula"` | 公式图片目录 |
| `THIRD_PARTY_DIR` | `BASE_DIR / "third_party"` | 第三方代码目录 |
| `GLM_DIR` | `THIRD_PARTY_DIR / "glm"` | GLM 目录 |
| `VENV_DIR` | `BASE_DIR / "venv"` | 兼容常量；当前机器实际解释器在 `.python312` |

### 1.2 API 与模型配置

| 名称 | 环境变量或固定值 | 说明 |
|---|---|---|
| `API_KEY` | `DEEPSEEK_API_KEY` | DeepSeek Key，导入时为空会直接报错 |
| `BASE_URL` | `OPENAI_BASE_URL` | 默认 `https://api.openai-next.com/v1` |
| `DEEPSEEK_API_KEY` | `API_KEY` 的兼容别名 | 旧代码仍可使用 |
| `DEEPSEEK_BASE_URL` | `BASE_URL` 的兼容别名 | 环境变量层面实际读取的是 `OPENAI_BASE_URL` |
| `TEXT_MODEL` | `deepseek-v4-1-flash-261017` | 文本翻译与校验模型 |
| `VISION_MODEL` | `deepseek-v4-1-flash-261017` | 视觉 OCR 模型 |
| `MODEL_TEXT` | `TEXT_MODEL` 的兼容别名 | 旧代码兼容 |
| `MODEL_VISION` | `VISION_MODEL` 的兼容别名 | 旧代码兼容 |
| `MODELS` | 字典 | `vision`、`text`、`planner`、`verifier` 映射 |
| `REQUEST_TIMEOUT` | `DEEPSEEK_TIMEOUT`，默认 `120` | OpenAI 客户端超时秒数 |
| `MAX_RETRIES` | `DEEPSEEK_MAX_RETRIES`，默认 `3` | 额外重试次数 |
| `TEMPERATURE` | 固定 `0.0` | 默认采样温度 |

异构校验配置：

| 名称 | 环境变量或默认值 | 说明 |
|---|---|---|
| `GLM_API_KEY` | `GLM_API_KEY` | 可选 |
| `QWEN_API_KEY` | `QWEN_API_KEY` | 可选 |
| `GLM_VERIFIER_MODEL` | `GLM_VERIFIER_MODEL`，默认 `glm-4v` | GLM 校验模型 |
| `QWEN_VERIFIER_MODEL` | `QWEN_VERIFIER_MODEL`，默认 `qwen-max` | Qwen 校验模型 |
| `GLM_BASE_URL` | `GLM_BASE_URL` | 默认 `https://open.bigmodel.cn/api/paas/v4` |
| `QWEN_BASE_URL` | `QWEN_BASE_URL` | 默认 DashScope OpenAI 兼容地址 |
| `HETERO_VERIFY_ENABLED` | `HETERO_VERIFY_ENABLED`，默认启用 | `0`、`false` 或 `False` 表示关闭 |
| `HETERO_VERIFY_TIMEOUT` | `HETERO_VERIFY_TIMEOUT`，默认 `60` | 单次异构校验超时秒数 |

### 1.3 函数

#### `ensure_dirs() -> None`

创建 `OUTPUT_DIR`、`DOCS_DIR`、`TESTS_CPP_DIR`、`TESTS_FORMULA_DIR` 和
`GLM_DIR`。使用 `parents=True, exist_ok=True`；文件系统异常由调用方处理。

#### `get_api_key(required: bool = True) -> str`

返回导入时读取到的 `API_KEY`。`required` 参数为兼容保留，当前函数内部不再
根据它做二次检查。

#### `get_base_url() -> str`

返回 `BASE_URL`。

### 1.4 可能异常

- 缺少 `DEEPSEEK_API_KEY`：导入模块时抛出 `RuntimeError`。
- `DEEPSEEK_TIMEOUT`、`DEEPSEEK_MAX_RETRIES` 或
  `HETERO_VERIFY_TIMEOUT` 不是合法整数：导入时可能抛出 `ValueError`。

## 2. `agent/cache.py`

缓存使用两个 pickle 文件：

- `.cache/ocr.pkl`
- `.cache/verify.pkl`

### 2.1 缓存键与隔离

| 缓存类型 | 键的组成 |
|---|---|
| OCR | 图片文件内容 SHA-256 + `0x00` + `scope`，再取 SHA-256 |
| 校验 | `latex + 0x00 + code + 0x00 + scope` 的 SHA-256 |

OCR 图片键先通过文件状态辅助加速，但最终依据图片内容，因此相同内容的不同
路径共享缓存。`scope` 用于隔离不同引擎、提示词或校验模式。`Pipeline` 当前使用：

- OCR scope：`"{engine}|{prompt or ''}"`
- 校验 scope：`"verifier:{verifier_mode}"`

### 2.2 `MathAgentCache`

#### `MathAgentCache()`

创建独立缓存实例，内部包含 OCR 与校验两组数据、加载状态、线程锁和统计计数。

#### `get_ocr(image_path, scope="") -> Any | None`

读取 OCR 缓存。成功返回深拷贝；未命中或任何异常返回 `None`。

#### `put_ocr(image_path, result, scope="") -> bool`

写入 OCR 缓存。成功返回 `True`；禁用、键生成失败或写入失败返回 `False`。

#### `get_verify(latex, code, scope="") -> Any | None`

读取校验缓存。成功返回深拷贝；未命中或异常返回 `None`。

#### `put_verify(latex, code, result, scope="") -> bool`

写入校验缓存。成功返回 `True`；失败返回 `False`。

#### `stats() -> dict`

返回统计快照。主要结构如下：

```python
{
    "disabled": False,
    "ocr": {"hits": 0, "misses": 0, "writes": 0, "errors": 0},
    "verify": {"hits": 0, "misses": 0, "writes": 0, "errors": 0},
    "total": {"hits": 0, "misses": 0, "writes": 0, "errors": 0},
    "hits": 0,       # 顶层别名
    "misses": 0,
    "writes": 0,
    "errors": 0,
}
```

#### `format_stats(stats_data=None) -> str`

把统计快照格式化成单行中文摘要。`stats_data` 为空时读取当前统计；禁用时返回
`"已禁用"`。

#### `clear() -> None`

清空当前实例的内存数据，并删除两个缓存文件。损坏文件删除失败只增加错误计数。
当前实现不会把已有的 `_stats` 计数归零。

### 2.3 模块级函数

模块级 `cache = MathAgentCache()` 是默认单例，以下函数都转发到它：

```python
get_ocr(image_path, scope="")
put_ocr(image_path, result, scope="")
get_verify(latex, code, scope="")
put_verify(latex, code, result, scope="")
stats()
format_stats(stats_data=None)
clear()
```

### 2.4 禁用与异常行为

- 只有 `MATH_AGENT_CACHE` 的明确值 `"0"` 会禁用缓存。
- 禁用后读操作返回 `None`，写操作返回 `False`，不增加命中、未命中或写入计数。
- 损坏 pickle 会改名为 `*.corrupt.<时间戳>`，随后以空缓存继续运行。
- 写入采用同目录临时文件加原子替换。
- 缓存公开 API 不会把读取、序列化或文件系统异常抛给主流程。

## 3. `agent/ocr_pix.py` 与 `agent/ocr_vision.py`

### 3.1 `OcrResult`

`agent/ocr_pix.py` 定义统一 OCR 结果，视觉模块直接复用。

| 字段 | 类型 | 含义 |
|---|---|---|
| `latex` | `str` | 识别出的 LaTeX |
| `ok` | `bool` | 是否成功 |
| `engine` | `str` | `"pix2text"`、`"vision"` 等 |
| `error` | `str` | 失败原因 |
| `meta` | `dict` | 尝试次数、耗时、token、链路对比等 |
| `error_code` | `str` | 结构化错误码 |

常见 `error_code`：

- `model_unavailable`
- `image_unreadable`
- `empty_result`
- `model_error`
- `missing_api_key`
- `encode_failed`
- `not_latex`
- `timeout`
- `api_error`

### 3.2 `PixOcr`

#### `PixOcr(device: Optional[str] = None)`

创建 Pix2Text 封装。模型在首次识别时懒加载，实例内复用。

#### `available() -> bool`

尝试加载模型；可用返回 `True`，不可用返回 `False`，不抛异常。

#### `recognize(src, *, retries=1, preprocess=True) -> OcrResult`

参数：

- `src`：文件路径、`bytes`、`bytearray` 或 `PIL.Image.Image`。
- `retries`：额外重试次数，总尝试次数为 `retries + 1`。
- `preprocess`：首轮是否做去白边、缩放和轻度去噪。

返回：`OcrResult`。模型缺失、图片不可读、结果为空或识别异常都收敛为
`ok=False`，并填写 `error_code`。正常识别会移除结果外层 `$$`。

### 3.3 `recognize_with_fallback(src, vision_recognizer=None) -> OcrResult`

该辅助函数先执行本地 Pix2Text，失败后调用可选视觉识别器。视觉识别器可返回
`OcrResult` 或字符串。主流水线的默认顺序相反，`Pipeline.ocr()` 是 Vision
优先、Pix2Text 兜底。

### 3.4 `VisionOcr`

#### `VisionOcr(model=None, api_key=None, base_url=None, timeout=None, temperature=None)`

参数为空时读取 `config.VISION_MODEL`、`config.API_KEY`、
`config.BASE_URL`、`config.REQUEST_TIMEOUT` 和 `config.TEMPERATURE`。

#### `recognize(src, *, prompt=VISION_PROMPT, retries=None) -> OcrResult`

把图片编码为 base64 data URI，调用 OpenAI 兼容 Chat Completions 视觉接口。
`retries` 为空时使用 `config.MAX_RETRIES`。

失败语义：

- 缺少 Key：`missing_api_key`
- 图片编码失败：`encode_failed`
- 模型返回空内容：`empty_result`
- 返回中文普通文本等非公式内容：`not_latex`
- 超时：`timeout`
- 其他 API 异常：`api_error`

所有失败都返回 `OcrResult(ok=False)`，不向调用方抛出网络异常。

### 3.5 其他公开辅助函数

- `to_image(src) -> PIL.Image.Image`
- `preprocess_image(img, *, min_side=800, max_side=2200, denoise=True)`
- `encode_image_base64(src, *, max_side=1600, fmt="JPEG", quality=88) -> str`
- `clean_latex(text) -> str`
- `looks_like_latex(text) -> bool`

## 4. `agent/llm.py`

### 4.1 `LLMResult`

| 字段 | 类型 | 含义 |
|---|---|---|
| `text` | `str` | 模型返回正文 |
| `ok` | `bool` | 是否成功 |
| `error` | `str` | 失败原因 |
| `meta` | `dict` | 耗时、尝试次数、finish reason、token 用量等 |
| `error_code` | `str` | `missing_api_key`、`empty_content`、`api_error`、`timeout` 等 |

当响应包含 usage 时，`meta.tokens` 结构为：

```python
{"prompt": 0, "completion": 0, "total": 0}
```

多次尝试时，代码会汇总各次 token，并在 `meta.usage_capture` 标记
`complete`、`partial` 或 `unavailable`。

### 4.2 `TextLLM`

#### `TextLLM(model=None, api_key=None, base_url=None, timeout=None)`

参数为空时读取 `config`。客户端在首次请求时创建。

#### `chat(messages, *, system=None, temperature=None, retries=None, max_tokens=None) -> LLMResult`

参数：

- `messages`：OpenAI 兼容消息列表。
- `system`：可选系统提示，会插到消息列表开头。
- `temperature`：默认 `config.TEMPERATURE`。
- `retries`：额外重试次数，默认 `config.MAX_RETRIES`。
- `max_tokens`：默认 `2048`。

返回 `LLMResult`。客户端初始化失败、空正文、超时和 API 异常都转换成结构化
失败结果。空正文会按重试策略继续。

#### `translate_cpp_to_math(code, language="cpp") -> LLMResult`

从 C++ 代码提取核心数学表达式。输入先截断到 20000 字符。首次预算
`8192` token；遇到截断时使用 `16384` token 重试；仍失败时按最多 8 段、
每段约 120 行切块翻译。返回文本可能包含 Markdown 代码围栏，调用方可用
`strip_code_fence()` 清理。

#### `translate_math_to_cpp(latex) -> LLMResult`

把 LaTeX 公式转换为 C++ 代码片段。默认最多两次尝试，单次预算 `8192`
token。

#### `normalize_latex(latex) -> LLMResult`

要求模型只修复 OCR 噪声和 LaTeX 语法，不改变数学含义。

#### `explain(latex, style="面试") -> LLMResult`

生成不超过约 200 字的解释，`style` 会写入提示词。

#### `strip_code_fence(text) -> str`

去掉完整的 Markdown 代码围栏；不匹配时返回原文本的 `strip()` 结果。

## 5. `agent/verify.py`

### 5.1 `DiffPoint`

字段：

- `point: dict`：采样点
- `lhs: Any`：左侧值
- `rhs: Any`：右侧值
- `rel_delta: float | None`：相对误差

`as_dict()` 返回可序列化字典。

### 5.2 `CheckResult`

```python
CheckResult(
    ok: bool,
    status: str,
    lhs: str = "",
    rhs: str = "",
    detail: str = "",
    diff_points: list = [],
    meta: dict = {},
)
```

主要 `status`：

- `equivalent`
- `numeric_equivalent`
- `not_equivalent`
- `parse_error`
- `shape_mismatch`
- `unknown`
- `error`
- `needs_human_review`
- `translation_failed`

方法：

- `to_dict() -> dict`
- `to_markdown() -> str`

### 5.3 `VerifierBase`

抽象基类，子类必须实现：

```python
verify(
    latex_expr: str,
    cpp_expr_or_code: str,
    context: Optional[dict] = None,
) -> CheckResult
```

`Verdict` 是 `CheckResult` 的别名。

### 5.4 `SymPyVerifier`

```python
SymPyVerifier().verify(latex_expr, cpp_expr_or_code, context=None)
```

数学逻辑保持不变。若 `context` 同时包含 `preparsed_lhs_expr` 和
`preparsed_rhs_expr`，直接调用 `compare_expressions()`；否则调用
`verify_latex_vs_code()`。

### 5.5 解析函数

#### `latex_to_expr(tex) -> tuple[Any | None, str]`

把 LaTeX 转成 SymPy 表达式或矩阵。优先使用 SymPy `parse_latex`，失败后使用
轻量文本转换和 `parse_expr`。常见矩阵环境 `bmatrix`、`pmatrix`、`matrix`、
`vmatrix`、`Bmatrix`、`array` 会先整体解析。

失败返回 `(None, 错误说明)`，不抛解析异常。

#### `parse_python_expr(code, symbols=None) -> tuple[Any | None, str]`

解析 Python/C++ 风格数学表达式。未知标识符可自动作为 SymPy 符号；失败返回
`(None, 错误说明)`。

### 5.6 比较函数

#### `compare_expressions(a, b, symbols=None, *, tol=1e-8, samples=24) -> CheckResult`

参数 `a`、`b` 必须是 SymPy 标量或矩阵。标量依次尝试：

1. `simplify(a - b) == 0`
2. `expand`、`trigsimp`、`factor`、`cancel`
3. 随机数值采样，默认 24 个有效点

无法比较的对象返回 `unknown`；内部异常返回 `error`。

#### `verify_latex_vs_code(latex, code_expr, symbols=None, *, tol=1e-8, samples=24) -> CheckResult`

端到端解析并比较 LaTeX 与代码表达式。任一侧解析失败时返回 `unknown`，表示
无法判定，不等同于已证明错误。

#### `compare_matrices(a, b, symbols=None, *, tol=1e-8, samples=12) -> CheckResult`

先比较矩阵形状，再逐元素比较。形状不同返回 `shape_mismatch`；某元素失败时
在 `meta` 中记录 `element` 和 `shape`。

#### `extract_index_scopes(latex_text, cpp_code) -> dict`

提取 LaTeX `\sum` 和 C++ `for` 循环的索引变量、上下界及所在行：

```python
{
    "latex_sums": [
        {"var": "k", "lower": "0", "upper": "j", "raw": "..."}
    ],
    "cpp_loops": [
        {
            "var": "k",
            "lower": "0",
            "upper": "j",
            "op": "<",
            "raw": "for (...) ",
        }
    ],
    "context": {"latex_line": "", "cpp_line": ""},
}
```

输入类型错误或解析异常时返回同样结构、各列表为空的结果。

### 5.7 其他公开函数

- `numeric_compare(a, b, symbols=None, *, samples=24, tol=1e-8, seed=0)`
- `format_report(results: list) -> str`
- `save_report(results: list, path: str) -> str`

## 6. `agent/verifier_hetero.py`

### 6.1 `HeteroVerifier`

```python
HeteroVerifier(
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: Optional[float] = None,
)
```

子类通过以下类属性选择提供方配置：

- `name`
- `provider_name`
- `api_key_env`
- `model_config_name`
- `base_url_env`
- `default_base_url`

缺少 Key 或模型名时抛 `VerifierConfigurationError`。该异常发生在构造阶段，
不会被误报为 `equivalent`。

#### `verify(latex_expr, cpp_expr_or_code, context=None) -> CheckResult`

调用 OpenAI Chat Completions 兼容接口，`temperature=0`、`max_tokens=1024`。
模型必须返回包含以下字段的 JSON：

```json
{
  "verdict": "equivalent|not_equivalent|unknown",
  "reason": "理由",
  "diff": "关键差异",
  "index_check": "索引范围检查"
}
```

失败语义：

- 输入不是字符串：`unknown`
- JSON 无效或 `verdict` 非法：`unknown`
- 网络、超时、鉴权等调用异常：`error`

`meta` 包含 `provider`、`model`、`network_used`、`elapsed`、`usage`、
`evidence`、`index_check` 和 `index_scopes`。响应证据会截断到 500 字符并
移除当前 Key。

### 6.2 内置子类

| 类 | `name` | Key | 模型配置 | 服务地址 |
|---|---|---|---|---|
| `DeepSeekVerifier` | `deepseek` | `DEEPSEEK_API_KEY` | `TEXT_MODEL` | `DEEPSEEK_BASE_URL` 别名 |
| `GLM4VVerifier` | `glm-4v` | `GLM_API_KEY` | `GLM_VERIFIER_MODEL` | `GLM_BASE_URL` |
| `QwenVLVerifier` | `qwen-vl` | `QWEN_API_KEY` | `QWEN_VERIFIER_MODEL` | `QWEN_BASE_URL` |

DeepSeek 的地址别名最终来自 `OPENAI_BASE_URL`，因此当前设置
`OPENAI_BASE_URL` 才会影响默认网关。

### 6.3 `VerifierChain`

```python
VerifierChain(
    verifiers: Iterable[VerifierBase],
    *,
    mode: str = "serial",
    max_workers: Optional[int] = None,
)
```

参数与异常：

- `verifiers` 不能为空，否则 `ValueError`
- 元素必须继承 `VerifierBase`，否则 `TypeError`
- `mode` 只能是 `serial` 或 `parallel`
- `max_workers` 必须是正整数

`verify(latex_expr, cpp_expr_or_code, context=None)` 会隔离单个校验器异常，
再把全部结果交给 `aggregate_verdicts()`。

### 6.4 `aggregate_verdicts(results, *, chain_mode="serial") -> CheckResult`

汇总规则：

| 分支情况 | 汇总 `status` | `ok` |
|---|---|---|
| 全部一致 | `equivalent` | `True` |
| 存在不一致，且没有一致 | `not_equivalent` | `False` |
| 一致与不一致同时存在 | `needs_human_review` | `False` |
| 没有不一致，但至少一个 `unknown`/`error` | `unknown` | `False` |
| 空结果或返回值类型错误 | `error` | `False` |

`meta.verdict_label` 使用 `HIGH_CONFIDENCE`、`CONFLICT`、
`CONFIRMED_ERROR`、`SYMPY_FAIL` 或 `UNKNOWN`。

### 6.5 `build_verifier_chain(...) -> VerifierBase`

```python
build_verifier_chain(
    *,
    mode: str = "serial",
    include_deepseek: Optional[bool] = None,
    include_glm: Optional[bool] = None,
    include_qwen: Optional[bool] = None,
    max_workers: Optional[int] = None,
) -> VerifierBase
```

规则：

1. 始终先放入 `SymPyVerifier`。
2. `HETERO_VERIFY_ENABLED=False` 时立即返回纯 SymPy。
3. `include_deepseek=True` 且 DeepSeek Key 非空时追加 DeepSeek 校验器。
4. `include_glm` 为 `False` 时关闭；为 `None` 时根据 Key 自动决定。
5. `include_qwen` 与 GLM 规则相同。
6. 只有一个校验器时直接返回 `SymPyVerifier`，否则返回 `VerifierChain`。
7. 任一异构校验器构造失败时跳过该分支，保留其余链。

`mode` 非法时抛 `ValueError`。

## 7. `agent/main.py`

### 7.1 `Pipeline`

#### `Pipeline(engine="auto", use_llm=True, verbose=False, direction="formula2code", verifier_mode="auto")`

参数：

- `engine`：`auto`、`vision`、`pix` 或 `both`
- `use_llm`：是否允许翻译；最终还会与 `config.API_KEY` 共同决定
- `verbose`：启用详细日志
- `direction`：`formula2code` 或 `code2formula`
- `verifier_mode`：`auto`、`sympy-only` 或 `hybrid`

`verifier_mode` 非法时抛 `ValueError`。

当前实现只显式校验 `verifier_mode`。`engine` 若不是 `vision`、`pix` 或
`both`，OCR 分支会走默认 `auto` 逻辑；`direction` 也建议只使用
`formula2code` 或 `code2formula`。

#### `ocr(image_path, *, prompt=None) -> OcrResult`

先查 OCR 缓存，再执行未缓存链路：

- `vision`：只走视觉 OCR
- `pix`：只走 Pix2Text
- `both`：两条链路都执行，结果写入 `meta.comparison`
- `auto`：Vision 优先，失败后 Pix2Text

成功结果写入缓存，并在 `meta.cache` 标记 `hit` 或 `miss`。

#### `run(*, image=None, latex=None, cpp_path=None, expect=None, direction=None, translate_formula=False) -> dict`

执行流水线并返回报告字典，不直接写报告文件。常见字段：

- `timestamp`
- `engine`
- `verifier_mode`
- `use_llm`
- `direction`
- `inputs`
- `steps`
- `ok`
- `elapsed`
- `cache`
- `error`（中断时）

`steps` 可包含 `ocr`、`latex`、`translate_math`、`translate_cpp` 和
`checks`。

注意：只有显式传入 `--direction formula2code` 时才调用公式转 C++；仅使用
默认值不会触发表述转换。直接读取不存在的 C++ 路径等文件系统异常可能从
`run()` 传播，CLI 会转换为退出码 `1`。

### 7.2 CLI 参数

| 参数 | 可选值或默认值 | 含义 |
|---|---|---|
| `-i`, `--image` | 路径 | 公式或代码图片 |
| `-l`, `--latex` | 字符串 | 直接提供 LaTeX，跳过 OCR |
| `-c`, `--cpp` | 路径 | C++ 源码，供代码转公式或对照 |
| `-e`, `--expect` | 字符串 | 期望数学表达式，Python/SymPy 语法 |
| `--mode` | `vision`、`pix`、`both` | 面向演示的 OCR 链路选择 |
| `--direction` | `formula2code`、`code2formula`，默认前者 | 翻译方向 |
| `--engine` | `auto`、`vision`、`pix`，默认 `auto` | 兼容旧命令的 OCR 参数 |
| `--verifier-mode` | `sympy-only`、`hybrid` | 显式校验模式；不填时为 `auto` |
| `--out` | 默认 `output` | 报告输出目录 |
| `--no-llm` | 开关 | 禁用 LLM 翻译 |
| `--json` | 开关 | 标准输出 JSON 报告 |
| `-v`, `--verbose` | 开关 | 详细日志和三段式终端展示 |

`--mode` 与 `--engine` 同时出现且值不一致时，CLI 返回参数错误。

### 7.3 校验模式

| `verifier_mode` | 构造方式 | 是否联网 |
|---|---|---|
| `sympy-only` | `SymPyVerifier()` | 否 |
| `hybrid` | `build_verifier_chain(mode="serial", include_deepseek=True)` | 有 DeepSeek Key 时会联网 |
| `auto` | `build_verifier_chain(mode="serial")` | GLM/Qwen Key 存在时会联网 |

三种模式使用不同的校验缓存 scope。

### 7.4 退出码

| 退出码 | 含义 |
|---:|---|
| `0` | 报告 `ok=True` |
| `1` | 报告 `ok=False`，或流水线发生未预期异常 |
| `2` | 参数、输入路径、输出目录等用户输入错误 |
| `130` | 收到 `KeyboardInterrupt` |

### 7.5 报告辅助函数

- `build_markdown(report: dict) -> str`
- `build_verbose_sections(report: dict) -> str`
- `build_parser() -> argparse.ArgumentParser`

## 8. 相关文档

- [README.md](README.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [TESTING.md](TESTING.md)
- [CONTRIBUTING.md](CONTRIBUTING.md)
- [output/verifier_comparison.md](output/verifier_comparison.md)
- [output/report.html](output/report.html)
