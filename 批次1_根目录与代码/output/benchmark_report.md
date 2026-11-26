# 数学公式测试集报告

- 生成时间：2026-11-14T22:33:16+08:00
- 用例总数：3

## 逐张结果

| 图 | 分类 | 预期 | 实际 | 是否命中 | 耗时(秒) | 错误 |
|---|---|---|---|---:|---:|---|
| correct/image_02.png | correct | pass | pass | 是 | 6.682 | - |
| boundary/image_05.png | boundary | skip | unknown | 否 | 4.897 | - |
| incorrect/image_07.png | incorrect | fail | fail | 是 | 17.736 | - |

## 汇总指标

| 指标 | 数值 |
|---|---:|
| 总体准确率 | 66.67% (2/3) |
| 正类召回（应 pass） | 100.00% (1/1) |
| 负类召回（应 fail） | 100.00% (1/1) |
| 边界拒绝率 | 0.00% (0/1) |
| fail 类 Precision | 100.00% |
| fail 类 Recall | 100.00% |
| fail 类 F1 | 100.00% |

## 混淆矩阵

| 标注 \ 实际 | pass | fail | non_formula | parse_error | timeout | error | pipeline_error | incomplete | unknown |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| pass | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| fail | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| skip | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |
