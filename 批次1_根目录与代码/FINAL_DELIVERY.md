# FINAL_DELIVERY — 交付清单与使用说明

> 生成时间：2026-11-13
> 目标机：RHEL 兼容环境，当前为 CentOS Stream 10 @ 192.168.179.128
> 工作目录：`/home/charlie/math-agent/`

## 1. 产出清单

### 1.1 核心代码（agent/，6 个模块）

| 文件 | 行数级 | 职责 |
|---|---|---|
| `agent/config.py` | ~80 | 模型名、API Key（env）、路径常量 |
| `agent/ocr_pix.py` | ~200 | Pix2Text 封装、预处理、降级 |
| `agent/ocr_vision.py` | ~180 | DeepSeek 视觉 API、base64、清洗 |
| `agent/verify.py` | ~380 | SymPy 四级校验 + DiffPoint + 报告 |
| `agent/llm.py` | ~200 | C++ ↔ 数学双向翻译 |
| `agent/main.py` | ~330 | 三层编排 + CLI + 报告 |

### 1.2 测试与评估

| 文件 | 说明 |
|---|---|
| `tests/cpp/*.cpp` | 4 个 C++ 样例（GLM）：正确矩阵乘、旋转 bug、LU 精度、共轭梯度 |
| `tests/formula/*.png` | 10 组公式图 + 1 张非数学图 |
| `tests/test_edge_cases.py` | 7 个边界用例 |
| `tests/test_pipeline_mock.py` | 链路A 代码路径 + 降级链（mock API） |
| `eval.py` | 自动评估（C++ + 符号用例） |

### 1.3 文档

| 文件 | 内容 |
|---|---|
| `README.md` | 项目介绍、安装、使用 |
| `ARCHITECTURE.md` | 三层架构图、模块说明、扩展接口 |
| `INTERVIEW.md` | 面试包装、演示流程、追问应答 |
| `KEY_POINTS.md` | 技术亮点与量化指标 |
| `FINAL_DELIVERY.md` | 本文档 |

### 1.4 运行产物（output/）

- `eval_report.json/.md`：评估报告
- `pix2text_latex.json`：链路B 的 10 组识别与校验数据
- `report_<ts>.json/.md`：单次运行报告
- `bin/`：C++ 编译产物

## 2. 当前验证状态

| 项目 | 结果 | 说明 |
|---|---|---|
| C++ 样例编译 | 4/4 ✅ | g++ 14.4.1 + GLM |
| 符号校验用例 | 10/10 ✅ | 含反例正确判负 |
| 边界测试 | 7/7 ✅ | 空输入/超长/非数学图/缺失文件/矩阵维度 |
| 链路B（Pix2Text） | 10/10 识别，5/5 校验 ✅ | 含 `\operatorname` 规范化 |
| 链路A（视觉）代码路径 | 4/4 ✅ | mock API，降级链 `vision>pix2text` 验证 |
| 链路A 真实 API | 10/10 识别，5/5 校验 ✅ | 模型 `deepseek-v4-flash-vision-exp`，10 图 12.9s |
| 双链路对比（工单22-24） | ✅ | 平均 LaTeX 相似度 85.95%，校验结论一致率 100% |
| 端到端 CLI | ✅ | 图片+期望 2.23s 通过；图片+C++ 走通 LLM 翻译层 |

## 3. 使用说明

```bash
cd /home/charlie/math-agent
source venv/bin/activate

# 单图校验（视觉优先，自动回退）
python agent/main.py -i tests/formula/formula_03.png \
    --expect "sin(theta)**2+cos(theta)**2-1"

# 强制本地 OCR
python agent/main.py -i tests/formula/formula_03.png --engine pix \
    --expect "sin(theta)**2+cos(theta)**2-1"

# 跳过 OCR，直接校验 LaTeX
python agent/main.py --latex "\frac{a}{b}" --expect "a/b" --no-llm

# 全量评估
python eval.py

# 边界测试
python tests/test_edge_cases.py
```

## 4. 面试演示指南

### 4.1 30 秒讲清项目价值

“这个项目解决数值代码和数学公式互相看不懂的问题：它能把公式图片识别成
LaTeX，再翻译为 C++；也能把 C++ 的核心运算还原成数学公式。关键不是让大模型
自由发挥，而是翻译后继续用 SymPy 做符号化简、数值采样和差异定位，把
‘看起来对’变成可解释的一致性结论，降低数学幻觉。”

讲述时按这个顺序停三拍：双向翻译、SymPy 符号校验、结构化报告。

### 4.2 现场 demo 操作流程

1. 先用手机拍一张排版清楚的公式，尽量只保留一个公式，避免阴影和倾斜；
2. 把图片传到虚拟机项目目录，例如 `/home/charlie/math-agent/tests/formula/`；
3. 设置 Key 后执行：

```bash
cd /home/charlie/math-agent
export DEEPSEEK_API_KEY='your-api-key'
bash demo.sh --mode both --direction formula2code --verbose tests/formula/新图片.png
```

4. 终端依次展示【数学推导】【校验结果】【知识点备注】；
5. 打开 `output/demo_<时间戳>.md`，重点指出 OCR 双链路、翻译结果、SymPy
   结论和差异点；
6. 如果现场要展示代码到公式，再执行：

```bash
venv/bin/python agent/main.py \
  --direction code2formula \
  --cpp tests/cpp/matrix_mul.cpp \
  --verbose
```

### 4.3 备用方案

- **提前录屏**：会前完整录一次 `demo.sh`，保留终端和报告页面，现场网络异常时
  直接播放；
- **API 故障**：使用 `bash demo.sh --mode pix --verbose <图片>` 改走本地
  Pix2Text；该链路只做 OCR，代码转公式仍需要文本模型；
- **继续降级**：使用 `--latex` 和 `--expect ... --no-llm` 演示纯 SymPy 校验，
  完全不访问外部 API；
- **本地缓存样例**：提前保存一份成功报告（如 `output/final_report.md`），API
  不可用时展示样例结果，并明确说明这是缓存，不冒充现场实时输出；
- **现场顺序**：先检查 `venv/bin/python agent/main.py --help` 和
  `bash -n demo.sh`，确认脚本没有环境类错误。

## 5. 环境固化点（换机需注意）

1. **rapidocr 必须 3.9.2**：`cnstd 1.2.8` 与 `rapidocr 3.10.0` API 不兼容；
2. **HF 镜像补丁**：`cnstd/consts.py`、`cnstd/hf_downloader.py`、`pix2text/hf_downloader.py` 的官方端点已替换为 `hf-mirror.com`（venv 重建后需重打）；
3. **antlr4 4.11.1**：SymPy `parse_latex` 需要；
4. **g++ 14.4.1**：编译 C++ 样例；
5. **reasoning 模型 token 预算**：`deepseek-v4-flash` / `deepseek-v4-pro` 会先输出思维链，
   `max_tokens` 需 ≥ 2048，否则 `content` 为空（代码已默认补足）；
6. **模型可用性**：平台实际可用 `deepseek-flash`、`deepseek-v4-pro`，
   视觉模型 `deepseek-v4-flash-vision-exp` 不在 `models.list` 中但可直接调用。

## 6. 后续扩展建议

1. **接入真实视觉 API** 后，跑 `tests/formula/` 全量，补工单22-24 对比报告；
2. **LLM 规范化层**：把 `norm2`（`\operatorname` 修复）并入 `llm.normalize_latex`，对 OCR 噪声做模型级修复；
3. **verifier 接口**：`config.MODEL_VERIFIER` 预留，可接第三方符号引擎做交叉验证；
4. **矩阵公式**：扩展 LaTeX→SymPy 对 `pmatrix/vec` 的解析，覆盖线性代数场景；
5. **批量模式**：新增 `--batch` 遍历目录，适配作业批改场景。

## 7. 已知限制

- 大写 `E`/`I`/`pi` 等 SymPy 保留名会被 `parse_latex` 解析为常量，用例需避开；
- 积分式（`\int`）目前不做符号校验，仅记录 OCR 文本；
- Pix2Text 首次加载约 50s（模型 200MB+），建议常驻进程复用；
- 视觉 API 依赖网络，离线时自动降级。
