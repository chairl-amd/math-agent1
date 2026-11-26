# SymPy-only 与 Hybrid 校验对比

- 生成时间：2026-11-15T19:41:35
- SymPy-only 数据：`output\benchmark_w9_sympy\benchmark_result.json`
- Hybrid 数据：`output\benchmark_w9_hybrid\benchmark_result.json`
- 对比用例数：33

## 汇总

| 模式 | 明确结论数 | 明确结论率 | 命中数 | 准确率 | unknown 数 | 平均耗时(s) |
|---|---:|---:|---:|---:|---:|---:|
| sympy-only | 25 | 75.76% | 25 | 75.76% | 8 | 11.15 |
| hybrid | 32 | 96.97% | 32 | 96.97% | 1 | 24.95 |

## 逐例明细

| 用例 | 预期 | SymPy-only | Hybrid | 耗时差(s) |
|---|---|---|---|---:|
| image_01 | pass | pass | pass | +20.5 |
| image_02 | pass | pass | pass | -19.8 |
| image_03 | fail | fail | fail | -31.2 |
| image_04 | fail | unknown | unknown | -7.2 |
| image_05 | skip | non_formula | non_formula | -2.7 |
| image_06 | pass | pass | pass | -19.7 |
| image_07 | fail | fail | fail | +34.9 |
| dot_product | pass | pass | pass | +0.5 |
| l2_norm | pass | pass | pass | -17.7 |
| softmax | pass | pass | pass | +18.0 |
| gradient_descent | pass | pass | pass | +0.5 |
| cg_alpha | pass | pass | pass | +3.8 |
| sign_flip | fail | fail | fail | +42.9 |
| off_by_one_sum | fail | fail | fail | -7.5 |
| transpose_index | fail | fail | fail | +8.2 |
| lu_div_index | fail | unknown | fail | +2.5 |
| missing_sqrt | fail | unknown | fail | +1.3 |
| lu_upper_bound | fail | fail | fail | +5.2 |
| matmul_missing_k | fail | unknown | fail | -0.1 |
| conv_shift | fail | fail | fail | +10.1 |
| strict_upper_bound | fail | unknown | fail | +0.6 |
| conv_stride | fail | fail | fail | +55.4 |
| jacobi_scope | fail | fail | fail | +7.9 |
| power_iter_norm | fail | fail | fail | +88.4 |
| pde_residual_scope | fail | unknown | fail | +38.9 |
| heat_1d | fail | fail | fail | +47.4 |
| wave_1d | fail | fail | fail | +123.1 |
| poisson_2d | fail | unknown | fail | +28.0 |
| ns_incompressible | fail | unknown | fail | -4.8 |
| chain3 | pass | pass | pass | +4.0 |
| chain4 | pass | pass | pass | -0.0 |
| assoc_paren | pass | pass | pass | +18.6 |
| chain_wrong_order | fail | fail | fail | +5.4 |

## 结论

- Hybrid 把 7 个用例从「无法判定」推进到「明确结论」：lu_div_index、missing_sqrt、matmul_missing_k、strict_upper_bound、pde_residual_scope、poisson_2d、ns_incompressible
- 反向退化（Hybrid 变模糊）的用例：无
- 两者都只能给出 unknown/incomplete 的用例：image_04
- 明确结论率：75.76% → 96.97%；准确率：75.76% → 96.97%
- 平均耗时：11.15s → 24.95s（Hybrid 增加异构模型调用）

> 说明：Hybrid 的收益集中在「SymPy 解析失败或数值抽样不足以证明」的用例；代价是每个用例多一次异构模型往返。

---
## 附：Qwen-VL 接入后的对比（工单3）

- 报告：[qwen_verifier_comparison.md](qwen_verifier_comparison.md)（由
  `compare_verifiers.py --only ...` 生成，不覆盖本文件）
- 校验链：`sympy + deepseek@deepseek-v4-1-flash-261017 + qwen-vl@qwen3-vl-plus`
- 子集：`image_07`、`sign_flip`、`missing_sqrt`、`matmul_missing_k`、
  `off_by_one_sum`
- 结果：SymPy-only 明确判错 3/6（50%），加入 Qwen-VL 后 6/6（100%），
  平均耗时 6.46s → 32.06s，hybrid 共发起 24 次网络调用。
- 在线冒烟（`qwen3-vl-plus` 直连）：等价例判 `equivalent`、漏项例与
  1-based/0-based 混用例判 `not_equivalent`，3/3 正确，单次 5–7s、
  约 1100 token。
- 说明：本机 `QWEN_VERIFIER_MODEL` 曾残留旧值 `qwen-max`（纯文本模型），
  跑对比前已显式设为 `qwen3-vl-plus`；`config.py` 的默认值也改为
  `qwen3-vl-plus`，并支持用 `DASHSCOPE_API_KEY` 代替 `QWEN_API_KEY`。

---
## 附二：w10 对抗用例上的 SymPy-only vs Hybrid

工单2 新增的 17 个对抗用例（`batch=stress`）单独跑两种模式的结果：

- SymPy-only 数据：`output/benchmark_w10_sympy_stress/benchmark_result.json`
- Hybrid 数据：`output/benchmark_w10_final/benchmark_result.json` 的
  `by_batch.stress`（同一份代码、同一批图片、同一运行配置）

| 模式 | 命中 | 准确率 | 正类召回 | 负类召回 | 平均耗时(s) |
|---|---:|---:|---:|---:|---:|
| sympy-only | 5/17 | 29.41% | 27.27% | 33.33% | 2.65 |
| hybrid（w10 修复后） | 15/17 | 88.24% | 81.82% | 100% | 23.5 |

逐例对比（SymPy-only → Hybrid，Hybrid 列取最终全量运行）：

| 用例 | 预期 | SymPy-only | Hybrid | 差异来源 |
|---|---|---|---|---|
| hw_newton | pass | unknown | pass | OCR 撇号渲染 + 大小写回退 + 翻译变量映射 |
| hw_trace | pass | pass | pass | 下标逗号/算子包装归一化 |
| hw_sigmoid | fail | unknown | fail | 异构校验补足符号层缺口 |
| blur_hinge | fail | unknown | fail | 同上 |
| noise_l1 | pass | pass | pass | `\left|·\right|` 解析修复 |
| blur_cosine | fail | fail | fail | 多行候选重排后由 Qwen 确认分母差异 |
| layout_piecewise | pass | unknown | pass | 能力缺口仲裁 |
| layout_nested_frac | pass | unknown | fail | OCR 把内层 `x` 读成 `X`（未修好） |
| layout_matrix_frac | pass | unknown | pass | 能力缺口仲裁 |
| layout_multiline_update | pass | pass | pass | — |
| edge_short | pass | unknown | pass | 能力缺口仲裁 |
| edge_mlp_chain | pass | unknown | pass | 能力缺口仲裁（`h_1` vs `h^{(1)}`） |
| edge_mixed_lang | fail | unknown | fail | 异构校验 |
| var_sum_sigma | pass | unknown | fail | 下标基准判定抖动（未稳定） |
| var_norm_squared | pass | unknown | pass | 翻译与 `\mathsf{T}` 归一化 |
| var_offbyone_variant | fail | fail | fail | — |
| var_avg_vs_sum | fail | unknown | fail | 异构校验 |

结论：

- 新增规则把「符号层可判定」的用例从 5 个提高到 9 个（`hw_trace`、`noise_l1`、
  `layout_multiline_update`、`var_offbyone_variant` 等由纯符号规则直接判出）；
- 剩下的 6 个由异构校验补齐，其中 4 个经能力缺口仲裁（`accepted_by=hetero_consensus`）；
- 反向退化：无。`var_offbyone_variant`（跨度不同）、`var_avg_vs_sum`（均值 vs 求和）
  与 `blur_cosine`（分母 + vs ×）仍稳定判错，说明仲裁没有把真错误放过去；
- 仍有 2 例（`layout_nested_frac`、`var_sum_sigma`）未稳定命中，原因见
  `output/failure_analysis.md` 第 2 节。

## 附三：w10 改动对 33 例基线的影响

- 基线子集：32/33 → 30/33（本次最终运行）。
  未命中的 3 例：`softmax`（下标基准判定抖动，pass→fail）、
  `chain4`（翻译层把 4 矩阵链简化成 `z=xy`，pass→fail）、
  `pde_residual_scope`（多行候选配对落到边界条件行，fail→unknown）。
- 中途出现过并已修复的回归（说明「提示词更规范」会破坏「符号可对齐」）：
  1. `gradient_descent`：翻译提示词要求「用标准记号」导致把 `w` 改写成 `x`，
     SymPy 因此比对 η·g 与 x 判错 → 去掉该规则，改为对译文做标识符对齐检查；
  2. `chain3`/`layout_multiline_update`：要求「展开中间变量」把两行公式合并成一行，
     配对随之错位 → 撤销该规则；
  3. `lu_div_index`：新增的下标基准提示被模型泛化成「下标命名差异不算差异」，
     把 `U_jj` 与 `U_i,i` 判成等价（假通过）→ 增加反例保护规则（不同元素下标不等价，
     但跨表示的标量/带下标写法仍算等价）；
  4. `l2_norm`：翻译输出 `\lVert x\rVert_2`，符号层未识别该定界符，解析成
     `lVert*(rVert_2*x)` → 范数归一化补充 `\lVert/\rVert/\Vert/\vert` 家族。
- 结论：**提示词里凡是"更规范/更简洁"的要求，都可能让译文偏离图片写法**；
  翻译层的首要目标是「与图片同名可对齐」，变量名必须沿用代码。
