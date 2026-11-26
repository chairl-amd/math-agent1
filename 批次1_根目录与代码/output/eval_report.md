# math-agent 评估报告

- 时间：2026-11-14T01:03:45
- C++ 样例编译成功：4/4
- 符号校验通过：10/10
- 总耗时：5.58s

## C++ 样例

| 样例 | 编译 | 退出码 | 输出 |
|---|---|---|---|
| matrix_mul | ✅ | 0 | `max_err = 5.960e-08` |
| rotate_y_bug | ✅ | 0 | `got=(0.8776, 0.0000, 0.4794) want=(0.8776, 0.0000, -0.4794) err=0.9589` |
| lu_decomp | ✅ | 0 | `x = [0.99999863, 1.00001264, 0.99997753, 1.00001073]
max|x - x_true| = 2.247e-05` |
| conjugate_gradient | ✅ | 0 | `iterations = 5, residual = 4.913e-09
x = [0.496154, 0.984615, 1.442308, 1.784616` |

## 符号校验

| 用例 | 结果 | 状态 | 说明 |
|---|---|---|---|
| 动能方程 | ✅ | equivalent | simplify(a-b)=0 |
| 三角恒等式 | ✅ | equivalent | simplify(a-b)=0 |
| 分式 | ✅ | equivalent | simplify(a-b)=0 |
| 勾股定理 | ✅ | equivalent | simplify(a-b)=0 |
| 对数乘积 | ✅ | equivalent | simplify(a-b)=0 |
| 平方差 | ✅ | equivalent | simplify(a-b)=0 |
| 余弦零值 | ✅ | equivalent | simplify(a-b)=0 |
| 根式 | ✅ | equivalent | simplify(a-b)=0 |
| 和式展开 | ✅ | equivalent | simplify(a-b)=0 |
| 反例(不等) | ✅ | not_equivalent | 数值不一致：最差点相对误差 1.807e+00 |