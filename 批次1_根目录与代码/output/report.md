# math-agent 数学公式校验可视化报告

- 生成时间：2026-11-14T22:33:16+08:00
- 数据来源：`D:\math-agent\output\benchmark_result.json`
- 标注文件：`D:\math-agent\tests\benchmark\annotations.json`
- 详细报告：`D:\math-agent\output\_benchmark_tmp\20261114_223247_444431`
- 总准确率：66.67%
- 命中数 / 总数：2 / 3

## 按标注分类统计

| 分类 | 用例数 | 命中数 | 准确率 |
|---|---:|---:|---:|
| correct | 1 | 1 | 100.00% |
| incorrect | 1 | 1 | 100.00% |
| boundary | 1 | 0 | 0.00% |
| structural | 0 | 0 | N/A |

## 逐例结果

| 图 | 预期 | 实际 | 结论 | 耗时 | 错误 |
|---|---|---|---|---:|---|
| correct/image_02.png | pass | pass | 通过（绿） | 6.682 秒 | 无 |
| boundary/image_05.png | skip | unknown | 需关注（黄） | 4.897 秒 | 无 |
| incorrect/image_07.png | fail | fail | 不通过（红） | 17.736 秒 | 无 |
