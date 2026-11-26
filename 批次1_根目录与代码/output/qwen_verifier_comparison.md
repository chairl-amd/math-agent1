# SymPy-only 与 Hybrid 校验对比

- 生成时间：2026-11-15T18:29:36+08:00
- DEEPSEEK_API_KEY 已配置：是
- 子进程超时：240s
- Hybrid 校验链：sympy + deepseek@deepseek-v4-1-flash-261017 + qwen-vl@qwen3-vl-plus
- 实际用例数：6
- benchmark incorrect：5
- structural：1

## 逐例明细

| 用例 | 模式 | 判定 | 判定质量 | 是否捕获 | 耗时(s) | 关键状态 |
|---|---|---|---|---|---:|---|
| image_07 | sympy-only | fail | 明确判错 | 是 | 12.797 | latex_vs_cpp:not_equivalent |
| image_07 | hybrid | fail | 明确判错 | 是 | 53.359 | latex_vs_cpp:not_equivalent |
| sign_flip | sympy-only | fail | 明确判错 | 是 | 4.329 | latex_vs_cpp:not_equivalent |
| sign_flip | hybrid | fail | 明确判错 | 是 | 76.562 | latex_vs_cpp:not_equivalent |
| off_by_one_sum | sympy-only | fail | 明确判错 | 是 | 6.016 | latex_vs_cpp:not_equivalent |
| off_by_one_sum | hybrid | fail | 明确判错 | 是 | 31.078 | latex_vs_cpp:not_equivalent |
| missing_sqrt | sympy-only | fail | 无法判定 | 是 | 8.594 | latex_vs_cpp:unknown |
| missing_sqrt | hybrid | fail | 明确判错 | 是 | 11.546 | latex_vs_cpp:not_equivalent |
| matmul_missing_k | sympy-only | fail | 无法判定 | 是 | 6.063 | latex_vs_cpp:unknown |
| matmul_missing_k | hybrid | fail | 明确判错 | 是 | 10.828 | latex_vs_cpp:not_equivalent |
| matmul_missing_k | sympy-only | fail | 无法判定 | 是 | 0.938 | latex_vs_cpp:unknown |
| matmul_missing_k | hybrid | fail | 明确判错 | 是 | 8.984 | latex_vs_cpp:not_equivalent |

## 汇总

| 模式 | 捕获数 | 明确判错 | 明确判错率 | 无法判定 | 用例数 | 平均耗时(s) | 网络调用次数 |
|---|---:|---:|---:|---:|---:|---:|---:|
| sympy-only | 6 | 3 | 50.00% | 3 | 6 | 6.456 | 0 |
| hybrid | 6 | 6 | 100.00% | 0 | 6 | 32.059 | 24 |

Hybrid 明确判错率相对 SymPy-only 提升：**50.00 个百分点**。

## 结论

- 仅 hybrid 给出明确判错：missing_sqrt, matmul_missing_k。这是由本次报告中的实际状态差异直接得到。
- 两者都明确判错：image_07, sign_flip, off_by_one_sum。
- 两者都只能判 unknown/error：无。
- 两者都漏：无。
- 明确判错率提升：50.00 个百分点；hybrid 实际网络调用 24 次。
