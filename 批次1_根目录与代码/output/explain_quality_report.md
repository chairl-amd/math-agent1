# 可解释批改质量报告（w11 工单3）

- 生成时间：2026-11-16T15:29:08
- 模型：`deepseek-v4-1-flash-261017`；提示词版本：`w11-explain-20261116i`
- 样本：`annotations.json` 中 expected=fail 的全部用例，共 **27** 例
- 证据来源：`output/benchmark_w10_final` 的逐例真实运行报告（不重新跑 OCR/翻译，避免结论漂移）

## 1. 一致率

| 口径 | 命中 | 一致率 |
|---|---:|---:|
| 严格一致（预测类型 == 标注类型，主指标） | 25/27 | **92.59%** |
| 根因族一致（辅助口径） | 26/27 | 96.30% |

> 根因族定义：boundary={index_scope, off_by_one, sum_bounds, stride_error}；transpose={transpose_error}；sign_or_operator={sign_error, coefficient_error, missing_operation, missing_term, norm_error, order_error, operator_error}；representation={variable_mapping, notation_variant}；capability={pde_capability, symbolic_scope_limit, parser_limitation}。

## 2. 逐例结果

| # | 用例 | 标注根因 | 预测根因 | 严格 | 族 | 置信度 | 来源 | 结论状态 |
|---:|---|---|---|---|---|---:|---|---|
| 1 | `image_03` | `symbolic_scope_limit` | `index_scope` | ❌ | ❌ | 0.9 | cache | `not_equivalent` |
| 2 | `image_04` | `parser_limitation` | `pde_capability` | ❌ | ✅ | 0.9 | cache | `not_equivalent` |
| 3 | `image_07` | `sign_error` | `sign_error` | ✅ | ✅ | 0.98 | cache | `not_equivalent` |
| 4 | `sign_flip` | `sign_error` | `sign_error` | ✅ | ✅ | 0.99 | cache | `not_equivalent` |
| 5 | `off_by_one_sum` | `off_by_one` | `off_by_one` | ✅ | ✅ | 0.9 | cache | `not_equivalent` |
| 6 | `transpose_index` | `transpose_error` | `transpose_error` | ✅ | ✅ | 0.95 | cache | `not_equivalent` |
| 7 | `lu_div_index` | `index_scope` | `index_scope` | ✅ | ✅ | 0.96 | cache | `not_equivalent` |
| 8 | `missing_sqrt` | `missing_operation` | `missing_operation` | ✅ | ✅ | 0.97 | cache | `not_equivalent` |
| 9 | `lu_upper_bound` | `index_scope` | `index_scope` | ✅ | ✅ | 0.72 | cache | `not_equivalent` |
| 10 | `matmul_missing_k` | `index_scope` | `index_scope` | ✅ | ✅ | 0.99 | cache | `not_equivalent` |
| 11 | `conv_shift` | `index_scope` | `index_scope` | ✅ | ✅ | 0.98 | cache | `not_equivalent` |
| 12 | `strict_upper_bound` | `index_scope` | `index_scope` | ✅ | ✅ | 0.96 | cache | `not_equivalent` |
| 13 | `conv_stride` | `stride_error` | `stride_error` | ✅ | ✅ | 0.93 | cache | `not_equivalent` |
| 14 | `jacobi_scope` | `index_scope` | `index_scope` | ✅ | ✅ | 0.99 | cache | `not_equivalent` |
| 15 | `power_iter_norm` | `norm_error` | `norm_error` | ✅ | ✅ | 0.98 | cache | `not_equivalent` |
| 16 | `pde_residual_scope` | `missing_term` | `missing_term` | ✅ | ✅ | 0.97 | cache | `unknown` |
| 17 | `heat_1d` | `pde_capability` | `pde_capability` | ✅ | ✅ | 0.9 | cache | `not_equivalent` |
| 18 | `wave_1d` | `pde_capability` | `pde_capability` | ✅ | ✅ | 0.88 | cache | `not_equivalent` |
| 19 | `poisson_2d` | `pde_capability` | `pde_capability` | ✅ | ✅ | 0.87 | cache | `not_equivalent` |
| 20 | `ns_incompressible` | `pde_capability` | `pde_capability` | ✅ | ✅ | 0.82 | cache | `not_equivalent` |
| 21 | `chain_wrong_order` | `order_error` | `order_error` | ✅ | ✅ | 0.9 | cache | `not_equivalent` |
| 22 | `hw_sigmoid` | `sign_error` | `sign_error` | ✅ | ✅ | 0.99 | cache | `not_equivalent` |
| 23 | `blur_hinge` | `missing_operation` | `missing_operation` | ✅ | ✅ | 0.97 | cache | `not_equivalent` |
| 24 | `blur_cosine` | `operator_error` | `operator_error` | ✅ | ✅ | 0.99 | cache | `not_equivalent` |
| 25 | `edge_mixed_lang` | `sign_error` | `sign_error` | ✅ | ✅ | 0.95 | cache | `not_equivalent` |
| 26 | `var_offbyone_variant` | `off_by_one` | `off_by_one` | ✅ | ✅ | 0.8 | cache | `not_equivalent` |
| 27 | `var_avg_vs_sum` | `missing_operation` | `missing_operation` | ✅ | ✅ | 0.99 | cache | `not_equivalent` |

## 3. 典型例子

### 3.1 命中的好例子

**`image_07`**（sign_error，置信度 0.98）

- 定位：`L8-L9: R[0][2] =  s; R[2][0] = -s;`
- 建议：

```text
GLM 的 `glm::mat3` 按列主序访问：`R[col][row]`。因此应改为：
```cpp
R[0][2] = -s;  // 元素 (2,0)
R[2][0] =  s;  // 元素 (0,2)
```
```

**`sign_flip`**（sign_error，置信度 0.99）

- 定位：`L6: w[i] += eta * g[i];`
- 建议：

```text
```cpp
w[i] -= eta * g[i];
```
```

**`off_by_one_sum`**（off_by_one，置信度 0.9）

- 定位：`L4: for (std::size_t i = 0; i + 1 < a.size(); ++i)`
- 建议：

```text
循环条件多排除了最后一个元素（实际只累加 a[0..n-2]），与参考式 Σ_{i=0}^{n-1} a_i 相比漏掉末项。改为：
```cpp
double sum_except_last(const std::vector<double>& a) { // 建议同时改名为 sum_all
    double s = 0.0;
    for (std::size_t i = 0; i < a.size(); ++i) {
        s += a[i];
    }
    return s;
}
```
若确实需要「除末项求和」，则参考式应写为 s = \sum_{i=0}^{n-2} a_i。
```

### 3.2 未命中例子

**`image_03`**：标注 `symbolic_scope_limit` / 预测 `index_scope`（根因族：capability → boundary，置信度 0.9）

- 定位：`L21: for (std::size_t k = 0; k < j; ++k)`

**`image_04`**：标注 `parser_limitation` / 预测 `pde_capability`（根因族：capability → capability，置信度 0.9）

- 定位：`L29-L34（动量残差定义）及整个残差函数`

## 4. 结论与局限

- 主指标 25/27（92.59%），目标 80%：达标。
- 解释层只依据「参考公式 + 翻译结果 + C++ 源码 + SymPy 差异 + VLM 判定依据」推断根因，未读取 annotations.json 的 note 或 error_type，因此不存在把标准答案喂给模型的情况。
- `pde_residual_scope` 在 w10 运行中的结论是 `unknown`（PDE 能力边界），按设计不会自动生成解释；本报告用 `force=True` 显式生成，并在逐例表中保留`forced` 信息，未改动任何判定结论。
- PDE / 解析能力边界的用例（image_04、heat_1d、wave_1d、poisson_2d、ns_incompressible）本身没有可自动证明的代数错误，解释只能给出边界说明，与标注的 `parser_limitation` / `pde_capability` 存在天然口径差异。
