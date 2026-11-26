# math-agent 贡献指南

本文档面向在 `D:\math-agent` 内维护代码的贡献者。当前项目是小型流水线，
优先做范围明确、可回归的增量修改。

## 1. 代码规范

### 1.1 运行环境

- Python 版本：3.12。
- 默认解释器：`D:\math-agent\.python312\python.exe`。
- 命令在 PowerShell 中执行。
- 不擅自切换框架、解析器、OCR 引擎或校验服务。

### 1.2 Python 风格

- 公共函数和关键内部函数使用类型注解。
- 变量、函数和文件名使用直白、语义明确的名称。
- 注释使用中文，只解释核心逻辑、边界条件和容易误改的地方。
- 保持现有缩进和文件结构，不做与任务无关的全文件格式化。
- 优先复用项目现有 `OcrResult`、`LLMResult`、`CheckResult` 和
  `VerifierBase`，不要为单个功能另建一套相似数据结构。

### 1.3 增量修改

- 只改完成任务必需的代码行。
- 修改本地文件前先在同目录创建
  `文件名.bak.<YYYYMMDD_HHMMSS>` 备份。
- 不覆盖、不删除用户已有改动。
- 大范围重构、批量替换、删除文件、安装依赖、系统级命令必须先说明影响并
  获得确认。
- 修改处可使用简短中文注释标记「新增」「修改」，方便对照。

### 1.4 异常与密钥

- 网络、模型、图片解码和缓存异常不能直接穿透到主流程。
- OCR、LLM 和异构校验应返回结构化失败结果，并为失败填写清晰的
  `error_code`。
- API Key 只从环境变量读取，不写源码、测试 JSON、Markdown、日志或报告。
- 文档中的密钥统一写 `<your-key>`。
- 不提交真实 Key、Cookie、Token 或个人路径之外的敏感信息。

### 1.5 依赖

- 默认不新增第三方依赖。
- 必须新增时，先说明用途、版本、安装体积、许可证和回退方案。
- 不修改全局 Python 环境；使用项目内 `.python312` 或明确指定的虚拟环境。

## 2. 如何新增 Verifier

### 2.1 选择基类

普通本地校验器继承：

```python
from agent.verify import CheckResult, VerifierBase
```

OpenAI Chat Completions 兼容的远程校验器优先继承：

```python
from agent.verifier_hetero import HeteroVerifier
```

### 2.2 实现统一接口

```python
def verify(
    self,
    latex_expr: str,
    cpp_expr_or_code: str,
    context: dict | None = None,
) -> CheckResult:
    ...
```

约束：

- 成功判等价返回 `status="equivalent"` 且 `ok=True`。
- 明确错误返回 `not_equivalent`、`shape_mismatch` 等不一致状态。
- 证据不足返回 `unknown`，不要猜测为 `equivalent`。
- 网络或执行故障返回 `error`，并保留脱敏后的 `meta`。
- 不把异常直接抛给 `VerifierChain`，链虽然会兜底，但会丢失更精确的上下文。

### 2.3 继承 `HeteroVerifier`

至少配置：

```python
class NewProviderVerifier(HeteroVerifier):
    name = "new-provider"
    provider_name = "NewProvider"
    api_key_env = "NEW_PROVIDER_API_KEY"
    model_config_name = "NEW_PROVIDER_MODEL"
    base_url_env = "NEW_PROVIDER_BASE_URL"
    default_base_url = "https://example.invalid/v1"
```

同时需要在 `agent/config.py` 增加对应环境变量配置，并确保：

- 缺失 Key 时抛 `VerifierConfigurationError`。
- 请求固定低温度，限制输出 token。
- JSON 解析失败收敛为 `unknown`。
- 网络和鉴权失败收敛为 `error`。
- `meta` 不保存 Key。

### 2.4 注册校验器

在 `agent.verifier_hetero.build_verifier_chain()` 中增加开关和条件分支：

```python
include_new_provider: Optional[bool] = None
```

规则应与现有 GLM/Qwen 一致：

- `False`：强制关闭。
- `None`：有 Key 时自动启用。
- `True`：显式启用，但仍需检查配置。

### 2.5 接入 CLI 或模式

如果要通过 CLI 选择新模式：

1. 在 `agent/main.py` 的 `Pipeline.__init__` 校验模式枚举。
2. 在 `build_parser()` 增加选项。
3. 在 `_get_verifier()` 构造对应链。
4. 更新校验缓存 scope，确保新模式不会复用旧模式结果。
5. 更新 `API.md`、`README.md` 和 `TESTING.md`。

当前 CLI 只显式开放 `sympy-only` 和 `hybrid`；无参数时为内部 `auto`。

### 2.6 测试要求

至少补以下测试：

- 基类契约和输入校验。
- 全一致、全不一致、冲突、unknown、error 五种汇总。
- 缺 Key、非法 JSON、超时和异常响应。
- 并行模式的结果顺序稳定。
- 缓存 scope 隔离。

若接入外部 API，另做小样本对比实验并更新
`output/verifier_comparison.md`，不要只依赖在线手工观察。

## 3. 如何新增 OCR 后端

### 3.1 实现统一返回

后端应提供：

```python
def recognize(image_path: str) -> OcrResult:
    ...
```

必须复用 `agent.ocr_pix.OcrResult`。失败时返回：

```python
OcrResult(
    ok=False,
    engine="backend-name",
    error="可读原因",
    error_code="structured_code",
)
```

不要把网络异常、模型加载异常或图片解码异常直接抛给 `Pipeline`。

### 3.2 接入 fallback 链

在 `Pipeline._ocr_uncached()` 中按已有顺序接入：

- `vision`
- `pix`
- `both`
- `auto`

若新后端进入 `auto`，明确它在哪一步执行、何时回退、最终错误如何合并。

### 3.3 缓存 scope

OCR 缓存键是图片内容哈希加 `scope`。`Pipeline.ocr()` 当前 scope 为：

```python
f"{self.engine}|{prompt or ''}"
```

新增后端时，scope 必须包含后端标识或后端组合。否则同一张图片在不同 OCR
后端之间可能错误复用结果。

### 3.4 测试要求

- 正常图片返回 `ok=True`。
- 缺失文件返回 `image_unreadable` 或等价结构化错误。
- 非公式图片不会被编造成公式。
- 首次失败后的 fallback 顺序正确。
- 缓存不跨后端复用。
- 文档中的引擎枚举、CLI 参数和失败语义同步更新。

## 4. 修改主流水线的注意事项

- `Pipeline.run()` 的顶层报告字段保持稳定，尤其是 `ok`、`steps`、`cache`、
  `verifier_mode` 和 `error`。
- 新增报告字段应保持 JSON 可序列化。
- 不要改变退出码约定：
  `0` 通过、`1` 未通过或运行失败、`2` 参数错误、`130` 中断。
- 修改缓存键时要考虑旧缓存是否仍有效；必要时更换 scope 或提醒清缓存。
- 修改 OCR 顺序时同步更新 `API.md` 和 `README.md`。
- 修改 verifier 汇总规则时同步更新 `ARCHITECTURE.md`、`API.md` 和测试。

## 5. 测试与回归

### 5.1 语法检查

```powershell
Get-ChildItem agent,tests -Recurse -Filter *.py | ForEach-Object {
  .\.python312\python.exe -m py_compile $_.FullName
}
```

### 5.2 单元测试

```powershell
$env:DEEPSEEK_API_KEY = '<your-key>'
.\.python312\python.exe tests\test_cache.py
.\.python312\python.exe tests\test_edge_cases.py
.\.python312\python.exe tests\test_index_scopes.py
.\.python312\python.exe tests\test_matrix_parse.py
```

`tests/test_verifier_chain.py` 当前仍包含旧离线 stub 断言，直接运行可能尝试
真实 GLM 网络调用。修复该测试前，不应把它描述为完全离线测试。

### 5.3 Benchmark 回归

```powershell
$env:DEEPSEEK_API_KEY = '<your-key>'
.\.python312\python.exe tests\benchmark\run_benchmark.py
```

重点比较：

- 总体命中率
- 正类召回
- 负类召回
- 边界拒绝率
- fail 类 precision、recall、F1
- 明确判错数，以及 unknown/error 数

只比较最终 `fail` 数量不够，因为 `unknown` 和 `error` 也可能让 `ok=False`。

### 5.4 校验器对比

仅在允许联网时执行：

```powershell
$env:DEEPSEEK_API_KEY = '<your-key>'
.\.python312\python.exe tests\benchmark\compare_verifiers.py
```

检查 `output/verifier_comparison.md` 是否如实区分“明确判错”和“无法判定”。

### 5.5 HTML 报告

```powershell
.\.python312\python.exe output\generate_html_report.py --report-format both
```

检查 `output/report.html` 能打开、图片路径有效、中文无乱码、移动端没有明显
横向溢出。

## 6. PR 前检查清单

- [ ] 修改前已创建 `*.bak.<时间戳>` 备份。
- [ ] 只修改任务必需文件，没有无关格式化。
- [ ] Python 文件通过 `py_compile`。
- [ ] 可离线单元测试通过。
- [ ] 需要联网的测试已明确告知并提供运行条件。
- [ ] Benchmark 无明显回归，或已在 PR 说明中解释变化。
- [ ] 新增接口、参数和失败语义已写入 `API.md`。
- [ ] 新增或调整测试流程已写入 `TESTING.md`。
- [ ] README 中的 Windows/PowerShell 命令可复现。
- [ ] 搜索并确认没有真实 Key、Token、Cookie 或敏感日志。
- [ ] 报告和文档中的密钥占位统一为 `<your-key>`。
- [ ] `output/verifier_comparison.md` 与 `output/report.html` 链接仍有效。

