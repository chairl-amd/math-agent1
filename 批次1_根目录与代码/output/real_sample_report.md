# 手写公式真实图片端到端测试报告（C-5 修正版）

生成时间：2026-11-14 01:42:29 CST

## 1. 状态与结论

- 本轮为纯本地重算：SymPy 严格化简、固定随机种子数值采样、g++ 编译运行；新增付费 API 调用 0 次。
- 六张图中，`image_05` 仍按非公式处理；五张公式图的最终状态由 `symbolic_equivalent`、`structural_consistent`、`runtime_consistent` 三个独立字段共同推导。
- 严格符号等价通过 3/5；`image_03` 和 `image_04` 的结构与运行均通过，但整体严格符号证明未成立，因此不再写作 `equivalent`。
- `image_01` 已改用 `r_next-r-alpha_k*sum_j(A_ij*p_j)` 的差值式重算，符号和运行均通过；旧版外层 `equivalent` 与内层 `not_equivalent` 的矛盾已消除。
- `image_01/02` 的核心错误加号和矩阵布局均被 Vision 与 C++ 保留；这只说明链路未擅自纠错，不说明原图数学正确。

## 2. 旧脚本与硬编码定位

- 未发现项目内持久化生成脚本；旧逻辑来自 `/home/charlie/.bash_history` 中的临时 Python heredoc。
- 旧 `image_01` 使用 `compare_expressions(r_next, r_i + alpha_k*Ap_i)` 比较“更新后的状态”和“右侧表达式”，没有先做方程重排，产生错误的 `not_equivalent`。
- 随后 `image_01` 外层 `validation_status` 仍被直接写成 `equivalent`，并再次覆盖 `detail`；这属于迎合性放宽。
- `image_04`、`image_06` 的旧外层 `equivalent` 是字面量；`image_03` 把 `4/4` 结构匹配直接外推为 `equivalent`，没有严格符号依据。
- 历史硬编码匹配位置已保存到 `output/real_sample_artifacts/c5_hardcoding_audit.json`。

### 修改前外层/内层状态对照

| 图 | 外层 validation_status | 内层 result.status | 关系 |
|---|---|---|---|
| image_01 | `equivalent` | `not_equivalent` | `inconsistent` |
| image_02 | `equivalent` | `equivalent` | `consistent` |
| image_03 | `equivalent` | `None` | `missing_inner_status` |
| image_04 | `equivalent` | `None` | `missing_inner_status` |
| image_05 | `non_formula` | `None` | `missing_inner_status` |
| image_06 | `equivalent` | `None` | `missing_inner_status` |

结论：修改前只有 `image_01` 存在可直接比较的“外层与内层状态矛盾”；`image_03/04/06` 外层写 `equivalent` 但根本没有内层 `result`，属于第二个结构缺陷，不能视为一致。

## 3. 校验方法修正

- 新增可重复脚本：`tests/validate_real_samples_c5.py`。脚本从 artifacts 读取 Vision LaTeX 和翻译 C++，不读取模型密钥。
- 方程先重排为 `LHS-RHS`。只有 SymPy `simplify` 严格得到 0 才把 `symbolic_equivalent` 置为真；数值近似不会升级为严格符号等价。
- 结构字段只表示公式片段与 C++ 语句存在明确映射，不再代替符号等价。
- 运行字段通过 g++ 编译翻译片段，并用 3 至 7 组固定/确定性随机样本对照 LaTeX 公式计算值。
- 最终状态由公共函数推导：三项全真才是 `equivalent`；符号未证明但结构和运行通过则为 `not_symbolically_equivalent`。
- `image_01` 的修正重点是把矩阵向量积展开为 `sum_j A_ij p_j`，再比较差值式，而不是比较 `r_next` 与右侧表达式本身。

## 4. 六张图真实结果

| 图 | validation_status | symbolic | structural | runtime | 真实说明 |
|---|---|---|---|---|---|
| image_01 | `equivalent` | 是 | 是 | 是 | 按差值式 r_next-r-alpha_k*(A p)_i 重算：LaTeX 的矩阵向量积展开与 C++ Ap_i=sum_j A_ij p_j 严格符号等价；编译运行采样一致。 |
| image_02 | `equivalent` | 是 | 是 | 是 | 9 个矩阵元素严格符号等价，且 7 个角度的 C++ 运行结果与 LaTeX 公式一致。 |
| image_03 | `not_symbolically_equivalent` | 否 | 是 | 是 | L_ii、U_ij、L_ij 三个局部递推式严格对照通过，3 个无主元矩阵与 C++ 编译运行一致；但 A=LU 的整体关系仅做到 3x3 符号回代和运行时回代，未完成任意 n 的严格符号证明，故不标记 equivalent。 |
| image_04 | `not_symbolically_equivalent` | 否 | 是 | 是 | 动量、连续性、能量三个规范化残差与 C++ 逐项一致，6 组编译运行采样一致；但原始 PDE LaTeX 被现有解析器误读，微分算子语义依赖人工映射，未完成严格符号等价证明。 |
| image_05 | `non_formula` | N/A | N/A | N/A | Vision 返回空公式并明确走非公式跳过；翻译状态为 skipped_non_formula；Pix2Text 的非空输出被记录为 OCR 幻觉，不进入公式校验分母。 |
| image_06 | `equivalent` | 是 | 是 | 是 | c_ij=sum_k a_ik b_kj 与 C++ 三重循环严格符号一致；3 组矩阵运行结果一致。 |

## 5. 重算指标

| 指标 | 结果 | 口径 |
|---|---:|---|
| 公式识别准确率 | 5/5 = 100.0% | 任务给定人工真值；`image_05` 非公式不计入 |
| 非公式拒绝率 | 1/1 = 100.0% | Vision 空公式且翻译主动跳过 |
| 翻译成功率 | 5/5 = 100.0% | 非空 C++ 且本地编译成功 ÷ 5 张公式图 |
| 翻译成功率（总样本） | 5/6 = 83.3% | 分子不变，分母改为 6 张总图 |
| 校验通过率（symbolic 口径） | 3/5 = 60.0% | `symbolic_equivalent=True` ÷ 5 张公式图 |
| 校验通过率（总样本） | 3/6 = 50.0% | 分子不变，分母改为 6 张总图 |
| 结构一致率 | 5/5 = 100.0% | 逐式/C++ 语句映射通过的公式图 |
| 运行一致率 | 5/5 = 100.0% | 编译和数值采样对照通过的公式图 |

严格符号通过名单：`image_01`、`image_02`、`image_06`。

未通过严格符号口径：`image_03`（`A=LU` 未完成任意 n 的符号证明）、`image_04`（原始 PDE LaTeX 解析误读，算子语义依赖人工映射）。两者结构一致和运行一致均为真。

## 6. 预算（沿用旧台账，不新增调用）

| 阶段 | token 口径 | 说明 |
|---|---:|---|
| C-1 | 6114 | 已捕获 usage |
| C-2 | 13259 | Vision 精确，3 次空翻译按保守上界 |
| C-3 | 4516 | 已捕获 usage |
| 累计保守上界 | 23889 | 0.23889 元 |
| C-5 | 0 | 本轮无付费 API 调用，仅本地 SymPy 和 g++ |

预算依据：`output/real_sample_artifacts/budget_ledger_c3.json`。本轮没有新增或修改历史调用数据。

## 7. 边界案例与修复记录

- `image_01`：曾把两个不同表达式直接比较，得到 `not_equivalent`；随后脚本把外层结论硬编码成 `equivalent`，形成虚假通过。现按正确差值式重算，外内状态一致。
- `image_01/02`：Vision LaTeX 与 C++ 均保留任务指出的数学错误。防幻觉结论为“是，未擅自纠错”。
- `image_03`：局部递推式可严格对照，`A=LU` 只用 3x3 符号回代和 2x2/3x3/4x4 运行回代，因此不冒充任意维度证明。
- `image_04`：现有解析器会把 `begin` 等 LaTeX 环境词当普通变量；报告只承认规范化残差、结构和运行一致，不承认完整 PDE 符号等价或物理解正确。
- `image_05`：Vision 正确拒绝非公式；Pix2Text 的公式输出保留为 OCR 幻觉证据。

## 8. 结论与改进建议

- 修正后的链内等价：`image_01/02/06` 为严格符号、结构、运行三项通过；`image_03/04` 只达到结构+运行一致。
- 翻译成功率与校验通过率必须分开：五张公式图都成功翻译并编译，但只有三张完成严格符号等价。
- 建议后续为多行方程组和 PDE 增加专用解析器；无法解析时必须返回 `not_symbolically_equivalent` 或 `parse_error`，禁止由外层脚本覆盖。
- 建议把符号、结构、运行三字段设为强制 schema，并在 CI 中检查外层状态必须等于内层 `result.status`。

## 9. 局限

- 人工真值来自任务给定内容和既有识别结果，不是独立公开数学数据集。
- 没有外部真值纠错，也没有启用异构付费校验器；因此只能判断链路内部一致性。
- 数值采样证明有限样本一致，不证明所有输入下都一致。
- `image_03` 的整体 LU 恒等关系未完成任意维度形式化证明。
- `image_04` 的 PDE 算子仍是人工规范化后的占位语义，未证明物理模型或数值解正确。

## 10. 产物与验证

- `output/real_sample_artifacts/image_01_validation.json` 至 `image_06_validation.json`：修正后的六份逐图校验。
- `output/real_sample_artifacts/c5_validation_evidence.json`：总体证据和状态源哈希。
- `output/real_sample_artifacts/metrics_c5.json`：由六份校验 JSON 动态计算的指标。
- `output/real_sample_artifacts/c5_hardcoding_audit.json`：旧 heredoc 位置与修改前状态审计。
- `tests/validate_real_samples_c5.py`：可重复校验脚本，可再次执行并生成相同语义结论。
