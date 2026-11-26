# math-agent 测试指南

本文档说明当前测试集、运行命令、指标口径、新增用例步骤和常见问题。所有命令
均在 `D:\math-agent` 下使用 PowerShell 执行。

## 1. 测试集结构

```text
tests/
├── benchmark/
│   ├── annotations.json
│   ├── correct/
│   ├── incorrect/
│   ├── boundary/
│   ├── structural_errors/
│   │   ├── cases.json
│   │   ├── make_images.py
│   │   ├── *.tex
│   │   ├── *.cpp
│   │   └── *.png
│   ├── run_benchmark.py
│   └── compare_verifiers.py
├── test_cache.py
├── test_edge_cases.py
├── test_index_scopes.py
├── test_matrix_parse.py
└── test_verifier_chain.py
```

### 1.1 Benchmark 图片用例

当前 `annotations.json` 共 7 个图片用例：

| 分类 | 用例 | 数量 |
|---|---|---:|
| `correct` | `image_01`、`image_02`、`image_06` | 3 |
| `incorrect` | `image_03`、`image_04`、`image_07` | 3 |
| `boundary` | `image_05` | 1 |

每条用例由同目录中的 `.png` 和 `.cpp` 组成。

### 1.2 `annotations.json` 字段

顶层字段：

| 字段 | 含义 |
|---|---|
| `version` | 标注格式版本 |
| `created_at` | 创建时间 |
| `source_dir` | 原始图片采集目录，仅作来源记录 |
| `cases` | 非空用例数组 |

用例字段：

| 字段 | 必需 | 含义 |
|---|---|---|
| `id` | 是 | 唯一用例标识 |
| `category` | 是 | `correct`、`incorrect` 或 `boundary` |
| `image` | 是 | 相对 `tests/benchmark` 的图片路径 |
| `cpp` | 是 | 相对 `tests/benchmark` 的 C++ 路径 |
| `expected` | 是 | 仅允许 `pass`、`fail`、`skip` |
| `latex` | 否 | 人工参考 LaTeX；可能包含多行或为空 |
| `expected_status` | 否 | 期望的细分状态说明 |
| `error_type` | 否 | 错误类别 |
| `note` | 否 | 人工说明 |

`run_benchmark.py` 强制校验 `id`、`category`、`image`、`cpp` 和
`expected`，并检查 `id` 不重复。

### 1.3 Structural 用例

`tests/benchmark/structural_errors/cases.json` 是数组，当前 3 个用例：

| 用例 | 说明 |
|---|---|
| `lu_upper_bound` | 图和代码都把 LU 的 `U_ij` 上界写成 `k<j` |
| `matmul_missing_k` | 代码少了最后一个 `k` |
| `conv_shift` | 代码循环边界少一项 |

单个用例字段：

| 字段 | 含义 |
|---|---|
| `id` | 唯一标识 |
| `expected` | 当前均为 `fail` |
| `latex_file` | 相对 `structural_errors` 的 `.tex` 文件 |
| `cpp` | 相对目录的 `.cpp` 文件 |
| `image` | 相对目录的 `.png` 文件 |
| `note` | 错误说明 |

`make_images.py` 只是把 `.tex` 源码文字画到白底 PNG 上，不是完整的 TeX
排版渲染。

## 2. 如何运行

### 2.1 设置环境变量

`main.py` 及其依赖会导入 `config.py`，而 `config.py` 要求
`DEEPSEEK_API_KEY` 非空。即使只是跑本地 SymPy 分支，也要先设置一个占位值或
真实值：

```powershell
$env:DEEPSEEK_API_KEY = '<your-key>'
```

需要 Hybrid、GLM 或 Qwen 校验时，再设置对应 Key：

```powershell
$env:GLM_API_KEY = '<your-key>'
$env:QWEN_API_KEY = '<your-key>'
```

密钥只从环境变量读取，不要写入源码或测试 JSON。

### 2.2 单元测试

可在没有 API 调用风险的场景下逐个运行：

```powershell
.\.python312\python.exe tests\test_cache.py
.\.python312\python.exe tests\test_edge_cases.py
.\.python312\python.exe tests\test_index_scopes.py
.\.python312\python.exe tests\test_matrix_parse.py
```

如果需要按文件名批量列出：

```powershell
Get-ChildItem tests\test_*.py | Select-Object FullName
```

工单1-4 新增/同步的回归测试：

- `tests/test_inner_product.py`：点积/矩阵向量乘规则 6 项，覆盖正例、
  分子换序、softmax 不触发、普通分式不触发、转置写法；
- `tests/test_web_batch.py`：5 张真实图片 + 确定性替身流水线，验证批量
  进度百分比、历史落盘/重启恢复、JSON/CSV 导出目录与字段（21 项）；
- `tests/test_w2_hybrid_offline.py`：已同步到新的 JSON 强制输出行为，
  断言 `json_retries`、`repair_elapsed` 与 2048→8192→8192 的预算；
- `tests/test_pde_analysis.py`（如存在）：逐行残差结构对齐。

`tests/test_verifier_chain.py` 需要特别注意：脚本中伪造了 GLM Key，但当前
`HeteroVerifier` 已完成真实网络调用改造，不再返回旧的离线 stub。因此直接
运行它会尝试访问 GLM，且其中“未联网”的旧断言与当前实现不一致。该问题属于
现有测试与代码的同步缺口，运行前应先明确是否允许联网，或先修复测试。

### 2.3 Benchmark

全量运行：

```powershell
.\.python312\python.exe tests\benchmark\run_benchmark.py
```

只运行指定用例：

```powershell
.\.python312\python.exe tests\benchmark\run_benchmark.py `
  --only image_01,image_02
```

限制运行数量：

```powershell
.\.python312\python.exe tests\benchmark\run_benchmark.py --limit 3
```

指定输出目录：

```powershell
.\.python312\python.exe tests\benchmark\run_benchmark.py `
  --out output\my_benchmark
```

输出：

- `benchmark_result.json`
- `benchmark_report.md`
- `_benchmark_tmp\<时间批次>\<用例id>\report_*.json`

Benchmark 子进程超时为 300 秒，单例失败不会中断其余用例。

### 2.4 校验器对比

对比脚本会运行 3 个 benchmark `incorrect` 用例和 3 个 structural 用例，
分别执行 `sympy-only` 与 `hybrid`：

```powershell
$env:DEEPSEEK_API_KEY = '<your-key>'
.\.python312\python.exe tests\benchmark\compare_verifiers.py
```

输出：

- `output/verifier_comparison.json`
- `output/verifier_comparison.md`

Hybrid 可能发起真实 DeepSeek 请求，单例超时为 240 秒。没有
`DEEPSEEK_API_KEY` 时，脚本会记录 Key 未配置，不能代表完整 Hybrid 能力。

### 2.5 缓存测试

```powershell
.\.python312\python.exe tests\test_cache.py
```

覆盖内容：

- 图片内容相同但路径不同共享 OCR 缓存
- `scope` 隔离
- 深拷贝隔离
- `MATH_AGENT_CACHE=0`
- 损坏 pickle 自动备份并恢复
- 校验缓存键稳定性

### 2.6 HTML 报告

先运行 benchmark 生成 `output/benchmark_result.json`，再执行：

```powershell
.\.python312\python.exe output\generate_html_report.py --report-format both
```

默认读取：

- `output/benchmark_result.json`
- `tests/benchmark/annotations.json`
- `output/_benchmark_tmp/` 下最新详细报告批次

输出：

- `output/report.html`
- `output/report.md`

只生成 HTML：

```powershell
.\.python312\python.exe output\generate_html_report.py `
  --report-format html
```

需要把原图以 Base64 嵌入单文件 HTML 时加 `--embed-images`。

### 2.7 端到端压测

```powershell
# 完整 24 个用例（20 正常 + 4 异常）
.\.python312\python.exe stress_test.py

# 快速验证
.\.python312\python.exe stress_test.py --limit 3

# 指定用例
.\.python312\python.exe stress_test.py --only image_02,structural_conv_shift
```

正常用例由 7 个 benchmark、3 个 structural、10 个 `tests/formula` 图片组成；
异常用例覆盖 0 字节图片、非图片文件、缺失路径和空 C++ 文件。输出：

- `output/stress_test_report.md`
- `output/stress_test_report.json`

指标口径：

- **正常用例流程成功率**：子进程产出合法 JSON 的比例，考察流水线会不会崩；
- **预期通过用例通过率**：`expected=pass` 的用例中 `report.ok=True` 的比例；
- **预期失败用例拦截率**：`expected=fail` 的用例中 `report.ok=False` 的比例，
  包含 `unknown/error` 结论，仅表示没有误放行；
- **缓存命中率**：所有报告 `cache.hits / (hits + misses)`；
- **异常用例优雅失败率**：异常输入返回非 0 退出码且没有未捕获 traceback 的比例。

2026-11-14 基线：流程成功率 100%（20/20），预期通过用例通过率 33.33%
（1/3，仅 `image_02`），预期失败用例拦截率 100%（6/6），平均耗时 9.5s，
P95 19.8s，缓存命中率 54.84%（17/31），异常优雅失败率 100%（4/4）。
`image_01` / `image_06` 未通过的原因分别是翻译层符号名不一致、多行
`array` 公式超出解析边界，详见压测报告末尾的“薄弱点与建议”。

## 3. 如何新增用例

### 3.1 新增普通 benchmark 用例

1. 根据预期把图片和 C++ 文件放入 `correct`、`incorrect` 或 `boundary`。
2. 在 `tests/benchmark/annotations.json` 的 `cases` 数组末尾追加对象。
3. 使用全局唯一 `id`，推荐 `image_08` 这类连续编号。
4. `image` 和 `cpp` 写相对 `tests/benchmark` 的路径。
5. `expected` 只能是：
   - `pass`：正确样例应通过
   - `fail`：错误样例应被拦截
   - `skip`：非公式或边界样例应被拒绝
6. 先运行单例：

```powershell
.\.python312\python.exe tests\benchmark\run_benchmark.py `
  --only image_08
```

7. 若结果与标注不符，先检查图片 OCR、C++ 翻译和最终 `latex_vs_cpp`
   status，再决定是修用例还是暴露产品问题。

`latex`、`expected_status`、`error_type` 和 `note` 不是 benchmark 运行必需
字段，但建议填写，便于人工审查和报告说明。

### 3.2 新增 structural 用例

1. 在 `tests/benchmark/structural_errors/` 新建同名 `.tex`、`.cpp` 和
   `.png`。
2. 在 `cases.json` 追加：

```json
{
  "id": "new_structural_case",
  "expected": "fail",
  "latex_file": "new_structural_case.tex",
  "cpp": "new_structural_case.cpp",
  "image": "new_structural_case.png",
  "note": "错误原因"
}
```

3. 根据 `.tex` 重新生成图片：

```powershell
.\.python312\python.exe tests\benchmark\structural_errors\make_images.py
```

4. 用 LaTeX 直接验证结构错误：

```powershell
$latexText = Get-Content -Encoding utf8 `
  tests\benchmark\structural_errors\new_structural_case.tex -Raw
.\.python312\python.exe agent\main.py `
  --verifier-mode hybrid `
  -l $latexText `
  -c tests\benchmark\structural_errors\new_structural_case.cpp `
  --json
```

5. 最后运行 `compare_verifiers.py`，确认新用例在报告中出现。

注意：`compare_verifiers.py` 当前要求总错误用例数严格为 6，即 3 个
benchmark incorrect 加 3 个 structural。新增 structural 用例后，需要同步
调整脚本中的数量校验逻辑，否则对比脚本会报数量不匹配。这是当前实现约束。

## 4. 结果指标口径

### 4.1 Benchmark 命中率

`run_benchmark.py` 的命中规则：

| 标注 `expected` | 实际 `actual` | 是否命中 |
|---|---|---|
| `pass` | `pass` | 是 |
| `fail` | `fail` | 是 |
| `skip` | `non_formula` | 是 |
| 其他组合 | 任意 | 否 |

`overall_accuracy = 命中数 / 总用例数`。

### 4.2 正类与负类召回

- 正类召回：`category=correct` 且实际 `pass` 的数量 / `correct` 总数。
- 负类召回：`category=incorrect` 且实际 `fail` 的数量 / `incorrect` 总数。
- fail 类 precision：真正错误被拦截数 / 所有实际判 fail 的 correct 与
  incorrect 数量之和。
- fail 类 F1：precision 与 recall 的调和平均。

### 4.3 边界拒绝率

`category=boundary` 且实际为 `non_formula` 的数量 / boundary 总数。

### 4.4 明确判错与 unknown/error

`compare_verifiers.py` 不把 `unknown` 或 `error` 当作“明确捕获”：

- 明确判错状态：
  `not_equivalent`、`shape_mismatch`、`needs_human_review`、
  `translation_failed`。
- 无法判定状态：
  `unknown`、`error`、`parse_error`、`timeout`。

`captured` 只看最终 `ok=False`，因此 unknown/error 也可能算“报告未通过”。
评估 Hybrid 是否真正提升时，应优先看“明确判错率”，不要只看捕获率。

## 5. 常见问题

### 5.1 未设置 `DEEPSEEK_API_KEY`

现象：导入 `config` 时报 `RuntimeError: 请设置 DEEPSEEK_API_KEY`，或
benchmark 子进程返回 `pipeline_error`。

处理：

```powershell
$env:DEEPSEEK_API_KEY = '<your-key>'
```

### 5.2 图片 OCR 失败

检查：

- 图片路径是否存在且确实是图片。
- 若报 `DLL load failed while importing _imaging`，先修复当前
  `.python312` 的 Pillow 原生扩展。`test_edge_cases.py` 中的兼容层只覆盖
  部分错误分支，不能替代完整图片处理能力。
- 先用 `--mode both` 对比 Vision 与 Pix2Text 的结果。
- Pix2Text 首次运行可能需要下载模型。
- 普通文字图片应返回非公式，而不是强行识别成 LaTeX。

单图调试：

```powershell
.\.python312\python.exe agent\main.py `
  -i tests\benchmark\incorrect\image_03.png `
  --mode both `
  -v
```

### 5.3 缓存复用了旧结果

临时禁用当前 PowerShell 会话的缓存：

```powershell
$env:MATH_AGENT_CACHE = '0'
```

恢复缓存：

```powershell
Remove-Item Env:MATH_AGENT_CACHE -ErrorAction SilentlyContinue
```

手动清空缓存：

```powershell
.\.python312\python.exe -c "import sys;sys.path.insert(0,'agent');import cache;cache.clear()"
```

缓存 scope 已包含 OCR 引擎/提示词和校验模式；如果新增后端但没有更新 scope，
仍可能发生跨后端复用。

### 5.4 `test_verifier_chain.py` 与本机网络策略冲突

当前测试脚本仍按旧“离线 stub”实现写断言，但生产代码会真实调用 GLM。
在没有网络许可时不要直接运行该脚本；将其视为待修复的测试同步问题，而不是
产品运行必需步骤。

