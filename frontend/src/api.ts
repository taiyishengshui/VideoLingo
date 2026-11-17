export interface StepStatus {
  id: string
  no: number               // 全局固定序号，与源码 _2…_12 一致（音频模式也不变）
  title: string
  stage: 'sub' | 'dub'
  done: boolean
  markers: string[]
  logs: string[]
  llm_titles: string[]
  last_seconds?: number | null  // 上一次实跑耗时（timing_service，秒）
  last_status?: string | null
  last_started_at?: string | null  // 上一次实跑开始时间（timing_service，YYYY-MM-DD HH:MM:SS）
  last_ended_at?: string | null    // 上一次实跑结束时间
  run_count?: number
}

export type TaskState = 'idle' | 'running' | 'paused' | 'stopped' | 'completed' | 'error'

/** 两个步骤下拉的默认值：开始固定转录，结束由「烧录字幕到视频」开关决定 */
export interface RunRange {
  from_step: string
  to_step: string
}

export interface PipelineStatus {
  state: TaskState
  queue: string[]
  current_step: string | null
  error: string
  failed_step: string | null   // 崩溃时具体失败的步骤 id（v1.32）
  stopped_step: string | null  // 用户中止时具体步骤 id（v1.32）
  steps: StepStatus[]
  log_file?: string | null   // 本次运行的落盘日志（相对项目根）
  defaults?: RunRange
  version?: string
}

/** 后端版本号 + git 构建标识（单一来源：项目根 version.py，FR-37） */
export interface BuildInfo {
  hash: string
  committed_at: string
  dirty: boolean
}

export interface VersionInfo {
  version: string
  build?: BuildInfo
}

export async function getVersion(): Promise<VersionInfo> {
  const r = await fetch('/api/version')
  if (!r.ok) throw new Error(`GET /api/version ${r.status}`)
  const data = await r.json()
  return { version: data.version ?? '', build: data.build }
}

/** 读取 output/ 下的日志文件内容（后端只允许 output/，不会越权） */
export async function tailLog(path: string, lines = 20000): Promise<string[]> {
  const r = await fetch(`/api/logs/tail?path=${encodeURIComponent(path)}&lines=${lines}`)
  if (!r.ok) throw new Error(`GET /api/logs/tail ${r.status}`)
  return (await r.json()).lines ?? []
}

export async function getPipeline(): Promise<PipelineStatus> {
  const r = await fetch('/api/pipeline')
  if (!r.ok) throw new Error(`GET /api/pipeline ${r.status}`)
  return r.json()
}

export interface StepTiming {
  id: string
  no: number
  title: string
  last: number
  last_status: string
  best: number
  count: number
  total: number
}

export interface TimingsReport {
  by_step: Record<string, StepTiming>
  updated_at: string
  runs: number
  report: string
  dub: Record<string, number | string | null>
}

export async function getTimings(): Promise<TimingsReport> {
  const r = await fetch('/api/pipeline/timings')
  if (!r.ok) throw new Error(`GET /api/pipeline/timings ${r.status}`)
  return r.json()
}

export interface RunPipelineBody {
  step_id?: string
  from_step?: string   // 起始步骤（含）
  to_step?: string     // 结束步骤（含）；省略 = 跑到最后
  force?: boolean
}

export async function runPipeline(body: RunPipelineBody): Promise<void> {
  const r = await fetch('/api/pipeline/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `run failed: ${r.status}`)
}

export async function control(action: 'pause' | 'resume' | 'stop'): Promise<void> {
  const r = await fetch(`/api/pipeline/${action}`, { method: 'POST' })
  if (!r.ok) throw new Error(`${action} failed: ${r.status}`)
}

/* ---------------- input media (download / select) ---------------- */

export interface InputInfo {
  path: string | null
  type: 'video' | 'audio' | null
  exists: boolean
  ambiguous: boolean
  manifest: boolean
  video: string | null
  audio: string | null
  message: string | null
}

export interface CookieStatus {
  cookies_path: string | null
  cookies_file_exists: boolean
  cookies_have_login: boolean
  browser: string | null
  browser_ok: boolean | null
  browser_error: string | null
  browser_have_login: boolean | null
  verdict: 'ok' | 'anonymous' | 'browser-unavailable' | 'unknown'
  hint: string | null
}

async function unwrap<T>(r: Response, what: string): Promise<T> {
  if (!r.ok) {
    const detail = await r.json().catch(() => ({} as { detail?: string }))
    throw new Error(detail.detail || `${what} failed: ${r.status}`)
  }
  return r.json()
}

export async function getInput(): Promise<InputInfo> {
  return unwrap<InputInfo>(await fetch('/api/input/current'), 'GET /api/input/current')
}

export async function getCookieStatus(): Promise<CookieStatus> {
  return unwrap<CookieStatus>(await fetch('/api/input/cookies'), 'GET /api/input/cookies')
}

export async function downloadVideo(
  url: string,
  resolution: string,
): Promise<{ ok: boolean; message: string }> {
  return unwrap(
    await fetch('/api/input/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, resolution }),
    }),
    'download',
  )
}

export async function clearInput(removeFile = false): Promise<{ removed: string[] }> {
  return unwrap(
    await fetch(`/api/input?remove_file=${removeFile}`, { method: 'DELETE' }),
    'clear input',
  )
}

/** 归档 / force 重跑前主动关闭后端的视频预览读句柄。
 *  Windows 上只要有一个 Range 读句柄没关，output_sub.mp4 就删不掉；
 *  后端 job 里还会兜底再关一次，所以这里失败不阻断主流程。 */
export async function releasePreviewStreams(): Promise<number> {
  try {
    const r = await fetch('/api/ad/preview/release', { method: 'POST' })
    if (!r.ok) return 0
    const data = await r.json() as { released?: number }
    return data.released ?? 0
  } catch {
    return 0
  }
}

/** 把 output/ 归档到 history/<视频名>/（异步任务，进度走 WebSocket 日志） */
export async function saveHistory(name?: string): Promise<{ ok: boolean; message: string }> {
  return unwrap(
    await fetch('/api/history/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(name ? { name } : {}),
    }),
    'save history',
  )
}

// ---------------- B站投稿文案 ----------------

/** 一手素材（视频转录）的状态统计，不含全文 */
export interface BiliTranscript {
  available: boolean
  source: string
  label: string
  lang: string
  sampled: boolean
  chars: number
  total_chars: number
  lines: number
  duration: number
  samples: number
  budget: number
  has_summary: boolean
}

export interface BiliContext {
  title: string
  description: string
  uploader: string
  source: 'downloaded' | 'filename' | 'none'
  speaker_suggest: string
  meta_file: string
  transcript: BiliTranscript
  /** 当前 output/bili_meta.md 里上次生成的三件套；无文件或损坏则 null（FR-27 刷新回显） */
  existing: BiliMetaResult | null
}

export interface BiliMetaResult {
  titles: string[]
  desc: string
  tags: string[]
  ep_tag: string
  speaker: string
  video_title: string
  saved_to: string
  /** 仅回显结果带：md 里记的生成时间 */
  generated_at?: string
  transcript?: BiliTranscript
}

export async function getBiliContext(): Promise<BiliContext> {
  const r = await fetch('/api/bili/context')
  if (!r.ok) throw new Error(`GET /api/bili/context ${r.status}`)
  return r.json()
}

/* ---------------- 广告位提醒（FR-28） ---------------- */

export interface AdTranscript {
  available: boolean
  source: string
  label: string
  lines: number
  chars: number
  duration: number
  reason?: string
}

export interface AdSegment {
  id: number
  brand: string
  note: string
  confidence: string
  enabled: boolean
  start: number
  end: number
  transcript_start: number
  transcript_end: number
  prev_end: number | null
  next_start: number | null
  start_line: number
  end_line: number
  start_gap: number
  start_basis: string
  end_basis: string
  scene_cuts: number[]
}

export interface AdEdl {
  meta: Record<string, unknown>
  segments: AdSegment[]
  /** 用户手动删掉的识别段：重新识别时不会自动加回，可点「恢复已删除」加回 */
  dismissed?: AdSegment[]
  sponsorblock?: { start: number; end: number; category: string; votes: number }[]
  edited_at?: string
}

export interface AdContext {
  transcript: AdTranscript
  video: string
  audio: string
  boundary_ready: boolean
  scene_cut_ready: boolean
  config: Record<string, string | number | boolean>
  has_edl: boolean
  edl: AdEdl | null
  sponsorblock: { start: number; end: number; category: string; votes: number }[]
  /** 每项 subtitled 表示画面里已烧进字幕；default_source 由后端决策（优先烧录版） */
  sources: { id: string; label: string; path: string; size: number; subtitled: boolean; version?: string }[]
  default_source: string | null
}

export interface AdDetectResult {
  segments: AdSegment[]
  meta: Record<string, unknown>
  saved_to: string
  edl_to: string
  sponsorblock: AdEdl['sponsorblock']
  dismissed?: AdSegment[]
}

export async function getAdContext(): Promise<AdContext> {
  const r = await fetch('/api/ad/context')
  if (!r.ok) throw new Error(`GET /api/ad/context ${r.status}`)
  return r.json()
}

/** 一次 LLM 调用 + 本地 ffmpeg 切点检测，走 ad_detect → default 任务链 */
export async function detectAds(extraHint = ''): Promise<AdDetectResult> {
  const r = await fetch('/api/ad/detect', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ extra_hint: extraHint }),
  })
  if (!r.ok) {
    const d = await r.json().catch(() => ({}))
    throw new Error(d.detail || `detect failed: ${r.status}`)
  }
  return (await r.json()).result
}

export interface AdCutResult {
  saved_to: string
  source: string
  source_id: string
  duration: number
  removed: number
  keep: { start: number; end: number }[]
  elapsed: number
  edl_file: string
}

/** File version refreshes media buffers after rebuilding the same filename. */
export function adPreviewUrl(source: string, version?: string): string {
  const params = new URLSearchParams({ source })
  if (version) params.set('v', version)
  return `/api/ad/preview?${params}`
}

/** 预览源的真实 fps / 时长 / 编码参数（逐帧步进 + 手动切到片尾） */
export interface AdMediaInfo {
  source: string
  fps: number
  duration: number
  width: number
  height: number
  pix_fmt: string
  fps_raw: string
  video_codec: string
  audio_codec: string
  video_bitrate: number
  audio_bitrate: number
  format_bitrate: number
  has_video: boolean
  has_audio: boolean
}

export async function getAdMediaInfo(source: string, version?: string): Promise<AdMediaInfo | null> {
  // v=<mtime_ns-size>：同名文件重烧后不能复用浏览器缓存的旧 fps / 时长
  const params = new URLSearchParams({ source })
  if (version) params.set('v', version)
  const r = await fetch(`/api/ad/media-info?${params}`)
  if (!r.ok) return null
  return r.json().catch(() => null)
}

/** 预览微调确认后，按当前区间裁剪并拼接成无广告成片 */
export async function cutVideo(source: string, segments: AdSegment[]): Promise<AdCutResult> {
  const r = await fetch('/api/ad/cut', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source, segments }),
  })
  if (!r.ok) {
    const d = await r.json().catch(() => ({}))
    throw new Error(d.detail || `cut failed: ${r.status}`)
  }
  return (await r.json()).result
}

/** 页面手工改起止秒 / 勾选 / 删除后回写 EDL。
 *  segments 允许为空（清空全部切点）；dismissed 显式传 [] = 恢复已删除的识别段。 */
export async function saveAdSegments(
  segments: AdSegment[],
  dismissed?: AdSegment[],
): Promise<AdEdl> {
  const body: { segments: AdSegment[]; dismissed?: AdSegment[] } = { segments }
  if (dismissed !== undefined) body.dismissed = dismissed
  const r = await fetch('/api/ad/edl', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) {
    const d = await r.json().catch(() => ({}))
    throw new Error(d.detail || `save failed: ${r.status}`)
  }
  return (await r.json()).edl
}

/** 生成三件套（一次 LLM 调用，同步返回，走 bili_meta → default 任务链） */
export async function generateBiliMeta(body: {
  ep: number
  speaker?: string
  extra_hint?: string
  title?: string
  description?: string
}): Promise<BiliMetaResult> {
  const r = await fetch('/api/bili/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) {
    const d = await r.json().catch(() => ({}))
    throw new Error(d.detail || `generate failed: ${r.status}`)
  }
  return (await r.json()).result
}
