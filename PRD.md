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

AI 侧**严格按 A → B → C 三阶段顺序执行，不得跳步，不得把 A 挪到 B 之后**（用户 2026-11-15 明确要求：按需求开发，不是开发完补需求；v1.35 固化为强制门）：

**A. 需求先行（写任何代码 / 测试 / 配置文件之前，必须先落盘）**

1. 读对应 FR、第 2 章 G 规则、第 11 章不要回退清单、第 12 章最近变更记录；按附录 A **grep 核对现状**（文档可能与代码不同步，以代码为准；不一致先提醒用户）。
2. 在 PRD 写清「需求/任务」：更新或新增对应 FR 的**当前行为 / 约束（不变式）/ 验收要点**（新需求标 `FR-xx（待实现）`）；性能类改动写明本机硬件边界（RTX 3060 12GB / 6C12T / 16GB）。
3. 在第 12 章**先追加一行变更记录**，此时只填 `版本 | 日期 | 影响章节 | **需求/任务**：…`，`**执行/结果**` 与验证列一律写「待填（开发完成前不得预填）」。
4. 建任务清单（需求 → 开发 → 验证/回填），按序推进。
   > **门禁**：PRD 需求行 + 变更记录行未落盘前，禁止编辑任何 `.py` / `.ts` / `.tsx` / `.json` / `config.yaml`、禁止新建测试文件。线上报错的紧急修复同样适用——先补一行「需求/任务」再改代码。

**B. 开发（只实现 A 已写明的需求）**

5. 只改与该 FR 相关的文件；跨入口决策归 `core/services`；涉及 401/402/403、key 池、步骤清理时跑 `node check_single_source.js`。
6. 补测试并覆盖 A 写明的验收要点；前端 `tsc --noEmit` + `npm run build`，后端按 0.4 跑全量。
7. 真实 GPU / conda / 常驻服务命令按「命令执行分工」交用户在 `(vl)` 终端跑，AI 只给完整命令 + 判定标准，拿到输出后只读验证。

**C. 结果后置**

8. 把 B 的实际改动、根因、验证结果回填到 A 那一行的 `**执行/结果**` 与验证列；去掉 FR 的「待实现」、补齐代码位置与验收结论。
9. 追加当日工作日志；`test/test_prd_process.py` 校验两段式记录与流程门存在（已并入全量 pytest）。

### 0.3 新增需求怎么标记

在对应章节加 `FR-xx（待实现）`，写清期望行为、影响的页面/接口/步骤、验收标准。**该 FR 必须在阶段 A 写入，禁止先写代码再补 FR**；AI 实现后（阶段 C）把「待实现」去掉并补齐代码位置与验收结论。

### 0.4 验证清单（每次改完必跑）

| 场景 | 命令 / 动作 |
|---|---|
| 改了 `core/` 任何 Python | `PYTHONPATH="C:/Users/happy/.workbuddy/binaries/python/vltest_deps;D:/code/vl" python3.exe -m pytest test/ -q` |
| 改了链 / key 池 / 401 决策 / 步骤清理 | 追加 `node check_single_source.js` |
| Python 语法快速自查 | `node _lex.js <file.py>`（本会话 Python 沙箱被禁，用它做字符级检查） |
| 改了 `frontend/` | `cd frontend && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/vite build` |
| 改了后端接口 / 服务 | 提醒用户重启 `python -m server`（用户常忘） |
| **任何 PRD 驱动的改动（v1.35）** | 全量 pytest 内含 `test/test_prd_process.py`：校验 0.2 三阶段门、v1.35 起变更记录「需求/任务 + 执行/结果」两段式、最新行无「待填」残留；阶段 C 未回填即失败 |
| **生效口径（v1.24）** | 改 `.py` / `config.yaml` → 重启 server；改 `.tsx` / `.ts` → `frontend/` 下 `npm run build`（不必重启后端）；新增 / 改 API 契约 → 两边都要。设置页 SCHEMA 选项这类数据驱动表单改后端免构建 |
| 涉及 GPU / whisperx / index-tts 实跑 | 交给用户在自己的 `(vl)` 终端跑，本会话 conda 环境无法创建临时目录、Python 写盘受限；AI 只给完整命令 + 判定标准，拿到输出后只读验证 |

### 0.5 环境事实

- Windows + Git Bash；项目根 `D:\code\vl`；`PROJECT_ROOT` 由 `server/app.py` 两层 `dirname` 推导。
- 启动：`python -m server`（托管 `frontend/dist`，默认 `127.0.0.1:8000`）；开发用 vite dev server `:5173`；`VL_HOST` / `VL_PORT` / `VL_RELOAD` 可覆盖。
- 本会话 conda `vl`（py3.10）跑不动；验证用 WorkBuddy 托管 `python3.exe`（3.13）+ `vltest_deps`。
- 用户本机 `(vl)` conda（`D:\app\miniconda3\envs\vl`，Python 3.10 / torch 2.8.0+cu126 / RTX 3060）已集成 IndexTTS-2.5 本地配音插件（`D:/code/index-tts`），依赖版本与互斥约束见 FR-33；真实合成、契约重建、A/B 试听、前端构建一律由用户执行，AI 不代跑。
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

输入源（下载/选择文件）**不属于 12 步注册表**，它由「输入源」卡片单独触发一次性 job。

### 1.3 技术栈

- 前端：React 18 + TypeScript + AntD 5 + Vite；hash 路由（`#/pipeline` 等），无路由库；WebSocket 事件驱动，无全站轮询。
- 后端：FastAPI + uvicorn；`LogBus` 事件总线（2000 条环形历史 + 每订阅者 5000 队列，满了丢事件不阻塞任务）；`TaskManager` 单任务状态机。
- 流水线：`core/pipeline.py` 声明式注册表（12 步，惰性 import）；步骤完成态由磁盘 marker 判定。
- 外部工具：yt-dlp、ffmpeg、whisperX / pyannote VAD、spaCy、OpenCV、pydub、各类 TTS 后端。

### 1.4 页面地图（7 个页面）

| Hash | 页面 | 主要职责 |
|---|---|---|
| `#/pipeline` | 流水线 | 输入源、运行控制、12 步状态（带全局序号）、实时日志、归档 |
| `#/batch` | 批量处理 | 目录扫描、勾选、进度点阵、范围重跑、日志 |
| `#/models` | 模型 | 平台凭证 / 模型 / 任务链 / key 池 / 连通性测试 |
| `#/terms` | 术语 & 提示词 | 术语库、提示词、切分实验场（L1~L4）、调用日志 |
| `#/artifacts` | 产物 | 浏览并下载 `output/`、`history/`、批量工作区的文件 |
| `#/voices` | 配音分配 | 按说话人分配音色、逐句人工改派 / 标记模糊、原声与配音试听、重配单句、视频时间轴双向联动（FR-34） |
| `#/settings` | 设置 | 30 个白名单配置项的可视化编辑 |

Header 全局按钮：「重启后端服务」「停止后端服务」（均二次确认，任务忙时 409）。`PlaceholderPage.tsx` 存在但未使用。**前端为「源码改 → `npm run build` → `frontend/dist` 静态托管」两步**：改 `.py` / `config.yaml` 需重启 server，改 `.tsx` / `.ts` 必须重新构建（不必重启后端），新增/改 API 契约两边都要；漏构建表现为「新菜单 / 新页面不出现」，见第 11 章。

### 1.5 术语表

| 词 | 含义 |
|---|---|
| marker | 步骤「已完成」的判定文件；`is_done()` 只看它是否存在 |
| clean_extra | 强制重跑时要删、但**不参与**完成判定的断点/缓存文件 |
| force | 「重做」语义：删产物 + 断点 + LLM 缓存后真重跑，不是简单置状态 |
| 续跑 | 不带 force：已完成的守卫步骤直接跳过，从缺口继续 |
| 链（chain） | 命名模型序列；任务（task）= `log_title`，绑定到链，未绑定走 `default` |
| 阶段一 / 阶段二 | 字幕阶段 `sub`（6 步）/ 配音阶段 `dub`（5 步） |
| refer_mode | `index_tts` 的音色来源：`1` 固定音色 / `2` 原视频首句人声 / `3` 逐句原声 / `4` 按说话人契约；2/3/4 **绝不静默回退**固定音色（FR-33/34） |
| 音色契约 | `output/audio/voice_map.json`，按视频生成的「句子 number → SPEAKER_XX → 代表音色 wav」映射，带任务表 `fingerprint` 防串片（FR-34） |
| producer | 音色契约的自动划分算法标识（`campplus-cosine-v2`）；换算法或调阈值后旧契约 fail-fast 失效、必须重建（FR-34） |
| 人工覆盖 overrides | 在 `#/voices` 手动改过的句子分配，是人工层唯一真源；同一 `fingerprint` 下重建初稿时原样保留（FR-34） |
| 双人混句 | 一条配音任务含 2+ 条原始字幕（`lines` 长度 > 1），常是两人对话；整句声纹是混合值，判给谁都错，必须按子行判人（FR-34 / v1.25） |
| 子行音色 chunk_speakers | 契约里 `number → line_index → SPEAKER_XX`；第 10 步逐子行合成时优先于整行 `segments`（FR-34 / v1.25） |
| 生成字幕 | 第 6 步 `gen_sub`（产出 `src.srt` / `trans.srt`）；「对齐字幕」专指第 5 步 `align` 的 LLM 切分。v1.26 起两步独立，不再混称 |
| 烧录 | 用户口语，PRD 中统一指步骤 `merge_sub`（`output/output_sub.mp4`） |
| 镜像工作区 | 批量模式 `<root>_log/` 下与原目录同构的 per-video 目录，子进程 `cwd` 指向它 |
| gpt_log 缓存 | `output/gpt_log/<log_title>.json`，按 **prompt 全文**精确命中，与模型无关 |
| 基础术语库 | `terms/*.json`，项目级、全局共享、手工维护的多套术语集合；每个任务按 `terminology.active` 勾选 0~N 套合并成「底板」，不随任务清空 |
| 任务术语表 | `output/log/terminology.json`，4.1 产出 = **勾选底板 + LLM 新抽**，每任务一份、随 `output/` 归档/force 清空；是 4.2 翻译与实验场**唯一**的术语来源 |
| 术语回写 | 在 `#/terms` 手动勾选任务术语表的词条，写进指定的 `terms/*.json`（可新建库）。只改基础库、不改任务术语表，因此不影响当前任务的翻译与断点，下次 4.1 生效 |
| 历史导入 | 在 `#/terms` 多选 `history/<视频名>/log/terminology.json`（每次归档留下的完整任务术语表），按 `src` 去重预览后勾选，追加写进指定的基础库（可新建）。只写 `terms/`，不改 `output/`、历史归档、断点与 GPT 缓存，零 LLM 费用；导入到已有库是**追加合并**而非整库覆盖 |
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
| **G-08 配置** | `load_key` 每次现开 `config.yaml`（可用 `VL_CONFIG` 覆盖路径）→ 手改配置立即生效、无需重启。`update_key` 用 `yaml.dump` 整体重写会**抹掉全部注释** → 重要配置一律手改文件，不要通过设置页 PUT。设置页只暴露 30 个白名单键（v1.24 起含 `index_tts.*` 5 项），密钥类键不下发。 |
| **G-09 互斥与错误码** | 单任务 `running/paused` 时：run / 下载 / 归档 / 重启 / 关服一律 409；批量 `running/stopping` 时 `/api/batch/start` 409。未知步骤 404，范围倒挂 400，越界读文件 403。 |
| **G-10 路径安全** | `/api/logs/tail` 只读 `output/`；`/api/batch/log` 只读当前扫描出的 `<root>_log`；`/api/batch/artifacts/*` 只读 `output/` + `history/` + 批量 log 根。实现为 `abspath` 字符串前缀匹配，`output_xxx` 这类兄弟目录名会被误判为合法（已知边界）。 |
| **G-11 输入唯一性** | `find_media_file()` 要求 `output/` 下媒体候选唯一；有歧义时下载接口 409、页面显示红 Tag。批量用 `output/input_manifest.json` 指定唯一源。手动放入子目录的文件必须移到 `output/` 或写 manifest。 |
| **G-12 音频输入** | 纯音频输入自动剔除 `needs_video` 的 `merge_sub` / `dub_to_vid`；步骤内部也会自行跳过并提示「字幕文件已在 output 目录」。结束步骤在音频模式下向前收敛到 `gen_sub`（序号保持全局 1~12，不重排）。 |
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
- **约束**：URL 必须 http(s)，分辨率必须是 `360/480/720/1080/best`，否则 400；已有输入时 409；输入不唯一时页面红 Tag 提示（G-11）。下载不在 12 步内，走 `MANAGER.start_job()`。
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

- **当前行为**：页面按阶段一/阶段二两个卡片列出 12 个步骤，数据来自 `GET /api/pipeline` 的 `steps[]`。**列表项同一行**（v1.30，用户明确要求「已完成放标题同行、后面加上开始/结束/耗时」）依次显示：步骤序号 + 标题、状态 Badge（运行中 / 已完成 / 未运行）、开始时间、结束时间、本次耗时。状态 Badge 与三时间来自 `steps[]` 的 `done` / `state` 与 `timing_service.attach_last_seconds` 回填的 `last_started_at` / `last_ended_at` / `last_seconds`（v1.28 起 `record()` 写、v1.30 起回填并在列表同行展示；历史 `pipeline_*.log` 经 `backfill` 用文件名日期 + 开始 ts + 耗时补全 `started_at` / `ended_at` 后同样有值）。状态**不再**放在标题下方副标题、时间**不再**只在 Modal 里看。卡片顶部「每步耗时」按钮仍打开统计 Modal，按 `GET /api/pipeline/timings` 展示各步「上次 / 最快 / 次数 / 累计」与第 10 步内部拆解（gpt/s2mel/bigvgan 推理耗时）。
- **入口/代码**：`core/pipeline.py: STEPS / pipeline_status / is_done`；`server/app.py: GET /api/pipeline`（附 `steps[].last_seconds/last_status/run_count/last_started_at/last_ended_at`）/ `GET /api/pipeline/timings`；`core/services/timing_service.py: record(…, started_at, ended_at)/backfill/summary/attach_last_seconds/dub_breakdown/format_report`；`frontend/src/api.ts: StepStatus` 增加 `last_started_at?/last_ended_at?`；`PipelinePage.tsx` 的 `stages` / `List` / `durationLabel` / `stepBadge`（同行渲染）/ 「每步耗时」Modal。
- **约束**：完成态只由 marker 文件存在推导（G-03）；`audio_only` 时列表少 2 步；marker 是步骤成功后的**持久产物**；耗时记录是诊断数据，写入失败静默（G-06 不拖累主流程），历史日志由 `startup` 幂等回填。
- **验收**：正常跑完一步 → `output/log/step_timings.json` 追加一条 `exact=true` 且含 `started_at` / `ended_at` 的记录；重启服务 → 旧 `pipeline_*.log` 自动补录进耗时统计且重复启动不重复计数（历史记录由文件名日期 + 开始 ts + 耗时补全 `started_at` / `ended_at`）；列表每一项同一行显示状态 Badge（已完成 / 运行中 / 未运行）+ 开始时间 + 结束时间 + 本次耗时，live 运行后三时间正确、历史 backfill 后也有值；点「每步耗时」仍能看到第 10 步 GPU 推理各阶段拆解。
- **失败/中止可见性（v1.32，先于开发）**：步骤**崩溃**时前端必须显示红色「失败」Badge + 红色「✕」头像 + 顶部红色错误横幅（含 `error` 文案可复制 + 落盘日志路径），**绝不能**退化为灰色时钟「未运行」让用户以为没跑；用户**中止**显示黄色「已停止」。根因：旧 `_run` 的 `finally` 清空 `current_step`，而 `stepBadge` 无失败分支 → 失败与「从未运行」视觉一致。修复：`TaskManager` 新增 `failed_step` / `stopped_step`，崩溃/中止时记录具体步骤 id（不清空），`snapshot()` 随 `PipelineStatus` 下发；前端 `stepBadge` 增加失败（红）/ 已停止（黄）分支且**优先于「已完成」**（残留旧 marker 也显红），头像同步 `CloseCircleFilled` / `MinusCircleFilled`，顶部 `Alert type=error` 展示 `error` 与 `log_file`。

### FR-05 运行范围选择（开始 / 结束）与默认值

- **当前行为**：控制栏有「开始」「结束」两个下拉（选项 = 全部步骤，闭区间）。主按钮「从「X」跑到「Y」」按所选区间运行（不带 force，续跑可跳过已完成守卫）；「强制重跑」按同一区间清产物后重做。
- **默认值**：开始固定 `transcribe`（转录 whisperX）；结束按设置 `burn_subtitles` —— **开** → `merge_sub`（第 7 步，合并字幕到视频），**关** → `gen_sub`（第 6 步，生成 srt）。默认值由后端 `GET /api/pipeline` 的 `defaults` 返回，用户在设置页改开关后刷新状态即生效；用户手动动过任一下拉后不再被默认值覆盖。
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
- **约束**：烧录关闭或纯音频输入时停在 `gen_sub`，开启烧录的视频到 `merge_sub`；所有步骤标题带全局序号（`StepStatus.no`），页面下拉 / 列表 / 运行态 Tag 口径一致；阶段按钮在输入缺失/歧义、范围未加载、任务运行/暂停或请求提交期间禁用。所有流水线运行入口共享提交锁，防止重复点击；后端在同一任务锁内检查空闲、执行强制请求的输入预检与清理、启动线程，忙碌或输入无效时不清理产物。
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
- **刷新回显（v1.37 新增）**：生成结果不能只活在浏览器内存里 —— 刷新页面 / 重开页面后，卡片必须自动回显**当前 `output/` 里最后一次生成**的三件套（3 个标题候选、简介、10 个标签），复制按钮直接可用。落盘文件 `output/bili_meta.md` 是唯一真相源：`GET /api/bili/context` 增 `existing` 字段回带结构化结果；前端在**首次成功取到 context 时**恢复一次，之后任何刷新（任务状态变化 / 手动刷新）都不得覆盖用户已选标题或本次刚生成的结果。回显零 LLM 调用、零费用、纯只读。
- **素材主次（v1.3 改）**：**视频转录结果是决定性一手素材** —— 标题钩子、主题词、简介的痛点/✔清单/答案金句、L3/L4 标签都要能在转录里找到出处；`video_meta.json` 的 YouTube 原标题/简介**只作背景校准**（校准主题/人名，含营销话术，与转录冲突时以转录为准），标题/简介缺失不再阻断生成。
- **转录来源优先级**：`output/trans.srt`（**中文译文，用户确认的主素材**：已过 ASR 纠错 + LLM 翻译润色 + 分句对齐，中文可直接做钩子，信息密度最高）> `src.srt`（英文原文）> `log/split_by_meaning.txt` / `split_by_nlp.txt`（英文完整句）> `log/cleaned_chunks.json`（第 2 步词级转录）> `log/asr_checkpoint/seg_*.json`（转录未完成时的断点兜底）。**无可用转录 → 400 明确提示先跑第 6 步（或至少第 2 步），不再只靠元数据生成**；卡片在任务结束时自动重查、也可手动刷新。
- **长度策略**：中文译文未超 `bili.transcript_chars`（默认 12000）→ **全文直送**（每 50 行插一个 `[mm:ss]` 标记，不逐行加）；超预算才按时间**均匀采样** `bili.transcript_samples` 段（默认 8，头尾各 1.5 倍配额，每段带 `[mm:ss]` 与开头/结尾标注）。`log/summary.txt`（第 4.1 步全局摘要）始终附加，补足节选未覆盖的部分。预算调大即退化为「永远全文」。**不做 RAG/向量库**：单视频语料远小于上下文窗口，且「钩子/痛点」这类抽象 query 检索易漏金句、切片会丢铺垫。
- **讲者名**：`suggest_speaker()` 在标题/频道/简介 + 转录前 3000 字里匹配人名池（内置 skill 硬红线名单 + `bili.speaker_names` 追加项），认不出退回频道名；页面可改，改过不再被覆盖。
- **公式来源**：bili-video-meta skill（GracePower 361 条投稿实测）提炼为 prompt 模板 `bili_meta`（「术语 & 提示词」页可编辑），运行时不依赖 WorkBuddy/skill 文件。硬红线：标题四段全角 `｜`（`EP{序号}｜钩子｜主题词}｜{讲者}`）、40–70 字；简介七段式（痛点反问→反转句→✔清单→困惑共鸣→答案金句→评论区互动→共情收尾）、不空行、350–600 字；标签恰好 10 个（L1 固定池 3 + L2 轮换 2-3 + L3 主题词 2-3 + L4 长尾 1-2）、每个 ≤10 字。
- **入口/代码**：`core/services/bili_meta_service.py`（`load_transcript` / `_read_transcript_lines` / `full_text` / `sample_lines` / `load_video_context` / `suggest_speaker` / `_valid_bili_meta` / `generate_bili_meta`）；`server/bili_api.py`（薄适配，`GET /context` 只回转录统计、不回全文）；`core/prompts.py: get_bili_meta_prompt` + `DEFAULT_TEMPLATES["bili_meta"]`（一手素材段 + 取材铁律 5 条）；`core/_1_ytdlp.py: _save_video_meta`；`frontend/src/pages/PipelinePage.tsx`。
- **配置**：`bili.speaker_names`、`bili.transcript_chars`、`bili.transcript_samples`（均手改，`load_key` 立即生效）。
- **约束**：不进 `pipeline.py` 注册表（按钮触发，非流水线步骤，不占任务线程）；LLM 走任务链 `bili_meta`（未绑定 → default 兜底）；**自定义提示词模板若没写 `{{transcript}}` 占位符，`get_bili_meta_prompt` 会检测并强制追加一手素材节**，保证主素材不被模板绕过；改内置模板会使 `gpt_log/bili_meta.json` 缓存失效（G-05）；校验失败（EP 前缀/四段/10 标签/长度不符）由 `valid_def` 报给 ask_gpt 走链重试，最终失败 → 400 带 detail；`_save_video_meta` 失败绝不影响下载。
- **回显约束（v1.37）**：① `output/bili_meta.md` 是**唯一真相源** —— 不新增第二份结构化落盘文件、不把文案塞 localStorage（跨浏览器 / 换设备失效，且归档后会串到新视频）；② `_render_md()`（唯一渲染）与 `load_saved_bili_meta()`（唯一解析）必须成对放在 `core/services/bili_meta_service.py`，server / 前端**禁止自行解析 md**；③ 解析复用 `_valid_bili_meta()`（3 标题 / 4 段全角｜ / EP 前缀 / 简介 200–2000 字 / 恰好 10 标签）作唯一校验，文件不存在、格式损坏、校验不过一律返回 `None`，`/context` 仍 200、绝不 500、绝不阻断卡片；④ 归档 `output/` 后 md 被移进 `history/<视频名>/`，此时 `existing=null` 是**正确行为**（新视频不显示旧文案），**不得从 history 回捞**；⑤ EP 序号仍按 localStorage 上次成功值 +1 预填，不变；⑥ 不进 `pipeline.py` 注册表、不占任务线程。
- **验收**：真实归档（33 分钟）读到 `trans.srt` 6301 字、`sampled=false`、prompt 9588 字符且含中文金句 + 取材铁律 + 全局摘要、一手素材排在次要参考之前；EP=42 生成后 3 个标题均以 `EP042｜` 开头且含 3 个全角 `｜`；标签恰好 10 个；无转录时页面禁用生成且接口 400 提示先跑转录。**回显验收（v1.37）**：生成 → 刷新页面，3 个标题 / 简介 / 10 个标签与 `output/bili_meta.md` 逐字一致、复制按钮可用；无 md / md 损坏 / 缺段落 → `/context` 返回 `existing: null` 且 200；归档清空 output 后亦为 `null`；单测覆盖 roundtrip、缺失、多种损坏与 `/context` 契约；`tsc --noEmit` + `npm run build` + 全量 pytest 通过。

### FR-28 广告位提醒（识别 → 视频预览微调 → 按此裁剪）

- **当前行为**：流水线页「广告位提醒」卡片（独立卡片，**不进 12 步注册表**）。一次 LLM 调用从转录里找出全部赞助中插 → 本地 ffmpeg 定准边界 → 产出 `output/ad_slots.md`（人读表格，含「转录开口 vs 建议起点」对照）+ `output/ad_segments.json`（EDL）。卡片内可播放视频预览、用**精细时间轴做帧级微调**、勾选是否裁剪、选裁剪源，确认后「按此裁剪」生成 `output/output_no_ads.mp4`。
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
- **约束**：G-01（决策只在 services）、G-05（`ask_gpt` prompt 精确缓存 + `log_title=ad_detect`，重复点击不二次付费）、G-06（EDL 原子落盘 tmp+replace）、G-07（ffmpeg 切点/静音/帧率探测用 `capture_output` **解析**输出，裁剪转码走 `run_logged`）；G-09（预览**与帧率接口**都按 id 白名单，不接受路径）；不改动 12 步注册表。
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
  1. `core/utils/media_probe.py: probe_media` 一次 ffprobe 读全（视频流缺 `bit_rate` 时按「总码率 − 音频码率」→「size/duration − 音频码率」估算）；`core/services/encode_service.py: build_encode_args` 是**唯一决策点**，第 7/12 步只拼参数（G-01）。`ad_cut_service.probe_frame_rate` 改为薄转发到 `probe_media` —— 帧率解析只允许一份。
  2. **v1.18 恒定质量（默认）**：`rate_control=cq` 时给 NVENC `-rc vbr -cq <level> -b:v 0 -preset medium -spatial_aq 1 -temporal_aq 1 -rc-lookahead 32 -multipass fullres`，**不传 `-maxrate` / `-bufsize`**，让复杂镜头自己拿码率；软编 libx264 时映射成 `-crf <level 换算>`。`quality_profile="cut"` 用 `encode.cut_cq_level`（默认 30，比默认 32 高 2 档）抵消二次转码损失。`encode.cq_gop` 可选显式 `-g`（默认不传，交给编码器；实测短 GOP 收益很小、体积涨得多）。**帧率 / `-pix_fmt` / 音频策略两种模式共用**：显式 `-r` 或 `fps_mode=passthrough`，音频优先 `-c:a copy` 直通，需重编码混音时按原音频码率给 `-b:a`。
  2b. **旧模式 `rate_control=bitrate`**：保留 v1.7 行为 —— 视频 `-b:v / -maxrate / -bufsize` 按原视频流码率 × `bitrate_scale`（`-maxrate` 等于目标码率是削峰根因，仅作兼容回退）。`match_source=false` 仍是完全旧行为（只给编码器、不给任何码率参数）。
  3. 原片是 AV1 但 **RTX 3060 硬件不支持 AV1 编码**（`av1_nvenc` 编译可用但设备不支持），软件 AV1 编 47 分钟不可接受 → 输出仍走 H264/NVENC，只把码率对齐；同码率下 H264 画质会低于 AV1，因此 CQ 模式用 `encode.cq_level` 权衡画质/体积（越小越清晰），旧 `bitrate` 模式仍用 `bitrate_scale`。
  4. 第 7 步显式 `-map 0:v:0 -map 0:a:0?`；第 12 步 `audio_reencode=True`（`amix` 产出的新音轨不能 copy → `aac` 按原音频码率 109k，不再写死 96k）。
  5. **「按此裁剪」也走同一决策点（v1.8 修复）**：`ad_cut_service.build_command` 原先写死 `-c:v libx264 -preset veryfast -crf 20 -c:a aac -b:a 192k`，是第二份编码决策（违反 G-01）—— 用户实跑发现 `output_sub.mp4` 已压到 390 kbps，裁完成片又被 CRF20 重新撑大。现在 `cut_video → _encode_args_for()` 调 `build_encode_args(src, audio_reencode=True, quality_profile="cut")`（`-map [outa]` 是滤镜输出、**不能 copy**），把编码参数注入 `build_command`；v1.18 起默认注入的是 CQ 参数而非码率参数，且 `cut` profile 单独放宽 2 档、优先探测原片（抵消二次转码损失，见 FR-29）。只有 `encode.match_source=false` 或源片参数探测失败才退回 `ad.cut_preset / cut_crf` 旧命令。摘要日志打印**实际编码方案**（如「视频 h264_nvenc · 预设 fast（由 veryfast 映射）· 码率 390k · 帧率 24000/1001 · 音频 aac 109k」），避免再靠猜。
  6. **速度预设按编码器归一**：x264 的 `veryfast` 不是 NVENC 的合法 `-preset`（NVENC 只有 `p1..p7 / fast / medium / slow` 等），直接透传会让 ffmpeg 报参数错。`build_encode_args(preset=...)` 内部映射：libx264 原值、NVENC `veryfast/ultrafast/faster/fast → fast`、`medium → medium`、非法值忽略；该映射只在旧 `bitrate` 模式生效；CQ 模式统一用 `encode.cq_preset`，入口传入的 `preset` 不生效。
- **配置**（均手改 G-08）：`encode.rate_control`（`cq` 默认 | `bitrate`）、`cq_level`（32）、`cut_cq_level`（30）、`cq_preset`（`medium`）、`cq_gop`（0 = 不传）；旧模式沿用 `match_source`（true）/ `bitrate_scale`（1.0）/ `min_video_bitrate`（200）；两种模式共用 `fps_mode`（`cfr` | `passthrough`）/ `copy_audio`（true）。适用范围 = 第 7 步 / 第 12 步 / 「按此裁剪」三处；`ad.cut_preset / cut_crf` 降级为完全旧行为专用。
- **入口/代码**：`core/utils/media_probe.py`（`probe_media` / `_fraction` / `empty_info`）；`core/services/encode_service.py`（`load_config` / `_encoder_for` / `build_encode_args(quality_profile=...)` / `_cq_args` + `_NVENC_PRESETS`，**CQ 与码率两套决策都只在这里**）；`core/_7_sub_into_vid.py` / `core/_12_dub_to_vid.py`（薄适配）；`core/services/ad_cut_service.py`（`_encode_args_for` 传 `quality_profile="cut"` 且优先探测原片 / `build_command(encode_args=...)`）。
- **约束**：G-01（CQ / 码率两套编码参数决策只在 `encode_service.py`，`check_single_source.js` 新增 `cq-encode-decisions` 守卫禁止第二份 `-cq` / `-spatial_aq` 等字面量）、G-07（转码走 `run_logged`、ffprobe 走 `capture_output` 解析）、G-08（新配置手改文件，别用设置页 PUT 抹注释）；不改 12 步注册表与产物文件名；**旧 `bitrate` 模式参数逐字不变**，保证回退可用。
- **成本提示（G-05）**：换编码参数会让产物变化，但**不影响 GPT 缓存**（缓存按 prompt 全文，与视频编码无关）；代价是重编码时间与体积（CQ32 约 1.8×，裁剪链路更高）。改前先评估是否要重跑第 7/12 步。
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
- **约束**：G-01（EDL 决策只在 `ad_service`）、G-06（原子落盘）、G-09（media-info 也按 id 白名单）；不改动 12 步注册表；`detect_ads` 不得吞掉手动段。
- **验收**：无 EDL 工作区加 3 段手动切点（0–10 / 100–120 / 280–300）→ `build_keep` = `[(10,100),(120,280)]`、保留 250s；再跑 `detect_ads` 后手动段仍在且 id 重编号为 `[0,1]`；MD 显示「1 段赞助中插 + 1 段手动切点」且无 `nan` / KeyError；无转录时页面手动链路可用；`447 passed, 1 skipped`；无头浏览器截图确认按钮行布局无重叠。

---

### FR-31 全局日志出口统一（卡片类同步操作也要上页面）

- **要解决的问题**：用户 2026-11-10 实跑「按此裁剪」时发现 `✂️ 裁剪…`、ffmpeg 的 `Input #0 / Stream mapping / libx264 …` 全部只出现在 cmd 窗口，页面「实时日志」卡片一片空白。根因不是 `run_logged` 失效，而是 `TaskManager._run()` 只在**流水线任务线程**里把 `sys.stdout` 换成 `_StreamCapture` → `BUS.publish`；裁剪 / 识别广告位 / B站文案这类**同步 HTTP 卡片操作**跑在 uvicorn 线程池里，任务线程没在跑时 `sys.stdout` 是原始的，`print` / `rprint` 直接落 cmd。更隐蔽的是：碰巧任务线程正在跑时，`sys.stdout` 是进程全局的，卡片日志反而会混进任务日志 —— 这就是「一会儿 cmd 一会儿页面」的真正成因。
- **当前行为**：**任意线程**产生的 core 层日志（`rprint`、`run_logged` 回灌的子进程输出）都同时进 cmd 与页面实时日志、并落盘到 `output/log/pipeline_latest.log`；任务线程不重复；core 不反向依赖 server（G-01）；不改 12 步注册表、不改前端日志订阅协议。页面日志卡片空态文案已改为「流水线步骤、广告位识别、视频裁剪、文案生成的日志都会实时出现在这里」。
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
- **约束**：G-01（删除判定只在 `ad_service`）、G-06（EDL 原子落盘）；`cut_video` 对空区间仍报「没有待裁剪区间，无需裁剪」，不允许对空 EDL 白跑一次 ffmpeg；不改动 12 步注册表。
- **验收**：`478 passed, 1 skipped`（新增 12 例）；删 2 段识别段 → 空 EDL + `dismissed` 2 条 + MD「暂无」；非法区间仍 400 且不清空；取消勾选 / 微调时间不进 `dismissed`；重新识别跳过被删段、保留手动段，点恢复后又出现；`PUT /api/ad/edl` 空列表 200、`dismissed=[]` 覆盖生效；`check_single_source.js` clean；tsc + vite build 通过。

---

## 4. 功能需求 · 配置与管理

### FR-11 设置页

- **当前行为**：5 组共 **40 个白名单键**（v1.24 新增 `index_tts.*` 5 项；v1.36 新增 `qwen3_asr.*` 10 项，FR-35），按类型渲染 `Select` / `InputNumber` / `Switch` / `Input`；修改先进本地脏数据，「保存 (n)」批量 PUT，「重载」丢弃修改。保存结果区分 `applied[]` / `rejected[]`。
- **入口/代码**：`frontend/src/pages/SettingsPage.tsx`；`server/settings_api.py: SCHEMA / GROUPS / SECRET_KEYS`；`core/utils/config_utils.py: load_key / update_key`。
- **分组**：通用（3）、网络代理（3）、转录 ASR（**14**：`whisper.model / language / runtime / demucs` 4 + `qwen3_asr.*` 10，FR-35）、字幕与切句（11）、配音 TTS（9：`tts_method` + `speed_factor` 3 + `index_tts.lang / refer_mode / spk_audio_prompt / model_dir / use_bf16` 5）。`tts_method` 下拉含 `index_tts`（FR-33）；`whisper.runtime` 下拉含 `qwen3_asr`（v1.36）。
- **数据驱动例外**：设置页下拉与标签是 `GET /api/settings` 的 SCHEMA 数据驱动的通用表单，因此只改 `settings_api.py` 的选项 / 标签**不需要重新构建前端**；结构性新增（新路由 / 菜单 / 页面 / 新 API 客户端）才必须 `npm run build`。
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
- **历史导入（v1.23）**：每次「保存历史」都会把当期 `output/log/terminology.json` 归档到 `history/<视频名>/log/terminology.json`，这些「底板 + 新抽」的完整表此前只能躺在归档里，页面选不到。用户拍板三点：**导入目标是 `terms/` 基础库**（不是当前任务表，避免作废 4.2 断点 / GPT 缓存）、**一次可多选多个历史任务批量合并**、**目标库已有同名 `src` 默认跳过保留人工译文**（另给覆盖开关）。决策层 `translate_service`：`history_term_sources()` 只读索引（只给元数据：目录名 / 条数 / theme / mtime / 可读，损坏文件仍列出标 `available=false`）；`_resolve_hist_file()` 是**唯一**路径解析——客户端只传 `history/` 下真实存在的一级目录名，校验无分隔符 / 无 `..`、白名单命中、realpath 不越界，杜绝路径穿越；`history_import_preview(names)` 按传入顺序用 `merge_terms` 去重（后者覆盖、保留首次拼写），每行给 `origins`（各期取值）、`conflict`（译文不一致）、`duplicated`，前端不自行合并；`import_history_terms(target, names, srcs, overwrite)` 从磁盘**重读** src/tgt/note（浏览器只传 key），经由新抽取的 `_merge_into_base_set()` 原子写入。`_merge_into_base_set` 是 `write_back_terms`（当前任务表）与历史导入**共用**的唯一读改写决策，不造第二份；导入到已有库是追加合并、保留原 `desc`，不整库覆盖、不自动激活。**只写 `terms/*.json`**：`output/`、历史归档、断点、GPT 缓存全不动，零 LLM 费用，需勾选该库并重跑 4.1 才生效。`#/terms` 加「从历史导入」Modal：多选历史任务表（禁用损坏项）→ 实时去重预览（统计 / 搜索 / 逐行勾选 / 展开逐期对照 / 标「已存在·跳过」）→ 选目标库（已有或新建）+ 覆盖开关。API：`GET /terms` 扩展 `history[]`；新增 `POST /terms/history/preview`（只读）与 `POST /terms/history/import` —— **必须走 POST**，`GET /terms/{name}` 会吞掉任何 GET 子路径。
- **入口/代码**：`frontend/src/pages/TermsPromptsPage.tsx: TermsTab`（列表 / 编辑态 / 回写 Modal / 历史导入 Modal / 按来源 Tabs / 并集总表）；`frontend/src/translateApi.ts`（`TermCatalog*` / `TermSource` / `HistoryTermSource` / `HistoryImport*` 类型）；`server/translate_api.py`（`GET /terms` 扩展 `job` + `catalog` + `history`、`POST /terms/writeback`、`POST /terms/history/preview`、`POST /terms/history/import`、`PUT /terms` 的 create/overwrite）；`core/services/translate_service.py: load_active_terms / merge_terms / load_job_terms / term_catalog / history_term_sources / history_import_preview / import_history_terms / _merge_into_base_set / validate_term_set_name / create_or_replace_term_set / write_back_terms / build_shared_context`；`core/_4_1_summarize.py`；`core/_4_2_translate.py`。
- **约束**：删除激活中术语库 409；`custom_terms.xlsx` 只做一次性迁移；共享上下文统一走 `prompts/shared.md` 模板（避免双重表头）；**基础库不自动回写**——4.1 新抽的术语不会自己进 `terms/`，必须在本页手动勾选回写；`GET /terms/{name}` 会吞掉任何新增的 `GET /terms/xxx` 子路径，故回写预览与全部术语目录都扩展现有 `GET /terms` 响应、写入走 `POST /terms/writeback`；名称校验、基础库写入、全部术语目录、按来源清单与历史导入决策只能在 `translate_service`（`check_single_source.js` 的 `term-set-write-decisions` / `term-catalog-view` / `term-source-file-labels` / `history-term-import-decisions` / `history-glossary-path` 守卫）；`sources` 的文件标签与清单只在 `term_catalog()` 里生成，`history/*/log/terminology.json` 只在 `_resolve_hist_file()` 里解析，API 与前端不得二次读盘重造；历史导入与任务表回写共用 `_merge_into_base_set()` 一处写入。
- **验收**：勾选 0 套 → 4.1 / 4.2 的 prompt 无基础术语；勾选两套 → 任务术语表含合并术语且重复 `src` 以靠后库为准；4.1 新抽术语出现在 4.2 每个块的 prompt 中；任务术语表缺失时 4.2 用底板正常翻译；术语变化后旧断点作废。回写：勾 N 条 → 目标库新增 N 条且内容取自任务术语表；同 `src` 已存在时跳过且不改译文；可回写进新库且不自动激活；无 4.1 产物 / 非法库名 / 坏目标库 / 空勾选均 400 且不写盘；回写后任务术语表与断点指纹不变。新建：点「新建」进入编辑态、可填库名并保存成 `terms/<名>.json`；同名新建先 409 确认再覆盖。全部术语：两套基础库（只激活一套）+ 4.1 新抽 → 总表按 `src` 去重后包含全部词条，未激活库标「未生效」但仍显示译文，4.1 新抽标「仅任务表」；`active: []` 且合法空任务术语表时 `effective=0` 不回退 `default`；基础库与任务表取值不同时标「冲突 / 已漂移」且生效值取任务表；读 `catalog` 前后 `terms/`、`config.yaml`、`output/log/terminology.json` 字节不变。按来源清单：两套基础库含同一 `src` 但译文不同时，两个来源页**分别保留各自取值**、并集只显示一行；无 4.1 产物时任务表来源项 `available=false` 且 `terms=[]`，绝不塞进回退底板；损坏的库不出现在 `sources`；从任务表页勾选行回写仍走 `write_back_terms` 单一决策，只写基础库、不改任务表。历史导入：两个归档含同一 `src` 但译文不同 → 预览去重为一行、按选择顺序后者覆盖并标 `conflict`、展开可见两期原值；导入进已有库时同名 `src` 跳过且人工译文 / `desc` 不变，`overwrite=true` 才替换；导入前后 `output/log/terminology.json` 与所有 `history/` 文件字节不变、只有 `terms/<目标>.json` 变化；非法库名 / 空选择 / 空勾选 / 不存在的归档 / 路径穿越（含 `../..`、绝对路径、分隔符）均 400 且不写盘；`GET /terms/history/preview` 不得命中 `GET /terms/{name}`（预览接口为 POST）；抽取公共写入后 `write_back_terms` 的返回契约与行为不变。

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

### FR-21 步骤总表（12 步，`core/pipeline.py` 为唯一来源）

| # | id | 标题 | 阶段 | 完成 marker | clean_extra | LLM 任务 | needs_video |
|---|---|---|---|---|---|---|---|
| 1 | `transcribe` | 转录 whisperX | sub | `output/log/cleaned_chunks.json` + `.xlsx` | `output/log/asr_checkpoint/` | `asr_domain_hints` | |
| 2 | `asr_correct` | 幻觉词修正 | sub | `output/log/asr_correction_report.json` | `asr_correct_ckpt.json` | `asr_sentence_fix` | |
| 3 | `split` | 切句 NLP + LLM | sub | `split_by_nlp.txt` + `split_by_meaning.txt` | — | `split_by_meaning` | |
| 4 | `translate` | 总结 + 翻译 | sub | `translation_results.xlsx` + `terminology.json`（任务术语表）+ `translate_ckpt.json` + `summary.txt` | — | `summary`、`translate_faith`、`translate_express` | |
| 5 | `align` | 对齐字幕 | sub | `translation_results_for_subtitles.xlsx` + `translation_results_remerged.xlsx` | — | `align_subs` | |
| 6 | `gen_sub` | 生成字幕 | sub | `src.srt` + `trans.srt` | — | — | |
| 7 | `merge_sub` | 合并字幕到视频 | sub | `output/output_sub.mp4` | — | — | ✔ |
| 8 | `audio_task` | 生成配音任务 | dub | `output/audio/tts_tasks.xlsx` | — | `sub_trim` | |
| 9 | `refer_audio` | 提取参考音频 | dub | `output/audio/refers/` | — | — | |
| 10 | `gen_audio` | 生成配音 | dub | `output/audio/segs/` | — | —（内部会用 `tts_correct_text`，注册表未登记） | |
| 11 | `merge_audio` | 合并完整音频 | dub | `output/dub.mp3` | — | — | |
| 12 | `dub_to_vid` | 合并配音到视频 | dub | `output/output_dub.mp4` | — | — | ✔ |

> 表中简写的 marker：`cleaned_chunks.*` / `split_by_*` / `translation_results*` / `*_ckpt.json` / `asr_correct*` 在 `output/log/` 下；`src.srt` / `trans.srt` 在 `output/` 下。`marker_mode` 默认 `all`（全部存在才算完成）。`is_done()` 只看存在性。
>
> **序号是全局固定值**（`STEP_NO`，`pipeline_status()` 返回 `no`），与源码文件编号 `_2`…`_12` 一一对应；纯音频输入过滤掉 `needs_video` 两步时**序号不重排**，否则页面上的「第 10 步生成配音」会和文档/口头说法对不上。前端起始/结束下拉、步骤列表、当前步骤 Tag 一律显示「`N. 标题`」（v1.26）。
>
> **第 6 步单独存在的理由**：v1.25 之前 `_5_split_sub`（LLM 对齐切分，全片最贵的一步）与 `_6_gen_sub`（纯本地按时间戳写 srt）被合并成一步，用户只想重新生成字幕时被迫连 LLM 一起重跑。拆开后单独「运行 / 重跑第 6 步」只删 `src.srt` + `trans.srt`，读第 5 步留下的两张中间 xlsx，**零 GPT 费用**。

### FR-22 阶段一步骤内部规格

| 步骤 | 输入 → 主要输出 | 跳过守卫 / 断点 | 外部工具与关键行为 |
|---|---|---|---|
| 输入下载（非注册步骤） | URL → `output/<title>.mp4/.jpg` + `input_manifest.json` | 无（一次性 job） | yt-dlp（先 pip 升级，`run_logged`）、`cookies.txt`、ffmpeg；`ejs:github`；bot 挑战转可读错误；浏览器 cookie 失败回退文件；自定义 `%(uploader)s/%(title)s` 会落子目录，需移入 `output/` 或写 manifest |
| 1 `transcribe` | 媒体 → `output/audio/raw.mp3` → `cleaned_chunks.json`（JSON 主存）+ `.xlsx` 导出 | `@check_file_exists(cleaned_chunks)`；`asr_checkpoint/` 分段断点（`segments.json`、`seg_*.json`、`asr_hints.json`），指纹 = 文件名+size+duration+gap_fill+decode算法版本/配置（v1.11） | ffmpeg 转码 / silencedetect、whisperX（local / 302 cloud / elevenlabs）、pyannote VAD、可选 Demucs 人声分离、LLM 生成领域提示词 `initial_prompt/hotwords`（仅 local）；断点**收尾故意保留**作备份 |
| 2 `asr_correct` | `cleaned_chunks.json` 原地改写 + `asr_correction_report.json` + `.json.bak/.xlsx.bak` | report 存在即跳过（含未解决批次时自动补跑）；`asr_correct_ckpt.json` 每批原子写、成功后删除 | 三层：L1a 词频实体统一（同键/复数/短词护栏）、L1b 热词模糊**只标记不改写**、L2 句子级 LLM 复核（3 并发、失败批次 3 轮 sweep、冷却 120/180/300s）、L3 索引锚定回写 |
| 3 `split` | `cleaned_chunks.json` → `split_by_nlp.txt` → `split_by_meaning.txt` | 两个 `@check_file_exists`（必须同时删） | spaCy 按标点/长句根切分；>80 spaCy token 安全阀；comma/connector 切分已移除（译前切碎伤质量）；LLM 语义切分重复 3 轮、线程池、`" " * retry` 破缓存；`split_sentence()` 是**保真**切分（LLM 只给切点，代码用原句切片拼回） |
| 4 `translate` | `split_by_meaning.txt` + 勾选的基础术语库 → `terminology.json`（任务术语表 = 底板 + 新抽）+ `summary.txt` → `translation_results.xlsx` | `_4_1` 无守卫（每轮重生成术语/摘要）；`_4_2` 有 `@check_file_exists`；`translate_ckpt.json` 每块原子落盘、收尾**只清空 chunks 保留文件**（它是完成 marker） | 4.1：`load_active_terms()` 底板（勾选 0~N 套）+ LLM 抽 ≤15 条 → `merge_terms` 去重写任务术语表（同 src **底板**优先）；4.2：`load_job_terms()` **只读任务术语表**（缺失/损坏回退底板），不再重复叠加基础库；分块按 `translate.chunk_size`/`max_sentences` 双上限；chunks 指纹 = 句子 + 块数 + **术语签名**，术语变化自动作废旧 ckpt；两阶段翻译（直译 → 可选意译 `reflect_translate`）；3 次重试 + 键名别名归一；并发 `check_len_then_trim`；**第 5 步才是 LLM 调用大头** |
| 5 `align` | 译表 → `translation_results_for_subtitles.xlsx` + `translation_results_remerged.xlsx` | 无 | `_5` 超长行 LLM 对齐切分（最多 3 轮，futures 必须显式消费）；全片 LLM 费用大头 |
| 6 `gen_sub` | 两张中间 xlsx + 词表 → `src.srt` / `trans.srt` / `src_trans.srt` / `trans_src.srt` + 配音用 srt | 无 | `_6` 两次 `align_timestamp`：显示字幕与配音字幕（用 remerged 表）；`autocorrect_py` 清理译文；**纯本地、零 LLM**，单独重跑不删第 5 步产物（v1.26） |
| 7 `merge_sub` | `src.srt` + `trans.srt` + 视频 → `output_sub.mp4` | audio-only 直接返回 | 双 `subtitles` 滤镜 force_style + `-map 0:v:0 -map 0:a:0?`；**编码参数由 `encode_service.build_encode_args` 按原片决定**（码率/帧率/pix_fmt/音频直通，FR-29）；`burn_subtitles=false` 只生成 0 秒黑帧占位；缺 srt 抛 `FileNotFoundError`（G-16）；`run_logged` 转发 |

### FR-23 阶段二步骤内部规格

| 步骤 | 输入 → 主要输出 | 跳过守卫 / 断点 | 关键行为 |
|---|---|---|---|
| 8 `audio_task` | 配音 srt → `tts_tasks.xlsx` | `@check_file_exists` | 解析 SRT、去括号与 `-`、`min_subtitle_duration` 合并过短字幕（`avoid_cross_turn_merge=true` 时话轮交替不合并）；不再二次 trim |
| 9 `refer_audio` | `tts_tasks.xlsx` + `vocal.mp3` → `refers/<n>.wav`；`refer_mode=4` 时另产出 `voice_map.json` + `voice_refs/`（FR-34） | 判**自己的 `refers/` 目录**非空（不能判 `segs/`）；`_refers_complete()` 按任务表 number 逐个校验 wav 存在且 >1KB | `demucs_audio()` 兜底（长音频 mp3 走流式编码，FR-33）；soundfile 按 SRT 时间切片（`float32` 降内存）；**仅** `index_tts.refer_mode=1` 时 `_fixed_voice_only()` 短路：建空 `refers/` 作 marker 后 return，跳过约 2.5 min Demucs；末尾 `maybe_build_voice_map()` 只在 mode 4 生效（非 mode 4 严格 no-op） |
| 10 `gen_audio` | `tts_tasks.xlsx` → `segs/<n>_<i>.wav` | `tts_main` 按 `save_as` 存在即跳过；`audio/tmp/` 存原始 | 按 `tts_method` 分发 10 种后端、3 次重试、末次 LLM 改词；前 5 行 warmup；**并行边界（v1.28）**：`index_tts` 的 GPU 推理（`index_tts_for_videolingo`）**完全主线程串行、连线程池都不进**（3.3GB 单例 + `_INFER_LOCK`，CUDA 上下文不跨线程，否则 12GB 卡上多实例必 OOM），gpt_sovits 强制单线程（独立控制台窗口，不能用 PIPE）；但 ffmpeg `atempo` 变速是纯 CPU/IO，走 `ThreadPoolExecutor`（并发度 `dub.speed_workers`，缺配/非法回退 4，设 1 恢复全串行）——**时间轴仍按字幕顺序累积**（`cur_time` 顺序累加，不依赖变速完成先后）；按 `speed_factor` 重排时间轴；单句重配 = 删该句 tmp/segs wav 后重跑本步，其余句秒跳过、`real_dur` 从文件重读（FR-34）；`new_sub_times` 写盘前一律经 `plain_time_pairs()` 转 builtin float（numpy>=2 的 `np.float64(...)` repr 会让第 11 步崩，v1.27）；合并宽表时 `finally` 调 `_shutdown_speed_executor`（`shutdown(wait=True, cancel_futures=True)` 取消排队任务、等在跑 ffmpeg 写完再返回） |
| 11 `merge_audio` | `tts_tasks.xlsx` + `segs/` → `dub.mp3` + `dub.srt` | 首段缺失则 return | **分批 ffmpeg 装配**（v1.31，先于开发）：每批 `BATCH_SIZE=80` 段，批内 `[j:a]aresample=16000,aformat=mono,asetpts[clipJ]`、空隙用共享 `anullsrc`（`-f lavfi -i anullsrc`，`atrim` 定长）内联静音、不落临时静音文件，`[silJ][clipJ]…concat=n=…[out]` 一次性 concat 编码该批 `batch_K.wav`(16k/单声道)；各批再经 `concat` demuxer（只传短 list 文件）串成 `dub.mp3`(16k/64k)。**原因**：Windows `CreateProcess` 命令行上限约 32767 字符，单趟 1448 个 `-i` + 巨型 `filter_complex` 直接 `WinError 206 文件名或扩展名太长`；分批后每批命令 ≪ 上限，终拼只传 list 文件无长命令。**禁止 pydub `+=` O(n²) 逐段拷贝**（1448 段约 90GB 字节搬运，实测 5m21s，FR-性能），**禁止逐段 wav→mp3→解码重编码**（双重有损）；ffmpeg 任一失败则回退旧 pydub 路径保证正确性（回退路径保留 `parse_excel_list` 解析，v1.27 禁止裸 `eval`）；`lines` / `new_sub_times` 单元格统一走 `parse_excel_list()`（numpy 2.x repr 兼容） |
| 12 `dub_to_vid` | `dub.mp3` + `dub.srt` + `background.mp3` + 视频 → `output_dub.mp4` | audio-only return；`burn_subtitles=false` 生成黑帧占位 | -20dB 归一化；ffmpeg `filter_complex` 字幕滤镜（**当前只有 `dub.srt` 单条黄中文**；`subtitle.source=unified` 时由 `subtitle_source_service` 叠 `dub_src.srt` 白英文 + `dub.srt` 黄中文双路，FR-36）/ 缩放 / pad + `amix`；编码参数同样走 `encode_service`（`audio_reencode=True` → aac 按原音频码率，不再写死 96k，FR-29）；`run_logged` 返回码非 0 抛 `RuntimeError` |

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

- **单测**：`test/` 下 33 个 `test_*.py` + `conftest.py` + `README.md`；跑法见0.4。当前基线 **662 passed, 1 skipped**（v1.27 新增 `test_dub_timeline.py` 9 例：numpy 2.x 标量 repr / 纯字面量 / 混合 np.int / 文本 lines / 非字符串透传 / 非法值 / 时间对 float 化 / 落盘往返 / 第 11 步端到端复现；v1.26：`test_voice_chunks.py` 13 例覆盖话轮保护 / 子行时间窗 / 等分兜底 / mixed 幂等 / 子行写入与音色优先 / 12 步序号 / 第 6 步零 LLM / 文本切分 / 拆分后 srt 游标 / 单行可展开 / 整行改人压过子行；v1.28 专项：`test_timing_service.py` 历史日志回填去重幂等 / `summary` 聚合 / 第 10 步拆解解析、`test_log_hooks.py` startup 回填失败不阻断服务、`test/test_dub_timeline.py` 已含线程池关闭语义（从 `core/_10_gen_audio.py` AST 抽取真实 `_shutdown_speed_executor` 验证排队取消 + 运行完成后再关闭，规避测试环境缺 `requests` 的 TTS 依赖链））。覆盖：链与走链、key 池、第 4 步（分块/断点/术语/共享上下文）、词级存储、ASR 纠错纯逻辑、译文校验、字幕时间、进度、VAD 补转、子进程日志、历史归档、接口层、cookie/下载、流水线范围、TLS 信任、B站文案（含转录来源优先级 / 全文与均匀采样 / 自定义模板兜底）、广告位识别与 EDL、精细时间轴的帧率探测、编码参数对齐与 preset 映射（FR-29）、手动切点补集与重识别保留（FR-30）、全局日志出口 / 防重 / 接口级上送（FR-31）、429 限流等待与解析（FR-25）、术语两层模型与 0 勾选 / 去重 / 断点签名 / 4.1 写盘（FR-13）、术语勾选回写与新建同名 409 / 名称校验 / 激活写入失败（FR-13）、广告位预览源默认决策与字幕标记 / 同名重烧版本 / context 单快照 / 预览 Range 白名单（FR-28）、CQ 参数与 cut profile 放宽 / 探测原片而非已压产物 / 非法配置回退 / 旧 bitrate 逐字不变（FR-29）、任务链删除改绑与空链 / 悬空绑定 / 零条链 / 缺 default 拒绝（FR-12/25）、全部术语目录与按来源完整清单 / 纯只读（FR-13）、历史归档术语索引 / 路径穿越白名单 / 多选去重预览与冲突 / 回写与导入共用写入决策 / 只落 `terms/` / 接口契约（FR-13）、配音时间轴列表单元格的读写与 numpy 2.x repr 兼容（FR-23/34，v1.27）、步骤耗时记录 / 历史日志回填 / 第 10 步变速并行线程池收尾（FR-23/34，v1.28）。
- **未覆盖**：步骤 5–8 与 9–12 的实跑、真实 ASR/spaCy/pyannote、GPU 路径、前端交互、prompts 模板渲染；**配音链路（FR-33/34）只有纯逻辑单测** —— 话轮判定、子行时间窗/拆分游标、契约人工层、音色消费优先级、步骤序号已有 13 例 `test_voice_chunks.py`，但 index-tts 真实推理、CAMPPlus 聚类与滑窗切换点、`#/voices` 页面与视频联动仍只有 AST / 静态链路 / 接口冒烟 / 内存 monkeypatch 验证；真实合成、重建契约、A/B 试听与浏览器交互按分工由用户在 `(vl)` 终端执行；`test_dub_timeline.py` 用 pydub stub 导入第 11 步，只验纯逻辑与 xlsx 往返，不跑真实音频拼接。
- **隔离三件套**：monkeypatch `config_utils.CONFIG_PATH` + `chdir(tmp_path)` + monkeypatch 绝对路径常量（`TERMS_DIR` / `KEY_STATE_FILE`）。
- **分层守卫**：`check_single_source.js` —— 401/402/403 决策字面量、`mark_key_dead` 调用、`delete_step_artifacts` 调用各自只允许出现在唯一归属文件。
- **只读自检**：`python check_vad_gaps.py` 优先扫当前ASR checkpoint的词级空档，回退词表，并标提示词回声；`--fill` 用已缓存本地模型关闭VAD/提示词复核，支持 `--start 00:09:30 --end 00:10:10`。不改断点/字幕/配置，不下载模型、无候选不加载；没有“历史视频固定区间”兜底。此工具不保证检出<3秒短漏词或所有内容错位。

### FR-33 本地 index-tts 配音（音色来源 1/2/3）

> 代码位于 `feat/voice-map` 实验分支（尚未合并 `main`）；`config.yaml` 被 `.gitignore` 排除，需单独备份。

- **需求**：`tts_method: index_tts` 时用本机 `D:/code/index-tts` 的 IndexTTS-2.5 做零样本音色克隆；这是**可选后端**，默认仍 `azure_tts`，未装插件不影响其它配音方式。
- **运行环境（用户本机 `(vl)` conda，AI 不代跑）**：Python 3.10/3.11；`pip install -r requirements.txt` 后 `pip install -e D:/code/index-tts --no-deps`（必须 `--no-deps`，否则插件严格 pin 回灌覆盖共享版本）。共享包锁定 `transformers==4.52.1` / `tokenizers==0.21.0`（4.57.x 已移除 index-tts 依赖的 `NEED_SETUP_CACHE_CLASSES_MAPPING`）、`opencv-python==4.11.0.86`（4.9.0.80 在 numpy 2.2.6 下 ABI 冲突，会炸第 7 步烧字幕）、`openai>=2.45,<3`；**不要升级 `huggingface-hub`**（4.52.1 要求 <1.0）、**刻意不装 keras**（会误拉 TensorFlow）。权重约 3.3 GB 常驻显存，首次加载 1–2 min；**与 index-tts 自带 `webui.py` 不能同时运行**（实测双实例必 OOM）。`setup.py` 解析 requirements 时须过滤空行与 `#` 注释行。
- **入口/代码**：`core/tts_backend/index_tts.py`（模块级单例 + `_ENGINE_INIT_LOCK` / `_INFER_LOCK` / `_REFER_LOCK` 三锁、`indextts` 懒加载、`index_tts_for_videolingo()` 适配器、`self_check()` / `--smoke`）；`core/tts_backend/tts_main.py` 的 `elif TTS_METHOD == 'index_tts'`（分支内局部 import，不拖慢 vl 启动）；`core/_10_gen_audio.py`（`index_tts` **完全主线程串行、连 ThreadPoolExecutor 都不进**，CUDA 上下文不跨线程，warmup 阶段加载一次并常驻；gpt_sovits 仍 `max_workers=1`）；`core/tts_backend/refer_audio.py`（第 9 步）；`core/asr_backend/demucs_vl.py`（长音频 mp3 流式编码）。所有模型路径强制绝对路径，**绝不 chdir**（vl cwd 恒为项目根）。
- **音色来源 `index_tts.refer_mode`（唯一决策在 `_resolve_reference()`）**：
  - `1` 固定音色 `spk_audio_prompt`：唯一允许使用预置 wav 的模式；第 9 步 `_fixed_voice_only()` 短路，建空 `refers/` 作 marker 后 return，跳过约 2.5 min Demucs。
  - `2` 原视频首句人声 `refers/1.wav`：全片复用同一参考音频，speaker condition 只算一次，最快。
  - `3` 逐句原声 `refers/{number}.wav`：最贴合但每句各自重算 speaker condition（1015 句明显更慢）；缺某句降级到**同视频** `refers/1.wav`，仍是原声。
  - `4` 按说话人音色契约（FR-34）。
  - **2/3/4 物理上不可能用到预置音色**：缺参考音频直接抛 `FileNotFoundError` 并提示先跑第 9 步。旧实现 mode 2 缺文件时静默回退 `voice_01.wav`（用户以为用了原声其实是固定音色），是已修真 bug。`_ensure_refers()` 在模型已常驻显存时不再触发 Demucs，直接报错防 OOM。
- **语言与其它配置**：`lang='auto'` 由 `target_language` 映射 ZH / EN / JA / ES / AR / YUE；`model_dir` / `cfg_path`、`device`（留空自动）、`use_bf16`、`use_emo_text`（默认关，额外 QwenEmotion 占显存）。设置页白名单 5 项（FR-11），其余手改 `config.yaml`。
- **第 9 步配套修复（与配音无关但被长视频触发）**：74 min `raw.mp3` 一次性 `lameenc.encode` 报 `RuntimeError: LAME encoding failed`。`demucs_vl._save_mp3_stream()` 改为 `_to_pcm_frames`（nan_to_num + int16 + (T,C) 交错连续）→ 优先 ffmpeg/libmp3lame stdin 按 60 s 分块（内存平稳），失败自动回退同一 encoder 分块 `lameenc` + flush 并打印 ffmpeg stderr；输出仍是 `vocal.mp3` / `background.mp3`，`_2_asr.py` 的 `format="mp3"` 不受影响。另修 `_refers_complete()` 按任务表 number 逐个校验 wav 存在且 >1KB（旧的 `os.listdir()` 非空会让半完成目录假装完成）、`sf.read(dtype="float32")` 把 74 min 立体声内存峰值约 3.1 GB → 1.6 GB。
- **约束**：第 9 步是**无条件**流水线步骤，与 `tts_method` 无关，接本地 TTS 应先检查能否短路；切到 mode 2/3/4 后必须重跑第 9 步（空 `refers/` 不触发完成守卫）；源码或 config 改动后 server 是常驻进程，**跑整片前必须重启**。
- **验收**：`python -m core.tts_backend.index_tts --smoke`（用户终端）确认 model_dir / cfg_path 存在、lang auto→ZH、ref 解析正确；`_resolve_reference` 在 mode 2 空目录报错、有 `1.wav` 返回原声、mode 3 缺句回退同视频 `1.wav`、mode 1 返回固定音色；`_10_gen_audio` 对 `index_tts` 不构造线程池；真实 1015 句已由用户跑通，单句约 2.73 s（gpt 1.52 + s2mel 0.86 + bigvgan 0.22，RTF 0.8171），产出的 `refers/*.wav` 1015/1015 齐全、2.50–13.00 s、44.1 kHz / 2ch / PCM_16。

### FR-34 按说话人的音色分配（契约 + 人工审核工作台）

- **需求**：多人视频要能听原声后逐句指定谁在说、显示所有配音、全部可人工调整、模糊句可标记。架构决策：**自动聚类只作初稿，终审在人**；不把多说话人做成第 4 个枚举硬塞进 TTS 后端，而是引入 per-video 数据契约。
- **三层架构（不要合并成第二份决策）**：
  - **生产层** `core/tts_backend/voice_map.py`：可插拔 producer，只负责生成契约；挂在第 9 步 `refer_audio.py` 两处提前 return 前的 `maybe_build_voice_map()`，**非 mode 4 严格 no-op**；必须函数内延迟 import（`core/__init__.py` 会导入 refer_audio，顶层 import 会触发 runpy `RuntimeWarning` 与意外预加载）。
  - **契约层** `output/audio/voice_map.json`：`version` / `producer` / `fingerprint`（绑定 `tts_tasks.xlsx`，换视频或重切句自动失效）/ `n_speakers` / `degraded` / `task_rows` / `mapped_segments` / `embedded_segments` / `speakers{SPEAKER_XX: n_clips, ref}` / `segments{number → SPEAKER_XX}` / `overrides` / `segment_meta` / `diagnostics`；v1.25 起另有 `chunk_speakers{number → {line_index → SPEAKER_XX}}` / `chunk_meta{number → {line_index → confidence, flags, time, manual, source}}` / `chunk_built_at`；v1.26 起另有 `chunk_splits{number → {at, n_src, lines, original, source}}`（单字幕行被人工/自动拆成两段的唯一真源，`n_src` 记录它原本消费几个 srt 块）。旧契约缺字段一律 `setdefault` 兼容，**不因新增字段递增 producer**；`os.replace` 原子写；`segments` 保持扁平结构，人工层用平行字段，消费层永不改。
  - **消费层** `core/tts_backend/index_tts.py` 的 `refer_mode=4`：只读契约，`_resolve_reference(number, cfg, chunk_index=None)` 优先查 `chunk_speakers[number][chunk_index]`，无子行判定才回退 `segments[number]` → `speakers[spk].ref`；缺句直接报错不回退固定音色。`chunk_index` 由 `_10_gen_audio.process_row` → `tts_main(..., line_index)` → `index_tts_for_videolingo(..., chunk_index=...)` 一路透传，其余 TTS 后端忽略该可选参数。
- **producer 演进**：v1 `mfcc-ward-v1` 用 MFCC + ward 自动估人数，真实数据 k=2 / silhouette 0.2247，但相邻句切换率 21.2%、同一段连续叙事被切成两种音色（用户试听裁决「不像一个人」）→ 判定分的是语调 / 情绪而非人，安全降级 `MIN_SILHOUETTE` 0.22→0.30 使 k 自动退化为 1；当前 v2 `campplus-cosine-v2` 用 index-tts 自带 CAMPPlus 声纹模型（`DTDNN` 192 维 + `campplus_cn_common.bin`，**零新增依赖 / 权重**）提嵌入、L2 归一化 + ward 聚类，三道降级判据（`MIN_SILHOUETTE=0.25` / `MIN_SPEAKER_SHARE=0.08` / `MAX_CENTER_COS=0.75`）+ `SMOOTH_WINDOW=5` 话轮平滑；`voice_map_speakers: 'auto' | 1..6` 可强制人数（绕过降级判据）。真实单人播客 v2 仍判 1 人（`silhouette 0.188`、簇间中心余弦 0.836）并 `degraded=True` —— **正确行为不是故障**，拿单人素材调阈值逼出 2 人没有意义。聚类全程**代码写死纯 CPU**（避开 server 内常驻 IndexTTS2 显存竞争、1015 次 batch=1 小前向 GPU 无优势、无卡环境可跑，实测 8–12 ms/段、1015 段约 10.2 s）；只有 `--samples` / 重配 / 正式配音走 GPU。
- **人工层（终审在人）**：`overrides` 是人工分配唯一真源，同一 `fingerprint` 重建初稿时原样保留（换视频自动清空），`clear_overrides=true` 才清除；`segment_meta` 逐句给 `confidence` 与 `flags`（`low_margin` / `turn_boundary` / `smoothed` / `short` / `silent` / `marked`）；`apply_overrides()` / `set_speaker_ref()` / `add_speaker()`（用指定句原声切片生成**独立** ref，不复用旧音色）/ `remove_speaker()`（最后一人或仍有句子时拒删）/ `clear_dubbed()`（regex 精确删 `tmp/{n}_*_temp.wav` 与 `segs/{n}_*.wav`）。**每次写契约后自动调 `index_tts.reload_voice_map()` 清 `_VOICE_MAP_CACHE`** → 保存即热更新，无需重启 server（旧实现进程内只加载一次，改 json 不重启不生效）。
- **stale 状态**：契约 `version` / `producer` / `fingerprint` 任一与当前不符时 `GET /api/voice/map` 返回 `status="stale"` + `reasons[]`，页面仍可编辑，但 `load_voice_map()` 严格校验会拦下合成 → 明确提示先「重建初稿」；这是 fail-fast，禁止静默复用上一版说话人划分。
- **页面 `#/voices`**（`frontend/src/pages/VoiceMapPage.tsx` + `voiceApi.ts`，`App.tsx` 加菜单、`ArtifactsPage.tsx` 加跳转 Alert）：说话人卡片（试听代表音色 / 换音色 / 删除 / 选中句改为此人 / 新增人）、逐句表格（下拉改人、置信度与 flags、原声 / 配音双试听、标记模糊、批量改、解除锁定、重配选中句）、筛选（关键词 / 说话人 / 只看模糊 / **只看双人混句** / 只看人工）、保存热更新、重建初稿（保留 / 清除人工）；v1.25 起 `mixed` 行可展开成子行列表（原文 / 相对时间 / 置信 / 人工标记 / 逐子行下拉改人），配「重建子行判定」「保存子行 (n)」「重配全部混句」三个按钮（后者一次性把全部混句编号交给 `POST /api/voice/revoice` 清旧 wav，再重跑第 10 步，避免手工勾 363 句）。**v1.26 起每一行都可展开**（`rowExpandable: () => true`）：单字幕行也能改「子行 0」的音色，或填切点秒数点「拆成两段并判定」把一句话里的两个人分开；展开区与顶部都有「重建此行 / 重建选中 N 行」。`mixed` 是分类不是警告，`isAmbiguous()` 把它与 `propagated` 一并排除，否则 363 条混句会淹没「只看模糊句」。
- **视频时间轴双向联动**：复用现成 `GET /api/ad/preview?source=original|burned`（`PreviewFileResponse` 支持 Range，**后端零改动**；判断说话人必须听未配音原声，默认 `original`）；`parseTime` 解析 `tts_tasks.xlsx` 的 `start_time` / `end_time`（`HH:MM:SS.mmm`，单调递增零重叠，末句 4432.5 s vs 原片 4434.254 s → 1:1 对齐）+ 二分 `findAt` / `nextAt`；点表格行 → `seekTo` 跳 `start+0.02` 并播放，「只播这一句」到 `end` 自动暂停；`onTimeUpdate` 反查当前句并高亮 + 自动翻页 + `scrollIntoView`；当前句信息卡、上一句 / 下一句 / 重播、切源保留播放位置、卸载清 `src` 释放读句柄。**`number` 非连续（1–1404，不是 1..1015）**，任何按行号取值都会错。
- **单句重配**：改契约 + `POST /api/voice/revoice` 删该句已落盘 wav + 重跑第 10 步；`tts_main` 的 `save_as` 存在即跳过其余句，`real_dur` 从文件重读、时间轴自动重算。
- **入口/代码**：`core/tts_backend/{voice_map,index_tts,refer_audio,tts_main}.py`、`core/{_8_1_audio_task,_8_2_dub_chunks,_10_gen_audio}.py`、`core/services/{subtitle_turn_service,dub_timeline_service}.py`（后者为配音任务表列表单元格读写的唯一决策，v1.27）；`server/voice_api.py`（v1.26 起 **13 端点**，`prefix=/api/voice`，router 必须在 StaticFiles mount 之前注册；v1.25 加 `PUT /chunks`、`POST /chunks/rebuild`，v1.26 加 `POST /chunks/split`）；`frontend/src/{voiceApi.ts,pages/VoiceMapPage.tsx,App.tsx,pages/ArtifactsPage.tsx}`；`core/pipeline.py`（12 步 + `STEP_NO`）；`config.yaml: index_tts.refer_mode / voice_map_speakers / avoid_cross_turn_merge`；`test/test_voice_chunks.py`（13 例）。
- **双人混句三层处理（v1.25）**：真实 `tts_tasks.xlsx` 1015 行中 **363 行（35.8%）塞了 2~5 条原始字幕**，16 条 `low_margin` 里 11 条（68.8%）是这类 —— 8.1 把过短字幕与下一句拼接、8.2 把多条 `trans.srt` 原文装进同一行 `lines`，两人声音落进同一句，CAMPPlus 抽到混合声纹，分给谁都错。三层互不替代：
  - **B 源头（可选开关）**：`config.yaml: avoid_cross_turn_merge`（默认 **false**）。8.1 合并短句前调 `core/services/subtitle_turn_service.looks_like_cross_turn()`（跨入口决策唯一归属；只认问句结尾 / 中英应答词开头等强信号，不认 `so` / `but` 以免同人续说被误拆），命中则不合并也不延长、两句各保原始时间轴。**默认关闭是因为开启会改变任务表行数 → fingerprint 失效 → 契约与人工种子全废、必须重跑 8.1→10**；旧行为可一键回退。
  - **C 识别与止血**：`voice_map.mixed_numbers(df)` 按 `len(lines)>1` 判混句，`mark_mixed()` 幂等给 `segment_meta` 打 `mixed`（`GET /api/voice/map` 对旧契约只内存补标、不写盘）；混合句当种子会污染说话人原型 → `propagate_from_seeds()` 直接拒绝并提示换纯净句，传播时混句**不自动改派**、保留原标签并标 `mixed`，`stat.mixed` 计数。
  - **A 逐子行音色（真正解决）**：`subline_windows()` 复刻 8.2 的顺序游标，从 `output/trans.srt` 取每条原始字幕时间、换算成相对 `refers/{number}.wav` 起点的窗口（本片 1015 行精确消费 1448 块、100% 命中真实 srt，缺 srt 才等分兜底）；`build_chunk_speakers()` 对每个窗口从原声切片抽 CAMPPlus 嵌入、与各说话人代表音色原型比余弦定人，写 `chunk_speakers` / `chunk_meta`，**人工改过的子行保留**、纯 CPU 零 GPT；`apply_chunk_overrides()` 是子行人工终审唯一写入口，写完照例 `reload_voice_map()` 热更新。合成链只加一个 `line_index` 透传 —— 第 10 步本就逐子行产 `{n}_{i}_temp.wav`、第 11 步逐段拼接且各占 `new_sub_times`，**字幕 / 翻译 / 断点 / 任务表行数一律不动**。
- **单字幕行的两个人（v1.26）**：ASR 有时把两人对话塞进**同一个** `trans.srt` 块，8.2 匹配出来 `lines` 长度 = 1 → 页面上没有展开箭头，也没法逐子行判人。处理办法：`chunk_rows()` 现在返回**所有**行（单行也会给一个「子行 0」，可直接改音色）；确需分开时 `split_rows({number: at|None})` 在 `refers/{n}.wav` 上用 0.45s 窗 / 0.10s 步长滑窗抽 CAMPPlus，找说话人归属翻转且两侧 margin 之和 ≥ `SWITCH_MIN_MARGIN=0.04` 的中段位置（`_find_switch_point()`），或直接用用户手填的切点；`_split_text_at()` 按时间比例在最近的标点/空格处断句，写回 `tts_tasks.xlsx` 的 `lines` 与契约 `chunk_splits`，再 `build_chunk_speakers(force=True, numbers=...)` 判两段音色。**关键**：`subline_windows()` 的 srt 游标按 `n_src`（原始块数，拆分行恒为 1）推进，绝不能按新的 `lines` 长度推进，否则后面每一行的字幕时间都会错位一位；`chunk_splits` 里存的 `lines` 与任务表不一致（例如重跑第 8 步）时 `_active_splits()` 会忽略这条拆分，退回原始行为。**这是唯一会改任务表 `lines` 内容的操作**（行数、`fingerprint`、字幕、翻译、断点都不变），改完必须 `clear_dubbed` + 重跑第 10 步；整行下拉改人时 `apply_overrides()` 会把该行所有 `chunk_speakers` 统一成新说话人，避免子行覆盖人工终审。
- **重建范围**：`POST /api/voice/chunks/rebuild` 不传 `numbers` 时只重判混句（历史行为、不碰人工整行决策）；传 `numbers` 时只处理选中行，单行会先尝试自动拆分、找不到明显切换点就只重判整句音色，人工锁定行除非 `force=true` 一律跳过。
- **约束**：不碰第 2~7 步、不重翻译、不调 LLM（聚类与子行判定零费用）；8.1 的话轮保护默认关闭，开启属于「重新切句」需按重跑口径处理；`GET /api/voice/audio` 只接受 `output/audio` 下的 wav/mp3（路径白名单）；`status=stale` 时禁止合成；改契约不会改已落盘 wav，必须 `clear_dubbed` + 重跑第 10 步；原片 AV1+Opus in MP4 需 Chrome/Edge 支持，黑屏 / 无声时切 `burned` 源或换浏览器；`showVideo=false` 用 `display:none` 隐藏画面但保留 video 元素与时间轴联动。
- **验收（v1.26 更新）**：`create_app().openapi()` 列出 13 个 voice 端点且 Mount 在最后、`pipeline_status()` 返回全局 `no` = 1..12（第 6 步 `gen_sub` / 第 10 步 `gen_audio`）、`align` 与 `gen_sub` 的 markers 不重叠且 `gen_sub.llm_titles == ()`；mode 1/2/3 行为逐字不变（非 mode 4 hook no-op）、mode 4 消费 mock 契约正确命中 SPEAKER / 子行优先 / 缺句报错；producer 不匹配 → 幂等不命中自动重建；人工 overrides 重建后保留；写契约后消费层缓存已清；真实 1015 行 / 1015 句全覆盖、时间码二分（片头前→#1、间隙→#2、片尾后→null、第 501 行→#631）；`GET /api/voice/audio?path=../../config.yaml` 返回 404；A/B 试听 `--samples=N` 写到 `output/audio/voice_exp/`（不写正式产物）；`test_voice_chunks.py` 13 例覆盖话轮信号 / 子行时间窗 / 等分兜底 / mixed 幂等 / 子行写入校验 / 子行音色优先 / 12 步序号 / 第 6 步零 LLM / 文本切分 / 拆分后 srt 游标不偏移 / 单行可展开 / 整行改人压过子行。全量 `653 passed, 1 skipped`。

### FR-35 Qwen3-ASR 本地转录后端（v1.36 已实现）

- **需求/任务**（用户 2026-11-16，先于开发写入）：第 2 步「本地转录」在原 `whisper.runtime = local | cloud | elevenlabs` 之外，增加第 4 个可选运行时 **`qwen3_asr`**，在本机跑 `Qwen3-ASR`（`Qwen/Qwen3-ASR-1.7B` / `0.6B`），并输出**词级时间戳**供 `process_transcription` → `cleaned_chunks.json` → 第 3~6 步使用。默认运行时仍是 `local`（whisperX），不改变任何现有行为。
- **入口/代码**：`core/_2_asr.py: transcribe`（唯一运行时分发）/ `_audio_fingerprint`；`core/asr_backend/qwen3_asr.py`（主环境适配层，只依赖 stdlib + 项目 utils）；`core/asr_backend/qwen3_asr_runner.py`（**在独立 conda 环境里执行**，只依赖 `qwen-asr` / `torch` / `numpy` / stdlib，不 import 项目模块）；`server/settings_api.py: SCHEMA / GROUPS`；`config.yaml: qwen3_asr.*`。
- **约束（不变式，调研实测）**：
  1. **禁止把 `qwen-asr` 装进 `(vl)` 主环境**：PyPI `qwen-asr 0.0.6` 硬依赖 `transformers==4.57.6` + `accelerate==1.12.0`，而 `(vl)` 是 **Python 3.10.0 + transformers 4.52.1**（IndexTTS-2.5 链路锁死，见 FR-33「勿升 transformers/huggingface-hub」），直接装会拆坏配音。官方也要求 fresh isolated environment。方案 = 用户在**独立 conda 环境**装全套依赖，配置 `qwen3_asr.python_exe` 指向该环境的 `python.exe`；主环境通过 `subprocess` 调 runner，**零依赖改动**。`qwen-asr 0.0.6` 是 `py3-none-any` wheel、`requires_python>=3.9`，故 Python 3.10/3.12 均可。
  2. **禁止用 vLLM HTTP 服务替代**：`/v1/audio/transcriptions` 与 `/v1/chat/completions` 只回 `language + text`，**不回词级时间戳**；`process_transcription` 会把 30 分钟整段退化成一个「词」，字幕 / 对齐全崩。词级时间戳只能由 `Qwen3-ForcedAligner-0.6B`（`return_time_stamps=True`）产出。
  3. **ForcedAligner 单段上限 5 分钟、仅 11 语种**（zh/en/yue/fr/de/it/ja/ko/pt/ru/es），而 `split_audio()` 分段目标 30 分钟 → runner 必须把每个分段再切成 `qwen3_asr.max_window_seconds`（默认 **270s**）子窗口，带 `overlap_seconds`（默认 1.5s）并**按 start 去重**；每个窗口时间加回窗口偏移 + `_2_asr` 传入的 `start`，得到**全片绝对时间**。语言不在 11 语种内 → 明确报错并提示改用 whisperX，不静默降级。
  4. **绝不静默回退**：`python_exe` 未配置 / 不存在、`qwen_asr` 未安装、权重未下载、CUDA 不可用、runner 非 0 退出 → 抛**带安装指引的明确错误**，绝不悄悄改用 whisperX 或返回空 segments（与 `refer_mode` 2/3/4 缺 ref 直接报错同口径）。
  5. **返回结构必须与 whisperX 对齐**：`{'segments': [{'text','start','end','words':[{'word','start','end'}]}]}`，时间戳为全片绝对秒；`score` 可缺（下游按 unknown 处理）。中文按字级时间戳、英文按词级，均原样透传，不自行合并。
  6. **checkpoint 指纹必须包含 `runtime` + qwen3 配置签名**：现有 `_audio_fingerprint` 只有 file/size/duration/gap_fill/decode，切换 ASR 后端会复用旧 `seg_*.json` 造成混合转录。新增 `runtime` 与 `qwen3_asr` 关键配置（model/aligner/language/window/overlap）进指纹，改任一项自动作废断点（G-06）。
  7. **whisperX 专属逻辑不进 qwen3 链路**：`asr_hints`（领域提示 / 热词，v1.11 证实诱发跳词）、`vad_gap_fill`、`decode_settings()` 的 beam/hotwords 只对 `local` 生效；qwen3 用 `language=None` 自动检测或显式语言名，不喂 initial_prompt / hotwords。
  8. **硬件边界（本机 RTX 3060 12GB / 6C12T / 16GB）**：`dtype=bfloat16`、`device=cuda:0`；1.7B 权重 ≈3.4GB + ForcedAligner-0.6B ≈1.2GB + 激活，串行加载、**不与 IndexTTS 3.3GB 常驻权重同时占用**（第 2 步与第 10 步本就不同时跑）；`max_inference_batch_size` 默认 **8** 克制显存，`max_new_tokens` 默认 1024。模型在 runner 进程内加载一次并逐窗口复用，进程退出即释放显存。
  9. runner 用 ffmpeg `-ss/-t` 只解码 `[start,end)` 窗口为 16k/mono float32 ndarray 传给 `model.transcribe(audio=(ndarray, 16000))`，**不做全片驻留**；stdout 只输出 JSON，日志走 stderr（避免污染解析）。
- **配置**：`whisper.runtime` 下拉增 `qwen3_asr`；新增 `qwen3_asr.python_exe`（绝对路径，空=未配置）、`model`（`Qwen/Qwen3-ASR-1.7B` / `-0.6B`）、`aligner`、`language`（auto/en/zh/ja/ko/fr/de/es/pt/ru/it/yue）、`max_window_seconds`、`overlap_seconds`、`max_inference_batch_size`、`max_new_tokens`、`device`、`dtype`。设置页是 SCHEMA 数据驱动通用表单 → **只改后端即生效，免重建前端 dist**（FR-11 数据驱动例外）。
- **验收**：`whisper.runtime=qwen3_asr` 时 `_2_asr` 正确分发到新后端且 `asr_hints=None`；纯逻辑单测覆盖 ≤270s 窗口切分、重叠去重、相对→绝对时间偏移、返回结构兼容 `process_transcription`、指纹含 runtime/配置、缺 `python_exe` / 缺依赖 / 非法语言 / runner 失败均明确报错且不回退；全量 `pytest test/ -q` 通过；`node check_single_source.js` clean。**真实模型下载与 GPU 实跑按「命令执行分工」交用户在独立环境执行**，AI 只给完整命令与判定标准，拿到输出后只读验证。

### FR-36 统一字幕源：第 7/12 步双语字幕一致（v1.38 已实现）

- **需求/任务**（用户 2026-11-16「配音的字幕和声音不统一」，先于开发写入）：只读实证结论是**配音版内部字幕↔声音严格对齐**（483 段 `segs/*.wav` 时长与 `new_sub_times` 窗口差值 mean/max 均为 0、拼接无累积漂移，朗读文本与 `dub.srt` 同源 `tts_tasks.xlsx.lines`）；真正不一致的是**压制版与配音版两套字幕**：第 7 步叠 `src.srt`（白英文，原文轴）+ `trans.srt`（黄中文，原文轴）双滤镜，第 12 步只挂 `dub.srt` 一个黄中文滤镜（配音轴），且第 12 步从 `find_video_files()` 取**原片重新烧**，不复用压制版画面。两版各 483 条、文本 479 相同、4 条为 TTS 朗读改写（`C-A-U-S-E-M-E-T-I-C-S→CAUSEMETICS`、`G-O-G-E-V-I-T-I→GOGEVITI`、去 `（HIPAA）`、`Q-Code→QCode`），0.5s 采样 19.6% 有字幕时间点显示不同句子。要求新增 `core/services/subtitle_source_service.py` 作为**唯一决策**：由它决定每个版本烧哪些字幕文件，并按行号把英文原文文本映射到配音轴，使两版同为双语、文字一致，各版仍贴合自己的音轨。
- **入口/代码**：`core/services/subtitle_source_service.py`（唯一决策）；`core/_7_sub_into_vid.py`（第 7 步薄适配）；`core/_12_dub_to_vid.py`（第 12 步薄适配 + 新增 SRC 英文样式）；`core/_11_merge_audio.py: create_srt_subtitle()` **不改动**；`config.yaml: subtitle.source`；`server/settings_api.py: SCHEMA / GROUPS`；`check_single_source.js` 新增唯一归属守卫；`test/test_subtitle_source.py`。
- **约束（不变式）**：
  1. **不强行统一时间轴**：压制版用原文 ASR 轴（`src.srt`/`trans.srt`），配音版用 `new_sub_times` 轴（`dub.srt`）。两者由不同音轨决定，强行合并会让配音与画面/嘴型错位，且需重做 483 段 `atempo` 变速（违背成本原则）。统一的是**字幕清单与文本**，不是秒数；第 12 步仍从原片重烧，不改成「只换音轨」。
  2. **`dub.srt` 一字不改**：它是配音音轨的逐字字幕（朗读版），改动会破坏「屏幕那句 = 耳朵那句」；第 11 步 `create_srt_subtitle()` 保持原样。unified 模式只在它之外**补一条英文原文行** `output/dub_src.srt`（`src.srt` 的文本 + `dub.srt` 的时间窗口，按行号一一配对），不替换 `dub.srt`。4 条朗读改写保留在配音版中文里，属已知取舍，试听后若刺眼再迭代（方案 B），本次不扩大范围。
  3. **按行号配对必须校验条数**：实测 5 份 srt 均 483 条且 `dub.srt` 与 `src.srt` 同序；条数不等 / 缺文件 / 时间码坏一律**抛明确错误**，不截断、不猜、不静默回退 legacy（与 `refer_mode` 缺 ref 同口径）。
  4. **开关默认 legacy、可逆**：`subtitle.source = legacy | unified`，默认 `legacy` → 第 12 步滤镜与此前**逐字相同**（只挂 `dub.srt`），试跑不好用时改一个配置值即回退、不必切分支；`unified` 才叠英文+中文两路。未知值按 legacy 处理并告警。`load_key` 缺键会抛 `KeyError` → service 必须 `try/except` 回退 legacy，**不能因 `config.yaml` 缺键而崩**。
  5. **只影响第 7/12 步的字幕烧录**：不碰第 2~6、8~11 步，不改 `tts_tasks.xlsx`、不动 `segs/*.wav`、不重跑第 10 步 GPU 推理、不调 LLM、不重做变速与音频拼接 → 试跑**只需重跑第 12 步，零 GPU / 零 LLM 费用**（G-05 / G-06）。
  6. **`config.yaml` 被 gitignore 排除**：开关值不随分支切换；从 `exp/unified-subtitle` 切回 `main` 时代码回到 legacy 实现，配置残留 `unified` 不影响 main（main 不读该键）。用户需手改 config 或通过设置页切换；切回后按改 `.py` 口径重启 `python -m server`。**回退前置条件（本次试跑实证）**：exp 与 main 同指 `3093955` 且改动未提交时，`git checkout main` 会**把脏改动带过去，并非真回退**；必须先在 exp 上 `git add -A && git commit`（或 stash）再切，或用 `git checkout main -- <file>` / `git reset --hard v1.37-stable`。提交后短 hash 变化，需 `npm run build` 重建 dist 才能让前后端 hash 一致。
  7. **备份文件命名**：`output_dub_legacy.mp4` 等备份以 `output_` 开头，当前 `input_manifest.json` 存在使 `find_video_files()` 走 manifest 优先、实测不受干扰；但 manifest 丢失时排除规则只精确排除 `output_sub.mp4`/`output_dub.mp4` + `output/output` 前缀，多余 mp4 会触发 `Number of videos found is not unique` → 试跑前确认 manifest 存在，或把备份移出 `output/` 顶层。
  8. **文档滞后一并修正**：第 5 章步骤表曾把第 12 步写成「双字幕」，实际代码只有单滤镜，本次以代码为准更正，并注明 unified 才双字幕（PRD 0.1「文档与代码不一致以代码为准」）。
  9. 设置页是 SCHEMA 数据驱动通用表单 → 只改后端即生效，**免重建前端 dist**（FR-11 数据驱动例外）。
- **配置**：`subtitle.source`（`legacy` 默认 / `unified`），加入 SCHEMA 与「字幕与切句」GROUPS。
- **验收**：`legacy` plan = `[("output/dub.srt","trans")]`、`unified` plan = `[("output/dub_src.srt","src"),("output/dub.srt","trans")]`；`dub_src.srt` 483 条、文本逐条等于 `src.srt`、时间窗口逐条等于 `dub.srt`；条数不等 / 缺 srt / 坏时间码均明确报错；`load_key` 缺键回退 legacy；单测覆盖 SRT 解析、行号配对、开关两档、异常路径；全量 `pytest test/ -q` 通过、`node check_single_source.js` clean、`tsc --noEmit` 通过（无前端改动可跳过 build）。**真实 ffmpeg 重跑第 12 步生成成片按「命令执行分工」交用户在 `(vl)` 终端执行**，AI 给完整命令与判定标准，拿到输出后只读抽帧验证「两行双语、文字与压制版一致、配音轴不错位」。
- **阶段 C 结论（已验收）**：代码位置见「入口/代码」；纯逻辑 / 真实 SRT 只读验收全部通过（24 例专项、483 条逐条配对、legacy 滤镜逐字一致、守卫 clean、前端类型检查与构建通过）。**真实成片已由用户于 2026-11-16 13:05:59–13:08:38 在 `(vl)` 实跑第 12 步（158.8s、`状态=completed`、零 error），AI 只读抽帧验收**：句子中段 7s / 428s / 1136s 三处「压制版 vs 配音版」均为「白英文 + 黄中文」双语且**文字逐字一致、样式一致**，证明统一字幕集合/文本的目标达成；边界时刻 9.15s / 1138.0s 压制版仍有字幕而配音版已空（与该时刻 `src.srt` 窗口 5.703–9.225 / 1134.105–1138.568、`dub.srt` 9.011 / 1137.623 的实测预测完全吻合），**印证「时间轴未统一」是设计取舍而非缺陷**——两条不同音轨不可能共用一条轴还都贴合。浏览器交互仍交用户；开关默认 `legacy`，不试跑时线上行为与 v1.37 完全相同。

### FR-37 构建标识：版本号要能确认代码新旧（v1.39 已实现）

- **需求/任务**（用户 2026-11-16「VideoLingo v3.0.0 为什么我的版本号一直不变捏，我加版本号就是为了确认我的代码是新的呀」，先于开发写入）：只读排查确认 `version.py: __version__ = "3.0.0"` 是**手写静态常量**，全库只在 `439c84a`（11-13 baseline）动过；`frontend/src/App.tsx → getVersion() → GET /api/version → version.py` 每次读同一常量，故提交多少次、重建多少次 dist 页面都显示 `v3.0.0`；PRD 此前**无任何版本号 FR**，`test/test_version.py` 反而把静态 semver 锁死。用户要的是**「当前跑的代码是不是新的」标识**，不是语义版本。要求保留 `__version__` 作产品版本不动，**新增随每次提交自动变化的 build 标识**：后端运行时返回 git 短 hash + 提交时间 + 工作区 dirty 标记；前端在 `npm run build` 时把 git 短 hash 注入 dist；页面同时显示两者，不一致时提示「前端 dist 过期，需重新构建」，根治前几轮反复踩的「改了 `.tsx` 漏 build / 漏 Ctrl+F5 看不到新页面」。
- **入口/代码**：`version.py`（`__version__` 不变 + 新增 `build_info()`）；`server/app.py: GET /api/version`（`GET /api/pipeline` 保持原样）；`frontend/vite.config.ts`（构建期 `define` 注入）；`frontend/src/api.ts` / `App.tsx`（读取与展示）；`test/test_version.py` 增例；`check_single_source.js` 增守卫（禁止第二份 git hash 探测）。
- **约束（不变式）**：
  1. **`__version__ = "3.0.0"` 与 `frontend/package.json.version` 一字不改**：`setup.py`、`launch.py`、`test_version.py` 的既有断言全部保留；`/api/version` 的 `version` 字段保持原值，只**新增** `build` 字段，不破坏任何调用方。
  2. **build id 是唯一决策、只读、容错**：只在 `version.py` 里探测 git（`git rev-parse --short HEAD` / `git show -s --format=%ci` / `git status --porcelain`），`subprocess` 带超时；无 `.git` / git 不可用 / 超时 / 非 0 退出一律返回 `{"hash": "unknown", ...}`，**绝不抛异常、绝不拖慢启动**。`server/app.py` 只转发，不自己跑 git。
  3. **前端 hash 必须是构建期快照**：`vite.config.ts` 在 `npm run build` 时求一次并 `define` 注入常量，**不是**运行时向后端请求的结果；这样它反映的是「dist 是哪次代码构建的」，与运行时后端 hash 对比才有意义。dev server（`:5173`）同样注入，不影响代理。
  4. **过期提示只提示、不阻断**：后端 hash ≠ 前端 hash 时显示黄色 Tag「前端 dist 过期，请重新 build + Ctrl+F5」；两者都 unknown 时不提示。不弹窗、不 reload、不改任何流水线行为。
  5. **零新依赖**：只用 stdlib `subprocess` / `os` / Vite 自带 `node:child_process`；不改 `requirements.txt` / `package.json` 依赖。
  6. **性能**：build 探测只在 `GET /api/version`（页面挂载一次）与构建期各跑一次，不进每个请求、不进流水线步骤；`version.py` 被 `setup.py` / `launch.py` 导入时**不得**触发 git 探测（保持纯常量导入零副作用）。
  7. 本项改了 `.tsx` / `.ts` / `vite.config.ts` → 必须 `npm run build` + Ctrl+F5；改了 `.py` → 重启 `python -m server`。
- **验收**：`GET /api/version` 返回 `{"version":"3.0.0","build":{"hash","committed_at","dirty"}}` 且 `version` 仍是 3.0.0；`from version import __version__` 不触发任何子进程（monkeypatch 断言）；无 `.git` / git 抛错时 `hash=="unknown"`；`test_version.py` 原 4 例全过 + 新增用例；`vite build` 产物含注入的短 hash；`tsc --noEmit` 通过；页面显示 `v3.0.0` + 两个短 hash，dist 过期时有黄色 Tag。真实「重启 server + Ctrl+F5 看页面」按分工交用户执行，AI 给完整命令与判定标准。
- **阶段 C 结论（已验收）**：代码位置见「入口/代码」；`__version__` 与原有 4 例断言零改动，`/api/version` 只增 `build` 字段；专项 24 例全过、`check_single_source.js` clean、`tsc --noEmit` 与 `vite build` 通过且 dist 内含注入 hash；实测 `build_info()` 返回 `3093955` + 提交时间 + `dirty=True`。页面实显与提交后 hash 变化按分工交用户重启 + 强刷验证。

### FR-38 Qwen3-ASR 词级时间戳解析兼容 ForcedAlignResult（v1.40 已实现）

- **需求/任务**（用户 2026-11-16「模型已经下载完成了，重跑了，还是报错了」，先于开发写入）：Qwen3-ASR 独立环境（`qw` / Python 3.12.13 / `qwen-asr 0.0.6`）已装好、权重已下载，第 2 步 3 个 ≤270s 窗口正常启动，但窗口 1 推理后 runner **退出码 5**：`ForcedAligner 时间戳为空或格式不可识别`。只读排查第三方源码确认：`ASRTranscription.time_stamps` 真实形态是 **`ForcedAlignResult` dataclass**（`items: List[ForcedAlignItem]`，item 有 `.text` / `.start_time` / `.end_time`，**单位为秒**，并实现 `__iter__` / `__len__` / `__getitem__`；`_merge_align_results()` 同样返回该类型）；而 FR-35 的 `parse_stamps()` 只假设 `list[list[dict]]` / `list[object]`，非 list 的对象被包成 `[stamps]`，遍历到的 item 是 `ForcedAlignResult` 本身 → 三个字段全取不到 → `words=[]` → 退出码 5。要求修复解析、保持既有不变式；并顺带根治 runner 中文诊断在 Windows GBK locale 下的**乱码**（本次报错信息已不可读）与 stdout JSON 中文转录文本的回传风险。
- **入口/代码**：`core/asr_backend/qwen3_asr_runner.py: parse_stamps()`（**唯一时间戳解析决策**）+ 模块初始化处 UTF-8 设置；`test/test_qwen3_asr.py` 增例。`core/asr_backend/qwen3_asr.py`（主环境适配层）**零改动** → runner 由子进程每次重新加载，**无需重启 `python -m server`**，前端 dist 无需重建、`config.yaml` 无需改。
- **约束（不变式）**：
  1. **真实形态优先**：先取 `time_stamps.items`（`ForcedAlignResult`），并兼容 `__iter__` 可迭代对象、`list[dict]`、`list[object]`、嵌套 `list[list[...]]` 与单个对象；`dict.items` 是方法（callable），**不得**误当成 item 列表。
  2. **时间单位不再换算**：`Qwen3ForcedAligner.align()` 已把 ms 除以 1000 并 round 到秒，`_offset_align_result()` 也以秒计；runner 直接透传 `start_time` / `end_time`，**禁止再除 1000**（否则时间戳缩小千倍、字幕全挤在片头）。
  3. **绝不静默回退 / 不吞字段**：解析不出任何词仍抛明确错误（退出码 5），错误信息带上真实类型名便于下次定位；`text` / `start_time` / `end_time` 缺一跳过该词，空 words 即失败，不伪造时间戳、不回退 whisperX。
  4. **stdout 契约不变**：只输出一个 `{"segments":[...]}` JSON，日志全走 stderr；窗口重叠去重（`win_start + overlap/2`）、相对→绝对时间偏移、退出码 2~6 语义不变；`normalize_runner_result()` 不改动。
  5. **UTF-8 自包含**：runner 在模块初始化时对 `sys.stdout` / `sys.stderr` 强制 UTF-8（`reconfigure` 不可用时安全跳过），不依赖外部环境设 `PYTHONIOENCODING`、不改主环境 `subprocess.run(encoding="utf-8")`；日志与 JSON 中文均可读。
  6. **零依赖 / 零 GPU 用例**：测试用本地 fake `ForcedAlignResult` dataclass 模拟真实形态，不 import `qwen-asr` / `torch`、不加载权重、不跑 CUDA。
- **验收**：`pytest test/test_qwen3_asr.py -q` 全过（含真实形态、旧形态回归、秒单位断言、UTF-8 设置守卫）；全量 `pytest test/ -q` 在阶段 C 回填后转绿；`node check_single_source.js` clean；`py_compile` 通过；**真实 GPU 重跑按「命令执行分工」交用户**，AI 拿到输出后只读验日志：3 个窗口全部完成、退出码 0、`cleaned_chunks.json` 有词级时间戳、无乱码。
- **阶段 C 结论（已验收）**：代码位置见「入口/代码」；只读核对第三方 `qwen-asr 0.0.6` 源码确认 `ForcedAlignResult.items` / `ForcedAlignItem(text, start_time, end_time)` 且单位为秒；专项 **25 passed**（含真实形态端到端 2 窗口聚合）、`py_compile` 与单源守卫通过；主环境零改动 → 无需重启 server / 重建 dist / 改配置 / 清断点。**真实 GPU 重跑已由用户执行并只读验收（2026-11-16 14:04:18→14:05:41，83.0s，`状态=completed` / 第 2 步 `状态=ok`）**：3 个 ≤270s 窗口全部完成、无退出码 5 / ERROR / traceback、中文乱码消失；`output/log/cleaned_chunks.json` 落盘 `version=1` + **1458 词**，时间 `0.000→602.360s`（覆盖 603.2s 切片），首词 `Men 0.0-0.32`、末词 `information 601.4-602.36`。唯一非致命 warning 是 1 个 34 字符广告网址连读词 `thrivecosmeticscausemeticscomjillian` 被 30 字符守卫跳过；`torchcodec is not installed correctly` 是 pyannote VAD 的 UserWarning（VAD 分段成功产出 1 段 0.0→603.2s），非异常。**实测 `speaker_id` 1458/1458 全为 `None`**：FR-35 返回结构本就不含说话人，Qwen3-ASR 不做 diarization；该结论属本次只读调研，不构成 FR-38 缺陷，也不改动代码。

### FR-39 Qwen3-ASR 词级时间戳整形（v1.41 已实现）

- **需求/任务**（用户 2026-11-16「在意的是 Qwen 时间戳整体质量 → 那 71 个零时长词才是真问题，按 C 一起做」，先于开发写入）：FR-38 真实 GPU 跑通后只读复盘 `output/log/asr_checkpoint/seg_0000.json`，3 个窗口共 **1459 个原始词里 71 个 `start=end` 零时长**（4.9%），全是 `to / a / this / of / you know` 等虚词；已核实是 ForcedAligner **原始输出就带**（缺 `start/end` 字段的词 = 0），不是 `process_transcription()` 补零跨度造成的，`cleaned_chunks.json` 残留 70 个。零时长词组成 **62 个连续组**（seg#0 27 / seg#1 26 / seg#2 9，最长组 4 词），按左右锚点统计 **31 组（50%）空隙 ≤ 0**、median 0.000~0.080s、max 4.320s。另有两条长串：`thrivecosmeticscomjillian`（25 字符，471.682–474.562，2.88s，**未超 30 阈值静默保留**，是真正会进字幕的网址口播）与 `thrivecosmeticscausemeticscomjillian`（36 字符，479.042–479.042 零时长，与 `That's` / `Probably` / `the biggest problem` 挤在同一零跨度，是品牌名重复吐出的**幻觉拼贴**，被 `audio_preprocess.py:189` 守卫跳过）。要求在第 2 步 runner 内新增**唯一词级时间戳整形决策**：丢弃零时长幻觉、给可插值的零时长组补时间窗、把连写网址长串切成可读片段并按字符比例分时间、stderr 汇总诊断可观测；**主环境零改动** → 不重启 server / 不重建 dist / 不改 config / 不清断点。
- **入口/代码**：`core/asr_backend/qwen3_asr_runner.py: normalize_words()`（**唯一整形决策**，在 `parse_stamps()` 之后、窗口偏移换算之前调用）；`check_single_source.js` 增 `qwen-word-normalize` 守卫；`test/test_qwen3_asr.py` 增例。`core/asr_backend/qwen3_asr.py: normalize_runner_result()`、`core/_2_asr.py`、`core/asr_backend/audio_preprocess.py`、`server/`、`config.yaml`、前端**零改动**。
- **约束（不变式）**：
  1. **主环境零改动**：runner 由子进程每次重新加载（FR-38 先例）；不新增配置键 → `_audio_fingerprint` 不变、`output/` 断点无需清理；改完只需重跑第 2 步。
  2. **三字段契约不变**：`normalize_runner_result()` 只保留 `word/start/end`，runner **不得新增任何字段**（加了也会被主环境丢弃）；stdout 仍只一个 `{"segments":[...]}` JSON，日志全走 stderr；窗口切分 / overlap 去重 / 相对→绝对偏移一律不动。
  3. **真实对齐到的词一字不动**：`start < end` 的正常词文本与时间**禁止改写**（唯一例外是约束 6 的 URL 长串拆分，且只命中纯 ASCII 长串）；绝不推移邻居的真实时间戳来"腾出"空隙，绝不伪造语音段、绝不回退 whisperX。
  4. **幻觉判据只用双硬条件**：`start >= end` **且** `len(text) > 30` 才丢弃，判为 ForcedAligner 重复幻觉并计数；**禁止引入模糊相似度 / 词频启发式**，避免误删正常长词。主环境 `audio_preprocess.py:189` / `:257` 的 >30 通用守卫保留不动（runner 负责 Qwen 特有的零时长异常，主环境负责通用兜底，两者边界不重叠）。
  5. **插值只在有空隙时发生**：连续零时长组的左锚点 = 前一词 `end`（无前词则组自身 `start`），右锚点 = 后一词 `start`（无后词则组自身 `end`）；仅当 `right - left >= MIN_GAP`（0.02s）时按**字符数比例**线性分配 `[left, right)`；结果 clamp 不越界、保持 `start <= end <= next.start`。
  6. **数学硬约束：空隙 ≤ MIN_GAP 的组保持零跨度**，不制造重叠、不后移邻居；这是本次实测 31/62 组的必然结果，**不得承诺"消除全部零时长"**，须在 stderr 诊断里显式计数 `still_zero`。
  7. **URL 连写长串拆分**：仅命中「非零时长 + 纯 ASCII 字母数字 + `len > 20` + 含 `www` / `com` 词素**且该词素在整串只出现 1 次** + 词素前段 ≥ 4 字符、后段 3~12 字符」的词；在词素处切成「域名 + 后缀」与「路径」两段，按字符比例分原时间窗。**词素表刻意收窄**：B 阶段实测 `org`（`organi*zation*`）、`net`（`inter*net*`）、`com`（`ac*com*modation`）会命中普通英文长词造成误拆，故去掉 `org/net`、保留 `com/www` 并加「全串仅出现一次」+ 前后段长度双校验；`accommodationrecommendation`（`com` 出现 2 次）与 `pneumonoultramicroscopicsilicovolcanoconiosis`（无词素）必须原样保留。中文按字级 / 含空格标点 / 找不到词素一律原样保留，**绝不改其它词**。
  8. **stdlib 自包含**：runner 不 import 任何项目模块（FR-35 约束 1/9），整形逻辑只用 stdlib；零新依赖、零 GPU、零 LLM，O(n) 一遍扫描（实测 1459 词 < 1ms，本机 RTX 3060 12GB / 6C12T / 16GB 无额外负担）。
  9. **诊断不静默**：每个窗口 stderr 打一行 `[Qwen3-ASR] word normalize: dropped=N interpolated=N still_zero=N split=N`，中文可读、UTF-8（FR-38）；全部为 0 时也打印，便于确认整形确实生效。
  10. **单源守卫**：`normalize_words` / 零时长插值决策只许存在于 runner，`check_single_source.js` 禁止 `core/**/*.py` / `server/**/*.py` 出现第二份同名决策（`audio_preprocess.py` 的"缺字段补零跨度"是不同用途，不违规）。
- **配置**：无新增键。整形默认启用；如需回退只需恢复 runner 文件（或按 A→B→C 写新需求加开关），不动 `config.yaml`。
- **验收**：专项用例覆盖「零时长+超长幻觉丢弃并计数 / 有空隙组按字符比例插值 / 无空隙组保持零跨度且不推移邻居 / 正常词文本与时间逐字不变 / URL 长串拆分与词素边界 / 中文与含标点词不误拆 / 单调性 / 诊断汇总 / `main()` 端到端仍只输出一个 JSON」；用真实 `seg_0000.json` 纯只读复算预期（`dropped=1`、约 31 组可插值、约 31 组仍零、`split=1`），**不写盘**；全量 `pytest test/ -q`、`node check_single_source.js` clean、`py_compile` 通过；**真实 GPU 重跑第 2 步按「命令执行分工」交用户在 `(vl)` 执行**，AI 只读验日志诊断行与 `cleaned_chunks.json` 词数 / 零时长残留数。
- **阶段 C 结论（已验收）**：`normalize_words()` 落在 runner，**主环境零改动**（mtime 实测：`qwen3_asr.py` / `_2_asr.py` 为 10:01、`audio_preprocess.py` 为 11-10、`server/app.py` / `settings_api.py` 为 12:43，均早于本次 14:17~14:18 的 4 个改动文件）；三字段契约、stdout 单 JSON、UTF-8 诊断不变，`check_single_source.js` 新增 `qwen-word-normalize` 守卫。专项 **34 passed**（v1.40 基线 25 → +9）、`py_compile` 通过。真实 `seg_0000.json` **纯内存复算、未写盘**：1459 → 1459 词（丢 1 幻觉 + 拆 1 长串成 2，净 0），零时长 **71 → 34（减少 37 / 52.1%）**，合计 `dropped=1 / interpolated=36 / still_zero=34 / split=1`；所有已对齐且 ≤20 字符的正常词文本与时间**逐字不变**（误改 0）、锚点词被推移 0；`thrivecosmeticscomjillian` 拆为 `thrivecosmetics.com 471.682–473.787` + `jillian 473.787–474.562`。**34 个仍零时长是数学硬约束**（左右锚点空隙 < 0.02s，实测 31/62 组），不制造重叠、不后移邻居、诊断里显式计数；ForcedAligner 原始输出缺陷无法靠插值凭空变准，**不得承诺「零时长归零」**。**重跑口径（C 阶段新增实证）**：本次只改 runner、`_audio_fingerprint` 不变，普通重跑会被 `_2_asr._load_done_results()` 从 `output/log/asr_checkpoint/seg_*.json` 直接恢复旧转录 → **整形完全不生效**；`force_clean()` 仅在 `force=true` 时调用（`server/app.py:170`），而第 2 步 `clean_extra=("output/log/asr_checkpoint",)` 正是为清它而存在。故**用户必须点第 2 步「强制重跑」**，日志应出现「已清除 转录 whisperX 的产物、断点与 LLM 缓存」+ `Transcribing 1 segments from scratch`；真实 GPU 重跑按分工交用户，AI 只读验日志。

### FR-40 第 11 步字幕空档静音串音修复（v1.42 已实现）

- **需求/任务**（用户 2026-11-16「再00:02:56这附近也出现了类似的，应该还有很多，多出来的配音（男人在亲热一晚后），没有字幕之类的，不知道哪来的」，并补充「`000043` 出现一次（男人在亲热一晚后）/`000117`、`000125`、`000142` 只有（男人）两个字/`000130`、`000136` 出现一次（男人在亲热一晚后）」，先于开发写入）：方案 C 真实成片后，用户反复听到**字幕里没有的中文配音短片段**——长空档念出片头第一句的前 8 个字「男人在亲热一晚后」，短空档只念出「男人」两个字，且「应该还有很多」。要求查清来源并根治：**字幕空档（gap）必须是真静音**，不得混入任何配音音频；不得改动配音文本、字幕文本、时间轴或第 10 步产物。
- **当前行为（阶段 A 只读诊断结论，非改动）**：
  1. 用户报的 6 个时间点与 `output/dub.srt` 的 >0.25s 字幕空档**一一对应**：43s↔41.67–43.76、77s↔77.74–78.32、85s↔84.87–85.33、90s↔89.83–92.16、96s↔97.22–99.60、102s↔102.39–103.04。全片 **22 个空档里 21 个实测有人声**（rms 3100–4600，与正常配音同量级），2:56 处空档 174.863–176.480s 内 175.0–176.4s rms 2700–5400。
  2. 「男人在亲热一晚后就疏远了」是 `tts_tasks.xlsx` 第 1 行 / `dub.srt` 第 1 条 / `segs/1_0.wav`（00:00:00–00:00:03.110）。空档音频与其**包络**相似度 0.96–0.987，PCM 因 mp3 seek 误差需对齐；最小复现中 bug 版第 2 个「静音」段与 `1_0.wav` 开头 PCM ncc=0.7855。**gap 越长念出的字越多**，正好解释「8 个字 vs 2 个字」。
  3. 排除项：142 段 wav 时长与 `new_sub_times` 窗口**累积误差 0.0s**（不是时间轴漂移）；背景轨在 176s rms=14.7（不是 Demucs 残留或旧配音混入）；`output_dub.mp4` 只有 1 条音轨（不是双音轨叠加）；segs 无重复 wav、拼接按任务表行序（不是字典序错排）。
  4. **根因**：`core/_11_merge_audio.py: build_batch_filtergraph()` 每批只挂 1 个 `anullsrc` 输入并标为 `[SIL0]`，却让**多个 `[SIL0]atrim=0:gap` 分支复用同一个标签**。ffmpeg 最小复现（纯内存、不落盘）：单 gap 时静音段 rms=0.0；2 个 gap 共享 `[SIL0]` 时第 2 段 rms=4145.1；3 个 gap 时第 2/3 段 rms=4094.1 / 3893.0，且串入的正是第一个输入 wav 的开头。现有 `test/test_merge_audio.py` 只断言 graph **字符串**、集成测试 `test_merge_audio_ffmpeg.py` 用的全是**静音 wav**，所以串音完全测不出来。
- **入口/代码**：唯一决策点仍是 `core/_11_merge_audio.py: build_batch_filtergraph()`；`test/test_merge_audio.py`、`test/test_merge_audio_ffmpeg.py` 补回归用例。`merge_audio_segments_ffmpeg()` / `iter_segments()` / `create_srt_subtitle()` / `core/_10_gen_audio.py` / 前端 / `config.yaml` **零改动**。
- **约束（不变式）**：
  1. **签名与语义不变**：仍返回 `(graph, new_prev_end, has_gap)`；`has_gap` 为真时 ffmpeg 命令仍只追加 **1 个** `anullsrc` 输入（索引 `len(batch)`）、仍 `-map "[out]"`、`BATCH_SIZE=80` 分批与终拼一律不动。
  2. **每个 gap 一条独立静音流**：用显式 `asplit=K` 把 `[n:a]` 复制成 `[S0]…[S{K-1}]`，第 j 个 gap 只消费 `[Sj]atrim=0:gap`；**禁止**回到「多个 `atrim` 共享 `[SIL0]`」。已验证 K=2/3 时各静音段 rms 全为 0.0，总时长不变。
  3. **不动任何产物数据**：`new_sub_times` / `lines` / `tts_tasks.xlsx` / `segs/*.wav` / `dub.srt` 内容一字不改；不改时间轴、不重排段落、不改字幕生成逻辑；修复后配音轨总长仍等于最后一段 `end`。
  4. **零新增依赖 / 零配置变更**：不新增 config 键、不改 `_audio_fingerprint`、不清断点、不重启 server、不重建 dist；改完**只需重跑第 11 步（不带 force）与第 12 步**，**绝不重跑第 10 步**（1448 段 IndexTTS GPU 合成，本机 RTX 3060 12GB 上最耗时）。
  5. **回归测试必须能抓到串音**：新增用例须用**非静音**音频并实测各静音段 rms 接近 0、clip 段 rms 明显 >0；仅靠 graph 字符串断言不算验收通过。
- **配置**：无新增键。回退只需恢复 `build_batch_filtergraph()` 旧实现（或按 A→B→C 另写需求加开关），不动 `config.yaml`。
- **验收**：专项覆盖「多 gap 显式 asplit 分支数 / 每分支只被消费一次 / 无 gap 时无 asplit 与 anullsrc / 跨批 prev_end 不变」；真实 ffmpeg 集成用例（有声明细 + 多段不同长度 gap）断言**每个静音段静音、每段配音仍在**；全量 `pytest test/ -q`、`node check_single_source.js` clean、`py_compile` 通过。**真实重跑按「命令执行分工」交用户在 `(vl)` 执行**（第 11 步继续跑 → 第 12 步），AI 只读验收：全片 22 个字幕空档 rms 应降到接近 0、字幕与配音文本/时间不变、`output_dub.mp4` 中不再出现无字幕的「男人」短句。
- **阶段 C 结论（已验收）**：`build_batch_filtergraph()` 是唯一改动的生产代码，显式 `asplit=K` 让每个 gap 独占一条静音流；函数签名 / `has_gap` / 单个 `anullsrc` 输入 / 分批与终拼 / `-map "[out]"` 全部保持，**零新增依赖、零配置变更、不清断点、不重启 server、不重建 dist**，`_audio_fingerprint` 不变。专项 **11 passed**（6 原有 + 2 新增结构断言 + 1 新增真实 ffmpeg 有声回归），全量 **754 passed / 1 skipped**（v1.41 基线 751 passed / 1 skipped → +3），`py_compile` 通过、`node check_single_source.js` clean，流程守卫 5 例全过。**真实产物只读复算（不落盘、未改 output）**：前 45 段覆盖 0–205.84s，6 个报障空档 rms 由 3100–4600 全部降为 **0.0**，3 个有字幕配音段 rms `3495.8 / 4074.7 / 4776.7` 保持不变，输出时长与 `new_sub_times` 末值一致 → 只删「多余配音」，配音文本、字幕文本与时间轴一字未动。**边界**：本次只根治「字幕空档串入片头第一句」这一确定性缺陷；FR-36 方案 C「统一的是字幕集合/文本、不是时间轴」的历史结论不变，任何其它听感上的音画错位仍需在真实重跑后单独诊断，不得用本 FR 承诺一并解决。**真实重跑按「命令执行分工」交用户在 `(vl)` 执行**：第 11 步「继续跑」（不带 force）→ 第 12 步；`segs/*.wav`（142 段）与 `tts_tasks.xlsx` 原样复用，**不重跑第 10 步**；**线上实跑验收已完成（用户 2026-11-16 15:31–15:32 在 `(vl)` 只重跑第 11、12 步，AI 不代跑真实合成）**：产物 `dub.srt` / `dub.mp3` / `output_dub.mp4` 已重建，`segs/*.wav` 与 `tts_tasks.xlsx` 时间戳仍为 14:59（第 10 步确实未重跑）；只读复测 **142 条字幕文本与末条时间 594.594–601.455s 未变、全片 22 个空档 rms 全部 0.0**（修复前 21 个为 3100–4600），成片同位置只剩背景轨底噪 2.3–14.2，有字幕配音段 rms 3304–4538 正常有声；用户听感确认「修复了」。FR-40 全流程（A 需求 → B 开发测试 → C 回填 + 线上验收）闭环。

### FR-41 配音时间轴按原片真实发声区间定位（v1.43 已实现）

- **需求/任务**（用户 2026-11-16「`00:08:02,269 --> 00:08:07,214` … 这句的其实英文，大概是 07 才开始的，但是 02 就开始展示了，然后配音的也就是再 02 就开始了，到真正说话的时候没有声音了」「我们必须要修这种问题，不然后影响配音质量」，并在「A 下游能量护栏 / B 上游 ASR 时间戳 / C A+B 一起做」三方案中**拍板 C**，先于开发写入）：方案 C 成片里 `dub.srt` #113（00:08:02.269–00:08:07.214）比英文真正开口早约 5.5 秒，配音在 08:02 就念完，而 08:07 真正说话时配音轴是空的。要求**上游 ASR 词级时间戳与下游配音轴两层一起修**，根治「配音提前 + 该说话时没声音」，不得只改一处或只做表面缓解。
- **当前行为（阶段 A 只读诊断结论，非改动）**：
  1. `src.srt` / `trans.srt` #113 = `478.722–491.042`（**12.32s**），`dub.srt` / `dub_src.srt` #113 = `482.269–487.214`（**4.945s**）；任务表 `number=102` 的 `start_time=478.722` / `end_time=491.042` / `real_dur=4.946` / `cut_off=1`。
  2. 波形实测：`vocal.mp3` 在 `487.2–487.8` rms≈1（静音断点）、`487.8–491.0` rms=4084（真正开口）；`dub.mp3` 在 `482.3–487.2` 有声、`487.2–491.0` rms=0.0。用户听感与波形完全一致。
  3. 全片规模：142 行 `dub` 起点相对 `src` 起点偏移均值 −0.02s、中位 0.00s，`|偏移|>2s` 仅 7 行 → **整体轴没漂移，是少数极端行**；#113 是全片最极端（起点 +3.55s 最大、窗口差 7.38s，第二名 3.69s）。9 行「原文窗口比配音窗口长 >2s」；19 个 >0.5s 配音空档里 **15 个原片明显有人声**（rms 2909–7284）。与 FR-40 的区别：FR-40 修的是「空档里串进片头音频」，本 FR 修的是「空档位置本身排错」。
- **根因（两层叠加）**：
  1. **上游**：ASR 原始断点 `output/log/asr_checkpoint/seg_0000.json` 里这句 10 个英文词被 ForcedAligner 均匀铺在 `478.722–491.042`，`problem / that / I / see` **各恰好 1.52s、起点间隔 3.04s 规律分布**（不是清洗阶段造的）。`src.srt` 窗口取句子首词→末词，于是把前面约 9 秒不属于这句话的音频算进了这一句。
  2. **下游**：`core/_10_gen_audio.py: merge_chunks()` 在 chunk（#100–#102）里只按**累积配音时长**排轴：`466.642 + 6.780 + 5.782 + 3.065 = 482.269`（正是 `dub.srt` 起点），`+4.946 = 487.215` 结束；`chunk_end_time = 491.042` **只约束「不超过」，不要求铺满或对齐**，剩下 3.83s 成为尾部空档，恰好压住真实人声。
- **入口/代码**：上游唯一改动点 `core/asr_backend/qwen3_asr_runner.py`（`normalize_words()` 所在层）＋ `test/test_qwen3_asr.py`；下游唯一决策点新建 `core/services/dub_axis_service.py`，`core/_10_gen_audio.py: merge_chunks()` 消费它。其余生产代码、`config.yaml`、`settings_api.py`、前端、`core/_11_merge_audio.py`、`subtitle_source_service.py` **零改动**。
- **约束（不变式）**：
  1. **上游只收缩、不搬运**：识别「连续 ≥3 个词、每个时长 ≥1.0s、相邻起点间隔近似相等、组跨度 ≥5s」的均匀摊词组，把命中词的时长压到由字符数决定的合理上限（`≈0.08×字符数+0.15s`），**起点、顺序、词文本、词数一律不变**，组外词一字不动。**禁止语义重定位、禁止用能量大小挑「真语音段」**：实测广告/非本句段 rms=3831 与真实主句 rms=4084 相当，纯能量判据会把词贴到广告段；FR-39「不得承诺零时长归零、不为腾空隙推移邻居真实时间」继续有效。stderr 增加 `word stretch:` 诊断，stdout 仍只一个 JSON、三字段契约不变。
  2. **下游只命中少数极端行**：仅当「该行是 chunk 末行」＋「原文窗口比该行配音总时长长 >2s」＋「窗口后段原片人声 rms ≥ 前段 ×0.8」三条同时成立才调整；**其余 133+ 行的 `new_sub_times` 逐行一字不变**。
  3. **右对齐、不改变速**：命中行把该行所有子行的 `start/end` 整体平移 `delta = chunk_end_time − cur_time`（配音终点落在 `chunk_end_time`），富余静音留在窗口前部，由第 11 步既有的 `asplit=K` 静音分支插入；不改 `speed_factor` / `keep_gaps` / `process_chunk()` / `chunk_end_time` 算法，不触发 `time_diff > 0.6` 异常，不改 `dub.srt` 文本。
  4. **不动任何字幕与产物数据**：`lines` / `src_lines` / `new_sub_times` 的非命中部分 / `tts_tasks.xlsx` 其它列 / `segs/*.wav` / `dub.srt` 文本 / `plain_time_pairs()` 唯一落盘整形全部保持；方案 C「统一的是字幕集合/文本、不是时间轴」的历史结论不变。
  5. **零新增依赖、可一键回退**：音频能量按窗口**按需**用 ffmpeg 解码小段（不全量驻留 16GB 内存），纯逻辑判据与 I/O 分离便于单测；开关 `dub.axis_guard` 用 `load_key` + 异常兜底默认 `true`，**不改 `config.yaml` 即可生效**，用户手改 `dub.axis_guard: false` 即回退到旧累积轴；不改 SCHEMA、不改前端、不重建 dist。
  6. **回归测试必须能抓到错位**：上游用合成词表验证收缩与保序，下游用合成能量值验证命中/不命中与「非命中行时间不变」；仅靠字符串断言不算验收。
- **配置**：可选 `dub.axis_guard`（默认 `true`，`false` 回退）；不新增 SCHEMA 键、不写 `config.yaml`。
- **验收**：专项覆盖「等长/非等长/短组/中文/零时长不误伤」「收缩后起点顺序词数不变」「命中三条件缺一即不动」「右对齐后终点 = chunk_end_time、非命中行逐行一致」「开关关闭零改动」；真实产物只读复算（不落盘）：#113 配音窗口由 `482.269–487.214` 变为约 `486.096–491.042`，与真实人声 `487.8–491.0` 重叠 ≥3s，且全片非命中行 `new_sub_times` 完全一致；全量 `pytest test/ -q`、`node check_single_source.js` clean、`py_compile` 通过。**真实重跑按「命令执行分工」交用户在 `(vl)` 执行**，AI 只读验收。
- **重跑口径（成本须事先写明，用户成本敏感）**：改 runner → 必须**强制重跑第 2 步**（清 ASR checkpoint，否则复用旧词时间戳不生效），随后第 3/4/5/6 步（LLM 分句/翻译费用）、第 8 步、第 10/11/12 步；第 9 步参考音频不受影响；第 10 步 `core/tts_backend/tts_main.py:35` 会跳过已有 wav，**不重合成 1448 段 IndexTTS GPU**，只重做 CPU 变速与时间轴。改 `.py` → 重启 `python -m server`；未改前端 → 不重建 dist。

- **阶段 C 结论（已实现，线上重跑待用户在 `(vl)` 执行）**：① 上游 `core/asr_backend/qwen3_asr_runner.py` 在 `normalize_words()`（仍是 runner 内**唯一**词级整形决策点）内新增第 5 步 `detect_uniform_stretch()` / `_shrink_uniform_runs()`：识别「≥3 个连续 ≥1.0s 词、起点间隔相对偏差 ≤15%、跨度 ≥5s」的均匀摊词组，按 `0.15 + 0.08×字符数` 秒封顶词时长并裁剪到下一个词的 `start`，**起点、顺序、词文本、词数一字不动**；stats 增加 `stretch_groups / stretched / stretch_seconds`，stderr 增加 `word normalize: … stretch_*` 诊断，stdout 仍只一个 JSON、三字段契约不变。`check_single_source.js` 的 `qwen-word-normalize` 守卫已把两个新函数纳入 `home`。② 下游新建 `core/services/dub_axis_service.py`（唯一配音轴定位决策，纯逻辑与 I/O 分离、stdlib-only）；`core/_10_gen_audio.py: merge_chunks()` 在 `cut_off` chunk 结束后，仅对末行调用 `axis_delta()`：命中则把该行所有子行整体平移 `delta = chunk_end_time − cur_time` 并 `cur_time = chunk_end_time`，富余静音留在窗口前部由第 11 步 `asplit=K` 插入；`dub.axis_guard` 用 `load_key` + `KeyError` 兜底默认 `true`，**不改 `config.yaml` 即可生效**，手改 `false` 一字不改地恢复旧累积轴。`process_chunk()` / `_speed_plan()` / `BATCH_SIZE` / 第 11 步 / 前端 / SCHEMA / 字幕文本 / `segs/*.wav` 全部零改动。③ 专项 `pytest test/test_qwen3_asr.py -q` **42 passed**（+8 FR-41 用例，原 FR-39 两处 stats 精确断言同步扩到 7 键）、`pytest test/test_dub_axis.py -q` **15 passed**；全量 `pytest test/ -q` **777 passed / 1 skipped**（v1.42 基线 754 → +23）；`py_compile`、`node check_single_source.js` clean、流程守卫 5 例全过。④ **真实产物只读复算（不落盘、未改 `output/`）**：按现有 `tts_tasks.xlsx` 的 124 行 / 84 个 chunk 调用护栏，命中 **5 个末行**（number 20 / 21 / 52 / 70 / 102），**119/124 行 `new_sub_times` 逐行不变**；用户报障的 #102 由 `482.269–487.215` 右移到 **`486.096–491.042`**，与真实人声 `487.8–491.0` 重叠 3.24s；平移后全片 142 段**负 gap 0 个**，#52 新终点 258.538 与下一段 258.638 仍留 0.1s，末段终点 `601.455` 不变。⑤ **边界（必须如实说明）**：上游只消除「1.52s 均匀假词时长」并输出诊断，**不搬运词、不承诺把 12.32s 窗口收缩**——实测广告/非本句段 rms 3831 与真实主句 4084 相当，纯能量判据会把词贴到广告段，故禁止语义重定位；真正把配音对齐到真实发声段的是下游护栏。FR-36 方案 C「统一的是字幕集合/文本、不是时间轴」与 FR-39「不得承诺零时长归零」的结论均不变。⑥ 本次 AI 未跑真实合成与 GPU 转录；线上重跑按「命令执行分工」交用户，口径见上条（强制第 2 步 → 3/4/5/6 → 8 → 10/11/12，第 10 步跳过已有 wav 不重合成 1448 段），跑完日志贴回由 AI 只读验收。

### FR-42 原声字幕 #119 起点错位诊断（v1.44，仅诊断落档，未改代码）

- **需求/任务**（用户 2026-11-16「`output_sub.mp4` 000759 这块字幕还是不对，000807 我听语音是 Probably the biggest problem that I see is that，`That 's` 是前面读网址那里的单词」，并贴出另一软件转录作参照；随后在「A 检测错位并只移那几个词 / B 不改词位只改切句 / C 先不改代码」三方案中**拍板 C**，先于任何落档写入）：查明 `src.srt` #119 从 `00:07:58,722` 起显示 `That 's Probably the biggest problem that I see is that`、而原声此时仍在读网址的原因；本条**只做诊断与方案留档，不改代码、不改测试、不改配置、不动 `output/` 产物**。
- **当前行为（阶段 A 只读诊断结论，非改动）**：
  1. `src.srt` #119 = `478.722–491.042`（12.32s），文本 `That 's Probably the biggest problem that I see is that`；#118 `of 20 off your first order` = `476.322–478.722`。
  2. 断点 `output/log/asr_checkpoint/seg_0000.json`（3 个子段，652+641+166=1459 词，mtime 2026-11-16 16:14）词级时间戳：`order`→`478.322`；`That's` `478.722–478.962`；`Probably` `478.962–479.042`；`the` `479.042–479.842`；`biggest` `479.842–479.922`；`problem` `479.922–480.632`；`that` `482.962–483.432`；`I` `486.002–486.232`；`see` `489.042–489.432`；`is` `490.722–490.802`；`that` `490.802–491.042`；`people` `491.042`。即这 9 个词被铺在 `478.7–491.0` 共 12.3s，词间出现 2.3s / 2.6s / 2.8s 的大间隙；其中 FR-41 已把均匀摊词的时长压短，起点未动。
  3. 原片实测静音：绝对约 `485.00–485.34`、`485.90–486.21`、`486.68–487.96`，与另一软件转录「(8:08) Probably the biggest problem…」及听感一致，真实开口约 `487.8`；该 8 词 35 字符按常速约 3s，恰与 `487.8–491.0` 吻合。**`That's` 属于广告尾句 `That's Thrive Cosmetics. C-A-U-S-E-M-E-T-I-C-S.com slash Jillian.`，不是 `Probably` 句的一部分，不得删除。**
  4. 全片 1459 词中词间隙 ≥1.0s 共 25 处，其中 `1168–1171` 连续 4 处（2.33/2.57/2.81/1.29s）即本案；其余 21 处均为孤立单次，多为正常停顿。**本案特征 = 同一句内连续多个大间隙**，不是单个停顿。
- **根因**：上游 Qwen3 ForcedAligner 在广告口播网址拼写（`thrivecosmetics.causemetics.com/jillian` / `C-A-U-S-E-M-E-T-I-C-S`）处没有稳定声学锚点，把紧随其后的 `Probably … is that` 前 9 个词锚到网址朗读的时间区间，真实开口在 `487.8`。该 36 字符零时长词 `thrivecosmeticscausemeticscomjillian` 已被 FR-39 丢弃（**此点由 ASR 文本与词表推断，未用真实模型复现**）。`audio_preprocess.process_transcription()` → `cleaned_chunks.json` → `src.srt` 只继承该时间戳，下游**不重定位词**，故字幕错位在 ASR 层产生。
- **入口/代码（只读，未改）**：`core/asr_backend/qwen3_asr_runner.py: normalize_words()`（唯一词级整形点）、`core/asr_backend/qwen3_asr.py`、`core/_2_asr.py`、`core/asr_backend/audio_preprocess.py`。
- **候选方案（留待用户决定，本条不实施）**：
  - **方案 A（上游检测并重排）**：在 `normalize_words()` 内新增检测，判据为「同一连续词串内 ≥3 个词间隙 ≥1.0s」且与 FR-41 约束 1 的关系须在新 FR 中显式放宽——FR-41 约束 1 禁止「用能量挑真语音段」，故判据**不得只凭 RMS**，应以间隙结构为主、静音断点为佐证；命中后只重排**那串前置异常词**到其后真实发声区间，`That's` 不删、不移出广告句，文本与词数不变。风险：可能误伤，须用 25 处大间隙真实数据逐个复算并写明命中范围。
  - **方案 B（只改切句）**：不改时间戳，在生成字幕时词间隙 >2s 强制断句。`Probably` 仍显示在 `478.96`，**不能**让字幕与读网址时的声音对上，只治标。
  - **方案 C（本次选定）**：仅诊断落档，待用户重跑第 2 步确认现象稳定后再定方案。
- **约束（不变式，对未来实施方案同样有效）**：① 不得删除有效词，尤其 `That's`；② 不得仅凭 RMS 搬词（FR-41 已实测广告段 rms 3831 ≈ 主句 4084）；③ FR-39 / FR-40 / FR-41 结论与第 11 章「不要回退」条目全部保持；④ 配音文本与字幕不一致、配音改词等问题**不并入本条**，需用户另行明确扩大范围；⑤ 任何代码改动须先在新的 FR / 变更记录行中写需求，再开发。
- **配置**：无。
- **验收**：本条仅要求 PRD 落档；无代码、无测试、无产物改动，工作区原有未提交改动与 `output/` 全部保留。
- **重跑口径**：本条无需重跑。若用户后续选定方案 A，须**强制重跑第 2 步**（清 `output/log/asr_checkpoint`），再按需 3–6、8、10–12（第 10 步复用已有 wav），真实重跑交用户在 `(vl)` 执行，AI 只读验收。
- **阶段 C 结论（已落档，未改代码）**：已核实 `seg_0000.json` 当前状态（3 个子段共 1459 词）并复算 478–493s 词表、全片间隙分布；诊断与候选方案写入本条；未改任何代码、测试、配置与 `output/` 产物。待用户决定方案 A / B 或先重跑第 2 步确认现象。

### FR-43 #119 错位的 ASR 源头对照实验（v1.45，先实验后决策，不改生产链路）

- **需求/任务**（用户 2026-11-17「先试试重新转写，调整参数等等方式，看看能从根源上解决这个问题，不行再用 A，A 这种其实会产生很多意外的影响，我害怕，比如中间停留超过 1 秒又乱了」）：在不动生产 ASR 链路的前提下，用**只读对照实验**验证 07:58–08:08（绝对约 476–495s）错位能否从 ASR 源头消除；方案 A（FR-42，检测大间隙并搬词）降为后备，仅当本条实验全部无效、且用户再次拍板时才启动。新增一个独立诊断脚本 `check_asr_probe.py`（仓库根，与 `check_vad_gaps.py` 同级，**不被流水线 import**），在 `qw` 环境运行，按组对同一段音频重新转写并输出词级时间戳对照。
- **当前行为（只读取证）**：① `_2_asr.py` 送入 ASR 的是 `output/audio/raw.mp3`（603.16s，`demucs: false`），不是 `vocal.mp3`；② 断点 `segments.json` 为单段 `[0, 603.16]`，按 `qwen3_asr.max_window_seconds=270 / overlap_seconds=1.5` 切 3 窗 `[0,270] [268.5,538.5] [537,603.16]`，错位词位于第 2 窗；③ `qwen3_asr_runner.main()` 调 `model.transcribe(audio, language=spec["language"], return_time_stamps=True)`，**不传 `context`**，`language=auto` 即 `None`；④ `qwen_asr` 库对带时间戳的音频会再按 `MAX_FORCE_ALIGN_INPUT_SECONDS=180` 在低能量处内部切块，所以 270s 窗口实际被切成更小块，该切点是否影响本案**未验证**；⑤ 该 36 字符网址词被 FR-39 丢弃，说明 ASR 文本里网址部分是一个超长连写词，ForcedAligner 对它没有稳定锚点（推断）。
- **实验分组（对照变量）**：`G0` 复现基线（窗口 `[268.5,538.5]`、`language=auto`、无 context）；`G1` 同窗口强制 `English`；`G2` 同窗口加 `context` 提示（品牌与网址词）；`G3` 短窗口 `[465,500]` auto；`G4` 短窗口 English；`G5` 短窗口 English + context；`G6` 中窗口 `[440,520]` English。每组输出：整窗转写文本、476–495s 内逐词 `word/start/end`、`Probably` 与 `That's` 起点、窗口内 ≥1.0s 词间隙数、文本是否含网址拼读（`cause` / `.com` / `slash`）。
- **判成功标准**：`Probably` 起点落在 `487.0–488.8`（真实开口约 `487.8`），`That's` 起点 ≤ `486.5`，且 `That's … Probably` 之间不再出现连续 ≥3 个 ≥1.0s 的词间隙。仅某组满足、且转写文本不丢词（词数与基线相差 ≤3%）才可作为落地候选。
- **约束**：① 脚本**只读**：不 import 流水线模块，不写 `output/`，不改 `config.yaml`，不改断点；结果写到系统临时目录（默认 `<tempdir>/asr_probe/result.json`）并同时打印；② 复用 `qwen3_asr_runner.py` 的 `decode_window` / `parse_stamps` / `normalize_words`（`importlib` 按路径加载），**不复制词级整形逻辑**，runner 本身零改动；③ 真实 GPU 运行交用户在 `qw` 环境执行，AI 只读验收输出；④ 模型一次加载、各组串行，避免多实例同时占用显存；⑤ `context` 提示可能诱发丢词（v1.11 的教训），所以 G2/G5 必须同时对比词数；⑥ 本条**不决定**落地方式。若某组有效，再另开 FR 评估落成配置项 / 局部补转 / 改 runner，并按需强制重跑第 2 步及 3–6、8、10–12；⑦ FR-39 / FR-40 / FR-41 与第 11 章「不要回退」条目全部保持；⑧ 配音改词与配音时间戳问题仍不并入。
- **配置**：无（脚本参数：`--groups`、`--out`、`--audio`，默认 `output/audio/raw.mp3` 只读）。
- **验收**：① 脚本语法与纯函数测试通过（`summarize_words()` / `verdict()` 用合成词表断言）；② `node check_single_source.js` clean；③ 全量 `pytest test/ -q` 无回归；④ 用户在 `qw` 环境跑完后贴回输出，AI 只读判定哪组达标；⑤ 工作区原有未提交改动与 `output/` 产物零改动。
- **重跑口径**：本条本身无需重跑任何流水线步骤。仅当后续 FR 把某组参数落地时，才需强制重跑第 2 步（清 `output/log/asr_checkpoint`）。
- **阶段 C 结论**：用户已在 `qw` 环境完成两轮真实 GPU 对照实验，G0 两轮均稳定复现生产错位（`Probably=478.962s`，连续 4 个 ≥1s 大间隙），基线可信。G1 强制 English 与 G0 相同；G2 context 将 `Probably` 移至 482.442s 但仍提前且间隙连续 3 个，并使观察区间词数增加；G4/G5 的 35s 短窗均得到 `Probably=481.48s`，间隙消失但词数与基线相差超 3%，不达标；G6 的 80s 窗口识别出 C-a-u-s-e-m-e-t-i-c-s 单字母，但 `Probably=484.48s` 仍提前约 3.3s，且 `the=488.48s`，词数也超容差。故所有已实测组 G0、G1、G2、G4、G5、G6 均 FAIL，没有已验证参数组可用于生产；G3（35s 短窗、auto）尚未运行。结果表明已测的语言、context、窗口长度设置不足以修复，且“转写文字缺少网址拼读是唯一根因”不能成立（G6 已有拼读而时间仍错）。方案 A 仍为后备，启用前须另开 FR 并处理与 FR-41 约束 1 的关系。FR-42 中「待用户决定方案 A / B」以本条为准（A 降为后备）。

### FR-44 #119 ASR 文本与 ForcedAligner 分层诊断（v1.46，仅只读实验）
- **需求/任务**：FR-43 已证实调语言、context、窗口大小及补出网址拼读仍不能修复 `Probably` 的时间戳。新增只读分层实验，将 ASR 文本识别和 ForcedAligner 对齐拆开：同一音频窗口先以 `return_time_stamps=False` 取得纯文本，再将该文本显式交给 `model.forced_aligner.align()`；并与同窗口 `return_time_stamps=True` 的库内组合流程对照。至少测试覆盖错位区间的 80s 中窗及 270s 生产窗，核对 `Probably` / `That's` 的纯文本存在性、直接对齐起点、完整词序及时间范围，判断偏差来源于 ASR 文本、ForcedAligner，或库内长音频分块/合并流程。只做诊断，不自动修复。
- **当前行为（只读取证）**：第三方 `qwen_asr` 的 `transcribe()` 在 `return_time_stamps=True` 时先分音频块、分别 ASR，再把每块的识别文本与音频交给 `forced_aligner.align()`，之后按块偏移并合并；`return_time_stamps=False` 不调用 ForcedAligner。ForcedAligner 可通过 `model.forced_aligner.align(audio, text, language)` 独立调用。FR-43 已测结果显示 G0 `Probably=478.962s`，G6 虽识别网址单字母但 `Probably=484.48s`，尚不能判定是哪一子阶段导致错位。
- **实验与输出**：两种窗口（相对生产窗口 `[268.5,538.5]` 与诊断窗口 `[440,520]`）分别运行纯 ASR 文本、同文本直接 ForcedAligner 对齐、库内 ASR+ForcedAligner 整体时间戳三条路径；对能执行的路径输出原始文本、词级 `word/start/end` 及统一绝对时间，标注 `That's`、`Probably` 与词序。文本需注明输入窗口及其边界；路径间文本不一致时不得把时间差单独归因于对齐器。
- **约束**：独立根目录诊断脚本，不 import 项目流水线、不修改第三方库、不写 `output/`、断点、配置或字幕；模型只加载一次、组间串行；结果仅写系统临时目录；仅 `qw` 环境 GPU 实跑由用户执行。禁止自动改词、搬移词、覆盖字幕，禁止把大间隙本身作为时间修正证据；保留正常停顿，方案 A 仍须用户另行确认。
- **验收**：脚本和纯函数测试通过；`node check_single_source.js` clean；全量测试无回归；GPU 对照输出能区分“文本缺词/错词”“同一文本直接对齐错位”“库内分块/合并与直接对齐不同”三种现象，结论只作根因证据、不作生产修复。
- **重跑口径**：无需运行流水线步骤，不清断点，不触碰 `output/`。
- **阶段 C 结论**：已新增根目录只读诊断脚本 `check_asr_layers.py` 与 `test/test_asr_layers.py`。脚本独立加载现有 runner 的 `decode_window()`，每个窗口串行比较纯 ASR、对纯 ASR 文本直接调用 ForcedAligner、库内 ASR+ForcedAligner 组合三条路径；输出原始文本、词级相对/绝对时间及 `That's` / `Probably` 起点。兼容直接 `ForcedAlignResult.items` 返回形状，直接对齐结果无文本字段时由词条拼接文本。脚本不导入流水线、不写 `output/`，默认报告写入系统临时目录。静态 AST 解析通过；8 个纯函数测试通过；`node check_single_source.js` clean；只读核对本机 `qwen_asr` 源码返回值形状与解析逻辑一致。用户已在 `qw` 环境完成真实 GPU 三路径实验，报告位于系统临时目录 `asr_layers/result.json`。诊断窗 `[440,520]` 中，纯 ASR 与库内组合文本规范化一致；直接 ForcedAligner 将 `Probably` 标在 484.56s（直接对齐词条终点 485.12s），库内组合也为 484.56–485.12s，而 `the` 到 488.48s 才开始，`biggest` 到 488.64s 才开始，形成约 3.36s 的跨词空档；因此诊断窗证据支持同文本 ForcedAligner 对 `Probably` 所在词序区间的时间定位异常，且库内流程继承了该异常，不是库内分块/合并独有偏差。该窗口 `That's` 为广告尾句词，时间约 440.48s，应保留。生产窗 `[268.5,538.5]` 的纯 ASR 与库内组合文本规范化不一致，故该窗口不能单独用来归因对齐器；直接对齐路径 `Probably=479.042s`，库内组合 `Probably=485.14s`，差约 6.10s，显示两条时间链路也存在显著差异，但需结合文本不一致谨慎解释。结论：ASR 文本内容与时间对齐需分层处理；诊断窗已捕获直接对齐异常，库内长窗结果另有路径差异；本实验只定位证据，**问题尚未修复**，未改生产链路，也不能据此自动搬移或删除词。`pytest test/test_asr_layers.py -q` 与全量 `pytest test/ -q` 未运行，不能宣称全量回归通过。无流水线重跑，`output/` 与断点未触碰。方案 A 仍须用户另行明确确认。

### FR-45 #119 库内组合流程 ForcedAligner 实际输入追踪（本地实现完成，GPU待验）
- **需求/任务**：在 FR-44 只读诊断基础上，补充记录 `return_time_stamps=True` 组合流程内部每次 ForcedAligner 调用的实际输入，严格比较库内组合路径与直接对齐路径是否使用了相同音频、文本和语言，避免仅凭最终规范化转写相同就推断对齐输入一致。
- **当前行为**：原 `check_asr_layers.py` 只记录 `model.transcribe(..., return_time_stamps=True)` 的最终文本与词时间；组合流程内部音频分块、每块实际 ASR 文本、预测语言及对齐调用结果未进入报告。第三方 `qwen_asr` 逐块把音频、文本、语言交给 `forced_aligner.align()`，再加偏移并合并；`fix_timestamp()` 原始 timestamp token 的修正前后情况也未记录。
- **实验与输出**：保持 FR-44 三路径实验不变；`check_asr_layers.py` 现以临时包装追踪库内每批实际 align 输入及结果摘要，并通过 `_offset_align_result()` 的实际调用顺序关联分块偏移；报告音频哈希、采样率、样本数、时长，原始/规范化文本、语言、窗口内绝对边界、词条数及关键词摘要。实际经过 `fix_timestamp()` 的输入/输出记录数量及首尾摘要；不能取得的字段标记 unavailable。直接与组合路径仅在单个组合输入且全部快照可用时给出可比判定。退出上下文时恢复原方法，异常路径也恢复。
- **约束（不变式）**：仅修改独立诊断脚本及纯函数测试；不修改第三方库、生产 ASR/ForcedAligner 代码、模型行为、字幕与时间戳；不 import 项目流水线；不写 `output/`、断点、配置或字幕；GPU 实跑由用户在 `qw` 环境执行。禁止自动改词、搬移词、删除 `That's` 或根据长间隙修复字幕；方案 A 仍须用户另行明确确认。
- **执行/结果**：已修改 `check_asr_layers.py` 与 `test/test_asr_layers.py`。AST 语法检查通过；13 个纯函数测试直接执行通过；`node check_single_source.js` clean。当前默认 Python 缺少 pytest，故未能运行 pytest 命令；全量测试未运行。GPU 实测与最终诊断窗的输入一致性结论待用户在 `qw` 环境执行并回传报告。
- **验收状态**：本地代码与纯函数验收通过；GPU 报告能否完整关联真实分块及直接/组合输入一致性尚待实机验收。本条仅增加根因证据，不作生产修复。
- **重跑口径**：不运行流水线步骤，不清断点，不触碰 `output/`；GPU 实跑交用户执行。

---

## 7. 配置总表（`config.yaml`）

> 「设置页」= `server/settings_api.py SCHEMA` 白名单（30 键，v1.24 起含 `index_tts.*` 5 项）；其余只能手改文件。标 ★ 的是常被手改的关键项。「当前值」以仓库默认 / 新装默认为准；用户本机 `config.yaml`（被 gitignore）现为 `tts_method: index_tts`、`index_tts.refer_mode: 4`，见 FR-33/34。

| 段 / 键 | 当前值 | 设置页 | 作用 |
|---|---|---|---|
| `display_language` | `zh-CN` | ✔ | 界面语言 |
| `target_language` | `简体中文` | ✔ | 写入提示词的目标语言 |
| `max_workers` | `4` | ✔ | 步骤内部 LLM/字幕并发（**不是**批量并发） |
| `demucs` | `false` | ✔ | 转录前是否做人声分离 |
| ★ `burn_subtitles` | `true` | ✔ | 决定默认结束步骤（FR-05）；关时第 7/12 步只写 0 秒黑帧占位 |
| `ffmpeg_gpu` | `true` | ✔ | h264_nvenc 加速 |
| `summary_length` | `8000` | ✔ | 摘要字符上限（**顶层键**，不在 subtitle 段） |
| `max_split_length` | `50` | ✔ | 第 3-2 步 LLM 切句阈值（spaCy token；译前安全阀，可给大值） |
| `subtitle.max_length` | `75` | ✔ | 单行字幕最大字符；调大显著减少第 5 步 LLM 调用 |
| `subtitle.target_multiplier` | `1.2` | ✔ | 译文长度系数 |
| `subtitle.source` | `legacy` | ✔ | 第 12 步字幕源：`legacy` 单挂 `dub.srt`（现状）/ `unified` 补英文原文行成双语（FR-36）；默认 legacy，改一个值即可回退 |
| `min_subtitle_duration` | `2.5` | ✔ | 配音任务合并过短字幕 |
| `avoid_cross_turn_merge` | `false` | ✘ | 8.1 话轮保护：相邻短句出现问句 / 应答词时不合并，避免两人挤进一句；**开启会改变任务表行数 → 契约 fingerprint 与人工种子失效，需重跑 8.1→10**（FR-34 / v1.25） |
| `tolerance` | `1.5` | ✔ | 字幕时间容差 |
| `reflect_translate` | `false` | ✔ | 是否在直译后再做意译（双倍调用） |
| `translate.chunk_size` | `8000` | ✔ | 第 4-2 步每块字符上限（代码缺省 2400） |
| `translate.max_sentences` | `60` | ✔ | 每块句数上限（代码缺省 10） |
| `tts_method` | `azure_tts`（默认）；本机 `index_tts` | ✔ | TTS 后端，下拉含 `index_tts`（FR-33） |
| ★ `index_tts.refer_mode` | `1`（默认）；本机 `4` | ✔ | 音色来源：1 固定音色（跳过 Demucs）/ 2 原视频首句 / 3 逐句原声 / 4 按说话人契约；2/3/4 绝不静默回退预置音色 |
| ★ `index_tts.voice_map_speakers` | `'auto'` | ✘ | 说话人数量：`auto` 由 CAMPPlus 声纹聚类估计；填 1–6 强制人数（绕过降级判据，填 1 等价全片统一音色） |
| `index_tts.model_dir` / `cfg_path` | `D:/code/index-tts/checkpoints` / `''` | ✔ / ✘ | 权重目录必须绝对路径；`cfg_path` 留空用 `model_dir/config.yaml` |
| `index_tts.lang` | `'auto'` | ✔ | 跟随 `target_language` 映射 ZH / EN / JA / ES / AR / YUE |
| `index_tts.use_bf16` / `device` | `true` / `''` | ✔ / ✘ | BF16 加速（RTX 3060 支持）；`device` 留空自动，可填 `cuda:0` / `cpu` |
| `index_tts.spk_audio_prompt` / `use_emo_text` | `D:/code/index-tts/examples/voice_01.wav` / `false` | ✔ / ✘ | 前者**仅 mode 1 使用**；后者开 QwenEmotion 情感引导但更占显存 |
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
| ★ `encode.cq_level` | `32` | ✘ | 第 7/12 步的 NVENC 恒定质量级别（越小越清晰/越大体积），实测 30s 样片 CQ32 = +6.4 dB / 1.8× 体积 |
| ★ `encode.cut_cq_level` | `30` | ✘ | 「按此裁剪」专用：它是第二次有损转码，单独放宽 2 档抵消损失 |
| ★ `encode.cq_preset` | `medium` | ✘ | CQ 模式速度预设（NVENC 合法值），默认开 AQ / lookahead / multipass |
| ★ `encode.cq_gop` | `0` | ✘ | CQ 模式显式 GOP 帧数；0 = 不传，交给编码器。实测短 GOP 仅 +0.85 dB 却涨 10~32% 体积，默认不开 |
| ★ `encode.match_source` | `true` | ✘ | `bitrate` 模式下是否对齐源片码率、帧率、音频码率（FR-29）；`false` = 完全旧行为。CQ 模式下也用它决定是否探测源片参数 |
| ★ `encode.bitrate_scale` | `1.0` | ✘ | **仅 `bitrate` 模式**：目标视频码率 = 原视频码率 × 此值（0.2–10）。原片 AV1 输出 H264 时 1.0 体积一致但画质略低，1.5~2.0 换画质 |
| ★ `encode.fps_mode` | `cfr` | ✘ | `cfr` 显式 `-r` 固定帧率 / `passthrough` 原样传时间戳（VFR 源） |
| ★ `encode.copy_audio` | `true` | ✘ | 音频直通零重编码；合成配音（amix）自动忽略并改 aac |
| ★ `encode.min_video_bitrate` | `200` | ✘ | 视频码率下限 kbps，防止极端低码率源压烂画面 |
| `whisper.model` / `language` / `runtime` | `large-v3` / `en` / `local` | ✔ | ASR 模型/语言/运行时；`runtime` 可选 `local` / `cloud` / `elevenlabs` / **`qwen3_asr`**（FR-35）；设置页选项缺 `large-v3-turbo`（已知差异） |
| `whisper.detected_language` | `en` | ✘ | 运行时回写 |
| `whisper.hint_mode` | `off` | ✘ | 主ASR是否接受领域提示：off（默认）/hotwords/domain；领域信息仍给纠错，叙述提示会诱发跳词，需谨慎启用 |
| `whisper.beam_size` / `no_repeat_ngram_size` | `5` / `0` | ✘ | 与原版一致；真实口语重复不能靠禁止3-gram裁掉；ASR服务统一构造options，版本和参数进入断点指纹 |
| ★ `whisper.vad_gap_fill` | `true` | ✘ | VAD 漏检补转开关 |
| `whisper.gap_min_seconds` | `3.0` | ✘ | 多大空档才补 |
| `whisper.gap_min_speech_ratio` | `0.5` | ✘ | 主要有声阈值；v1.11首轮空档内累计有声≥1s也复核，避免3.9s/47%这类真实短句被跳过；有声仍不能等同语音 |
| `whisper.gap_fill_noise` / `gap_fill_min_silence` | `-35dB` / `0.25` | ✘ | silencedetect 参数 |
| `whisperX_302_api_key` / `elevenlabs_api_key` | 占位符 | ✘ | 云端 ASR 凭证 |
| ★ `qwen3_asr.python_exe` | `''` | ✔ | 独立 conda 环境的 `python.exe` 绝对路径；**空 = 未配置**，运行时报带安装指引的错（FR-35） |
| ★ `qwen3_asr.model` / `aligner` | `Qwen/Qwen3-ASR-1.7B` / `Qwen/Qwen3-ForcedAligner-0.6B` | ✔ | ASR 模型（可切 `-0.6B`）与强制对齐模型；后者是词级时间戳唯一来源 |
| ★ `qwen3_asr.language` | `auto` | ✔ | `auto` → `language=None` 自动检测；其余映射到官方语言名；ForcedAligner 仅 11 语种，越界明确报错 |
| ★ `qwen3_asr.max_window_seconds` / `overlap_seconds` | `270` / `1.5` | ✔ | FA 单段上限 5 分钟，留 30s 余量；窗口重叠后按 start 去重，避免切词 |
| ★ `qwen3_asr.max_inference_batch_size` / `max_new_tokens` | `8` / `1024` | ✔ | RTX 3060 12GB 显存克制；改小可避 OOM |
| ★ `qwen3_asr.device` / `dtype` | `cuda:0` / `bfloat16` | ✔ | bf16 + CUDA；进程退出即释放显存，不与 IndexTTS 常驻权重同时占用 |
| ★ `youtube.cookies_path` | `D:\code\vl\cookies.txt` | ✘ | 必须含登录态字段 |
| ★ `youtube.cookies_from_browser` | `''` | ✘ | 保持空（Edge app-bound 加密无解） |
| `youtube.js_runtime` | `''` | ✘ | 空 = `ejs:github` |
| `ytb_resolution` | `1080` | ✘ | 下载默认分辨率 |
| `llm.retries_per_model` | `2` | ✘（模型页管） | 每个模型重试次数 0–10 |
| `llm.endpoints` / `models` / `chains` / `tasks` | 见文件 | ✘（模型页管） | 三表 + 绑定 |
| `terminology.active` | `[]`（当前） | ✘（术语页管） | 勾选的基础术语库名称，按序合并成底板；**`[]` = 不启用任何基础库**，仅键缺失/读取异常才回退 `default`；勾选在 4.1 冻结进任务术语表 |
| `pause_before_translate` | `false` | ✘ | 抽完术语后是否暂停等人工改 |
| `min_trim_duration` | `3.5` | ✘ | 音频裁剪阈值 |
| 各云端 TTS 段（`sf_fish_tts` / `openai_tts` / `azure_tts` / `fish_tts` / `sf_cosyvoice2` / `edge_tts` / `gpt_sovits` / `f5tts`） | 见文件 | ✘ | 各后端 key / voice / character / refer_mode（`gpt_sovits.refer_mode` 是另一套，勿与 `index_tts.refer_mode` 混淆） |
| `index_tts` 段 | 见上 | 部分 ✔ | 本地 IndexTTS-2.5 插件；前提与依赖版本见 FR-33 |
| `dub.speed_workers` | `4` | ✘ | 第 10 步 ffmpeg `atempo` 变速并行度（v1.28）；缺配/非法回退 4，`1` = 全串行，不加速但时间轴顺序不变 |
| `model_dir` | `./_model_cache` | ✘ | 模型缓存 |
| `allowed_video_formats` / `allowed_audio_formats` | 见文件 | ✘ | 批量扫描扩展名 |
| `spacy_model_map` | en/ru/fr/ja/es/de/it/zh → md 模型 | ✘ | spaCy 模型映射 |
| `language_split_with_space` / `without_space` | 见文件 | ✘ | 决定词间连接符 |

> **已知差异**：`translate_service.DEFAULTS` = 2400/10，config.yaml = 8000/60；「切分实验场」硬编码 2400/10 且参数不落盘；设置页 `whisper.model` 选项缺 `large-v3-turbo`。三者都不是 bug，但改需求时要一起看。

---

## 8. API 总表

| 组 | 方法·路径 | 作用 | 主要错误 |
|---|---|---|---|
| 流水线 | `GET /api/pipeline` | snapshot + `steps[]`（每步附 `last_seconds` / `last_status` / `run_count`）+ `defaults` | — |
| | `GET /api/pipeline/timings` | 步骤耗时汇总（按 id 聚 `last/best/count/total`）+ Markdown `report` + 第 10 步内部拆解 `dub`（v1.28） | — |
| | `POST /api/pipeline/run` | `step_id` 或 `from_step`+`to_step`（闭区间），`force` | 404 未知步 / 400 倒挂 / 409 忙 |
| | `POST /api/pipeline/{pause,resume,stop}` | 状态控制 | — |
| 输入 | `GET /api/input/current` | 当前输入媒体（含 `ambiguous`） | 异常降级 200 |
| | `GET /api/input/cookies` | cookie 体检 | 异常降级 `unknown` |
| | `POST /api/input/download` | `{url,resolution}` 异步下载 | 400 参数 / 409 已有输入或忙 |
| | `DELETE /api/input?remove_file=` | 清 manifest / 删媒体 | — |
| 归档 | `POST /api/history/save` | `{name?}` 异步归档 | 409 忙 |
| 日志 | `GET /api/logs/tail?path=&lines=` | 读 `output/` 日志末尾 | 403 越界 |
| WS | `/ws` | 先补发历史 2000 条，再推事件 | 队列满丢事件 |
| 设置 | `GET/PUT /api/settings` | 30 白名单键读写（含 `index_tts.*` 5 项） | 400 未知/只读键 |
| | `POST /api/settings/test-llm` | `{profile}` 单轮连通性 | 404 profile |
| 模型 | `GET /api/models` | 三表（key 掩码）+ key 池状态 | — |
| | `PUT/DELETE /api/models/endpoint` | 凭证增删改 | 404 / 409 被引用 |
| | `POST /api/models/endpoint/reset-keys` / `reveal-key` | 清死 key / 显式看 key | 400 读文件失败 |
| | `PUT/DELETE /api/models/model` | 模型增删改 | 404 / 409 被链引用 |
| | `PUT /api/models/chains` | 一次写链 + 任务绑定 + 重试次数（删链时一并提交改绑后的全量配置） | 400 校验：空链（文案含加模型/删链指引）、引用不存在模型、悬空任务绑定、零条链、缺 `default` |
| | `POST /api/models/test` / `fetch-models` / `reveal-key` | 测试 / 拉模型列表 / 看 key | 404 / 400 / 网络异常以 `ok:false` 返回 |
| 翻译台 | `POST /api/translate/preview` | L1 切块 + L2 估算（免费） | 404 缺切句文件 / 400 空文本 |
| | `POST /api/translate/probe` | L3 实测选中块（不写产物） | 400 块序号越界 |
| | `GET/PUT/DELETE /api/translate/terms`（GET 另返回 `job` 回写预览 + `catalog{rows,stats,sources}`：并集总表 + 每份来源文件的完整清单 + `history[]` 历史归档索引），`POST /terms/writeback`，`POST /terms/history/preview`（只读去重预览），`POST /terms/history/import`（写入基础库），`PUT /terms/active`，`GET /terms/{name}` | 术语库 | 409 删激活中 / 409 同名新建 / 400 非法名 / 400 历史名越界或空选择 |
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
| B站文案 | `GET /api/bili/context` | `transcript` 状态统计（来源/语言/全文或节选/字数/摘要，**不回全文**）+ 次要参考信息（标题/简介/讲者建议/来源）+ `existing`（当前 `output/bili_meta.md` 解析出的上次三件套，无文件或损坏则 `null`；纯只读零费用） | — |
| | `POST /api/bili/generate` | 生成三件套（一次 LLM 调用，以转录为一手素材，写 `output/bili_meta.md`）；无可用转录直接拒绝 | 400 参数/校验/无转录 · 502 LLM 失败 |
| 广告位 | `GET /api/ad/context` | 转录统计 + 输入视频/音频 + 定点配置 + 已有 EDL + 可用裁剪源（`sources[]` 带 `subtitled`）+ `default_source`（优先带字幕的烧录版） | — |
| | `GET/HEAD /api/ad/preview?source=` | 视频预览（`PreviewFileResponse` 支持单段 Range / 尾部 Range / 416 / If-Range，读句柄同步关闭并登记）；`source` 只接受固定 id 白名单 | 404 未知源 / 产物不存在 · 416 越界 |
| | `POST /api/ad/preview/release` | 主动关闭所有预览读句柄，返回 `released` 数量；归档 / force 重跑前调用（后端 job 内也会兜底关一次） | — |
| | `GET /api/ad/media-info?source=` | ffprobe 一次读全：fps / duration / 分辨率 / 音视频 codec 与码率（逐帧步进 + 手动切到片尾） | 404 未知源 / 产物不存在 |
| | `GET /api/ad/frame-rate?source=` | 上一条的**旧路径别名**（同一函数），保留兼容 | 404 未知源 / 产物不存在 |
| | `POST /api/ad/detect` | 识别广告位 + 画面切点定点，写 `ad_slots.md` / `ad_segments.json` | 400 无转录/校验失败 · 502 LLM/ffmpeg 失败 |
| | `GET/PUT /api/ad/edl` | 读 / 手工改 EDL 区间、勾选与删除（`segments=[]` 合法清空；`dismissed=[]` 恢复已删除的识别段，MD 同步重渲染） | 404 无 EDL · 400 区间非法 |
| | `POST /api/ad/cut` | 按当前区间取补集裁剪拼接 → `output_no_ads.mp4`（原片不动） | 400 无 EDL/源不可用 · 502 ffmpeg 失败 |
| 配音分配 | `GET /api/voice/map` | 契约 + 逐句表（行数 = `tts_tasks.xlsx` 行数，含 `start_time` / `end_time`）+ 说话人 / 计数 + `status`（`ok` / `stale` + `reasons[]`）；stale 仍可编辑但禁止合成 | 契约不可读降级 |
| | `GET /api/voice/audio?path=` | 试听 wav / mp3（原声切片、代表音色、已合成配音）；只接受 `output/audio` 白名单 | 404 音频不存在或路径越界 / 400 非 wav·mp3 |
| | `PUT /api/voice/segments` | 人工改派 `{changes, marks, unassign}`，原子写契约并自动热更新消费层 | 400 领域错误（非法说话人 / 句子不存在） |
| | `POST /api/voice/rebuild` | 重建初稿 `{force, clear_overrides}`；后者为 true 才清人工分配 | 400 领域错误 / 409 运行态冲突 |
| | `POST /api/voice/propagate` | 按种子声纹相似度传播到全片，结果写为人工锁定；混合句不参与、计入 `stat.mixed` | 400 种子不足 / 混合句当种子 |
| | `PUT /api/voice/chunks` | 子行人工终审 `{changes: {number: {line_index: speaker}}}`，原子写并热更新 | 400 句子 / 子行 / 说话人非法 |
| | `POST /api/voice/chunks/rebuild` | 纯 CPU 逐子行抽 CAMPPlus 判人，人工子行保留；可选 `{numbers, force}` 只重建选中行，单行先尝试滑窗自动找说话人切换点并拆分 | 400 少于两位有效音色 / 缺声纹依赖 |
| | `POST /api/voice/chunks/split` | `{changes: {number: at_seconds \| null}}`；`null` = 自动找切换点。把单字幕行拆成两段，写 `tts_tasks.xlsx.lines` + `chunk_splits` 并判音色，返回 `split / skipped / at / hint` | 400 该行已有多条字幕 / 太短 / 文本切不开 / 缺声纹依赖 |
| | `POST /api/voice/revoice` | 删选中句已落盘的 `tmp/{n}_*_temp.wav` / `segs/{n}_*.wav`，返回重跑第 10 步提示 | 400 空选择 |
| | `POST /api/voice/reload` | 清 `index_tts._VOICE_MAP_CACHE`（手动改契约后热更新） | 400 reload 失败 |
| | `POST /api/voice/speakers`，`PUT /api/voice/speakers/ref`，`DELETE /api/voice/speakers/{name}` | 新增说话人 / 换代表音色（用指定句原声切片）/ 删除说话人 | 400 重名·无此人·最后一人·仍被引用 |
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
│   │             voice_map.json（音色契约，FR-34）/ voice_refs/（每人代表音色）/
│   │             voice_exp/（A/B 试听，不写正式产物）
│   ├── log/      cleaned_chunks.json(+xlsx) / split_by_*.txt / summary.txt /
│   │             terminology.json / translation_results*.xlsx / translate_ckpt.json /
│   │             asr_correction_report.json / asr_correct_ckpt.json / asr_checkpoint/ /
│   │             pipeline_latest.log + pipeline_<时间戳>.log（最多 10 份）
│   ├── gpt_log/  <log_title>.json（prompt 缓存）/ error.json / calls.jsonl
│   └── key_state.json       死 key 永久标记
├── history/<视频名>/        归档（移动产物，结构同 output/；同名覆盖）
├── <root>_log/              批量镜像工作区（每视频 job_state.json + output/）
├── docs/batch-phase1.md     旧交付说明（批量期1）
└── test/                    单测（33 个 test_*.py + conftest.py + README.md；`test_voice_chunks.py` 覆盖 FR-34 话轮/子行链路、`test_dub_timeline.py` 覆盖配音时间轴单元格读写，其余配音链路仍无自动化用例，见 FR-26）
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
| 本地配音（FR-33/34） | IndexTTS-2.5 权重 3.3 GB 常驻显存 → GPU 推理单例 + 三锁 + **主线程串行**（CUDA 上下文不跨线程，多实例必 OOM），与 `webui.py` 互斥；**但 ffmpeg `atempo` 变速纯 CPU/IO，走 `ThreadPoolExecutor` 并行**（`dub.speed_workers`，默认 4，设 1 恢复串行，v1.28），时间轴仍按字幕顺序累积；音色聚类固定纯 CPU 避开显存竞争；聚类/人工审核零 LLM 费用；契约按 `fingerprint` + `producer` fail-fast，绝不静默复用上一版划分 |
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
25. **本地配音别退回线程池 / 多实例**：`index_tts` 3.3 GB 权重常驻显存，`_10_gen_audio` 必须完全主线程串行（连 `ThreadPoolExecutor` 都不进），warmup 加载一次复用；模型已常驻时不要再触发 Demucs。与 index-tts 自带 `webui.py` 同时运行必 OOM。别把 `index_tts` 顶层 import 进 `core/__init__.py` 或未选该后端时的启动路径。
26. **音色 2/3/4 绝不静默回退固定音色**：旧 `_resolve_reference()` 在 mode 2 缺 `refers/1.wav` 时偷偷用了 `voice_01.wav`，用户以为用了原声其实是预置音色。缺参考音频一律报错并提示先跑第 9 步；`_fixed_voice_only()` 的短路**只在 `refer_mode=1`** 为真，别把它扩到其它模式（否则切回原声时半完成的空 `refers/` 会被当完成）。第 9 步无条件执行，与 `tts_method` 无关。
27. **前端是源码 → 构建 → dist 两步**：改 `.py` / `config.yaml` → 重启 server；改 `.tsx` / `.ts` → `frontend/` 下 `npm run build`（不必重启后端）；新增/改 API 契约 → 两边都要。设置页选项 / 产物页列表是 SCHEMA 数据驱动的通用表单，改 `settings_api.py` 免构建；新路由 `#/voices`、菜单项、新 API 客户端写死在 tsx 里，漏构建就表现为「页面和入口都不出现」，刷新浏览器 / 重启后端都无用。`frontend/dist/` 被 gitignore，切分支 / pull 不会更新它。
28. **自动聚类只是初稿，终审在人**：v1 MFCC+ward 把同一人的连续叙事切成两种音色（试听裁决 + 相邻句切换率 21.2% 的硬证据），别把「检出 k 人」当成识别正确；单人素材 v2 判 `degraded=True / k=1` 是正确降级，不要调阈值逼出 2 人。`producer` / `fingerprint` 变更后旧契约必须 fail-fast 失效并重建；server 常驻的 `_VOICE_MAP_CACHE` 每次写契约都要 `reload_voice_map()`；**改契约不会改已落盘 wav**，单句重配必须 `clear_dubbed()` 删 tmp/segs 后重跑第 10 步。`number` 非连续，别用行号当句子编号。
29. **别为消 `pip check` 警告降级依赖**：`transformers==4.52.1` / `tokenizers==0.21.0` 是 index-tts 内部符号的硬要求，`huggingface-hub` 必须 <1.0；`opencv-python==4.11.0.86` 是 numpy 2.2.6 的 ABI 要求，装回 4.9 会炸 `import cv2` 与第 7 步。`indextts 2.0.0 requires opencv-python==4.9.0.80` 这条冲突是**预期且故意**的（运行时不 import cv2）；`VideoLingo.egg-info` 的旧元数据快照也不是真实冲突。mode 4 合成时长实测比 baseline 长 20–60%，整片要留意音画对齐。
30. **配音试听端点尚未接入预览流释放链路**：`GET /api/voice/audio` 用的是普通 `FileResponse`，不在 `server/preview_stream.py` 的句柄注册表里，`close_preview_streams()` / `POST /api/ad/preview/release` 管不到它。音频文件小、浏览器通常完整读完即释放，风险低于视频 Range 预览；但若「保存历史」时报 `output/audio/` 下 wav 被占用，别再回头加归档重试，应把该端点换成 `PreviewFileResponse` 或给 `/api/voice` 补 release 钩子（v1.20 的教训）。另：`check_single_source.js` 目前没有为「音色来源 / 契约写入」加唯一归属守卫，后续补守卫时只需盯 `_resolve_reference()` 与 `voice_map.py` 的写契约函数，不要在 API 层再造第二份决策。
31. **双人混句别靠整句声纹硬判，也别默认改 8.1 切句**：`tts_tasks.xlsx` 的 `lines` 长度 > 1 时该句是混合声纹，种子传播必须跳过、更不许拿它当说话人原型；真正的修法是按 `trans.srt` 顺序游标拿每条原始字幕时间、逐子行判人（第 10/11 步本就逐子行产 wav 与占时间轴，只差把 `line_index` 透传到 `_resolve_reference()`）。**不要**为了消掉混句就默认打开 `avoid_cross_turn_merge`：那会换掉任务表行数 → `fingerprint` 失效 → 用户已标的种子 / overrides / 契约全废，而且已落盘的 `{n}_{i}.wav` 会被 `tts_main` 的 `save_as` 存在即跳过逻辑复用成旧音色，必须先 `clear_dubbed()` + 重跑第 10 步。子行判定属纯 CPU 但依赖 `soundfile` / `torch` / `torchaudio`，缺依赖要在 `build_chunk_speakers()` 里转成 400 提示，别让页面按钮弹 500。
32. **拆单字幕行时，srt 游标只能按原始块数走**：v1.26 允许把 `lines` 长度 = 1 的行拆成两条（`chunk_splits`），但 `subline_windows()` 消费 `trans.srt` 块的个数必须取 `n_src`（原始 1 块），**绝不能改成 `len(lines)`** —— 否则拆分行之后的每一行都会少吃一块字幕、整体错位一位，且症状要等到试听才发现。同理，`chunk_splits` 里的 `lines` 与任务表不一致时必须忽略该拆分（用户重跑了第 8 步），不能偷偷沿用旧切点。拆分是**唯一**会改 `tts_tasks.xlsx.lines` 内容的操作，行数 / `fingerprint` / 字幕 / 翻译 / 断点都不变；改完必须清旧 wav + 重跑第 10 步。另外「第 5 步对齐字幕」与「第 6 步生成字幕」必须保持两个独立 Step：合并回去会让「只想重新生成 srt」被迫连 `align_subs` LLM 一起重跑（费用大头），也让 12 步序号与源码 `_2`…`_12` 脱钩、页面「第 N 步」再次对不上。
33. **配音任务表的列表单元格永远别用裸 `eval`，也别让 numpy 标量进 Excel**：`tts_tasks.xlsx` 的 `lines` / `new_sub_times` 单元格存的是 `str(list)`。numpy>=2 把标量 repr 从 `1.2` 改成 `np.float64(1.2)`；`gap` / `tolerance` 从 DataFrame 取出就是 `np.float64`，第 10 步 `cur_time += gap/speed_factor` 会把整条时间轴升级成 numpy 标量，落盘后第 11 步 `eval()` 直接 `NameError: name 'np' is not defined`（本片 83/1015 行中招）。唯一决策在 `core/services/dub_timeline_service.py`：写侧 `plain_time_pairs()`、读侧 `parse_excel_list()`（`ast.literal_eval` + 剥 `np.*(...)` 包装），第 10/11 步都只准调它，别各写一份；更别改成 `eval(s, {"np": numpy})`，那既引回重依赖又保留任意代码执行面。**修读取侧就能救回存量任务表**：只需重跑第 11 步，不要为了这个错让用户重跑第 10 步（会重跑 1448 段 ffmpeg `atempo` 变速并覆盖 `segs/`，纯浪费）。

---

## 12. 变更记录

| 版本 | 日期 | 涉及 FR | 改动摘要 | 验证 |
|---|---|---|---|---|
| v1.47 | 2026-11-17 | FR-45 | **需求/任务**：为 FR-44 诊断补充库内组合流程每次 ForcedAligner 调用的真实音频块边界、原始/规范化文本、语言及结果摘要；可用时记录原始 timestamp 与修正前后摘要；纯函数测试覆盖缺失字段与输入一致性判定。**执行/结果**：修改独立诊断脚本与测试，新增临时 align/offset/fix_timestamp 追踪，正常及异常时恢复原方法；无 GPU/pytest 环境，本地以 AST + 13 个直接测试函数验收。GPU 实测待用户执行。 | AST 解析通过；13 个纯函数测试通过；`node check_single_source.js` clean；pytest 命令因默认 Python 无 pytest 未执行，全量测试未运行；GPU 实测待验 |
| v1.0 | 2026-11-09 | 全部 | 首次生成 as-built 规格，覆盖 6 个页面、11 个步骤、两套批量、全部 API/配置/全局规则 | 代码全量只读盘点；基线 `335 passed, 1 skipped` |
| v1.1 | 2026-11-10 | FR-12、G-17（新增）、第 7 章 | 修 `/v1/models` 拉取报 `CERTIFICATE_VERIFY_FAILED`：urllib 出站改用系统 CA + certifi 合并 context（不关校验），新增可选 `network.ca_bundle`；错误提示按证书/HTTP 状态/网络故障分类，不再一律劝用户切代理 | `362 passed, 1 skipped`；真实请求 zero.cat 从证书失败变为 `HTTP 401`（假 key 预期）；`check_single_source.js` clean；tsc + vite build |
| v1.2 | 2026-11-10 | FR-27（新增）、FR-12、第 7/8/9 章 | 新增「B站投稿文案」：第 1 步下载保存 `video_meta.json`；公式提炼自 bili-video-meta skill 成内置模板 `bili_meta`（提示词页可编辑）；流水线页卡片一次 LLM 调用出 3 标题+简介+10 标签 → `output/bili_meta.md`；任务名 +1（`bili_meta`）；讲者名池 `bili.speaker_names` 可追加 | `377 passed, 1 skipped`；prompt 渲染冒烟（变量替换/全角｜collapse）✓；`check_single_source.js` clean；tsc + vite build |
| v1.3 | 2026-11-10 | FR-27、第 7/8/9/11 章、附录 A | 标题生成改以**转录结果**为决定性一手素材（用户确认首选 `trans.srt` 中文译文）：多来源优先级、未超预算全文直送 / 超预算按时间均匀采样（头尾加权）+ 全局摘要；原视频元数据降级为次要参考、缺失不再阻断；prompt 新增一手素材段与取材铁律 5 条，自定义模板缺 `{{transcript}}` 时强制兜底注入；无转录 400 且前端禁用；新增 `bili.transcript_chars` / `transcript_samples`；`/context` 回转录统计；卡片显示主素材状态并支持手动/任务结束刷新 | `393 passed, 1 skipped`；真实归档冒烟（6301 字全文直送、prompt 9588 字符、金句/铁律/摘要齐全、主次顺序正确）；`check_single_source.js` clean；tsc + vite build |
| v1.4（仅文档） | 2026-11-10 | FR-28（新增·待实现） | 新增「广告位提醒」需求条目：针对「转录起点已进广告」，记录当前片 4 段赞助的词级空隙实测（2.50~3.09s）与窗口内画面切点位置，拟定「LLM 句子序号 → 词级候选窗口 → ffmpeg 切点/静音定点 → SponsorBlock 交叉校验」的分层边界算法，并预留阶段二自动裁剪的 EDL/字幕平移/交叉淡化约束。**边界信号选型尚未拍板，本版不实现代码** | 只读实测：`cleaned_chunks.json` 词级空隙 4/4 命中；`ffmpeg scene>0.08` 4/4 窗口有切点；`silencedetect` 在边界检出 1.35s 静音；SponsorBlock API 本片 404（对照视频 `aircAruvnKk` 200 有数据） |
| v1.5 | 2026-11-10 | FR-28（实现）、第 7/8/9/11 章、附录 A | 用户最终选定 **A 画面切点融合**，并确认成片方案「用 `output_sub.mp4` 按 EDL 取补集再 concat」。新增：LLM 按**行号**标广告段（prompt 模板 `ad_detect`、任务链 `ad_detect`）、ffmpeg 场景切点定点（取窗口内最后一个切点 − pad）、`ad_slots.md` + `ad_segments.json`（可手工改、原子落盘）、视频预览（Range 支持）+「跳到/取当前」微调 + 裁剪源下拉、单条 `trim+concat` 帧级精确裁剪成 `output_no_ads.mp4`（30ms 音频淡化、原片不动）；`ad.*` 9 个配置项；不进 12 步注册表 | `419 passed, 1 skipped`（新增 `test_ad_slots.py` 26 例）；真实转录+ffmpeg 冒烟 4/4 命中切点、起点比转录提前 1.47~1.67s；20s 样片端到端裁剪 20s→15.015s；`check_single_source.js` clean；tsc + vite build ✓ |
| v1.6 | 2026-11-10 | FR-28（精细时间轴）、第 8/11/12 章、附录 A | 用户反馈「跳转切点时原生时间轴范围太大，一点跳好几秒，要剪映那种帧级切换」。新增 `FrameScrubber`：局部窗口 ±1/±3/±10s（1px≈10ms）、点「跳到」自动居中并聚焦、窗口越界跟随、逐帧/0.1s/1s 按钮 + 方向键/Shift/Alt/空格、帧网格吸附（默认开）、轴上画正文末词/画面切点/广告开口/裁剪区间（图例只列窗口内可见项）、时间码精确到 ms + 帧号；新增 `GET /api/ad/frame-rate` + `probe_frame_rate()`（ffprobe 实测，本片 23.976fps），页面区分实测/手选/默认；修三个精度坑（连按漂移、播放中取当前 ≤50ms 误差、元数据未加载完 seek 丢失） | `428 passed, 1 skipped`（新增 9 例：帧率分数解析/回退/坏输出/接口白名单）；真实 `output_sub.mp4` 实测 23.976fps；`check_single_source.js` clean；tsc + vite build ✓；无头浏览器截图确认布局无重叠/截断 |
| v1.7 | 2026-11-10 | FR-29 / FR-30（新增并实现）、FR-12、第 7/8/9/11/12 章、附录 A | 用户反馈「压制字幕后视频大很多，码率/总比特率/帧率要跟原片一致」+「想自己加切点切片头片尾/中间」。① 新增 `core/utils/media_probe.py`（一次 ffprobe 读全）+ `core/services/encode_service.py`（编码参数唯一决策点），第 7/12 步改为调用服务层：视频码率 `-b:v/-maxrate/-bufsize` 按原片、显式 `-r`、原 `pix_fmt`、音频 `-c:a copy` 直通（配音混音时 aac 按原码率），`encode.*` 5 个手改配置；`probe_frame_rate` 改为薄转发消除第二份帧率解析。② 自定义切点复用 EDL：无 EDL 自动建骨架、`detect_ads` 保留手动段并重编号、`render_md` 分开渲染、页面新增「+ 添加切点 / 切掉片头到当前 / 从当前切到片尾」+ 删除 + 表格条件渲染；播放器和精细时间轴不再依赖「已有区间」才渲染；`GET /api/ad/media-info` 返回 fps + 时长（`/frame-rate` 保留别名） | `447 passed, 1 skipped`（新增 `test_encode_service.py` 20 例 + 手动切点 5 例）；**真实双字幕 20s 端到端**：视频 394152 bps（目标 408326）、总 530335 bps（原 521646，+1.7%）、帧率/分辨率一致、抽帧确认中英字幕均烧上，外推全片 ≈183 MB（旧版 748 MB）；无 EDL 三段手动切点补集 `[(10,100),(120,280)]`；`check_single_source.js` clean；tsc + vite build ✓；无头浏览器截图确认按钮行布局 |

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

| v1.23 | 2026-11-13 | FR-13、1.5 术语表、接口表、附录A、守卫 | 用户要求「术语表能从历史记录的那些文件夹里导入」。只读核对：26 个 `history/<视频名>/log/terminology.json`（每次归档留下的完整任务术语表）共 499 条、去重 346 条，最高同一 `src` 出现 11 期，此前完全选不到。用户拍板三点：导入目标 = `terms/` 基础库（不动当前任务表，避免作废 4.2 断点 / GPT 缓存）、多选批量合并、目标库已有 `src` 默认跳过保留人工译文（另给覆盖开关）。① `translate_service` 新增 `history_term_sources()`（只读元数据索引，损坏文件标 `available=false`）、`_resolve_hist_file()`（唯一路径解析：一级目录白名单 + 无分隔符 / 无 `..` + realpath 不越界）、`history_import_preview()`（按选择顺序复用 `merge_terms` 去重，每行给 `origins/conflict/duplicated`）、`import_history_terms()`（服务端重读值、浏览器只传 key）；抽出 `_merge_into_base_set()` 作为**任务表回写与历史导入共用**的唯一读改写决策，`write_back_terms` 返回契约不变。只写 `terms/*.json`，零 LLM 费用。② `GET /terms` 扩展 `history[]`；新增 `POST /terms/history/preview`（只读）与 `/terms/history/import` —— 必须 POST，`GET /terms/{name}` 会吞掉 GET 子路径。③ `#/terms` 加「从历史导入」Modal：多选历史任务（禁损坏项）→ 实时去重预览（统计 / 搜索 / 逐行勾选 / 展开逐期对照 / 标「已存在·跳过」/ 只选未收录）→ 选目标库（已有或新建）+ 覆盖开关；导入到已有库是追加合并、不整库覆盖、不自动激活。④ 守卫新增 `history-term-import-decisions` / `history-glossary-path` | `640 passed, 1 skipped`（新增 `test/test_history_terms_import.py` 33 例：索引只读 / 损坏文件 / 10 组路径穿越参数 / 去重后者覆盖 + 冲突 / 选择顺序 / 预览零写盘 / 新建库 / 人工译文保留 / 覆盖 / 服务端重读 / 只落 `terms/` / 坏输入 / 坏目标拒写 / 回写不回归 / 4 个接口契约）；`check_single_source.js` clean（含 2 条新规则）；`_lex.js` 3 个改动 py 通过；`tsc --noEmit` + `vite build` 通过、dist 已重建；真实工作区实跑得 26 期 / 499 条 → 去重 346 条、冲突 21 条；未跑真实浏览器交互与 GPU 任务 |

| v1.24 | 2026-11-13 | FR-33/34（新增）、FR-11/21/23/26、1.4/1.5、第 7/8/9/10/11 章、附录 A/B | **用户自行完成了配音相关功能，本次只做「只读核对 + 录入 PRD」，不改任何代码**（改动集中在 `feat/voice-map` 实验分支，尚未合并 `main`；`config.yaml` 被 gitignore）。① 环境层：在 `(vl)` conda 集成 `D:/code/index-tts`（IndexTTS-2.5），修正 `requirements.txt` / `setup.py`，锁定 `transformers 4.52.1`+`tokenizers 0.21.0`、`opencv 4.11.0.86`、`openai>=2.45,<3`，移除会误拉 TF 的 keras；`pip install -e ... --no-deps`。② `tts_method=index_tts` 后端：单例 + 三锁 + 懒加载、第 10 步全主线程串行、`refer_mode` 1/2/3 三档音色；修 74 min 音频 `LAME encoding failed`（ffmpeg 流式分块 + lameenc 兜底）、固定音色模式短路跳过 Demucs、原声模式**绝不静默回退预置音色**、`_refers_complete` 逐句校验。③ 多说话人三层架构：可插拔 producer → `voice_map.json` 契约（fingerprint + producer + overrides + segment_meta）→ 消费层 `refer_mode=4` 只读；v1 MFCC+ward 经试听裁决判为误分后安全降级 k=1，v2 换 index-tts 自带 CAMPPlus 192 维声纹（零新增依赖），三道降级判据 + 话轮平滑、纯 CPU、真实单人素材判 `degraded=True`。④ 人工终审工作台：新增 `#/voices`（9 端点 `server/voice_api.py`、`voiceApi.ts`、`VoiceMapPage.tsx`、菜单与产物页入口），逐句改派 / 标记 / 批量 / 试听 / 重配单句、保存热更新（`reload_voice_map`）、stale fail-fast、视频时间轴双向联动（复用 `/api/ad/preview` 后端零改动）；用户两次前端构建踩坑后明确「源码→build→dist」两步约定。⑤ 本次按「PRD 单一来源」补齐 FR-33/34、页面地图 6→7、术语表 4 词、设置页 25→30 键、配置表 `index_tts.*`、API 表配音分配组、目录树 `voice_map/voice_refs/voice_exp`、非功能与 5 条不要回退清单、附录 A/B、FR-23 第 8/9 步与 FR-26 未覆盖说明 | **640 passed, 1 skipped**（基线未变，v1.24 未新增自动化用例）；`node _lex.js` 9 个配音相关 py 全 OK；`check_single_source.js` clean（配音未造第二份单一来源决策）；`tsc --noEmit` EXIT=0；`GET /api/voice/map` 冒烟 200、1015 行 / `status=ok`；只读契约核对 `producer=campplus-cosine-v2`、`fingerprint=b025d32ac180d2c9`、`task_rows=mapped_segments=embedded_segments=1015`、`segment_meta=1015`、`overrides=0`、`degraded=True`、`diagnostics.silhouette=0.188 / center_cos=0.836`（v2 三道判据降级）；当前 `n_speakers=2` 但 `SPEAKER_01` 0 句（页面新增说话人后的状态，1015 句全归 `SPEAKER_00`）。**未跑**真实 GPU 合成、`--build --force` 重建、`--samples` A/B 试听、前端 `npm run build` 与浏览器交互——按分工全部由用户在 `(vl)` 终端执行；配音链路目前只有静态 / 接口 / 内存验证，无自动化单测 |

| v1.25 | 2026-11-13 | FR-34、FR-23、1.5、第 7/9/11 章、附录 A/B | 用户反馈「模糊的好多都是句子没切好、双人的都在一起」，并拍板 A+B+C 一起做。只读实测：`tts_tasks.xlsx` 1015 行里 **363 行（35.8%）** 的 `lines` 含 2~5 条原始字幕，16 条 `low_margin` 中 **11 条（68.8%）** 是这类；#645 / #974 / #1208 明显是问答交替。根因是 8.1 合并过短字幕 + 8.2 把多条 `trans.srt` 原文装进一行；而第 10/11 步本就逐子行产 wav、各占时间轴，只缺子行级音色决策。① **B**：新增 `core/services/subtitle_turn_service.looks_like_cross_turn()`（跨入口决策唯一归属、只认问句/应答词强信号）+ `config.yaml: avoid_cross_turn_merge`（**默认 false**：开启会换任务表行数使契约/种子失效，需重跑 8.1→10）；8.1 命中话轮交替时不合并也不延长。② **C**：`voice_map.mixed_numbers()` / `mark_mixed()` 幂等打 `mixed`（旧契约 GET 只内存补标、不写盘）；`propagate_from_seeds()` 拒绝混合句当种子、传播时跳过混句并计入 `stat.mixed`；`isAmbiguous()` 把 `mixed` 与 `propagated` 一并排除，避免 363 条淹没模糊列表。③ **A**：`subline_windows()` 复刻 8.2 顺序游标从 `output/trans.srt` 取子行真实时间（本片 1015 行精确消费 1448 块、100% 命中 srt，缺 srt 才等分兜底）；`build_chunk_speakers()` 纯 CPU 抽 CAMPPlus 逐子行判人写 `chunk_speakers` / `chunk_meta`（人工子行保留，缺依赖转 400 不弹 500）；`apply_chunk_overrides()` 为子行人工终审唯一写入口；`line_index` 沿 `_10_gen_audio → tts_main → index_tts_for_videolingo → _resolve_reference(chunk_index=...)` 透传，mode 4 优先子行音色、回退整行 `segments`。④ 新增 `PUT /api/voice/chunks` / `POST /api/voice/chunks/rebuild`（voice 端点 **10→12**；v1.24 记「9 端点」是漏了 `POST /api/voice/propagate`，本次一并补录进 API 表）、`GET /map` 增 `n_mixed` / `chunks` / `chunk_built_at` 与每行 `n_chunks` / `mixed`；`#/voices` 增「只看双人混句」筛选、混句行展开逐子行改人、「重建子行判定」「保存子行 (n)」「重配全部混句」。**字幕 / 翻译 / 断点 / 任务表行数一律不动，零 GPT 费用** | `647 passed, 1 skipped`（新增 `test/test_voice_chunks.py` 7 例：话轮信号 / srt 时间窗 / 等分兜底 / mixed 幂等 / 子行回退与写入校验 / 子行音色优先）；`check_single_source.js` clean；`tsc --noEmit` + `npm run build` 通过、dist 已重建；真实工作区只读实跑 `subline_windows()` 得 1015 行 / 1448 块全命中 / 363 条混句。**未跑**真实声纹推理（本会话测试环境缺 `soundfile`，页面按钮由用户 `(vl)` 环境的 server 进程执行）、真实 GPU 合成与浏览器交互，按分工交用户 |

| v1.26 | 2026-11-13 | FR-05/21/22/23/26/34、1.4/1.5、第 6/7/9/11 章、附录 A/B | 用户三条反馈：①「重建子行判定还是有一些句子没识别出来，没有 + 号，想点哪行就对哪行重建」；②「步骤上面都没有序号，不知道哪个是哪个」；③「将对齐好的句子生成字幕这个单独步骤没了，害我点错好几次对齐字幕」。① **任意行可终审**：`chunk_rows()` 改为返回全部行（单字幕行也给子行 0，可直接改音色），前端 `rowExpandable: () => true`；新增 `POST /api/voice/chunks/split` + `voice_map.split_rows()`，用 0.45s/0.10s 滑窗 CAMPPlus 找说话人翻转点（`_find_switch_point()`，找不到就让用户手填切点秒数），`_split_text_at()` 按时间比例在最近标点断句，写回 `tts_tasks.xlsx.lines` + 契约 `chunk_splits`，再判两段音色；**srt 游标按 `n_src` 原始块数推进**、`_active_splits()` 校验 lines 一致才生效，防止后续行整体错位；`POST /chunks/rebuild` 支持 `{numbers, force}` 只重建选中行（单行先自动尝试拆分，人工锁定行跳过），`apply_overrides()` 整行改人时统一覆盖该行所有 `chunk_speakers`。② **全局序号**：`core/pipeline.py` 加 `STEP_NO`，`pipeline_status()` 返回 `no`（音频模式不重排），前端下拉 / 列表 / 运行态 Tag / 范围按钮统一显示「`N. 标题`」。③ **第 6 步独立**：`align` 只跑 `_5_split_sub`（两张中间 xlsx 作 marker、`align_subs`），新增 `gen_sub`「生成字幕」只跑 `_6_gen_sub`（`src.srt` + `trans.srt` 作 marker、**零 LLM 任务**），`NO_BURN_END_STEP` 改 `gen_sub`；12 步序号恰与源码 `_2`…`_12` 对齐，第 10 步 = 「生成配音」，页面/文档/口头口径统一；同步更新后端提示文案与测试期望 | `653 passed, 1 skipped`（`test_voice_chunks.py` 7→13 例，新增 12 步序号 / 第 6 步零 LLM / 文本切分 / 拆分后游标不偏移 / 单行可展开 / 整行改人压过子行）；`node check_single_source.js` clean；`create_app().openapi()` 13 个 voice 端点、12 步 `no` 连续 1..12；`tsc --noEmit` + `npm run build` 通过、dist 已重建。**未跑**真实声纹滑窗推理与真实 GPU 合成（按分工交用户 `(vl)` 环境）；拆分会改任务表 `lines` 内容，改完必须清旧 wav + 重跑第 10 步 |

| v1.27 | 2026-11-13 | FR-23/26/34、第 7/9/11 章、附录 A/B | 用户实跑第 11 步「合并完整音频」报 `NameError: name 'np' is not defined`（`core/_11_merge_audio.py:22` 裸 `eval`）。只读排查坐实完整链条：环境 numpy **2.5.3**，`repr(np.float64(1.2))` 已是 `np.float64(1.2)`（1.x 是 `1.2`）；`gap` / `tolerance` 从 DataFrame 取出是 `np.float64`，第 10 步 `cur_time += gap/speed_factor` 把整条时间轴升级为 numpy 标量，`to_excel` 将 `new_sub_times` 存成 `[[np.float64(90.693), ...]]`；真实任务表 **83/1015 行 / 198 处** 中招，第 11 步 `eval()` 作用域没有 `np` 即崩。① 新增 `core/services/dub_timeline_service.py`（stdlib-only、跨入口唯一决策）：`parse_excel_list()` 优先 `ast.literal_eval`、失败时剥掉 `np.float*/int*/bool_(...)` 包装再解析（不引 numpy、不执行任意代码），`plain_time_pairs()` 写盘前把时间对转 builtin float。② 读取侧 `_11_merge_audio.py` 的 `load_and_flatten_data` / `get_audio_files` 全改走 `parse_excel_list`，消除裸 `eval`；`create_srt_subtitle()` 同链路自动修好。③ 写入侧 `_10_gen_audio.py`：`cur_time` / `gap` / `tolerance` / `ad_dur` / `speed_factor` / `chunk_end_time` 全强制 float，`new_sub_times` 落盘统一过 `plain_time_pairs`，杜绝再产出 `np.*` 字面量。**修读取侧即可救回存量任务表**：只需重跑第 11 步，不必重跑第 10 步重做 1448 段 ffmpeg 变速。 | `662 passed, 1 skipped`（新增 `test/test_dub_timeline.py` 9 例，含用 pydub stub 导入第 11 步的端到端复现）；`node _lex.js` 4 个改动 py 全 OK；`check_single_source.js` clean（新增 `dub-task-table-cell-codec` 唯一归属规则，共 15 条：`parse_excel_list` / `plain_time_pairs` 只准在 service 定义）；只读实跑真实 `tts_tasks.xlsx`：1015 行全解析、83 行 legacy `np.float64` 修复、1448 子行 ↔ 1448 时间窗对齐、值全为 builtin float。**未跑**真实音频拼接（第 11 步实跑按分工交用户 `(vl)` 环境）；本次只改 `.py` 与 PRD，无需重建前端 dist |

| v1.28 | 2026-11-14 | FR-23/26/34、第 8/10/11 章、附录 A/B | 用户反馈「生成配音这步用时太久，每步消耗时间要统计出来」。① **步骤耗时统计**：新增 `core/services/timing_service.py`（stdlib-only，跨入口唯一来源）：`record()` 在每步结束时原子写 `output/log/step_timings.json`（失败静默，不拖累主流程）；`backfill()` 在 FastAPI `startup` 把历史 `pipeline_20*.log` 解析回填（按 source+id+at+seconds 去重，幂等可重复跑；无「步骤结束」行的旧日志用下一边界时间戳推算，1s 精度）；`summary()` 按 id 聚合 `last/best/count/total`，`attach_last_seconds()` 回填 `/api/pipeline` 的 `steps[]`；`dub_breakdown()` / `latest_breakdown()` 解析第 10 步日志里的 gpt/s2mel/bigvgan 推理耗时做内部拆解；`format_report()` 输出 Markdown 表。② **API/前端**：新增 `GET /api/pipeline/timings`（汇总 + report + dub 拆解）；流水线页加「每步耗时」按钮打开统计 Modal，步骤列表每项显示上次实跑耗时与记录次数，第 10 步可展开内部拆解。③ **配音提速边界（不改 GPU 串行）**：第 10 步 ffmpeg `atempo` 变速是纯 CPU/IO，改为 `ThreadPoolExecutor` 并行（并发度 `dub.speed_workers`，缺配/非法回退 4，设 1 恢复全串行）；**时间轴仍按字幕顺序累积**（`cur_time` 顺序累加，不依赖变速完成先后）；IndexTTS 的 GPU 推理依旧主线程串行（3.3GB 单例 + `_INFER_LOCK`，CUDA 上下文不跨线程）。合并宽表时 `finally` 调 `_shutdown_speed_executor`（`shutdown(wait=True, cancel_futures=True)`）取消排队任务、等在跑 ffmpeg 写完再返回。 | `23 passed, 8 warnings`（v1.28 三个专项文件 `test_dub_timeline.py` / `test_timing_service.py` / `test_log_hooks.py` 合计量；其中含线程池关闭语义用例，从 `core/_10_gen_audio.py` AST 抽取真实 `_shutdown_speed_executor` 规避测试环境缺 `requests` 的 TTS 依赖链）；`node check_single_source.js` clean（`single-source guard clean`）；`py_compile` 5 个改动 py 全过；`check_vad_gaps.py` 未跑。**未跑**全量 `pytest test/`（依 PRD 0.4 由用户在 `(vl)` 环境执行，本会话 conda 受限）；真实 IndexTTS 合成 / 真实 ffmpeg 变速并行 / 浏览器交互按分工交用户 |
| v1.29 | 2026-11-14 | FR-31、第 8 章、`server/app.py` | 全量验证 + 消除 FastAPI 弃用警告。用户全量 `pytest test/ -q` 全过，但 `server/app.py` 的 `@app.on_event("startup")` 触发 `DeprecationWarning: on_event is deprecated, use lifespan event handlers instead`，要求迁移。改动仅 `server/app.py`：顶部加 `from contextlib import asynccontextmanager`；原 `_startup()` 逻辑（`BUS.bind_loop` / `register(publish_external)` / `install_disconnect_noise_filter` / 历史耗时 `timing_service.backfill()` 失败不阻断启动）迁入模块级 `lifespan(app)` 并以 `yield` 收尾；`create_app()` 内 `FastAPI(..., lifespan=lifespan)` 挂上、删除原 `on_event` 块。 | 全量 `pytest test/ -q` → **`665 passed, 1 skipped`**（基线 662 → 现 665，v1.28 新增 3 个计时/日志用例已在全量内），且 stderr 零 warning（`on_event` 弃用警告消失）；`py_compile server/app.py` 通过；`grep -rn on_event server/` 零命中；`pytest test/test_log_hooks.py -q` → `12 passed`（含「startup 注册 sink 使 HTTP 线程日志可达 BUS」「backfill 失败不阻断启动」两用例，证实 lifespan 迁移行为不变）。**提醒**：`app.py` 改动后若 `python -m server` 在跑需重启；前端 dist 无需重建 |
| v1.30 | 2026-11-14 | FR-04、第 8 章、`core/services/timing_service.py`、`frontend/src/api.ts`、`frontend/src/pages/PipelinePage.tsx` | **需求/任务**（用户明确，先于开发写入 FR-04）：步骤列表每一项在标题同一行显示状态 Badge（运行中/已完成/未运行）+ 开始时间 + 结束时间 + 本次耗时；此前只有 Modal/副标题，且「开始/结束」后端根本没记录（前端为空）。**执行/结果**：① `timing_service.record()` 新增 `started_at`/`ended_at`（ended=now，started=ended−seconds 精确推导，`task_manager` 零改动）；② `summary()`/`attach_last_seconds()` 回填 `steps[].last_started_at/last_ended_at`；③ `parse_log()` 从文件名 `pipeline_YYYY-MM-DD_*` 提取日期，结合开始 ts + 耗时补全历史 `started_at`/`ended_at`；④ 前端 `StepStatus` 增两字段，`PipelinePage` 把 `stepBadge` 从 `description` 移到标题同行 `Space` 末尾，其后显示「开始/结束/耗时」（墙上时间截 `HH:MM:SS`）。 | 全量 `pytest test/ -q` → **`667 passed, 1 skipped`**（v1.29 基线 665 → 现 667，新增 2 个 `timing_service` 用例：live `record` 写 start/end、`attach_last_seconds` 回填；原有 backfill 用例改带连字符日期名以覆盖历史日期补全）；前端 `tsc --noEmit` 通过、`npm run build` 成功、dist 已重建（前端改动后浏览器需 Ctrl+F5） |
| v1.31 | 2026-11-14 | FR-性能、第 11 章 `core/_11_merge_audio.py`、`test/test_merge_audio.py` | **需求/任务**（先于开发写入第 11 步 FR）：废除 pydub `+=` O(n²) 逐段字节拷贝（1448 段约 90GB 搬运，实测 5m21s），也禁止逐段 wav→mp3→解码重编码（双重有损）；改用 ffmpeg 一次装配，保留字幕对齐所需的段间静音空隙，ffmpeg 失败才回退旧 pydub 路径。**执行/结果（含一次线上修复）**：首版写成「单趟 `filter_complex`」——1448 个 `-i` + 巨型 filter；用户实跑 `force` 重跑第 11 步直接 `WinError 206 文件名或扩展名太长`（`FileNotFoundError`，Windows `CreateProcess` 命令行上限约 32767 字符）。改**分批**：`BATCH_SIZE=80`，每批 `filter_complex` 内 `[j:a]aresample=16000,aformat=mono,asetpts[clipJ]` + 共享 `anullsrc`（`-f lavfi -i anullsrc`，`atrim` 定长）内联静音、`[silJ][clipJ]…concat=n=…[out]` 编码该批 `batch_K.wav`；各批再经 `concat` demuxer（只传短 list 文件）串成 `dub.mp3`(16k/64k)。每批命令 ≪ 上限、终拼无长命令，彻底绕过 WinError 206；静音在片段**之前**（时间轴对齐正确）。`build_merge_filtergraph` 更名为 `build_batch_filtergraph` 并返回 `(graph, new_prev_end, has_gap)` 供跨批续接。 | 全量 `pytest test/ -q` → **`673 passed, 1 skipped`**（v1.30 基线 667 → 现 673，新增 6 个 `test_merge_audio.py` 用例：无 gap 不引 anullsrc、正 gap 在片段前插静音、零首/零间隙静音跳过、静音优先链序、跨批 `prev_end` 续接、批大小常量合理）；`py_compile` 通过；`grep -rn build_merge_filtergraph` 零命中（旧名已清）。**未跑**真实第 11 步终拼（依分工交用户 `(vl)` 环境 `force` 重跑验证）；本次只改 `.py`/测试/PRD，`timing_service` 等无改动，前端 dist 无需重建 |
| v1.32 | 2026-11-14 | FR-04、`server/task_manager.py`、`frontend/src/api.ts`、`frontend/src/pages/PipelinePage.tsx`、`test/test_pipeline_run.py` | **需求/任务**（用户明确，先于开发）：步骤**失败**时前端必须显示红色「失败」Badge + 红色「✕」头像 + 顶部红色错误横幅（含 `error` 文案可复制 + 落盘日志路径），**绝不能**退化为灰色时钟「未运行」让用户以为没跑；用户**中止**显示黄色「已停止」。**执行/结果**：根因——旧 `_run` 的 `finally` 清空 `current_step`，而 `stepBadge` 只有「运行中/已完成/未运行」三分支、无失败态，崩溃后 `state=error`+`current_step=None`+`step.done=False` 落到灰色时钟「未运行」，与「从未运行」视觉一致（用户原话「失败了，还显示这个进行中的图，错了」）。`TaskManager` 新增 `failed_step` / `stopped_step`，崩溃/中止时在 `except` 里记录具体步骤 id（不清空），`snapshot()` 随 `PipelineStatus` 下发 `failed_step`/`stopped_step`；`api.ts` 补两字段；`stepBadge` 增加失败（红 `error` Badge）/ 已停止（黄 `warning` Badge）分支且**优先于「已完成」**（残留旧 marker 也显红）；头像同步 `CloseCircleFilled` / `MinusCircleFilled`；顶部 `Alert type=error` 展示 `error` 与 `log_file`。新增 2 个后端单测验证 `failed_step`/`stopped_step` 记录。 | 全量 `pytest test/ -q` → **`675 passed, 1 skipped`**（v1.31 基线 673 → 现 675，新增 2 个 `test_pipeline_run.py`：`test_failed_job_records_failed_step_and_error_state` / `test_stopped_job_records_stopped_step`）；前端 `tsc --noEmit` 通过、`npm run build` 成功、dist 已重建。**未跑**真实崩步骤的浏览器交互（交用户 `(vl)` 环境：重启 `python -m server` + Ctrl+F5 验证失败红标 + 横幅） |
| v1.33 | 2026-11-14 | FR-性能、第 11 章 `core/_11_merge_audio.py`、`test/test_merge_audio_ffmpeg.py` | **修复 v1.31 分批装配的两个线上 bug**（用户实跑 `force` 重跑第 11 步再报错）：① 批处理 `filter_complex` 带命名输出 `[out]` 时**必须显式 `-map "[out]"`**，否则 ffmpeg 报 `Filter 'concat:out:a0' has output 0 (out) unconnected`（实证：不加 `-map` 该 gyan.dev 8.x ffmpeg 退出码 127，加后 EXIT=0）；② 终拼原用 `concat` demuxer 的 list 文件（`file '绝对路径'`），但该 ffmpeg 对带引号绝对路径直接 `Impossible to open`（相对路径才成功），list 文件路径/引号处理脆弱 → 改为与批处理一致的方式：把所有批 wav 当 `-i` 输入、用 `filter_complex` 一次性 `concat=n=批数:v=0:a=1[out]` + `-map "[out]"` 终拼（批数 ~19，命令 ≪ 32767 上限，彻底绕开 list 文件路径坑）；③ pydub 回退路径 `merge_audio_segments` 内 `AudioSegment` 漏了 import（lazy import 只加了 `process_audio_segment` 局部），导致回退时 `NameError: name 'AudioSegment' is not defined` → 在 `merge_audio_segments` 顶部补 `from pydub import AudioSegment`。 | 新增 `test/test_merge_audio_ffmpeg.py`（真实 ffmpeg 集成，无 ffmpeg 时 skip）：`test_merge_audio_segments_ffmpeg_real`（5 段带 gap 跑通全链路、产出 dub.mp3 且 >1KB）、`test_merge_audio_segments_ffmpeg_missing_first`（首段缺失安全返回 False 不崩）；全量 `pytest test/ -q` → **`677 passed, 1 skipped`**（v1.32 基线 675 → 现 677，+2 集成用例）；`py_compile` 通过。**未跑**真实 1448 段终拼耗时（交用户 `(vl)` 环境 `force` 重跑第 11 步，预期从 5m21s 降到几十秒级）；本次只改 `.py`/测试/PRD，未动 API/前端，server 无需重启即可跑、但重跑任务由用户触发 |
| v1.34 | 2026-11-15 | 第 7 章依赖、`requirements.txt`、`installer.py`、`test/test_installer_protobuf.py`、附录 B | **需求/任务**（用户实跑第 2 步 ASR 报错）：`whisperx.load_model` 抛 `ImportError: cannot import name 'builder' from 'google.protobuf.internal'`，要求修复并防复发。**根因（只读核查 `(vl)` 环境实测，非项目代码 bug）**：`pyannote.audio 4.0.7`（requirements 锁 `>=4.0.0`）→ `opentelemetry-exporter-otlp` → `opentelemetry-proto 1.43.0`（硬约束 `protobuf>=5.0,<8`，其 `common_pb2.py` 由 protoc 5.26.1 生成、首行 `from google.protobuf.internal import builder`，而 `builder` 自 protobuf 3.20 才引入）；但 `descript-audiotools==0.7.2`（requirements 第 119 行，demucs/index-tts 链路）硬钉 `protobuf<3.20,>=3.9.2`，pip 最终留 **3.19.6**。两者数学上不可调和；实测该包源码**完全不 import protobuf**（只是随 tensorboard 间接声明），故违反它的 pin 无运行时影响。**执行/结果**：① `requirements.txt` 在 `pyannote-audio` 行后加冲突注释块 + 手动修复命令；**故意不写 protobuf 实体行**（写了会与 `descript-audiotools` 直接 `ResolutionImpossible`）。② `installer.py`：新增 `PROTOBUF_VERSION="5.29.6"`（PyPI 核实有 cp310-win_amd64 wheel；**不用 5.29.5**，其含 CVE-2026-0994 DoS，5.29.6 为修复版且 `vulnerabilities: []`）、`protobuf_version_ok()`、`ensure_protobuf()`（版本 <5 时用 `pip install --no-deps protobuf==5.29.6` **单变量兜底**，绝不连带重装其它包）；`install_all()` 在 `install_base_requirements()` 之后调用（base 安装可能把 protobuf 降回 3.19.6）；`health_check()` 检出旧版并直接给出修复命令，打印列表加 protobuf。③ 新增 `test/test_installer_protobuf.py` 4 例（版本判定 None/3.x/4.x/5.x/6.x/非法、旧版触发 `--no-deps` 安装、新版 no-op、health_check 能检出坏版本）。**次要独立警告**：`torchcodec 0.7.0` 的 core4/5/6/7 dll 均在，但系统 FFmpeg 8.1.2 只提供 `avcodec-62`，torchcodec 只支持 FFmpeg 4–7 → `pyannote.io` 警告 built-in 解码不可用；ASR 音频由 whisperx 自身解码、VAD 可走 in-memory waveform，**不阻塞本次修复**，未在本次改动范围内。 | 全量 `pytest test/ -q` → **`681 passed, 1 skipped`**（v1.33 基线 677 → 现 681，+4 守卫用例），零 warning；`py_compile installer.py` 通过；`read_base_requirements()` 复核仍为 54 行、无 protobuf 实体行、注释未被误当作包。**待用户（依分工，conda 环境命令不代跑）**：`(vl)` 执行 `python -m pip install --no-deps "protobuf==5.29.6"`，再 `python -c "from google.protobuf.internal import builder; import pyannote.audio, whisperx; print('OK')"` 验证；`pip check` 会残留一条 `descript-audiotools` 与 protobuf 5.x 不兼容，属已知且唯一可接受的冲突，勿降回 3.19.6。本次只改依赖声明/安装器/测试/文档，未动 API/前端，server 无需重启、dist 无需重建 |
| v1.35 | 2026-11-15 | 0.2/0.3/0.4、第 12 章、`test/test_prd_process.py`、项目/用户级 MEMORY | **需求/任务**（用户明确，先于开发写入 0.2 流程条款）：把「按需求开发，不是开发完补需求」固化为**强制顺序门**——A 阶段先把需求/任务与验收写进 FR、并在第 12 章追加只含「需求/任务」的变更记录行（执行/结果与验证列留「待填」），B 阶段才允许改代码 / 测试 / 构建，C 阶段回填执行结果与验证；线上报错的紧急修复同样适用，**禁止先开发后补需求行**；需有可执行守卫防止流程被绕过，并把规则同步到项目级与用户级长期记忆。**执行/结果**：① `PRD.md` 0.2 重写为 **A/B/C 三阶段强制门**：A 需求先行（读 FR/G 规则/不要回退清单 + grep 核对现状 → 写 FR 当前行为 / 约束 / 验收 → 第 12 章先追加只含「需求/任务」的变更记录行、结果与验证列留「待填」→ 建任务清单；**门禁**：PRD 需求行 + 变更记录行未落盘前禁止编辑 `.py`/`.ts`/`.tsx`/`.json`/`config.yaml`、禁止新建测试文件，线上报错的紧急修复同样适用）；B 只实现 A 已写明的需求（跨入口决策归 `core/services`、按 0.4 验证、真实 GPU/conda 交用户）；C 回填执行结果 / 验收结论 / 去「待实现」/ 补日志。0.3 补「FR 必须在阶段 A 写入，禁止先写代码再补 FR」；0.4 验证清单新增「任何 PRD 驱动的改动」行指向守卫。② 第 12 章表尾注记改为两段式强制格式（v1.35 起每行含两段标记、最新行结果不得残留占位；v1.30–v1.34 历史行不追溯改写，v1.33 是线上返工）。③ 新增 `test/test_prd_process.py` 5 例**只读**守卫：三阶段门存在 / 注记禁止开发完补需求 / v1.35 起每条变更记录两段式 / 最新行「执行/结果」已回填（只看该标记之后，避免需求段引用「待填」误伤）/ 0.4 提到守卫；该守卫在阶段 B 单独跑实测 `1 failed, 4 passed`，**证明结果未回填就跑全量会被拦下**。④ 项目级 `.workbuddy/memory/MEMORY.md` 与用户级 `MEMORY.md` 同步为跨会话强制规则。⑤ 本次**未改任何业务代码 / API / 前端**，只动文档、测试与记忆。 | 全量 `pytest test/ -q` → **`686 passed, 1 skipped`**（v1.34 基线 681 → 现 686，+5 守卫用例），零 warning；守卫阶段 B 为 `1 failed, 4 passed`（预期拦截）、阶段 C 回填后 5 例全过；`py_compile test/test_prd_process.py` 通过。本次只改文档 / 测试 / 记忆，**server 无需重启、dist 无需重建** |
| v1.36 | 2026-11-16 | FR-35（新增）、FR-11、第 7/12 章、附录 A、`core/_2_asr.py`、`core/asr_backend/qwen3_asr*.py`、`server/settings_api.py`、`config.yaml`、`test/test_qwen3_asr.py` | **需求/任务**（用户，先于开发）：第 2 步「本地转录」增加 Qwen3-ASR 运行时。只读调研实测三条硬约束：`qwen-asr 0.0.6` 硬依赖 `transformers==4.57.6`，与 `(vl)` 的 Python 3.10.0 + `transformers 4.52.1`（IndexTTS 锁死）冲突 → **禁止装进主环境**；vLLM HTTP 服务只回纯文本、无词级时间戳 → **禁止采用**（下游字幕会崩）；`Qwen3-ForcedAligner-0.6B` 单段 ≤5 分钟、仅 11 语种，而项目分段 30 分钟 → runner 按 270s 窗口 + 1.5s 重叠去重。定为**独立 conda 环境 + 子进程 runner** 方案，主环境零依赖改动，词级时间戳回传并偏移成全片绝对时间；缺依赖/权重/语言越界一律明确报错、**绝不静默回退 whisperX**；`_audio_fingerprint` 加 runtime + 配置签名防止切换后端复用旧断点；按 RTX 3060 12GB 取 bf16 / batch 8 / 串行不并存。完整约束与验收见 **FR-35（待实现）**。**执行/结果**：① 新增 `core/asr_backend/qwen3_asr_runner.py`（独立环境内执行、不 import 项目模块）：ffmpeg `-ss/-t` 只解码窗口为 16k/mono float32 ndarray → 按主环境算好的 `windows` 逐窗 `Qwen3ASRModel.transcribe(audio=(ndarray,16000), language=..., return_time_stamps=True)` → `parse_stamps()` 容错解析 ForcedAligner 的 dict / 对象 / 嵌套 list → 重叠区按 `win_start + overlap/2` 去重 → stdout 只输出一个 JSON（日志全走 stderr），依赖/权重/时间戳/推理失败分别给带指引的退出码 2~6。② 新增 `core/asr_backend/qwen3_asr.py`（主环境适配层，stdlib-only）：`qwen3_config()` 校验 11 语种 / 窗口 ≤300s / overlap / batch、`qwen3_signature()` 供断点指纹、`build_windows()` **唯一窗口切分决策**、`normalize_runner_result()` 校验并偏移为全片绝对时间、`transcribe_audio_qwen3_asr()` 起子进程（spec 走临时 JSON 文件、`finally` 清理、只解析 stdout 最后一个 JSON 行）；缺 `python_exe` / 缺 runner / 非 0 退出 / 非 JSON 一律抛带安装指引的错，**无一处回退 whisperX**。③ `core/_2_asr.py`：`runtime == "qwen3_asr"` 分发到新后端、`asr_hints` 仍仅 `local`、未知 runtime 明确 `ValueError`；`_audio_fingerprint` 增 `runtime` 与 `qwen3_signature()`，切换后端自动作废旧断点。④ `config.yaml` 新增 `qwen3_asr` 10 键（默认 1.7B / 270s / overlap 1.5 / batch 8 / bf16 / cuda:0）+ 注释说明「禁止装进主环境」。⑤ `server/settings_api.py`：`whisper.runtime` 增 `qwen3_asr`、SCHEMA 增 10 键、GROUPS 转录 ASR 组 4→14；设置页是数据驱动表单 → 免重建 dist。⑥ 新增 `test/test_qwen3_asr.py` **19 例**：窗口切分（短段 / 900s 覆盖与 1.5s 重叠 / 非法参数）、配置校验（默认 / 非 11 语种 / >5 分钟 / 非法 overlap）、签名排除 python_exe、结果偏移与结构兼容 `process_transcription`、时间戳容错解析、缺配置 **不启动子进程**、runner 失败 / 非 JSON 明确报错、成功路径 spec 内容与语言映射、`_audio_fingerprint` 静态守卫，以及**用假 torch/qwen_asr 模块跑通 `runner.main()` 全链路**（2 窗口聚合、重叠词去重、stdout 只含 JSON）。⑦ `check_single_source.js` 新增 `qwen3-asr-runtime-dispatch` 规则，禁止出现第二份运行时分发决策。 | 全量 `pytest test/ -q` → **`705 passed, 1 skipped`**（v1.35 基线 686 → +19 新用例），零 warning；阶段 B 单独跑（暂排除 PRD 流程守卫）`699 passed, 1 skipped`，流程守卫在结果回填前按设计报 `1 failed`、回填后全过；`node check_single_source.js` clean（含新规则）；改动 `.py` 编译/导入通过；`ruamel.yaml` 解析 `config.yaml` 得 `qwen3_asr` 10 键、`whisper.runtime=local`，`SCHEMA` 与 `GROUPS` 均 40 键且零缺失、runtime 选项含 `qwen3_asr`。**未跑**真实独立环境安装 / 权重下载 / GPU 转录（依「命令执行分工」交用户在 `(vl)` 侧执行，AI 只给完整命令与判定标准，拿到输出后只读验证）；改了 `.py` → 需重启 `python -m server`，前端 dist 无需重建 |
| v1.37 | 2026-11-16 | FR-27、第 7/12 章、附录 A、`core/services/bili_meta_service.py`、`server/bili_api.py`、`frontend/src/api.ts`、`frontend/src/pages/PipelinePage.tsx`、`test/test_bili_meta.py` | **需求/任务**（用户报告「B站投稿文案生成后，刷新页面就看不到了」，先于开发写入 FR-27）：只读排查确认结果其实已落盘 `output/bili_meta.md`（本机 EP017｜Jillian Turecki，2026-11-16 10:17，2496 字节），但 `biliResult` 只是 `PipelinePage.tsx` 的 React state；`GET /api/bili/context` 只回素材统计、`existing` 字段根本不存在，前端也没有任何读取已生成文案的接口 —— 刷新即组件重新挂载、state 归零，唯一被持久化的只有 localStorage 里的 EP 序号。要求补齐「生成 → 落盘 → 刷新回显」闭环：md 作唯一真相源、`/context` 回带 `existing`、前端首次成功取到 context 时恢复一次；解析与渲染成对、损坏 / 缺失安全返回 `null`、归档后不回捞、零 LLM 费用。完整约束与验收见 FR-27。**执行/结果**：① `core/services/bili_meta_service.py` 新增 `load_saved_bili_meta(workspace)` —— 与 `_render_md` 配对的**唯一解析**：按 `# B站投稿文案（EPxxx｜讲者）` 首行 + `## 标题候选` / `## 简介` / `## 标签` 三节还原三件套，复用 `_valid_bili_meta()` 作唯一校验（3 标题 / 全角｜四段 / EP 前缀 / 简介 200–2000 字 / 恰好 10 标签），另带 `video_title` / `generated_at` / `saved_to`；文件不存在、编码坏、缺节、校验不过一律返回 `None`，绝不 500。② `server/bili_api.py` 的 `GET /api/bili/context` 增 `existing`（纯只读、零 LLM 费用）。③ `frontend/src/api.ts` 补 `BiliContext.existing` 与 `BiliMetaResult.generated_at`。④ `PipelinePage.tsx` 用 `biliHydratedRef` 保证**只在首次成功取到 context 时恢复一次**，之后任务状态刷新 / 手动刷新都不覆盖用户已选标题或本次刚生成的结果；`doGenerateBili` 成功后置 `biliRestored=false` 并标记已 hydrate，结果区加蓝色「上次生成 · 已自动恢复」Tag 与生成时间。⑤ `test/test_bili_meta.py` 新增 11 例（roundtrip / 生成后重读等价于刷新 / 无文件 / 5 种损坏 / 坏编码 / `/context` 有与无 existing / 损坏 md 仍 200）。⑥ `check_single_source.js` 新增 `bili-meta-md-format` 守卫，禁止其它模块出现 md 版式字面量。前端 dist 已重建（浏览器需 Ctrl+F5），后端改 `.py` 需重启 `python -m server`。 | 全量 `pytest test/ -q` → **716 passed, 1 skipped**（v1.36 基线 705 → +11）；阶段 B 未回填时守卫按设计 `1 failed, 715 passed`（证明结果后置门生效）；`node check_single_source.js` clean；`py_compile` 通过；`tsc --noEmit` 通过、`vite build` 成功（3044 modules）；真实 `output/bili_meta.md`（EP017｜Jillian Turecki，10:17:56）只读解析实测得 3 标题 / 548 字简介 / 10 标签。**未跑**浏览器 F5 交互（交用户：重启 server + Ctrl+F5 后应直接看到蓝色「已自动恢复」Tag 与三件套） |
| v1.38 | 2026-11-16 | FR-36（新增）、第 5/7/12 章、附录 A、`core/services/subtitle_source_service.py`、`core/_7_sub_into_vid.py`、`core/_12_dub_to_vid.py`、`config.yaml`、`server/settings_api.py`、`check_single_source.js`、`test/test_subtitle_source.py` | **需求/任务**（用户 2026-11-16「配音的字幕和声音不统一」，先于开发写入 FR-36）：只读实证配音版内部字幕↔声音严格对齐（483 段 wav 时长与 `new_sub_times` 窗口差值 0、无累积漂移），不一致的是压制版（双字幕、原文轴）与配音版（单字幕、配音轴）两套字幕，0.5s 采样 19.6% 有字幕时间点显示不同句子；第 12 步从原片重烧而非复用压制版画面。要求新增 `subtitle_source_service` 作唯一决策，按行号把 `src.srt` 英文文本映射到 `dub.srt` 的配音轴，使两版同为双语、文字一致；`dub.srt` 与第 11 步一字不改；只影响第 7/12 步，重跑第 12 步零 GPU / 零 LLM；`subtitle.source` 开关默认 legacy、可一键回退；条数不等明确报错；实测 5 份 srt 均 483 条、备份 mp4 因 manifest 优先不干扰 `find_video_files()`。完整约束与验收见 **FR-36（已实现）**。**执行/结果**：① 新增 `core/services/subtitle_source_service.py`（stdlib-only **唯一决策**）：`subtitle_source()` 读 `subtitle.source`，`load_key` 缺键 / 非法值安全回退 legacy；`parse_srt_blocks()` 严格解析（坏时间码抛 `ValueError`，绝不跳过造成错位）；`format_srt_time()` 毫秒 round 稳定；`build_dub_src_srt()` 按行号把 `src.srt` 文本装进 `dub.srt` 时间窗，`os.replace` 原子写 `dub_src.srt`，缺文件 / 空文件 / 条数不等一律明确报错；`subtitle_plan(version, workspace)` 返回有序「路径 + role」清单，路径统一正斜杠（ffmpeg `subtitles=` 把反斜杠当转义）。② 第 7/12 步改薄适配：`_7_sub_into_vid.py` 从 `subtitle_plan("sub", OUTPUT_DIR)` 取两路、删硬编码常量；`_12_dub_to_vid.py` 新增 `_subtitle_style(role)`（src 白英文 / trans 黄中文），滤镜按 plan 拼装，`legacy` 与旧滤镜逐字一致；`_11_merge_audio.py: create_srt_subtitle()` 与 `dub.srt` **一字未改**。③ `config.yaml` 增 `subtitle.source: legacy` 与注释；`server/settings_api.py` SCHEMA / GROUPS 增该键（数据驱动表单，免重建 dist）。④ `check_single_source.js` 增 `subtitle-source-decision` / `build-id-git-probe` 两条守卫；新增 `test/test_subtitle_source.py`（SRT 解析、时间码、行号配对、原子写、缺键回退、两档 plan、异常路径、第 7/12 步委派守卫）。⑤ 第 5 章步骤表按代码更正第 12 步「当前只有 dub.srt 单条黄中文」、第 7 章配置总表增行、附录 A 登记 service。⑥ 用户已在 `exp/unified-subtitle` 备份 `output_dub_legacy.mp4` / `dub_legacy.srt` / `tts_tasks_legacy.xlsx`，可秒回 `main`。 | 专项 `pytest test/test_subtitle_source.py test/test_version.py -q` → **24 passed**（补防回退用例后 23→24）；`node check_single_source.js` clean；`py_compile` 通过；`tsc --noEmit` exit 0、`vite build` 成功（3044 modules）；AST 静态核验 `legacy filter byte-identical: True`（测试环境缺 `cv2` 无法 import 第 12 步，改走 AST 抽取）；真实 `output/src.srt` / `dub.srt` 纯只读核验各 **483 条**、配对文本逐条等于 `src.srt`、时间窗逐条等于 `dub.srt`，首条 `(5.703, 9.011, 'why do men pull away after a night of being intimate')`；实测 `subtitle_plan("dub")` legacy 只挂 `output/dub.srt`、`("sub")` 为 src+trans；阶段 B 未回填时全量 `1 failed, 734 passed, 1 skipped`（守卫按设计拦截），阶段 C 回填后全量 `736 passed, 1 skipped` 转绿。**真实成片已验收（13:05:59–13:08:38，用户 `(vl)` 实跑第 12 步 158.8s / `状态=completed` / 零 error）**：`config.yaml` 切 `unified` 后 `subtitle_plan` 实测两路、日志出现 `Parsed_subtitles_2` + `Parsed_subtitles_3` 两个 libass 节点；`output_dub.mp4` 重生成 317,877,383 B（legacy 备份 313,820,709 B），ffprobe 1798.233s / H264 1920x1080 24000/1001 + AAC，与压制版 1798.234s 一致；只读抽帧 7s / 428s / 1136s 三处两版双语文字逐字一致，边界 9.15s / 1138.0s 压制版有字幕、配音版已空（与 SRT 窗口预测吻合，印证时间轴刻意不统一）。**未跑**浏览器播放观感（交用户） |
| v1.39 | 2026-11-16 | FR-37（新增）、第 12 章、附录 A、`version.py`、`server/app.py`、`frontend/vite.config.ts`、`frontend/src/api.ts`、`frontend/src/App.tsx`、`test/test_version.py` | **需求/任务**（用户 2026-11-16「VideoLingo v3.0.0 为什么我的版本号一直不变捏，我加版本号就是为了确认我的代码是新的呀」，先于开发写入 FR-37）：只读排查确认 `version.py` 的 `__version__ = "3.0.0"` 是手写静态常量、全库只在 `439c84a` 动过，`App.tsx → getVersion() → /api/version` 每次读同一值，故提交/构建多少次都不变；PRD 此前无版本号 FR，`test_version.py` 反把静态 semver 锁死。用户要的是代码新旧标识而非语义版本。要求保留 `__version__` 不动，新增随提交自动变化的 build 标识：后端运行时回 git 短 hash + 提交时间 + dirty，前端 `npm run build` 时注入 git 短 hash；页面并列显示两者，不一致时提示「前端 dist 过期」，根治反复踩的漏 build / 漏 Ctrl+F5。无 .git 或 git 失败安全回退 unknown、零新依赖、纯常量导入零副作用、`/api/version` 保留原 `version` 字段。完整约束与验收见 **FR-37（已实现）**。**执行/结果**：① `version.py` 保留 `__version__ = "3.0.0"` 一字不动，新增 `build_info()` 作**唯一 git 探测**：`git rev-parse --short HEAD` / `git show -s --format=%ci HEAD` / `git status --porcelain` 经 `_git()` 带 2s 超时、`check=False`，无 `.git` / git 不可用 / 超时 / 非 0 退出一律返回 `{"hash":"unknown","committed_at":"unknown","dirty":False}`，绝不抛异常；探测只在调用时发生，`import version` 零副作用。② `server/app.py` 的 `GET /api/version` 改为 `{"version": __version__, "build": build_info()}`，`GET /api/pipeline` 保持原样，server 不自跑 git。③ `frontend/vite.config.ts` 用 `execFileSync` 在构建期求一次短 hash，`define: { __BUILD_ID__: JSON.stringify(gitShortHash()) }` 注入，失败回退 `unknown`；dev server 同样注入。④ `frontend/src/api.ts` 增 `BuildInfo` / `VersionInfo` 与 `getVersion()` 解析 `build`；`App.tsx` 并列显示 `v3.0.0` + 后端 hash + 前端 hash + 提交时间 / dirty，两侧 hash 已知且不等时显示黄色「前端 dist 过期，请 npm run build + Ctrl+F5」Tag，不弹窗 / 不 reload。⑤ `check_single_source.js` 增 `build-id-git-probe` 守卫（禁止 route / core / server 出现第二份 git build id 探测）。⑥ `test/test_version.py` 保留原 4 例，新增 `/api/version.build` 字段断言、`import version` 无子进程副作用（monkeypatch）、无 `.git` / git 抛错回退 unknown、正常提交返回 hash + 时间 + dirty 等用例。⑦ 实测 `build_info()` = `{'hash':'3093955','committed_at':'2026-11-16 12:21:36 +0800','dirty':True}`（dirty=True 因实验分支改动尚未提交，符合预期）。 | 专项 `pytest test/test_subtitle_source.py test/test_version.py -q` → **24 passed**；`node check_single_source.js` clean；`py_compile` 通过；`tsc --noEmit` exit 0；`vite build` 成功（3044 modules），`frontend/dist/assets/index-C8obdFOu.js` 内实测含注入的短 hash `3093955`；`/api/version` 的 `version` 仍为 `3.0.0`，原 4 例版本断言全过。全量 `pytest test/ -q` → **736 passed, 1 skipped**（v1.37 基线 716 → +20；阶段 B 未回填时守卫按设计拦下 `1 failed`，C 回填后全绿）。**未跑**真实「重启 server + Ctrl+F5 看页面」与提交后 hash 变化（按分工交用户：重启 + 强刷后应看到两个相同短 hash，改 `.tsx` 不 build 时应出现黄色过期 Tag） |
| v1.40 | 2026-11-16 | FR-38（新增）、第 12 章、`core/asr_backend/qwen3_asr_runner.py`、`test/test_qwen3_asr.py` | **需求/任务**（用户 2026-11-16「模型已经下载完成了，重跑了，还是报错了」，先于开发写入）：Qwen3-ASR 首次真实 GPU 实跑在窗口 1 返回**退出码 5**（`ForcedAligner 时间戳为空或格式不可识别`），只读第三方源码定位 `time_stamps` 实际是 `ForcedAlignResult` dataclass（`.items` 里是 `ForcedAlignItem`、单位为秒），而 FR-35 的 `parse_stamps()` 只认 list 形态 → `words` 恒空；附带修复 runner 中文诊断在 Windows GBK 下的乱码与 stdout JSON 中文回传风险。要求只改 runner + 测试、主环境零改动、不重启 server；兼容旧形态、不二次换算秒、绝不静默回退、stdout 仍只一个 JSON、UTF-8 自包含。完整约束与验收见 **FR-38（已实现）**。**执行/结果**：① `core/asr_backend/qwen3_asr_runner.py: parse_stamps()` 重写为**唯一时间戳解析决策**：优先取 `time_stamps.items`（真实 `ForcedAlignResult`），兼容 `__iter__` 可迭代对象、`list[dict]` / `list[object]` / 嵌套 list / 单个 dict；`dict.items` 是 bound method，用 `callable()` 排除避免误判；时间直接透传 `start_time` / `end_time`（`Qwen3ForcedAligner.align()` 已按秒输出，**不二次除 1000**）；空 `words` 仍抛退出码 5，错误信息带 `time_stamps` / `items` 真实类型名与条目数，绝不伪造时间戳、绝不回退 whisperX。② 新增模块级 `_force_utf8()`，对 `sys.stdout` / `sys.stderr` 强制 UTF-8（不可 reconfigure 时安全跳过），根治 Windows GBK 中文诊断乱码与 stdout JSON 中文回传风险；主环境 `subprocess.run(encoding="utf-8")` 无需改。③ `test/test_qwen3_asr.py` 增 **6 例**：真实 `ForcedAlignResult` dataclass 解析 + 秒单位断言、无 `.items` 的 `__iter__` 回退、`dict.items` 不误判、空结果带类型名报错、UTF-8 模块级调用的 AST 守卫、`runner.main()` 用真实形态跑通 2 窗口聚合 + 重叠去重 + stdout 只一个 JSON。④ **主环境零改动**：`qwen3_asr.py` / `_2_asr.py` / `server` / `config.yaml` / 前端一字未动 → runner 由子进程每次重新加载，**不用重启 `python -m server`、不用重建 dist**；修复不改变 `_audio_fingerprint`，`output/` 产物与断点无需清理。 | 专项 `pytest test/test_qwen3_asr.py -q` → **25 passed**（FR-35 基线 19 → +6）；`py_compile` 通过；`node check_single_source.js` clean；阶段 A 流程守卫按设计 `1 failed, 4 passed`（最新行待填），阶段 C 回填后全量 `pytest test/ -q` 转绿。**真实 GPU 重跑已验收**（按「命令执行分工」由用户执行，AI 只读验日志）：`2026-11-16 14:04:18 === 任务开始` → `14:05:41 === 任务结束 状态=completed`，第 2 步 83.0s / `状态=ok`；3 个 ≤270s 窗口全过，无退出码 5、无 ERROR / traceback、中文乱码消失；`cleaned_chunks.json` 落盘 1458 词、时间 0.000→602.360s 覆盖 603.2s 切片；仅 1 条非致命超长词 warning 与 pyannote VAD 的 torchcodec UserWarning。另只读调研确认 `speaker_id` 全为 `None`（Qwen3-ASR 不含 diarization，项目 speaker_id 下游零消费者），详见 FR-38 阶段 C 结论 |
| v1.41 | 2026-11-16 | FR-39（新增·待实现）、第 12 章、`core/asr_backend/qwen3_asr_runner.py`、`test/test_qwen3_asr.py`、`check_single_source.js` | **需求/任务**（用户 2026-11-16「在意的是 Qwen 时间戳整体质量 → 那 71 个零时长词才是真问题，按 C 一起做」，先于开发写入）：FR-38 真实 GPU 跑通后只读复盘发现 ForcedAligner 原始输出 1459 词里 71 个零时长（4.9%，62 个连续组，其中 31 组左右锚点空隙 ≤ 0 数学上无法插值），另有 36 字符零时长品牌名重复幻觉与 25 字符非零时长网址连写串。要求在 runner 内新增唯一词级时间戳整形决策：零时长+超长幻觉丢弃、有空隙组按字符比例插值、空隙 ≤ MIN_GAP 组保持零跨度并显式计数、URL 连写长串按词素切分并按比例分时间、stderr 汇总诊断；三字段契约与窗口/去重/偏移一律不变，主环境零改动（不重启 server / 不重建 dist / 不改 config / 不清断点），纯 stdlib 零 GPU 零 LLM。完整约束与验收见 **FR-39（已实现）**。**执行/结果**：① `core/asr_backend/qwen3_asr_runner.py` 新增 `normalize_words()` 作**唯一词级时间戳整形决策**（常量 `MIN_INTERP_GAP=0.02` / `HALLUCINATION_CHARS=30` / `URL_WORD_CHARS=20` / `URL_TOKENS=("www","com")` + `_split_url_word()`），四步顺序：丢「零时长 + >30 字符」幻觉 → 拆非零时长超长纯 ASCII URL 串（按字符比例分时间窗）→ 有空隙零时长组按字符比例线性插值到 `[prev.end, next.start)` → 空隙 <0.02s 组保持零跨度并计 `still_zero`；全程 O(n) / 纯 stdlib / 不 import 项目模块，输入与已对齐词不改写，返回 `(words, stats)`。② `main()` 在 `parse_stamps()` 之后调用并每窗口打一行 `[Qwen3-ASR] word normalize: dropped=… interpolated=… still_zero=… split=…`（stderr / UTF-8），窗口切分 / overlap 去重 / 相对→绝对偏移 / stdout 单 JSON 一律不动。③ **词素表按约束 7 收窄**：B 阶段发现 `org`（`organization`）/ `net`（`internet`）/ `com`（`accommodation`）会命中普通长词造成误拆，故去掉 org/net、保留 www/com 并加「全串仅出现 1 次 + 前段 ≥4 + 后段 3~12 字符」双校验，**先改 PRD 约束 7 再改代码**。④ `test/test_qwen3_asr.py` 增 **9 例**（幻觉丢弃 / 对齐长词不丢 / 按字符比例插值 / 无空隙不推移邻居 / 正常词逐字不变 / URL 拆分与比例 / 中文·标点·短词·双 com·长词不误拆 / 插值不越界且单调 / stats 与三字段契约 / `main()` 端到端仍单 JSON）；`check_single_source.js` 增 `qwen-word-normalize` 守卫。⑤ **主环境零改动**：`qwen3_asr.py: normalize_runner_result()` / `_2_asr.py` / `audio_preprocess.py` / `server/` / `config.yaml` / 前端一字未动 → 不重启 server、不重建 dist、不改配置、`_audio_fingerprint` 不变、断点无需清理，改完只需重跑第 2 步。 | 专项 `pytest test/test_qwen3_asr.py -q` → **34 passed**（v1.40 基线 25 → +9）；`py_compile` 通过；`node check_single_source.js` clean；阶段 A / B 未回填时流程守卫按设计 `1 failed, 4 passed`、全量 `1 failed, 750 passed, 1 skipped`，**C 回填后全量 `751 passed, 1 skipped`**；流程守卫 5 例全过。真实 `output/log/asr_checkpoint/seg_0000.json` **纯内存复算、未写盘**：1459 → 1459 词（丢 1 幻觉 + 拆 1 长串成 2，净 0）、零时长 **71 → 34（减少 37 / 52.1%）**、合计 `dropped=1 / interpolated=36 / still_zero=34 / split=1`；所有已对齐且 ≤20 字符的正常词文本与时间逐字不变（误改 0）、锚点词被推移 0；`thrivecosmeticscomjillian` 拆为 `thrivecosmetics.com 471.682–473.787` + `jillian 473.787–474.562`。34 个仍零时长是数学硬约束（实测 31/62 组锚点空隙 ≤0），不制造重叠、不后移邻居。**未跑**真实 GPU 重跑第 2 步（按「命令执行分工」交用户，AI 只读验日志诊断行与 `cleaned_chunks.json`） |
| v1.42 | 2026-11-16 | FR-40（新增）、第 12 章、`core/_11_merge_audio.py`、`test/test_merge_audio.py`、`test/test_merge_audio_ffmpeg.py` | **需求/任务**（用户 2026-11-16「再00:02:56这附近也出现了类似的，应该还有很多，多出来的配音（男人在亲热一晚后），没有字幕之类的，不知道哪来的」+ 6 处时间点补充，先于开发写入）：方案 C 成片里多处字幕空档出现无字幕的中文配音短片段（长空档念「男人在亲热一晚后」、短空档只念「男人」），只读诊断已定位为 `build_batch_filtergraph()` 让多个 `atrim` 复用同一个 `[SIL0]` 静音标签，ffmpeg 只给第一个分支真静音、其余串入第一个输入 wav 的开头；6 个报障时间点与 22 个空档中 21 个有声一一吻合。要求每个 gap 一条独立静音流（显式 `asplit=K`），保持函数签名 / `has_gap` / 单个 `anullsrc` 输入 / 分批与终拼 / `-map "[out]"` 不变，不动任何产物数据、时间轴、字幕与第 10 步产物，零新增依赖与配置；回归测试必须用非静音 wav 实测静音段 rms（纯字符串断言不足以验收）。改完只重跑第 11、12 步，不重跑第 10 步 GPU 合成。完整约束与验收见 **FR-40（已实现）**。**执行/结果**：① `core/_11_merge_audio.py: build_batch_filtergraph()` 改为两遍扫描：第一遍只收集每个 gap 的 `(分支号, gap 秒数)`，第二遍用显式 `asplit=K` 给每个 gap 分配**独占** `[S0]`…`[S{K-1}]` 分支，第 j 段消费自己的 `[S{k}]atrim`；K=1 时直接 `[S0]` 不加 asplit。函数签名 `(graph, prev_end, has_gap)`、`has_gap` 语义（仍只挂 1 个 `anullsrc` 输入）、`BATCH_SIZE=80` 分批与终拼、`-map "[out]"` 全部不变；`merge_audio_segments_ffmpeg()` / `iter_segments()` / `create_srt_subtitle()` / 第 10 步 / 前端 / `config.yaml` **零改动**。② `test/test_merge_audio.py` 共 8 例：原 6 例更新 `[SIL0]` 断言，新增「3 个不同长度 gap 各占独占分支、每个 `[S{k}]atrim` 只出现一次」「K=1 不加 asplit」；`test/test_merge_audio_ffmpeg.py` 新增 1 例**非静音 tone wav + 真实 ffmpeg + PCM rms** 回归（原有静音 wav 用例保留）——原有用例全是静音 wav，串音测不出来，这次才真正守住约束 5。③ **真实产物只读复算、不落盘**：取 `output/audio/tts_tasks.xlsx` flatten 前 45 段、按修复后滤镜链跑单批 ffmpeg 到 stdout，输出 `205.84s` 与 `new_sub_times` 末值一致；用户报障的 6 个空档（43s / 77s / 85s / 90s / 96s / 2:56）rms **全部 0.0**（修复前同位置 3100–4600），有字幕的配音段 rms `3495.8 / 4074.7 / 4776.7` 不变。④ **重跑口径**：只需重跑第 11 步（继续跑、不带 force）与第 12 步；`segs/*.wav` 与 `tts_tasks.xlsx` 原样复用，**不重跑第 10 步**，省掉 1448 段 IndexTTS GPU 合成。 | 专项 `pytest test/test_merge_audio.py test/test_merge_audio_ffmpeg.py -q` → **11 passed**；`py_compile` 通过；`node check_single_source.js` clean；真实产物只读复算 6/6 空档 rms=0.0 且配音段有声；全量结果与阶段 C 结论见下。全量 `pytest test/ -q` → **754 passed / 1 skipped**（v1.41 基线 751 → +3），流程守卫 5 例全过。**线上真实重跑已由用户在 `(vl)` 完成并验收通过（2026-11-16 15:31–15:32）**：只重跑第 11、12 步，`output/dub.mp3`(15:31:39)、`output/dub.srt`(15:31:37)、`output/output_dub.mp4`(15:32:46) 已重建，而 `segs/1_0.wav`、`tts_tasks.xlsx` 仍为 14:59（**未重跑第 10 步**）。AI 只读复测：`dub.srt` 仍 142 条、首条「男人在亲热一晚后就疏远了」与末条 594.594–601.455s 未变；**全片 22 个 >0.25s 空档 rms 全部 0.0**（max=0.0、mean=0.0、>300 者 0 个，修复前 21/22 为 3100–4600），成片 `output_dub.mp4` 同位置仅剩 2.3–14.2 的背景轨底噪；有字幕配音段 rms `3870.1 / 4538.1 / 3304.7` 保持有声。用户听感确认「修复了」，无字幕的「男人」短句消失。 |

| v1.43 | 2026-11-16 | FR-41（新增）、第 12 章、`core/asr_backend/qwen3_asr_runner.py`、`core/_10_gen_audio.py`、新建 `core/services/dub_axis_service.py`、`test/test_qwen3_asr.py` 与下游新单测 | **需求/任务**（用户 2026-11-16「00:08:02 字幕与配音提前显示，00:08:07 英文才真正开口、配音却没声音了」「我们必须要修这种问题，不然后影响配音质量」，并在 A/B/C 三方案中**拍板 C：上游 ASR + 下游配音轴一起修**，先于开发写入）：只读诊断坐实两层根因——上游 Qwen3-ASR ForcedAligner 把 #113 的 10 个英文词均匀铺成 12.32s 窗口（4 个词各恰好 1.52s、间隔 3.04s），真实发声只在 487.8–491.0；下游 `merge_chunks()` 按 chunk 累积时长把 4.946s 配音排在 482.269–487.214，`chunk_end_time` 只约束「不超过」导致尾部 3.83s 空档恰好压住真实人声。要求两层一起修：上游识别并收缩均匀假词组（只缩时长、不搬运、不臆造语义重定位），下游对「chunk 末行 + 窗口比配音长 >2s + 窗口后段人声不低于前段 0.8 倍」的少数极端行把配音右对齐到 `chunk_end_time`，其余行 `new_sub_times` 一字不动；不动字幕文本、翻译、`segs/*.wav`、变速算法与第 11 步静音分支，`dub.axis_guard` 默认开、手改 false 可一键回退，零新增依赖。完整约束与验收见 **FR-41（已实现）**。**执行/结果**：① 上游 `core/asr_backend/qwen3_asr_runner.py` 在唯一整形点 `normalize_words()` 内新增第 5 步 `detect_uniform_stretch()` / `_shrink_uniform_runs()`（≥3 个连续 ≥1.0s 词、起点间隔偏差 ≤15%、跨度 ≥5s 判为均匀摊词组，按 `0.15+0.08×字符数` 秒封顶并裁剪到下一个词 start，**起点/顺序/词文本/词数一字不动、禁止语义重定位**），stats 扩为 7 键、stderr 增 `stretch_*` 诊断、stdout 仍单 JSON；`check_single_source.js` 的 `qwen-word-normalize` 守卫纳入两个新函数。② 下游新建 `core/services/dub_axis_service.py`（唯一配音轴定位决策，纯逻辑/IO 分离、stdlib-only、`decode_rms` 按需 ffmpeg 解码窗口小段不全量驻留）；`core/_10_gen_audio.py: merge_chunks()` 仅在 chunk 结束后对末行调用 `axis_delta()`，命中则把该行所有子行平移 `chunk_end_time − cur_time` 并令 `cur_time = chunk_end_time`，富余静音交第 11 步 `asplit=K` 插入；`dub.axis_guard` 用 `load_key` + `KeyError` 兜底默认 `true`（不改 `config.yaml` 即生效、手改 `false` 回退），`process_chunk()` / `_speed_plan()` / 第 11 步 / 前端 / SCHEMA / 字幕文本 / `segs/*.wav` 零改动。③ `test/test_qwen3_asr.py` +8 例（原两处 stats 精确断言同步扩到 7 键）、新建 `test/test_dub_axis.py` 15 例（非静音 PCM rms、注入式能量、短路不解码、右对齐、开关回退）。④ **真实产物只读复算、不落盘**：124 行 / 84 chunk 中命中 5 个末行（number 20/21/52/70/102），119/124 行 `new_sub_times` 逐行不变；用户报障 #102 由 `482.269–487.215` → **`486.096–491.042`**，与真实人声 `487.8–491.0` 重叠 3.24s；全片 142 段负 gap 0 个、末段终点 601.455 不变。⑤ **边界**：上游只消除 1.52s 均匀假词时长并输出诊断，不搬运词、不承诺收缩 12.32s 窗口（广告段 rms 3831 与真实主句 4084 相当，纯能量会贴错），实质对齐由下游护栏完成。 | 专项 `pytest test/test_qwen3_asr.py -q` → **42 passed**、`pytest test/test_dub_axis.py -q` → **15 passed**；全量 `pytest test/ -q` → **777 passed / 1 skipped**（v1.42 基线 754 → +23）；`py_compile` 通过；`node check_single_source.js` clean；流程守卫 5 例全过；真实产物只读复算 5/84 chunk 命中、#102 右移 3.827s、142 段无负 gap。**线上真实重跑按「命令执行分工」交用户在 `(vl)` 执行**（改 runner 须强制重跑第 2 步清 ASR checkpoint → 3/4/5/6 步 LLM → 8 → 10/11/12；第 10 步 `tts_main.py:35` 跳过已有 wav 不重合成 1448 段 GPU；改 `.py` 需重启 server、未改前端不重建 dist），AI 未跑真实合成，重跑后只读验收 `dub.srt` 窗口、`dub.mp3` 各空档 rms 与成片听感。 |
| v1.44 | 2026-11-16 | FR-42（新增，仅诊断）、第 12 章 | **需求/任务**（用户 2026-11-16「000759 字幕还是不对，000807 听到 Probably the biggest problem…，`That 's` 是前面读网址的单词」，并选定方案 C「先不改代码」）：查明原声字幕 #119 起点错位原因，把诊断、候选方案与约束先落档，不改代码 / 测试 / 配置 / `output/`。**执行/结果**：已核实 `seg_0000.json`（3 子段 1459 词）并复算 478–493s 词表与全片 ≥1.0s 间隙分布（25 处，本案为连续 4 处）；结论为 ASR ForcedAligner 把 `Probably … is that` 前 9 个词锚到读网址区间（`478.96–490.80`），真实开口约 `487.8`，下游只继承；`That's` 属广告尾句、不得删除；候选方案 A（上游检测重排，不得只凭 RMS）/ B（只改切句，治标）已写入 FR-42，等待用户决定。本次**未改任何代码、测试、配置与 `output/` 产物**，未跑 pytest（无代码改动）。 | 只读：`seg_0000.json` 词表复算、`src.srt` #118/#119 核对、静音检测对照；`test/test_prd_process.py` 守卫待下一步运行确认 |
| v1.45 | 2026-11-17 | FR-43（新增）、新增 `check_asr_probe.py`、`test/test_asr_probe.py` | **需求/任务**（用户 2026-11-17「先试试重新转写，调整参数等等方式，看看能从根源上解决这个问题，不行再用 A，A 会有很多意外影响，比如中间停留超过 1 秒又乱了」）：为 #119 错位新增只读对照实验脚本，用 7 组（基线复现 / 强制 English / 加 context / 短窗口 / 中窗口）重转 `raw.mp3` 的 476–495s，判断能否从 ASR 源头消除错位；方案 A 降为后备；不改生产链路与 runner，不写 `output/`。**执行/结果**：① 新增 `check_asr_probe.py`（仓库根，**只读**）：`importlib` 按路径加载 `qwen3_asr_runner.py`，复用 `decode_window` / `parse_stamps` / `normalize_words`，按 G0–G6 分组重转 `raw.mp3` 并只看 476–495s，输出每组转写文本、词时间、`Probably` / `That's` 起点、≥1.0s 大间隙、连续大间隙数、是否含网址拼读，并给出 PASS/FAIL 判定，结果写 `<tempdir>/asr_probe/result.json`。② 新增 `test/test_asr_probe.py` 7 例纯函数用例（间隙计数、摘要、判定、词数容差、网址拼读、无 Probably 安全、只读静态检查）。③ runner / 配置 / `output/` / 工作区原有改动一字未动，**主环境零改动，无需重启 server、无需重建 dist**。④ 只读核实 `segments.json` 为单段 `[[0.0, 603.157]]`；修正 FR-43 中「12GB 显存」未核实表述。⑤ **真实 GPU 对照实验**：用户在 `qw` 环境完成 G0/G1/G4 与 G0/G2/G5/G6 两轮运行；G0 两轮均复现 `Probably=478.962s`、连续 4 个大间隙，基线可信。G1=G0；G2 `Probably=482.442s` 且连续 3 个大间隙；G4/G5 `Probably=481.48s`、无连续大间隙但词数超 3% 容差；G6 识别出网址单字母拼读，但 `Probably=484.48s` 仍早于真实约 487.8s，`the=488.48s`，词数超容差。已测 G0/G1/G2/G4/G5/G6 全 FAIL，无已验证生产候选；G3 未跑。已测语言、context、窗口长短及补出网址拼读都不足以修复。⑥ 未改 runner / 配置 / `output/`；需另开 FR 隔离验证 ASR 文本与 ForcedAligner 对齐，方案 A 仍须用户再次拍板。 | `ast.parse` 通过；pytest 无法在沙箱启动，直接调用 7 个测试函数 → **7 passed / 0 failed**；`node check_single_source.js` clean；全量 pytest 未在沙箱运行。用户完成两轮 GPU 实验，结果均按 FR-43 判定为 FAIL；工作区原有改动和 `output/` 未动。 |
| v1.46 | 2026-11-17 | FR-44（新增）、`check_asr_layers.py`、`test/test_asr_layers.py` | **需求/任务**：为 #119 错位新增独立只读诊断，对 `[268.5,538.5]` 与 `[440,520]` 两个窗口分别比较纯 ASR 文本、同文本直接 ForcedAligner 对齐、库内 ASR+ForcedAligner 组合时间戳，输出原文、词级时间及统一绝对时间，重点标注 `That's` / `Probably` 与词序；据差异区分 ASR 文本错误、直接对齐异常、库内分块/合并差异。脚本不改生产链路、runner、第三方库、配置、字幕、断点或 `output/`，仅写系统临时目录；真实 GPU 执行由用户在 `qw` 环境完成。**执行/结果**：新增独立只读脚本及 8 个纯函数测试；修正直接 `ForcedAlignResult.items` 的解析，并通过 AST 静态解析、8 个纯函数测试及 `node check_single_source.js`。只读核对第三方返回值结构通过。用户已在 `qw` 环境完成真实 GPU 三路径实验：诊断窗纯 ASR 与库内组合文本规范化一致；直接 ForcedAligner 和库内组合均把 `Probably` 定位于 484.56–485.12s，但 `the` 到 488.48s 才开始，支持诊断窗存在直接对齐定位异常、且库内流程继承异常。生产窗纯 ASR 与库内组合文本不一致，不能据此单独归因对齐器；直接对齐 `Probably=479.042s`、组合 `Probably=485.14s`，显示时间路径差异。实验仅提供根因证据，问题未修复；未改生产链路、配置、字幕、断点或 `output/`；方案 A 未实施，须用户另行确认。pytest 专项及全量回归未运行，不宣称通过。 | 本地无写盘验证：AST 解析通过；8 个纯函数测试通过；`node check_single_source.js` 输出 `✓ single-source guard clean`。用户 GPU 实验报告已只读检查，路径为 `%TEMP%\asr_layers\result.json`；关键绝对时间与文本比较已复核。`pytest test/test_asr_layers.py -q` 与全量 `pytest test/ -q` 均未运行，不能宣称全量测试通过；`output/` / 断点未触碰。 |

> **变更记录强制格式（v1.35 起）**：每一行都必须按「先需求后开发」分两段——阶段 A 先追加本行、只写 `**需求/任务**：…`，`**执行/结果**` 与验证列写「待填」；阶段 C 才回填实际改动与验证结果。`test/test_prd_process.py` 校验 v1.35 起每行同时含两段标记、且最新行不得残留「待填」；**禁止开发完才补需求行**。v1.30–v1.34 为历史行，不追溯改写（其中 v1.33 是线上返工、未按两段式记录）。

---

## 附录 A 代码索引（按目录）

| 区域 | 文件 | 关键定义 |
|---|---|---|
| 注册表 | `core/pipeline.py` | `Step`、`STEPS`、`get_step`、`get_steps`、`pipeline_status` |
| 服务层 | `core/services/run_service.py` | `resolve_steps`、`force_clean`、`default_run_range`、`burn_subtitles_enabled`、`clamp_end_index` |
| | `core/services/asr_service.py` | `decode_settings`、`build_asr_options`、`DECODE_VERSION`（主转录解码策略及断点指纹） |
| | `core/services/llm_service.py` | `classify_error`、`on_failure`、`check_response`、`probe`、`probe_pool` |
| | `core/services/archive_service.py` | `archive_current`、`_remove_with_retry`、`retry_locked` |
| | `core/services/bili_meta_service.py` | `load_transcript`、`_read_transcript_lines`、`full_text`、`sample_lines`、`transcript_brief`、`load_video_context`、`suggest_speaker`、`_valid_bili_meta`、`generate_bili_meta`、`_render_md`、`load_saved_bili_meta`（唯一 md 解析，与 `_render_md` 配对） |
| | `core/services/subtitle_source_service.py` | `subtitle_source`、`subtitle_plan`、`build_dub_src_srt`、`parse_srt_blocks`、`format_srt_time`（**唯一字幕源决策**：第 7/12 步烧哪些 srt、按行号把原文文本映射到配音轴；v1.38 / FR-36） |
| | `core/services/ad_service.py` | `parse_srt`、`read_transcript`、`numbered_transcript`、`normalize_ads`、`validate_ads`、`find_scene_cuts`、`find_silences`、`refine_segments`、`sponsorblock_segments`、`_is_manual`、`_slot_overlap`、`is_dismissed`、`_merge_dismissed`、`render_md`、`save_edl`、`load_edl`、`update_edl_segments`、`context`、`detect_ads` |
| | `core/services/ad_cut_service.py` | `list_sources`、`default_source`、`resolve_source`、`probe`、`probe_frame_rate`、`media_info`、`build_keep`、`build_command`、`_encode_args_for`、`cut_video` |
| | `core/services/encode_service.py` | `load_config`、`_encoder_for`、`build_encode_args`（`preset` 按编码器映射）、`DEFAULTS`、`_NVENC_PRESETS`（FR-29 编码参数唯一决策点） |
| | `core/services/dub_timeline_service.py` | `parse_excel_list`（安全解析 `tts_tasks.xlsx` 列表单元格 / 兼容 numpy 2.x repr）、`plain_time_pairs`（写盘前转 builtin float）—— 第 10/11 步共用的唯一决策，stdlib-only 便于无 pydub 环境单测（FR-23/34，v1.27） |
| | `core/services/timing_service.py` | `record`（原子写 `output/log/step_timings.json`，IO 失败静默）、`backfill`（历史 `pipeline_20*.log` 回填，按 source+id+at+seconds 去重幂等）、`summary` / `attach_last_seconds`（按 id 聚合 last/best/count/total 并回填流水线页）、`dub_breakdown` / `latest_breakdown`（第 10 步内部 gpt/s2mel/bigvgan 拆解）、`format_report`（Markdown 表）—— 步骤耗时唯一来源，stdlib-only（FR-23/34，v1.28） |
| | `core/services/translate_service.py` | `split_chunks`、`load_active_terms`、`merge_terms`、`load_job_terms`、`term_catalog`、`history_term_sources`、`history_import_preview`、`import_history_terms`、`_merge_into_base_set`、`write_back_terms`、`build_shared_context`、`TranslationCkpt`、`DEFAULTS` |
| 配音（FR-33/34） | `core/tts_backend/index_tts.py` | `index_tts_for_videolingo`、`_resolve_reference`（音色来源唯一决策）、`_ensure_refers`、`_get_voice_map`、`reload_voice_map`、`_get_engine`、`self_check`（单例 + 三锁 + 懒加载） |
| | `core/tts_backend/voice_map.py` | `build_voice_map`、`load_voice_map`、`maybe_build_voice_map`、`apply_overrides`、`set_speaker_ref`、`add_speaker`、`remove_speaker`、`clear_dubbed`、`_fingerprint`、`_choose_k`、`_extract_embedding`、`_smooth_labels`、`_run_samples`、`PRODUCER_ID`、`VERSION` |
| | `core/tts_backend/{tts_main,refer_audio}.py` | `tts_main` 分发 10 后端（index_tts 分支局部 import）/ `extract_refer_audio_main`、`_fixed_voice_only`、`_refers_complete` |
| | `core/asr_backend/demucs_vl.py` | `demucs_audio`、`_save_mp3_stream`、`_to_pcm_frames`、`_encode_ffmpeg`、`_encode_lameenc_chunked`（长音频流式编码） |
| 步骤 | `core/_1_ytdlp.py` | `pick_js_runtime`、`probe_cookies`、`cookies_have_login`、`build_ydl_opts` |
| | `core/_2_asr.py` | `transcribe`（唯一 ASR 运行时分发）、`_audio_fingerprint`、`_load_or_save_segments`、`_save_seg_result` |
| | `core/asr_backend/qwen3_asr.py` | `qwen3_config`、`build_windows`、`normalize_runner_result`、`transcribe_audio_qwen3_asr` —— 主环境适配层，子进程调用 + 时间偏移 + 词级结构（FR-35，v1.36） |
| | `core/asr_backend/qwen3_asr_runner.py` | `qwen3_asr_runner` —— 独立 conda 环境内执行，ffmpeg 解码 → 窗口切分 → `Qwen3ASRModel` + ForcedAligner → stdout JSON（FR-35，v1.36） |
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
| | `server/voice_api.py` | `get_map`、`get_audio`、`put_segments`、`post_rebuild`、`post_propagate`、`put_chunks`、`post_rebuild_chunks`、`post_split_chunks`、`post_revoice`、`post_reload`、`post_speaker`、`put_speaker_ref`、`delete_speaker`（13 端点，`/api/voice`；`subtitle_turn_service` / `voice_map` 为决策层） |
| 批量 | `core/batch_engine.py` / `core/job_runner.py` | `BatchEngine` / 子进程入口 |
| 前端 | `frontend/src/App.tsx` | hash 路由（7 页）、重启/停止 |
| | `frontend/src/pages/*.tsx` | 7 个页面（含 `VoiceMapPage.tsx` 配音分配，FR-34） |
| | `frontend/src/api.ts` / `batchApi.ts` / `translateApi.ts` / `voiceApi.ts` / `ws.ts` / `inputDraft.ts` | 接口、事件、草稿持久化 |
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

# protobuf 依赖对齐（第 2 步 ASR 报 `cannot import name 'builder'` 时，(vl) 环境执行）
# 修复后 pip check 残留一条 descript-audiotools 不兼容，属已知可接受冲突
python -m pip install --no-deps "protobuf==5.29.6"
python -c "from google.protobuf.internal import builder; import pyannote.audio, whisperx; print('OK')"

# 前端
cd frontend && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/vite build

# VAD 空档自检（只读；--fill 需 GPU）
python check_vad_gaps.py

# 本地配音（以下均在用户自己的 (vl) 终端执行；跑 GPU 前先关 index-tts webui）
python -m core.tts_backend.index_tts --smoke           # 权重 / lang / 音色解析自检
python -m core.tts_backend.voice_map                   # 只读校验契约（producer / 人数 / degraded）
python -m core.tts_backend.voice_map --build --force   # 重建音色初稿，写 voice_map.json + voice_refs/
python -m core.tts_backend.voice_map --samples=3       # A/B 试听，产物写 output/audio/voice_exp/

# 生效口径：改 .py / config.yaml → 重启 python -m server；改 .tsx / .ts → 前端 npm run build
# （dist 静态托管，漏构建 = 新页面不出现）；新增 / 改 API 契约 → 两边都要
```

相关文档：`docs/batch-phase1.md`（批量期1交付说明）、`README.md`（上游开源项目说明）、`test/README.md`（测试隔离约定）、`.workbuddy/memory/`（项目工作日志与长期记忆）。
