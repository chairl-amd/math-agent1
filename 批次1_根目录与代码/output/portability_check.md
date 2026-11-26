# 可移植性验收报告（工单4）

- 验收时间：2026-11-16 17:07（Asia/Shanghai）
- 验收方式：全仓只读扫描 + 临时目录干净环境冒烟 + tests/ 全量回归 + 判定链哈希比对
- 结论：**新增 7 个文件、现有源码改动 0 行、20/20 测试文件通过、无真实 Key 进入新文件/交付包**

---

## 1. 脱敏扫描（全仓，含 `.bak` / `output` / `cache`）

扫描范围：`D:\math-agent\` 下**全部 4,456 个非二进制文件**（含 `.bak.*`、`output\`、`tests\`、`scripts\`；
排除 `.python312\`、`third_party\` 的依赖源码）+ 4 个 `pkl` 缓存 + `cache\circuit_state.json` 的二进制 ASCII 扫描。

判定规则：`sk-` 后接 ≥16 位字符视为「疑似真 Key」，逐个人工复核长度与上下文。

| 命中位置 | 长度 | 判定 | 处理 |
|---|---|---|---|
| `scripts/create_shortcut.py.bak.w13_20261116_1615:23` | 51 | **真实 Key**（`set DASHSCOPE_API_KEY=...`，w13 轮次留下的 `.bak`，早于本轮） | **未动**（本轮铁律：`.bak.*` 禁改/禁删）；**排除在交付包外**；建议用户轮换 Key 后手动删除 |
| `tests/test_qwen_verifier.py:25,29,79,119,124` | 16/17 | 假值 `sk-test-deep...(假值)` / `sk-test-dash...(假值)`（测试夹具） | 保留（tests/ 禁改，去掉会破坏测试） |
| `output/_w11_baseline_tests.log:189` | 17 | 上条测试的日志回显（假值） | 保留（历史日志） |
| `README.md.bak:74` 等 3 个历史 README 备份 | 11 | 文档占位示例 `sk-xxx` | 保留（历史文档） |

**新增文件专项复核（7 个文件逐个 grep）**：

```
.env.example            仅 YOUR_DEEPSEEK_KEY_HERE / YOUR_DASHSCOPE_KEY_HERE 占位，0 个 sk-
requirements.txt        0 个 sk-
start.bat               0 个 sk-（Key 只从 .env 读，脚本内不存）
run_demo.py             0 个 sk-
SETUP_给别人用.md        0 个 sk-
output/portability_audit.md   0 个 sk-（只记录位置，全部脱敏）
output/portability_check.md   0 个 sk-
```

> 说明：`.env` 实际文件当前**不存在**（本机靠系统环境变量跑），因此不存在「真 Key 落在 `.env` 里被打包」的风险；
> 交付包也会显式排除 `.env`。

---

## 2. 干净环境冒烟（证明适配层不依赖本机路径）

做法：在 `D:\math-agent\.tmp\clean_env\` 重建一个**换路径**的最小运行环境：

```
.tmp\clean_env\
  agent\  web\            ← 现有代码原样复制
  .python312              ← 目录联接（junction）指向真实解释器，只为省拷贝
  start.bat  run_demo.py  requirements.txt  .env.example
  .env                    ← 由 .env.example 复制，填的是假 Key（sk-fake-*），并加 MATH_AGENT_PORT=7861
```

结果：

| 检查项 | 结果 |
|---|---|
| `start.bat --check` 自定位 | `base = D:\math-agent\.tmp\clean_env`（**没有**用到 `D:\math-agent`） |
| `.env` 解析 | 两个 Key 均 `set`、`QWEN_VERIFIER_MODEL=qwen3-vl-plus`、`port=7861` |
| 依赖探测 | `deps ok`（gradio/openai/sympy/PIL 全部就位） |
| 启动 Web | `python ...\clean_env\web\app.py --host 127.0.0.1 --port 7861` |
| HTTP 探测 | **HTTP 200**，页面长度 94,423 字节（与主目录启动时一致） |
| `run_demo.py --check --no-env` | 正确报告「主模型Key 缺失、依赖全部就位、可以启动」 |
| 清理 | junction 已用 `rmdir` 解除（原件 19,386 文件完好），临时目录已删 |

> 全程使用**假 Key**，未产生任何模型调用费用。
> 该目录里出现过的假 Key（形如 `sk-fake-` 开头的短串）只存在于已删除的临时 `.env`，未写入任何新增交付文件。

---

## 3. 全量回归（tests/ 20 个文件）

命令：逐个 `D:\math-agent\.python312\python.exe tests\test_*.py`（环境变量为假 Key，测试全部离线/替身）。

| # | 文件 | 退出码 | PASS 标记 | FAIL 标记 |
|---|---|---|---|---|
| 1 | test_cache.py | 0 | 28 | 0 |
| 2 | test_circuit_persistence.py | 0 | 21 | 0 |
| 3 | test_cross_representation.py | 0 | 15 | 0 |
| 4 | test_edge_cases.py | 0 | 20 | 0 |
| 5 | test_explain.py | 0 | 35 | 0 |
| 6 | test_index_scopes.py | 0 | 3（`Ran 4 tests / OK`） | 0 |
| 7 | test_inner_product.py | 0 | 6 | 0 |
| 8 | test_latex_extract.py | 0 | 1（`Ran 5 tests / OK`） | 0 |
| 9 | test_latex_normalization.py | 0 | 15 | 0 |
| 10 | test_matrix_chain.py | 0 | 11 | 0 |
| 11 | test_matrix_parse.py | 0 | 4（`Ran 6 tests / OK`） | 0 |
| 12 | test_pde_analysis.py | 0 | 16 | 0 |
| 13 | test_pipeline_mock.py | 0 | 18 | 0 |
| 14 | test_qwen_verifier.py | 0 | 19 | 0 |
| 15 | test_ref_normalize.py | 0 | 24 | 0 |
| 16 | test_translation_stability.py | 0 | 25 | 0 |
| 17 | test_verifier_chain.py | 0 | 23 | 0 |
| 18 | test_w10_rules.py | 0 | 26 | 0 |
| 19 | test_w2_hybrid_offline.py | 0 | 14 | 0 |
| 20 | test_web_batch.py | 0 | 21 | 0 |

**20/20 全过，合计 0 个 FAIL。** 原始日志：`.tmp\tests_run_w14.log`（本轮临时文件）。

---

## 4. 判定链零改动（哈希逐字节比对）

本轮开始前记录指纹（见 `output/portability_audit.md` 附录），回归结束后重算，**全部一致**：

| 文件 | SHA256 前 16 位 | 字节 |
|---|---|---|
| agent\verify.py | 70E3C720F77FBB8F | 83576 |
| agent\symbol_norm.py | B0AAE61322854FD4 | 12659 |
| agent\pde_analysis.py | DA56A67028EF0512 | 12614 |
| agent\verifier_hetero.py | 97D98B7FDBA2B8F0 | 60323 |
| agent\llm.py | 3BBC4DC327759C1D | 31768 |
| agent\main.py | 8CE4CEB1E9799EB4 | 52777 |
| agent\config.py | 2EE6651647BAC10C | 6633 |
| agent\explain.py | 0738AE4A88D3953E | 40607 |
| agent\cache.py | 66232955F7D1E167 | 15876 |
| agent\circuit_breaker.py | 9A1847C021200479 | 18450 |
| agent\ocr_pix.py | ACFD7482AE704A23 | 14653 |
| agent\ocr_vision.py | 70CAC9D0F2E26BBD | 11260 |
| agent\matrix_chain.py | B0D0B912A7E714ED | 18290 |
| agent\metrics.py | 8A0F379A0CAB8CBB | 6074 |
| agent\ref_normalize.py | 6C69BF060D7819B6 | 7882 |
| web\app.py | 2A415C24F66DD7A9 | 37489 |
| tests\benchmark\annotations.json | C6C23A0C9E45D563 | 24031 |
| tests\benchmark\run_benchmark.py | 600139DB2CE5125D | 25467 |
| README.md | 094258CE9CBA22BA | 32896 |
| README_人话版.md | 839998401133B421 | 4551 |
| ARCHITECTURE.md | E7D53B14BF50873E | 26177 |
| TESTING.md | 4B3FAA5CC1C4A94D | 13430 |
| start_math_agent.bat | 7A97CD2ABECC2D7D | 3499 |

判定链、Pipeline、Web、文档、既有启动器**一个字节都没变** → 现有能力不可能因本轮改动而退化。

### 关于 50 例基准 45/50

- 定死口径（以此为准）：**Hybrid 45/50 = 90.00%**（baseline 90.91%、stress 88.24%），正类 81.82%、负类 96.30%、fail_F1 0.9123。
- 判定链逐字节未变，故该指标**不可能回退**；本轮**未重跑全量 benchmark**（50 例 ≈ 20+ 分钟且会真实烧额度，本工单明确不跑）。
- 最近一次落盘记录 `output/benchmark_w12_hybrid/benchmark_result.json`（2026-11-16 15:28）为 **47/50 = 94.00%**，属既定「同一批偶尔飘 1~2 题」的观测抖动，**不作为新口径**。
- 仍如实保留的 5 例边界未动：手写极细撇号（VLM 视觉上限）、连续 PDE vs 离散实现（数学上无可证代数恒等）等；**没有为刷分新增任何判定规则**。

---

## 5. 新增文件列表（本轮唯一改动 = 新增）

| # | 文件 | 用途 |
|---|---|---|
| 1 | `D:\math-agent\.env.example` | 配置模板（占位 Key，复制成 `.env` 使用） |
| 2 | `D:\math-agent\requirements.txt` | 依赖冻结（gradio 6.32.0 / openai 3.51.0 / sympy 1.14.0 / pillow 13.0.0 / numpy 2.5.4；pix2text、python-docx 标注为可选） |
| 3 | `D:\math-agent\start.bat` | 可移植启动器（自定位 + 读 `.env` + 自动选解释器 + 默认 7860） |
| 4 | `D:\math-agent\run_demo.py` | Python 版启动器（`--check` / `--open` / `--port` / `--no-env`） |
| 5 | `D:\math-agent\SETUP_给别人用.md` | 大白话安装说明 |
| 6 | `D:\math-agent\output\portability_audit.md` | 工单1 只读盘点（路径/Key/import/端口） |
| 7 | `D:\math-agent\output\portability_check.md` | 本文件（工单4 验收 + 工单6 完整包清单） |

工作区中**没有**任何既有文件被覆盖、移动或删除（上表哈希可证）。

---

## 6. 例外改动列表

**无。例外改动 = 0 行。**

原因：`agent/config.py:15`、`agent/circuit_breaker.py:32`、`web/app.py:24` 已用 `Path(__file__)` 派生根目录，
启动脚本只需「自身目录 + 相对路径 + 环境变量」即可换机器运行，**没有任何写死路径需要打补丁**。
因此未触碰 `agent/config.py`，也不需要 `MATH_AGENT_ROOT` 的源码级兜底（该变量只由新增的启动器解释，属于新文件内部逻辑）。

---

## 7. 一键还原（撤销本轮全部新增）

在 PowerShell 里执行（只删除本轮新增的 7 个文件 + 桌面交付包文件夹，**不碰任何既有文件**）：

```powershell
Remove-Item 'D:\math-agent\.env.example'            -Force -ErrorAction SilentlyContinue
Remove-Item 'D:\math-agent\requirements.txt'        -Force -ErrorAction SilentlyContinue
Remove-Item 'D:\math-agent\start.bat'               -Force -ErrorAction SilentlyContinue
Remove-Item 'D:\math-agent\run_demo.py'             -Force -ErrorAction SilentlyContinue
Remove-Item 'D:\math-agent\SETUP_给别人用.md'        -Force -ErrorAction SilentlyContinue
Remove-Item 'D:\math-agent\output\portability_audit.md' -Force -ErrorAction SilentlyContinue
Remove-Item 'D:\math-agent\output\portability_check.md' -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Math-Agent-给别人用') -Recurse -Force -ErrorAction SilentlyContinue
```

还原后工作区即回到本轮开始前的状态（既有文件从未被修改，无需从 `.bak` 恢复）。

---

## 8. 本轮实际消耗

- **开发代理（DEV 池）**：约 **4.0 USD**（约 1 小时、~80 次工具调用、含一次 366 MB 整包复制）
- **产品调用（RUN-DEEPSEEK 池）**：**0.00 USD** —— 两次 Web 冒烟只做了页面 GET（200），
  所有 Key 均为假值（`smoke-test-not-a-real-key` / `sk-fake-*`），**未发出任何模型请求**。
- BRAIN 池：未参与。

---

## 9. 桌面完整包清单 + 排除清单 + Key 扫描结果（工单6 追加）

### 9.1 位置与规模

- 路径：`C:\Users\28481\Desktop\Math-Agent-给别人用`（由 `[Environment]::GetFolderPath('Desktop')`
  解析，未写死用户名/路径）
- 规模：**16,961 个文件 / 371.2 MB**（其中 `.python312` 约 366 MB）
- 复制方式：`robocopy D:\math-agent <桌面文件夹> /E /MT:16`，**只复制，不移动、不删除**工作区任何原件
- 顶部目录：`.python312/ .tools/ acl-recovery/ agent/ assets/ backup/ docs/ gradio/ output/ scripts/ tests/ third_party/ web/`

### 9.2 必须保留项（逐项实测）

| 项 | 结果 |
|---|---|
| `agent/`（含 verify.py、main.py、verifier_hetero.py…） | ✅ |
| `web/`（app.py） | ✅ |
| `tests/`（20 个 test_*.py + benchmark 目录） | ✅ |
| 50 例测试图（benchmark 下 png/jpg） | ✅ **50 张** |
| `annotations.json` / `make_hard_cases.py` / `run_benchmark.py` | ✅ |
| `scripts/`（eval_explain_quality.py 等） | ✅ |
| `output/` 下全部 `.md` 报告（含本文件与 audit） | ✅ |
| `README.md` / `README_人话版.md` / `ARCHITECTURE.md` / `TESTING.md` / `API.md` 等文档 | ✅ |
| `requirements.txt` / `.env.example` / `start.bat` / `run_demo.py` / `SETUP_给别人用.md` | ✅ |
| `.python312`（便携解释器） | ✅ 已复制；包内 `python.exe` 实测输出 **3.12.8** |

### 9.3 排除清单（实测均为 0）

| 排除项 | 目标包内数量 |
|---|---|
| 所有 `*.bak*` 备份 | **0** |
| `cache/` 与 `*.pkl` 缓存产物 | **0** |
| `output/web_history.json` | **0** |
| 任何 `.env` / `.env.local.bat` | **0** |
| `__pycache__/` | **0** |
| `.tmp*` 临时目录 | **0** |
| `.ssh/`（虚拟机相关凭据） | **0** |
| `*.pyc` | **0** |

> 说明：`.cache`、`cache`、`.tmp*`、`__pycache__`、`.ssh` 整目录排除；`*.bak*`、`*.pkl`、`.env`、
> `web_history.json` 按文件名排除。`output/` 的历史 benchmark 结果目录随包保留（报告可对照），
> 其中不含任何 Key。

### 9.4 Key 扫描结果（目标文件夹全量，含 `.python312` 依赖源码）

- `sk-` 后接 **≥16 字符**的疑似真 Key：**0 处**
- `sk-` 后接 5–15 字符：16 处，逐条核对全部**无害**：
  - `portability_audit.md` / `portability_check.md` 自身的脱敏写法（`sk-<N>chars` 等），共 10 处；
  - `output/_w11_baseline_tests.log:189` 历史测试日志回显（假值）；
  - `tests/test_qwen_verifier.py:25,29,79,119,124` 测试夹具假值（去掉会导致测试失败，且肉眼可辨为 `sk-test-*`）。
- `.env.example`：仅 `YOUR_DEEPSEEK_KEY_HERE` / `YOUR_DASHSCOPE_KEY_HERE` 两处占位符（第 14、21 行），无任何真 Key。
- 结论：**完整包内无任何真实 Key**；工作区里那份含真 Key 的 `.bak`（见第 1 节）已被排除在包外。

### 9.5 纸条与包内容一致性

`说明_先看我.txt` 写的是「**已经自带 Python（.python312，3.12.8），不用另外装、也不用 pip install**」，
与实际复制结果一致（`.python312` 确实在包内且实测可用），**没有**同时出现「需自己装 Python」的矛盾表述。

### 9.6 工作区原件

`D:\math-agent\` 下所有原件**保持原位**：复制前已存在文件一个未改名、未移动、未删除；
新增的 7 个文件仍在工作区；判定链哈希与第 4 节一致。桌面侧**未启动任何服务、未跑全量测试**，
只做了文件复制、静态路径核对，以及一次 `python.exe` 版本确认。
