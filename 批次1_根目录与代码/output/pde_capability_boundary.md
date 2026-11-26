# PDE 能力边界记录（工单3）

- 生成时间：2026-11-15（工单3 更新：逐行残差结构分析）
- 相关代码：`agent/pde_analysis.py`、`agent/main.py`（PDE 兜底标记）
- 相关测试：`tests/test_latex_normalization.py`（PDE 分析 7 项断言）
- 数据来源：`output/benchmark_final/benchmark_result.json`（hybrid，33 用例）

## 1. 问题

`image_04` 是 Navier-Stokes 方程截图，代码是显式离散的动量/连续/能量残差。
此前校验层只能给出 `unknown`，报告无法说明"是能力不足"还是"公式确实不一致"。

根本原因是语义层级不同：

- 图片侧是**连续 PDE**：`\partial_t(\rho u) + \nabla\cdot(\rho u\otimes u) = -\nabla p + \mu\Delta u + \rho g + \nabla\cdot\tau`
- 代码侧是**离散残差**：`r_mom = ∂_t(ρu) + ∇·(ρu⊗u) + ∇p - μΔu - ρg - ∇·τ`

两者之间不存在 SymPy 能自动证明的代数恒等关系：离散残差是连续方程的
有限差分近似，只在网格极限下收敛，不是符号等价。

## 2. 兜底方法

`agent/pde_analysis.py` 做纯文本级结构分析（不依赖 SymPy，解析失败也能用）：

1. `is_pde()`：识别 `\partial`、`\nabla`、`\Delta`、`\frac{\partial}{\partial x}`；
2. `residual_signature()`：把等式两侧按顶层 `+/-` 拆项，提取每项的微分算子
   序列（`dt`、`dxx`、`div`、`lap`、`grad`），张量散度
   `\partial_{x_i}\tau_{ij}` 归一化为 `div`；
3. `analyze()`：比较两侧算子多重集，输出 `structurally_aligned`；
4. 无论是否对齐，一律 `needs_human_review = true`，并把结论写入
   `steps.pde_analysis` 与 `steps.checks[].meta`。

## 3. 实际结果（33 用例中的 PDE 相关项）

| 用例 | 结构对齐 | 校验状态 | 解读 |
|---|---|---|---|
| `image_04`（N-S 三方程） | ✅ True | `unknown` | 算子集合一致（dt/div/grad/lap），逐行残差 3/3 对齐；连续↔离散无代数等价，**需人工确认** |
| `pde_residual_scope` | ✅ True | `not_equivalent` | 结构对齐但代码漏项，正确判为不等价 |
| `heat_1d` | — None | `not_equivalent` | 参考侧是离散代码、无微分记号，无法结构对比 |
| `wave_1d` | — None | `not_equivalent` | 同上 |
| `poisson_2d` | — None | `not_equivalent` | 同上 |
| `ns_incompressible` | ❌ False | `not_equivalent` | 两侧算子集合不同（左侧多出 `grad/grad/lap`），判不等价 |

`image_04` 的算子证据：`['dt', 'div', 'grad', 'lap', 'dx', 'dt', 'lap', 'div', 'grad']`，
经归一化后与参考侧 `{'dt': 1, 'div': 2, 'grad': 1, 'lap': 1}` 一致。

### 3.1 image_04 逐行细化结果（工单3新增）

`agent/pde_analysis.py` 新增 `compare_pde_rows()`：把多行 `aligned` 拆成逐行
等式，再统一化为 `残差 = 左端 - 右端` 的**带符号算子多重集**，与代码翻译
出的残差式逐行比较。`image_04` 结果：

| 行 | 方程 | 左侧残差算子 | 右侧残差算子 | 对齐 |
|---|---|---|---|---|
| 1 | 动量方程 | `dt-, div-, grad-, lap+, div+` | 同左 | ✅ |
| 2 | 连续性方程 | `div-` | `div-` | ✅ |
| 3 | 能量方程 | `dt-, grad-, lap+` | 同左 | ✅ |

（表中为规范化后的表示；`R` 与 `-R` 视为同一条残差方程，故整体符号可
同时取反，不影响对齐结论。）

即：三行都能抽出可比较的残差结构，且带符号算子完全一致。这说明代码的
`r_momentum / r_continuity / r_energy` 与图中三条方程在**残差结构**上对应，
不是漏项或符号错误。

但仍不能升级为 `equivalent`：该比较只覆盖微分算子的存在性与符号，不覆盖
系数（`ρ`、`μ`、`c_p`、`k`）、张量缩并顺序、边界条件与离散格式；而连续 PDE
与离散残差之间本身也不存在 SymPy 可证明的代数恒等关系。因此最终结论保持
`unknown + needs_human_review=true`，新增的逐行证据写入
`steps.pde_analysis.row_alignment`，供人工直接定位到具体方程。

## 4. 能力边界（明确声明）

1. **不判定连续 PDE 与离散实现的数学等价性。** 系统不做网格收敛分析、
   量纲分析之外的推导，也不声称"PDE 一致"。结论只能是 `unknown` +
   `needs_human_review=true`。逐行残差结构一致（如 `image_04` 的 3/3）
   只作为人工复核证据，不改变该边界。
2. **结构对齐 ≠ 等价。** `structurally_aligned=True` 只说明两侧微分算子
   集合相同，可能仍存在系数、边界条件或离散格式差异（见
   `pde_residual_scope`：结构对齐但漏项，仍需判错）。
3. **离散代码无微分记号时无法结构对比**，此时 `structurally_aligned=None`，
   `detail` 会说明"只能人工确认"。
4. **PDE 标记不改变 `ok` 与退出码**：仍按原 `status` 汇总，避免把
   "需人工确认"误当作"已证明错误"。

## 5. 人工复核建议

对 `needs_human_review=true` 的 PDE 结果，建议按以下顺序人工核对：

1. 对照 `steps.pde_analysis.lhs_operators` / `rhs_operators` 确认算子是否
   齐全（时间导数、对流项、压力梯度、粘性扩散、源项）；
2. 核对离散格式的系数（`Δt`、`Δx`、`ν` 与 `μ/ρ` 的对应关系）；
3. 核对边界条件与网格索引范围（用 `meta.index_check`）；
4. 只有当离散格式与该连续方程在数学上可对应时，才认为实现正确。
