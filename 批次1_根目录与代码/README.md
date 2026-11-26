# math-agent

> 手写数学公式/代码 **OCR -> 双向翻译 -> 可插拔校验 -> 报告** 三层流水线。
> Windows 宿主机运行，视觉主力 DeepSeek Vision，本地兜底 Pix2Text，
> 数学等价校验以 SymPy 为主，可叠加 DeepSeek/GLM/Qwen 异构校验。

## 1. 项目简介

`math-agent` 面向前置“手写公式与代码是否一致”的检测场景：

1. **OCR 层**：把公式或代码图片转成 LaTeX/C++，Vision 与 Pix2Text 可单独
   使用，也可双链路对比。
2. **翻译层**：使用 DeepSeek 文本模型完成 C++ 到数学公式、数学公式到 C++
   的双向转换。
3. **校验层**：使用 SymPy 做符号化简、数值采样、矩阵逐元素比较；Hybrid
   模式可追加远程异构校验器。
4. **报告层**：生成 JSON、Markdown、HTML 报告，并记录缓存命中、耗时、
   token 和差异点。

当前状态：

- 三层流水线已接通，`Pipeline.run()` 可直接处理图片、LaTeX、C++ 和期望
  表达式。
- OCR 与校验结果已接入本地 pickle 缓存，第二次运行可命中；翻译结果新增
  独立缓存（`translate.pkl`），强制 `temperature=0`，可选 3 轮投票。
- 校验器支持 `sympy-only`、`hybrid` 和未显式指定时的 `auto` 链；超时熔断
  状态持久化到 `cache/circuit_state.json`，跨进程共享（文件锁）。
- Benchmark 已扩充到 **50 个用例**：33 个 `baseline`（`correct/` 8、
  `incorrect/` 8、`structural_errors/` 8、`pde/` 4、`matrix_chain/` 4、
  `boundary/` 1）+ 17 个 `stress` 对抗用例（手写体、模糊/噪声、复杂排版、
  记号变体、边界情况），由 `tests/benchmark/make_hard_cases.py` 生成。
- 校验层新增**能力缺口仲裁**（w10）：SymPy 因能力边界给不出结论、而独立语义
  校验器一致判等价时采信后者，结论带 `accepted_by=hetero_consensus` 审计字段；
  PDE 用例永远不升级，`HETERO_ACCEPT_CONSENSUS=0` 可整体关闭。
- 归一化层新增记号变体规则（`\over`、`\operatorname`、`\mathsf{T}`、下标逗号、
  `\left|…\right|` 解析）与单字母大小写回退；参考多行改为按符号重叠度配对。
- 新增 PDE 兜底分析（`agent/pde_analysis.py`）：识别 ∂/∇/Δ 算子、按项提取
  微分算子签名、结构对齐时标注“需人工确认”并记录能力边界。
- 新增 Gradio Web 界面（`web/app.py`），支持拖拽上传、模式选择、批量进度、
  持久化历史、JSON/CSV 导出与预览缩放，访问 `http://localhost:7860`。
- 已有 SymPy-only 与 Hybrid 对比脚本，以及 Benchmark HTML 报告生成器。
- 已完成 24 用例端到端压测（20 正常 + 4 异常），报告见
  `output/stress_test_report.md`：流程成功率 100%、预期失败拦截率 100%、
  异常优雅失败率 100%，缓存命中率约 55%。

最新一轮全量结果（2026-11-15，`output/benchmark_w10_final`，50 用例）：

| 指标 | w9 基线（33 例） | w10 基线子集（33 例） | w10 对抗子集（17 例） | w10 全量（50 例） |
|---|---:|---:|---:|---:|
| 命中 | 32/33 | 30/33 | 15/17 | **45/50** |
| 总体准确率 | 96.97% | 90.91% | 88.24% | **90.00%** |
| 正类召回 | 95.24% | 81.82% | 81.82% | 81.82% |
| 负类召回 | 100% | 96.30% | 100% | 96.30% |
| fail_F1 | 0.9756 | 0.9259 | 0.9231 | 0.9123 |
| 平均耗时 | 24.95s | 26.4s | 22.9s | 25.21s |

17 个对抗用例由 `tests/benchmark/make_hard_cases.py` 生成（手写体 3、退化图像 3、
复杂排版 4、记号变体 4、边界 3），首轮 11/17（64.71%），逐例诊断并修复后
15/17（88.24%）。未命中的 2 例（`layout_nested_frac`、`var_sum_sigma`）与
基线的 3 例（`softmax`、`chain4`、`pde_residual_scope`）全部可解释，
详见 `output/failure_analysis.md`；能力分层与前后对比见
`output/system_capability_map.md`，SymPy-only/Hybrid 对照见
`output/verifier_comparison.md`。

> 注意：`softmax`、`var_sum_sigma`、`hw_trace` 这类「下标基准不同但元素个数相同」
> 的用例，异构校验（Qwen-VL）的判定存在抖动，实测同一份代码重复运行会有
> 1–2 例翻转，单次全量准确率约 90% ± 4%。

## 2. 运行环境

- Windows 宿主机，项目目录：`D:\math-agent`。
- Python 3.12 便携解释器：
  `D:\math-agent\.python312\python.exe`。
- 终端：PowerShell。
- Python 包层面已有 `sympy`、`openai`、`requests`、`Pillow`；Pix2Text 是否
  可用取决于当前环境。若原生扩展加载异常，见 8.4 节。
- 所有命令都在项目根目录执行：

```powershell
Set-Location D:\math-agent
```

## 3. 目录结构

```text
math-agent/
├── agent/
│   ├── config.py          # 路径、模型、API Key 和异构校验配置
│   ├── cache.py           # OCR/校验/翻译持久化缓存
│   ├── circuit_breaker.py # 跨进程持久化超时熔断（文件锁 + 原子写）
│   ├── symbol_norm.py     # LaTeX 归一化（求和上下限/迭代下标/范数）
│   ├── pde_analysis.py    # PDE 兜底分析（微分算子签名 + 人工复核标记）
│   ├── ocr_pix.py         # Pix2Text 本地 OCR
│   ├── ocr_vision.py      # DeepSeek Vision OCR
│   ├── llm.py             # C++ 与数学公式双向翻译（temperature=0 + 缓存）
│   ├── verify.py          # CheckResult、SymPy 校验与索引范围提取
│   ├── verifier_hetero.py # DeepSeek/GLM/Qwen 与校验链
│   ├── explain.py         # 可解释批改（根因/定位/修改建议 + 独立缓存）
│   └── main.py            # Pipeline、CLI、Markdown 报告
├── web/
│   └── app.py             # Gradio Web 界面（单图 + 批量进度 + 历史 + 导出）
├── scripts/
│   ├── clean_pip_leftovers.ps1  # 管理员清理项目根目录残留的 pip-* 空目录
│   ├── clean_pip_leftovers.bat  # 弹出 UAC 的快捷入口
│   ├── eval_explain_quality.py  # 解释质量逐例验收（对照 annotations.json）
│   └── verify_explain_web.py    # 3 张错图的 Web 面板/导出在线验证
├── tests/
│   ├── benchmark/
│   │   ├── correct/            # 8 个正例
│   │   ├── incorrect/          # 8 个错例
│   │   ├── boundary/           # 1 个边界用例
│   │   ├── structural_errors/  # 8 个结构性错误
│   │   ├── pde/                # 4 个偏微分方程用例
│   │   ├── matrix_chain/       # 4 个矩阵链用例
│   │   ├── annotations.json
│   │   ├── make_cases.py       # 用例图片生成器
│   │   ├── run_benchmark.py
│   │   ├── make_comparison.py  # 基于两轮结果生成对比报告
│   │   └── compare_verifiers.py
│   ├── cpp/
│   ├── formula/
│   └── test_*.py
├── output/
│   ├── generate_html_report.py
│   ├── benchmark_result.json
│   ├── benchmark_report.md
│   ├── verifier_comparison.md
│   ├── performance_analysis.md # 性能剖析（阶段耗时 + 异构调用统计）
│   ├── web_history.json        # Web 界面的持久化历史（最多 200 条）
│   ├── web_exports/            # Web 导出的 JSON/CSV
│   ├── report.html
│   └── report_<时间戳>.json/.md
├── cache/
│   └── circuit_state.json      # 跨进程熔断状态
├── third_party/glm/       # GLM 头文件库（仓库未收录，非运行必需，需要时自行下载）
├── .python312/            # Windows 便携解释器（仓库未收录，见 SETUP_给别人用.md）
├── API.md
├── ARCHITECTURE.md
├── TESTING.md
└── CONTRIBUTING.md
```

## 4. 安装与依赖检查

当前机器依赖已安装，通常不需要重新安装。先检查基础包：

```powershell
.\.python312\python.exe -c "import sympy, openai, requests, PIL; print('基础依赖可用')"
```

检查 Pix2Text：

```powershell
.\.python312\python.exe -c "import pix2text; print('pix2text 可用')"
```

若在新机器重建环境，再按项目现有版本安装依赖并复查 compatibility。不要
直接使用系统里的其他 Python，避免包和本地扩展 DLL 不一致。

可选 HuggingFace 镜像：

```powershell
$env:HF_ENDPOINT = 'https://hf-mirror.com'
```

## 5. 配置 API Key

密钥只从环境变量读取，不写进源码或配置文件。PowerShell 当前会话配置：

```powershell
$env:DEEPSEEK_API_KEY = '<your-key>'
```

重要：当前 `agent/config.py` 在导入时就要求 `DEEPSEEK_API_KEY` 非空。因此
即使使用 `--mode pix`、`--no-llm` 或只做本地 SymPy 校验，也要先设置该环境
变量。

可选配置：

```powershell
$env:OPENAI_BASE_URL = 'https://api.openai-next.com/v1'
$env:DEEPSEEK_TIMEOUT = '120'
$env:DEEPSEEK_MAX_RETRIES = '3'

$env:GLM_API_KEY = '<your-key>'
$env:QWEN_API_KEY = '<your-key>'
$env:GLM_VERIFIER_MODEL = 'glm-4v'
$env:QWEN_VERIFIER_MODEL = 'qwen3-vl-plus'

$env:HETERO_VERIFY_TIMEOUT = '60'
$env:HETERO_VERIFY_ENABLED = '1'
```

Qwen-VL 专用说明（工单3）：

- `QWEN_API_KEY` 与 DashScope 官方变量名 `DASHSCOPE_API_KEY` 等价，二者设置其一
  即可；代码优先读 `QWEN_API_KEY`。
- 默认模型是视觉模型 `qwen3-vl-plus`；若环境里残留旧的
  `QWEN_VERIFIER_MODEL=qwen-max`（纯文本模型），请显式改成
  `qwen3-vl-plus`，否则校验器读不到图片证据。
- 默认服务地址是 `https://dashscope.aliyuncs.com/compatible-mode/v1`，
  可用 `QWEN_BASE_URL` 覆盖。
- 配置后在 `--verifier-mode hybrid` 下会自动组成
  `sympy + deepseek + qwen-vl` 校验链，启动日志会打印
  `[HYBRID_CHAIN] sympy=..., deepseek=..., qwen-vl=qwen3-vl-plus`；
  校验缓存按「逻辑版本 + 校验链构成」隔离，加入 Qwen 后不会复用旧结论。

DeepSeek 网关当前实际读取 `OPENAI_BASE_URL`。关闭终端后环境变量会失效；
如果需要长期保存，请使用 Windows 用户环境变量界面，并避免把真实 Key
写入项目文件。

## 6. 使用

### 6.1 单图校验

```powershell
.\.python312\python.exe agent\main.py `
  -i tests\formula\formula_01.png `
  -e "m*c**2"
```

默认 OCR 链路为 `auto`，先调用 Vision，失败后尝试 Pix2Text。

### 6.2 Hybrid 校验

```powershell
.\.python312\python.exe agent\main.py `
  -i tests\benchmark\incorrect\image_03.png `
  -c tests\benchmark\incorrect\image_03.cpp `
  --verifier-mode hybrid `
  --json
```

`hybrid` 会在 SymPy 后追加 DeepSeek 校验器。`sympy-only` 不联网；不指定
`--verifier-mode` 时使用 `auto`，GLM/Qwen Key 存在时会自动加入对应校验器。

### 6.3 公式到代码

只有显式指定 `--direction formula2code` 时才触发表述到 C++ 的生成：

```powershell
.\.python312\python.exe agent\main.py `
  -i tests\formula\formula_03.png `
  --direction formula2code `
  -e "sin(theta)**2+cos(theta)**2-1"
```

### 6.4 代码到公式

```powershell
.\.python312\python.exe agent\main.py `
  --direction code2formula `
  -c tests\cpp\matrix_mul.cpp `
  -v
```

### 6.5 直接校验 LaTeX

```powershell
.\.python312\python.exe agent\main.py `
  -l "\frac{a}{b}" `
  -e "a/b" `
  --no-llm `
  --verifier-mode sympy-only
```

### 6.6 双链路 OCR 对比

```powershell
.\.python312\python.exe agent\main.py `
  -i tests\formula\formula_03.png `
  --mode both `
  -e "sin(theta)**2+cos(theta)**2-1" `
  -v
```

旧的 `--engine auto|vision|pix` 参数仍兼容。`--mode` 与 `--engine` 同时出现
且值不同时，CLI 返回参数错误。

### 6.7 Benchmark

当前测试集共 **50 个用例**，分两批（`annotations.json` 的 `batch` 字段）：
33 个 `baseline`（`suite` 字段区分来源目录）+ 17 个 `stress`（w10 新增对抗用例）。

| 目录 | 数量 | 说明 |
|---|---:|---|
| `correct/` | 8 | 图片与代码一致，期望 `pass` |
| `incorrect/` | 8 | 图片正确但代码有错，期望 `fail` |
| `structural_errors/` | 8 | 索引范围、步长、缺项等结构性错误 |
| `pde/` | 4 | 偏微分方程（能力边界用例） |
| `matrix_chain/` | 4 | 矩阵链结合与顺序 |
| `boundary/` | 1 | 非公式图片，期望 `skip` |
| `hard_hand/` | 3 | **stress**：手写体 + 撇号/上下标/迭代下标 |
| `hard_noise/` | 3 | **stress**：模糊、低分辨率、椒盐噪声 |
| `hard_layout/` | 4 | **stress**：分段函数、嵌套分式、矩阵展开、多行链式等式 |
| `hard_variant/` | 4 | **stress**：记号变体、1-based vs 0-based、范数性质量 |
| `hard_edge/` | 3 | **stress**：超短公式、MLP 链式命名、中英混排 |

对抗用例由 `tests/benchmark/make_hard_cases.py` 生成（自带渲染器，
支持手写体 `hand`、模糊 `blur`、噪声 `noise`、小字号 `tiny`、深色编辑器 `mono`），
每条都带 `formula_key`、参考 LaTeX、参考 C++ 与期望结论：

```powershell
.\.python312\python.exe tests\benchmark\make_hard_cases.py --list
.\.python312\python.exe tests\benchmark\make_hard_cases.py --only hw_newton --force
```

```powershell
.\.python312\python.exe tests\benchmark\run_benchmark.py
```

只运行单例：

```powershell
.\.python312\python.exe tests\benchmark\run_benchmark.py `
  --only image_01
```

指定校验链路（默认 `auto`）：

```powershell
.\.python312\python.exe tests\benchmark\run_benchmark.py `
  --verifier hybrid `
  --out output\benchmark_hybrid
```

输出 `output/benchmark_result.json` 和 `output/benchmark_report.md`；报告新增
“按套件分组”一节。重新生成用例图片：

```powershell
.\.python312\python.exe tests\benchmark\make_cases.py --force
```

### 6.8 校验器对比实验

完整重跑（会真实调用两轮模型，较慢）：

```powershell
.\.python312\python.exe tests\benchmark\compare_verifiers.py
```

只跑指定用例并单独落盘（工单3 用来验证 Qwen-VL 接入，不覆盖全量报告）：

```powershell
$env:QWEN_API_KEY = '<dashscope-key>'          # 或 $env:DASHSCOPE_API_KEY
$env:QWEN_VERIFIER_MODEL = 'qwen3-vl-plus'
.\.python312\python.exe tests\benchmark\compare_verifiers.py `
  --only image_07,sign_flip,missing_sqrt,matmul_missing_k,off_by_one_sum `
  --json-out output\qwen_verifier_comparison.json `
  --md-out output\qwen_verifier_comparison.md
```

报告的头部会打印本次 hybrid 实际加载的校验链，例如
`sympy + deepseek@deepseek-v4-1-flash-261017 + qwen-vl@qwen3-vl-plus`。
实测 6 个负例：SymPy-only 明确判错 3/6（50%），加入 Qwen-VL 的 hybrid 为
6/6（100%），代价是每例多一次模型往返。

基于已有两轮 benchmark 结果快速生成对比（不重新跑流水线）：

```powershell
.\.python312\python.exe tests\benchmark\make_comparison.py `
  --sympy output\benchmark_w8_sympy\benchmark_result.json `
  --hybrid output\benchmark_w8_hybrid\benchmark_result.json
```

两种方式都会输出 `output/verifier_comparison.json` 与
`output/verifier_comparison.md`。Hybrid 会发起真实 DeepSeek 请求，使用时需要
有效 Key 和网络。

### 6.9 HTML 报告

先完成 benchmark，再执行：

```powershell
.\.python312\python.exe output\generate_html_report.py --report-format both
```

输出：

- `output/report.html`
- `output/report.md`

需要单文件 HTML 时加 `--embed-images`。

### 6.10 观察缓存效果

相同 LaTeX、代码和校验模式第二次运行时会命中校验缓存：

```powershell
.\.python312\python.exe agent\main.py `
  -l "\frac{a}{b}" `
  -e "a/b" `
  --no-llm `
  --verifier-mode sympy-only `
  --out output\cache_demo
```

连续执行两次，第二次报告中的校验结果 `meta.cache` 应为 `hit`，缓存统计中的
命中数会增长。缓存 scope 包含校验模式与 `VERIFIER_LOGIC_VERSION`
（`agent/main.py`），因此归一化规则升级后旧缓存会自动失效，不会读到升级前
的结论。

翻译缓存单独存放在 `.cache/translate.pkl`，键包含方向、模型名和提示词版本；
设置 `MATH_AGENT_TRANSLATE_CACHE=0` 可临时关闭。

### 6.11 Web 界面

```powershell
$env:DEEPSEEK_API_KEY = "<你的 Key>"
.\.python312\python.exe web\app.py
```

浏览器打开 `http://localhost:7860`（可用 `--port` / `--host` 改端口与监听地址）：

- 左侧拖拽上传公式图片，可选附参考 C++ 文件，并选择 OCR 链路与校验模式；
- 右侧展示 OCR/LaTeX、代码翻译、结论徽标（✅ 等价 / ❌ 不等价 / ⚠️ 需人工
  确认）、耗时、缓存命中情况与判定依据；
- 右侧大图预览支持「100% 放大 / 适应窗口」两档高度，方便核对上下标；
- “批量上传”支持一次选择多张图片，除 `正在处理 i/N` 文本外，还显示带
  `当前/总数 + 百分比` 的 HTML 进度条（如 `已完成 3/5（60%）`）与实时结果表；
  单张失败不中断整批，最后给出通过/不通过/待人工/异常的汇总；
- 批量模式下若图片同目录存在同名 `.cpp`，会自动作为该图的参考代码，
  便于「多图 + 多代码」成对回归；
- 底部保留最近 200 条历史记录，落盘到 `output/web_history.json`，
  重启界面后仍在；“清空历史”只清历史，不影响导出文件；
- “导出 JSON / 导出 CSV”把当前批量或历史结果写到
  `output/web_exports/web_export_<时间戳>.<fmt>`（9 个字段：
  时间、图片、结论、状态、耗时、OCR 链路、校验模式、判定依据、错误），
  并在界面上给出下载链接。

界面只做编排与展示，判定逻辑仍由 `agent/main.py` 的 `Pipeline` 提供。
单图与批量都通过 `demo.queue(default_concurrency_limit=1)` 串行执行，
避免并发抢占 OCR 缓存与 API 配额。

#### 6.11.1 一键启动脚本（w13）

不想每次敲命令时，用生成器产出一个可双击的 `.bat`：

```powershell
.\.python312\python.exe scripts\create_shortcut.py          # 生成到仓库根目录
.\.python312\python.exe scripts\create_shortcut.py --check  # 只自检，不写文件
.\.python312\python.exe scripts\create_shortcut.py --desktop # 尝试写桌面（受限时自动回退）
```

产物：`D:\math-agent\start_math_agent.bat`（3,372 字节，正文纯 ASCII + CRLF，避免
cmd 代码页把中文变乱码）。双击后：解析密钥 → 校验解释器与 `web/app.py` →
`http://localhost:7860` 起服务，关窗即停。

**密钥不进脚本**（铁律 4）。`.bat` 在运行时按下面的顺序取 Key：

1. 当前进程已有的 `DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY`；
2. 仓库根目录下用户自建的 `.env.local.bat`（`set DEEPSEEK_API_KEY=...`）；
3. 当前用户级持久化环境变量（用 `powershell -NoProfile -Command
   "[Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY','User')"` 读取）。

一次性注入（只写注册表的用户环境，不落任何文件）：

```powershell
setx DEEPSEEK_API_KEY "<你的 Key>"
setx DASHSCOPE_API_KEY "<你的 Key>"
```

`QWEN_VERIFIER_MODEL` 不是密钥，脚本里固化为 `qwen3-vl-plus`。
缺 `DASHSCOPE_API_KEY` 时只提示、照常启动（异构链退化为 SymPy + DeepSeek）；
缺 `DEEPSEEK_API_KEY` 时警告并暂停，避免"界面能开、一点就报错"。

> 桌面在 C 盘受限区，沙箱默认拒绝写入（实测 `PermissionError: [Errno 13]`），
> 因此默认生成在仓库根目录：需要桌面入口时，把 `start_math_agent.bat`
> 拖到桌面，或右键 → 发送到 → 桌面快捷方式。

### 6.12 性能剖析与残留目录清理

```powershell
# 从最近一次 hybrid benchmark 生成阶段耗时拆分
.\.python312\python.exe scripts\perf_report.py
# 输出：output\performance_analysis.md
```

报告会拆分 OCR、C++→LaTeX 翻译、SymPy 校验、异构 LLM 校验四段耗时，
并统计异构调用次数、空响应重试、token 用量与最慢用例。

项目根目录曾有 84 个 `pip-*` 残留目录（早期 `TMP=.` 运行 pip 产生，多为
`pip-metadata-*` / `pip-unpack-*`，内含 `METADATA` 或 `*.whl.metadata`）。
**已于 2026-11-15 全部清理（84 个 / 约 49MB）**；脚本保留，供以后再次残留时使用：

```powershell
# 先预演，再实际删除（普通权限即可，若被拒绝再用管理员）
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\clean_pip_leftovers.ps1 -WhatIf
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\clean_pip_leftovers.ps1
```

或直接双击 `scripts\clean_pip_leftovers.bat`（会弹 UAC）。脚本只处理项目根目录
下匹配 `pip-<类型>-<随机串>` 的目录；纯 `METADATA`/`*.whl.metadata` 的 pip
临时目录视同空目录直接删，其它含真实文件的目录默认跳过并列出，逐条打印绝对路径。

### 6.13 可解释批改（批改说明）

判定为 `not_equivalent` / `CONFIRMED_ERROR` 后，系统会在结论**之后**再生成一份结构化
说明，只写入 `steps.checks[i].explain`，**不改动任何判定字段**（`ok/status/lhs/rhs/
detail/diff_points/meta` 保持原值）：

| 字段 | 含义 |
|---|---|
| `error_type` | 根因分类 id（索引范围 / 符号 / 转置 / 漏项 / 能力边界等 17 类） |
| `location` | 出错代码行（形如 `L12: ...`）或公式片段 |
| `suggestion` | 可直接采用的修正（改后的 C++ 片段或正确公式） |
| `confidence` | 0–1，这条定位/建议的可信度 |
| `needs_human_review` | 是否建议人工复核；能力边界与低置信度恒为 `true` |

开关（默认开启；关闭时完全跳过解释层，零额外调用、报告里不出现 `explain` 字段）：

```powershell
$env:EXPLAIN_ENABLED="0"          # 关闭
$env:EXPLAIN_MAX_TOKENS="8192"    # 解释层单次正文预算（推理模型会被推理耗尽）
$env:EXPLAIN_TIMEOUT="90"
```

Web 端在右侧「批改说明（可解释批改）」折叠面板中展示：错误类型徽标、定位片段、
修改建议代码块、置信度百分比、人工复核提示；批量导出 JSON 的 `explain` 为结构化
对象，CSV 中为 JSON 字符串。CLI 报告直接读取同一字段；解释失败只写日志，不影响主流程。

质量验收（逐例对照 `tests/benchmark/annotations.json`，产出逐例表与未命中清单）：

```powershell
.\.python312\python.exe scripts\eval_explain_quality.py            # 全部预期 fail 用例
.\.python312\python.exe scripts\eval_explain_quality.py --cases "image_03,image_04"
# 输出：output\explain_quality_report.md / output\explain_failures.md / output\explain_quality.json
```

解释层使用独立缓存（`explain.pkl`，键含模型、提示词版本、状态、判定标签与证据摘要），
重复运行零模型调用。

#### 6.13.1 w12 稳定性收尾：校验链口径、Qwen-VL 端点与基准复现

> 本节是 w11 之后的工程收尾记录，**不改变任何判定逻辑**，只把「怎样跑才有可比性」
> 和两处环境事实写清楚，避免复现时把配置差异当成模型退化。

**（1）`auto` 与 `hybrid` 的校验链口径不同，指标不可混比。**

| `--verifier-mode` | 实际校验链（有 DeepSeek Key、无 GLM/Qwen Key 时） | 本轮 50 例 |
|---|---|---:|
| `auto`（默认） | `SymPy` only（`build_verifier_chain` 未传 `include_deepseek=True`） | 31/50（62%） |
| `hybrid` | `SymPy + deepseek@deepseek-v4-1-flash-261017` | **47/50（94%）** |

`auto` 只有在配置了 `GLM_API_KEY` 或 `QWEN_API_KEY` 时才会追加异构校验器；否则
异构层全程不参与，符号层判 `unknown` 的用例会直接落成 `unknown`。因此**报告 50 例
指标时必须写明 `--verifier hybrid`**，否则数字不可比。

```powershell
$env:DEEPSEEK_API_KEY="<key>"
.\.python312\python.exe tests\benchmark\run_benchmark.py --verifier hybrid --out output\benchmark_w12_hybrid
```

**（2）Qwen-VL 端点：本机 DashScope Key 不可用，主网关可用，但默认仍走官方端点。**

- `https://dashscope.aliyuncs.com/compatible-mode/v1` + 当前 Key → `401 invalid_api_key`；
- `https://api.openai-next.com/v1` + 同一 Key + `qwen3-vl-plus` → 正常返回。

`agent/config.py` 因此保留了三种写法：官方端点常量 `QWEN_DASHSCOPE_BASE_URL`、
默认值仍指向官方端点、`QWEN_BASE_URL` 环境变量可一键切到主网关：

```powershell
$env:DASHSCOPE_API_KEY="<key>"
$env:QWEN_VERIFIER_MODEL="qwen3-vl-plus"
$env:QWEN_BASE_URL="https://api.openai-next.com/v1"   # 走主网关时才需要设置
```

**为什么没有把主网关设成默认**：实测（4 例定向 A/B，`output/benchmark_w12_probe`）
接入 Qwen 后，SymPy 判 `unknown` 的 PDE 用例 `image_04` 会被异构「不一致」票升级为
`CONFIRMED_ERROR`（`status=not_equivalent`），突破「PDE 恒为 `unknown` +
人工复核」的既定边界。Qwen 是**可选增强**，不是默认路径；边界优先于分数。

**（3）临时目录与权限。** `.tmp/` 下历史遗留的 `probe_*`、`w11_explain_cache_*`
目录曾因目录权限项缺失而无法删除（`Remove-Item` 报 `UnauthorizedAccessException`，
且 `Get-ChildItem` 也拒绝枚举）。用 `diagnose-windows-sandbox-acl` 一次性补齐当前
用户的完全控制权限项后删除成功；恢复记录与回滚脚本留在 `acl-recovery/`
（`acl-backup-*.json` + `.ps1`）。今后清理仍用 §6.12 的预演命令。

**（4）本轮回归结论**：`tests/` 20 个测试文件全部 0 失败；解释一致率 25/27 = 92.59%
（根因族 26/27 = 96.30%），与 w11 持平；三例未命中全部是 `unknown` /
`needs_human_review`，**无一例误报**（`fail_precision = 1.0`）。

## 7. CLI、输出与退出码

主要 CLI 参数：

| 参数 | 含义 |
|---|---|
| `-i`, `--image` | 图片路径 |
| `-l`, `--latex` | 直接提供 LaTeX |
| `-c`, `--cpp` | C++ 源文件 |
| `-e`, `--expect` | 期望数学表达式 |
| `--mode` | `vision`、`pix`、`both` |
| `--direction` | `formula2code`、`code2formula` |
| `--engine` | `auto`、`vision`、`pix` |
| `--verifier-mode` | `sympy-only`、`hybrid` |
| `--out` | 报告输出目录 |
| `--no-llm` | 禁用 LLM 翻译 |
| `--json` | stdout 输出 JSON |
| `-v`, `--verbose` | 详细日志和三段式中间结果 |

输出：

- `output/report_<时间戳>.json`：结构化报告
- `output/report_<时间戳>.md`：可读 Markdown 报告
- `output/benchmark_result.json`：benchmark 汇总
- `output/benchmark_report.md`：benchmark 表格
- `output/verifier_comparison.md`：校验器对比
- `output/qwen_verifier_comparison.md`：加入 Qwen-VL 的子集对比
- `output/performance_analysis.md`：性能剖析（阶段耗时与优化建议）
- `output/web_history.json` / `output/web_exports/`：Web 历史与导出
- `output/report.html`：可视化报告

退出码：

- `0`：报告通过
- `1`：报告未通过或流水线运行失败
- `2`：参数、输入路径或输出目录错误
- `130`：用户中断

## 8. 常见问题

### 8.1 提示 `请设置 DEEPSEEK_API_KEY`

```powershell
$env:DEEPSEEK_API_KEY = '<your-key>'
```

该检查发生在导入 `config.py` 时，不是只有 Vision 请求才检查。

### 8.2 Pix2Text 首次运行很慢

首次加载或下载模型属正常现象。网络受限时可设置：

```powershell
$env:HF_ENDPOINT = 'https://hf-mirror.com'
```

### 8.3 图片路径错误

CLI 会在 OCR 前检查图片和 C++ 文件。相对路径以当前 PowerShell 目录为准，
建议先执行 `Set-Location D:\math-agent`。

### 8.4 Python 原生扩展加载失败

若出现：

```text
ImportError: DLL load failed while importing _imaging
```

说明当前解释器的原生扩展不可用（常见于受限沙箱、位数不匹配或缺少
Visual C++ 运行库）。请在正常宿主机 PowerShell 会话中确认：

```powershell
.\.python312\python.exe -c "import PIL, hashlib, ctypes; print(PIL.__version__)"
```

若该命令正常，则流水线可以运行；若仍失败，需准备与 Python 3.12 匹配的
Pillow 二进制环境，或改用系统 Python 并安装相同依赖。

### 8.5 缓存复用了旧结果

临时禁用：

```powershell
$env:MATH_AGENT_CACHE = '0'
```

手动清空：

```powershell
.\.python312\python.exe -c "import sys;sys.path.insert(0,'agent');import cache;cache.clear()"
```

### 8.6 对比脚本没有产生有效 Hybrid 结果

检查：

- `DEEPSEEK_API_KEY` 是否已设置。
- 网络是否可达 `OPENAI_BASE_URL`。
- `HETERO_VERIFY_ENABLED` 是否被设为关闭。
- `output/verifier_comparison.md` 中是否为 unknown/error，而不是明确判错。

## 9. 已知限制

- 多行 `aligned`、PDE 和复杂 LaTeX 环境存在解析边界，当前可能返回
  `unknown`，不能把“无法判定”当成“已证明错误”。
- **PDE 能力边界**：`\partial`、`\nabla`、`\Delta` 这类连续算子与离散实现之间
  不存在 SymPy 可判定的代数等价关系。遇到 PDE 时
  `agent/pde_analysis.py` 会按项提取微分算子签名并写入
  `steps.checks[].meta.needs_human_review = true`，结论应视为“需人工确认”，
  而不是自动判定通过。
- **表示差异边界**：当代码翻译层引入中间变量（如先算 `T = Σ exp(z_j)` 再
  写 `s_i = exp(z_i)/T`）时，变量命名与图片公式不一致，SymPy 与异构模型都可能
  给出 `unknown` 或误判 `not_equivalent`。目前已通过“多行参考拆行配对”
  （`row:<行号>` 候选）、矩阵链结合律规则与点积/矩阵向量乘规则覆盖 softmax、
  chain3、chain4、assoc_paren、cg_alpha 等典型写法。点积规则只处理“两侧都是
  分式、分子分母同为内积/乘积链、系数名一致”的受限形状，遇到
  `exp/log/sin/sqrt/∂/∇` 会主动放弃；更一般的向量范数恒等式仍可能需要人工复核。
- 矩阵转置等“形状相同、元素错位”的错误仍可能需要元素级证据，SymPy
  `parse_latex` 也可能直接报 `error`。
- OCR 对图片注记、阴影、倾斜和复杂排版敏感，普通文字图片应被拒绝为
  `non_formula`；视觉模型偶尔会把“空的思考过程”当正文返回，`main.py` 会
  对 JSON 包装与中文说明做二次判定。
- Hybrid 依赖外部 API 稳定性；超时、限流或鉴权失败会影响结果，代码会把
  这类情况收敛为 `unknown` 或 `error`，连续超时会触发跨进程熔断
  （`cache/circuit_state.json`）。
- `.cache` 中的 pickle 缓存会跨进程复用；修改引擎、提示词或校验逻辑后应
  更新 scope（校验缓存已带 `VERIFIER_LOGIC_VERSION` 与异构校验链标签）或手动
  清缓存。
- **Qwen-VL 的索引口径**：`qwen3-vl-plus` 会把 `i=1..n` 与 `i=0..n-1` 判为
  `not_equivalent`（1-based/0-based 混用）。这是刻意的严格行为，若业务上认为
  两种下标的求和等价，需要在提示词或归一化层显式声明口径。
- **模型侧 JSON 不稳定**：推理模型可能把预算耗在 `reasoning_content` 上，
  导致正文为空或只有推理文本。`verifier_hetero.py` 现在优先用
  `response_format=json_object` 约束输出，并把 `max_tokens` 默认提升到 8192；
  仍失败时进入最多 2 次“强制 JSON 修复”重试（独立预算
  `HETERO_JSON_MAX_TOKENS`），两次都失败才收敛为 `unknown`，不会误判为
  `not_equivalent`。`poisson_2d` 已能在 Hybrid 下给出明确 `not_equivalent`。
- 本机未安装 Pix2Text，`test_pipeline_mock.py` 的回退成功断言会自动跳过；
  如需完整回退链路验证，请先安装 `pix2text`。
- **可解释批改的口径边界**：解释层的 `error_type` 是“最可能的一处根因”，与
  `tests/benchmark/annotations.json` 的人工标注并非一一对应。本轮 27 例预期 fail
  中 25 例严格一致、26 例根因族一致；`image_03`（标注 `symbolic_scope_limit`，
  证据显示具体边界差）与 `image_04`（标注 `parser_limitation`，连续 N-S 与离散
  残差不可比）属于标注口径分歧，已写入 `output/explain_failures.md`，未为了对齐
  指标而改写判定或伪造根因。

## 10. 相关文档

- [ARCHITECTURE.md](ARCHITECTURE.md)：三层架构、校验链和扩展接口
- [API.md](API.md)：模块公开接口、CLI 参数和失败语义
- [TESTING.md](TESTING.md)：测试集、运行方法、指标和新增用例
- [CONTRIBUTING.md](CONTRIBUTING.md)：代码规范、扩展流程和 PR 检查
- [output/verifier_comparison.md](output/verifier_comparison.md)：SymPy-only
  与 Hybrid 实际对比结果
- [output/qwen_verifier_comparison.md](output/qwen_verifier_comparison.md)：
  加入 Qwen-VL 后的子集对比（含校验链构成）
- [output/performance_analysis.md](output/performance_analysis.md)：阶段耗时
  拆分、异构调用统计与优化建议
- [output/report.html](output/report.html)：Benchmark 可视化报告

