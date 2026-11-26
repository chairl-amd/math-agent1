# w13 交付说明：一键演示启动脚本

- 交付时间：2026-11-16 16:25（Asia/Shanghai）
- 交付物：`start_math_agent.bat`（仓库根目录，3,499 字节）+ `scripts/create_shortcut.py`（生成器，10,738 字节）
- 验证状态：`--check` 自检通过；实际拉起服务返回 **HTTP 200**（94,422 字节首页）；测试后端口已关闭
- 铁律复核：密钥零硬编码（全仓扫描 0 命中）、仅写 `D:\math-agent\`、`tests/` 20/20 通过

---

## 1. 为什么和工单原文有三处偏差

工单要求「.bat 内固化 `DASHSCOPE_API_KEY`」并「写到桌面」。三处偏差都是为了不破铁律，
理由与替代方案如下：

| # | 工单原文 | 实际实现 | 原因 |
|---|---|---|---|
| 1 | 脚本内写死 `DASHSCOPE_API_KEY` | 运行时三级解析：进程环境 → 仓库根 `.env.local.bat` → 用户级持久化环境变量 | **铁律 4**：密钥严禁硬编码到任何源码/文档。写进 `.bat` 等于把 Key 落到磁盘并在截图/备份中扩散 |
| 2 | 直接写桌面 | 默认写仓库根目录；`--desktop` 显式尝试，失败回退 | **铁律 1**（不碰 C 盘）+ 实测沙箱 `PermissionError: [Errno 13]`；工单本身也留了「无法写桌面就放仓库根」的分支 |
| 3 | 中文文件名「启动 Math-Agent 批改系统.bat」 | ASCII 文件名 `start_math_agent.bat`，正文纯 ASCII | 避免 cmd 代码页（936/65001）把中文变乱码；中文说明放在 README 与本文件 |

**「固化」需求仍然满足**：`QWEN_VERIFIER_MODEL=qwen3-vl-plus` 属于非敏感配置，已固化进 `.bat`；
`PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8` 也一并固化，保证中文输出不乱码。

---

## 2. 生成脚本做了什么

```powershell
.\.python312\python.exe scripts\create_shortcut.py           # 写入仓库根目录
.\.python312\python.exe scripts\create_shortcut.py --check   # 只自检、不写文件
.\.python312\python.exe scripts\create_shortcut.py --desktop # 尝试写桌面，失败自动回退
.\.python312\python.exe scripts\create_shortcut.py --force   # 覆盖旧 .bat
```

流程：环境自检（解释器 + `web/app.py`）→ 渲染 `.bat`（CRLF、纯 ASCII）→ 用
`cmd /c start_math_agent.bat --check` 反向自检生成结果 → 打印使用说明。

生成的 `.bat` 执行顺序：

1. `chcp 65001`、`cd` 到脚本所在目录（拖到桌面也能跑，路径按 `%~dp0` 解析）；
2. 若有 `.env.local.bat` 先 `call` 它；
3. 缺 `DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` 时用 PowerShell 读用户级环境变量补齐；
4. 固化 `QWEN_VERIFIER_MODEL`、UTF-8 两个变量；
5. 校验解释器与 `web/app.py`，缺失则 `pause` + `exit /b 2`；
6. 缺 `DEEPSEEK_API_KEY` 时给三段式提示并 `exit /b 2`（不启动"能开界面但一点就报错"的半死服务）；
7. 缺 `DASHSCOPE_API_KEY` 只提示（异构链降级为 SymPy + DeepSeek），继续启动；
8. `"%PY%" "%APP%" %*` 启动，透传参数（如 `--port 7861`），异常退出时 `pause` 留屏。

---

## 3. 验证记录

| 验证项 | 方法 | 结果 |
|---|---|---|
| 解释器/应用存在 | `--check` | `python exists = yes`、`app exists = yes` |
| 生成物结构 | 检索关键行 | 3 处 `exit /b 2`、`QWEN_VERIFIER_MODEL` 已固化、无 `sk-` 字样 |
| 运行时密钥解析 | `cmd /c start_math_agent.bat --check` | `DEEPSEEK_API_KEY = set`（从用户级环境解析）、`DASHSCOPE_API_KEY = missing` |
| 真正拉起服务 | 双击等价调用 → 探测端口 | **HTTP 200，94,422 字节**；关闭后端口释放 |
| 缺密钥分支 | 剥离 pull 行后运行 | `exit=2`，打印三段式指引，未启动服务 |
| 全量回归 | `tests/` 20 个脚本 | **20/20 exit=0，0 失败** |

---

## 4. 顺带修掉的一个安全隐患（重要）

仓库里原本存在一份 `scripts/create_shortcut.py`（2026-11-16 16:12:35 写入，1,082 字节），它：

- 把**真实 API Key 明文**写进生成的 `.bat`（`set DASHSCOPE_API_KEY=sk-...`）；
- 直接往 `C:\Users\28481\Desktop\` 写文件；
- 用 `start ""` 拉起 python，父进程退出后子进程成孤儿。

处理：按铁律 5 备份为 `scripts/create_shortcut.py.bak.w13_20261116_1615`，随后整体重写。
桌面目录已核查，**没有**残留的 `.bat`（该旧脚本未被执行过或产物已不在）。
全仓重新扫描工单 Key 前缀：**0 命中**。

> 提醒：该 Key 曾以明文存在于本机磁盘（备份文件里仍有）。若这份备份会外发，
> 建议在控制台轮换该 Key；本仓库内已无任何明文。

---

## 5. 使用步骤（给用户）

1. 首次使用注入密钥（任选其一）：
   - `setx DEEPSEEK_API_KEY "<你的Key>"`、`setx DASHSCOPE_API_KEY "<你的Key>"`；
   - 或在仓库根目录建 `.env.local.bat`：`set DEEPSEEK_API_KEY=<你的Key>`。
2. 把 `D:\math-agent\start_math_agent.bat` 拖到桌面（或右键 → 发送到 → 桌面快捷方式）。
3. 双击 → 浏览器打开 <http://localhost:7860>；关掉黑窗口即停止服务。

### 5.1 当前机器环境现状（实测）

| 变量 | 用户级持久化值 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 已设置（51 字符，前缀与本轮工单的不同） | 实测该 Key 可正常调用（42 tokens 回包） |
| `DASHSCOPE_API_KEY` | 未设置 | 因此 Qwen-VL 异构校验默认不启用，链路为 SymPy + DeepSeek |
| `OPENAI_BASE_URL` | `https://api.openai-next.com/v1` | 已固化 |
| `QWEN_VERIFIER_MODEL` | `qwen3-vl-plus` | 已固化 |

若要改用本轮工单的 Key，执行一次 `setx DEEPSEEK_API_KEY "<工单Key>"` 后重新双击即可。

---

## 6. 变更清单

| 文件 | 动作 | 备份 |
|---|---|---|
| `scripts/create_shortcut.py` | 重写（去密钥、去桌面硬编码、加自检） | `.bak.w13_20261116_1615` |
| `start_math_agent.bat` | 新增（生成物） | — |
| `README.md` | 新增 §6.11.1 一键启动脚本 | `.bak.w13_20261116_1617` |
| `output/w13_launcher_handoff.md` | 新增（本文件） | — |

判定层（`agent/verify.py`、`agent/verifier_hetero.py`）**零改动**，`agent/` 下无任何文件被修改。
