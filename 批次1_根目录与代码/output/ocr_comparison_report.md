# OCR 双链路对比报告

- 链路A（DeepSeek 视觉）OCR 成功率：100%
- 链路B（Pix2Text）OCR 成功率：100%
- 链路A 校验通过率：100%
- 链路B 校验通过率：100%
- 平均 LaTeX 相似度：85.95%
- 完全一致率：10%
- 校验结论一致率：100%

| 图片 | 相似度 | 完全一致 | A 校验 | B 校验 | 结论一致 |
|---|---|---|---|---|---|
| formula_01.png | 0.86 | ❌ | - | - | - |
| formula_02.png | 0.94 | ❌ | - | - | - |
| formula_03.png | 0.89 | ❌ | equivalent | equivalent | ✅ |
| formula_04.png | 1.00 | ✅ | equivalent | equivalent | ✅ |
| formula_05.png | 0.88 | ❌ | equivalent | equivalent | ✅ |
| formula_06.png | 0.53 | ❌ | - | - | - |
| formula_07.png | 0.90 | ❌ | - | - | - |
| formula_08.png | 0.79 | ❌ | equivalent | equivalent | ✅ |
| formula_09.png | 0.92 | ❌ | equivalent | equivalent | ✅ |
| formula_10.png | 0.88 | ❌ | - | - | - |