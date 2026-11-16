export interface VoiceRow {
  number: number
  start_time: string
  end_time: string
  duration: number
  text: string
  origin: string
  speaker: string | null
  confidence: number
  flags: string[]
  manual: boolean
  n_chunks: number
  mixed: boolean
  has_ref: boolean
  has_dub: boolean
  ref_url: string | null
  dub_url: string | null
}

export interface VoiceChunk {
  index: number
  text: string
  time: [number, number]
  speaker: string | null
  confidence: number
  flags: string[]
  manual: boolean
}

export interface VoiceChunkGroup {
  rows: VoiceChunk[]
  source: 'srt' | 'split'
  mixed: boolean
  split?: boolean
}

export interface VoiceSpeaker {
  name: string
  n_clips: number
  ref_duration: number | null
  ref_source: number | null
  confidence: number | null
  ref_manual: boolean
  ref_url: string | null
  ref_rel: string | null
}

export interface VoiceMap {
  status: 'ok' | 'stale' | 'missing'
  reasons: string[]
  version?: number
  producer?: string
  expected_producer?: string
  fingerprint?: string
  created_at?: string
  n_speakers?: number
  silhouette?: number
  degraded?: boolean
  diagnostics?: Record<string, unknown>
  task_rows?: number
  overrides: Record<string, string>
  seeds?: Record<string, number[]>
  counts: Record<string, number>
  speakers: VoiceSpeaker[]
  rows: VoiceRow[]
  n_mixed?: number
  chunks?: Record<string, VoiceChunkGroup>
  chunk_built_at?: string | null
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init)
  if (!r.ok) {
    const d = await r.json().catch(() => ({}))
    throw new Error(d.detail || `${url} ${r.status}`)
  }
  return r.json()
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
}

export const getVoiceMap = (): Promise<VoiceMap> => req<VoiceMap>('/api/voice/map')

export const putSegments = (body: {
  changes?: Record<string, string>
  marks?: Record<string, boolean>
  unassign?: number[]
}): Promise<{ ok: boolean }> => req('/api/voice/segments', json('PUT', body))

export const postRebuild = (clearOverrides = false): Promise<{ ok: boolean; n_speakers: number }> =>
  req('/api/voice/rebuild', json('POST', { force: true, clear_overrides: clearOverrides }))

export const postRevoice = (numbers: number[]): Promise<{ count: number; hint: string }> =>
  req('/api/voice/revoice', json('POST', { numbers }))

export interface PropagateStat {
  seeds: number
  embedded: number
  assigned: number
  low_margin: number
  smoothed: number
  protected: number
  counts: Record<string, number>
}

export const postPropagate = (
  seeds: Record<string, number[]>, protectManual = false,
): Promise<{ ok: boolean; stat: PropagateStat }> =>
  req('/api/voice/propagate', json('POST', { seeds, protect_manual: protectManual }))

export const putChunks = (
  changes: Record<string, Record<string, string>>,
): Promise<{ ok: boolean; changed: number; hint: string }> =>
  req('/api/voice/chunks', json('PUT', { changes }))

export interface ChunkStat {
  rows: number
  chunks: number
  assigned: number
  kept_manual: number
  fallback: number
  low_margin: number
  skipped_manual?: number
  auto_split?: number
  auto_skipped?: Record<string, string>
  source: Record<string, number>
  counts?: Record<string, number>
}

export interface SplitStat {
  split: number[]
  skipped: Record<string, string>
  at: Record<string, number>
  chunks?: number
  hint?: string
}

/** 不传 numbers = 全片混句重建；传了 = 只重建这些行（单行也会尝试自动拆分）。 */
export const postRebuildChunks = (
  numbers?: number[], force = false,
): Promise<{ ok: boolean; stat: ChunkStat }> =>
  req('/api/voice/chunks/rebuild', json('POST', { numbers: numbers ?? null, force }))

/** {number: 切点秒数 | null}；null 让 CAMPPlus 自动找说话人切换点。 */
export const postSplitChunks = (
  changes: Record<string, number | null>,
): Promise<{ ok: boolean; stat: SplitStat }> =>
  req('/api/voice/chunks/split', json('POST', { changes }))

export const postReload = (): Promise<{ ok: boolean }> =>
  req('/api/voice/reload', { method: 'POST' })

export const postSpeaker = (name: string, number: number): Promise<{ ok: boolean }> =>
  req('/api/voice/speakers', json('POST', { name, number }))

export const putSpeakerRef = (name: string, number: number): Promise<{ ok: boolean }> =>
  req('/api/voice/speakers/ref', json('PUT', { name, number }))

export const deleteSpeaker = (name: string): Promise<{ ok: boolean }> =>
  req(`/api/voice/speakers/${encodeURIComponent(name)}`, { method: 'DELETE' })

export const FLAG_LABELS: Record<string, string> = {
  low_margin: '音色接近',
  turn_boundary: '话轮边界',
  smoothed: '平滑改动',
  short: '过短',
  silent: '无原声',
  marked: '人工标记',
  propagated: '种子传播',
  mixed: '双人混句',
}

export function isAmbiguous(row: VoiceRow): boolean {
  // "propagated" marks machine-assigned lines; "mixed" is a category with its
  // own filter (363+ rows), otherwise the ambiguous list becomes useless.
  const real = row.flags.filter((f) => f !== 'propagated' && f !== 'mixed')
  return real.length > 0 || (!row.mixed && row.confidence < 0.25)
}
