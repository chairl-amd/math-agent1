# math-agent 端到端压测报告

- 生成时间：2026-11-14T22:19:41+08:00
- DEEPSEEK_API_KEY 已配置：是
- 子进程超时：180s
- 正常用例数：20
- 异常用例数：4

## 汇总指标

| 指标 | 数值 |
|---|---:|
| 正常用例流程成功率 | 100.00% (20/20) |
| 预期通过用例通过率 | 33.33% (1/3) |
| 预期失败用例拦截率 | 100.00% (6/6) |
| 平均耗时 | 9.507s |
| 中位耗时 | 2.992s |
| P95 耗时 | 19.767s |
| 缓存命中率 | 54.84% (17/31) |
| 异常用例优雅失败率 | 100.00% (4/4) |

## 逐例结果

| ID | 类别 | 返回码 | 合法 JSON | 耗时(s) | report.ok | 缓存 H/M/W | 错误摘要 |
|---|---|---:|---|---:|---|---:|---|
| image_01 | benchmark | 1 | 是 | 5.394 | False | 1/1/1 | - |
| image_02 | benchmark | 0 | 是 | 6.615 | True | 1/1/1 | - |
| image_03 | benchmark | 1 | 是 | 91.028 | False | 1/1/1 | - |
| image_04 | benchmark | 1 | 是 | 10.597 | False | 1/1/1 | - |
| image_05 | benchmark | 1 | 是 | 19.767 | False | 0/2/2 | - |
| image_06 | benchmark | 1 | 是 | 8.760 | False | 1/1/1 | - |
| image_07 | benchmark | 1 | 是 | 10.121 | False | 1/1/1 | - |
| structural_lu_upper_bound | structural | 1 | 是 | 8.448 | False | 0/1/1 | - |
| structural_matmul_missing_k | structural | 1 | 是 | 9.512 | False | 0/1/1 | - |
| structural_conv_shift | structural | 1 | 是 | 14.398 | False | 0/1/1 | - |
| formula_01 | formula | 1 | 是 | 0.556 | False | 1/0/0 | - |
| formula_02 | formula | 1 | 是 | 0.549 | False | 1/0/0 | - |
| formula_03 | formula | 1 | 是 | 0.533 | False | 1/0/0 | - |
| formula_04 | formula | 1 | 是 | 0.537 | False | 1/0/0 | - |
| formula_05 | formula | 1 | 是 | 0.538 | False | 1/0/0 | - |
| formula_06 | formula | 1 | 是 | 0.540 | False | 1/0/0 | - |
| formula_07 | formula | 1 | 是 | 0.545 | False | 1/0/0 | - |
| formula_08 | formula | 1 | 是 | 0.567 | False | 1/0/0 | - |
| formula_09 | formula | 1 | 是 | 0.550 | False | 1/0/0 | - |
| formula_10 | formula | 1 | 是 | 0.589 | False | 1/0/0 | - |
| abnormal_empty_png | abnormal | 1 | 是 | 1.407 | False | 0/1/0 | 没有获得可用 LaTeX（图片 OCR 失败且未直接提供 --latex） |
| abnormal_not_image | abnormal | 1 | 是 | 1.398 | False | 0/1/0 | 没有获得可用 LaTeX（图片 OCR 失败且未直接提供 --latex） |
| abnormal_missing_image | abnormal | 2 | 否 | 0.549 | - | 0/0/0 | [错误] 图片路径不存在或不是文件：D:\\math-agent\\.tmp\\missing_image.png |
| abnormal_empty_cpp | abnormal | 1 | 是 | 6.615 | False | 1/1/1 | - |

## 失败用例

- `image_01`：returncode=1，无错误摘要
- `image_06`：returncode=1，无错误摘要

## 薄弱点与建议

- 预期通过但 report.ok 未通过的用例：image_01，image_06。
