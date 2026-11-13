// 术语 & 提示词 & 切分实验场 API 客户端

export interface TermSetInfo { name: string; desc: string; count: number }
export interface Term { src: string; tgt: string; note: string }
export interface JobTermsReview {
  available: boolean          // 4.1 是否真的产出了任务术语表（false = 不能把底板当新抽）
  terms: Term[]
  plate_srcs: string[]        // 当前勾选底板里的 src（已 lowercase），用于标记来源
  sets: string[]              // 已有基础库名，回写目标下拉用
}
export interface WriteBackResult {
  ok: boolean; target: string; added: number; updated: number; skipped: number; total: number
}
/** 一个术语在所有基础库里的各自取值（展开行用） */
export interface TermVariant {
  set: string; src: string; tgt: string; note: string; active: boolean
}
/** 「全部术语」总表的一行：一个唯一 src（大小写不敏感）+ 4.2 实际生效值 */
export interface TermCatalogRow {
  key: string; src: string; tgt: string; note: string
  sets: string[]; variants: TermVariant[]
  effective: boolean       // 4.2 会不会把它注入 prompt
  in_plate: boolean        // 在当前勾选底板里
  in_job: boolean          // 在当前任务术语表里
  job_fresh: boolean       // 只在任务术语表（4.1 新抽，尚未回写）
  conflict: boolean        // 多套基础库译文不一致
  drift: boolean           // 4.1 冻结值与当前所有基础库取值都不同
}
/** 一个来源文件的完整词条清单（不跨库折叠）。并集总表会把同一个 src
 * 合并成一行，看不全某一套基础库，所以需要按来源逐份完整展示。 */
export interface TermSource {
  id: string; kind: 'base' | 'job'; name: string; path: string; label: string
  active: boolean; available: boolean; count: number; terms: Term[]
}
export interface TermCatalog {
  rows: TermCatalogRow[]
  sources: TermSource[]
  stats: {
    total: number; sets: number; active: number; base_entries: number
    job_available: boolean; job_count: number
    effective: number; job_fresh: number; conflicts: number
  }
}

export interface TermSetsResponse {
  sets: TermSetInfo[]; active: string[]; job: JobTermsReview; catalog: TermCatalog
}

export async function getTermSets(): Promise<TermSetsResponse> {
  const r = await fetch('/api/translate/terms')
  if (!r.ok) throw new Error(`GET /terms ${r.status}`)
  return r.json()
}

export async function getTermSet(name: string): Promise<{ desc: string; terms: Term[] }> {
  const r = await fetch(`/api/translate/terms/${encodeURIComponent(name)}`)
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `GET ${name} ${r.status}`)
  return r.json()
}

export async function saveTermSet(name: string, desc: string, terms: Term[],
  create = false, overwrite = false,
): Promise<{ ok: boolean; name: string; count: number; created: boolean }> {
  const r = await fetch('/api/translate/terms', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, desc, terms, create, overwrite }),
  })
  if (!r.ok) throw new Error(errDetail(await r.json().catch(() => ({}))) || '保存失败')
  return r.json()
}

export async function deleteTermSet(name: string): Promise<void> {
  const r = await fetch('/api/translate/terms', {
    method: 'DELETE', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || '删除失败')
}

export async function setActiveTermSets(active: string[]): Promise<void> {
  const r = await fetch('/api/translate/terms/active', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ active }),
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || '激活失败')
}

/** 勾选任务术语表的词条，回写进指定基础库（可新建）。只传 src，译文由服务端重读。 */
export async function writeBackTerms(body: {
  target: string; srcs: string[]; overwrite?: boolean
}): Promise<WriteBackResult> {
  const r = await fetch('/api/translate/terms/writeback', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(errDetail(await r.json().catch(() => ({}))) || '回写失败')
  return r.json()
}

// ---------------- prompts ----------------

export interface PromptInfo {
  name: string; desc: string; overridden: boolean; content: string
}

export interface LanguageDefaults { src_lang: string; tgt_lang: string }

export async function getPrompts(): Promise<{ prompts: PromptInfo[]; language_defaults: LanguageDefaults }> {
  const r = await fetch('/api/translate/prompts')
  if (!r.ok) throw new Error(`GET /prompts ${r.status}`)
  return r.json()
}

export async function savePrompt(name: string, content: string): Promise<void> {
  const r = await fetch('/api/translate/prompts', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, content }),
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || '保存失败')
}

export async function resetPrompt(name: string): Promise<void> {
  const r = await fetch('/api/translate/prompts', {
    method: 'DELETE', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || '恢复失败')
}

export async function previewPrompt(body: {
  name: string; sample: string; content?: string
  src_lang?: string; tgt_lang?: string
}): Promise<{ name: string; prompt: string; language_defaults: LanguageDefaults }> {
  const r = await fetch('/api/translate/prompts/preview', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || '预览失败')
  return r.json()
}

// ---------------- call log (traceability) ----------------

export interface CallLogEntry {
  ts: string; task: string; profile?: string; model?: string
  base_url?: string; key?: string; ok: boolean; cached?: boolean; chain?: string
  error?: string; in_chars?: number; out_chars?: number; secs?: number
}

export async function getCallLog(params: {
  task?: string; limit?: number; only_fail?: boolean
}): Promise<{ calls: CallLogEntry[]; total: number }> {
  const q = new URLSearchParams()
  if (params.task) q.set('task', params.task)
  if (params.limit) q.set('limit', String(params.limit))
  if (params.only_fail) q.set('only_fail', 'true')
  const r = await fetch(`/api/translate/calls?${q.toString()}`)
  if (!r.ok) throw new Error(`GET /calls ${r.status}`)
  return r.json()
}

// ---------------- translation verification (L4 QA) ----------------

export interface VerifyIssue { line?: number; type?: string; detail?: string }

export interface VerifyResult {
  ok: boolean; score?: number; issues?: VerifyIssue[]
  suggestion?: string; error?: string; cached?: boolean
}

export async function verifyTranslation(body: {
  source: string; translation: string; use_cache?: boolean
}): Promise<VerifyResult> {
  const r = await fetch('/api/translate/verify', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || '校验失败')
  return r.json()
}

// ---------------- split playground ----------------

export interface PreviewChunk { index: number; chars: number; sentences: number; prompt_chars: number; preview: string }

export interface PreviewResult {
  chunks: PreviewChunk[]
  stats: { count: number; chars: { total: number; min: number; max: number; avg: number }; sentences: { total: number; min: number; max: number } }
  params: { chunk_size: number; max_sentences: number }
  estimate: {
    llm_calls: number; calls_per_chunk: number; reflect: boolean; active_term_sets: string[]
    terms_count: number  // 任务术语表条数（勾选底板 + 4.1 新抽），缺失时回退底板
    prompt_total_chars: number; prompt_avg_chars: number; prompt_max_chars: number; context_chars: number
  }
}

export async function previewSplit(body: {
  text?: string; use_split_file?: boolean; chunk_size?: number; max_sentences?: number
}, signal?: AbortSignal): Promise<PreviewResult> {
  const r = await fetch('/api/translate/preview', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body), signal,
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || '预览失败')
  return r.json()
}

export interface ProbeChunkResult {
  index: number; ok: boolean; line_count_src: number; line_count_trans: number
  error: string | null; translation: string | null; source: string | null
}

export interface ProbeResult {
  results: ProbeChunkResult[]
  summary: {
    probed: number; ok: number; failed: number; chunk_total: number
    params: { chunk_size: number; max_sentences: number; reflect: boolean }
  }
}

// probe a SINGLE chunk — for progressive per-chunk requests in the UI
export async function probeOneChunk(body: {
  text?: string; use_split_file?: boolean; chunk_size?: number; max_sentences?: number
  chunk_index: number; reflect?: boolean; use_cache?: boolean
}, signal?: AbortSignal): Promise<ProbeResult> {
  const r = await fetch('/api/translate/probe', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, chunk_indices: [body.chunk_index] }), signal,
  })
  if (!r.ok) throw new Error(errDetail(await r.json().catch(() => ({}))) || '实测失败')
  return r.json()
}

/** FastAPI's `detail` can be a string OR a validation-error array — render
 * both readably instead of "[object Object]". */
function errDetail(j: { detail?: unknown }): string {
  const d = j.detail
  if (typeof d === 'string') return d
  try { return d ? JSON.stringify(d) : '' } catch { return '' }
}

export async function probeChunks(body: {
  text?: string; use_split_file?: boolean; chunk_size?: number; max_sentences?: number
  chunk_indices: number[]; reflect?: boolean
}, signal?: AbortSignal): Promise<ProbeResult> {
  const r = await fetch('/api/translate/probe', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body), signal,
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || '实测失败')
  return r.json()
}
