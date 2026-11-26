# 当前能力边界明细表（w12 审计口径）

- 生成时间：2026-11-16 16:05（Asia/Shanghai）
- 数据源：`output/benchmark_w12_hybrid/_benchmark_tmp/*/report_*.json`（50 例逐例报告，只读汇总）
- 执行铁律：**第 7 条** —— `image_04` / PDE 恒为 `unknown` + `needs_human_review`，严禁为刷分判 pass
- 结论：边界项按**类别**统计为 **5 类 / 9 例**；按**单次未命中**统计为 **3 例**；按 **w10 遗留名册**为 **5 例**。
  三个数字互不矛盾，区别只在"数什么"（见 §2）。

---

## 1. 边界名册（5 类 / 9 例，铁律口径）

| # | 类别 | 用例 | 期望 | 实际 | 状态 / 标签 | 人工复核 | 铁律要求 | 处理 |
|---:|---|---|---|---|---|---|---|---|
| 1 | **PDE 不可比** | `image_04` | fail | **`unknown`** | `SYMPY_FAIL` | ✅ `true` | 恒为 unknown + 人工复核 | **保留 `unknown`**，写 `pde_capability_limit`；不得升级 |
| 2 | **校验器冲突** | `softmax` | pass | `unknown` | `CONFLICT` | ✅（状态本身即复核） | 冲突不采信 | 转人工复核，不判 pass |
| 3 | **校验器冲突** | `chain4` | pass | `unknown` | `CONFLICT` | ✅（状态本身即复核） | 冲突不采信 | 转人工复核，不判 pass |
| 4 | **非公式/OCR 空** | `image_05` | skip | `non_formula` | 无 check 节点 | —（正确拒绝） | 不得猜测 | 保持拒绝；`pix2text` 未安装已记录 |
| 5 | **PDE 族（离散侧可比）** | `heat_1d`、`wave_1d`、`poisson_2d`、`ns_incompressible`、`pde_residual_scope` | fail | `not_equivalent` | `CONFIRMED_ERROR` | ✅ `true`（全部） | 必须带人工复核 | 判 fail 但**强制** `needs_human_review=true` + 写 `pde_capability_limit` |

### 1.1 关键字段实证（逐例取自报告 `checks[0].meta`）

| 用例 | `sympy_verdict` | 异构票 | `verdict_label` | `pde_detected` | `needs_human_review` | `accepted_by` |
|---|---|---|---|---|---|---|
| `image_04` | `unknown` | deepseek → `equivalent` | `SYMPY_FAIL` | true | **true** | —（PDE 排除仲裁） |
| `softmax` | `not_equivalent` | deepseek → `equivalent` | `CONFLICT` | — | 状态 = `needs_human_review` | — |
| `chain4` | `not_equivalent` | deepseek → `equivalent` | `CONFLICT` | — | 状态 = `needs_human_review` | — |
| `pde_residual_scope` | `unknown` | deepseek → `not_equivalent` | `CONFIRMED_ERROR` | true | **true** | — |
| `heat_1d` / `wave_1d` / `poisson_2d` / `ns_incompressible` | `unknown` | deepseek → `not_equivalent` | `CONFIRMED_ERROR` | true | **true** | — |

> `image_04` 是唯一停在 `unknown` 的 PDE 用例：异构票判**等价**，而 PDE 排除规则
> 禁止把等价票升级（`_capability_gap_consensus` 内的 `_looks_like_pde` 拦截），
> 于是最终仍为 `unknown` —— 这正是铁律 7 要求的行为。

---

## 2. 「3 例 vs 5 例」口径对账

| 口径 | 定义 | 数量 | 用例清单 | 适用场景 |
|---|---|---:|---|---|
| **A. 单次运行未命中** | 某一次 50 例运行的 `hit=False` | **3** | `image_04`、`softmax`、`chain4` | 汇报"本轮准确率"时的分母说明 |
| **B. w10 定死 45/50 的遗留名册** | `output/failure_analysis.md` §2 的"未修好的 5 例" | **5** | `softmax`、`pde_residual_scope`、`chain4`、`layout_nested_frac`、`var_sum_sigma` | 追历史欠账、做对比基线 |
| **C. 铁律边界名册**（本文件 §1） | 需人工复核 / 无法判定 / 正确拒绝的类别 | **5 类 / 9 例** | 见 §1 | 对外说明"系统能力的真实边界" |

三者为什么不同：

- B 里的 `pde_residual_scope`、`layout_nested_frac`、`var_sum_sigma` 在本轮已转为命中
  （原因见 `output/benchmark_audit_w12.md` §3：换链、OCR 修复、既有仲裁），所以不进 A；
- A 里的 `image_04` 在 w10 曾经"命中"（qwen 判 fail），本轮按铁律退回 `unknown`，
  所以 A 有它而 B 没有它；
- C 是**类别视图**，把 5 个 PDE 用例合并成 1 行，因此"5 类"对应 9 个用例。

**建议对外口径**：准确率报 A（3 例未命中，并注明这是 47/50 那次运行）；
能力边界报 C（5 类 9 例）；历史对比报 B。切勿把 A 的 3 当成"只剩 3 个问题"，
因为 C 里有 5 个 PDE 用例虽然命中，但每一个都带 `needs_human_review=true`。

---

## 3. 全部非 pass 用例的分类统计（30 例）

| 分类 | 数量 | 说明 |
|---|---:|---|
| `not_equivalent`（期望 fail 且命中） | 26 | 真实判错，全部与标注一致；其中 **5 例**属 PDE 族并带 `needs_human_review=true`（`heat_1d`、`wave_1d`、`poisson_2d`、`ns_incompressible`、`pde_residual_scope`；另 `edge_mixed_lang` 见 §4.1） |
| `unknown`（未命中） | 1 | `image_04`（PDE，铁律强制） |
| `needs_human_review`（未命中） | 2 | `softmax`、`chain4`（校验器冲突） |
| `non_formula`（命中，正确拒绝） | 1 | `image_05` |
| **合计** | **30** | 20 例 pass + 30 例非 pass = 50 |

> 注：benchmark 运行器把 `unknown` 与 `needs_human_review` 都归入 `actual=unknown`，
> 因此 `benchmark_result.json` 里看到的是 3 个 `unknown`；逐例报告里才能区分
> `SYMPY_FAIL`（image_04）与 `CONFLICT`（softmax / chain4）。

---

## 4. 审计中发现的两个"非评分"问题（如实记录）

### 4.1 `edge_mixed_lang` 被误标为 PDE

- 用例内容：`W_{k+1} = W_k - η ∇L(W_k)`（梯度下降，属于优化，不是偏微分方程）
- 实际：`pde_detected=true`、`needs_human_review=true`、`pde_capability_limit` 被写入
- 原因：`verifier_hetero._looks_like_pde()` 的关键词表包含 `\nabla`，只要出现就命中；
  而 `explain.pde_strong` 用的是更严的 `∂ / \partial / ∇² / ∇·`，两套口径不一致
- 影响：**不影响结论**（该例本来就被异构票判 `not_equivalent`，且标注就是 fail），
  只增加了人工复核负担
- 建议：把 `_looks_like_pde` 的 `\nabla` 改为 `\nabla\cdot` / `\nabla^2` / `\nabla²`
  （**本轮禁止改判定层，仅记录，建议单独立项并加回归**）

### 4.2 `pix2text` 未安装

- `image_05` 的 OCR 日志：`vision: 模型返回空内容 | pix2text: pix2text 未安装: ModuleNotFoundError`
- 影响：双引擎退化单引擎；该例恰好是扫描件（无公式），仍被正确判为 `non_formula`
- 建议：按 `README.md` §4 的依赖检查补齐 `pix2text`，或在报告中显式标注引擎降级

---

## 5. 边界项与铁律 7 的一致性核验

| 核验项 | 方法 | 结果 |
|---|---|---|
| `image_04` 是否为 `unknown` | 读逐例报告 `checks[0].status` | ✅ `unknown` |
| `image_04` 是否带人工复核 | 读 `meta.needs_human_review` | ✅ `true` |
| 是否存在 PDE 用例被判 `pass` | 扫全部 `pde_detected=true` 用例的 `status` | ✅ 无（5 例均为 `not_equivalent` 或 `unknown`） |
| 是否为刷分强行改判 | 对比 `benchmark_w10_final` 同例结论 | ✅ 无（`image_04` 反而由 fail 退回 unknown，丢 1 分） |
