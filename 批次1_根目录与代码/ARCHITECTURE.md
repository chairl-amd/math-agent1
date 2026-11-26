# ARCHITECTURE — math-agent 三层架构

## 1. 总览

```
                    ┌─────────────────────────────┐
   输入层            │  图片 (.png/.jpg)            │
                    │  LaTeX 文本 / C++ 源码       │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
   OCR 层            │  DeepSeek Vision (主力)      │
   (ocr_vision.py)  │      │ 失败                 │
                    │      ▼                       │
                    │  Pix2Text (本地兜底)          │
                    │  (ocr_pix.py, 预处理+重试)     │
                    └──────────────┬──────────────┘
                                   │ LaTeX
                    ┌──────────────▼──────────────┐
   翻译层            │  DeepSeek Text (llm.py)      │
                    │  C++ -> 数学公式 / LaTeX 修复  │
                    └──────────────┬──────────────┘
                                   │ 两侧表达式
                    ┌──────────────▼──────────────┐
   校验层            │       VerifierBase           │
                    │  ┌──────────┐  ┌───────────┐ │
                    │  │ SymPy    │  │ GLM/Qwen  │ │
                    │  │ 符号校验  │  │ 真实 VLM  │ │
                    │  └────┬─────┘  └─────┬─────┘ │
                    │       └──── 串行/并行 ┘       │
                    │  汇总：状态 + 四类审查标签    │
                    └──────────────┬──────────────┘
                                   │ CheckResult
                    ┌──────────────▼──────────────┐
   输出层            │ JSON + Markdown 报告          │
                    │ output/report_<ts>.{json,md} │
                    └─────────────────────────────┘
```

## 2. 模块职责

| 模块 | 职责 | 关键设计 |
|---|---|---|
| `config.py` | 模型名、API Key、路径常量 | Key 只从 env 读取；模型集中配置，便于切换 |
| `ocr_pix.py` | Pix2Text 封装、图片预处理、降级 | 懒加载模型；单图失败不抛异常，返回 `OcrResult(ok=False)` |
| `ocr_vision.py` | DeepSeek 视觉 API（base64） | 图片压缩控制 token；`clean_latex` 清理围栏/`$$` |
| `llm.py` | C++ ↔ 数学双向翻译、LaTeX 修复 | 统一 `LLMResult`；用量写入 `meta.tokens`，便于预算监控 |
| `verify.py` | 统一校验接口 + SymPy 等价性校验 + 差异定位 | `VerifierBase` 定义契约；`SymPyVerifier` 包装原逻辑，原函数继续兼容 |
| `verifier_hetero.py` | 真实 VLM 调用 + 串并行链 + 四类结论 | OpenAI 兼容协议、配置驱动、无 Key 自动回退；冲突标为 `needs_human_review` |
| `explain.py` | 错例的结构化批改说明 | 只在确认错误后运行；复用 `llm.py` 客户端与 `verifier_hetero.py` 的 JSON 强输出；独立缓存，不改判定 |
| `main.py` | 编排 + CLI | `Pipeline.ocr()` 内建视觉→本地的 fallback 链 |

## 3. 数据结构

```python
OcrResult   = {latex, ok, engine, error, meta}
LLMResult   = {text, ok, error, meta}
CheckResult = {ok, status, lhs, rhs, detail, diff_points[], meta}
```

`status` 取值：`equivalent` / `numeric_equivalent` / `not_equivalent` /
`parse_error` / `shape_mismatch` / `unknown` / `error` /
`needs_human_review` / `translation_failed`。`needs_human_review` 只由多校验器
汇总产生。汇总结果的 `meta.verdict_label` 取值为 `HIGH_CONFIDENCE` /
`CONFLICT` / `CONFIRMED_ERROR` / `SYMPY_FAIL` / `UNKNOWN`。

## 4. 校验层（可插拔）

### 4.1 统一接口

所有校验器继承 `VerifierBase`，实现同一个方法：

```python
verify(latex_expr, cpp_expr_or_code, context=None) -> Verdict
```

`Verdict` 与现有 `CheckResult` 是同一类型的别名，因此 `main.py` 和
`eval.py` 的既有调用无需修改。结果统一包含：

- `ok`：是否通过；
- `status`：一致、不一致或无法判定的具体状态；
- `detail`：人类可读证据；
- `diff_points`：差异采样点；
- `meta`：校验器名称、模型、证据等扩展信息。

### 4.2 SymPyVerifier

`SymPyVerifier` 只包装原有 `verify_latex_vs_code()`，不复制数学逻辑，也不改
阈值：

1. **严格化简**：`simplify(a - b) == 0` → `equivalent`
2. **归一化**：依次尝试 `expand` / `trigsimp` / `factor` / `cancel`
3. **数值采样**：随机 24 点（避开复数/定义域外），相对误差 `< 1e-8`
4. **矩阵**：先比 `shape`，再逐元素走 1–3
5. 失败时记录最多 5 个 `DiffPoint`

模块级 `compare_expressions()`、`verify_latex_vs_code()` 等函数继续保留，
作为向后兼容入口。

### 4.3 异构 VLM 校验与自动回退

`verifier_hetero.py` 提供 `GLM4VVerifier` 和 `QwenVLVerifier`。Key 分别从
`GLM_API_KEY`、`QWEN_API_KEY` 环境变量读取，并通过 OpenAI Chat Completions
兼容协议发起真实网络调用。服务地址分别由 `GLM_BASE_URL`、
`QWEN_BASE_URL` 配置；`HETERO_VERIFY_TIMEOUT` 控制单次调用超时，
`HETERO_VERIFY_ENABLED` 控制是否启用异构分支。

调用要点：

- 使用 `openai.OpenAI(api_key=..., base_url=..., timeout=...)` 的
  `chat.completions.create`，固定 `max_tokens=1024`、`temperature=0`；
- 提示词要求模型只返回
  `{"verdict":"equivalent|not_equivalent|unknown","reason":"...","diff":"...","index_check":"..."}`；
- JSON 解析兼容 Markdown 围栏和前后废话，非法 JSON 或非法 verdict 收敛为
  `unknown`；超时、网络和鉴权异常收敛为 `error`；
- 成功结果的 `meta` 记录校验器、提供方、模型、网络状态、耗时、token 用量和
  最长 500 字符的脱敏响应证据，不保存 Key；
- 构造时仍检查 Key 和模型名，缺失时抛受控的
  `VerifierConfigurationError`，不会误报 `equivalent`。

**索引范围结构检查。** `verify.extract_index_scopes()` 从 LaTeX 的 `\sum_{...}`
与 C++ 的 `for (...)` 中提取变量、上下界和所在行，作为证据注入提示词并写入
`meta["index_scopes"]`。提示词明确要求模型按算法的标准数学定义核对索引范围——
例如 LU 分解必须满足 `U_ij = A_ij - Σ_{k<i} L_ik·U_kj`、`L_ij` 的上界为
`k<j`；公式与代码即使彼此一致、但都偏离标准定义，也必须判 `not_equivalent`。
模型的索引审计结论落在 `meta["index_check"]`。

这一机制专门覆盖"公式与代码一致地错"的情况：单纯比较表述一致性会让这类
样例误通过，而索引审计能把它判为 `not_equivalent`（进而在汇总层成为
`CONFIRMED_ERROR`）。

`build_verifier_chain()` 的策略：

- 没有任何异构 Key：直接返回纯 `SymPyVerifier`；
- 有一个或多个异构 Key：返回包含 SymPy 与对应真实校验器的 `VerifierChain`，
  并用 `[HYBRID_CHAIN]` 日志打印实际生效的 `名字=模型` 组合；
- `HETERO_VERIFY_ENABLED=False`：即使配置了 Key，也只返回纯 `SymPyVerifier`；
- 可以通过 `include_glm=False` / `include_qwen=False` 单独关闭某个校验器。

**Qwen-VL 接入（工单3）。** `QwenVLVerifier` 的 Key 既接受 `QWEN_API_KEY`，
也接受 DashScope 官方变量名 `DASHSCOPE_API_KEY`；默认模型从 `qwen-max`
改为视觉模型 `qwen3-vl-plus`（纯文本模型拿不到图片证据），默认地址为
`https://dashscope.aliyuncs.com/compatible-mode/v1`。实测单次判定 5–7 秒、
约 1100 token，能把「1-based 求和 vs 0-based 循环」这类索引歧义判成
`not_equivalent`。

**校验缓存的隔离维度（工单3）。** `Pipeline._verify_scope` 形如
`verifier:<模式>:<逻辑版本>|hetero=deepseek@<模型>,qwen@qwen3-vl-plus`。
加入或去掉任一异构校验器都会改变 scope，从而避免「加了 Qwen 之后仍复用
没有它的旧结论」。

**三层容错（工单5）。** 异构调用按以下顺序收敛，任何一层成功即返回：

1. 正文 JSON → 失败时用 `reasoning_content` 里的 JSON；
2. 正文为空 → 一次「请直接输出一行 JSON」重试；
3. 正文非空但不是 JSON（模型把推理写进正文）→ 一次 JSON 修复重试，
   把上一次输出原样回贴并要求只返回 JSON 对象。

### 4.4 串行、并行与汇总规则

`VerifierChain(mode="serial"|"parallel")` 使用标准库
`concurrent.futures.ThreadPoolExecutor` 执行并行分支。单个校验器抛异常时，
链会把它转换为 `error` 结果，不会拖垮其他分支。

汇总规则：

| 各分支结论 | 汇总状态 | 是否通过 |
|---|---|---|
| 全部一致 | `equivalent` | 是 |
| 全部不一致 | `not_equivalent` | 否 |
| 一致与不一致同时存在 | `needs_human_review` | 否，需人工复核 |
| 存在 unknown，且没有 inconsistent | `unknown` | 否，不能声明全一致 |
| 同时存在 unknown 与 inconsistent | `not_equivalent` | 否，保留不一致优先的既有语义 |

在保持上述 `status` 不变的前提下，`meta.verdict_label` 按以下规则给出审查
结论：

| 分支条件 | `verdict_label` | 对应汇总 `status` |
|---|---|---|
| 全部为 consistent | `HIGH_CONFIDENCE` | `equivalent` |
| 同时存在 consistent 与 inconsistent | `CONFLICT` | `needs_human_review` |
| 存在 inconsistent 且无 consistent（含 SymPy 无法判定、视觉确认错误） | `CONFIRMED_ERROR` | `not_equivalent` |
| 存在 unknown，且至少一个 consistent、没有 inconsistent | `SYMPY_FAIL` | `unknown` |
| 其他组合 | `UNKNOWN` | 保留原有汇总状态 |

`meta` 同时保存 `sympy_verdict` 和 `hetero_verdicts`，便于审查各层结论。

### 4.5 校验模式与缓存调用流程

`Pipeline` 支持三种校验模式，并写入报告顶层 `verifier_mode` 字段：

- `sympy-only`：直接构造 `SymPyVerifier()`，只走符号与数值对比。
- `hybrid`：调用
  `build_verifier_chain(mode="serial", include_deepseek=True)`，在 SymPy
  之后追加 `DeepSeekVerifier`；缺少 Key 或构造失败时保留纯 SymPy 链。
- `auto`：调用 `build_verifier_chain(mode="serial")`，保持旧调用行为；
  GLM/Qwen 是否有 Key 决定是否自动追加对应校验器。

`DeepSeekVerifier` 继承 `HeteroVerifier`，Key 从 `DEEPSEEK_API_KEY` 读取，
模型使用 `config.TEXT_MODEL`，服务地址使用 `config.DEEPSEEK_BASE_URL`（由
`OPENAI_BASE_URL` 派生，默认 `https://api.openai-next.com/v1`）。CLI
通过 `--verifier-mode {sympy-only,hybrid}` 显式选择模式；不指定时使用
`auto`。

校验缓存 scope 为 `verifier:<实际模式>:<VERIFIER_LOGIC_VERSION>`，因此
`auto`、`sympy-only` 与 `hybrid` 三者的校验结果不会互相复用，且归一化规则
升级后旧缓存自动失效（`VERIFIER_LOGIC_VERSION` 在 `main.py` 顶部维护）。
`hybrid` 使用 C++ 输入时，`main.py` 会把原始源码合并为
`context["cpp_code"]` 传给校验器，同时保留已有的 `preparsed_lhs_expr` /
`preparsed_rhs_expr`，使索引审计能够读取 `for` 循环边界。

### 4.6 超时熔断的跨进程持久化

`agent/circuit_breaker.py` 提供 `PersistentCircuitBreaker`：

- 状态写入 `cache/circuit_state.json`，采用“临时文件 + `os.replace`”原子落盘，
  避免并发写坏 JSON；
- 读写通过文件锁串行化：优先使用 `filelock.FileLock`，不可用时退化为
  `O_EXCL` 创建锁文件 + 30s 陈旧锁回收；
- 连续超时达到阈值（默认 2）后 `is_open()` 为真，`build_verifier_chain()`
  退化为纯 `SymPyVerifier`，后续进程同样能观察到熔断状态；
- 一旦有成功调用立即关闭熔断并清零计数；
- 环境变量 `MATH_AGENT_CIRCUIT_STATE`、`MATH_AGENT_CIRCUIT_LOCK`、
  `MATH_AGENT_CIRCUIT_DISABLED`、`HETERO_CIRCUIT_BREAKER_THRESHOLD` 可覆盖
  路径、禁用与阈值；
- 命令行：`python -m agent.circuit_breaker --status|--reset`。

`verifier_hetero._HybridCircuitBreaker` 只是该类的薄封装（`provider="hybrid"`），
因此 CLI、Web、benchmark 三个入口共享同一份熔断状态。

### 4.7 LaTeX 归一化层

`agent/symbol_norm.py` 在 `latex_to_expr()` 解析前依次执行：

| 函数 | 作用 | 典型输入 → 输出 |
|---|---|---|
| `normalize_latex_symbols` | 希腊字母、运算符等符号统一 | `\theta` 等保持规范形式 |
| `normalize_sum_limits` | 补全求和/求积上下限 | `\sum_i a_i b_i` → `\sum_{i=0}^{n-1} a_i b_i` |
| `normalize_iteration_subscripts` | 消除迭代/时间步下标 | `w_{t+1}=w_t-\eta g_t` → `w=w-\eta g` |
| `normalize_norm_bars` | 范数符号改写 | `\left\|x\right\|_2` → `\|x\|` → `|x|` |

这些规则只影响“解析用的规范化文本”，不改变报告里展示的原始 LaTeX。
带集合索引的 `\sum_{i \in S}` 不会被改写；`\alpha_k`、`a_i` 这类普通索引
也保持不变。

### 4.8 PDE 兜底分析

PDE 无法用 SymPy 完成严格符号证明，`agent/pde_analysis.py` 提供纯文本级
兜底：

1. `is_pde()` 识别 `\partial`、`\nabla`、`\Delta` 等算子；
2. `residual_signature()` 把等式两侧按顶层 `+/-` 拆项，提取每项的微分算子
   序列（`dt`/`dxx`/`div`/`lap`/`grad`），其中张量散度 `\partial_{x_i}\tau_{ij}`
   归一化为 `div`；
3. `analyze()` 比较两侧算子多重集，给出 `structurally_aligned`；
4. 无论是否对齐，都设置 `needs_human_review=True`，并把结论写入
   `steps.pde_analysis` 与 `steps.checks[].meta`。

`main.py` 在生成 checks 后调用该模块：PDE 用例的 `meta` 一定带有
`pde_detected`、`needs_human_review`、`pde_structurally_aligned` 和
`pde_capability_limit`，`detail` 会说明“连续方程与离散实现之间不存在
SymPy 可判定的代数等价关系”。

### 4.9 变量映射与矩阵链规则（工单1/2）

C++ 翻译层常把一行公式拆成多行、或引入中间变量（`T = Σ exp(z_j)`），导致
变量名与图片公式对不上，SymPy 只能给 `unknown`。两条互补的规则解决这类
「表示差异但数学等价」的用例：

1. **多行参考拆行配对**：`main.py::_split_multiline_latex()` 把参考 LaTeX
   （翻译结果）按空行/顶层条件分隔成多行，`_parse_reference_latex_candidates()`
   为每行额外生成 `row:<n>` 候选；任一行与图片公式等价即判 `equivalent`。
   `ref_normalize.strip_trailing_conditions()` 负责剥离 `i=1,\dots,n` 这类
   范围条件，避免整行解析失败。
2. **矩阵链结合律（`agent/matrix_chain.py`）**：先归一化 `\left/\right`、
   `\ell→l`、`\operatorname{X}→@X@`、`\qquad→\n`，再识别两类结构：
   纯字母乘积链（`(AB)C = A(BC)`）与逐元素链
   （`d_{ij}=ΣΣ a_{ik}b_{kl}c_{lj}`）。规范化成
   `(自由下标, ((基底, 下标...), ...))` 后：
   - 两边乘积序列相同 → `equivalent`（结合律）；
   - 逐元素签名（≥3 个因子）相同 → `equivalent`；
   - 只有一个乘积且多重集相同但顺序不同 → `not_equivalent`；
   - 出现 `(AB)C = A(CB)` 这类左侧恒等式 → `not_equivalent`。

该规则在 `verify.py` 的 `verify_latex_vs_code()` 与 `SymPyVerifier.verify()`
中都最先尝试，属纯本地判定，不联网；`tests/test_matrix_chain.py` 覆盖 11 条
用例，benchmark 的 `matrix_chain` 子集从 1/4 提升到 4/4。

## 5. 扩展接口

### 5.1 接入真实异构校验器

已实现真实异构校验器。接入新的 OpenAI 兼容校验器时，只需继承
`HeteroVerifier` 并实现或配置 `verify()` 所需的提供方信息，继续返回
`CheckResult`。应保持以下约束：

1. 不把 Key 写入日志或结果；
2. 网络超时、限流和解析失败统一返回 `unknown` 或 `error`；
3. 只有模型明确给出可验证证据时才返回 `equivalent`；
4. 接入 `build_verifier_chain()` 后，由校验链统一处理回退和冲突。

当前接入点：`Pipeline.run()` 已使用 `build_verifier_chain(...).verify(...)`
处理 `--expect` 和已解析 C++ 翻译公式的校验；无异构 Key 时该链自动退化为
单 `SymPyVerifier`。

### 5.2 新增 OCR 引擎

实现 `recognize(src) -> OcrResult` 的可调用对象，注入 `Pipeline.ocr()` 的 fallback 链即可，无需改主流程。

### 5.3 新增输出格式

`main.py` 的 `build_markdown(report)` 与 JSON 落盘相互独立；新增格式只需增加一个 `build_xxx(report)` 函数。

### 5.4 新增 Web 交互

`web/app.py` 把 `Pipeline.run()` 的返回值映射成界面字段：

- `_verdict_of(report)`：`equivalent`→✅、`not_equivalent`→❌、`unknown`/
  `translation_failed`/`parse_error`→⚠️、无 LaTeX 的错误→“非公式/已跳过”；
- `_cache_text(report)`：从 `report["cache"]` 汇总 OCR/校验/翻译三类命中；
- `_detail_text(report)`：优先展示 `latex_vs_cpp` 的 `detail` 与 `index_check`；
- `run_single()` / `run_batch()` 都是 generator：先 yield “正在处理”，再
  yield 结果，配合 `gr.Progress` 显示逐张进度；批量里单张异常只记录
  `verdict=error`，不中断整批；
- 历史记录由 `_normalize_history_record()` 统一成 9 字段字典，
  `_persist_history_unlocked()` 用「临时文件 + `os.replace`」原子写
  `output/web_history.json`，最多保留 200 条；
- 导出：`export_records(fmt)` 输出 JSON/CSV 到 `output/web_exports/`，
  空数据时只提示、不建文件；
- 批量里若图片同目录存在同名 `.cpp`，自动作为该图的参考代码，支持
  「多图 + 多代码」成对回归。

新增展示字段时只改这几个映射函数，不要复制判定逻辑。

## 6. 安全边界

- 所有文件写入限定在 `D:\math-agent\`；
- API Key 不落盘、不进日志；
- `main.py` 的文件读取使用 `errors="replace"`，避免编码异常中断流水线；
- Web 界面默认只监听 `127.0.0.1:7860`，不对外开放；队列并发固定为 1，
  避免多请求同时写 pickle 缓存。

## 7. 性能与成本（实测）

数据来自 `output/benchmark_w8_hybrid`（33 例全冷启动）与
`output/performance_analysis.md`：

| 阶段 | 合计 | 占比 | 说明 |
|---|---:|---:|---|
| OCR（视觉模型） | 248.9s | 25.0% | 图片识别，`temperature=0` 且缓存按图片内容哈希 |
| C++→LaTeX 翻译 | 355.1s | 35.7% | 单次 3–26s，受模型侧排队影响 |
| 异构 LLM 校验 | 391.1s | 39.3% | 32 次调用，平均 12.2s，空响应重试 4 次 |
| SymPy 校验 | ≈0 | ~0% | 纯本地，亚秒级 |
| 进程启动/导入/写报告 | 218.1s | — | benchmark 每例新起进程，约 6.6s/例 |

结论：瓶颈是模型往返而不是 `compact_cpp_context`（实测 0.052ms/次，可忽略）。
真正有效的降耗手段依次是：

1. **提高缓存命中率**：校验缓存按「LaTeX + 代码 + 逻辑版本 + 校验链」隔离，
   同图重复校验约 1s（实测命中后平均 2.2s）；
2. **抑制空响应**：空响应会触发一次重试，直接把单例成本翻倍；
3. **多校验项并行**：`VerifierChain(mode="parallel")` 已具备并行能力，
   多 check 用例可把墙钟压到最慢一次调用。

## 8. 可解释批改（w11）

### 8.1 位置与触发

判定链路完全不变。`main.py:run()` 在 `report["ok"]` 落盘之后调用
`attach_explanations(report, work_latex=..., ref_latex=..., raw_cpp_code=...)`，
只往 `steps.checks[i].explain` 写一个字典，`ok / status / lhs / rhs / detail /
diff_points / meta` 一个都不改。`needs_explanation()` 只接受 `not_equivalent` /
`needs_human_review` 状态或 `CONFIRMED_ERROR` 标签；`equivalent` /
`numeric_equivalent` 永远跳过（即使 meta 里残留错误标签）。`EXPLAIN_ENABLED=0`
时在入口直接返回，报告字段集合与改动前逐键相同（离线测试有断言）。

### 8.2 证据组装

`_build_evidence()` 把五类材料压成可复核文本，同时算一个 `ev` 摘要进缓存键：

1. 代码 `L1..Ln`（`compact_cpp_context`，保留原始行号）；
2. SymPy 的 `lhs/rhs/detail` 与最多 3 个数值差异点；
3. 异构 VLM 的判定理由（最多 2 条，带 verifier 名）；
4. 确定性观察：符号相反、缺 `sqrt`、求和边界、转置式下标、非 1 步长等；
5. PDE 标志。`pde_strong` 只在出现 `∂ / \partial / ∇² / ∇·` 时为真，
   用来把「连续方程 vs 离散实现」与优化里的 `∇L` 区分开。

### 8.3 模型调用

复用 `llm.py` 的客户端与模型名，沿用 `verifier_hetero.py` 验证过的 JSON 强输出：
`response_format={"type":"json_object"}`（服务端不支持时自动降级），正文优先、
`reasoning_content` 兜底，最多 2 次强制 JSON 修复。推理模型会把预算花在推理通道上，
因此正文预算按 8192 → 16384 → 32768 递增（实测 2048 会连续三次空正文）；
温度 0，超时 90s，全部可被环境变量覆盖。

### 8.4 输出契约

| 字段 | 约束 |
|---|---|
| `error_type` | 17 类之一；无法归一化时回落 `unknown` 并强制复核 |
| `location` | 代码行号（`L12: ...`）或公式片段；空值给占位说明 |
| `suggestion` | 可直接采用的 C++ 片段或正确公式 |
| `confidence` | 0–1，越界裁剪；PDE 强制 ≤0.6 |
| `needs_human_review` | PDE、`unknown`、置信度 <0.5 恒为 `true` |
| `source` | `llm` / `rule` / `cache`（`rule` 为模型失败时的确定性兜底） |

模型失败不会让面板空白：`_rule_based_explanation()` 用符号相反、缺开方、非 1 步长、
转置等确定性证据给出合法 JSON，并强制人工复核。强 PDE 证据下
`_apply_pde_guard()` 会把模型给出的局部结论收敛为 `pde_capability`，
避免"连续 vs 离散"被误报成一行可修的符号错误。

### 8.5 分类口径（与 annotations 对齐）

判定顺序 A→D：A 能指出具体可局部修正的错误 → 具体类型；B 指不出错误、纯解析器
不支持（`\displaystyle`、环境词）→ `parser_limitation`；C 连续方程与离散实现整体
不可比 → `pde_capability`；D 符号层无法完成整体证明（如 `A=LU` 的任意 n 归纳）
→ `symbolic_scope_limit`。

为对齐 `tests/benchmark/annotations.json` 的既有词表，做了三处口径收口（只影响
解释层，不影响判定）：`sum_bounds` 并入 `index_scope`；`off_by_one` 只用于一维序列
的求和边界；`variable_mapping` 只表示变量身份错位（用错下标仍归 `index_scope`）；
单元素下标顺序写反归 `index_scope`，整体转置才归 `transpose_error`。

### 8.6 缓存

`explain.pkl`，键 = sha256(模型 + 提示词版本 + 状态 + 判定标签 + PDE 标志 + 证据摘要)，
命中返回 `source=cache`；`MATH_AGENT_CACHE=0` 全局关闭；解释缓存单独统计，
不计入 ocr/verify/translate 的命中率。

### 8.7 质量与成本（本轮实测）

27 例预期 fail：严格一致 25/27 = 92.59%，根因族 26/27 = 96.30%（数据见
`output/explain_quality_report.md`）。两例未命中均为标注口径分歧，已如实写入
`output/explain_failures.md`：`image_03`（标注 `symbolic_scope_limit`，证据显示
`k<j` 与 `k<i` 的具体边界差，判 `index_scope`）与 `image_04`（标注
`parser_limitation`，连续 N-S 与离散残差不可比，判 `pde_capability`）。
解释缓存使重复运行零调用；单例平均约 1 次调用、2–20s。

### 8.8 w12 稳定性收尾：解释层不变，链路口径收敛

本轮只做「工程收尾 + 深度回归」，解释层与判定层的代码没有改动，但把三件会影响
指标复现的事实固定下来（对应 `README.md` §6.13.1）。

**（1）校验链口径。** `Pipeline._hetero_chain_tag()` 把「实际会调用的异构校验器
与模型」压进校验缓存命名空间，所以换链会自然失效旧缓存 —— 这是正确行为，但也意味着
`auto` 与 `hybrid` 的指标**不可混比**：

| 模式 | 无 GLM/Qwen Key 时的链 | 异构层是否参与 | 本轮 50 例 |
|---|---|---|---:|
| `auto` | `[SymPyVerifier]` | 否（`include_deepseek` 默认 `None`） | 31/50 |
| `hybrid` | `[SymPyVerifier, DeepSeekVerifier]` | 是 | 47/50 |

`auto` 的语义是「有第三方 Key 才升级异构」，不是「总是三方投票」。写报告时必须
标注模式，否则 62% 与 94% 的差异会被误读成模型退化。

**（2）异构仲裁的边界优先级。** `_capability_gap_consensus()` 只在
`verdict_label == SYMPY_FAIL` 且全部异构校验器一致**等价**、且非 PDE 时才升级结论；
PDE 用例被 `_looks_like_pde()` 显式排除。实测把 QwenVLVerifier 接入主网关后，
`image_04` 会因异构「不一致」票从 `unknown` 变成 `CONFIRMED_ERROR` —— 这条路径
`_resolve_verdict_label()` 并不拦（它只拦「升级为等价」）。因此 Qwen 的默认端点
保持 DashScope 官方地址，主网关通过 `QWEN_BASE_URL` 显式开启；`needs_human_review`
与 `pde_capability_limit` 在任何情况下都保留。

**（3）解释层与判定的解耦再次验证。** 关闭 `EXPLAIN_ENABLED` 前后，报告键集合逐键
相同（`test_explain.py` 断言）；解释失败只写日志。本轮 27 例解释复核全部命中缓存
（0 token），一致率与 w11 完全一致，说明解释缓存键（模型 + 提示词版本 + 状态 +
判定标签 + 证据摘要）没有随校验链变化而漂移。


