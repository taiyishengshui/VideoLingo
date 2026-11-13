# VideoLingo 单元测试（流水线 1–4 步）

离线、零 API 成本：网络层被假客户端替换，落盘全部在临时目录。
任何用例都不读写真实的 `config.yaml` / `output/` / `terms/`。

## 跑起来

```bash
conda activate vl
cd D:/code/vl
python -m pytest test/ -q          # 全量
python -m pytest test/test_llm_chains.py -q   # 单文件
python -m pytest test/ -k chain -q            # 按名字筛
```

（不要在项目根直接敲 `pytest`，那会去收集 `core/`；显式给 `test/`。）

## 文件清单

| 文件 | 覆盖对象 | 说明 |
|---|---|---|
| `conftest.py` | — | 临时 config / cwd / 假 LLM 客户端等共享夹具 |
| `test_llm_chains.py` | `core/utils/llm_profiles.py` | **链机制**：三表解析、任务→链绑定、链 ID 格式、key 池与死 key |
| `test_ask_gpt_chain.py` | `core/utils/ask_gpt.py` | **走链行为**：重试→换模型、401 换 key、缓存、JSON 修复 |
| `test_translate_service.py` | `core/services/translate_service.py` | 第 4 步：分块规则、断点续跑、术语库、共享上下文 |
| `test_words_store.py` | `core/utils/words_store.py` | 第 2 步：词级存储（JSON 主存 / xlsx 导出 / 优先级） |
| `test_asr_correct.py` | `core/asr_correct.py` | 第 1→2 步纠错：实体统一、词形对齐、热词标记、句子重建、回写、断点 |
| `test_translate_lines.py` | `core/translate_lines.py` | 第 4 步：译文校验 + 走真实链路的贯通用例 |
| `test_subtitle_time.py` | `core/utils/subtitle_time.py` | 字幕对齐：完整文本顺序匹配/重复句不回头、拆分缩写无漂移、合并词覆盖空行、标点不吃词、毫秒四舍五入；锚点计数仅作容错 |
| `test_progress.py` | `core/utils/progress.py` | `ChunkProgress`：tty 进度条 / 非 tty 计数行 |
| `test_input_api.py` | `server/input_api.py` + `server/task_manager.py` | 输入源端点：URL/分辨率校验、已有输入 409、删除边界；`start_job` 一次性任务 |
| `test_asr_vram_backoff.py` | `core/asr_backend/whisperX_local.py` | 显存回退：VAD batch 按空闲显存取值、改写 whisperx VAD、异常布局不抛 |
| `test_asr_policy.py` | `core/services/asr_service.py` | 无叙述提示词/无热词默认、原版重复策略、显式模式与断点参数变化 |
| `test_split_sub_integrity.py` | `core/_5_split_sub.py` | 切分保真：非空目标段数量校验、源文有丢失不请求译文分配且保留原行 |
| `test_pipeline_run.py` | `server/app.py` + `server/task_manager.py` | 强制重跑：force 才调 force_clean、范围闭区间、阶段一烧录开/关/纯音频、忙碌/输入无效不清理、准备与启动互斥、清理可见日志、400/404/409；日志落盘（写文件、归档上次、轮换、job 的 print 进文件） |
| `test_ytdlp_cookies.py` | `core/_1_ytdlp.py` | 第 1 步下载：cookie 登录态检测、浏览器直取、bot 挑战可读化、JS 运行时选择（不联网，用假 yt_dlp） |
| `test_subprocess_log.py` | `core/utils/subprocess_log.py` | 子进程输出转发：stdout+stderr 都进 print、进度行节流（末条必打）且不受行数上限、超限省略、`check=True` 抛错、`cwd` 透传 |
| `test_vad_gap_fill.py` | `core/asr_backend/vad_gap_fill.py` | VAD补转：NoVad与时间坐标、词级覆盖/显式范围首尾、空结果、内部词空洞整段替换、options/VAD还原、非空但不完整时一次兜底、停顿较多仍验收、未解决告警、提示词回声保守替换、静音不重试；检测用固定输出，不依赖历史output音频 |
| `test_log_hooks.py` | `core/utils/log_hooks.py` + `server/logbus.py` | 全局日志出口（FR-31）：markup 纯文本化、sink 幂等/异常隔离、任务线程防重、共享 append 落盘、startup 注册、接口级裁剪日志上送 |
| `test_ad_slots.py` | `core/services/ad_service.py` + `ad_cut_service.py` + `server/ad_api.py` | 广告位/切点（FR-28/30/32）：SRT 行号锚点、画面切点定点、补集裁剪、编码参数注入、手动切点、**删除识别段 / 清空 EDL / dismissed 留档与恢复**、接口白名单 |
| `test_server_disconnect.py` | `server/app.py` | 预览 Range 断连（FR-32）：只吞 `_call_connection_lost` 的连接重置类噪音，其它异常照常上报、安装幂等 |

## 隔离手法（写新用例时照抄）

- **配置**：`cfg_path(yaml_text)` 夹具写一份临时 `config.yaml`，并改写
  `config_utils.CONFIG_PATH`（`load_key()` 在函数体内读全局常量，所以生效）。
- **相对路径**：用 `tmp_cwd` 夹具 `chdir` 到临时目录。`output/log/...`、
  `output/gpt_log/...`、`output/key_state.json` 都是相对 cwd 的。
- **绝对路径常量**：`monkeypatch.setattr(translate_service, "TERMS_DIR", ...)`、
  `monkeypatch.setattr(llm_profiles, "KEY_STATE_FILE", ...)`。
- **网络**：`fake_llm([...])` 替换 `net_policy.make_openai_client`，脚本按调用
  顺序逐个返回响应或抛异常；`calls` 记录每次 `(model, messages)`
  与 `("__key__", api_key)`，用于断言"确实用了这条链的这个模型/这个 key"。

## 假 LLM 速查

```python
calls, script = fake_llm([resp_ok("hello")])        # 正常响应
calls, script = fake_llm([status_error(401)])       # HTTP 状态码错误（走死 key 逻辑）
calls, script = fake_llm([status_error(500), resp_ok("ok")])   # 瞬时失败后成功
```

配合配置里的 `llm.tasks` 绑定，就能精确断言某一步用了哪条链：

```python
assert [m for tag, m in calls if tag != "__key__"] == ["gpt-big", "gpt-mini"]
```

## 覆盖范围（边界说清楚，别以为绿了就万事大吉）

**已覆盖**（纯逻辑、离线、2 秒跑完）：

- **链机制全链路**：配置解析（v2 dict / v1 裸列表 / v0 内联三种格式、未知模型/链报错、default 回退）→ 任务→链绑定 → profile 序列 → `ask_gpt` 走链（本 profile 重试→换下一个模型、401/402/403 换同端点下一个 key、池尽才换模型、死 key 永久标记、缓存命中不发请求、JSON 修复与列表兜底）
- **key 池**：`label----key` 解析、脱敏 id（首8..尾4）、标记/过滤/单端点或全量重置
- **第 4 步**：分块规则（字符+句数双上限、超长句独立成块、CRLF/blob 归一化）、断点续跑（原子写、索引锚点、文件损坏兜底）、术语库（多套按序合并、同 src 后者覆盖并告警、无 src 丢弃、缺失库跳过）、共享上下文（只有裸内容无表头、渲染必须走模板）
- **第 2 步词级存储**：JSON 无损往返（毫秒时间戳）、xlsx 兼容导出（引号/列序）、JSON 优先 → xlsx 兜底 → 空 DataFrame
- **第 1→2 步纠错**：实体统一的三道护栏（同键/复数/短词）、词形对齐（大小写+标点尾巴）、热词只标记不改写、句子重建（标点尾巴/60 词硬上限）、回写（索引锚定、行数与时间戳不变、锚点失效丢弃）、断点指纹失配即作废、零调用统计口径
- **译文校验 + 贯通用例**：`translate_lines` 真实走 `translate_faith` → 链 → 模型，并断言重试时 prompt 带 `<!-- attempt N/3 -->`
- **字幕时间轴对齐**：`get_sentence_timestamps` 按词行投影（标点/大小写/纠错免疫）、合并词连同被吞的空行一起消费（时间戳覆盖整段音频）、词行耗尽时降级 clamp 而非硬抛
- **输入源 / 下载端点**：URL 前缀与分辨率白名单校验、已有输入时 409 拒绝（避免下完撞 `not unique`）、删除输入只作用于 `output/` 下相对路径、`TaskManager.start_job` 的完成/拒绝/异常三种终态

**没覆盖**（明确不在本次范围，或属于集成测试）：

- 步骤 5–8：字幕切分/合成/烧录、音频对齐、TTS、合成视频（依赖 ffmpeg 与真实素材）
- 真实 ASR（whisper / whisperX / faster-whisper）、spacy 依存句法（3-1）、pyannote（需要模型权重与音频）
- `server/*.py` 的 HTTP 接口层——**仅 `input_api`（输入源/下载）已覆盖**，其余（pipeline 控制、settings、models、batch、translate）仍无用例；前端 TS/React
- `prompts.py` 的 `render_prompt` / `_parse_template`（栈式占位符渲染）——纯函数，很好测，**建议下一个补它**
- `run_service.resolve_steps` / `force_clean`、`pipeline.py` 的步骤注册表与 markers 一致性——**建议补**（"步骤内守卫文件与 markers 必须一致"这条铁律目前靠人工）
- `net_policy` 的代理模式探测（涉及真实网络）

## 改了东西，该跑什么

| 改动位置 | 必跑 |
|---|---|
| `core/**.py` | `python -m pytest test/ -q`（约 2 秒，改完就跑） |
| 链 / key / 401 决策相关 | 上面那条 + `node check_single_source.js`（决策字面量唯一归属） |
| `server/input_api.py`、`server/task_manager.py` | `python -m pytest test/test_input_api.py -q`（需环境装有 fastapi + httpx） |
| `server/app.py`（`/api/pipeline/run`） | `python -m pytest test/test_pipeline_run.py -q`（force 语义：清产物→推日志→启动） |
| `core/asr_backend/whisperX_local.py` | `python -m pytest test/test_asr_vram_backoff.py -q`（需 torch/whisperx/pyannote；缺依赖时整文件 skip） |
| 其他 `server/*.py` | 无接口层用例；跑 pytest 后仍需重启服务手验接口 |
| `frontend/` | `cd frontend && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/vite build` |
| `prompts.py` | 建议先补 `render_prompt` 用例再跑 |

注意：**测试绿 ≠ 功能对**。这套用例不碰真实 API 与音视频，涉及真实链路（换模型、改提示词、调分块参数）的改动，仍需实跑一次确认。

## 新增用例的注意点

- 不要靠真实模型：任何要发请求的路径都必须先装 `fake_llm`。
- 不要 `time.sleep`：瞬时失败会退避，`test_ask_gpt_chain.py` 里有 `_no_sleep`
  自动夹具，新文件需要的话照抄一个。
- 改动了源码里的决策逻辑（401/402/403、死 key 标记、步骤清理）后，跑一次
  `node check_single_source.js`，决策字面量只允许出现在唯一归属文件里。
