# 可移植性只读盘点（工单1）

- 盘点时间：2026-11-16 17:03（Asia/Shanghai）
- 盘点方式：**纯只读**。`Get-ChildItem` 列目录 + `Select-String` 内容检索 + `pip freeze` 只读。
  本轮未修改、未移动、未删除任何既有文件；本文件是唯一新增产物。
- 扫描范围：`D:\math-agent\` 全仓（排除 `.python312\`、`third_party\`、`assets\` 的体积型依赖，
  这三处单独说明）。共扫文本类文件 2830 个（含 `.bak.*`）、全类型文件 4456 个（含 pkl 二进制扫描）。
- 密钥口径：本文件**只记录位置与行号，不记录任何 Key 内容**；所有命中均已脱敏为 `sk-<N>chars`。

结论先行：**运行时路径全部由 `Path(__file__)` 派生（`agent/config.py:15`、`agent/circuit_breaker.py:32`、
`web/app.py:24`），启动只需「设置环境变量 + 用相对路径调用解释器」，无需改动任何现有源码。**

---

## ① 绝对路径 / 盘符 / 用户名清单

### 1.1 运行时相关（决定别台机器能否启动）

| 文件:行号 | 类型 | 内容性质 | 影响 | 建议绕法 |
|---|---|---|---|---|
| `agent/config.py:15` | 路径派生 | `BASE_DIR = Path(__file__).resolve().parent.parent` | **无影响**（自动跟随所在目录） | 无需改动 |
| `agent/circuit_breaker.py:32` | 路径派生 | `BASE_DIR = Path(__file__).resolve().parent.parent` | 无影响 | 无需改动 |
| `agent/circuit_breaker.py:33` | 注释 | 注释里写了 `D:\math-agent\cache\`（第 34 行实际用 `BASE_DIR / "cache"`） | 无影响，仅注释过期 | 不改（注释，不碰源码） |
| `web/app.py:24` | 路径派生 | `BASE = Path(__file__).resolve().parent.parent`，并 `sys.path.insert` | 无影响；**从任意 cwd 启动都能找到 `agent/`** | 无需改动 |
| `tests/benchmark/run_benchmark.py:32,73,74` | 默认输出目录 | 第 32 行 `DEFAULT_OUT_DIR = BASE / "output"`；第 74 行 help 文本里写死 `D:\math-agent\output` | 实际行为正确（走 BASE），仅 `--help` 文案过期 | 不改（tests/ 禁改；需要时可 `--output-dir` 覆盖） |
| `tests/benchmark/compare_verifiers.py:15,18` | 解释器路径 | `ROOT_DIR/.python312/python.exe`（相对 ROOT_DIR） | 只有「对比脚本」需要；包内带 `.python312` 即可用 | 不改；不带解释器时该脚本不可用（非启动必需） |
| `scripts/create_shortcut.py:29,30` | 生成器 | 按 `BASE_DIR/.python312/python.exe` 生成含 `D:\` 硬编码的 `start_math_agent.bat` | **已存在的历史产物** `start_math_agent.bat` 内含 `D:\math-agent\...` 绝对路径（第 12-13 行） | 本轮**不重新生成、不修改**；改用新增的 `start.bat`（自身目录定位） |
| `scripts/clean_pip_leftovers.ps1:32` | 维护脚本默认参数 | `[string]$Root = 'D:\math-agent'` | 仅清理残留目录用，不影响启动 | 需要时显式传 `-Root <新路径>` |
| `tests/benchmark/annotations.json:5` | 元数据 | `"source_dir": "C:\\Users\\28481\\Desktop\\豆包"`（仅溯源备注；逐例图片用相对路径 `image` 字段） | 无运行时影响 | 不改（tests/ 禁改） |

### 1.2 文档中的绝对路径（只影响阅读，禁改）

| 文件:行号 | 内容 |
|---|---|
| `README.md:71,73,80,445,642` | 项目目录 `D:\math-agent`、解释器 `.python312\python.exe`、`Set-Location D:\math-agent` |
| `README_人话版.md:53,75,78` | `D:\math-agent\output\`、`D:\math-agent\.python312\` |
| `API.md:4,5,19` | `D:\math-agent\`、便携解释器路径 |
| `ARCHITECTURE.md:19,337` | `BASE_DIR` 示例值 `D:\math-agent`、写入范围 |
| `TESTING.md:4` | 命令均在 `D:\math-agent` 下执行 |
| 历史 `.bak.*` 文档（README/ARCHITECTURE 等 10 余个） | 同上，均为历史快照 |

> 这些是**文档叙述**，不是运行配置；按本轮约束一律不动。

### 1.3 产物/日志里的绝对路径（历史报告，不影响运行）

`output\*.json`、`output\*.md`、`output\benchmark_*\...` 中共 1000+ 处 `D:\math-agent\...`，
例如 `output\stress_test_report.json`（105 处）、`output\qwen_verifier_comparison.json`（71 处）、
`output\explain_quality.json`（27 处）。性质：**历史运行结果里记录的输入/输出路径**，属于只读产物。
建议：交付包保留 `output\*.md` 报告即可，`output\benchmark_w*` 之类的大目录可不带走（非必需）。

---

## ② `sk-` 开头 Key 与 Key 引用位置（只记位置，不输出内容）

### 2.1 真实 Key 残留：**1 处（既有 `.bak` 文件，本轮无权修改）**

| 文件:行号 | 类型 | 说明 |
|---|---|---|
| `scripts/create_shortcut.py.bak.w13_20261116_1615:23` | **真实 Key（51 字符，`sk-gr...C4Y`）** | 该行形如 `set DASHSCOPE_API_KEY=sk-<51chars>`。文件是 **w13 轮次生成的 `.bak` 备份**（2026-11-16 16:15），早于本轮。 |

处置说明（重要）：
- 本轮约束明确「所有 `.bak.*` 不许动」，因此**未删除、未改写**该文件；
- 该文件**已被排除**在桌面完整包之外（打包排除 `*.bak.*`），不会外泄给收件人；
- 建议用户后续手动处理：删除该 `.bak`，并在控制台**轮换这两个 Key**（一分钟操作，非阻塞项）。

### 2.2 假 Key / 占位符（可安全保留，均为测试夹具）

| 文件:行号 | 长度 | 性质 |
|---|---|---|
| `tests/test_qwen_verifier.py:25,29,79,119,124` | 16/17 | `sk-test-deep...(假值)` / `sk-test-dash...(假值)` 测试假值 |
| `output/_w11_baseline_tests.log:189` | 17 | 上一条测试的日志回显（假值） |
| `README.md.bak:74` 等 3 个历史 README 备份 | 11 | 文档里的 `sk-xxx` 占位示例 |

### 2.3 Key 的读取位置（全部走环境变量，无硬编码）

- 主 Key：`agent/config.py:27`（`DEEPSEEK_API_KEY`），缺失即 `RuntimeError`（第 31-32 行）
- 网关：`agent/config.py:28`（`OPENAI_BASE_URL`，默认 `https://api.openai-next.com/v1`）
- Qwen：`agent/config.py:41-44`（`QWEN_API_KEY` 或 `DASHSCOPE_API_KEY`）、`:47-48`（`QWEN_VERIFIER_MODEL`，默认 `qwen3-vl-plus`）、`:59-62`（`QWEN_BASE_URL`，默认 DashScope 官方端点）
- GLM：`agent/config.py:39,45,63`
- 运行期检查/透传：`web/app.py:971`、`agent/llm.py:50-52`、`stress_test.py:178,414,638`、
  `tests/benchmark/run_benchmark.py:420-423`、`tests/benchmark/compare_verifiers.py:111,552`、
  `scripts/eval_explain_quality.py:339`、`scripts/verify_explain_web.py:122`、`tests/verify_hetero_live.py:307,312`
- 测试夹具赋值（假值）：`tests/test_edge_cases.py:28`、`test_explain.py:24`、
  `test_latex_normalization.py:20`、`test_translation_stability.py:23`、`test_w10_rules.py:20`、
  `test_w2_hybrid_offline.py:16`、`test_qwen_verifier.py:25,29`
- 既有启动器的 Key 来源：`start_math_agent.bat` 第 18-27 行（可选 `.env.local.bat` + Windows 用户级环境变量），
  `scripts/create_shortcut.py:52-53` 同款逻辑

### 2.4 二进制/缓存扫描

`cache\circuit_state.json`、`.cache\*.pkl`（explain/ocr/translate/verify 共 4 个）、
`output\web_history.json`（**不存在**）：逐字节 ASCII 扫描，**均无 `sk-` 命中**。
`output/web_history.json` 当前不存在，说明本机 Web 历史未落盘或已被清理；打包时仍按排除清单处理。

---

## ③ 第三方 import 清单（→ requirements.txt 依据）

直接 import（`agent/`、`web/`、`scripts/`、`tests/`、根目录 `.py`，共 110 个文件）：

| import 名 | pip 包 | 已装版本 | 必需性 |
|---|---|---|---|
| `gradio` | gradio | 6.32.0 | **必需**（Web 界面；`web/app.py:32`、`scripts/verify_explain_web.py`） |
| `openai` | openai | 3.51.0 | **必需**（`agent/llm.py` 模型调用） |
| `sympy` | sympy | 1.14.0 | **必需**（判定链符号层） |
| `PIL` | pillow | 13.0.0 | **必需**（图片读取/缩放） |
| `numpy` | numpy | 2.5.4 | 间接必需（gradio/pandas 依赖；源码未直接 import） |
| `docx` | python-docx | **未安装** | 可选（只有根目录 `read_docx_1.py`、`read_docx_2.py` 两个一次性脚本用） |
| `pix2text` | pix2text | **未安装** | 可选（`agent/ocr_pix.py:30-33` 已 try/except，缺失时抛 `OcrUnavailableError` → 走 `[SKIP]`/VLM 回退） |
| `requests` | requests | 2.34.2 | 间接（`urllib/http` 为主；装 gradio 会带上） |

标准库覆盖其余全部 import（`json/os/pathlib/re/argparse/dataclasses/threading/pickle/hashlib/base64/...`），
**无需额外安装**。

环境基线：`D:\math-agent\.python312\python.exe` = **Python 3.12.8**，`pip freeze` 共 **62** 个包；
`.python312` 为**便携式独立解释器**（无 `pyvenv.cfg`，自带 `python312._pth`，内含
`python312.zip`、`.`、`Lib\site-packages` 且 `import site`），体积 **366.3 MB / 19,384 文件**。

---

## ④ 现有启动方式与端口

| 入口 | 命令 | 端口/地址 | 备注 |
|---|---|---|---|
| Web 界面 | `.python312\python.exe web\app.py` | `127.0.0.1:7860`（`--host` / `--port` 可改，`web/app.py:958-961`） | 主入口；缺 Key 只告警不崩（`:971`），但调用会失败 |
| 一键启动器（既有） | 双击 `start_math_agent.bat` | 同上 7860 | 内含 `D:\math-agent` 硬编码（第 12-13 行），**别台机器不可直接用**；本轮不改它，另建 `start.bat` |
| 启动器自检 | `start_math_agent.bat --check` | — | 打印 BASE/PY/APP/Key 是否就位 |
| 命令行 Pipeline | `.python312\python.exe agent\main.py <图片>` | — | 需 `DEEPSEEK_API_KEY` |
| Benchmark（50 例） | `.python312\python.exe tests\benchmark\run_benchmark.py` | 默认输出 `output\` | 会真实调用模型（费额度），本轮不跑全量 |
| 解释层网页自检 | `.python312\python.exe scripts\verify_explain_web.py` | `127.0.0.1:7867` | 备用端口，非主入口 |
| 单元测试 | 逐个 `.python312\python.exe tests\test_*.py` | — | 共 **20** 个 `test_*.py`（未装 pytest，均为 unittest 风格） |

端口冲突提示：7860 若被占，用 `web\app.py --port 7861`；`scripts\verify_explain_web.py` 用 7867，互不冲突。

---

## ⑤ 结论与工单2 适配层设计

1. 运行时路径已全部相对化 → **工单3 预计「零改动」**：只要「chdir 到项目根 + 注入环境变量 + 相对路径调用 python」即可。
   因此**不需要**触碰 `agent/config.py` 之类配置读取处。
2. 缺口只有三个：**没有 `.env.example`、没有 `requirements.txt`、没有「换台机器也能双击」的启动器**。
   → 工单2 新增：`.env.example`、`requirements.txt`、`start.bat`、`run_demo.py`、`SETUP_给别人用.md`。
3. 别台机器若不带 `.python312`：需自行安装 Python 3.12 + `pip install -r requirements.txt`；
   若带 `.python312`（366 MB）则双击即用 —— 工单6 的二选一以**实际复制结果**为准。
4. 唯一 Key 残留是既有 `.bak`（见 2.1），**打包必须排除 `*.bak.*`**，并建议用户轮换 Key。

---

## 附：本轮基线指纹（用于工单4 证明「判定链零改动」）

SHA256 前 16 位 + 字节数：

```
agent\verify.py                     70E3C720F77FBB8F  83576
agent\symbol_norm.py                B0AAE61322854FD4  12659
agent\pde_analysis.py               DA56A67028EF0512  12614
agent\verifier_hetero.py            97D98B7FDBA2B8F0  60323
agent\llm.py                        3BBC4DC327759C1D  31768
agent\main.py                       8CE4CEB1E9799EB4  52777
agent\config.py                     2EE6651647BAC10C   6633
agent\explain.py                    0738AE4A88D3953E  40607
agent\cache.py                      66232955F7D1E167  15876
agent\circuit_breaker.py            9A1847C021200479  18450
agent\ocr_pix.py                    ACFD7482AE704A23  14653
agent\ocr_vision.py                 70CAC9D0F2E26BBD  11260
agent\matrix_chain.py                B0D0B912A7E714ED  18290
agent\metrics.py                    8A0F379A0CAB8CBB   6074
agent\ref_normalize.py              6C69BF060D7819B6   7882
web\app.py                          2A415C24F66DD7A9  37489
tests\benchmark\annotations.json    C6C23A0C9E45D563  24031
tests\benchmark\run_benchmark.py    600139DB2CE5125D  25467
README.md                           094258CE9CBA22BA  32896
README_人话版.md                     839998401133B421   4551
ARCHITECTURE.md                     E7D53B14BF50873E  26177
TESTING.md                         4B3FAA5CC1C4A94D  13430
start_math_agent.bat                7A97CD2ABECC2D7D   3499
```

最新一次 50 例 Hybrid 记录（`output/benchmark_w12_hybrid/benchmark_result.json`，2026-11-16 15:28）：
50 例、命中 47（94.00%）、正类 20/22、负类 26/27。定死口径为 45/50 = 90.00%；
两者差异属既有观测抖动（该批次历史上在 45–47 之间浮动），**不是回归**。
