# 50 例基准增量审计报告（w12 → 47/50 是否违规）

- 审计时间：2026-11-16 15:55（Asia/Shanghai）
- 审计对象：执行端在 w12 轮汇报的「50 例基准 47/50（94%）」
- 审计依据：文件哈希 + 逐行 diff + 逐例报告复核（全部为本地只读审计，**未新增任何判定逻辑**）
- 定死口径：**45/50**（w10_final，2026-11-15 22:43）—— 本审计**不修改**该口径
- 结论：**未发现违规刷分，不需回滚**；47/50 的 +2 全部可归因于「校验链组成 + 单次运行方差 + 铁律强制边界」

---

## 1. 审计结论（一句话版）

判定层四个文件（`verify.py`、`verifier_hetero.py`、`symbol_norm.py`、`llm.py`）与 w10_final
备份**逐字节相同**；`main.py` 相对 w10 只多 18 行解释层钩子（无删改）；
`config.py` 的改动是解释层开关 + Qwen 端点等价重写 + 一个只读自检函数，
`QWEN_BASE_URL` 的默认值**仍是** `https://dashscope.aliyuncs.com/compatible-mode/v1`。

因此 47/50 不是「改代码刷出来的」，而是**换了一条校验链 + 一次 OCR 波动 + 铁律 7 让出的 1 分**
三者叠加的结果。按「定死 45/50」的纪律，**对外仍报 45/50**，47/50 只能作为
「同代码、不同链配置的单次观测」记录。

---

## 2. 判定层完整性核验（工单 1 动作 1）

### 2.1 SHA256 比对（前 16 位）

| 文件 | 当前大小/哈希 | w10_final 备份大小/哈希 | 结论 |
|---|---|---|---|
| `agent/verify.py` | 83,576 / `70E3C720F77FBB8F` | 83,576 / `70E3C720F77FBB8F` | **完全一致** |
| `agent/verifier_hetero.py` | 60,323 / `97D98B7FDBA2B8F0` | 60,323 / `97D98B7FDBA2B8F0` | **完全一致** |
| `agent/symbol_norm.py` | 12,659 / `B0AAE61322854FD4` | 12,659 / `B0AAE61322854FD4` | **完全一致** |
| `agent/llm.py` | 31,768 / `3BBC4DC327759C1D` | 31,768 / `3BBC4DC327759C1D` | **完全一致** |
| `agent/main.py` | 52,777 / `8CE4CEB1E9799EB4` | 51,840 / `5853155B2B1D16DD` | 仅新增 18 行（见 2.2） |
| `agent/config.py` | 6,633 / `2EE6651647BAC10C` | 4,421 / `92DFD131E2F3ABE1` | 仅新增开关/等价重写（见 2.3） |

> 比对命令：`Get-FileHash <file> -Algorithm SHA256`；备份文件名
> `agent/<file>.bak.w10_final_20261115_2255`。
> `verify.py`、`verifier_hetero.py` 是铁律明令禁止修改的两个文件，**哈希完全相同**。

### 2.2 `main.py` 的 18 行差异（全部为解释层钩子）

`Compare-Object` 结果：`新增 18 行 / 删除 0 行 / 修改 0 行`，新增内容为：

```python
# ---- 工单1（w11）：可解释批改层 ----
if getattr(config, "EXPLAIN_ENABLED", False):
    try:
        from .explain import attach_explanations
        attach_explanations(report, latex_expr=work_latex,
                            ref_latex=ref_latex, cpp_code=raw_cpp_code)
    except Exception as exc:
        logger.warning("解释层执行失败（判定结论不变）：%s", exc)
```

它位于 `report["ok"]` 落盘**之后**，只写 `steps.checks[i].explain`，不改任何判定字段
（`test_explain.py` 有「关闭前后报告键集合逐键相同」的断言）。

### 2.3 `config.py` 的差异（逐条归类）

| 差异 | 是否影响判定 |
|---|---|
| 新增 `EXPLAIN_ENABLED / EXPLAIN_MAX_TOKENS / EXPLAIN_TIMEOUT / EXPLAIN_CACHE_ENABLED` | 否，仅解释层 |
| `QWEN_BASE_URL` 三行改写为 `env or QWEN_DASHSCOPE_BASE_URL` | **否**，默认值字符串完全相同（`test_qwen_verifier.py` 断言该 URL，已通过） |
| 新增 `qwen_vl_ready()` | 否，只读判断，无调用方改动 |
| `__main__` 打印 3 行自检 | 否 |
| `HETERO_*` 系列开关 | **零改动** |

**判定相关开关（`HETERO_VERIFY_ENABLED`、`HETERO_ACCEPT_CONSENSUS`、
`HETERO_VERIFY_TIMEOUT`、`HETERO_MAX_TOKENS`）在 diff 中一行未动。**

---

## 3. 指标差异逐例归因（工单 1 动作 2）

### 3.1 三轮的校验链构成（关键前提）

| 轮次 | 运行模式 | 实际异构校验器 | 报告里的 `verifier_count` | 命中 |
|---|---|---|---:|---:|
| `benchmark_w10_final`（定死口径） | `auto` + `DASHSCOPE_API_KEY` | **qwen-vl** | 2（SymPy + Qwen） | **45/50** |
| `benchmark_w11_regression` | `hybrid` | **deepseek** | 2（SymPy + DeepSeek） | 46/50 |
| `benchmark_w12_hybrid`（本轮） | `hybrid` | **deepseek** | 2（SymPy + DeepSeek） | **47/50** |
| `benchmark_w12_final`（对照） | `auto`，无第三方 Key | **无**（纯 SymPy） | — | 31/50 |

> 即：**45/50 与 47/50 不是同一实验**。45/50 的异构票来自 Qwen-VL，47/50 来自 DeepSeek。
> 统计方式：遍历 50 份逐例报告，读 `checks[0].meta.verifier_count` 与
> `hetero_verdicts[*].meta.verifier`（49 份可比报告，1 份 `image_05` 无 check 节点）。

### 3.2 w10（45）→ w11（46）：换链带来 +3 / −2

| 用例 | 期望 | w10（qwen 链） | w11（deepseek 链） | 归属 | 判定 |
|---|---|---|---|---|---|
| `pde_residual_scope` | fail | `unknown` | `fail` | Qwen 判 unknown，DeepSeek 判 not_equivalent | 合法（二源真实票） |
| `layout_nested_frac` | pass | `fail` | `pass` | **OCR 差异**：w10 读成大写 `X`，w11 读成 `x` | 合法（OCR 修复） |
| `var_sum_sigma` | pass | `fail` | `pass` | `accepted_by=hetero_consensus`（符号层 error + 异构一致等价） | 合法（w10 已有的仲裁逻辑） |
| `image_04` | fail | `fail` | `unknown` | 铁律 7：PDE 恒为 unknown + 人工复核 | **合法但丢 1 分** |
| `var_norm_squared` | pass | `pass` | `fail` | **OCR 误读**：`x_i^2` 被读成 `X_i^4` | 噪音（下一轮自动恢复） |

### 3.3 w11（46）→ w12（47）：同链，仅 1 例差异

| 用例 | 期望 | w11 | w12 | 原因 |
|---|---|---|---|---|
| `var_norm_squared` | pass | `fail` | `pass` | 同一张图，OCR 本轮读出正确的 `\sum_{i=1}^{n} x_i^2`（上轮误读为 `X_i^4`） |

**同链下唯一的 +1 来自 OCR 单次波动，与代码无关。** 这正是「指标有 ±1–2 例自然抖动」的实证。

### 3.4 汇总：45 → 47 的 +2 是怎么来的

```text
45/50（qwen 链）
  +3   pde_residual_scope / layout_nested_frac / var_sum_sigma   ← 换链 + OCR + 既有仲裁
  −2   image_04（铁律让分） / var_norm_squared（OCR 误读）        ← 都不是代码问题
= 46/50（deepseek 链，w11）
  +1   var_norm_squared（OCR 本轮读对）
= 47/50（deepseek 链，w12）
```

### 3.5 仲裁路径的规模（透明披露）

20 个 `pass` 中有 **6 例**不是符号层直接证明的，而是 `accepted_by=hetero_consensus`
（符号层 `unknown/error` + 全部异构校验器一致判等价 + 非 PDE）：

| 用例 | 符号层 | 异构票 | 相对 w10 |
|---|---|---|---|
| `assoc_paren` | unknown | deepseek → equivalent | 原本就是（w10 用 qwen 票） |
| `dot_product` | unknown | deepseek → equivalent | 原本就是 |
| `edge_mlp_chain` | unknown | deepseek → equivalent | 原本就是 |
| `hw_newton` | unknown | deepseek → equivalent | 原本就是 |
| `layout_matrix_frac` | unknown | deepseek → equivalent | 原本就是 |
| `var_sum_sigma` | error | deepseek → equivalent | **本轮新增升级** |

该路径由 `_capability_gap_consensus()` 实现，`verifier_hetero.py` 哈希与 w10_final 相同，
属于**既定架构**而非本轮改动；披露它的意义在于：这 6 例的 `pass` 依赖"第二来源一致"，
一旦异构校验器不可用（如纯 SymPy 模式），它们会退回 `unknown`（对照实验
`benchmark_w12_final` 里 6 例全部变 `unknown`，正是这个原因）。

---

## 4. 是否违规 / 是否需要回滚（工单 1 动作 3）

| 违规类型 | 检查方法 | 结论 |
|---|---|---|
| 改动判定层代码 | `verify.py`/`verifier_hetero.py`/`symbol_norm.py`/`llm.py` SHA256 比对 | **无**（四文件逐字节相同） |
| 强行把 `unknown` 升级为 `equivalent` | 逐例扫 `accepted_by` / `accepted_from_status` | 本轮 **6 例**走该路径：`assoc_paren`、`dot_product`、`edge_mlp_chain`、`hw_newton`、`layout_matrix_frac`、`var_sum_sigma`。均为 w10 既有 `_capability_gap_consensus`（符号层 `unknown/error` + **全部**异构一致等价 + 非 PDE），代码哈希与 w10_final 相同；相对 w10 新增的只有 `var_sum_sigma` 一例 |
| 强行把 PDE 判 pass | 查 `image_04` 及其余 PDE 用例 | **无**：`image_04` 仍是 `unknown` + `needs_human_review=true` |
| 改标注文件迁就结果 | `annotations.json` 大小/mtime | 未改动（24,031 字节，mtime 2026-11-15 20:37:22，与 w10_final 快照一致） |
| 用缓存伪造结论 | 查 `meta.cache` 与 `cache` 统计块 | 49 份可比报告全部 `cache: miss`（真实运行，无缓存复用） |

**审计判定：不构成违规，不执行回滚。**

### 4.1 若上级仍要求回到 45/50 口径，回滚 runbook（已备好，未执行）

```powershell
# 方式 A（推荐，零代码改动）：按 w10 的链复现——用 auto + Qwen Key
$env:DEEPSEEK_API_KEY="<key>"; $env:DASHSCOPE_API_KEY="<key>"
.\.python312\python.exe tests\benchmark\run_benchmark.py --out output\benchmark_rollback_to45

# 方式 B（若确需撤销 w12 的 config 改动）
Copy-Item agent\config.py.bak.w12_20261116_1440 agent\config.py -Force
# 该备份 = 本轮改动前的 config（含 w11 解释层开关、Qwen 官方端点默认值）
```

> 注：方式 A 复现的是「链」，不是「历史随机性」；`image_04` 仍会是 `unknown`（铁律），
> `var_norm_squared` 取决于 OCR 单次结果，因此结果落在 45–47 之间都属正常。

---

## 5. 审计用的复现命令（只读，不产生模型调用）

```powershell
# 1) 判定层哈希比对
Get-FileHash agent\verify.py,agent\verifier_hetero.py -Algorithm SHA256
Get-FileHash agent\verify.py.bak.w10_final_20261115_2255 -Algorithm SHA256

# 2) 逐行 diff（只应看到解释层新增行）
Compare-Object (Get-Content agent\main.py.bak.w10_final_20261115_2255) (Get-Content agent\main.py)

# 3) 逐例归因（只读，打印本轮未命中用例）
.\.python312\python.exe -c "import json; d=json.load(open('output/benchmark_w12_hybrid/benchmark_result.json',encoding='utf-8')); [print(c['id'], c['expected'], c['actual']) for c in d['cases'] if not c['hit']]"

# 4) 校验链构成（每例实际参与的异构校验器）
.\.python312\python.exe -c "import json,glob; [print(f.split(chr(92))[-3], [(h.get('meta') or {}).get('verifier') for h in (json.load(open(f,encoding='utf-8'))['steps']['checks'][0]['meta'].get('hetero_verdicts') or [])]) for f in glob.glob('output/benchmark_w12_hybrid/_benchmark_tmp/*/*/report_*.json')]"
```

---

## 6. 审计员签名栏

| 项 | 值 |
|---|---|
| 审计人 | 总工程师（DSH） |
| 审计方式 | 只读文件审计 + 逐例报告复核，**未运行新基准、未改任何判定代码** |
| 审计样本 | `output/benchmark_w10_final`、`benchmark_w11_regression`、`benchmark_w12_hybrid`、`benchmark_w12_final` |
| 回滚动作 | **未执行**（无违规） |
| 定死口径是否变更 | **未变更**（仍为 45/50） |
