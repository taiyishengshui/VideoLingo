# VideoLingo 产品需求文档（PRD）

> **性质**：as-built 规格（当前已实现功能的准确快照），不是未来规划清单。
> **版本**：v1.12 ｜ **生成日期**：2026-11-09（v1.12 更新 2026-11-11）｜ **维护方式**：人工编辑本文件
> **用途**：本文件是需求侧的单一事实来源。以后要改功能，先改这里对应的 FR 条目，再让 AI 按本文档改程序。
> **范围**：`D:\code\vl` 当前工作树（含 2026-09 ~ 2026-11 的全部定制改造）。

---

## 0. 使用约定（给人和 AI 看的操作规则）

### 0.1 文档性质

- 每条 `FR-xx` 描述**现在的行为**，字段统一为：当前行为 / 入口与代码 / 配置 / 约束（不变式）/ 验收要点。
- 第 2 章 `G-xx` 是跨功能全局规则；改任何 FR 都不得违反 G 规则。若新需求与 G 冲突，AI 必须先指出冲突再动手。
- 第 11 章是「不要回退清单」，记录踩过的坑；改动这些地方前必须读。
- 第 7 / 8 / 9 章分别是配置、接口、目录的速查表；附录 A 是代码索引（只写文件与函数/常量名，**不写行号**，避免行号漂移）。

### 0.2 修改流程

用户侧：改本文件中对应的 FR 条目（可以直接用自然语言写「期望行为」），然后在对话里说「按 PRD 改」或指出 FR 编号。

AI 侧必须按序执行：

1. 读本文档的对应 FR、第 2 章相关 G 规则、第 11 章相关坑、第 12 章变更记录；
2. 按附录 A 定位代码并 **grep 核对现状**（文档可能与代码不同步，以代码为准；发现不一致先提醒用户更新文档）；
3. 只改与该 FR 相关的文件；涉及 401/402/403 决策、key 池、步骤清理时必须跑 `node check_single_source.js`；
4. 跑 0.4 验证清单；
5. **回写本文档**：更新被修改的 FR 条目 + 在第 12 章追加一行变更记录，保证文档与代码同步。

### 0.3 新增需求怎么标记

在对应章节加 `FR-xx（待实现）`，写清期望行为、影响的页面/接口/步骤、验收标准。AI 实现后把「待实现」去掉并补齐代码位置。

### 0.4 验证清单（每次改完必跑）

| 场景 | 命令 / 动作 |
|---|---|
| 改了 `core/` 任何 Python | `PYTHONPATH="C:/Users/happy/.workbuddy/binaries/python/vltest_deps;D:/code/vl" python3.exe -m pytest test/ -q` |
| 改了链 / key 池 / 401 决策 / 步骤清理 | 追加 `node check_single_source.js` |
| Python 语法快速自查 | `node _lex.js <file.py>`（本会话 Python 沙箱被禁，用它做字符级检查） |
| 改了 `frontend/` | `cd frontend && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/vite build` |
| 改了后端接口 / 服务 | 提醒用户重启 `python -m server`（用户常忘） |
| 涉及 GPU / whisperx 实跑 | 交给用户在自己的 cmd 里跑，本会话 conda 环境无法创建临时目录 |

### 0.5 环境事实

- Windows + Git Bash；项目根 `D:\code\vl`；`PROJECT_ROOT` 由 `server/app.py` 两层 `dirname` 推导。
- 启动：`python -m server`（托管 `frontend/dist`，默认 `127.0.0.1:8000`）；开发用 vite dev server `:5173`；`VL_HOST` / `VL_PORT` / `VL_RELOAD` 可覆盖。
- 本会话 conda `vl`（py3.10）跑不动；验证用 WorkBuddy 托管 `python3.exe`（3.13）+ `vltest_deps`。
- 用户偏好：极简指令式沟通、AI 自主完成重试与状态记录；**对 LLM 调用成本高度敏感**，任何工程都要持久化 + checkpoint-resume，刷新/中断不能浪费已花的费用。

---

## 1. 产品概述

### 1.1 定位

把一条视频（本地文件或网络链接）自动处理成**双语字幕视频**，可选继续生成**中文配音视频**。全过程由 11 个步骤组成；部分步骤支持分段/分批断点，其他步骤依靠完成文件或模型响应缓存减少重复工作，视频编码尚无中途续跑能力。流程重度依赖 LLM 翻译/切分，因此成本、缓存、断点、错误恢复是核心设计约束。

### 1.2 三种工作模式

| 模式 | 入口 | 工作区 | 说明 |
|---|---|---|---|
| 单任务 | 「流水线」页 | 项目根 `output/` | 一次一条视频，产物直接在 `output/`，跑完可归档到 `history/<视频名>/` |
| REST 批量 | 「批量处理」页 | `<根目录>_log/<子路径>/` 镜像工作区 | 递归扫描目录，逐条串行、每条独立子进程、`job_state.json` 断点 |
| 旧 CLI 批量 | `batch/OneKeyBatch.bat` | `batch/output/` | Excel 驱动的遗留实现，与 REST 批量**不共享状态**（见 FR-19） |

输入源（下载/选择文件）**不属于 11 步注册表**，它由「输入源」卡片单独触发一次性 job。

### 1.3 技术栈

- 前端：React 18 + TypeScript + AntD 5 + Vite；hash 路由（`#/pipeline` 等），无路由库；WebSocket 事件驱动，无全站轮询。
- 后端：FastAPI + uvicorn；`LogBus` 事件总线（2000 条环形历史 + 每订阅者 5000 队列，满了丢事件不阻塞任务）；`TaskManager` 单任务状态机。
- 流水线：`core/pipeline.py` 声明式注册表（11 步，惰性 import）；步骤完成态由磁盘 marker 判定。
- 外部工具：yt-dlp、ffmpeg、whisperX / pyannote VAD、spaCy、OpenCV、pydub、各类 TTS 后端。

### 1.4 页面地图（6 个页面）

| Hash | 页面 | 主要职责 |
|---|---|---|
| `#/pipeline` | 流水线 | 输入源、运行控制、11 步状态、实时日志、归档 |
| `#/batch` | 批量处理 | 目录扫描、勾选、进度点阵、范围重跑、日志 |
| `#/models` | 模型 | 平台凭证 / 模型 / 任务链 / key 池 / 连通性测试 |
| `#/terms` | 术语 & 提示词 | 术语库、提示词、切分实验场（L1~L4）、调用日志 |
| `#/artifacts` | 产物 | 浏览并下载 `output/`、`history/`、批量工作区的文件 |
| `#/settings` | 设置 | 25 个白名单配置项的可视化编辑 |

Header 全局按钮：「重启后端服务」「停止后端服务」（均二次确认，任务忙时 409）。`PlaceholderPage.tsx` 存在但未使用。

### 1.5 术语表

| 词 | 含义 |
|---|---|
| marker | 步骤「已完成」的判定文件；`is_done()` 只看它是否存在 |
| clean_extra | 强制重跑时要删、但**不参与**完成判定的断点/缓存文件 |
| force | 「重做」语义：删产物 + 断点 + LLM 缓存后真重跑，不是简单置状态 |
| 续跑 | 不带 force：已完成的守卫步骤直接跳过，从缺口继续 |
| 链（chain） | 命名模型序列；任务（task）= `log_title`，绑定到链，未绑定走 `default` |
| 阶段一 / 阶段二 | 字幕阶段 `sub`（6 步）/ 配音阶段 `dub`（5 步） |
| 生成字幕 | 用户口语，PRD 中统一指步骤 `align`（产出 `src.srt` / `trans.srt`） |
| 烧录 | 用户口语，PRD 中统一指步骤 `merge_sub`（`output/output_sub.mp4`） |
| 镜像工作区 | 批量模式 `<root>_log/` 下与原目录同构的 per-video 目录，子进程 `cwd` 指向它 |
| gpt_log 缓存 | `output/gpt_log/<log_title>.json`，按 **prompt 全文**精确命中，与模型无关 |
| 基础术语库 | `terms/*.json`，项目级、全局共享、手工维护的多套术语集合；每个任务按 `terminology.active` 勾选 0~N 套合并成「底板」，不随任务清空 |
| 任务术语表 | `output/log/terminology.json`，4.1 产出 = **勾选底板 + LLM 新抽**，每任务一份、随 `output/` 归档/force 清空；是 4.2 翻译与实验场**唯一**的术语来源 |
| 术语回写 | 在 `#/terms` 手动勾选任务术语表的词条，写进指定的 `terms/*.json`（可新建库）。只改基础库、不改任务术语表，因此不影响当前任务的翻译与断点，下次 4.1 生效 |
| 全部术语 | `#/terms` 的只读视图，两种互补形态：① **按来源完整清单** —— 每一套 `terms/*.json` 与 `output/log/terminology.json` 各自**逐条完整**列出，不跨库折叠；② **并集总表** —— 所有基础库（含未激活）+ 当前任务术语表按 `src` 大小写不敏感合并成一行，「生效」专指 4.2 会注入 prompt 的值（有 4.1 产物就用任务术语表，否则用当前勾选底板） |

---

## 2. 全局规则（G）

| 编号 | 规则 |
|---|---|
| **G-01 分层铁律** | 入口层（`server/*.py`、批量引擎）= 薄适配器，只做参数校验与 HTTP 包装；跨入口决策唯一归属 `core/services/`（`llm_service`：错误分类/失败策略/响应校验/探针；`run_service`：步骤范围/清理；`archive_service`：归档；`translate_service`：术语与分块/共享上下文）；`core/utils/` 只做数据与构造，不做行为决策。新增入口先 grep 同概念现有实现，有则 import，没有则写进 services 再调用，禁止第二份。守卫：`node check_single_source.js`。 |
| **G-02 注册表单一来源** | 步骤顺序、id、markers、阶段、LLM 标题全部以 `core/pipeline.py` 的 `STEPS` 为准。UI / API / 批量 / 文档都从它派生。 |
| **G-03 完成判定** | `is_done()` = 磁盘 marker 文件存在（`all`/`any`），不校验内容或时间。任何会被代码**物理删除**的文件（断点、缓存、tmp）**只能进 clean_extra**，进 markers 会让该步骤永远显示「未运行」。例外：`translate_ckpt.json` 收尾只清空 `chunks`、保留文件与 fingerprint，可作 marker；`asr_correct_ckpt.json` 是物理删除，绝不可以。 |
| **G-04 force 语义** | `force` / 批量 `reset_from..to` / `force_restart` 必须先删 `markers + clean_extra + output/gpt_log/<llm_title>.json`，否则 `@check_file_exists` 守卫会静默跳过。清理发生在任务线程之外，必须显式推一条可见日志；单任务先在 `TaskManager.start` 的同一锁内检查空闲，再执行输入预检和清理并启动，忙碌或输入无效不清理产物。 |
| **G-05 缓存与成本** | `ask_gpt` 缓存按 prompt（+resp_type+log_title）精确匹配、与模型无关 → 换模型/改配置不清缓存会静默重放旧答案。破缓存用显式 `<!-- attempt N/3 -->` 标记或 `bypass_cache=True`，禁止尾部空格 hack。所有 LLM 工程必须持久化 + checkpoint-resume。 |
| **G-06 断点** | 每完成一个单位立刻原子落盘（tmp + rename），不能循环结束才批量写；指纹变化自动作废旧断点（`_2_asr` 含 gap_fill 配置、`_4_2` 含 chunks 指纹、批量含 size+mtime）。 |
| **G-07 日志三出口** | ① 页面日志只捕获 Python 层 `print`（`sys.stdout` 被换）；② 子进程必须走 `core/utils/subprocess_log.py: run_logged` 转发（进度行节流 2s、末条必打、进度行不受 max_lines 限制）；③ 落盘 `output/log/pipeline_latest.log`（带时间前缀与步骤边界，归档最多留 10 份）。排查以文件为准。不要改 `gpt_sovits_tts.py` 的 `CREATE_NEW_CONSOLE`、`_11_merge_audio.py` 的静默调用、以及所有 `capture_output=True` 用于解析的调用。 |
| **G-08 配置** | `load_key` 每次现开 `config.yaml`（可用 `VL_CONFIG` 覆盖路径）→ 手改配置立即生效、无需重启。`update_key` 用 `yaml.dump` 整体重写会**抹掉全部注释** → 重要配置一律手改文件，不要通过设置页 PUT。设置页只暴露 25 个白名单键，密钥类键不下发。 |
| **G-09 互斥与错误码** | 单任务 `running/paused` 时：run / 下载 / 归档 / 重启 / 关服一律 409；批量 `running/stopping` 时 `/api/batch/start` 409。未知步骤 404，范围倒挂 400，越界读文件 403。 |
| **G-10 路径安全** | `/api/logs/tail` 只读 `output/`；`/api/batch/log` 只读当前扫描出的 `<root>_log`；`/api/batch/artifacts/*` 只读 `output/` + `history/` + 批量 log 根。实现为 `abspath` 字符串前缀匹配，`output_xxx` 这类兄弟目录名会被误判为合法（已知边界）。 |
| **G-11 输入唯一性** | `find_media_file()` 要求 `output/` 下媒体候选唯一；有歧义时下载接口 409、页面显示红 Tag。批量用 `output/input_manifest.json` 指定唯一源。手动放入子目录的文件必须移到 `output/` 或写 manifest。 |
| **G-12 音频输入** | 纯音频输入自动剔除 `needs_video` 的 `merge_sub` / `dub_to_vid`；步骤内部也会自行跳过并提示「字幕文件已在 output 目录」。结束步骤在音频模式下向前收敛到 `align`。 |
| **G-13 UI 通用规则** | 任务运行/暂停期间禁用输入与运行类按钮；破坏性操作一律 `Popconfirm` / `Modal.confirm`；错误统一取 FastAPI `detail`；WS 断线指数退避重连（1s→10s）；页面日志上限：流水线 1500 行、批量 800 行。 |
| **G-14 引用完整性** | endpoint 被模型引用、模型被链引用、激活中的术语库 → 删除返回 409 或禁用；**被任务绑定的链可以删除**（v1.19）：删除时把引用它的任务自动改绑到 `default` 链（若被删的正是默认链则取剩余第一条），确认框列出受影响任务；最后一条链禁止删（运行时要求至少一条链 + `default` 绑定）。链必须引用已存在模型且非空、任务必须绑定已存在链、必须存在 `default` 绑定；`retries_per_model ∈ [0,10]`。 |
| **G-15 实验场隔离** | `/api/translate/preview`、`/probe`、`/prompts/preview`、`/verify` 全部**不写流水线产物**；L3 实测结果只内联返回，调用仍记入 `calls.jsonl` 以便追溯。 |
| **G-16 后台线程** | 步骤里禁止 `exit()` / `sys.exit()`（`SystemExit` 会静默杀死任务线程并卡在 running）；要失败就抛普通异常。 |
| **G-17 出站 TLS 信任** | 所有 `urllib` 出站请求统一经 `core/utils/net_policy.py: ssl_context()`，CA = 系统存储 + certifi 追加 + 可选 `network.ca_bundle`；配置 `ca_bundle` 时 OpenAI SDK 路径复用同一 context（未配置则保持 httpx 自带 certifi）。**禁止用 `CERT_NONE` 关闭证书校验**（`/v1/models` 的 `Authorization` 头带 API Key）。证书错误不是代理问题，错误提示不得建议「切换代理开关」。 |

---

## 3. 功能需求 · 单任务工作台

### FR-01 输入源与视频下载

- **当前行为**：粘贴 URL（YouTube / Bilibili / X 等 yt-dlp 支持的站点）→ 选分辨率（1080p / 720p / 480p / 360p / 最佳画质）→「下载」启动异步 job；成功后页面显示输入文件名与类型（视频/音频）。也可手动把媒体文件放进 `output/`。「删除输入」二次确认后删 `input_manifest.json` 并（可选）删媒体文件。
- **入口/代码**：`frontend/src/pages/PipelinePage.tsx`；`frontend/src/api.ts: downloadVideo / getInput / clearInput / getCookieStatus`；`server/input_api.py`；`core/_1_ytdlp.py`；`core/utils/media_files.py`。
- **配置**：`youtube.cookies_path`、`youtube.cookies_from_browser`（保持空）、`youtube.js_runtime`（空 = `ejs:github`）、`ytb_resolution`。
- **约束**：URL 必须 http(s)，分辨率必须是 `360/480/720/1080/best`，否则 400；已有输入时 409；输入不唯一时页面红 Tag 提示（G-11）。下载不在 11 步内，走 `MANAGER.start_job()`。
- **验收**：下载完成 `output/` 下有唯一媒体 + `input_manifest.json`；日志同时出现在页面与 `pipeline_latest.log`。

### FR-02 输入草稿持久化

- **当前行为**：输入框里的地址与分辨率选择每次变更即写入浏览器 `localStorage`（key `videolingo.input.draft`），刷新 / 重开页面仍在；**下载成功后不清空地址**，方便改分辨率重下；点输入框的 × 清空即删除草稿。
- **入口/代码**：`frontend/src/inputDraft.ts`（`loadInputDraft` / `saveInputDraft`）；`PipelinePage.tsx` 懒加载 + `useEffect` 写回。
- **约束**：纯前端，不写后端、不存凭证；隐私模式 / 存储被禁 / JSON 损坏一律 try/catch 降级为「不记住」，绝不白屏。换浏览器或清站点数据会丢。
- **验收**：输入 → 刷新 → 地址与分辨率保持；点 × → 刷新 → 为空。

### FR-03 Cookie 体检

- **当前行为**：页面加载时查一次 `GET /api/input/cookies`，`verdict != ok` 时在输入源卡片下显示黄色告警（含 hint、cookies 路径是否存在、浏览器名）。
- **入口/代码**：`core/_1_ytdlp.py: probe_cookies()`（`cookies_have_login()` 判 `LOGIN_INFO`/`SAPISID`/`SID`/`__Secure-1PSID`）；`server/input_api.py`。
- **取值**：`ok` / `anonymous` / `browser-unavailable` / `unknown`。
- **约束**：Edge 127+ app-bound 加密导致 `Failed to decrypt with DPAPI`，普通权限无解 → `cookies_from_browser` 留空，只用手动导出的 `cookies.txt`；浏览器 cookie 读取失败（含 dpapi 字样）自动回退 cookie 文件重试一次。`remote_components` 合法值只有 `ejs:github` / `ejs:npm`，`ejs:deno` 非法；JS 运行时由 yt-dlp 自动探测，不要配。
- **验收**：匿名 cookie → 页面黄色提示「anonymous」，下载会报可读的 bot 挑战错误而不是堆栈。

### FR-04 步骤注册表与状态显示

- **当前行为**：页面按阶段一/阶段二两个卡片列出 11 个步骤，每步显示头像（运行中/已完成/未运行）与状态 Badge，数据来自 `GET /api/pipeline` 的 `steps[]`。
- **入口/代码**：`core/pipeline.py: STEPS / pipeline_status / is_done`；`PipelinePage.tsx` 的 `stages` 与 `List`。
- **约束**：完成态只由 marker 文件存在推导（G-03）；`audio_only` 时列表少 2 步；marker 是步骤成功后的**持久产物**。
- **验收**：删掉某步 marker → 页面变「未运行」；生成 marker → 变「已完成」，无需重启后端。

### FR-05 运行范围选择（开始 / 结束）与默认值

- **当前行为**：控制栏有「开始」「结束」两个下拉（选项 = 全部步骤，闭区间）。主按钮「从「X」跑到「Y」」按所选区间运行（不带 force，续跑可跳过已完成守卫）；「强制重跑」按同一区间清产物后重做。
- **默认值**：开始固定 `transcribe`（转录 whisperX）；结束按设置 `burn_subtitles` —— **开** → `merge_sub`（合并字幕到视频），**关** → `align`（对齐字幕 / 生成 srt）。默认值由后端 `GET /api/pipeline` 的 `defaults` 返回，用户在设置页改开关后刷新状态即生效；用户手动动过任一下拉后不再被默认值覆盖。
- **入口/代码**：`core/services/run_service.py: default_run_range / resolve_steps / clamp_end_index / burn_subtitles_enabled`；`server/app.py: RunRequest`；`frontend/src/pages/PipelinePage.tsx`；`frontend/src/api.ts: RunPipelineBody`。
- **约束**：闭区间；`step_id` 单步优先（忽略 from/to）；起点晚于终点 → 400；音频模式下终点只向前收敛，绝不越过用户所选；结束下拉里早于起点的选项禁用，改起点越界时自动把终点提到起点。
- **验收**：默认打开页面 = 「转录 whisperX」→「合并字幕到视频」；关掉烧录刷新 → 结束变「对齐字幕」；选 `asr_correct → translate` → 队列恰好 3 步。

### FR-06 单步运行与强制重跑

- **当前行为**：每步两个按钮 ——「强制重跑」（Popconfirm，只清这一步的产物/断点/LLM 缓存后重做，提示下游不会自动清）、「运行」/「重跑」（未完成 → 不带 force 续跑；已完成 → 带 force 重做）。顶部「强制重跑」覆盖所选区间全部步骤。
- **入口/代码**：`core/services/run_service.py: force_clean` → `core/utils/job_context.py: delete_step_artifacts`；`server/app.py`；`PipelinePage.tsx`。
- **约束**：见 G-04。`delete_step_artifacts` 只允许由 `run_service` 调用（守卫脚本检查）。
- **验收**：强制重跑第 4 步后 `translate_ckpt.json` 与 `gpt_log/translate_*.json` 被删，页面看到「🧹 强制重跑」日志，翻译真的重新调用。

### FR-07 完整跑阶段一与强制重跑一阶段

- **当前行为**：「阶段一 · 字幕」卡片标题内的「完整跑阶段一」固定从 `transcribe` 跑到**默认结束步骤**，不受顶部两个下拉的临时选择影响，不带 force（可续跑）。
- **强制重跑（v1.12 已实现）**：相邻新增红色「强制重跑一阶段」按钮，二次确认后对同一个 `stageRange` 提交 `force=true`。复用 `run_service.force_clean`，先清范围内产物、断点与 LLM 响应缓存，再依注册表顺序重新执行。确认框显示实际起终点、翻译重新调用与费用提示；保留输入媒体、阶段二和广告裁剪等范围外产物。
- **入口/代码**：`PipelinePage.tsx` 的 `stageRange` 与统一提交函数；复用 `runPipeline`、`POST /api/pipeline/run`、`TaskManager.start`、`run_service.force_clean`，不另设运行接口或清理实现。
- **约束**：烧录关闭或纯音频输入时停在 `align`，开启烧录的视频到 `merge_sub`；阶段按钮在输入缺失/歧义、范围未加载、任务运行/暂停或请求提交期间禁用。所有流水线运行入口共享提交锁，防止重复点击；后端在同一任务锁内检查空闲、执行强制请求的输入预检与清理、启动线程，忙碌或输入无效时不清理产物。
- **验收**：普通按钮保留断点并跳过已完成守卫；强制按钮只清并执行转录→默认终点，绝不进入配音；视频烧录开/关及纯音频三种范围正确；忙碌强制请求409、无输入/歧义400且清理未发生；取消确认不发送请求。

### FR-08 暂停 / 继续 / 停止与任务状态

- **当前行为**：`running` 显示「暂停」「停止」，`paused` 显示「继续」「停止」；状态机 `idle / running / paused / stopped / completed / error`，页面用 Tag 展示 state 与 current_step。
- **入口/代码**：`server/task_manager.py: start / start_job / pause / resume / stop`；`core/st_utils/task_runner.py: check_cancel`（协作式取消）；`core/utils/__init__.py: check_cancel()`。
- **约束**：`start()` 按注册表顺序重排队列；pause 只在 running 生效、resume 只在 paused 生效；stop 置标志后由 worker 收尾改 state；忙时新任务 409。
- **验收**：遇到协作检查点时暂停/停止生效，继续后从原处接着跑；停止后 state=stopped，可重新启动。没有内部检查点的切句、纠错、对齐和烧录可能要等当前步骤完成；网页刷新不停止后端线程。
- **阶段一实际恢复能力（v1.12 代码核查）**：转录按音频片段、纠错按 LLM 批次、翻译按文本块保存文件断点；总结仅响应缓存。切句没有逐句进度断点，保留已完成 NLP 文件、语义切句可复用相同请求的成功响应；对齐没有行级断点，普通运行也会重算并复用响应缓存；烧录没有编码断点，中断后重编。强制重跑清掉所选范围的这些断点/缓存。完成态仅说明 marker 存在，重做中间步骤不会自动让下游旧产物失效，需另选下游范围强制重跑。

### FR-09 实时日志与落盘日志

- **当前行为**：页面黑底日志区实时追加（上限 1500 行、自动滚到底）；标题显示行数与 `log_file` 路径；「下载完整日志」把 `output/log/pipeline_latest.log` 存成 `videolingo-<时间戳>.log`；「清空日志」只清页面显示。
- **入口/代码**：`server/logbus.py: LogBus`；`server/task_manager.py: _StreamCapture / open_run_log / _rotate_logs / _publish_log`；`server/app.py: GET /api/logs/tail`；`core/utils/subprocess_log.py: run_logged`。
- **约束**：G-07；`pipeline_latest.log` 正在被写，归档时只复制不移走；归档日志最多 10 份；`/api/logs/tail` 只允许读 `output/` 下文件，越界 403。
- **验收**：长 ffmpeg 转码后半段页面仍有进度行；下载的文件含 `HH:MM:SS` 前缀与步骤边界。

### FR-10 保存历史记录（归档）

- **当前行为**：控制栏「保存历史」二次确认 → 后台 job 把 `output/` 内容**移动**到 `history/<视频名>/`（成品、字幕、表格、`log/`、`gpt_log/`、`audio/`），然后清空 `output/` 的内容但**保留目录本身**；完成后弹「已归档 N 个文件 → dest」。同名视频重复保存直接覆盖，不生成 `xxx_2`。
- **入口/代码**：`core/services/archive_service.py: archive_current / _remove_with_retry / retry_locked`；`core/utils/onekeycleanup.py`（薄包装，同样只清内容）；`server/preview_stream.py: PreviewFileResponse / open_stream / close_preview_streams`（预览读句柄唯一登记与释放）；`server/history_api.py`；`server/ad_api.py`；`PipelinePage.tsx`。
- **约束**：文件名非法字符换 `_`；名称优先取输入媒体文件名，回退 `input_manifest.json`；`pipeline_latest.log` 只复制不移走（任务线程持有句柄）；任务忙时 409；WS `history_saved` 刷新输入区，残留后台清出后发 `history_cleaned`。
- **占用与释放（v1.14 → v1.20 重写）**：根因是旧 `FileResponse` 在 `<video>` 掐断 Range 请求时被 Starlette cancel 掉流式任务，`async with anyio.open_file()` 退出时的 `aclose()` 也是 `await`，在已取消的 scope 里会被立刻取消 → Windows 上留下**没有 `FILE_SHARE_DELETE` 的读句柄**（Python 默认打开方式不含它），归档 `shutil.move` 必然 `PermissionError`；v1.14 只靠「前端撤 src + 等 3s」治标，页面还会因 `history_saved` 立刻用旧 sources 重新挂载播放器把残留文件再锁一次，形成「刷新页面后再保存一次」的死循环。五层修复：① `PreviewFileResponse` 取代 `FileResponse`——同步 `finally: f.close()`（不 await，不受取消影响）、句柄登记到注册表、generation 拦住「open 与登记之间被释放」的竞态、被主动关闭后流安静收尾；② 前端 `releasePreview()` + `await releasePreviewStreams()`（`POST /api/ad/preview/release`）在**归档与任何运行提交（含 force 重跑清产物）之前**调用；③ 后端归档 job 开头 `close_preview_streams()`，force 重跑清理前同样调用；④ `archive_current(on_retry=...)` 删源重试 10×0.5s，每次失败**先主动关一次句柄**再等；⑤ 前端 `archiveHoldRef` / `previewHold`：归档结束等 `refreshAd()` 拿回新 sources 才恢复预览，locked 时保持断流；后端 `retry_locked` 在后台继续清出残留（只删归档时那份文件，按 size+mtime_ns 签名核对，绝不误删同名新产物；释放钩子只在最初 3 轮调用以免打断用户新开的预览），完成发 `history_cleaned`。仍失败**绝不静默**：文件先复制进归档、记入 `stats["locked"]`。
- **验收**：归档后 `output/` 目录仍存在且为空（除 `log/pipeline_latest.log`）；`history/<名>/` 内结构完整；预览流正在读取时直接归档也能一次清出，**无需刷新页面、无需再点一次保存**；源被占且重试不释放时 `locked` 上报且其余文件照常归档，后台按签名安全清出后才发 `history_cleaned(locked=0)`；同名文件被新任务重写时后台绝不误删；占用的文件**先复制后删源**成功时计入 `moved`。

### FR-27 B站投稿文案（标题 / 简介 / 标签三件套）

- **当前行为**：流水线页「B站投稿文案」卡片。填 EP 序号（默认 = 上次成功值 + 1，存 localStorage `videolingo.bili.ep`）+ 讲者名（自动建议、可改）→ 一次 LLM 调用产出 3 个标题候选 + 简介 + 10 个标签 → 落盘 `output/bili_meta.md`，页面可单选标题并复制。
- **素材主次（v1.3 改）**：**视频转录结果是决定性一手素材** —— 标题钩子、主题词、简介的痛点/✔清单/答案金句、L3/L4 标签都要能在转录里找到出处；`video_meta.json` 的 YouTube 原标题/简介**只作背景校准**（校准主题/人名，含营销话术，与转录冲突时以转录为准），标题/简介缺失不再阻断生成。
- **转录来源优先级**：`output/trans.srt`（**中文译文，用户确认的主素材**：已过 ASR 纠错 + LLM 翻译润色 + 分句对齐，中文可直接做钩子，信息密度最高）> `src.srt`（英文原文）> `log/split_by_meaning.txt` / `split_by_nlp.txt`（英文完整句）> `log/cleaned_chunks.json`（第 2 步词级转录）> `log/asr_checkpoint/seg_*.json`（转录未完成时的断点兜底）。**无可用转录 → 400 明确提示先跑第 6 步（或至少第 2 步），不再只靠元数据生成**；卡片在任务结束时自动重查、也可手动刷新。
- **长度策略**：中文译文未超 `bili.transcript_chars`（默认 12000）→ **全文直送**（每 50 行插一个 `[mm:ss]` 标记，不逐行加）；超预算才按时间**均匀采样** `bili.transcript_samples` 段（默认 8，头尾各 1.5 倍配额，每段带 `[mm:ss]` 与开头/结尾标注）。`log/summary.txt`（第 4.1 步全局摘要）始终附加，补足节选未覆盖的部分。预算调大即退化为「永远全文」。**不做 RAG/向量库**：单视频语料远小于上下文窗口，且「钩子/痛点」这类抽象 query 检索易漏金句、切片会丢铺垫。
- **讲者名**：`suggest_speaker()` 在标题/频道/简介 + 转录前 3000 字里匹配人名池（内置 skill 硬红线名单 + `bili.speaker_names` 追加项），认不出退回频道名；页面可改，改过不再被覆盖。
- **公式来源**：bili-video-meta skill（GracePower 361 条投稿实测）提炼为 prompt 模板 `bili_meta`（「术语 & 提示词」页可编辑），运行时不依赖 WorkBuddy/skill 文件。硬红线：标题四段全角 `｜`（`EP{序号}｜钩子｜主题词}｜{讲者}`）、40–70 字；简介七段式（痛点反问→反转句→✔清单→困惑共鸣→答案金句→评论区互动→共情收尾）、不空行、350–600 字；标签恰好 10 个（L1 固定池 3 + L2 轮换 2-3 + L3 主题词 2-3 + L4 长尾 1-2）、每个 ≤10 字。
- **入口/代码**：`core/services/bili_meta_service.py`（`load_transcript` / `_read_transcript_lines` / `full_text` / `sample_lines` / `load_video_context` / `suggest_speaker` / `_valid_bili_meta` / `generate_bili_meta`）；`server/bili_api.py`（薄适配，`GET /context` 只回转录统计、不回全文）；`core/prompts.py: get_bili_meta_prompt` + `DEFAULT_TEMPLATES["bili_meta"]`（一手素材段 + 取材铁律 5 条）；`core/_1_ytdlp.py: _save_video_meta`；`frontend/src/pages/PipelinePage.tsx`。
- **配置**：`bili.speaker_names`、`bili.transcript_chars`、`bili.transcript_samples`（均手改，`load_key` 立即生效）。
- **约束**：不进 `pipeline.py` 注册表（按钮触发，非流水线步骤，不占任务线程）；LLM 走任务链 `bili_meta`（未绑定 → default 兜底）；**自定义提示词模板若没写 `{{transcript}}` 占位符，`get_bili_meta_prompt` 会检测并强制追加一手素材节**，保证主素材不被模板绕过；改内置模板会使 `gpt_log/bili_meta.json` 缓存失效（G-05）；校验失败（EP 前缀/四段/10 标签/长度不符）由 `valid_def` 报给 ask_gpt 走链重试，最终失败 → 400 带 detail；`_save_video_meta` 失败绝不影响下载。
- **验收**：真实归档（33 分钟）读到 `trans.srt` 6301 字、`sampled=false`、prompt 9588 字符且含中文金句 + 取材铁律 + 全局摘要、一手素材排在次要参考之前；EP=42 生成后 3 个标题均以 `EP042｜` 开头且含 3 个全角 `｜`；标签恰好 10 个；无转录时页面禁用生成且接口 400 提示先跑转录。

### FR-28 广告位提醒（识别 → 视频预览微调 → 按此裁剪）

- **当前行为**：流水线页「广告位提醒」卡片（独立卡片，**不进 11 步注册表**）。一次 LLM 调用从转录里找出全部赞助中插 → 本地 ffmpeg 定准边界 → 产出 `output/ad_slots.md`（人读表格，含「转录开口 vs 建议起点」对照）+ `output/ad_segments.json`（EDL）。卡片内可播放视频预览、用**精细时间轴做帧级微调**、勾选是否裁剪、选裁剪源，确认后「按此裁剪」生成 `output/output_no_ads.mp4`。
- **要解决的问题**：转录给出的「广告开始」= 广告第一句话的**首词时间**，此刻已经进广告（前面有 2.5~3.1s 过场/静音/赞助卡），直接拿去裁剪会留广告头。用户要求起点改为「上一句正文结束再往后一点」，并**最终选定 A 方案：用 ffmpeg 画面切点把「一点」定死**（本地算、不花 API 钱）。
- **实测基线（2026-11-10，当前片 `Everything You Need to Know Before Your Next Relationship`，4 段赞助）**：

  | 品牌 | 上一正文词 end | 广告首词 start | 词级空隙 | 窗口内画面切点（相对 prev_end） | 建议起点 | 比转录提前 |
  |---|---|---|---|---|---|---|
  | Ritual | 13:07.225 | 13:09.787 | 2.562s | +0.31 / +0.44 / +1.40 | 13:08.321 | 1.47s |
  | Wayfair | 21:06.515 | 21:09.600 | 3.085s | +0.79 / +0.92 / +1.72 | 21:07.925 | 1.67s |
  | BetterHelp | 30:00.388 | 30:03.193 | 2.805s | +0.50 / +1.54 | 30:01.625 | 1.57s |
  | Aria | 38:44.069 | 38:46.574 | 2.505s | +0.25 / +1.25 | 38:45.023 | 1.55s |

  空隙稳定 **2.50~3.09s**，窗口内普遍有两个 `scene>0.08` 切点（+0.25~0.8s 离开正文镜头、+1.25~1.7s 上赞助卡）。→ 帧差数值即可定位，**不需要理解帧内容 / 不抽帧 / 不调 VLM**，4/4 命中。
- **识别与定点**：
  1. `trans.srt`（中文译文）> `src.srt` > `trans_src.srt`，解析成 1-based **行号**表；无带时间戳字幕 → 400 并禁用按钮。
  2. prompt `ad_detect` 送「`[行号] mm:ss 文本`」全文（当前片 786 行 / 10974 字 → prompt 22050 字符），LLM 只准回 **start_line / end_line + 品牌 + 说明 + 置信度**，不准编时间戳。模板可在「术语 & 提示词」页编辑；自定义模板缺 `{{transcript}}` 时强制追加转录节。任务链 `ad_detect`（未绑定 → default）。
  3. 行号映回 SRT 时间 → 候选窗口 `[上一句正文末词, 广告首词]`；`ffmpeg select='gt(scene,0.08)' + showinfo` 找窗口内切点，取**最后一个** − `ad.pre_roll_pad`（0.3s）为起点；终点取 `[广告末句末词, 下一句正文首词]` 内第一个切点 + `ad.post_roll_pad`。每条区间记录 `start_basis` / `end_basis`。
  4. 兜底链：无切点 → 最长静音段中点 → 「上一句正文末词 + pad」；`boundary_mode=prev_end` 可跳过 ffmpeg（不依赖视频文件），`silence` 用静音中点。**所有结果夹在「不切进正文、不晚于广告开口」的安全范围内**。
  5. 可选 `ad.sponsorblock=true` 查 `GET https://sponsor.ajay.app/api/skipSegments?videoID=<id>` 做交叉校验（免费、精确），**不作主路径**：本片实测 404 无覆盖，对照 `aircAruvnKk` 有数据。
- **预览微调与裁剪（用户 2026-11-10 确认的成片方案）**：
  - 裁剪源默认 **`output_sub.mp4`（字幕已烧进画面）**，可选配音版 / 原片，按固定 id 白名单解析（`GET /api/ad/preview` 用 `PreviewFileResponse` 支持 HTTP Range，播放器可拖动；读句柄由后端登记、归档时可主动关闭，见 FR-10）。**字幕在画面里 → 切完拼完直接成片，不需要平移 SRT 时间戳、不需要重烧字幕**。
  - **预览源选择（v1.17）**：`GET /api/ad/context` 的 `sources[]` 带 `subtitled`（烧录版 / 配音版 = true，原片 = false）和 `version`（文件修改时间纳秒 + 大小）；`default_source` 由 `ad_cut_service.default_source` 从同一次源列表快照决定：优先 `output_sub.mp4`，否则第一个可用源。未手动选择时，烧录版生成后自动从原片切到烧录版；用户明确选过且仍可用的源始终保留，换输入 / 归档后重置选择。选源下拉**常驻在播放器上方**（不依赖切点），标出画面是否带字幕，并提供「切到带字幕版」。手动 / 自动换源和同路径重烧均恢复播放位置，按文件 `version` 更新预览 URL 与媒体信息，避免旧缓冲。
  - **显示 / 隐藏（v1.17，用户追加）**：选源旁常驻「显示预览」开关；关闭时 `pause + removeAttribute('src') + load()` 中止请求并卸载播放器 / 精细时间轴，释放文件占用，再显示恢复之前位置。开关在当前浏览器持久化；后台刷新、切源、任务完成均不得擅自打开用户隐藏的预览。归档 / 运行提交的临时断流与用户开关分开，任务结束后可恢复，运行 / 归档期间禁止重新加载以免抢占待写文件。隐藏不删除切点，仍可手填秒数、保存和按已选源裁剪；预览源与实际裁剪源保持同一个选择并在确认框写明文件。
  - 成片 = EDL 广告区间的**补集**：保留 `[0, A1)` / `[A1.end, A2)` / … 再 concat。页面上不勾选的段不参与裁剪。
  - 单条 `filter_complex`：`trim/atrim + setpts + concat`，**帧级精确**，避开 `-c copy` 吸附关键帧会偏 1~2 秒的坑（实测 2 段各 3s → 成片 6.006s）；每段音频 30ms 淡入淡出防爆音；重编码 `libx264 veryfast/crf20`。原视频不动，只生成新文件，可撤销。
  - 「按此裁剪」先把页面当前区间回写 EDL，保证产物与所见一致；裁剪是同步 HTTP 请求（长视频会较久），前端按钮显示 loading。
  - **断连噪音（v1.9）**：预览用 HTTP Range，浏览器拖进度条 / 换裁剪源 / 关页面会取消上一次请求；Windows 的 Proactor 事件循环在 `_call_connection_lost` 回调里对已关闭 socket 调 `shutdown()` 抛 `ConnectionResetError [WinError 10054]`，asyncio 把回调异常打到 cmd，看着像崩溃，其实那次请求早已结束、不影响预览与裁剪。`server/app.py: install_disconnect_noise_filter` 在 startup 给 loop 装异常处理器，**只吞这一类**（`_call_connection_lost` + 连接重置类异常），其他异常仍交原 handler；重复安装幂等。
- **精细时间轴（用户 2026-11-10 反馈「原生进度条一点跳好几秒，要剪映那种帧级切换」）**：
  - **问题定量**：原生 `<video>` 进度条铺的是整部片（47 分钟压在几百像素 ≈ 一点跳 5 秒），无法把裁剪点定到帧。
  - **局部窗口**：播放器下方另加 `FrameScrubber`，只画边界附近 `±1s / ±3s（默认）/ ±10s`，1 像素 ≈ 10ms；点「跳到」把窗口中心搬到该边界并让方向键立即生效；用原生进度条跳到远处时窗口自动跟随（播放头越窗 92% 才滚动，拖动微调时窗口保持不动）。
  - **帧级步进**：`−1帧 / +1帧`（步长 `1/fps`）、`±0.1s`、`±1s` 按钮；方向键 `←/→` 一帧、`Shift` 一秒、`Alt` 0.1 秒、空格播放/暂停；按钮点完自动把焦点还给时间轴，可连续逐帧。步进基准从 `video.currentTime` 读，避免连按累积漂移；元数据未加载完时把 seek 排队到 `loadedmetadata`。
  - **帧网格吸附**（默认开）：`snapFrame(t) = round(t × fps) / fps`，seek 与「设为起点/终点」写回 EDL 都落到整帧，保证页面秒数与 ffmpeg `trim` 的真实帧边界一致。可关，可手选 fps。
  - **帧率 / 时长来源**：`GET /api/ad/media-info?source=`（`/frame-rate` 为同函数别名）→ `core/utils/media_probe.py` 一次 ffprobe 读全，`avg_frame_rate` 的 `0/0` 回退 `r_frame_rate`，失败返回 `0` 由前端回退 30 并在页面标「（默认）」橙字提示。实测当前片 `output_sub.mp4` 为 **23.976 fps**（一帧 41.7ms），所以不能硬编码 30。页面标注实测 / 手选 / 默认三种来源，**手选后不再被自动值覆盖**；时长供 FR-30「从当前切到片尾」使用。
  - **轴上标记**：当前段的「上一句正文末词」（绿）、`scene_cuts` 画面切点（紫）、「广告开口」（红）、「裁剪起点/终点」（橙粗线）、「下一句正文」（灰）；只画窗口内的，图例也只列窗口内真实存在的标记。未选边界时显示「先点表格『跳到』」。
  - 原生 `controls` 保留（音量 / 全屏），但精调一律走局部时间轴。
- **入口/代码**：`core/services/ad_service.py`（`parse_srt` / `read_transcript` / `numbered_transcript` / `normalize_ads` / `validate_ads` / `find_scene_cuts` / `find_silences` / `refine_segments` / `sponsorblock_segments` / `render_md` / `save_edl` / `load_edl` / `update_edl_segments` / `context` / `detect_ads`）；`core/services/ad_cut_service.py`（`list_sources` / `default_source` / `media_info` / `resolve_source` / `probe` / `probe_frame_rate` / `build_keep` / `build_command` / `cut_video`）；`server/ad_api.py`（薄适配，含 `GET /frame-rate`）；`core/prompts.py: get_ad_detect_prompt` + `DEFAULT_TEMPLATES["ad_detect"]`；`PipelinePage.tsx`（`FrameScrubber` / `snapFrame` / `seekExact` / `focusBoundary` / `nudge` / `setFromPlayer` / `refreshAd` / `pickAdSource` / `changeAdSource` / `toggleAdPreview` / `releasePreview` / `resetAdTask`）；`api.ts`（`getAdFrameRate` / `getAdMediaInfo` / `adPreviewUrl`，预览与媒体信息均带 `v=<version>` 防缓存）。
- **配置**：`ad.boundary_mode`（`scene_cut` 默认 / `prev_end` / `silence`）、`ad.pre_roll_pad`、`ad.post_roll_pad`、`ad.scene_threshold`、`ad.min_gap_seconds`、`ad.sponsorblock`、`ad.transcript_chars`、`ad.cut_preset`、`ad.cut_crf`（均手改）。
- **约束**：G-01（决策只在 services）、G-05（`ask_gpt` prompt 精确缓存 + `log_title=ad_detect`，重复点击不二次付费）、G-06（EDL 原子落盘 tmp+replace）、G-07（ffmpeg 切点/静音/帧率探测用 `capture_output` **解析**输出，裁剪转码走 `run_logged`）；G-09（预览**与帧率接口**都按 id 白名单，不接受路径）；不改动 11 步注册表。
- **验收**：`428 passed, 1 skipped`（v1.6 当时基线；帧率解析用例在 v1.7 迁入 `test_encode_service.py`）；真实 `output_sub.mp4` 实测 **23.976 fps**；真实转录 + 真实 ffmpeg 冒烟 4/4 命中画面切点（起点比转录提前 1.47~1.67s）；20s 样片端到端裁剪 20s → 15.015s（去掉 5s）；`check_single_source.js` clean；tsc + vite build 通过；无头浏览器截图确认精细时间轴布局无重叠/截断。**v1.17 追加**：`554 passed, 1 skipped`（新增 6 例）；真实 `output_sub.mp4` 与原片各抽 300s 一帧对比，确认仅烧录版画面带双语字幕；`GET /api/ad/context` 返回 `default_source=burned` 与 `subtitled`，`GET /api/ad/preview?source=burned` 与 `original` 的 Range 响应大小不同（确实是两个文件）；选源下拉常驻、显示/隐藏开关与持久化、切源保留播放头、隐藏下仍可裁剪为代码走查 + tsc/vite build 验证，本轮未跑真实浏览器交互与 GPU 流水线。

### FR-29 烧录 / 合成 / 裁剪的输出编码参数

- **当前行为（v1.18 起，默认恒定质量）**：步骤 6 `merge_sub`（用户口语「压制字幕到视频」）、步骤 11 `dub_to_vid`、FR-28 卡片「按此裁剪」三处重编码，**默认走 NVENC 恒定质量 `CQ32`**（`medium` 预设 + 空间/时间 AQ + lookahead32 + multipass），**不再用 `-b:v/-maxrate/-bufsize` 锁死峰值码率**；帧率、像素格式、音频码率仍对齐源片。**「按此裁剪」因为是对 `output_sub.mp4` 的第二次有损转码，单独放宽到 `CQ30`**，且参数探测优先读原片而非已压产物。旧「对齐源码率」模式保留为 `encode.rate_control: bitrate`（含 `match_source=false` 的完全旧行为），一键可回退。
- **为什么同码率不等于同画质（2026-11-11 实测，用户反馈「很多地方变虚、镜头切换虚一段时间」）**：
  1. 原片是 **AV1 353 kbps 的 VBR**：简单画面 271k、复杂画面 1089k、峰值 1336k（**3.78× 均值**），18.8% 的秒数超过均值 2 倍；关键帧平均 5.34s 且**跟着场景切换走**（42 种间隔）。`-b:v 353k -maxrate 353k -bufsize 706k` 把瞬时码率也锁在 353k，等于把复杂镜头预算砍掉 —— 实测最复杂的 15 秒原片均值 1150k → 产物仅 460k（削 42~80%），全片 **672/1682 秒（40%）贴着上限跑**。
  2. **NVENC 默认固定 GOP 250 帧（10.43s）且不做场景切换检测**：镜头一换没有可参考帧，必须靠新 IDR 重编整幅画面，但码率被锁死、下一个 IDR 最长还要等 10.4s → 切换后一整段用糊掉的参考帧预测（`output_sub` / `output_no_ads` 关键帧间隔都只有 1 种，切点附近 PSNR 从 41.6 掉到 36~39 dB）。
  3. **AV1 → H264 效率差 30~50%**（RTX 3060 不支持 AV1 编码、软编 47 分钟不可接受），同码率 H264 天然更糊。
  4. **去广告成片是第二次有损转码**：裁剪源是已压过的 `output_sub.mp4`，旧逻辑还探测它把目标码率定成 324k（比原片 353k 更低）再编一次 → 峰值 737k、p90 413k、超均值 2× 的秒数只剩 **0.4%**（原片 18.8%）。
  - **结论**：瓶颈不是"码率数字"，是**码率分配方式**（削峰）与**编码次数**。缩短 GOP / 强制切点 IDR 在固定码率下反而更差（38.34 dB < 当前 40.05 dB，I 帧增多挤占预算），恒定质量才是正解。
- **30s 样片实测（crop 掉字幕区，对比原片 PSNR / 体积）**：当前参数 40.05 dB / 1.39 MB；只去掉 maxrate 40.99 dB / 2.12 MB；码率 1.5× + AQ 44.43 dB / 2.30 MB；**CQ32 46.44 dB / 2.55 MB（+6.4 dB，约 1.8×）**；CQ30 48.03 dB / 3.28 MB；x264 软编 crf23 48.01 dB / 2.52 MB（自带场景切换检测，但 47 分钟片软编明显变慢，未采用）；CQ32 + 短 GOP48 47.29 dB / 3.36 MB、+ 强制切点 IDR 47.30 dB / 2.79 MB（仅多 0.85 dB 却涨 10~32% 体积，不做默认）。用户拍板：**恒定质量 CQ32 + 去广告链路单独放宽**。
- **实测基线（2026-11-10，当前片）**：

  | 参数 | 原片 | 旧 `output_sub.mp4` | 倍数 |
  |---|---|---|---|
  | 视频编码 / 码率 | AV1 / 408326 bps | H264 / 2044485 bps | **5.0×** |
  | 音频 | opus 109016 bps | aac 127357 bps | 变了 |
  | 总比特率 | 521646 bps | 2177382 bps | **4.2×** |
  | 帧率 | 24000/1001 | 24000/1001 | 一致 |
  | 体积 | 180 MB | 748 MB | **4.2×** |

- **根因**：`_7_sub_into_vid.py` 的 ffmpeg 命令**只有滤镜，没有任何编码参数**；`ffmpeg_gpu: true` 时只加了 `-c:v h264_nvenc`，其余全交给 NVENC 默认（≈2 Mbps）。`_12_dub_to_vid.py` 同理，且音频写死 `aac 96k`。
- **实现**：
  1. `core/utils/media_probe.py: probe_media` 一次 ffprobe 读全（视频流缺 `bit_rate` 时按「总码率 − 音频码率」→「size/duration − 音频码率」估算）；`core/services/encode_service.py: build_encode_args` 是**唯一决策点**，第 6/11 步只拼参数（G-01）。`ad_cut_service.probe_frame_rate` 改为薄转发到 `probe_media` —— 帧率解析只允许一份。
  2. **v1.18 恒定质量（默认）**：`rate_control=cq` 时给 NVENC `-rc vbr -cq <level> -b:v 0 -preset medium -spatial_aq 1 -temporal_aq 1 -rc-lookahead 32 -multipass fullres`，**不传 `-maxrate` / `-bufsize`**，让复杂镜头自己拿码率；软编 libx264 时映射成 `-crf <level 换算>`。`quality_profile="cut"` 用 `encode.cut_cq_level`（默认 30，比默认 32 高 2 档）抵消二次转码损失。`encode.cq_gop` 可选显式 `-g`（默认不传，交给编码器；实测短 GOP 收益很小、体积涨得多）。**帧率 / `-pix_fmt` / 音频策略两种模式共用**：显式 `-r` 或 `fps_mode=passthrough`，音频优先 `-c:a copy` 直通，需重编码混音时按原音频码率给 `-b:a`。
  2b. **旧模式 `rate_control=bitrate`**：保留 v1.7 行为 —— 视频 `-b:v / -maxrate / -bufsize` 按原视频流码率 × `bitrate_scale`（`-maxrate` 等于目标码率是削峰根因，仅作兼容回退）。`match_source=false` 仍是完全旧行为（只给编码器、不给任何码率参数）。
  3. 原片是 AV1 但 **RTX 3060 硬件不支持 AV1 编码**（`av1_nvenc` 编译可用但设备不支持），软件 AV1 编 47 分钟不可接受 → 输出仍走 H264/NVENC，只把码率对齐；同码率下 H264 画质会低于 AV1，因此 CQ 模式用 `encode.cq_level` 权衡画质/体积（越小越清晰），旧 `bitrate` 模式仍用 `bitrate_scale`。
  4. 第 6 步显式 `-map 0:v:0 -map 0:a:0?`；第 11 步 `audio_reencode=True`（`amix` 产出的新音轨不能 copy → `aac` 按原音频码率 109k，不再写死 96k）。
  5. **「按此裁剪」也走同一决策点（v1.8 修复）**：`ad_cut_service.build_command` 原先写死 `-c:v libx264 -preset veryfast -crf 20 -c:a aac -b:a 192k`，是第二份编码决策（违反 G-01）—— 用户实跑发现 `output_sub.mp4` 已压到 390 kbps，裁完成片又被 CRF20 重新撑大。现在 `cut_video → _encode_args_for()` 调 `build_encode_args(src, audio_reencode=True, quality_profile="cut")`（`-map [outa]` 是滤镜输出、**不能 copy**），把编码参数注入 `build_command`；v1.18 起默认注入的是 CQ 参数而非码率参数，且 `cut` profile 单独放宽 2 档、优先探测原片（抵消二次转码损失，见 FR-29）。只有 `encode.match_source=false` 或源片参数探测失败才退回 `ad.cut_preset / cut_crf` 旧命令。摘要日志打印**实际编码方案**（如「视频 h264_nvenc · 预设 fast（由 veryfast 映射）· 码率 390k · 帧率 24000/1001 · 音频 aac 109k」），避免再靠猜。
  6. **速度预设按编码器归一**：x264 的 `veryfast` 不是 NVENC 的合法 `-preset`（NVENC 只有 `p1..p7 / fast / medium / slow` 等），直接透传会让 ffmpeg 报参数错。`build_encode_args(preset=...)` 内部映射：libx264 原值、NVENC `veryfast/ultrafast/faster/fast → fast`、`medium → medium`、非法值忽略；该映射只在旧 `bitrate` 模式生效；CQ 模式统一用 `encode.cq_preset`，入口传入的 `preset` 不生效。
- **配置**（均手改 G-08）：`encode.rate_control`（`cq` 默认 | `bitrate`）、`cq_level`（32）、`cut_cq_level`（30）、`cq_preset`（`medium`）、`cq_gop`（0 = 不传）；旧模式沿用 `match_source`（true）/ `bitrate_scale`（1.0）/ `min_video_bitrate`（200）；两种模式共用 `fps_mode`（`cfr` | `passthrough`）/ `copy_audio`（true）。适用范围 = 第 6 步 / 第 11 步 / 「按此裁剪」三处；`ad.cut_preset / cut_crf` 降级为完全旧行为专用。
- **入口/代码**：`core/utils/media_probe.py`（`probe_media` / `_fraction` / `empty_info`）；`core/services/encode_service.py`（`load_config` / `_encoder_for` / `build_encode_args(quality_profile=...)` / `_cq_args` + `_NVENC_PRESETS`，**CQ 与码率两套决策都只在这里**）；`core/_7_sub_into_vid.py` / `core/_12_dub_to_vid.py`（薄适配）；`core/services/ad_cut_service.py`（`_encode_args_for` 传 `quality_profile="cut"` 且优先探测原片 / `build_command(encode_args=...)`）。
- **约束**：G-01（CQ / 码率两套编码参数决策只在 `encode_service.py`，`check_single_source.js` 新增 `cq-encode-decisions` 守卫禁止第二份 `-cq` / `-spatial_aq` 等字面量）、G-07（转码走 `run_logged`、ffprobe 走 `capture_output` 解析）、G-08（新配置手改文件，别用设置页 PUT 抹注释）；不改 11 步注册表与产物文件名；**旧 `bitrate` 模式参数逐字不变**，保证回退可用。
- **成本提示（G-05）**：换编码参数会让产物变化，但**不影响 GPT 缓存**（缓存按 prompt 全文，与视频编码无关）；代价是重编码时间与体积（CQ32 约 1.8×，裁剪链路更高）。改前先评估是否要重跑第 6/11 步。
- **验收**：**真实双字幕滤镜 20s 端到端（v1.7/v1.8 旧 `bitrate` 模式实测，CQ 模式下体积/码率不再适用）**：视频 394152 bps（目标 408326，−3.5%）、音频 opus 直通 129765 bps、总 530335 bps（原 521646，+1.7%）、帧率 `24000/1001` 与 1920×1080 一致，抽帧确认中英双语字幕均烧上；外推 47 分钟 ≈183 MB（原 180 MB，旧版 748 MB）。**真实裁剪端到端（20s 样片，去掉 4s）**：源 `output_sub.mp4` 505 kbps → 成片 547 kbps（+8.3%）、视频 439185 bps（目标 407k）、音频 aac 104536 bps（目标 105k）、帧率一致、耗时 3s；旧 CRF20 路径会到数 Mbps。`466 passed, 1 skipped`；`check_single_source.js` clean；tsc + vite build 通过；`match_source=false` 与 preset 映射均有单测保证。**v1.18 追加**：真实三文件逐包实测（见上）、30s 样片 8 组编码对照（CQ32 +6.4 dB / 1.8× 体积、短 GOP 反而更差）；CQ 参数注入 / cut profile 放宽 2 档 / 探测原片 / 非法配置回退 / 旧 bitrate 逐字不变均有单测；`check_single_source.js` clean（含 `cq-encode-decisions` 新规则）；全量 pytest 与 py_compile 通过。未跑全片重编码与真实 GPU 长时间任务（重编码成本由用户在实跑时承担）。

### FR-30 自定义切点（片头 / 片尾 / 中间任意段）

- **当前行为**：用户不跑 LLM 识别，也能手动指定要切掉的区间 —— 片头 `[0,X]`、片尾 `[X,时长]`、中间 `[A,B]`；与广告段共用同一个 EDL 和**一次取补集裁剪**，产物仍是 `output_no_ads.mp4`。卡片更名「广告位 / 自定义切点」。
- **实现**：
  1. 复用 FR-28 的 EDL + `build_keep`（任意区间求补集），不新建裁剪引擎。手动段带 `confidence=manual` / `start_basis=manual` 标记，其余字段补齐避免下游 KeyError。
  2. `update_edl_segments` 在 EDL 不存在时**自动创建骨架**（旧行为 400「请先点识别广告位」会挡住纯手动场景）；`detect_ads` 重新识别时**保留已有手动段**并统一重编号；`render_md` 标题区分「N 段赞助 + N 段手动切点」、手动行显示「手动指定 | —」、全是手动段时不渲染边界对照表；`cut_video` 日志改为「裁剪区间 / 裁剪成片」。
  3. 页面新增「+ 添加切点」（当前播放头起 +30s）、「切掉片头到当前」`[0, playhead]`、「从当前切到片尾」`[playhead, duration]`；时长与 fps 来自 `GET /api/ad/media-info`。**所有段（识别 + 手动，见 FR-32）都可删除（Popconfirm）**、可改时间、可勾选，表格标「手动」Tag、不显示「比转录早」；精细时间轴对手动段不画「广告开口」红线。
  4. **播放器和精细时间轴不再依赖「已有区间」才渲染** —— 否则加第一个切点前无法定位播放头（鸡生蛋问题）。没转录时「识别广告位」仍禁用，但手动切点、预览、保存、裁剪全部可用（G-12 音频输入除外）。
- **入口/代码**：`core/services/ad_service.py`（`_is_manual` / `render_md` / `update_edl_segments` / `detect_ads`）；`core/services/ad_cut_service.py`（`media_info` / `probe_frame_rate` 薄转发 / `cut_video` 文案）；`server/ad_api.py`（`GET /media-info`，`/frame-rate` 为同函数别名）；`frontend/src/api.ts: AdMediaInfo / getAdMediaInfo`；`PipelinePage.tsx`（`makeManualSeg` / `addManualSeg` / `removeSeg` + 按钮行 + 表格条件渲染）。
- **配置**：无新增，复用 `ad.*`。
- **约束**：G-01（EDL 决策只在 `ad_service`）、G-06（原子落盘）、G-09（media-info 也按 id 白名单）；不改动 11 步注册表；`detect_ads` 不得吞掉手动段。
- **验收**：无 EDL 工作区加 3 段手动切点（0–10 / 100–120 / 280–300）→ `build_keep` = `[(10,100),(120,280)]`、保留 250s；再跑 `detect_ads` 后手动段仍在且 id 重编号为 `[0,1]`；MD 显示「1 段赞助中插 + 1 段手动切点」且无 `nan` / KeyError；无转录时页面手动链路可用；`447 passed, 1 skipped`；无头浏览器截图确认按钮行布局无重叠。

---

### FR-31 全局日志出口统一（卡片类同步操作也要上页面）

- **要解决的问题**：用户 2026-11-10 实跑「按此裁剪」时发现 `✂️ 裁剪…`、ffmpeg 的 `Input #0 / Stream mapping / libx264 …` 全部只出现在 cmd 窗口，页面「实时日志」卡片一片空白。根因不是 `run_logged` 失效，而是 `TaskManager._run()` 只在**流水线任务线程**里把 `sys.stdout` 换成 `_StreamCapture` → `BUS.publish`；裁剪 / 识别广告位 / B站文案这类**同步 HTTP 卡片操作**跑在 uvicorn 线程池里，任务线程没在跑时 `sys.stdout` 是原始的，`print` / `rprint` 直接落 cmd。更隐蔽的是：碰巧任务线程正在跑时，`sys.stdout` 是进程全局的，卡片日志反而会混进任务日志 —— 这就是「一会儿 cmd 一会儿页面」的真正成因。
- **当前行为**：**任意线程**产生的 core 层日志（`rprint`、`run_logged` 回灌的子进程输出）都同时进 cmd 与页面实时日志、并落盘到 `output/log/pipeline_latest.log`；任务线程不重复；core 不反向依赖 server（G-01）；不改 11 步注册表、不改前端日志订阅协议。页面日志卡片空态文案已改为「流水线步骤、广告位识别、视频裁剪、文案生成的日志都会实时出现在这里」。
- **实现**：
  1. 新增 `core/utils/log_hooks.py`：`register(sink)` / `unregister` / `emit(line)` / `plain(text)` / `rprint`；`emit` 在 `sys.stdout` 带 `_vl_capture` 标记时**直接跳过**（此时 `print` 已经由 `_StreamCapture` 送进 BUS + 落盘，不能再发一次）。`plain()` 用 rich markup 解析去 `[cyan]` 之类样式标签，fallback 只删已知样式名，不误吃 `[1/3]` / `[LLM]`。
  2. `subprocess_log._emit` 与 `log_hooks.rprint` 各调一次 `emit`（只上送字符串参数、全程 try/except）；任务线程里自动跳过，HTTP 线程里补上 BUS 这一路。`rprint` 放在 `log_hooks` 而不是 `core/utils/__init__.py`，是为了让后者先导入的 `ask_gpt` / `decorator` 也能安全引用，不产生循环 import。
  3. **13 处 `from rich import print as rprint` 全部收敛**到 `from core.utils.log_hooks import rprint`（含 `ask_gpt` 两处、ASR / TTS / 翻译等模块）—— 否则 `ask_gpt` 的 LLM 请求日志在识别广告位 / B站文案这类同步请求里仍只落 cmd。
  4. `server/logbus.py` 增加 `append_log_file(line)`（共享锁、随写随关的 append）与 `publish_external(line)`（BUS.publish + 落盘）；`TaskManager` 落盘改为调用同一个 `append_log_file`，`open_run_log()` 只负责归档 + 清空、**返回 None**，不再长期持有 `'w'` 句柄 —— 消除「任务线程与外部操作写同一文件互相截断」的隐患。路径常量动态取 `logbus.LOG_DIR`，测试只需 patch 一处。
  5. `server/app.py` startup 注册 `publish_external` 为唯一 sink（幂等）；`_StreamCapture` 加 `_vl_capture = True` 防重标记。`test_pipeline_run.py` 的 4 个落盘用例随新机制改写（不再断言文件句柄）。
- **入口/代码**：`core/utils/log_hooks.py`（新）、`core/utils/__init__.py`、`core/utils/subprocess_log.py`、`server/logbus.py`、`server/task_manager.py`、`server/app.py`；`test/test_log_hooks.py`（新，11 例）；`test/conftest.py` 新增 autouse `isolated_log_dir`（把 `logbus.LOG_DIR` 指到 tmp_path，防止 startup 注册的 sink 把测试输出写进真实 `output/log`）。
- **约束**：G-01（日志路由决策只在 `core/utils/log_hooks.py` + `server/logbus.py`，入口只注册）、G-07（子进程仍走 `run_logged`）；日志失败绝不能拖垮业务（所有 sink 调用 try/except）。
- **验收**：**真实 ffmpeg 20s 样片 + `POST /api/ad/cut`**：HTTP 200、`BUS.snapshot()` 收到 44 条日志（含「✂️ 裁剪…」「编码参数：视频 h264_nvenc · 码率 407k…」与 `Input #0 / Stream mapping / frame=` 等回灌行）、`pipeline_latest.log` 同步 44 行；任务线程捕获器下同一行只发 1 条（防重单测）。`466 passed, 1 skipped`（新增 `test_log_hooks.py` 11 例 + 编码参数/preset 8 例）；重跑全量 pytest 前后真实 `pipeline_latest.log` 字节数不变（isolated_log_dir 生效）；`check_single_source.js` clean；tsc + vite build 通过。

---

### FR-32 识别出的广告段也能手动删除（删除 / 清空 / 恢复）

- **当前行为**：流水线页「广告位 / 自定义切点」表格的**每一行**（LLM 识别的赞助段 + 手动切点）都有删除按钮，删除需二次确认。删到一段不剩也能保存（按钮显示「保存（清空）」），EDL 变空列表、`ad_slots.md` 显示「待裁剪区间（暂无）」、裁剪按钮自动隐藏。
- **删除不等于「取消勾选」**：被删掉的**自动识别段**写进 `ad_segments.json` 的 `dismissed` 字段（完整段数据）。以后重新点「识别广告位」时，LLM 结果里与之区间重合 ≥60% 且品牌一致的段被跳过（日志提示「跳过 N 段已手动删除的识别结果」），不会一次次复活；页面出现「恢复已删除的 N 段」，一键加回列表并立即保存、清掉 `dismissed`。手动切点删除不留档。
- **实现**：
  1. `ad_service._slot_overlap` / `is_dismissed` / `_merge_dismissed`：按区间重合比例认「同一段广告」（用户可能先微调起止秒再删，不能用精确时间或 id 比），品牌为空时只按区间匹配。
  2. `update_edl_segments(workspace, segments, dismissed=None)`：`segments=[]` 合法；传了非空却全是非法区间仍 400（防坏请求静默清空）。`dismissed=None` 时按「哪些自动段从列表里消失了」增量记录，取消勾选 / 改时间不算删除；显式传 `[]` = 恢复。`render_md(..., dismissed_n=)` 在 MD 里写明已删除段数。
  3. `detect_ads`：过滤 `dismissed` 后再与手动段合并重编号，返回值与 EDL 都带 `dismissed`。
  4. 前端 `adDirtyRef`：任何编辑标脏，脏时 `refreshAd` 不得用服务端旧 EDL 覆盖 —— 否则「删掉最后一段」会被下一次刷新立刻读回来。「按此裁剪」先回写 EDL，成功后清脏。
- **入口/代码**：`core/services/ad_service.py`（`_slot_overlap` / `is_dismissed` / `_merge_dismissed` / `render_md` / `update_edl_segments` / `detect_ads`）；`server/ad_api.py`（`SegmentsRequest.dismissed`）；`frontend/src/api.ts`（`AdEdl.dismissed` / `AdDetectResult.dismissed` / `saveAdSegments(segments, dismissed?)`）；`PipelinePage.tsx`（`adDismissed` / `adDirtyRef` / `removeSeg` / `restoreDismissed` / 删除按钮对所有行 / 保存按钮允许空列表）；`test/test_ad_slots.py`（7 例）；`server/app.py: install_disconnect_noise_filter` + `test/test_server_disconnect.py`（5 例，见 FR-28 断连噪音）。
- **约束**：G-01（删除判定只在 `ad_service`）、G-06（EDL 原子落盘）；`cut_video` 对空区间仍报「没有待裁剪区间，无需裁剪」，不允许对空 EDL 白跑一次 ffmpeg；不改动 11 步注册表。
- **验收**：`478 passed, 1 skipped`（新增 12 例）；删 2 段识别段 → 空 EDL + `dismissed` 2 条 + MD「暂无」；非法区间仍 400 且不清空；取消勾选 / 微调时间不进 `dismissed`；重新识别跳过被删段、保留手动段，点恢复后又出现；`PUT /api/ad/edl` 空列表 200、`dismissed=[]` 覆盖生效；`check_single_source.js` clean；tsc + vite build 通过。

---

## 4. 功能需求 · 配置与管理

### FR-11 设置页

- **当前行为**：5 组共 **25 个白名单键**，按类型渲染 `Select` / `InputNumber` / `Switch` / `Input`；修改先进本地脏数据，「保存 (n)」批量 PUT，「重载」丢弃修改。保存结果区分 `applied[]` / `rejected[]`。
- **入口/代码**：`frontend/src/pages/SettingsPage.tsx`；`server/settings_api.py: SCHEMA / GROUPS / SECRET_KEYS`；`core/utils/config_utils.py: load_key / update_key`。
- **分组**：通用（3）、网络代理（3）、转录 ASR（4）、字幕与切句（11）、配音 TTS（4）。
- **约束**：未知/只读键 400；密钥类键不下发；`update_key` 整体重写会抹注释（G-08）；「修改立即生效于下一次任务，不影响进行中的任务」。
- **验收**：改 `burn_subtitles` 保存 → 流水线页刷新后默认结束步骤跟着变（FR-05）。

### FR-12 模型与任务链管理

- **当前行为**：三张卡 —— ① 平台凭证（新建/编辑/删除、Keys 查看、重置 N 个坏 key、key 池状态标签）；② 模型（新建/编辑/删除、测试连通性、从 `/v1/models` 拉取模型列表、代理三态、JSON 输出、思考模式）；③ 任务链（模型池拖拽、链增删改名/排序、任务→链绑定、每模型重试次数、保存/撤销）。
- **链删除（v1.19）**：任何链都可删，**包括被任务绑定的链**（旧版删除按钮在 `usedBy.length > 0` 时被禁用，用户把链的模型清空后既存不了也删不掉，卡在「链 X 不能为空」）。删除时把引用它的任务自动改绑到 `default` 链（若被删的正是默认链则取剩余第一条），确认框列出受影响任务；最后一条链禁止删（运行时要求至少一条链 + `default` 绑定）；空链显示橙色「空链 · 加模型或删除」标记；保存前置校验拦下空链 / 悬空绑定并给出可操作指引，新建链后提示「需加入至少一个模型，不需要可直接删除」。删除仍是草稿操作，点「保存链与绑定」才落盘，可用「撤销修改」还原。
- **入口/代码**：`frontend/src/pages/ModelsPage.tsx`；`server/models_api.py`；`core/utils/llm_profiles.py`；`core/services/llm_service.py: probe / probe_pool`。
- **任务名（12）**：`asr_hints`、`asr_sentence_fix`、`split_by_meaning`、`summary`、`translate_faith`、`translate_express`、`translate_verify`、`align_subs`、`sub_trim`、`tts_correct_text`、`bili_meta`、`ad_detect`。
- **约束**：G-14、G-17；链 id `^[a-z0-9][a-z0-9-]{0,31}$`；GET 一律屏蔽 key，需显式 `reveal-key` 才返回；`/api/models/test` 与 `/api/settings/test-llm` 走同一探针与失败策略。
- **拉取模型列表**：`POST /api/models/fetch-models` → `<base_url>/models`，走 `net_policy.urllib_opener`（auto = 直连优先，失败再试 env 代理），15s 超时；接受 `{data:[{id}]}` / 裸列表 / 字符串列表，排序后最多 500 个。失败一律返回 `ok:false` + `error` + `hint`，**不抛 5xx**。`hint` 由 `net_policy.network_error_hint()` 按错误分类：证书 → 指向 `network.ca_bundle`；401/403 → 检查 key；404/405 → 平台无此接口、手填模型名；429/5xx → 稍后重试；其余网络故障 → 切代理开关。
- **验收**：把 `translate_faith` 绑到另一条链 → 下次翻译日志里的模型变化；坏 key 被标记后自动轮换到同平台下一把；对 `https://zero.cat/v1` 用有效 key 点「拉取模型」能出下拉，用无效 key 得到的是 401 提示而非证书报错；删除被多个任务绑定的链后保存成功，任务绑定自动落到默认链、`GET /api/models` 已无该链；空链 / 悬空绑定 / 零条链 / 缺 `default` 均被拦下且空链 400 文案含「添加模型 / 删除」指引。

### FR-13 术语库（基础库 → 任务术语表 → 勾选回写）

- **两层模型**（2026-11-11 用户拍板）：
  1. **基础库** `terms/*.json`：项目级、全局共享、手工维护，可放多套，不随任务清空。格式 `{"desc": "...", "terms": [{"src","tgt","note"}]}`，空 `src` 过滤。
  2. **任务术语表** `output/log/terminology.json`：每任务一份，随 `output/` 归档 / force 清空；内容 = **勾选合并的底板 + 4.1 新抽术语**，是**第 4.2 步翻译唯一**的术语来源。
- **勾选**：每个新视频在 `#/terms` 勾选 **0~N 套**（有序），写 `config.yaml: terminology.active`。`active: []` 表示**不启用任何基础库**（不回退 `default`）；仅配置键缺失/读取异常时才回退 `["default"]`。
- **流程**：`load_active_terms()` 按勾选顺序合并底板（同 `src` 大小写不敏感、靠后的库覆盖并告警）→ 底板送 4.1 的 summary prompt（标为「已有术语，勿重复抽取」）并并入 4.1 输出 → `merge_terms` 去重后写任务术语表（同 `src` 以**底板**为准）→ 4.2 与实验场 `load_job_terms()` **只读任务术语表，不重复叠加基础库**。
- **勾选冻结**：勾选在 4.1 运行时冻结进任务术语表；中途改勾选或改基础库内容须重跑 4.1（或 force 第 4 步）才生效，4.2 **不会**动态重读 `terms/*.json`。
- **回退**：任务术语表不存在 / 损坏 / 结构不合法（只跑 4.2、新工作区、实验场）→ 用当前勾选的底板，不报错；文件合法但 `terms: []` 视为**有意的空术语表**，不静默重新注入底板。
- **一致性**：`translate_ckpt.json` 指纹纳入术语签名，术语变化自动作废旧译文断点（G-06，避免旧块沿用旧术语）；改术语/提示词会让 4.2 的 gpt_log 缓存（按 prompt 全文）全部失效 → 重跑第 4 步**全额重新计费**（G-05，改前先评估成本）。
- **勾选回写（v1.16）**：`#/terms` 列表卡片「从任务术语表回写」→ Modal 列出当前任务术语表全部词条（标注「来自底板 / 4.1 新抽」与「目标库已存在」），手动勾选 0~N 条 → 选目标基础库（已有库下拉，或输入新库名新建，**不自动激活**）→ 提交。服务端只接收 `src` 列表并**重新从任务术语表读取 src/tgt/note**（前端不回传译文，杜绝把编辑过的脏数据写进基础库）；`write_back_terms()` 复用 `merge_terms` 按 `src.strip().lower()` 合并、`save_term_set` 原子落盘，返回 `added/updated/skipped`。**同名 src 默认跳过**、保留基础库里的人工译文（与 4.1「底板压过 LLM 猜测」一致）；已有库保留原 `desc`，目标库文件损坏 → 400 拒绝覆盖，不静默重建。
- **回写边界**：只写 `terms/*.json`，**不修改 `output/log/terminology.json`** → 保持「勾选在 4.1 冻结」语义，对当前任务的 4.2 翻译、`translate_ckpt.json` 指纹、GPT 缓存**零影响零费用**；下次任务勾选该库并跑到 4.1 才生效。不调用 `update_key`（避免抹掉 config.yaml 注释）、不自动激活新库；目标库正在激活中也可回写。无 4.1 产物（只跑 4.2 / 新工作区）→ 页面与接口明确提示先跑 4.1，**绝不把回退底板当成新抽术语**（`load_job_terms(allow_fallback=False)` 严格模式）。
- **新建修复（v1.16）**：原「新建」按钮用空串 `''` 表示新建，但 `if (editing)` 把空串当成 false → 点击毫无反应；编辑态也没有库名输入框，`saveTermSet('')` 必然被名称正则 400，且 `if (!editing) return` 会静默不发请求；`PUT /terms` 是整库覆盖，同名新建会**静默覆盖**旧库。修复：`editing !== null` 判断 + 新建态可填「库名」（编辑态只读）+ 名称校验上移到 `translate_service.validate_term_set_name`（新建 / 编辑 / 回写三入口共用一份）+ `create=true` 且目标已存在时返回 409，前端二次确认后才带 `overwrite=true` 重提。顺带修掉同类静默失败：`update_key` 在 config 缺 `terminology.active` 键时返回 `False`，原 `PUT /terms/active` 忽略返回值仍回 200（勾选没落盘却显示成功），现在返回 500 明确提示。
- **全部术语视图（v1.21）**：`#/terms` 原先只列出各库的**词条数**，词条必须逐库点「编辑」才看得到，4.1 新抽术语更是只藏在回写 Modal 里。`GET /terms` 扩展 `catalog{rows,stats}`（避开 `GET /terms/{name}` 吞子路径），由 `translate_service.term_catalog()` 唯一决策：读所有 `terms/*.json`（含未激活）+ `load_job_terms(allow_fallback=False)`，按 `src.strip().lower()` 合并成一行；`effective` = 4.2 真正注入的值（有 4.1 产物用任务术语表、否则用当前勾选底板），未生效词条仍展示自身译文但标「未生效」；每行给 `sets / variants`（各库的 src/tgt/note/是否激活）、`in_plate / in_job / job_fresh`（只在任务表 = 4.1 新抽未回写）、`conflict`（多库译文不一致）、`drift`（4.1 冻结值与当前所有基础库都不同，即「勾选在 4.1 冻结」的可视化）。前端总表支持搜索、范围筛选（生效 / 未生效 / 4.1 新抽 / 冲突漂移）、按来源筛选、分页、展开逐库对照、点来源库标签直接进入编辑。**纯只读**：不写 `terms/`、config、任务术语表、断点与 GPT 缓存，不调 LLM、零费用；合并/去重仍只用 `merge_terms`，不许出现第二份「生效」判定。
- **按来源完整清单（v1.22）**：用户明确「`terms/` 是按分类 / 人物 / 行业分套存放的基础（汇总）库，每次翻译新文本要把它加到新抽术语里，`output/log/terminology.json` 就是这份完整的「基础 + 新获取」表，且能回写进指定的基础库」，并要求**两个位置的术语表都完整展示**。v1.21 的并集总表把同一 `src` 在多套库里的取值折成一行，读不全某一套库；因此 `term_catalog()` 在同一次只读遍历里额外产出 `sources[]`：每套 `terms/*.json` + `output/log/terminology.json` 各一项，含 `id/kind/name/path/label/active/available/count/terms`（文件内全部词条、保持文件顺序，仅用 `merge_terms` 规范化与库内去重）。`#/terms` 把「全部术语」卡片改成 Tabs：默认页「任务术语表（4.2 使用）」完整列出 `output/log/terminology.json` 并可**直接勾选行 → 选目标基础库回写**（沿用同一个回写 Modal，不造第二份写入逻辑）；每套基础库各一页完整列出该库词条、标已勾选 / 未勾选、对照 4.2 生效值、一键进编辑；末页保留 v1.21 的并集总表（搜索 / 筛选 / 展开逐库对照）。页顶用大白话写明「基础库 → 勾选底板 → 4.1 生成任务术语表（底板 + 新抽、按 src 去重、底板优先）→ 4.2 只用这张表 → 勾选回写」链路与当前条数；勾选基础库时明确提示**必须重跑 4.1** 才会更新任务表（会重新调 LLM 且作废 4.2 断点 / GPT 缓存），不假装即时生效。仍是**纯只读 + 零费用**，`rows/stats` 契约不变；生效值只从 `catalog.rows` 取，前端不自行判定。
- **入口/代码**：`frontend/src/pages/TermsPromptsPage.tsx: TermsTab`（列表 / 编辑态 / 回写 Modal / 按来源 Tabs / 并集总表）；`frontend/src/translateApi.ts`（`TermCatalog*` / `TermSource` 类型）；`server/translate_api.py`（`GET /terms` 扩展 `job{available,terms,plate_srcs,sets}` + `catalog{rows,stats}`、`POST /terms/writeback`、`PUT /terms` 的 create/overwrite）；`core/services/translate_service.py: load_active_terms / merge_terms / load_job_terms / term_catalog / validate_term_set_name / create_or_replace_term_set / write_back_terms / build_shared_context`；`core/_4_1_summarize.py`；`core/_4_2_translate.py`。
- **约束**：删除激活中术语库 409；`custom_terms.xlsx` 只做一次性迁移；共享上下文统一走 `prompts/shared.md` 模板（避免双重表头）；**基础库不自动回写**——4.1 新抽的术语不会自己进 `terms/`，必须在本页手动勾选回写；`GET /terms/{name}` 会吞掉任何新增的 `GET /terms/xxx` 子路径，故回写预览与全部术语目录都扩展现有 `GET /terms` 响应、写入走 `POST /terms/writeback`；名称校验、基础库写入、全部术语目录与按来源清单决策只能在 `translate_service`（`check_single_source.js` 的 `term-set-write-decisions` / `term-catalog-view` / `term-source-file-labels` 守卫）；`sources` 的文件标签与清单只在 `term_catalog()` 里生成，API 与前端不得二次读盘重造。
- **验收**：勾选 0 套 → 4.1 / 4.2 的 prompt 无基础术语；勾选两套 → 任务术语表含合并术语且重复 `src` 以靠后库为准；4.1 新抽术语出现在 4.2 每个块的 prompt 中；任务术语表缺失时 4.2 用底板正常翻译；术语变化后旧断点作废。回写：勾 N 条 → 目标库新增 N 条且内容取自任务术语表；同 `src` 已存在时跳过且不改译文；可回写进新库且不自动激活；无 4.1 产物 / 非法库名 / 坏目标库 / 空勾选均 400 且不写盘；回写后任务术语表与断点指纹不变。新建：点「新建」进入编辑态、可填库名并保存成 `terms/<名>.json`；同名新建先 409 确认再覆盖。全部术语：两套基础库（只激活一套）+ 4.1 新抽 → 总表按 `src` 去重后包含全部词条，未激活库标「未生效」但仍显示译文，4.1 新抽标「仅任务表」；`active: []` 且合法空任务术语表时 `effective=0` 不回退 `default`；基础库与任务表取值不同时标「冲突 / 已漂移」且生效值取任务表；读 `catalog` 前后 `terms/`、`config.yaml`、`output/log/terminology.json` 字节不变。按来源清单：两套基础库含同一 `src` 但译文不同时，两个来源页**分别保留各自取值**、并集只显示一行；无 4.1 产物时任务表来源项 `available=false` 且 `terms=[]`，绝不塞进回退底板；损坏的库不出现在 `sources`；从任务表页勾选行回写仍走 `write_back_terms` 单一决策，只写基础库、不改任务表。

### FR-14 提示词管理

- **当前行为**：列出 4 个内置提示词（`prompts/faith.md`、`express.md`、`shared.md`、`summary.md`），标记「默认 / 已自定义」；编辑 `TextArea`（`{{var}}` 占位符）、保存覆盖、恢复默认（删除覆盖文件）；「渲染预览」免费显示真实将发送的 prompt（不调 LLM、不保存）。
- **入口/代码**：`TermsPromptsPage.tsx: PromptsTab`；`server/translate_api.py: /prompts*`；`core/prompts.py`；`prompts/*.md`。
- **约束**：留空保存 = 恢复默认；改提示词会让已完成块的 `gpt_log` 缓存失效（prompt 变了）→ **改前先评估重跑成本**（G-05）；`shared` 渲染预览用任务术语表（4.2 视角），`summary` 渲染预览用勾选底板（4.1 视角）。
- **验收**：改写 `faith.md` 后预览里能看到新内容；正式翻译的 `calls.jsonl` 里出现新 prompt。

### FR-15 切分实验场（L1 / L2 / L3）

- **当前行为**：
  - 输入：可粘贴 6 行示例文本，或勾选「载入本任务切句结果（`split_by_nlp.txt`）」；参数「分块字符数」（默认 2400）、「每块句数上限」（默认 10）。
  - L1「预览切分」：免费，返回块列表 + 字符/句数统计 + 上传字符估算；表格可勾选、可展开看每块预览。
  - L2 成本估算：三条 Alert（块数与均/最大字符 + 当前任务术语表条数、预计 LLM 调用次数、上传总字符与共享上下文）；上下文术语取 `load_job_terms()`，与 4.2 完全一致。
  - L3「实测选中 N 块」：逐块串行真实调用 `translate_faith` 链（按块计费）、可中途停止、「用缓存（免费回放）」开关；结果表展开看原文/译文上下对照。
- **入口/代码**：`TermsPromptsPage.tsx: PlaygroundTab`；`frontend/src/translateApi.ts: previewSplit / probeOneChunk / verifyTranslation`；`server/translate_api.py: /preview /probe`；`core/services/translate_service.py: split_chunks / render_shared_context`。
- **约束**：G-15（绝不写正式产物）；参数**只用于试跑、不落盘**，默认值硬编码 2400/10，与 `config.yaml` 的 `translate.chunk_size: 8000` / `max_sentences: 60` 不一致（已知差异，用户已确认）；新预览先 abort 旧请求；实测逐块返回即刷新进度。
- **验收**：L1/L2 零 LLM 调用；L3 只在选中块产生 `calls.jsonl` 记录；切回来 tab 状态保留（4 个 tab `forceRender`）。

### FR-16 调用日志与译文校验（L4）

- **当前行为**：「调用日志」tab 按任务过滤 / 只看失败 / 刷新，表格显示时间、任务、任务链、结果（缓存/成功/失败）、接入点·模型·key（脱敏）、耗时、上下字符、错误（点开看完整）。数据源 `output/gpt_log/calls.jsonl`。L3 结果行可点「校验」→ `verifyTranslation` 给 `score/10`、issues、suggestion，或「重新校验」跳过缓存。
- **入口/代码**：`TermsPromptsPage.tsx: CallsTab / PlaygroundTab`；`server/translate_api.py: /calls /verify`；`core/utils/ask_gpt.py`（写 `calls.jsonl`、`error.json`）。
- **约束**：key 脱敏为 `首8..尾4`；`limit` 夹在 1–1000，默认 200，倒序。
- **验收**：任何 LLM 调用（含实验场）都能在日志里查到；失败调用能看到完整错误。

### FR-17 服务重启 / 停止

- **当前行为**：Header 两个图标按钮，二次确认后 `POST /api/restart`（1.5s 宽限后 `os.execv` 自替换；前端每 0.5s 探 `GET /api/pipeline` 直到恢复，最多 60s，然后 `location.reload()`）或 `POST /api/stop`（1s 后 SIGTERM / Windows TerminateProcess，3s 兜底 `os._exit`）。
- **入口/代码**：`frontend/src/App.tsx`；`server/restart_api.py`；`server/__main__.py`。
- **约束**：重启是一次性标志，宽限窗口内二次请求 409；任务忙（单任务或批量）时两者均 409；`GET /api/restart-status` 查标志。
- **验收**：改完 core 代码点重启 → 页面自动恢复，新逻辑生效。

---

## 5. 功能需求 · 批量与产物

### FR-18 批量处理（REST，页面在用）

- **当前行为**：输入根目录（或「浏览...」弹窗选目录/文件）→「扫描」列出所有媒体（递归、跳过 `*_log` 与点文件，视频在前音频在后）→ 默认勾选未完成文件 →「开始处理 (N 个已选)」。表格显示进度点阵（pending/running/done/failed）、整体状态（pending/partial/done/delivered/failed）、日志与单文件重跑。动作条还有「全部重跑」「重跑失败的」「停止」。单文件重跑弹窗支持「整个重来」或按「起始/结束步骤」范围重跑。完成后自动把 `output/trans_src.srt` 回送成视频旁的 `<文件名>_trans_src.srt`。
- **入口/代码**：`frontend/src/pages/BatchPage.tsx`；`frontend/src/batchApi.ts`；`server/batch_api.py`；`core/batch_engine.py`；`core/job_runner.py`；`core/utils/job_context.py: workspace_for / JobState / Job.deliver`。
- **镜像规则**：`root/a1/001.mp4` → `<root>_log/a1/001/`，子进程 `cwd=工作区`、env 注入 `PYTHONPATH` / `PYTHONUNBUFFERED` / `PYTHONIOENCODING`；写 `output/input_manifest.json` 指向源视频绝对路径。
- **约束**：**串行**（一次一个视频，Phase 1），`max_workers` 是步骤内部 LLM 并发、不是批量并发；`job_state.json` 原子写、含输入指纹（size+mtime），指纹变化视为全新任务全量重置；只跑非 `done` 步骤；用户 stop 时被杀步骤保持非 done 供精确续跑；`force_restart` 清空工作区内容但保留目录；引擎层无重试，LLM 层才有。
- **事件**：`batch_state`、`job_start`、`job_steps`、`job_step`、`job_done`、`job_error`、`log`（前端 800ms 节流刷新行）。
- **验收**：中断后续跑不重复已完成步骤；两个视频的产物互不污染；完成后源视频旁出现 `<stem>_trans_src.srt`。

### FR-19 旧 CLI 批量（遗留，Excel 驱动）

- **当前行为**：`batch/OneKeyBatch.bat` → `python batch/utils/batch_processor.py`；`batch/input/` 放视频，`batch/tasks_setting.xlsx` 列 `Video File / Source Language / Target Language / Dubbing / Status`；只处理 Status 为空或含 Error 的行；每步最多 3 次尝试，失败把产物挪到 `batch/output/ERROR/<stem>/` 并在 Excel 写错误；成功 `cleanup('batch/output')`。运行前须关闭 Excel。
- **入口/代码**：`batch/utils/batch_processor.py`、`video_processor.py`、`settings_check.py`；`batch/README.zh.md`。
- **约束**：与 REST 批量**不共享状态、不共享工作区**，PRD 与实现都不可混写。
- **验收**：不在 `batch/` 目录做任何改动时，页面批量功能不受影响。

### FR-20 产物浏览与下载

- **当前行为**：「产物」页可浏览三个根：`output/`（单任务）、`history/`（归档）、批量工作区（需先在批量页扫描）。「根 / 上一级 / 刷新」导航，目录列表点击进入，文件表显示名称（可复制）与大小，「下载」新开窗口取文件。
- **入口/代码**：`frontend/src/pages/ArtifactsPage.tsx`；`server/batch_api.py: /api/batch/artifacts/browse|download`。
- **约束**：G-10 路径白名单，越界 403；目录不存在/无权限 400/403。
- **验收**：归档过的视频能在 `history/<名>/` 下找到并下载 srt。

---

## 6. 功能需求 · 处理流水线

### FR-21 步骤总表（11 步，`core/pipeline.py` 为唯一来源）

| # | id | 标题 | 阶段 | 完成 marker | clean_extra | LLM 任务 | needs_video |
|---|---|---|---|---|---|---|---|
| 1 | `transcribe` | 转录 whisperX | sub | `output/log/cleaned_chunks.json` + `.xlsx` | `output/log/asr_checkpoint/` | `asr_domain_hints` | |
| 2 | `asr_correct` | 幻觉词修正 | sub | `output/log/asr_correction_report.json` | `asr_correct_ckpt.json` | `asr_sentence_fix` | |
| 3 | `split` | 切句 NLP + LLM | sub | `split_by_nlp.txt` + `split_by_meaning.txt` | — | `split_by_meaning` | |
| 4 | `translate` | 总结 + 翻译 | sub | `translation_results.xlsx` + `terminology.json`（任务术语表）+ `translate_ckpt.json` + `summary.txt` | — | `summary`、`translate_faith`、`translate_express` | |
| 5 | `align` | 对齐字幕 | sub | `src.srt` + `trans.srt` + 两张中间 xlsx | — | `align_subs` | |
| 6 | `merge_sub` | 合并字幕到视频 | sub | `output/output_sub.mp4` | — | — | ✔ |
| 7 | `audio_task` | 生成配音任务 | dub | `output/audio/tts_tasks.xlsx` | — | `sub_trim` | |
| 8 | `refer_audio` | 提取参考音频 | dub | `output/audio/refers/` | — | — | |
| 9 | `gen_audio` | 生成配音 | dub | `output/audio/segs/` | — | —（内部会用 `tts_correct_text`，注册表未登记） | |
| 10 | `merge_audio` | 合并完整音频 | dub | `output/dub.mp3` | — | — | |
| 11 | `dub_to_vid` | 合并配音到视频 | dub | `output/output_dub.mp4` | — | — | ✔ |

> 表中简写的 marker：`cleaned_chunks.*` / `split_by_*` / `translation_results*` / `*_ckpt.json` / `asr_correct*` 在 `output/log/` 下；`src.srt` / `trans.srt` 在 `output/` 下。`marker_mode` 默认 `all`（全部存在才算完成）。`is_done()` 只看存在性。

### FR-22 阶段一步骤内部规格

| 步骤 | 输入 → 主要输出 | 跳过守卫 / 断点 | 外部工具与关键行为 |
|---|---|---|---|
| 输入下载（非注册步骤） | URL → `output/<title>.mp4/.jpg` + `input_manifest.json` | 无（一次性 job） | yt-dlp（先 pip 升级，`run_logged`）、`cookies.txt`、ffmpeg；`ejs:github`；bot 挑战转可读错误；浏览器 cookie 失败回退文件；自定义 `%(uploader)s/%(title)s` 会落子目录，需移入 `output/` 或写 manifest |
| 1 `transcribe` | 媒体 → `output/audio/raw.mp3` → `cleaned_chunks.json`（JSON 主存）+ `.xlsx` 导出 | `@check_file_exists(cleaned_chunks)`；`asr_checkpoint/` 分段断点（`segments.json`、`seg_*.json`、`asr_hints.json`），指纹 = 文件名+size+duration+gap_fill+decode算法版本/配置（v1.11） | ffmpeg 转码 / silencedetect、whisperX（local / 302 cloud / elevenlabs）、pyannote VAD、可选 Demucs 人声分离、LLM 生成领域提示词 `initial_prompt/hotwords`（仅 local）；断点**收尾故意保留**作备份 |
| 2 `asr_correct` | `cleaned_chunks.json` 原地改写 + `asr_correction_report.json` + `.json.bak/.xlsx.bak` | report 存在即跳过（含未解决批次时自动补跑）；`asr_correct_ckpt.json` 每批原子写、成功后删除 | 三层：L1a 词频实体统一（同键/复数/短词护栏）、L1b 热词模糊**只标记不改写**、L2 句子级 LLM 复核（3 并发、失败批次 3 轮 sweep、冷却 120/180/300s）、L3 索引锚定回写 |
| 3 `split` | `cleaned_chunks.json` → `split_by_nlp.txt` → `split_by_meaning.txt` | 两个 `@check_file_exists`（必须同时删） | spaCy 按标点/长句根切分；>80 spaCy token 安全阀；comma/connector 切分已移除（译前切碎伤质量）；LLM 语义切分重复 3 轮、线程池、`" " * retry` 破缓存；`split_sentence()` 是**保真**切分（LLM 只给切点，代码用原句切片拼回） |
| 4 `translate` | `split_by_meaning.txt` + 勾选的基础术语库 → `terminology.json`（任务术语表 = 底板 + 新抽）+ `summary.txt` → `translation_results.xlsx` | `_4_1` 无守卫（每轮重生成术语/摘要）；`_4_2` 有 `@check_file_exists`；`translate_ckpt.json` 每块原子落盘、收尾**只清空 chunks 保留文件**（它是完成 marker） | 4.1：`load_active_terms()` 底板（勾选 0~N 套）+ LLM 抽 ≤15 条 → `merge_terms` 去重写任务术语表（同 src **底板**优先）；4.2：`load_job_terms()` **只读任务术语表**（缺失/损坏回退底板），不再重复叠加基础库；分块按 `translate.chunk_size`/`max_sentences` 双上限；chunks 指纹 = 句子 + 块数 + **术语签名**，术语变化自动作废旧 ckpt；两阶段翻译（直译 → 可选意译 `reflect_translate`）；3 次重试 + 键名别名归一；并发 `check_len_then_trim`；**第 5 步才是 LLM 调用大头** |
| 5 `align` | 译表 + 词表 → `src.srt` / `trans.srt` / `src_trans.srt` / `trans_src.srt` + 配音用 srt | 无 | `_5` 超长行 LLM 对齐切分（最多 3 轮，futures 必须显式消费）；`_6` 两次 `align_timestamp`：显示字幕与配音字幕（用 remerged 表）；`autocorrect_py` 清理译文 |
| 6 `merge_sub` | `src.srt` + `trans.srt` + 视频 → `output_sub.mp4` | audio-only 直接返回 | 双 `subtitles` 滤镜 force_style + `-map 0:v:0 -map 0:a:0?`；**编码参数由 `encode_service.build_encode_args` 按原片决定**（码率/帧率/pix_fmt/音频直通，FR-29）；`burn_subtitles=false` 只生成 0 秒黑帧占位；缺 srt 抛 `FileNotFoundError`（G-16）；`run_logged` 转发 |

### FR-23 阶段二步骤内部规格

| 步骤 | 输入 → 主要输出 | 跳过守卫 / 断点 | 关键行为 |
|---|---|---|---|
| 7 `audio_task` | 配音 srt → `tts_tasks.xlsx` | `@check_file_exists` | 解析 SRT、去括号与 `-`、`min_subtitle_duration` 合并过短字幕；不再二次 trim |
| 8 `refer_audio` | `tts_tasks.xlsx` + `vocal.mp3` → `refers/<n>.wav` | 判**自己的 `refers/` 目录**非空（不能判 `segs/`） | 先 `demucs_audio()` 兜底；soundfile 按 SRT 时间切片 |
| 9 `gen_audio` | `tts_tasks.xlsx` → `segs/<n>_<i>.wav` | `tts_main` 按 `save_as` 存在即跳过；`audio/tmp/` 存原始 | 按 `tts_method` 分发 9 种后端、3 次重试、末次 LLM 改词；前 5 行 warmup；gpt_sovits 强制单线程（独立控制台窗口，不能用 PIPE）；ffmpeg `atempo` 变速；按 `speed_factor` 重排时间轴 |
| 10 `merge_audio` | `tts_tasks.xlsx` + `segs/` → `dub.mp3` + `dub.srt` | 首段缺失则 return | ffmpeg 逐段转 16k 单声道 64k mp3；pydub 按 `new_sub_times` 插静音拼接 |
| 11 `dub_to_vid` | `dub.mp3` + `dub.srt` + `background.mp3` + 视频 → `output_dub.mp4` | audio-only return；`burn_subtitles=false` 生成黑帧占位 | -20dB 归一化；ffmpeg `filter_complex` 双字幕 / 缩放 / pad + `amix`；编码参数同样走 `encode_service`（`audio_reencode=True` → aac 按原音频码率，不再写死 96k，FR-29）；`run_logged` 返回码非 0 抛 `RuntimeError` |

### FR-24 字幕质量机制（关键，不许回退）

| 机制 | 位置 | 规则 |
|---|---|---|
| 词级存储 | `core/utils/words_store.py` | JSON 为主存（xlsx 曾给词加字面引号、数值往返改毫秒）；`asr_correct` 合并修正 = `words[i].text = "in such a way"` + 被吞行 `text=""`，**行数与全部时间戳不变** |
| 时间戳对齐 | `core/utils/subtitle_time.py: get_sentence_timestamps` | **v1.11完整原文顺序匹配优先**：原文/词表相同标准化、去内部空格，字符映射至词组（包括合并后空白行）；重复句只消费下一次，缩写拆分不靠token数猜时间。完整匹配失败才使用有边界的句首3词锚点+词元投影容错，禁止跨已确认匹配边界回退；标点句不消耗下一词，回退重叠收紧，耗尽告警。不得靠修改SRT统一偏移修复源ASR错位。 |
| 缩写 token 差异 | 上面两把尺子 | 历史漂移源为spaCy少量拆分did n't，而非第5步改写。v1.11不改tokenizer：完整匹配路径双侧去空白、同标点标准化，did n't/didn't映到相同字符，避免额外吃词；保留锚点作为异常文本容错。 |
| ASR漏词与源时间戳（v1.11） | `whisperX_local.py` / ASR服务层 | 用户提供原版ASR为无提示词、temperature=0。相同模型/VAD窗口实测：现版领域initial_prompt+hotwords让窗口前20s讲话丢失，仅留下尾句或复述提示词；随后强制对齐将尾句放到窗口起点，造成约20s提前。默认恢复原版无叙述提示/无热词/不强制禁止重复的解码，领域提示仅供纠错；配置及算法版本纳入ASR断点指纹。禁止仅对字幕统一平移；旧断点需失效或显式重转，保留原音频/旧成果便于比较。 |
| 字幕重复句匹配（v1.11） | `core/utils/subtitle_time.py` | 参考原版按顺序完整文本匹配：标准化后去内部空格构造字符→词组映射，当前位置完整匹配优先，缩写did n't/didn't等不靠数token推算；同句重复必须消费下一次，不能向后匹配到已用过词。完整匹配失败才保留有限锚点容错，回退不能跨已确认句子起点；标点行不吃下句，merged词+空白行音频跨度保留。毫秒格式统一四舍五入，避免09:34.860截断成09:34.859。 |
| VAD 漏检补转 | `core/asr_backend/vad_gap_fill.py` | 转写后扫描未覆盖空档→ffmpeg判有声→临时NoVad转写→align→合并；NoVad继承Vad并按30s切span，时间只+a，模型状态finally还原。**v1.10补转验收**：补转临时清空initial_prompt/hotwords/prefix并finally还原；按词级覆盖检查原空档完整范围（包含补转后首/尾和整段无结果），残留≥min_gap且有声比例达阈值或累计有声≥min_gap时，用同一底层Whisper模型的非批处理、无VAD/无提示词转写重试一次；重试只采纳有声未覆盖时长减少的结果，仍残留明确告警，不能只凭返回非空就报成功。与领域提示词连续重合且低对齐分的主转录段作为候选重转，只有实际对齐覆盖合格才替换，失败保留原记录并告警；不无限重试。全片诊断须检查原始checkpoint、词表、字幕及低分/提示词回声，静音/音乐不能仅因有声就认定漏转。自检去掉历史视频硬编码区间，指定范围和当前全片均可只读复核，不自动清断点或调用付费LLM。旧断点不自动删除（用户可保留已有成果）。 |
| 翻译键名容错 | `core/translate_lines.py: normalize_sub_keys` | 模型偶把 `direct`/`free` 写成 `translation`/`polished` 等别名 → 原地归一；除 `origin` 外只剩一个键时兜底；≥2 个候选**不猜**，交给校验报错重试。校验失败也进 3 次重试（新 attempt marker 破缓存）。刻意不改 prompt，避免已完成块的缓存失效重付费 |
| 幻觉词修正 | `core/asr_correct.py` | 见 FR-22 第 2 步；report 是唯一完成判据，ckpt 只能进 clean_extra |
| 译后切分保真（v1.11） | `core/_5_split_sub.py` | 参考用户原版：源文没有真正切开/拼回后与原句不一致时保持整行；target_part必须为非空字符串且数量相等，不能让空译文或单侧切分进入字幕；两侧一起成功才更新内存行。 |

### FR-25 LLM 调用、任务链与 key 池

- **三表配置**：`llm.endpoints`（平台凭证 + `key` / `key_file` / `keys` 池）→ `llm.models`（模型条目）→ `llm.chains` + `llm.tasks`（任务→链绑定，未绑定走 `default`）。兼容 v2 dict / v1 裸列表 / v0 内联三种旧格式。**链可删除（含被任务绑定的链）**：删除时任务自动改绑到默认链 / 剩余第一条链；链必须非空且只引用已存在模型，`PUT /api/models/chains` 对空链返回 400 并提示「添加模型或删除该链」（v1.19）。
- **key 池**：key 文件格式 `label----key`；`output/key_state.json` 永久标记死 key；`401/402/403` → 标记死 → 同平台换下一把 key（不换模型、不退避）；池耗尽才切链上下一模型。`429` 单独归 `rate_limit`：解析 `Retry-After` 头 / 消息中的限流窗口（如 `maximum 10 requests in 1 minutes` → 等 60s+1s）/ `try again in Xs`，兜底 30s、上限 120s；等待**不消耗** `retries_per_model` 预算（模型没坏，只是节奏太快），每模型最多等 3 次后切下一模型。`5xx/超时` 归 transient 指数退避 `2**n`；`ValueError` 归 content 重试。
- **唯一归属**：`core/services/llm_service.py: classify_error / on_failure / check_response / probe / probe_pool / rate_limit_wait`；`check_single_source.js` 强制这几组决策字面量（401/402/403、429）只能出现在唯一文件。
- **调用与缓存**：`core/utils/ask_gpt.py`；`temperature=0.4`、`timeout=300`；支持 `response_format=json_object` 的模型才加，否则追加「valid JSON only」；`json_repair` 兜底；`net_policy` 按 auto/direct/proxy 记忆优先模式；`calls.jsonl` 全量追踪、`error.json` 存校验失败原文。
- **成本原则**：G-05 + G-06；第 5 步是 LLM 调用大头（40.9% 英文原句 >75 字符 ≈ 3095 次切分调用，是第 4 步的 12 倍），想省钱先调 `subtitle.max_length`；将来做第 5 步批量对齐时**批量大小要写成 config 可配项**，不要硬编码。
- **显存回退**：whisperx 的 pyannote VAD `Inference.batch_size` 写死 32，OOM 时它是第一个炸点；改 `model.vad_model.vad_pipeline._segmentation.batch_size`，force_reload 后必须重新 apply；pyannote OOM 抛 `MemoryError` 不是 `RuntimeError`；顶层需 `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`。

### FR-26 测试与守卫

- **单测**：`test/` 下 28 个 `test_*.py` + `conftest.py` + `README.md`；跑法见0.4。当前基线 **572 passed, 1 skipped**（v1.19，新增恒定质量编码与裁剪二次转码放宽、任务链删除与绑定改绑）。覆盖：链与走链、key 池、第 4 步（分块/断点/术语/共享上下文）、词级存储、ASR 纠错纯逻辑、译文校验、字幕时间、进度、VAD 补转、子进程日志、历史归档、接口层、cookie/下载、流水线范围、TLS 信任、B站文案（含转录来源优先级 / 全文与均匀采样 / 自定义模板兜底）、广告位识别与 EDL、精细时间轴的帧率探测、编码参数对齐与 preset 映射（FR-29）、手动切点补集与重识别保留（FR-30）、全局日志出口 / 防重 / 接口级上送（FR-31）、429 限流等待与解析（FR-25）、术语两层模型与 0 勾选 / 去重 / 断点签名 / 4.1 写盘（FR-13）、术语勾选回写与新建同名 409 / 名称校验 / 激活写入失败（FR-13）、广告位预览源默认决策与字幕标记 / 同名重烧版本 / context 单快照 / 预览 Range 白名单（FR-28）、CQ 参数与 cut profile 放宽 / 探测原片而非已压产物 / 非法配置回退 / 旧 bitrate 逐字不变（FR-29）、任务链删除改绑与空链 / 悬空绑定 / 零条链 / 缺 default 拒绝（FR-12/25）。
- **未覆盖**：步骤 5–8 与 9–11 的实跑、真实 ASR/spaCy/pyannote、GPU 路径、前端、prompts 模板渲染、前端交互。
- **隔离三件套**：monkeypatch `config_utils.CONFIG_PATH` + `chdir(tmp_path)` + monkeypatch 绝对路径常量（`TERMS_DIR` / `KEY_STATE_FILE`）。
- **分层守卫**：`check_single_source.js` —— 401/402/403 决策字面量、`mark_key_dead` 调用、`delete_step_artifacts` 调用各自只允许出现在唯一归属文件。
- **只读自检**：`python check_vad_gaps.py` 优先扫当前ASR checkpoint的词级空档，回退词表，并标提示词回声；`--fill` 用已缓存本地模型关闭VAD/提示词复核，支持 `--start 00:09:30 --end 00:10:10`。不改断点/字幕/配置，不下载模型、无候选不加载；没有“历史视频固定区间”兜底。此工具不保证检出<3秒短漏词或所有内容错位。

---

## 7. 配置总表（`config.yaml`）

> 「设置页」= `server/settings_api.py SCHEMA` 白名单（25 键）；其余只能手改文件。标 ★ 的是常被手改的关键项。

| 段 / 键 | 当前值 | 设置页 | 作用 |
|---|---|---|---|
| `display_language` | `zh-CN` | ✔ | 界面语言 |
| `target_language` | `简体中文` | ✔ | 写入提示词的目标语言 |
| `max_workers` | `4` | ✔ | 步骤内部 LLM/字幕并发（**不是**批量并发） |
| `demucs` | `false` | ✔ | 转录前是否做人声分离 |
| ★ `burn_subtitles` | `true` | ✔ | 决定默认结束步骤（FR-05）；关时第 6/11 步只写 0 秒黑帧占位 |
| `ffmpeg_gpu` | `true` | ✔ | h264_nvenc 加速 |
| `summary_length` | `8000` | ✔ | 摘要字符上限（**顶层键**，不在 subtitle 段） |
| `max_split_length` | `50` | ✔ | 第 3-2 步 LLM 切句阈值（spaCy token；译前安全阀，可给大值） |
| `subtitle.max_length` | `75` | ✔ | 单行字幕最大字符；调大显著减少第 5 步 LLM 调用 |
| `subtitle.target_multiplier` | `1.2` | ✔ | 译文长度系数 |
| `min_subtitle_duration` | `2.5` | ✔ | 配音任务合并过短字幕 |
| `tolerance` | `1.5` | ✔ | 字幕时间容差 |
| `reflect_translate` | `false` | ✔ | 是否在直译后再做意译（双倍调用） |
| `translate.chunk_size` | `8000` | ✔ | 第 4-2 步每块字符上限（代码缺省 2400） |
| `translate.max_sentences` | `60` | ✔ | 每块句数上限（代码缺省 10） |
| `tts_method` | `azure_tts` | ✔ | TTS 后端 |
| `speed_factor.min` / `accept` / `max` | `1` / `1.2` / `1.4` | ✔ | 配音语速判定 |
| `network.proxy_mode` / `proxy_url` / `no_proxy` | `auto` / `http://127.0.0.1:7890` / `localhost,127.0.0.1` | ✔ | 出网策略（auto 按 base_url 记忆直连优先） |
| ★ `network.ca_bundle` | `''` | ✘ | 额外根证书 PEM 路径（会替换证书的 MITM 代理用）；留空 = 系统 CA + certifi；路径无效时明确报错 |
| ★ `bili.speaker_names` | `[]` | ✘ | B站投稿文案的讲者名池追加项（内置 skill 硬红线 9 人） |
| ★ `bili.transcript_chars` | `12000` | ✘ | 送进 prompt 的转录字数预算（1000–60000）；未超则全文直送，超了才均匀采样；调大即退化为「永远全文」 |
| ★ `bili.transcript_samples` | `8` | ✘ | 超预算时的均匀采样段数（2–30），头尾各取 1.5 倍配额并标注开头/结尾 |
| ★ `ad.boundary_mode` | `scene_cut` | ✘ | 广告边界定点：画面切点融合（默认）/ `prev_end` 上一句结束+pad / `silence` 静音中点 |
| `ad.pre_roll_pad` / `post_roll_pad` | `0.3` / `0.3` | ✘ | 切点往前 / 广告末句往后留的秒数；pre 设 0 = 严格用切点那一秒 |
| `ad.scene_threshold` / `min_gap_seconds` | `0.08` / `0.8` | ✘ | 场景切换分阈值 / 小于此空隙不找切点直接退回 padding |
| `ad.sponsorblock` / `transcript_chars` | `false` / `60000` | ✘ | 众包库交叉校验开关 / 送进 prompt 的转录字符上限（截断不重新编号） |
| `ad.cut_preset` / `cut_crf` | `veryfast` / `20` | ✘ | **仅旧行为**：`encode.match_source=false` 或源片探测失败时的速度/质量；正常裁剪不用（v1.8 起，见 FR-29） |
| ★ `encode.rate_control` | `cq` | ✘ | `cq`（v1.18 默认）= 恒定质量不削峰；`bitrate` = 旧的对齐源码率（会削峰，仅作回退）。见 FR-29 |
| ★ `encode.cq_level` | `32` | ✘ | 第 6/11 步的 NVENC 恒定质量级别（越小越清晰/越大体积），实测 30s 样片 CQ32 = +6.4 dB / 1.8× 体积 |
| ★ `encode.cut_cq_level` | `30` | ✘ | 「按此裁剪」专用：它是第二次有损转码，单独放宽 2 档抵消损失 |
| ★ `encode.cq_preset` | `medium` | ✘ | CQ 模式速度预设（NVENC 合法值），默认开 AQ / lookahead / multipass |
| ★ `encode.cq_gop` | `0` | ✘ | CQ 模式显式 GOP 帧数；0 = 不传，交给编码器。实测短 GOP 仅 +0.85 dB 却涨 10~32% 体积，默认不开 |
| ★ `encode.match_source` | `true` | ✘ | `bitrate` 模式下是否对齐源片码率、帧率、音频码率（FR-29）；`false` = 完全旧行为。CQ 模式下也用它决定是否探测源片参数 |
| ★ `encode.bitrate_scale` | `1.0` | ✘ | **仅 `bitrate` 模式**：目标视频码率 = 原视频码率 × 此值（0.2–10）。原片 AV1 输出 H264 时 1.0 体积一致但画质略低，1.5~2.0 换画质 |
| ★ `encode.fps_mode` | `cfr` | ✘ | `cfr` 显式 `-r` 固定帧率 / `passthrough` 原样传时间戳（VFR 源） |
| ★ `encode.copy_audio` | `true` | ✘ | 音频直通零重编码；合成配音（amix）自动忽略并改 aac |
| ★ `encode.min_video_bitrate` | `200` | ✘ | 视频码率下限 kbps，防止极端低码率源压烂画面 |
| `whisper.model` / `language` / `runtime` | `large-v3` / `en` / `local` | ✔ | ASR 模型/语言/运行时；设置页选项缺 `large-v3-turbo`（已知差异） |
| `whisper.detected_language` | `en` | ✘ | 运行时回写 |
| `whisper.hint_mode` | `off` | ✘ | 主ASR是否接受领域提示：off（默认）/hotwords/domain；领域信息仍给纠错，叙述提示会诱发跳词，需谨慎启用 |
| `whisper.beam_size` / `no_repeat_ngram_size` | `5` / `0` | ✘ | 与原版一致；真实口语重复不能靠禁止3-gram裁掉；ASR服务统一构造options，版本和参数进入断点指纹 |
| ★ `whisper.vad_gap_fill` | `true` | ✘ | VAD 漏检补转开关 |
| `whisper.gap_min_seconds` | `3.0` | ✘ | 多大空档才补 |
| `whisper.gap_min_speech_ratio` | `0.5` | ✘ | 主要有声阈值；v1.11首轮空档内累计有声≥1s也复核，避免3.9s/47%这类真实短句被跳过；有声仍不能等同语音 |
| `whisper.gap_fill_noise` / `gap_fill_min_silence` | `-35dB` / `0.25` | ✘ | silencedetect 参数 |
| `whisperX_302_api_key` / `elevenlabs_api_key` | 占位符 | ✘ | 云端 ASR 凭证 |
| ★ `youtube.cookies_path` | `D:\code\vl\cookies.txt` | ✘ | 必须含登录态字段 |
| ★ `youtube.cookies_from_browser` | `''` | ✘ | 保持空（Edge app-bound 加密无解） |
| `youtube.js_runtime` | `''` | ✘ | 空 = `ejs:github` |
| `ytb_resolution` | `1080` | ✘ | 下载默认分辨率 |
| `llm.retries_per_model` | `2` | ✘（模型页管） | 每个模型重试次数 0–10 |
| `llm.endpoints` / `models` / `chains` / `tasks` | 见文件 | ✘（模型页管） | 三表 + 绑定 |
| `terminology.active` | `[]`（当前） | ✘（术语页管） | 勾选的基础术语库名称，按序合并成底板；**`[]` = 不启用任何基础库**，仅键缺失/读取异常才回退 `default`；勾选在 4.1 冻结进任务术语表 |
| `pause_before_translate` | `false` | ✘ | 抽完术语后是否暂停等人工改 |
| `min_trim_duration` | `3.5` | ✘ | 音频裁剪阈值 |
| 各 TTS 段（`sf_fish_tts` / `openai_tts` / `azure_tts` / `fish_tts` / `sf_cosyvoice2` / `edge_tts` / `gpt_sovits` / `f5tts`） | 见文件 | ✘ | 各后端 key / voice / character / refer_mode |
| `model_dir` | `./_model_cache` | ✘ | 模型缓存 |
| `allowed_video_formats` / `allowed_audio_formats` | 见文件 | ✘ | 批量扫描扩展名 |
| `spacy_model_map` | en/ru/fr/ja/es/de/it/zh → md 模型 | ✘ | spaCy 模型映射 |
| `language_split_with_space` / `without_space` | 见文件 | ✘ | 决定词间连接符 |

> **已知差异**：`translate_service.DEFAULTS` = 2400/10，config.yaml = 8000/60；「切分实验场」硬编码 2400/10 且参数不落盘；设置页 `whisper.model` 选项缺 `large-v3-turbo`。三者都不是 bug，但改需求时要一起看。

---

## 8. API 总表

| 组 | 方法·路径 | 作用 | 主要错误 |
|---|---|---|---|
| 流水线 | `GET /api/pipeline` | snapshot + `steps[]` + `defaults` | — |
| | `POST /api/pipeline/run` | `step_id` 或 `from_step`+`to_step`（闭区间），`force` | 404 未知步 / 400 倒挂 / 409 忙 |
| | `POST /api/pipeline/{pause,resume,stop}` | 状态控制 | — |
| 输入 | `GET /api/input/current` | 当前输入媒体（含 `ambiguous`） | 异常降级 200 |
| | `GET /api/input/cookies` | cookie 体检 | 异常降级 `unknown` |
| | `POST /api/input/download` | `{url,resolution}` 异步下载 | 400 参数 / 409 已有输入或忙 |
| | `DELETE /api/input?remove_file=` | 清 manifest / 删媒体 | — |
| 归档 | `POST /api/history/save` | `{name?}` 异步归档 | 409 忙 |
| 日志 | `GET /api/logs/tail?path=&lines=` | 读 `output/` 日志末尾 | 403 越界 |
| WS | `/ws` | 先补发历史 2000 条，再推事件 | 队列满丢事件 |
| 设置 | `GET/PUT /api/settings` | 25 白名单键读写 | 400 未知/只读键 |
| | `POST /api/settings/test-llm` | `{profile}` 单轮连通性 | 404 profile |
| 模型 | `GET /api/models` | 三表（key 掩码）+ key 池状态 | — |
| | `PUT/DELETE /api/models/endpoint` | 凭证增删改 | 404 / 409 被引用 |
| | `POST /api/models/endpoint/reset-keys` / `reveal-key` | 清死 key / 显式看 key | 400 读文件失败 |
| | `PUT/DELETE /api/models/model` | 模型增删改 | 404 / 409 被链引用 |
| | `PUT /api/models/chains` | 一次写链 + 任务绑定 + 重试次数（删链时一并提交改绑后的全量配置） | 400 校验：空链（文案含加模型/删链指引）、引用不存在模型、悬空任务绑定、零条链、缺 `default` |
| | `POST /api/models/test` / `fetch-models` / `reveal-key` | 测试 / 拉模型列表 / 看 key | 404 / 400 / 网络异常以 `ok:false` 返回 |
| 翻译台 | `POST /api/translate/preview` | L1 切块 + L2 估算（免费） | 404 缺切句文件 / 400 空文本 |
| | `POST /api/translate/probe` | L3 实测选中块（不写产物） | 400 块序号越界 |
| | `GET/PUT/DELETE /api/translate/terms`（GET 另返回 `job` 回写预览 + `catalog{rows,stats,sources}`：并集总表 + 每份来源文件的完整清单），`POST /terms/writeback`，`PUT /terms/active`，`GET /terms/{name}` | 术语库 | 409 删激活中 / 409 同名新建 / 400 非法名 |
| | `GET/PUT/DELETE /api/translate/prompts`，`POST /prompts/preview` | 提示词覆盖与预览 | 404 未知提示词 |
| | `GET /api/translate/calls` | 调用日志（倒序，limit 1–1000） | — |
| | `POST /api/translate/verify` | L4 译文校验 | 400 空文本 |
| 批量 | `POST /api/batch/scan` | 目录/单文件扫描 | 400 非媒体 |
| | `POST /api/batch/browse` | 文件浏览器（Windows 列盘符） | 400 / 403 |
| | `POST /api/batch/start` | `{files,force_restart,reset_from,reset_to}` | 400 / 404 / 409 |
| | `POST /api/batch/stop`，`GET /api/batch/status` | 停止 / 状态 | — |
| | `GET /api/batch/log?path=&lines=` | 工作区日志 | 409 未扫描 / 403 越界 |
| | `POST /api/batch/artifacts/browse`，`GET /api/batch/artifacts/download` | 产物浏览 / 下载 | 403 / 404 |
| 服务 | `POST /api/restart`，`GET /api/restart-status`，`POST /api/stop` | 自重启 / 关服 | 409 忙或重启中 |
| B站文案 | `GET /api/bili/context` | `transcript` 状态统计（来源/语言/全文或节选/字数/摘要，**不回全文**）+ 次要参考信息（标题/简介/讲者建议/来源） | — |
| | `POST /api/bili/generate` | 生成三件套（一次 LLM 调用，以转录为一手素材，写 `output/bili_meta.md`）；无可用转录直接拒绝 | 400 参数/校验/无转录 · 502 LLM 失败 |
| 广告位 | `GET /api/ad/context` | 转录统计 + 输入视频/音频 + 定点配置 + 已有 EDL + 可用裁剪源（`sources[]` 带 `subtitled`）+ `default_source`（优先带字幕的烧录版） | — |
| | `GET/HEAD /api/ad/preview?source=` | 视频预览（`PreviewFileResponse` 支持单段 Range / 尾部 Range / 416 / If-Range，读句柄同步关闭并登记）；`source` 只接受固定 id 白名单 | 404 未知源 / 产物不存在 · 416 越界 |
| | `POST /api/ad/preview/release` | 主动关闭所有预览读句柄，返回 `released` 数量；归档 / force 重跑前调用（后端 job 内也会兜底关一次） | — |
| | `GET /api/ad/media-info?source=` | ffprobe 一次读全：fps / duration / 分辨率 / 音视频 codec 与码率（逐帧步进 + 手动切到片尾） | 404 未知源 / 产物不存在 |
| | `GET /api/ad/frame-rate?source=` | 上一条的**旧路径别名**（同一函数），保留兼容 | 404 未知源 / 产物不存在 |
| | `POST /api/ad/detect` | 识别广告位 + 画面切点定点，写 `ad_slots.md` / `ad_segments.json` | 400 无转录/校验失败 · 502 LLM/ffmpeg 失败 |
| | `GET/PUT /api/ad/edl` | 读 / 手工改 EDL 区间、勾选与删除（`segments=[]` 合法清空；`dismissed=[]` 恢复已删除的识别段，MD 同步重渲染） | 404 无 EDL · 400 区间非法 |
| | `POST /api/ad/cut` | 按当前区间取补集裁剪拼接 → `output_no_ads.mp4`（原片不动） | 400 无 EDL/源不可用 · 502 ffmpeg 失败 |
| 静态 | `GET /` | `frontend/dist` 静态页；无构建时返回 `{status:"backend up"}` | — |

**WS 事件类型全集**：`log`、`state`、`step_start`、`step_done`、`history_saved`、`history_cleaned`、`batch_state`、`job_start`、`job_steps`、`job_step`、`job_done`、`job_error`。字段：`line`、`step`、`id`、`title`、`state`、`current_step`、`queue`、`error`、`dest`、`moved`、`locked`、`video`、`workspace`、`status`、`total`、`deliver`、`failures`。

---

## 9. 数据与目录

```
<项目根>/
├── config.yaml              全部配置（手改优先；注释会被设置页 PUT 抹掉）
├── PRD.md                   ← 本文件
├── prompts/                 内置 7 个可覆盖提示词 + 用户覆盖文件
├── terms/*.json             基础术语库（项目级，勾选后合并成底板；任务术语表可勾选回写进来）
├── output/                  单任务工作区（永远不删目录，只清内容）
│   ├── <输入视频> / *.jpg / input_manifest.json
│   ├── video_meta.json      下载时保存的 YouTube 元数据（B站文案【次要参考】）
│   ├── bili_meta.md         B站投稿文案三件套（FR-27 生成）
│   ├── ad_slots.md  ad_segments.json  待裁剪区间表格 + 机器可读 EDL（FR-28 赞助 / FR-30 手动切点共用；`dismissed` 记录被手动删掉的识别段，FR-32）
│   ├── output_no_ads.mp4    按 EDL 取补集裁剪后的无广告成片（FR-28，原片不动）
│   ├── src.srt  trans.srt  src_trans.srt  trans_src.srt
│   │            （trans.srt = 中文译文，FR-27 / FR-28 的一手素材）
│   ├── output_sub.mp4  output_dub.mp4  dub.mp3  dub.srt  normalized_dub.wav
│   ├── audio/    raw.mp3 / vocal.mp3 / background.mp3 / *_subs_for_audio.srt /
│   │             tts_tasks.xlsx / refers/ / segs/ / tmp/
│   ├── log/      cleaned_chunks.json(+xlsx) / split_by_*.txt / summary.txt /
│   │             terminology.json / translation_results*.xlsx / translate_ckpt.json /
│   │             asr_correction_report.json / asr_correct_ckpt.json / asr_checkpoint/ /
│   │             pipeline_latest.log + pipeline_<时间戳>.log（最多 10 份）
│   ├── gpt_log/  <log_title>.json（prompt 缓存）/ error.json / calls.jsonl
│   └── key_state.json       死 key 永久标记
├── history/<视频名>/        归档（移动产物，结构同 output/；同名覆盖）
├── <root>_log/              批量镜像工作区（每视频 job_state.json + output/）
├── docs/batch-phase1.md     旧交付说明（批量期1）
└── test/                    单测（22 个 test_*.py + conftest.py + README.md）
```

**产物下载 / 输入定位**：`find_media_file()` 优先读 `output/input_manifest.json`（`{"path","type"}`），否则要求 `output/` 下媒体候选唯一。

---

## 10. 非功能需求

| 项 | 要求 |
|---|---|
| 成本 | 所有 LLM 调用必须可缓存、可断点续跑；重跑不能重复花已完成块的钱；改模型/改配置必须清对应 `gpt_log` 缓存才生效 |
| 可靠性 | 断点每单位原子落盘；子进程失败不拖累已成功部分；`job_state.json` tmp+rename 抗掉电；WS 慢客户端不阻塞任务线程 |
| 可观测 | 每次运行都有 `pipeline_latest.log`；每条 VAD 空档留跳过/补回原因；所有 LLM 调用进 `calls.jsonl` |
| 性能 | 第 5 步是调用大头；调 `subtitle.max_length` 直接减负；`check_len_then_trim` 并发且静音正常行；长转码进度行节流但不失声 |
| 显存 | 12GB 卡上先 `nvidia-smi` 查残留占用；VAD batch_size 才是首个 OOM 点；`MemoryError` 也要进回退 ladder |
| 平台 | Windows 为主（Git Bash）；路径安全用 `abspath` 前缀；中文文件名与全角字符需可处理 |
| 安全 | 密钥类键不下发前端；日志里 key 脱敏；接口读路径白名单；CORS 只放行 `:5173` |
| 兼容 | 旧 `llm.profiles/api` 配置格式、旧 CLI 批量仍可运行；设置页 PUT 不得覆盖 API key |

---

## 11. 已知限制与「不要回退」清单

1. **marker 不能是会被代码自己删的文件**（断点和缓存只能进 `clean_extra`）—— 否则该步骤永远显示「未运行」。加 marker 前先 grep 它是否被 `os.remove` / `unlink` 过。
2. **force = 清产物+断点+LLM 缓存后重做**；「继续跑」「从某步跑到完」「步骤卡片未完成时的运行按钮」**不能**带 force，否则每次清断点。
3. **步骤内守卫文件必须与 registry markers 一致**：`split` 两个守卫要同时删；`refer_audio` 只能判自己的 `refers/`。
4. **`ask_gpt` 缓存按 prompt、与模型无关** —— 重跑换模型必须删 `gpt_log/<title>.json`；破缓存用显式 attempt 标记。
5. **切句和字幕定位不要混一把尺子**：spaCy token用于切句阈值；v1.11字幕完整匹配在两侧同标准化后去空白，兼容did n't/didn't、合并词与空行；锚点计数仅作容错，不能退回重复句优先回头找三词的旧逻辑。
6. **`NoVad` 必须继承 `whisperx.vads.Vad`** 且自己切30s span；补转时间只+a，VAD/options用完还原。非空补转文本不代表补全：必须按词级覆盖检查原空档首尾/内部；仍有声则非批处理有限重试并告警。仅查SRT空白会被字幕延长显示掩盖；低分候选需复核，不按低分直接删除真实文字。片内词空洞重转所属整段并在可靠恢复后替换，避免词序跨段错乱。
7. **日志**：页面只拦 Python `print`，子进程必须 `run_logged`；不要改 `gpt_sovits_tts.py` 的独立控制台、`_11_merge_audio.py` 的静默调用、所有用于解析输出的 `capture_output=True`。
8. **`exit()` 不能用于后台线程**；`_7` 抛 `FileNotFoundError`，`_12` 对 ffmpeg 返回码抛 `RuntimeError`。
9. **`_5_split_sub` 的 futures 必须显式消费**（`executor.map` 惰性曾导致从不切分）；`_4_2` 的 `ESTIMATOR` 必须并发前预热。
10. **字幕对齐合并写法**：`words[i].text` 带内部空格 + 被吞行 `text=""`，行数和时间戳不变；任何对词表做逐字符精确子串匹配的下游都会崩。
11. **两套批量不共享状态**；REST 批量 Phase 1 串行、lane 并行未实现；`max_workers` 不是批量并发度。
12. **路径白名单是字符串前缀匹配**，`output_xxx` 兄弟目录会被误判为合法（已知边界，未修）。
13. **改设置页会抹掉 config.yaml 全部注释**；重要配置手改文件。
14. **本会话环境**：conda `vl` 跑不动、无法创建临时目录 → GPU 实跑一律交给用户；语法用 `node _lex.js`。
15. 未覆盖测试：步骤 5–11 实跑、真实 ASR/spaCy/pyannote、前端交互自动化（精细时间轴目前只有无头浏览器截图验证，无点击/键盘断言）、server HTTP 层全量。prompts 模板渲染已有单测覆盖（`test_ad_slots.py` / `test_bili_meta.py`）。
16. **B站文案的素材主次**：转录（`trans.srt` 中文译文优先）是一手素材，YouTube 元数据只作参考，别退回「只拿元数据生成标题」。用户在提示词页存的自定义模板可能没有 `{{transcript}}` 占位符 —— `get_bili_meta_prompt` 的兜底注入不能删，否则主素材会被静默吞掉。
17. **广告边界别退回「转录开口那一秒」**：实测上一句正文末词到广告首词有稳定 2.50~3.09s 过场空隙，转录时间已经进广告了。窗口内通常有两个画面切点，**必须取最后一个**（第一个是离开正文镜头，第二个才上赞助卡），再用 `pre_roll_pad` 往前留一点。裁剪也别用 `-ss -c copy`（会吸附关键帧偏 1~2 秒，可能留广告头），用 `trim/atrim + concat` 帧级精确。
18. **广告裁剪不用动字幕时间戳**：裁剪源默认 `output_sub.mp4`（字幕已烧进画面），切完拼完直接成片；别退回「平移 SRT + 重编号 + 重烧」那条复杂链路。SponsorBlock 众包库覆盖不全（本片 404），只能交叉校验，不能当主路径。
19. **边界微调别退回全片进度条**：原生 `<video>` 进度条 47 分钟铺在几百像素上，一点跳好几秒，无法定帧。必须保留 `FrameScrubber` 的**局部窗口**（±1/±3/±10s）+ 逐帧步进 + 帧网格吸附；帧率要由 `ffprobe` 实测（本片 23.976，不是 30），别硬编码。逐帧步进的基准要读 `video.currentTime`（连按不漂移），且按钮点完要把焦点还给时间轴，否则方向键失效。
20. **烧录 / 合成别退回编码器默认参数**：只给 ffmpeg 传滤镜 + 一个 `-c:v h264_nvenc`，NVENC 会按 ≈2 Mbps 输出 —— 本片原片 AV1 408 kbps / 180 MB，产物被撑到 H264 2044 kbps / **748 MB（4.2 倍）**。码率 / 帧率 / pix_fmt / 音频策略必须由 `encode_service.build_encode_args` 从原片探测后决定（FR-29），两个步骤入口不许各写一份。音频优先 `-c:a copy`（实测 opus→mp4 可行）；帧率解析只允许 `media_probe._fraction` 一份。别以为 RTX 3060 有 `av1_nvenc` 就能编 AV1 —— 硬件不支持，软编 47 分钟不可接受。
21. **手动切点别逼用户先跑 LLM**：`update_edl_segments` 在 EDL 不存在时要自动建骨架（旧版 400「请先点识别广告位」会挡住纯手动场景）；`detect_ads` 重新识别必须保留 `confidence=manual` 的段并统一重编号，否则用户辛苦标的片头片尾会被一次识别吞掉。播放器 / 精细时间轴不能依赖「已有区间」才渲染，否则加第一个切点前根本没法定位播放头。
22. **裁剪不能再写第二份编码决策**：`ad_cut_service.build_command` 曾写死 `libx264 crf20 + aac 192k`，绕开 `encode_service`，导致源片压到 390 kbps 后裁完成片又被 CRF 撑大几倍。三处重编码（第 6 / 11 步 / 裁剪）必须共用 `build_encode_args`；裁剪因 `-map [outa]` 是滤镜输出**必须** `audio_reencode=True`，不能 `-c:a copy`。另外 x264 的 `veryfast` 不是 NVENC 合法 `-preset`，预设要在服务层按编码器映射，别在入口透传。
23. **日志出口不要绑在任务线程上**：只在 `TaskManager._run()` 里替换 `sys.stdout`，会让裁剪 / 识别 / 文案这类同步 HTTP 操作的 `print` 只落 cmd；而 `sys.stdout` 是进程全局的，任务线程恰好在跑时这些日志又会混进任务日志，形成「一会儿 cmd 一会儿页面」。统一走 `core/utils/log_hooks.emit`，用 `_vl_capture` 标记防重；所有 core 模块的 `rprint` 必须从 `log_hooks` 导入（直接 `from rich import print` 是漏网点）。落盘改为共享锁的 append，别再长期持有 `'w'` 句柄。
24. **删广告段别只做前端**：只从表格里 filter 掉，下一次刷新会从 EDL 读回来、重新点「识别广告位」又会加回来；删光最后一段时后端若还坚持「没有有效的裁剪区间」就 400，用户根本存不进空 EDL。必须 `segments=[]` 合法 + `dismissed` 留档（按区间重合比而非精确秒 / id 匹配，用户可能先微调边界）+ 前端 dirty 保护三件一起做；但**非空 payload 全非法时仍要报错**，别把坏请求当成用户清空。预览 Range 断连的 `WinError 10054` 是 asyncio 回调噪音，只过滤 `_call_connection_lost` 这一类，别把所有 `ConnectionResetError` 一律吞掉。

---

## 12. 变更记录

| 版本 | 日期 | 涉及 FR | 改动摘要 | 验证 |
|---|---|---|---|---|
| v1.0 | 2026-11-09 | 全部 | 首次生成 as-built 规格，覆盖 6 个页面、11 个步骤、两套批量、全部 API/配置/全局规则 | 代码全量只读盘点；基线 `335 passed, 1 skipped` |
| v1.1 | 2026-11-10 | FR-12、G-17（新增）、第 7 章 | 修 `/v1/models` 拉取报 `CERTIFICATE_VERIFY_FAILED`：urllib 出站改用系统 CA + certifi 合并 context（不关校验），新增可选 `network.ca_bundle`；错误提示按证书/HTTP 状态/网络故障分类，不再一律劝用户切代理 | `362 passed, 1 skipped`；真实请求 zero.cat 从证书失败变为 `HTTP 401`（假 key 预期）；`check_single_source.js` clean；tsc + vite build |
| v1.2 | 2026-11-10 | FR-27（新增）、FR-12、第 7/8/9 章 | 新增「B站投稿文案」：第 1 步下载保存 `video_meta.json`；公式提炼自 bili-video-meta skill 成内置模板 `bili_meta`（提示词页可编辑）；流水线页卡片一次 LLM 调用出 3 标题+简介+10 标签 → `output/bili_meta.md`；任务名 +1（`bili_meta`）；讲者名池 `bili.speaker_names` 可追加 | `377 passed, 1 skipped`；prompt 渲染冒烟（变量替换/全角｜collapse）✓；`check_single_source.js` clean；tsc + vite build |
| v1.3 | 2026-11-10 | FR-27、第 7/8/9/11 章、附录 A | 标题生成改以**转录结果**为决定性一手素材（用户确认首选 `trans.srt` 中文译文）：多来源优先级、未超预算全文直送 / 超预算按时间均匀采样（头尾加权）+ 全局摘要；原视频元数据降级为次要参考、缺失不再阻断；prompt 新增一手素材段与取材铁律 5 条，自定义模板缺 `{{transcript}}` 时强制兜底注入；无转录 400 且前端禁用；新增 `bili.transcript_chars` / `transcript_samples`；`/context` 回转录统计；卡片显示主素材状态并支持手动/任务结束刷新 | `393 passed, 1 skipped`；真实归档冒烟（6301 字全文直送、prompt 9588 字符、金句/铁律/摘要齐全、主次顺序正确）；`check_single_source.js` clean；tsc + vite build |
| v1.4（仅文档） | 2026-11-10 | FR-28（新增·待实现） | 新增「广告位提醒」需求条目：针对「转录起点已进广告」，记录当前片 4 段赞助的词级空隙实测（2.50~3.09s）与窗口内画面切点位置，拟定「LLM 句子序号 → 词级候选窗口 → ffmpeg 切点/静音定点 → SponsorBlock 交叉校验」的分层边界算法，并预留阶段二自动裁剪的 EDL/字幕平移/交叉淡化约束。**边界信号选型尚未拍板，本版不实现代码** | 只读实测：`cleaned_chunks.json` 词级空隙 4/4 命中；`ffmpeg scene>0.08` 4/4 窗口有切点；`silencedetect` 在边界检出 1.35s 静音；SponsorBlock API 本片 404（对照视频 `aircAruvnKk` 200 有数据） |
| v1.5 | 2026-11-10 | FR-28（实现）、第 7/8/9/11 章、附录 A | 用户最终选定 **A 画面切点融合**，并确认成片方案「用 `output_sub.mp4` 按 EDL 取补集再 concat」。新增：LLM 按**行号**标广告段（prompt 模板 `ad_detect`、任务链 `ad_detect`）、ffmpeg 场景切点定点（取窗口内最后一个切点 − pad）、`ad_slots.md` + `ad_segments.json`（可手工改、原子落盘）、视频预览（Range 支持）+「跳到/取当前」微调 + 裁剪源下拉、单条 `trim+concat` 帧级精确裁剪成 `output_no_ads.mp4`（30ms 音频淡化、原片不动）；`ad.*` 9 个配置项；不进 11 步注册表 | `419 passed, 1 skipped`（新增 `test_ad_slots.py` 26 例）；真实转录+ffmpeg 冒烟 4/4 命中切点、起点比转录提前 1.47~1.67s；20s 样片端到端裁剪 20s→15.015s；`check_single_source.js` clean；tsc + vite build ✓ |
| v1.6 | 2026-11-10 | FR-28（精细时间轴）、第 8/11/12 章、附录 A | 用户反馈「跳转切点时原生时间轴范围太大，一点跳好几秒，要剪映那种帧级切换」。新增 `FrameScrubber`：局部窗口 ±1/±3/±10s（1px≈10ms）、点「跳到」自动居中并聚焦、窗口越界跟随、逐帧/0.1s/1s 按钮 + 方向键/Shift/Alt/空格、帧网格吸附（默认开）、轴上画正文末词/画面切点/广告开口/裁剪区间（图例只列窗口内可见项）、时间码精确到 ms + 帧号；新增 `GET /api/ad/frame-rate` + `probe_frame_rate()`（ffprobe 实测，本片 23.976fps），页面区分实测/手选/默认；修三个精度坑（连按漂移、播放中取当前 ≤50ms 误差、元数据未加载完 seek 丢失） | `428 passed, 1 skipped`（新增 9 例：帧率分数解析/回退/坏输出/接口白名单）；真实 `output_sub.mp4` 实测 23.976fps；`check_single_source.js` clean；tsc + vite build ✓；无头浏览器截图确认布局无重叠/截断 |
| v1.7 | 2026-11-10 | FR-29 / FR-30（新增并实现）、FR-12、第 7/8/9/11/12 章、附录 A | 用户反馈「压制字幕后视频大很多，码率/总比特率/帧率要跟原片一致」+「想自己加切点切片头片尾/中间」。① 新增 `core/utils/media_probe.py`（一次 ffprobe 读全）+ `core/services/encode_service.py`（编码参数唯一决策点），第 6/11 步改为调用服务层：视频码率 `-b:v/-maxrate/-bufsize` 按原片、显式 `-r`、原 `pix_fmt`、音频 `-c:a copy` 直通（配音混音时 aac 按原码率），`encode.*` 5 个手改配置；`probe_frame_rate` 改为薄转发消除第二份帧率解析。② 自定义切点复用 EDL：无 EDL 自动建骨架、`detect_ads` 保留手动段并重编号、`render_md` 分开渲染、页面新增「+ 添加切点 / 切掉片头到当前 / 从当前切到片尾」+ 删除 + 表格条件渲染；播放器和精细时间轴不再依赖「已有区间」才渲染；`GET /api/ad/media-info` 返回 fps + 时长（`/frame-rate` 保留别名） | `447 passed, 1 skipped`（新增 `test_encode_service.py` 20 例 + 手动切点 5 例）；**真实双字幕 20s 端到端**：视频 394152 bps（目标 408326）、总 530335 bps（原 521646，+1.7%）、帧率/分辨率一致、抽帧确认中英字幕均烧上，外推全片 ≈183 MB（旧版 748 MB）；无 EDL 三段手动切点补集 `[(10,100),(120,280)]`；`check_single_source.js` clean；tsc + vite build ✓；无头浏览器截图确认按钮行布局 |

| v1.8 | 2026-11-10 | FR-29（扩展）、FR-31（新增并实现）、第 7/11/12 章、附录 A | 用户实跑「按此裁剪」反馈「成片还是很大，好像不是原视频参数」+「日志又在 cmd 里不在页面上，需要统一」。① **根因**：`ad_cut_service.build_command` 写死 `libx264 crf20 + aac 192k`，是绕开 `encode_service` 的第二份编码决策；改为 `cut_video → _encode_args_for → build_encode_args(audio_reencode=True)` 注入源片码率/帧率/音频参数，`match_source=false` 才退回 `ad.cut_*`；`preset` 在服务层按编码器归一（NVENC 不认 `veryfast`）；摘要日志打印实际编码方案。② **日志统一**：新增 `core/utils/log_hooks.py`（`emit` 用 `_vl_capture` 防重），13 处 `from rich import print` 收敛到统一 `rprint`；`subprocess_log._emit` 补一路 emit；`server/logbus.py` 增加共享锁 append + `publish_external`，`TaskManager` 不再长期持有 `'w'` 句柄；startup 注册唯一 sink；4 个落盘用例随新机制改写；页面日志空态文案更新 | `466 passed, 1 skipped`（新增 `test_log_hooks.py` 11 例 + 编码参数/preset 8 例；`conftest` 加 autouse 日志目录隔离）；**真实 ffmpeg 20s 裁剪端到端**：源 505 kbps → 成片 547 kbps（+8.3%）、视频 439185 bps、帧率一致、耗时 3s；接口级 44 条日志同时进 BUS 与 `pipeline_latest.log`，任务线程不重复；`check_single_source.js` clean；tsc + vite build ✓ |
| v1.9 | 2026-11-10 | FR-32（新增并实现）、FR-28、FR-30、第 8/9/11/12 章、附录 A | 用户反馈「识别出的广告位切点也要能手动删除」+「cmd 里常出现 WinError 10054 是什么情况」。① **删除闭环**：表格每行（识别段 + 手动段）可删；`segments=[]` 合法清空、非空却全非法仍 400；被删的识别段记入 EDL `dismissed`，重新识别按区间重合 ≥60% 跳过，页面「恢复已删除」一键加回并立即保存；前端 `adDirtyRef` 防止刷新把删空的列表读回来；`render_md` 标注已删除段数。② **断连噪音**：`<video>` Range 预览被浏览器取消时 Windows Proactor 回调抛 `ConnectionResetError [WinError 10054]`，`install_disconnect_noise_filter` 只吞 `_call_connection_lost` 这一类，其他异常照常上报 | `478 passed, 1 skipped`（新增 `test_ad_slots.py` 7 例 + `test_server_disconnect.py` 5 例）；`PUT /api/ad/edl` 空列表与恢复覆盖接口级验证；`check_single_source.js` clean；tsc + vite build ✓ |

| v1.10 | 2026-11-11 | FR-24/26、第11章、附录A | 用户报告原片09:34–09:40缺字并要求全片排查。当前36:41视频ASR尚有4处内部长空档79.113s（02:49.804–03:09.777、09:40.282–10:05.311、27:19.159–27:38.761、35:25.362–35:39.871），离线复核均有真实讲话；另有提示词回声/内容提前错位。源头在ASR结果及补转未验收完整覆盖，非翻译/烧录丢字。补转临时清除initial_prompt/hotwords/prefix、词级覆盖含首尾验收、有声残留一次非批处理重试、未解决明确告警；提示词回声候选可靠恢复才替换，词内空洞重转整段避免乱序。只读自检改扫当前checkpoint，去掉历史区间硬编码，支持--start/--end，无候选不加载模型。保留当前产物与旧断点，诊断恢复文本单独交付 | `491 passed, 1 skipped`（新增13例），守卫clean，3文件lexer通过；真实4区间WhisperX+align复核，02:40段首轮仍漏约13s后触发非批处理兜底；9:38.7–10:05.3段恢复3句且无≥3s残留。已复核低分候选与去广告时间映射；不是全片逐字人工听写，错位和短漏词仍需核对 |

| v1.11 | 2026-11-11 | FR-22/24/26、配置表、附录A | 用户提供四份原版代码要求同时修漏词和时间错位。同一音频同VAD窗口A/B复现：叙述领域提示+热词导致跳前文/复述提示，强制对齐把残留尾句提前约20s。ASR服务统一恢复无提示、temperature0、beam5、重复限制0；decode版本/参数入断点指纹。字幕参考原版完整文本顺序字符匹配优先，内部空格同标准化兼容合并词/缩写，重复句不复用已消费位置；旧锚点有边界保留容错；毫秒统一round。参考第5步代码补非空译文和源文保真守卫。实际重转整片并复用499条旧译文，补齐183条，独立682条中英字幕+40s烧录预览；既有主字幕/断点/成片保留，原输入manifest已恢复且下载测试增加临时cwd隔离 | `502 passed, 1 skipped`；守卫clean。新词表5310行，零倒序；原4处79.113s空档消除，唯一剩余≥3s空档1952.125–1955.467有声7.6%静音。Number four 900.301→922.252，This is different 1540.752→1562.829；682条无空/无零时长，完整文本投影对新词时间最大误差0.0s（只证明文本映射一致，不代表声学绝对零误差）；字幕预览40s原片帧率24000/1001，原编码决策服务复用 |

| v1.12 | 2026-11-11 | FR-07/08/26、G-04 | 阶段一标题相邻新增「强制重跑一阶段」：与完整跑共用stageRange，确认显示实际范围与模型费用，提交force=true复用统一清理；所有运行入口共享提交锁。TaskManager在同一RLock内检查空闲、强制输入预检/清理再启动，修正忙碌请求先删产物再409的竞态；无输入/歧义400且不清理。核清阶段一恢复能力并更正“全部步骤可断点”的表述：转录/纠错/翻译有分段批断点，切句/对齐仅复用部分缓存，烧录须重编 | `511 passed, 1 skipped`（新增9例）；single-source及3文件语法通过；tsc+vite build通过；隔离浏览器验证确认/取消不请求、烧录开关范围、running/paused/无输入禁用，全部API拦截替身，未触发真实流水线；当前切句任务不重启、不清产物 |
| v1.13 | 2026-11-11 | FR-25/26 | 用户实跑遇到 `429: maximum 10 requests in 1 minutes`（new_api 网关），原逻辑把 429 归 transient 走 `2**n` 指数退避——面对按分钟限流的网关只会连续撞墙、烧完 retries_per_model 后误切模型。① 429 从 transient 拆出独立类别 `rate_limit`；新增 `llm_service.rate_limit_wait`（决策单一来源）：解析 `Retry-After` 头（秒/HTTP日期）→ 消息中的限流窗口 `maximum N requests in M minutes` → 等整个窗口+1s → `try again in Xs` → 兜底 30s，上限 120s。② `ask_gpt` 重试循环：限流等待**不消耗** `retries_per_model`（模型没坏只是节奏太快），每模型最多 3 次等待（`MAX_RATE_LIMIT_WAITS`）后切下一模型；等待时打印剩余等待次数。③ `check_single_source.js` 新增 `rate-limit-status-code` 规则（429 字面量仅允许出现在 llm_service.py）；`conftest.status_error` 扩展 message/headers 参数（唯一实现） | `520 passed, 1 skipped`（新增 5 例：预算不消耗、3 次封顶切模型、裸 429 兜底、`rate_limit_wait` 六种解析、429/500 分类区分）；`check_single_source.js` clean（含新规则）；retries=1 配置验证限流重试不再被预算吞掉 |
| v1.14 | 2026-11-11 | FR-10/26 | 用户报告「保存历史清空不了 output/output_sub.mp4，文件一直被 python 使用」。根因：广告位卡片 `<video>` 只要 output_sub.mp4 存在就自动挂载流式加载，`FileResponse` 读句柄常驻，Windows 禁止删除/改名被占用文件；归档 `_transfer` 的 PermissionError 降级路径「复制+删源」中 `os.remove` 失败被**静默 pass**——文件复制进归档却永远留在 output/。三层修复：① 前端 `releasePreview()`（pause + 撤 src + `load()` 中止流）挂到 `submitRun`（force 重跑清产物同样受此锁）与 `doSaveHistory`，`previewOff` 状态卸下播放器；② 后端 `_remove_with_retry`（6×0.5s）等前端断开后句柄释放；③ 仍失败记入 `stats["locked"]` + 日志明确提示 + `history_saved` 事件带 `locked` 数 + 页面警告「刷新后再保存一次」，绝不静默。`ws.ts` BusEvent 补 `locked` 字段 | `522 passed, 1 skipped`（新增 2 例：删源重试期间释放→计入 moved；永久占用→复制进归档、源留原地、locked 上报、其余文件不受牵连）；`check_single_source.js` clean；tsc + vite build 通过 |
| v1.15 | 2026-11-11 | FR-13/14/15/21/22/26、G-01/05/06、配置表 | 用户澄清术语表概念：`vl/terms` 放几套基础库、每个新文件勾选 0~N 套作底板，4.1 带底板生成「基础 + 新抽」的任务术语表，4.2 翻译就用 output 目录下这份。核实现状有 3 处偏差：① **4.2 重新 `load_active_terms()` 只读底板**，4.1 新抽的术语从未进入翻译 prompt；② `active_term_names()` 把显式 `[]` 回退成 `["default"]`，用户当前 config 正是 `active: []`，取消全选后 default 仍偷偷生效；③ 4.1 用 `extend` 拼接，同 src 会在 prompt 里出现两行互相冲突的术语指令。修复：新增 `merge_terms`（唯一去重/覆盖规则，后者 = 底板优先、保留首次出现的 src 拼写）、`load_job_terms`（读 4.1 的 per-job 产物，相对 cwd 每任务一份，缺失/损坏回退底板，合法空表不回退）、`terms_signature`；4.2 与实验场（L1/L2/L3、shared 提示词预览、`_sample_shared_context`）统一改用 `load_job_terms`，summary 预览仍用底板；`chunks_fingerprint` 加 `extra`，术语变化作废旧译文断点；`get_summary_prompt` 在 0 勾选时不再注入空 Existing Terms 节；L2 Alert 显示任务术语条数；`check_single_source.js` 新增 3 条守卫（任务术语表唯一读取器 / 路径常量唯一 / 4.2 禁止再用底板） | `532 passed, 1 skipped`（新增 10 例：0 勾选、键缺失兼容、产物 = 基础+新且不重复叠加、缺失/损坏回退、合法空表不被覆盖、merge 去重、术语签名、空术语 prompt、4.1 写盘去重且保 theme）；`check_single_source.js` clean（含 3 条新规则）；tsc + vite build 通过；未跑真实 GPU 流水线。**成本提示**：术语变化会让 4.2 的 GPT 缓存与断点一起失效，下次第 4 步全额重新计费，已在 FR-13 标注 |
| v1.16 | 2026-11-11 | FR-13/26、附录A | 用户要求「任务术语表能手动勾选回写进指定基础库」，并报告术语页「新建」不好用。排查新建：① `setEditing('')` 用空串表示新建却被 `if (editing)` 判为 false，点击毫无反应；② 编辑态没有库名输入框，保存必然被名称正则 400 且 `if (!editing) return` 静默不发请求；③ `PUT /terms` 整库覆盖，同名新建静默覆盖旧库。实现：`translate_service` 新增 `validate_term_set_name`（校验从 API 上移，新建/编辑/回写三入口共用）、`create_or_replace_term_set`（唯一整库写入，同名 create 抛 `FileExistsError`→409，前端二次确认后 overwrite）、`write_back_terms`（只收 src 列表、服务端重读任务术语表取值，复用 `merge_terms` 按 src 去重、同名默认跳过保留人工译文、`save_term_set` 原子写，返回 added/updated/skipped）、`load_job_terms(allow_fallback=False)` 严格模式（绝不把回退底板当成新抽术语）；`GET /terms` 扩展 `job{available,terms,plate_srcs,sets}`（避开 `GET /terms/{name}` 吞掉子路径），新增 `POST /terms/writeback`；TermsTab 加回写 Modal（目标可选已有/新建、勾选表格标来源与「已存在·跳过」、默认只勾 4.1 新抽）+ 库名输入；回写只写 `terms/*.json`，不动任务术语表 → 当前任务翻译 / 断点指纹 / GPT 缓存零影响零费用，下次 4.1 生效。顺带修 `PUT /terms/active`：`update_key` 缺键返回 `False` 时原接口仍回 200（勾选静默失效），现在 500 明确提示。守卫新增 `term-set-write-decisions`（名称校验 / 整库写入 / 回写决策唯一归属） | `548 passed, 1 skipped`（新增 16 例：回写新建库 / 冲突跳过 / 覆盖 / 坏目标拒绝 / 无 4.1 产物 / 非法名 / 任务术语表不变 / 同名 409 / 接口契约 / 激活静默失败）；`check_single_source.js` clean（含新规则）；tsc + vite build 通过，dist 已重建；未跑真实 GPU 流水线 |
| v1.17 | 2026-11-11 | FR-28/26、接口表、G-01 | 用户反馈「广告位 / 自定义切点加载的不是压制过字幕的视频，没法边看字幕边切」，随后追加「视频这块能否选择想看的视频和取消展示」。抽帧核对：真实 `output_sub.mp4` 300s 处有双语烧录字幕、原片无字幕；根因是前端 `adSource` 只要旧值仍在可用列表就一直保留，烧录版生成后粘在无字幕原片上，且选源下拉只在「已有切点」时才显示。① 后端唯一决策 `ad_cut_service.default_source(sources=...)`：有烧录版优先 `burned`，否则取第一个可用源；`list_sources` 每项加 `subtitled`（烧录 / 配音 = true，原片 = false）与 `version`（`mtime_ns-size`）；`GET /api/ad/context` 用**同一份 sources 快照**算 `default_source`，不二次扫描；空产物（0 字节）跳过，避免把正在写入的文件当可用源。② 前端：选源下拉常驻视频正上方并标字幕状态、无字幕时一键切回；未手动选择时每次刷新 context 按 `default_source` 自动纠正（烧录版一生成就切回带字幕版），用户明确选过且仍可用的源始终保留，换输入 / 归档时 `resetAdTask()` 重置；切源与同名重烧（version 变化）在 `loadedmetadata` 恢复播放头；`adPreviewUrl` / `getAdMediaInfo` 带 `v=<version>` 防浏览器缓存旧画面与旧 fps。③ 新增「显示预览」开关（localStorage 持久化）：关闭时 `pause + removeAttribute('src') + load()` 卸载播放器并释放文件占用，再打开恢复之前位置；用户主动隐藏与 v1.14 的「归档 / 运行临时断流」彻底分离（`previewVisible` vs `previewOff`），任务运行 / 归档期间 `adTaskBusy` 禁止重新加载以免抢占待写文件，归档成功由 `history_saved` 复位临时断流但**不擅自打开用户隐藏的预览**；隐藏不删切点，裁剪按钮只依赖「有可用源」而非播放器可见，确认框写明实际裁剪文件。守卫新增 `ad-default-source`（默认选源决策唯一归属） | `554 passed, 1 skipped`（新增 6 例：烧录版优先与 subtitled 标记 / 配音·原片·空目录兜底 / 0 字节产物跳过 / 同名重烧 version 变化 / context 单快照不二次扫描 / 白名单 Range 返回选中文件）；`check_single_source.js` clean（含新规则）；tsc + vite build 通过，dist 已重建；真实抽帧与 HTTP Range 已核对；未跑真实浏览器交互与 GPU 流水线 |
| v1.18 | 2026-11-12 | FR-29/26、配置表、附录A | 用户反馈「和原视频一样的码率，很多地方变虚，镜头切换会虚一段时间」，并明确重点对比 `原片 → output_no_ads.mp4`（去广告成片），直接压制的课件线也需要。① 真实三文件逐包实测定位：原片 AV1 是 VBR（峰值 1336k = 3.78× 均值、关键帧跟着场景走），旧 `-b:v 353k -maxrate 353k -bufsize 706k` 把瞬时码率也锁死 → 最复杂 15 秒原片 1150k / 产物仅 460k、40% 秒数贴上限；NVENC 固定 GOP 250 帧（10.43s）不检测场景切换，切点后 PSNR 从 41.6 掉到 36~39 dB；去广告成片是 `output_sub.mp4` 的第二次有损转码，旧逻辑还探测它把目标码率降到 324k。② 30s 样片 8 组对照：当前参数 40.05 dB / 1.39 MB，CQ32 46.44 dB / 2.55 MB（+6.4 dB、约 1.8× 体积），缩短 GOP 或强制切点 IDR 只多 0.85 dB 却涨 10~32% 体积，x264 crf23 48.01 dB 但 47 分钟软编不可接受。③ `encode_service` 默认改恒定质量 `-rc vbr -cq 32 -b:v 0` + 空间/时间 AQ + lookahead32 + multipass + `cq_preset=medium`，不传 maxrate/bufsize；旧 `bitrate` 模式逐字保留可回退；`quality_profile="cut"` 用 `cut_cq_level=30` 单独放宽 2 档，且 `_encode_args_for` 优先探测**原始输入视频**而非已压产物；新增 `encode.rate_control / cq_level / cut_cq_level / cq_preset / cq_gop` 手改配置（G-08）。④ 守卫新增 `cq-encode-decisions` | 本版新增 10 例（CQ 参数 / cut 放宽 / x264 CRF 换算 / 非法配置回退 / 探测原片 / 旧 bitrate 逐字不变）；守卫 clean；tsc + vite build 通过；**未跑全片重编码与真实 GPU 长任务**，CQ 体积约 1.8× 需用户实跑确认 |
| v1.19 | 2026-11-12 | FR-12/25/26、G-14、接口表 | 用户反馈「任务链里链的模型为空时提示 `Error: 链 test 不能为空`，任务链能添加但需要能删除，不然一直这样报错」。根因：`ModelsPage.tsx` 的删除按钮在 `usedBy.length > 0` 时被禁用（G-14 旧约束），而新建链默认 `models: []`、被任务绑定的链又必须先解绑 → 空链既存不了也删不掉，死循环。① 删除被绑定链时自动把引用它的任务改绑到 `default` 链（被删的正是默认链则取剩余第一条），确认框列出受影响任务；最后一条链禁止删；空链橙色标记、保存前置校验拦下空链/悬空绑定并给出可操作指引、新建链提示需加模型；删除仍是草稿操作，点「保存链与绑定」才落盘（可撤销）。② `PUT /api/models/chains` 空链 400 文案补「添加模型或删除该链」，仍拒绝悬空绑定 / 零条链 / 缺 default / 引用不存在模型；G-14 改为「被任务绑定的链可删除但自动改绑」。③ 同步清场 v1.18 遗留：8 个旧编码断言改为 `rate_control=bitrate` 回归并补 CQ 新用例，`test_ad_slots` 裁剪断言改 CQ30 且验证探测原片 | `572 passed, 1 skipped`（新增 `test_models_api.py` 7 例 + 编码/裁剪 10 例）；守卫 clean；tsc + vite build 通过；未跑真实浏览器交互 |
| v1.20 | 2026-11-12 | FR-10/28、接口表、附录A、守卫 | 用户报告「保存历史提示已归档 45 个文件，另有 1 个被视频预览占用未能清出 —— 刷新页面后再保存一次即可」，即 v1.14 的补丁没根治。根因：Starlette `FileResponse` 在 `<video>` 掐断 Range 请求时 cancel 流式任务，`async with anyio.open_file()` 的 `aclose()` 也是 await、在已取消 scope 里被立刻取消 → Windows 留下无 `FILE_SHARE_DELETE` 的读句柄；且 `history_saved` 立刻用旧 sources 重新挂载播放器把残留文件再锁一次。① 新增 `server/preview_stream.py`：`PreviewFileResponse`（单段/尾部 Range、416、If-Range、HEAD；同步 `finally: f.close()`；句柄注册表 + generation 竞态拦截；主动关闭后流安静收尾），`/api/ad/preview` 改用它并支持 HEAD，新增 `POST /api/ad/preview/release`。② 前端归档 / 运行提交前 `await releasePreviewStreams()`；后端归档 job 开头、force 重跑清理前、删源每次重试都调 `close_preview_streams()`（`archive_current(on_retry=...)`，10×0.5s）。③ 归档结束等 `refreshAd()` 拿回新 sources 才恢复预览（`archiveHoldRef` / `previewHold`），不提示用户刷新；残留由 `retry_locked` 后台按 size+mtime_ns 签名安全清出（同名新产物绝不误删、释放钩子只前 3 轮），完成发 `history_cleaned` 自动解除挂起。④ 守卫新增 `preview-stream-release` | `596 passed, 1 skipped`（新增 24 例：`test_preview_stream.py` 15 + 归档 on_retry/retry_locked/「预览流占用时直接归档一次清出」集成 6 + history_api 3）；`check_single_source.js` clean（含新规则）；`_lex.js` 5 个改动文件通过；tsc + vite build 通过、dist 已重建；未跑真实浏览器交互与 GPU 任务 |
| v1.21 | 2026-11-12 | FR-13、1.5 术语表、接口表、附录A、守卫 | 用户反馈「基础术语库页面只看到 `default / 从上次 4.1 输出导入 / 13`，想在术语表页面看到所有的术语」。现状：`#/terms` 只列各库**词条数**，词条要逐库点「编辑」才看得到，4.1 新抽术语更只藏在回写 Modal 里；真实工作区基础库 13 条（NLP 建模）+ 任务术语表 15 条（觉知养育）互不可见。① 决策层新增 `translate_service.term_catalog()`：所有 `terms/*.json`（含未激活）+ 严格模式任务术语表的**并集**，按 `src.strip().lower()` 合并成一行；`effective` 只认 4.2 真正注入的值（有 4.1 产物用任务表、否则用当前勾选底板），未生效词条仍显示自身译文但标「未生效」；每行给 `sets/variants`（逐库取值与激活状态）、`in_plate/in_job/job_fresh`、`conflict`（多库译文不一致）、`drift`（4.1 冻结值与当前所有库都不同，可视化「勾选在 4.1 冻结」）。合并/去重仍复用 `merge_terms`，无第二份「生效」判定；纯只读，不写盘、不调 LLM、零费用。② `GET /api/translate/terms` 扩展 `catalog{rows,stats}`（不新增子路由，避免被 `GET /terms/{name}` 吞掉）。③ `TermsTab` 新增常驻「全部术语」总表：统计标签、搜索、范围筛选（生效/未生效/4.1 新抽/冲突漂移）、按来源筛选、分页、展开逐库对照、点库名标签直接进编辑；可一键收起。④ 守卫新增 `term-catalog-view` | `604 passed, 1 skipped`（新增 8 例：`test_term_catalog.py` 6 + `test_terms_api.py` 2，覆盖并集/未激活/冻结值与漂移/0 勾选 + 合法空任务表/坏库/纯只读）；`check_single_source.js` clean（含新规则）；`_lex.js` 4 个改动 py 通过；tsc + vite build 通过、dist 已重建；真实工作区实跑 `term_catalog()` 得 28 行（13 基础库 + 15 任务表，15 生效）；未跑真实浏览器交互 |

| v1.22 | 2026-11-12 | FR-13、1.5 术语表、接口表、守卫 | 用户澄清术语模型（截图里总表显示 0，且要求「`vl\terms\default.json`、`vl\terms\JillianTurecki`、`vl\output\log\terminology.json` 这两个位置的术语表都要展示」）：`terms/` 是按分类 / 人物 / 行业分套存放的**基础（汇总）库**；每次翻译新文本把勾选的基础库当底板，与新文本重新抽取的术语合并去重写进 `output/log/terminology.json`；4.2 翻译只用这张完整表；它还要能勾选回写进用户指定的基础库。核对确认**运行链路 v1.15~v1.21 已完全符合该模型**（4.1 = `merge_terms(LLM 新抽, 勾选底板)`、4.2 只读任务表、回写只写基础库），差的只是页面可见性：v1.21 的并集总表按 `src` 折叠，同一词条在多套库的取值只能展开看，读不全某一套库；截图 0 条则是后端进程仍跑旧代码、前端对无 `catalog` 字段做了空兜底。① `term_catalog()` 在同一次只读遍历里新增 `sources[]`：每套 `terms/*.json` + `output/log/terminology.json` 各一项，含路径 / 激活 / 可用 / 条数与**文件内全部词条**（保持顺序，仅用 `merge_terms` 规范化与库内去重），`rows/stats` 契约不变，仍是零费用纯只读。② `#/terms` 的「全部术语」卡片改 Tabs：默认「任务术语表（4.2 使用）」逐条完整列出并可直接勾选行→选目标库回写（复用同一 Modal 与 `write_back_terms`，不造第二份写入）；每套基础库各一页完整列出、标勾选状态与 4.2 生效对照、一键进编辑；末页保留并集总表。③ 页顶用大白话写清「基础库 → 勾选底板 → 4.1 生成任务表（底板 + 新抽、按 src 去重、底板优先）→ 4.2 只用这张表 → 勾选回写」链路与实时条数；勾选基础库时明确提示**必须重跑 4.1**（会重新调 LLM 且作废 4.2 断点 / GPT 缓存），不假装即时生效。④ 守卫新增 `term-source-file-labels`，`term-catalog-view` 口径扩到按来源清单 | `607 passed, 1 skipped`（新增 3 例：同 src 多库各自完整取值 / 无 4.1 时任务表来源为空且不塞回退底板 / 接口 `catalog.sources` 契约；另在既有只读与坏库用例补断言）；`check_single_source.js` clean（含新规则）；`_lex.js` 4 个改动 py 通过；`tsc --noEmit` + `vite build` 通过、dist 已重建；真实工作区实跑得 3 份来源（JillianTurecki 15 / default 13 / 任务表 15）与 28 行并集、15 条生效；未跑真实浏览器交互与 GPU 任务 |

> 以后每次按本文档改完程序，AI 必须在此表追加一行，并同步更新被改动的 FR 条目。

---

## 附录 A 代码索引（按目录）

| 区域 | 文件 | 关键定义 |
|---|---|---|
| 注册表 | `core/pipeline.py` | `Step`、`STEPS`、`get_step`、`get_steps`、`pipeline_status` |
| 服务层 | `core/services/run_service.py` | `resolve_steps`、`force_clean`、`default_run_range`、`burn_subtitles_enabled`、`clamp_end_index` |
| | `core/services/asr_service.py` | `decode_settings`、`build_asr_options`、`DECODE_VERSION`（主转录解码策略及断点指纹） |
| | `core/services/llm_service.py` | `classify_error`、`on_failure`、`check_response`、`probe`、`probe_pool` |
| | `core/services/archive_service.py` | `archive_current`、`_remove_with_retry`、`retry_locked` |
| | `core/services/bili_meta_service.py` | `load_transcript`、`_read_transcript_lines`、`full_text`、`sample_lines`、`transcript_brief`、`load_video_context`、`suggest_speaker`、`_valid_bili_meta`、`generate_bili_meta` |
| | `core/services/ad_service.py` | `parse_srt`、`read_transcript`、`numbered_transcript`、`normalize_ads`、`validate_ads`、`find_scene_cuts`、`find_silences`、`refine_segments`、`sponsorblock_segments`、`_is_manual`、`_slot_overlap`、`is_dismissed`、`_merge_dismissed`、`render_md`、`save_edl`、`load_edl`、`update_edl_segments`、`context`、`detect_ads` |
| | `core/services/ad_cut_service.py` | `list_sources`、`default_source`、`resolve_source`、`probe`、`probe_frame_rate`、`media_info`、`build_keep`、`build_command`、`_encode_args_for`、`cut_video` |
| | `core/services/encode_service.py` | `load_config`、`_encoder_for`、`build_encode_args`（`preset` 按编码器映射）、`DEFAULTS`、`_NVENC_PRESETS`（FR-29 编码参数唯一决策点） |
| | `core/services/translate_service.py` | `split_chunks`、`load_active_terms`、`merge_terms`、`load_job_terms`、`term_catalog`、`write_back_terms`、`build_shared_context`、`TranslationCkpt`、`DEFAULTS` |
| 步骤 | `core/_1_ytdlp.py` | `pick_js_runtime`、`probe_cookies`、`cookies_have_login`、`build_ydl_opts` |
| | `core/_2_asr.py` | `transcribe`、`_audio_fingerprint`、`_load_or_save_segments`、`_save_seg_result` |
| | `core/_3_1_split_nlp.py` / `_3_2_split_meaning.py` / `_4_1_summarize.py` / `_4_2_translate.py` / `_5_split_sub.py` / `_6_gen_sub.py` / `_7_sub_into_vid.py` / `_8_1_audio_task.py` / `_8_2_dub_chunks.py` / `_9_refer_audio.py` / `_10_gen_audio.py` / `_11_merge_audio.py` / `_12_dub_to_vid.py` | 各步主函数 |
| 质量 | `core/asr_correct.py` | `correct_transcription`、`_unify_entities`、`_hotword_suggestions`、`_llm_review`、`apply_corrections`、`_ckpt_delete` |
| | `core/utils/subtitle_time.py` | `get_sentence_timestamps`、`_find_anchor`、`_build_groups`、`align_timestamp`、锚点常量 |
| | `core/asr_backend/vad_gap_fill.py` | `NoVad`、`fill_gaps`、`find_uncovered_gaps`、`speech_ratio`、`transcribe_unprompted`、`_unprompted`、`_sequential_retry`、`prompt_echo`、`looks_like_hallucination` |
| | `core/translate_lines.py` | `translate_lines`、`normalize_sub_keys`、`valid_translate_result` |
| 工具 | `core/utils/ask_gpt.py` | `ask_gpt`、`_call_once`、`_load_cache` |
| | `core/utils/llm_profiles.py` | `load_llm_config`、`get_profile_chain`、`mark_key_dead`、`parse_key_file` |
| | `core/utils/config_utils.py` | `load_key`、`update_key`、`CONFIG_PATH` |
| | `core/utils/job_context.py` | `workspace_for`、`JobState`、`Job`、`delete_step_artifacts` |
| | `core/utils/subprocess_log.py` | `run_logged`、`_emit`（回灌 + `log_hooks.emit`，FR-31） |
| | `core/utils/log_hooks.py` | `register`、`unregister`、`emit`、`plain`、`rprint`（FR-31 全局日志出口 / 唯一决策点） |
| | `core/utils/media_probe.py` | `probe_media`、`_fraction`、`empty_info`（FR-29 原始参数探测，帧率解析唯一来源） |
| | `core/utils/net_policy.py` | `get_policy`、`ordered_modes`、`ssl_context`、`_opener`、`urllib_opener`、`urllib_fallback_opener`、`network_error_hint` |
| | `core/utils/words_store.py` / `media_files.py` / `decorator.py` / `onekeycleanup.py` | 词表 / 输入定位 / 守卫 / 清目录 |
| 后端 | `server/app.py` | `create_app`、`RunRequest`、`_audio_only`、`install_disconnect_noise_filter` |
| | `server/preview_stream.py` | `PreviewFileResponse`、`_parse_range`、`open_stream`、`close_preview_streams`、`active_stream_count`（预览读句柄唯一登记/释放点） |
| | `server/task_manager.py` | `TaskManager`、`_StreamCapture`（`_vl_capture` 防重标记）、`open_run_log`、`_rotate_logs` |
| | `server/logbus.py` | `LogBus`、`BUS`、`append_log_file`、`publish_external`、`LOG_FILE_LOCK`（FR-31 统一落盘） |
| | `server/{input,history,settings,models,translate,batch,restart,bili,ad}_api.py` | 各组路由 |
| 批量 | `core/batch_engine.py` / `core/job_runner.py` | `BatchEngine` / 子进程入口 |
| 前端 | `frontend/src/App.tsx` | hash 路由、重启/停止 |
| | `frontend/src/pages/*.tsx` | 6 个页面 |
| | `frontend/src/api.ts` / `batchApi.ts` / `translateApi.ts` / `ws.ts` / `inputDraft.ts` | 接口、事件、草稿持久化 |
| 守卫 | `check_single_source.js` / `_lex.js` / `check_vad_gaps.py` | 分层守卫 / 语法检查 / VAD 自检 |

## 附录 B 命令速查

```bash
# 启动（项目根）
python -m server

# 全量单测（本会话可用解释器）
PYTHONPATH="C:/Users/happy/.workbuddy/binaries/python/vltest_deps;D:/code/vl" python3.exe -m pytest test/ -q

# 分层守卫 / 语法自查
node check_single_source.js
node _lex.js core/services/run_service.py

# 前端
cd frontend && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/vite build

# VAD 空档自检（只读；--fill 需 GPU）
python check_vad_gaps.py
```

相关文档：`docs/batch-phase1.md`（批量期1交付说明）、`README.md`（上游开源项目说明）、`test/README.md`（测试隔离约定）、`.workbuddy/memory/`（项目工作日志与长期记忆）。
