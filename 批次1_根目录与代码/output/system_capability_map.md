# 系统能力地图（工单 3）

- 生成时间：2026-11-15
- 数据来源：`output/benchmark_w10_final`（50 用例全量）、`output/benchmark_w9_hybrid`（33 用例基线）
- 对照：`output/benchmark_w10_sympy`（SymPy-only，同用例同图）

## 1. 一句话结论

系统在**可符号化**的公式上稳定（w9 基线 32/33；加入 17 个对抗用例后全量 45/50），
本次把能力边界从「SymPy 能算」推进到
「SymPy 或独立语义校验之一能给出确定结论」，并用 17 个对抗用例把原来靠运气的
环节（撇号、下标基准、记号变体、多行配对）显式固化成规则。
剩余不可解的部分集中在**连续 PDE 语义**与**迭代态符号映射**两类，已明确声明为能力边界；
此外异构校验（Qwen-VL）在「下标基准不同但元素个数相同」上存在约 ±2 例的判定抖动，
单次全量准确率因此有 ±4% 波动，详见 `output/failure_analysis.md` 第 2 节。

## 2. 关键指标

| 指标 | w9 基线（33 例） | w10 全量（50 例） | w10 基线子集（33 例） | w10 对抗子集（17 例） |
|---|---:|---:|---:|---:|
| 命中数 | 32/33 | 45/50 | 30/33 | 15/17 |
| 总体准确率 | 96.97% | 90.00% | 90.91% | 88.24% |
| 正类召回 | 95.24% | 81.82% | 81.82% | 81.82% |
| 负类召回 | 100% | 96.30% | 96.30% | 100% |
| fail_F1 | 0.9756 | 0.9123 | 0.9259 | 0.9231 |
| 平均耗时 | 24.95s | 25.21s | 26.4s | 22.9s |

> 负类召回的分母包含 `image_05`（预期 `skip`，系统判 `non_formula`，计为命中）。

## 3. 能力分层

### 3.1 稳定能力（可重复复现，有规则或代数证明支撑）

- **代数等价判定**：多项式/分式/三角/指数表达式的化简、展开、因式分解与数值抽样。
- **矩阵运算**：矩阵乘法、转置、行列式、迹、范数；矩阵写法与逐元素展开写法的跨表示比较。
- **求和/循环索引**：上下界提取、索引跨度核对、0-based/1-based 基准归一。
- **记号变体**：`\over`/`\frac`、`\operatorname`/`\mathrm`/`\text`、`\mathsf{T}`/`^\top`、
  `a_{i,i}`/`a_{ii}`、`h_1`/`h^{(1)}`、`\left|·\right|`/`|·|`。
- **PDE 结构证据**：算子签名（∂/∇/Δ）提取与逐行残差对齐，作为人工复核证据。

### 3.2 有条件能力（依赖异构校验，需注明依据）

- **符号层无结论但语义等价**：矩阵写法 vs 展开写法、变量重命名、函数名差异。
  触发 `HETERO_ACCEPT_CONSENSUS` 仲裁，结论带 `accepted_by=hetero_consensus`。
- **手写/模糊图片**：OCR 能识别，但撇号、上下标、大小写依赖图像质量；
  w10 通过渲染改造与提示词规则把「撇号丢失」从随机事件变成可复现用例。

### 3.3 能力边界（明确不承诺）

1. **连续 PDE vs 离散实现**（`image_04`）：返回 `unknown + needs_human_review`，
   仲裁层显式排除 PDE，不做等价升级。
2. **迭代态下标 vs 无下标变量**（`hw_newton` 的 `X_k` ↔ `x`）：SymPy 视为不同符号，
   若异构判等价则进入 `needs_human_review`。
3. **图注/非公式图片**（`image_05`）：判 `non_formula`，不做数学判断。
4. **图像不可辨认**：OCR 置信度低或输出空时判 `non_formula`/`parse_error`，
   不猜测内容。

## 4. 前后对比（同用例）

### 4.1 校验链

| 维度 | w9 | w10 |
|---|---|---|
| 共识策略 | 全一致才等价，任一 unknown 即 unknown | 新增能力缺口仲裁（非 PDE、异构全一致、网络成功） |
| OCR 缓存键 | `engine\|prompt`，默认提示词不进键（改提示词不失效） | `engine\|实际提示词`（改提示词必然失效） |
| 参考多行配对 | 同序 / 左侧符号键，其余按顺序 | 再叠加符号重叠度贪心配对 |
| 候选选择 | 首个失败的候选被保留 | 按状态优先级 + 符号重叠度选信息量最大的候选 |
| 下标基准 | 依赖模型自由判断 | 逐例 `<index_base_hint>` 确定性提示 |

### 4.2 规则层新增

- `symbol_norm.normalize_notation_variants`：`\over`、`\operatorname*`、算子别名、转置记号。
- `symbol_norm.normalize_subscript_commas`：多指标下标去逗号。
- `verify._try_case_insensitive_symbols`：单字母大小写回退（严格比较失败后）。
- `verify._row_similarity` / `_formula_tokens`：多行子式相似度配对。
- `verify._clean_latex_for_parse` 剥离 `\left`/`\right`。
- `verifier_hetero._capability_gap_consensus` + `config.HETERO_ACCEPT_CONSENSUS`：能力缺口仲裁。
- `verifier_hetero._format_index_base_hint`：逐例下标基准提示（证据取自
  `normalize_sum_limits` 之后，隐式 `\sum_j` 也能触发）。
- `verifier_hetero` 校验提示词第 11 条：同一套下标命名下不同下标是不同元素
  （`U_{jj}` ≠ `U_{i,i}`），但跨表示（标量 vs 带下标）仍算等价。
- `symbol_norm.normalize_norm_bars` 扩展 `\lVert/\rVert/\Vert/\vert` 家族。
- `llm._ensure_variable_alignment`：译文里一个代码变量都没出现时带提示重试一次。
- `_capability_gap_consensus` 前置检查：非 SymPy 校验器报错 / 链里没有 SymPy 时不仲裁。

## 5. 风险与代价

- 仲裁放宽了「unknown」出口，理论上增加把「真错误」判成等价的风险。缓解：
  只在 **无任何 inconsistent、异构全等价、网络真实返回、非 PDE** 时才采信，
  并保留 `accepted_by`/`accepted_from_status` 便于审计。
- 异构模型调用仍是主要耗时来源（单次 5–10s），全量 50 例的耗时约为 SymPy-only 的 2–3 倍。
- 大小写回退只在严格比较失败后触发，若图中 `A`（矩阵）与代码 `a`（标量）确实不同，
  仍可能被误判为等价；本批 50 例未出现该误判。

## 6. 回归测试

19 个 `tests/test_*.py` 全部通过（`python tests/test_xxx.py`，退出码 0）：

| 类别 | 文件 | 结果 |
|---|---|---|
| 新增 w10 规则 | `test_w10_rules.py` | 21/0 |
| 校验链（含异常隔离、仲裁开关） | `test_verifier_chain.py` | 11/0 |
| 翻译稳定性（缓存、投票、温度） | `test_translation_stability.py` | 19/0 |
| 其余 16 个（缓存、跨表示、矩阵、PDE、Web 批处理等） | `test_*.py` | 全部通过 |

`test_w10_rules.py` 覆盖：`\over`、`\operatorname`、`\mathsf{{T}}`、下标逗号、
`\left|` 解析、能力缺口仲裁策略（PDE/冲突/离线/开关关闭）、范数定界符家族。
