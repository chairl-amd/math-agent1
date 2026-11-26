# 失败与能力边界分析（工单 2）

- 分析对象：`tests/benchmark/annotations.json` 中 50 个用例（33 个既有基线 + 17 个 w10 新增对抗用例）
- 运行环境：DeepSeek Vision OCR（`deepseek-v4-1-flash-261017`）+ C++ 翻译 + SymPy/Qwen-VL 混合校验
- 结论数据：`output/benchmark_w10_final/benchmark_result.json`

## 1. 问题分类与根因

新增的 17 个对抗用例先跑出 11/17（64.71%），逐例诊断（`tests/benchmark/diagnose_w10.py`）
后归为 6 类根因。下表把「现象 → 根因层 → 修复动作」对齐，便于判断哪一类属于
系统缺陷、哪一类属于能力边界。

| # | 用例 | 现象 | 根因层 | 处理 |
|---|---|---|---|---|
| 1 | `hw_newton` | pass→fail：OCR 输出 `f(X_k)/f(X_k)`，分母撇号丢失 | OCR（视觉模型丢撇号） | 提示词规则 5 要求保留 `f'(x)`；同时把撇号渲染成明确斜线笔画（原渲染只有几像素，模型无从识别） |
| 1b | `hw_newton` | 撇号恢复后仍不一致：OCR 写 `X_{k+1}=X_k-…`，翻译写 `x_{new}=x-…` | 符号归一化（大小写 + 迭代下标） | 新增大小写不敏感回退（仅在严格比较失败后触发），迭代上标原有规则处理 `_{k+1}` |
| 1c | `hw_newton` | 翻译输出 `fx/dfx` 这类变量名，与 `f(x)/f'(x)` 形成伪符号差异 | 翻译提示词（变量映射） | 翻译规则 4：函数值写成 `f(x)`、导数写成 `f'(x)` |
| 2 | `hw_trace` | pass→unknown：OCR 1-based `Σ_{i=1}^{n}a_{ii}`，翻译 0-based `Σ_{i=0}^{n-1}a_{i,i}` | 符号归一化（下标逗号、算子包装） | `normalize_subscript_commas`、`normalize_notation_variants` 让两侧落到同一表达式；`\left|…\right|` 解析修复 |
| 2b | `hw_trace`/`var_sum_sigma` | 同一用例两次运行在 equivalent / not_equivalent 间翻转 | 异构校验稳定性 | 提示词新增逐例 `<index_base_hint>`：确定性告知两侧基准不同，并要求按「元素集合是否相同」判断 |
| 3 | `noise_l1` | pass→fail：Qwen 误判 1-based vs 0-based | 异构校验提示词 | 规则 9（基准差异不算差异、跨度不同才算）+ 逐例提示后稳定判等价 |
| 4 | `layout_matrix_frac` | pass→unknown：SymPy 无法比较“矩阵写法 vs 展开元素写法” | 校验共识策略（架构性） | 新增能力缺口仲裁：SymPy 无结论 + 全部异构校验器一致等价 + 非 PDE → 采信为等价 |
| 5 | `edge_mlp_chain` | pass→unknown：`h_1` 与 `h^{(1)}` 命名差异，SymPy 无结论，Qwen 判等价 | 同 #4 | 同上（共识仲裁） |
| 6 | `var_norm_squared` | pass→fail：翻译自造链式等式 `norm_sq(x)=Σx_i²=x^T x`，解析出 `x*x**(T*mathsf)` | 翻译提示词 | 翻译规则 5：每行一个等式、不得补充代码中没有的恒等式；`\mathsf{T}` 归一化 |
| 7 | `blur_cosine` | fail→unknown：翻译多输出一行 `n=dim(a)`，逐行配对把余弦公式与这一行错配 | 参考配对（多处） | ① 剩余行按符号重叠度配对；② 无等价候选时按“信息量”重排候选（不再固定保留第一行） |

### 1.1 结构性根因（三条，价值最高）

1. **「符号层无结论」被当成「无法判定」**：SymPy 的能力边界（解析失败、
   不可代数化、矩阵写法差异）与「证明不等价」被混在同一个 `unknown` 出口，
   导致异构 VLM 明确判等价也无法进入最终结论。w10 新增
   `HETERO_ACCEPT_CONSENSUS` 仲裁（详见 `ARCHITECTURE.md` 第 6 节）。
2. **提示词层面的「规则淹没」**：编号规则写在 10 条固定条目里，模型会忽略；
   改成「逐例事实提示」（`<index_base_hint>`）后同一用例判定稳定。
3. **翻译层过度生成**：模型倾向补全它认为“显然”的恒等式（链式等式、
   辅助定义行），单行公式因此被撑成多行，配对随之错位。

### 1.2 能力边界（不可修复，已记录）

- `image_04`（N-S 三方程 PDE）：连续 PDE 与离散残差之间不存在 SymPy 可判定的
  代数等价关系，且 PDE 语义校验不可靠。系统按边界返回
  `unknown + needs_human_review`，本次仲裁刻意排除 PDE（`_looks_like_pde`），
  避免把「需人工确认」升级成「等价」。
- `hw_newton` 的 `X_k`（迭代态）与代码中无下标 `x` 的映射：SymPy 会把
  `x_k` 当成独立符号，无法证明等价；异构校验若判等价，与 SymPy 的
  `not_equivalent` 构成冲突，按设计进入 `needs_human_review`，不强行采信。

## 2. 修复后的表现

最终全量（`output/benchmark_w10_final`，50 例，2026-11-15 22:43）：

| 指标 | w9 基线（33 例） | 本次 baseline 子集（33 例） | 本次 stress 子集（17 例） | 本次全量（50 例） |
|---|---:|---:|---:|---:|
| 命中 | 32/33 | 30/33 | 15/17 | 45/50 |
| 总体准确率 | 96.97% | 90.91% | 88.24% | 90.00% |
| 正类召回 | 95.24% | 81.82% | 81.82% | 81.82% |
| 负类召回 | 100% | 96.30% | 100% | 96.30% |
| fail_F1 | 0.9756 | 0.9259 | 0.9231 | 0.9123 |
| 平均耗时 | 24.95s | 26.4s | 22.9s | 25.21s |

新增 17 例对抗用例：**首轮 11/17（64.71%）→ 最终 15/17（88.24%）**，
共修好 6 例（`hw_newton`、`hw_trace`、`noise_l1`、`layout_matrix_frac`、
`edge_mlp_chain`、`blur_cosine`）。其中 `hw_trace`、`var_sum_sigma` 属于
“同一份代码在多次运行间翻转”的用例，修的是稳定性而不是单次结果。

未修好的 5 例（全部可解释，见 §1）：

| 用例 | 期望 | 实际 | 原因 | 归类 |
|---|---|---|---|---|
| `softmax` | pass | fail | 图片 `\sum_j` 与代码 `\sum_{j=1}^{n}`：SymPy 把隐式下界补成 0，Qwen-VL 在“n 项但起点不同”上判不等价（同一用例在不同轮次会翻转） | 异构校验不稳定 |
| `var_sum_sigma` | pass | fail | 同上，VLM 对 0-based/1-based 的容忍度不稳定 | 异构校验不稳定 |
| `layout_nested_frac` | pass | fail | OCR 把内层 `x` 读成大写 `X`；符号层大小写回退因左侧同时出现 `x` 与 `X` 被判为“有意区分”而未触发 | OCR + 归一化边界 |
| `chain4` | pass | fail | 翻译层把 4 矩阵链写成 `z=xy` + `chain4(a,b,c,d)=(((ab)c)d)`，与图片 `E=ABCD` 无法对齐（零样本随机性） | 翻译层过度简化 |
| `pde_residual_scope` | fail | unknown | 翻译输出多行系统（含边界条件），候选配对落到 `u_0=0` 一行；PDE 不走仲裁，最终 unknown | 参考配对 + PDE 边界 |

> 说明：`softmax`、`var_sum_sigma`、`hw_trace`、`l2_norm` 这类用例在多轮
> 运行中都在 pass / fail 之间出现过翻转（`l2_norm` 先 fail、补上范数定界符
> 归一化后稳定 pass；`softmax` 在 w10c 版 prompt 下 pass、在 w10i 下 fail）。
> 根因是 Qwen-VL 对「下标基准不同但元素个数相同」的判定不稳定。
> 系统的**单次准确率**因此存在约 ±2 例（±4%）的抖动，上表是其中一次
> 完整运行的结果。

## 3. 逐例结果

### 3.1 baseline 子集（33 例，命中 30）

| 用例 | 批次 | 期望 | 实际 | 命中 | 耗时(s) |
|---|---|---|---|---|--:|
| image_01 | baseline | pass | pass | ✅ | 4.2 |
| image_02 | baseline | pass | pass | ✅ | 4.4 |
| image_03 | baseline | fail | fail | ✅ | 12.4 |
| image_04 | baseline | fail | fail | ✅ | 8.8 |
| image_05 | baseline | skip | non_formula | ✅ | 29.5 |
| image_06 | baseline | pass | pass | ✅ | 6.6 |
| image_07 | baseline | fail | fail | ✅ | 22.8 |
| dot_product | baseline | pass | pass | ✅ | 10.7 |
| l2_norm | baseline | pass | pass | ✅ | 1.0 |
| softmax | baseline | pass | fail | ❌ | 1.0 |
| gradient_descent | baseline | pass | pass | ✅ | 5.0 |
| cg_alpha | baseline | pass | pass | ✅ | 6.8 |
| sign_flip | baseline | fail | fail | ✅ | 16.2 |
| off_by_one_sum | baseline | fail | fail | ✅ | 28.3 |
| transpose_index | baseline | fail | fail | ✅ | 48.5 |
| lu_div_index | baseline | fail | fail | ✅ | 8.1 |
| missing_sqrt | baseline | fail | fail | ✅ | 11.0 |
| lu_upper_bound | baseline | fail | fail | ✅ | 16.1 |
| matmul_missing_k | baseline | fail | fail | ✅ | 14.5 |
| conv_shift | baseline | fail | fail | ✅ | 21.0 |
| strict_upper_bound | baseline | fail | fail | ✅ | 13.3 |
| conv_stride | baseline | fail | fail | ✅ | 45.7 |
| jacobi_scope | baseline | fail | fail | ✅ | 13.5 |
| power_iter_norm | baseline | fail | fail | ✅ | 59.6 |
| pde_residual_scope | baseline | fail | unknown | ❌ | 36.6 |
| heat_1d | baseline | fail | fail | ✅ | 120.5 |
| wave_1d | baseline | fail | fail | ✅ | 86.2 |
| poisson_2d | baseline | fail | fail | ✅ | 82.3 |
| ns_incompressible | baseline | fail | fail | ✅ | 21.3 |
| chain3 | baseline | pass | pass | ✅ | 7.4 |
| chain4 | baseline | pass | fail | ❌ | 30.3 |
| assoc_paren | baseline | pass | pass | ✅ | 57.9 |
| chain_wrong_order | baseline | fail | fail | ✅ | 19.6 |

### 3.2 stress 子集（17 例，命中 15）

| 用例 | 批次 | 期望 | 实际 | 命中 | 耗时(s) |
|---|---|---|---|---|--:|
| hw_newton | stress | pass | pass | ✅ | 5.6 |
| hw_trace | stress | pass | pass | ✅ | 1.0 |
| hw_sigmoid | stress | fail | fail | ✅ | 9.4 |
| blur_hinge | stress | fail | fail | ✅ | 106.7 |
| noise_l1 | stress | pass | pass | ✅ | 27.5 |
| blur_cosine | stress | fail | fail | ✅ | 52.3 |
| layout_piecewise | stress | pass | pass | ✅ | 8.0 |
| layout_nested_frac | stress | pass | fail | ❌ | 4.8 |
| layout_matrix_frac | stress | pass | pass | ✅ | 7.0 |
| layout_multiline_update | stress | pass | pass | ✅ | 5.1 |
| edge_short | stress | pass | pass | ✅ | 10.7 |
| edge_mlp_chain | stress | pass | pass | ✅ | 16.9 |
| edge_mixed_lang | stress | fail | fail | ✅ | 55.6 |
| var_sum_sigma | stress | pass | fail | ❌ | 1.0 |
| var_norm_squared | stress | pass | pass | ✅ | 5.1 |
| var_offbyone_variant | stress | fail | fail | ✅ | 57.0 |
| var_avg_vs_sum | stress | fail | fail | ✅ | 15.8 |
