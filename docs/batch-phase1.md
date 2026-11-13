# 批量处理 + 镜像工作区 + 步骤范围重跑（期1交付）

日期：2026-09-08

## 功能总览

用户选定一个根目录（含子目录中的视频），系统在**同级**生成镜像日志目录，
每个视频独立工作区、独立 job_state.json，支持断点续跑、步骤范围重跑、
完成后自动回送双语字幕。

```
D:\视频\
├── a\
│   ├── b.mp4
│   ├── b_trans_src.srt        ← 完成后回送（中文在上）
│   └── a1\
│       ├── 001.mp4
│       └── 001_trans_src.srt  ← 完成后回送
└── a_log\                     ← 与 a 同级的镜像工作区
    ├── b\                     ← b.mp4 的全部产物
    │   ├── job_state.json     ← 每视频一个，原子写入
    │   ├── output\...         ← 流水线全部产物（cwd=工作区）
    │   └── ...
    └── a1\
        └── 001\               ← 目录结构镜像原目录
```

## 新增文件

| 文件 | 职责 |
|---|---|
| `core/utils/job_context.py` | 镜像路径推导、JobState（原子读写/指纹校验/步骤重置/marker清理）、Job（工作区准备/manifest/交付回送） |
| `core/batch_engine.py` | 批量引擎：扫描跳过 `*_log`、逐文件顺序跑、subprocess 启动、日志转发、stop 杀子进程 |
| `core/job_runner.py` | 子进程入口：cwd=工作区，按注册表顺序跑步骤，stdout 输出 JSON 步骤事件 |
| `server/batch_api.py` | REST：/api/batch/scan·start·stop·status·log |
| `frontend/src/batchApi.ts` | 前端 API 客户端 |
| `frontend/src/pages/BatchPage.tsx` | 批量处理页：目录扫描、勾选、进度点阵、单行重跑（整个重来/步骤范围）、全部重跑、重跑失败的、实时日志 |

## 修改文件

| 文件 | 改动 |
|---|---|
| `core/utils/config_utils.py` | config.yaml 锚定项目根（cwd 无关），支持 VL_CONFIG 覆盖 |
| `core/asr_backend/whisperX_local.py` | model_dir 相对路径锚定项目根 |
| `core/_4_1_summarize.py` | custom_terms.xlsx 锚定项目根 |
| `core/utils/media_files.py` | find_video_files/find_audio_files 先查 manifest（批量模式视频不在工作区） |
| `server/app.py` | 挂载 batch router |
| `frontend/src/App.tsx` | 新增「批量处理」导航 |
| `frontend/src/ws.ts` | BusEvent 扩展批量事件类型 |

## 核心机制

**零改动复用 30+ 步骤文件**：所有步骤用 `output/...` 相对路径 →
子进程 cwd=工作区 → 产物天然落进 `a_log/a1/001/output/...`。

**断点续跑**：job_state.json 原子写（tmp+rename），重启后从第一个非 done 步骤继续。
输入指纹（size+mtime）不匹配 → 自动从头重跑。

**步骤范围重跑**：reset_steps(m,n) 把 m..n 标 pending 并删除这些步骤的
marker 产物（防 @check_file_exists 静默跳过），之前的步骤产物复用。
场景：换翻译模型 → 4→末尾；换切句配置 → 3→末尾。

**停止即杀**：stop() terminate 子进程，当前步骤保持非 done，续跑从断点继续。

## 已知限制（期2/期3）

- 串行处理（一个文件跑完再下一个）；三车道并行（GPU/API/本地）为期2
- UI 进度矩阵刷新有 800ms 节流
- 产物页/模型页仍是占位
- Windows 下若视频路径含 emoji，os.walk 正常但工作区目录名会替换为 `_`
