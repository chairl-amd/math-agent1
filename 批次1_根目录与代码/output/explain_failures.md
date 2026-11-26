# 解释未命中明细（w11 工单3）

- 生成时间：2026-11-16T15:29:08
- 未命中：2/27
- 说明：以下逐例列出「标注根因 vs 预测根因」及模型给出的完整解释，不做任何事后改写；是否属于能力边界由读者判断。

## `image_03`

- 标注根因：`symbolic_scope_limit`（族：capability）
- 预测根因：`index_scope`（族：boundary，置信度 0.9）
- 校验结论：`not_equivalent`；解释来源：`cache`；force=False
- 定位：`L21: for (std::size_t k = 0; k < j; ++k)`
- 标注备注：LU 局部递推式可对照，但 A=LU 整体关系未完成任意 n 的严格符号证明
- token 用量：{'prompt_tokens': 3037, 'completion_tokens': 4498, 'total_tokens': 7535}

<details><summary>完整修改建议</summary>

```text
将内层求和上界改为 `k < min(i,j)`：
```cpp
const std::size_t kmax = (i <= j ? i : j);
double sum = 0.0;
for (std::size_t k = 0; k < kmax; ++k) {
    sum += L[i][k] * U[k][j];
}
```
即 U_ij 在 i<=j 时用 k<i，L_ij 在 i>j 时用 k<j。
```

</details>

## `image_04`

- 标注根因：`parser_limitation`（族：capability）
- 预测根因：`pde_capability`（族：capability，置信度 0.9）
- 校验结论：`not_equivalent`；解释来源：`cache`；force=False
- 定位：`L29-L34（动量残差定义）及整个残差函数`
- 标注备注：Navier-Stokes PDE：规范化残差一致，但原始 LaTeX 环境词被解析器误读，未完成严格符号等价
- token 用量：{'prompt_tokens': 2918, 'completion_tokens': 3786, 'total_tokens': 6704}

<details><summary>完整修改建议</summary>

```text
代码本身将参考 PDE 移项为残差 LHS−RHS=0，符号正确。建议在比较前将参考公式改写为同形式残差，或在代码中显式注释残差定义：
```cpp
// 残差约定：r = LHS - RHS = 0
// r_mom = ∂t(ρu) + ∇·(ρu⊗u) + ∇p - μ∇²u - ρg - ∇·τ
```
然后与 r.momentum 逐项对齐比较。
```

</details>

