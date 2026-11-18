// Batch API client — scan / start / rerun / stop / status

export type StepState = 'pending' | 'running' | 'done' | 'failed'

export interface BatchStepInfo {
  id: string
  status: StepState
  error: string | null
}

export type OverallState = 'pending' | 'partial' | 'done' | 'delivered' | 'failed'

export interface BatchFileRow {
  video: string
  workspace: string
  root: string
  overall: OverallState
  done_count: number
  total: number
  steps: BatchStepInfo[]
  error: string | null
}

export interface ScanResult {
  root: string
  log_root: string
  files: BatchFileRow[]
  state: string
}

export async function scanBatch(root: string): Promise<ScanResult> {
  const r = await fetch('/api/batch/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ root }),
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `scan failed: ${r.status}`)
  return r.json()
}

export interface StartOptions {
  files: string[]
  force_restart?: boolean
  reset_from?: string
  reset_to?: string
}

export async function startBatch(opts: StartOptions): Promise<void> {
  const r = await fetch('/api/batch/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `start failed: ${r.status}`)
}

export async function stopBatch(): Promise<void> {
  const r = await fetch('/api/batch/stop', { method: 'POST' })
  if (!r.ok) throw new Error(`stop failed: ${r.status}`)
}

export async function batchStatus(): Promise<{
  state: string
  root: string | null
  current: string | null
  files: { video: string; workspace: string }[]
}> {
  const r = await fetch('/api/batch/status')
  if (!r.ok) throw new Error(`status failed: ${r.status}`)
  return r.json()
}

export async function tailBatchLog(path: string, lines = 200): Promise<string[]> {
  const r = await fetch(`/api/batch/log?path=${encodeURIComponent(path)}&lines=${lines}`)
  if (!r.ok) return []
  const j = await r.json()
  return j.lines ?? []
}

// ---------------- file browser ----------------

export interface BrowseEntry {
  name: string
  path: string
  type: 'dir' | 'video' | 'audio'
  log?: boolean
}

export interface BrowseResult {
  path: string | null
  parent: string | null
  dirs: BrowseEntry[]
  files: BrowseEntry[]
}

export async function browse(path: string | null): Promise<BrowseResult> {
  const r = await fetch('/api/batch/browse', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
  if (!r.ok) {
    const d = await r.json().catch(() => ({}))
    throw new Error(d.detail || `browse failed: ${r.status}`)
  }
  return r.json()
}
