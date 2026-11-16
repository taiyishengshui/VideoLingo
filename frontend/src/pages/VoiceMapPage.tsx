import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Button, Card, Checkbox, Input, InputNumber, Modal, Popconfirm,
  Select, Space, Table, Tag, Typography, message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  FLAG_LABELS, deleteSpeaker, getVoiceMap, isAmbiguous, postPropagate,
  postRebuild, postRebuildChunks, postRevoice, postSpeaker, postSplitChunks,
  putChunks, putSegments, putSpeakerRef,
} from '../voiceApi'
import type { VoiceChunk, VoiceMap, VoiceRow } from '../voiceApi'

type TimelineItem = { number: number; start: number; end: number }

function parseTime(v: string | number): number {
  if (typeof v === 'number') return v
  const s = String(v ?? '').trim()
  if (!s) return NaN
  if (/^\d+(\.\d+)?$/.test(s)) return parseFloat(s)
  const p = s.split(':')
  if (p.length === 3) return Number(p[0]) * 3600 + Number(p[1]) * 60 + parseFloat(p[2])
  if (p.length === 2) return Number(p[0]) * 60 + parseFloat(p[1])
  return NaN
}

function fmtTime(sec: number): string {
  const s = Math.max(0, Math.floor(sec))
  return `${String(Math.floor(s / 3600)).padStart(2, '0')}:${String(Math.floor(s % 3600 / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

const PREVIEW_SOURCES = [
  { value: 'original', label: '原始视频（无字幕，听原声）' },
  { value: 'burned', label: '已烧录双语字幕' },
]

export default function VoiceMapPage() {
  const [data, setData] = useState<VoiceMap | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)
  const [changes, setChanges] = useState<Record<string, string>>({})
  const [marks, setMarks] = useState<Record<string, boolean>>({})
  const [selected, setSelected] = useState<number[]>([])
  const [keyword, setKeyword] = useState('')
  const [speakerFilter, setSpeakerFilter] = useState('all')
  const [onlyAmbiguous, setOnlyAmbiguous] = useState(false)
  const [onlyMixed, setOnlyMixed] = useState(false)
  const [chunkDraft, setChunkDraft] = useState<Record<string, Record<string, string>>>({})
  // 单行强制拆分的切点输入：{number: 相对本句起点的秒数}；留空 = 自动找切换点
  const [splitAt, setSplitAt] = useState<Record<string, number | null>>({})
  const [onlyManual, setOnlyManual] = useState(false)
  const [page, setPage] = useState(1)
  const [batchSpeaker, setBatchSpeaker] = useState('')
  const [refInputs, setRefInputs] = useState<Record<string, number | undefined>>({})
  const [newName, setNewName] = useState('')
  const [newSource, setNewSource] = useState<number | undefined>(undefined)
  const [msg, ctxHolder] = message.useMessage()

  const [videoSource, setVideoSource] = useState('original')
  const [showVideo, setShowVideo] = useState(true)
  const [follow, setFollow] = useState(true)
  const [singleClip, setSingleClip] = useState(true)
  const [currentNum, setCurrentNum] = useState<number | null>(null)
  const [currentTime, setCurrentTime] = useState(0)
  const [inGap, setInGap] = useState(false)
  const [pageSize, setPageSize] = useState(20)
  const [seeds, setSeeds] = useState<Record<string, number[]>>({})
  const [seedFp, setSeedFp] = useState('')
  const [protectManual, setProtectManual] = useState(false)
  const videoRef = useRef<HTMLVideoElement>(null)
  const stopAtRef = useRef<number | null>(null)
  const expectSeekRef = useRef<number | null>(null)
  const pendingSeekRef = useRef<number | null>(null)
  const lastSecRef = useRef(-1)

  useEffect(() => () => {
    const v = videoRef.current
    if (v) { v.pause(); v.removeAttribute('src'); v.load() }
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setErr('')
    try {
      setData(await getVoiceMap())
      setChanges({})
      setMarks({})
      setChunkDraft({})
      setSelected([])
      setPage(1)
    } catch (e) {
      setErr(String(e))
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => { void load() }, [load])

  // Seeds are expensive human judgements: persist them per video fingerprint
  // so a refresh or a rebuild never loses the labelled ground truth.
  useEffect(() => {
    const fp = data?.fingerprint ?? ''
    if (!fp || fp === seedFp) return
    try {
      const raw = localStorage.getItem(`voice-seeds:${fp}`)
      setSeeds(raw ? JSON.parse(raw) as Record<string, number[]> : (data?.seeds ?? {}))
    } catch { setSeeds(data?.seeds ?? {}) }
    setSeedFp(fp)
  }, [data?.fingerprint, data?.seeds, seedFp])

  useEffect(() => {
    if (!seedFp || seedFp !== data?.fingerprint) return
    try { localStorage.setItem(`voice-seeds:${seedFp}`, JSON.stringify(seeds)) } catch { /* ignore */ }
  }, [seeds, seedFp, data?.fingerprint])

  const seedCount = (name: string) => (seeds[name] ?? []).length

  const addSeed = (name: string, num: number) => {
    setSeeds((s) => {
      const next: Record<string, number[]> = {}
      // One line can only be a seed for one speaker.
      for (const k of Object.keys(s)) next[k] = s[k].filter((n) => n !== num)
      next[name] = [...(next[name] ?? []), num]
      return next
    })
    msg.success(`#${num} 已标为 ${name} 的种子`)
  }

  const removeSeed = (name: string, num: number) =>
    setSeeds((s) => ({ ...s, [name]: (s[name] ?? []).filter((n) => n !== num) }))

  const propagate = () => {
    const active = Object.fromEntries(
      Object.entries(seeds).filter(([, v]) => v.length > 0))
    if (Object.keys(active).length < 2) {
      msg.warning('至少给两位说话人各标 1 句种子，推荐每人 3-5 句、分散在不同段落')
      return
    }
    Modal.confirm({
      title: '按种子传播到全片？',
      content: `已标 ${Object.values(active).reduce((a, b) => a + b.length, 0)} 句种子。`
        + '会按声纹相似度把全片句子分给这两位说话人，结果写为人工锁定（重建初稿会保留）。'
        + '之后可以继续标更多种子再传播，随时修正。',
      okText: '开始传播', cancelText: '取消',
      onOk: async () => {
        setLoading(true)
        try {
          const r = await postPropagate(active, protectManual)
          const st = r.stat
          msg.success(`传播完成：${Object.entries(st.counts).map(([k, v]) => `${k} ${v} 句`).join('、')}`
            + `；低置信 ${st.low_margin} 句待复核`)
          await load()
        } catch (e) { msg.error(String(e)) }
        finally { setLoading(false) }
      },
    })
  }

  const timeline = useMemo<TimelineItem[]>(() => (data?.rows ?? [])
    .map((r) => ({
      number: r.number,
      start: parseTime(r.start_time),
      end: parseTime(r.end_time),
    }))
    .filter((x) => Number.isFinite(x.start) && Number.isFinite(x.end) && x.end > x.start)
    .sort((a, b) => a.start - b.start), [data])

  const findAt = useCallback((t: number): TimelineItem | null => {
    let lo = 0, hi = timeline.length - 1, ans = -1
    while (lo <= hi) {
      const mid = (lo + hi) >> 1
      if (timeline[mid].start <= t) { ans = mid; lo = mid + 1 } else hi = mid - 1
    }
    if (ans < 0) return null
    return t <= timeline[ans].end ? timeline[ans] : null
  }, [timeline])

  const nextAt = useCallback((t: number): TimelineItem | null => {
    let lo = 0, hi = timeline.length
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      if (timeline[mid].start <= t) lo = mid + 1; else hi = mid
    }
    return lo < timeline.length ? timeline[lo] : null
  }, [timeline])

  const seekTo = useCallback((item: TimelineItem) => {
    const v = videoRef.current
    if (!v) return
    const t = Math.max(0, item.start + 0.02)
    expectSeekRef.current = t
    stopAtRef.current = singleClip ? item.end : null
    setCurrentNum(item.number)
    if (v.readyState < 1) { pendingSeekRef.current = t; return }
    v.currentTime = t
    void v.play().catch(() => undefined)
  }, [singleClip])

  const seekRow = (row: VoiceRow) => {
    const start = parseTime(row.start_time)
    const end = parseTime(row.end_time)
    if (!Number.isFinite(start)) { msg.warning('这句没有有效时间码'); return }
    seekTo({ number: row.number, start, end: Number.isFinite(end) ? end : start + 3 })
  }

  const step = (delta: number) => {
    if (!timeline.length) return
    const idx = currentNum === null ? -1 : timeline.findIndex((x) => x.number === currentNum)
    const next = currentNum === null
      ? timeline[0]
      : timeline[Math.min(timeline.length - 1, Math.max(0, idx + delta))]
    if (next) seekTo(next)
  }

  const switchSource = (src: string) => {
    pendingSeekRef.current = videoRef.current?.currentTime ?? 0
    stopAtRef.current = null
    setVideoSource(src)
  }

  const handleLoadedMetadata = () => {
    const v = videoRef.current
    if (v && pendingSeekRef.current !== null) {
      v.currentTime = pendingSeekRef.current
      pendingSeekRef.current = null
    }
  }

  const handleSeeked = () => {
    if (expectSeekRef.current === null) stopAtRef.current = null
    expectSeekRef.current = null
  }

  const handleTimeUpdate = () => {
    const v = videoRef.current
    if (!v) return
    const t = v.currentTime
    const sec = Math.floor(t)
    if (sec !== lastSecRef.current) { lastSecRef.current = sec; setCurrentTime(t) }
    if (stopAtRef.current !== null && t >= stopAtRef.current) {
      v.pause()
      stopAtRef.current = null
    }
    if (follow) {
      const exact = findAt(t)
      const hit = exact ?? nextAt(t)
      setInGap(exact === null)
      const n = hit?.number ?? null
      setCurrentNum((prev) => (prev === n ? prev : n))
    } else {
      setInGap(false)
    }
  }

  const currentRow = currentNum === null
    ? null
    : (data?.rows ?? []).find((r) => r.number === currentNum) ?? null

  const speakerOf = (row: VoiceRow): string =>
    changes[String(row.number)] ?? row.speaker ?? ''
  const markedOf = (row: VoiceRow): boolean =>
    marks[String(row.number)] ?? row.flags.includes('marked')
  const dirtyCount = Object.keys(changes).length + Object.keys(marks).length

  const rows = useMemo(() => {
    if (!data) return []
    const kw = keyword.trim().toLowerCase()
    return data.rows.filter((r) => {
      if (kw && !(`${r.text} ${r.origin} ${r.number}`.toLowerCase().includes(kw))) return false
      if (speakerFilter !== 'all' && speakerOf(r) !== speakerFilter) return false
      if (onlyManual && !(r.manual || changes[String(r.number)])) return false
      if (onlyMixed && !r.mixed) return false
      if (onlyAmbiguous) {
        const f = markedOf(r) ? [...r.flags, 'marked'] : r.flags
        if (!isAmbiguous({ ...r, flags: [...new Set(f)] })) return false
      }
      return true
    })
  }, [data, keyword, speakerFilter, onlyManual, onlyMixed, onlyAmbiguous, changes, marks])

  useEffect(() => {
    if (!follow || currentNum === null) return
    const idx = rows.findIndex((r) => r.number === currentNum)
    if (idx < 0) return
    const target = Math.floor(idx / pageSize) + 1
    if (page !== target) { setPage(target); return }
    const id = requestAnimationFrame(() => {
      document.querySelector<HTMLElement>(`tr[data-row-key="${currentNum}"]`)
        ?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    })
    return () => cancelAnimationFrame(id)
  }, [follow, currentNum, rows, page, pageSize])

  const speakerOptions = (data?.speakers ?? []).map((s) => ({
    value: s.name, label: `${s.name} (${s.n_clips})`,
  }))

  const toggleMark = (row: VoiceRow) => {
    const key = String(row.number)
    setMarks((m) => ({ ...m, [key]: !markedOf(row) }))
  }

  const save = async () => {
    if (!dirtyCount) { msg.info('没有待保存的修改'); return }
    try {
      await putSegments({ changes, marks })
      msg.success(`已保存 ${dirtyCount} 项修改，合成链路已热更新（无需重启）`)
      await load()
    } catch (e) { msg.error(String(e)) }
  }

  const chunkDirty = Object.values(chunkDraft)
    .reduce((a, m) => a + Object.keys(m).length, 0)

  const saveChunks = async () => {
    if (!chunkDirty) { msg.info('没有待保存的子行修改'); return }
    try {
      const r = await putChunks(chunkDraft)
      msg.success(`已保存 ${r.changed} 条子行音色，合成链路已热更新`)
      await load()
    } catch (e) { msg.error(String(e)) }
  }

  /** numbers 为空 = 全片混句；有值 = 只重建这些行，单行还会尝试自动拆分。 */
  const rebuildChunks = (numbers?: number[]) => {
    const scoped = !!numbers?.length
    const count = scoped ? numbers!.length : (data?.n_mixed ?? 0)
    Modal.confirm({
      title: scoped ? `重建选中的 ${count} 句？` : '重建子行音色判定？',
      content: scoped
        ? `只重判勾选的 ${count} 句：混句逐段抽声纹，单句会先用滑窗声纹找说话人切换点，找到就拆成两段分别配音（会改这一行的配音子行，需要重跑第 10 步）。纯 CPU、不调 LLM、不改字幕与翻译；人工改过的子行会保留。`
        : `把 ${count} 条双人混句按原始字幕时间拆开，逐段抽声纹判给最像的说话人（纯 CPU、不调 LLM、不改字幕与翻译，几百段声纹需要一点时间）。已人工改过的子行会保留。`,
      okText: '开始判定', cancelText: '取消',
      onOk: async () => {
        setLoading(true)
        try {
          const s = (await postRebuildChunks(scoped ? numbers : undefined)).stat
          msg.success(`判定完成：${s.rows} 句 / ${s.chunks} 个子行，自动拆分 ${s.auto_split ?? 0} 句，低置信 ${s.low_margin} 条待复核`)
          await load()
        } catch (e) { msg.error(String(e)) }
        finally { setLoading(false) }
      },
    })
  }

  /** 单行强制拆分：切点秒数由用户听原声后填，留空则让声纹自动找。 */
  const splitRow = (num: number) => {
    const at = splitAt[String(num)]
    Modal.confirm({
      title: `把第 ${num} 句拆成两段？`,
      content: at == null
        ? '用滑窗声纹在这句的原声里找说话人切换点；找不到明显切换就不会拆，可以在展开区手填切点秒数。拆分会改配音任务表的子行，之后要「重配选中句」+ 重跑第 10 步。'
        : `在相对本句起点 ${at.toFixed(2)} 秒处拆开，前后两段分别配音。拆分会改配音任务表的子行，之后要「重配选中句」+ 重跑第 10 步。`,
      okText: '拆分并判定', cancelText: '取消',
      onOk: async () => {
        setLoading(true)
        try {
          const s = (await postSplitChunks({ [String(num)]: at ?? null })).stat
          if (s.split.length) {
            msg.success(`已拆成 2 段（切点 ${s.at[String(num)]}s），${s.chunks ?? 0} 个子行已判音色，请重配后重跑第 10 步`)
            await load()
          } else {
            msg.warning(Object.values(s.skipped)[0] ?? '没有可拆分的句子')
          }
        } catch (e) { msg.error(String(e)) }
        finally { setLoading(false) }
      },
    })
  }

  const revoiceMixed = () => {
    const nums = (data?.rows ?? []).filter((r) => r.mixed).map((r) => r.number)
    if (!nums.length) { msg.info('没有双人混句'); return }
    Modal.confirm({
      title: `重配全部 ${nums.length} 条混句？`,
      content: '会删掉这些句子已落盘的配音 wav，然后到流水线重跑第 10 步即可只重配它们（其余句子秒跳过）。不动字幕、翻译与断点。',
      okText: '清掉旧配音', cancelText: '取消',
      onOk: async () => {
        try {
          const r = await postRevoice(nums)
          msg.success(`已清除 ${r.count} 个旧音频文件；到流水线重跑第 10 步`)
        } catch (e) { msg.error(String(e)) }
      },
    })
  }

  const applyBatch = () => {
    if (!selected.length || !batchSpeaker) {
      msg.warning('先勾选句子并选择目标说话人'); return
    }
    setChanges((c) => {
      const next = { ...c }
      selected.forEach((n) => { next[String(n)] = batchSpeaker })
      return next
    })
    msg.info(`已暂存 ${selected.length} 句，点「保存修改」生效`)
  }

  const unassign = async () => {
    if (!selected.length) { msg.warning('先勾选句子'); return }
    try {
      await putSegments({ unassign: selected })
      msg.success(`已解除 ${selected.length} 句的人工锁定，下次重建会重新判定`)
      await load()
    } catch (e) { msg.error(String(e)) }
  }

  const rebuild = (clear: boolean) => {
    Modal.confirm({
      title: clear ? '清除全部人工分配并重建？' : '重新生成自动初稿？',
      content: clear
        ? '所有人工改过的句子都会被算法重新判定，此操作不可撤销。'
        : '只重算自动初稿；人工改过的句子会原样保留。纯 CPU 计算，约需十几秒到一分钟。',
      okText: '开始重建', cancelText: '取消',
      onOk: async () => {
        setLoading(true)
        try {
          await postRebuild(clear)
          msg.success('初稿已重建')
          await load()
        } catch (e) { msg.error(String(e)) }
        finally { setLoading(false) }
      },
    })
  }

  const revoice = () => {
    if (!selected.length) { msg.warning('先勾选句子'); return }
    Modal.confirm({
      title: '重配选中句？',
      content: `将删除 ${selected.length} 句已合成的 wav。之后到「流水线」页重跑第 10 步『生成配音』，其余句子会直接跳过。`,
      okText: '删除并准备重配', cancelText: '取消',
      onOk: async () => {
        try {
          const r = await postRevoice(selected)
          msg.success(r.count ? r.hint : '这些句子还没有合成音频，无需删除')
        } catch (e) { msg.error(String(e)) }
      },
    })
  }

  const changeRef = async (name: string) => {
    const num = refInputs[name]
    if (!num) { msg.warning('先填一个原声切片编号'); return }
    try {
      await putSpeakerRef(name, num)
      msg.success(`已用第 ${num} 句原声替换 ${name} 的音色`)
      await load()
    } catch (e) { msg.error(String(e)) }
  }

  const addSpeaker = async () => {
    if (!newName.trim() || !newSource) { msg.warning('填说话人名和来源句编号'); return }
    try {
      await postSpeaker(newName.trim(), newSource)
      msg.success(`已新增 ${newName.trim()}`)
      setNewName(''); setNewSource(undefined)
      await load()
    } catch (e) { msg.error(String(e)) }
  }

  const removeSpeaker = async (name: string) => {
    try {
      await deleteSpeaker(name)
      msg.success(`已删除 ${name}，其句子已归并`)
      await load()
    } catch (e) { msg.error(String(e)) }
  }

  const columns: ColumnsType<VoiceRow> = [
    { title: '编号', dataIndex: 'number', width: 72, fixed: 'left' },
    {
      title: '时间', dataIndex: 'start_time', width: 100,
      render: (t: string) => <Typography.Text style={{ fontSize: 11 }}>{t}</Typography.Text>,
    },
    {
      title: '定位', width: 66,
      render: (_: unknown, row: VoiceRow) => (
        <Button size="small" type="link" style={{ padding: 0 }}
          onClick={(e) => { e.stopPropagation(); seekRow(row) }}>跳转</Button>
      ),
    },
    {
      title: '文本 / 原文', dataIndex: 'text', width: 320,
      render: (_: unknown, row: VoiceRow) => (
        <div style={{ maxWidth: 310 }}>
          <Typography.Text ellipsis={{ tooltip: row.text }} style={{ fontSize: 12 }}>
            {row.text}
          </Typography.Text>
          <div>
            <Typography.Text type="secondary" ellipsis={{ tooltip: row.origin }} style={{ fontSize: 11 }}>
              {row.origin}
            </Typography.Text>
          </div>
        </div>
      ),
    },
    {
      title: '说话人', width: 175,
      render: (_: unknown, row: VoiceRow) => (
        <Space size={2}>
          <Select size="small" style={{ width: 145 }} placeholder="未分配"
            value={speakerOf(row) || undefined} options={speakerOptions}
            onChange={(v) => setChanges((c) => ({ ...c, [String(row.number)]: v }))} />
          {row.manual && <Tag color="blue">锁定</Tag>}
        </Space>
      ),
    },
    {
      title: '置信', width: 70,
      render: (_: unknown, row: VoiceRow) => (
        <Typography.Text type={row.confidence < 0.25 ? 'danger' : 'secondary'} style={{ fontSize: 12 }}>
          {row.confidence.toFixed(2)}
        </Typography.Text>
      ),
    },
    {
      title: '标记', width: 200,
      render: (_: unknown, row: VoiceRow) => (
        <Space size={3} wrap>
          {row.flags.filter((f) => f !== 'propagated').map((f) => (
            <Tag key={f} color={f === 'marked' || f === 'low_margin' ? 'orange' : undefined}>
              {FLAG_LABELS[f] ?? f}
            </Tag>
          ))}
          {markedOf(row) && !row.flags.includes('marked') && <Tag color="red">人工标记</Tag>}
          <Button size="small" type="link" onClick={() => toggleMark(row)}>
            {markedOf(row) ? '取消' : '标记'}
          </Button>
        </Space>
      ),
    },
    {
      title: '原声切片', width: 165,
      render: (_: unknown, row: VoiceRow) => row.ref_url
        ? <audio controls preload="none" src={row.ref_url} style={{ width: 150, height: 30 }} />
        : <Tag>缺失</Tag>,
    },
    {
      title: '合成配音', width: 165,
      render: (_: unknown, row: VoiceRow) => row.dub_url
        ? <audio controls preload="none" src={row.dub_url} style={{ width: 150, height: 30 }} />
        : <Typography.Text type="secondary" style={{ fontSize: 11 }}>未合成</Typography.Text>,
    },
  ]

  return (
    <div>
      <style>{`
        .voice-current-row > td { background: rgba(22,119,255,0.16) !important; }
        .voice-current-row > td:first-child { box-shadow: inset 3px 0 0 #1677ff; }
        .voice-current-row:hover > td { background: rgba(22,119,255,0.26) !important; }
      `}</style>
      {ctxHolder}
      {err && <Alert type="error" showIcon message="读取契约失败" description={err} style={{ marginBottom: 12 }} />}
      {data?.status === 'missing' && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }} message="还没有 voice_map.json"
          description="先跑流水线第 9 步，或点下面的「重建初稿」生成。" />
      )}
      {data?.status === 'stale' && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          message="自动初稿已过期：人工分配可编辑保存，但直接跑第 10 步会被拦下"
          description={`${data.reasons.join('；')}。请先点「重建初稿（保留人工）」用当前算法重算一次；人工改过的句子会原样保留。纯 CPU，不占显存。`} />
      )}

      <Card size="small" style={{ marginBottom: 12 }}>
        <Space wrap size={6} style={{ marginBottom: 10 }}>
          <Tag>共 {data?.rows.length ?? 0} 句</Tag>
          <Tag>{data?.n_speakers ?? 0} 位说话人</Tag>
          <Tag color={data?.degraded ? 'orange' : undefined}>
            {data?.degraded
              ? ((data.n_speakers ?? 0) > 1 ? '初稿未分出多人（已人工补人）' : '已降级单人')
              : '多说话人'}
          </Tag>
          <Tag>producer {data?.producer ?? '-'}</Tag>
          <Tag>fingerprint {data?.fingerprint ?? '-'}</Tag>
          <Tag color="blue">人工锁定 {Object.keys(data?.overrides ?? {}).length}</Tag>
          <Tag color={dirtyCount ? 'red' : undefined}>待保存 {dirtyCount}</Tag>
        </Space>
        <div>
          <Space wrap>
            <Button size="small" onClick={() => void load()} loading={loading}>刷新</Button>
            <Button size="small" type="primary" disabled={!dirtyCount} onClick={() => void save()}>
              保存修改{dirtyCount ? ` (${dirtyCount})` : ''}
            </Button>
            <Button size="small" onClick={() => rebuild(false)} loading={loading}>重建初稿（保留人工）</Button>
            <Popconfirm title="清除全部人工分配？不可撤销" onConfirm={() => rebuild(true)}>
              <Button size="small" danger>清除人工并重建</Button>
            </Popconfirm>
          </Space>
        </div>
      </Card>

      <Card size="small" style={{ marginBottom: 12, position: 'sticky', top: 0, zIndex: 5 }}
        title={<Space size={6}>
          <span>视频时间轴 · 听原声判断说话人</span>
          <Tag color="blue">{fmtTime(currentTime)}</Tag>
        </Space>}
        extra={<Space size={4} wrap>
          <Select size="small" style={{ width: 205 }} value={videoSource}
            onChange={switchSource} options={PREVIEW_SOURCES} />
          <Checkbox checked={follow} onChange={(e) => setFollow(e.target.checked)}>跟随播放</Checkbox>
          <Checkbox checked={singleClip} onChange={(e) => setSingleClip(e.target.checked)}>只播这一句</Checkbox>
          <Button size="small" onClick={() => setShowVideo((s) => !s)}>
            {showVideo ? '收起画面' : '展开画面'}
          </Button>
        </Space>}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
          <div style={{ flex: '0 0 44%', maxWidth: 480 }}>
            <video key={videoSource} ref={videoRef} controls preload="metadata" playsInline
              src={`/api/ad/preview?source=${videoSource}`}
              onTimeUpdate={handleTimeUpdate} onSeeked={handleSeeked}
              onLoadedMetadata={handleLoadedMetadata}
              style={{ width: '100%', maxHeight: 225, background: '#000', display: showVideo ? 'block' : 'none' }} />
            {showVideo && (
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                点表格任意行或「跳转」→ 视频跳到该句播原声；拖动进度条 → 表格自动高亮并翻到对应句。
              </Typography.Text>
            )}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            {currentRow ? (
              <>
                <Space wrap size={5} style={{ marginBottom: 6 }}>
                  <Tag color={inGap ? 'default' : 'processing'}>#{currentRow.number}</Tag>
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    {currentRow.start_time} → {currentRow.end_time}
                  </Typography.Text>
                  <Tag color="blue">{speakerOf(currentRow) || '未分配'}</Tag>
                  {currentRow.manual && <Tag>人工锁定</Tag>}
                  {inGap && <Tag color="orange">句间间隙 · 指向下一句</Tag>}
                </Space>
                <div style={{ fontSize: 13, lineHeight: 1.6 }}>{currentRow.text}</div>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>{currentRow.origin}</Typography.Text>
                <div style={{ marginTop: 8 }}>
                  <Space size={4} wrap>
                    <Button size="small" onClick={() => step(-1)}>上一句</Button>
                    <Button size="small" onClick={() => step(1)}>下一句</Button>
                    <Button size="small" onClick={() => seekRow(currentRow)}>重播这句</Button>
                    <Button size="small" onClick={() => setSelected((s) =>
                      s.includes(currentRow.number) ? s : [...s, currentRow.number])}>勾选这句</Button>
                    <Select size="small" style={{ width: 160 }} placeholder="改归谁"
                      value={speakerOf(currentRow) || undefined} options={speakerOptions}
                      onChange={(v) => setChanges((c) => ({ ...c, [String(currentRow.number)]: v }))} />
                    {(data?.speakers ?? []).map((s) => (
                      <Button key={s.name} size="small"
                        type={(seeds[s.name] ?? []).includes(currentRow.number) ? 'primary' : 'default'}
                        onClick={() => addSeed(s.name, currentRow.number)}>
                        种子 {s.name.replace('SPEAKER_', 'S')}
                      </Button>
                    ))}
                  </Space>
                </div>
              </>
            ) : (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                点表格中的一句，或播放视频，这里会显示对应的句子和它归谁。
              </Typography.Text>
            )}
          </div>
        </div>
      </Card>

      <Card size="small" title="种子标注 · 半监督传播" style={{ marginBottom: 12 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
          用上方视频听原声，听到确定是谁说的，就点视频卡片里的「种子 S00 / S01」把这句标下。
          每人标 3-5 句、尽量分散在不同段落；种子是人工听辨的真值，比无监督聚类可靠得多。
          标完点「按种子传播到全片」，其余 1000 多句会按声纹相似度自动归位，低置信的句子会打标记供你复核。
        </Typography.Text>
        <Space wrap align="start" style={{ marginBottom: 8 }}>
          {(data?.speakers ?? []).map((s) => (
            <Card key={s.name} size="small" style={{ width: 290 }}
              title={<Space size={4}><Tag color="blue">{s.name}</Tag>
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  {seedCount(s.name)} 句种子
                </Typography.Text></Space>}>
              <Space wrap size={4}>
                {(seeds[s.name] ?? []).map((n) => (
                  <Tag key={n} closable onClose={() => removeSeed(s.name, n)}>#{n}</Tag>
                ))}
                {seedCount(s.name) === 0 && (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>还没标种子</Typography.Text>
                )}
              </Space>
              <Button size="small" block style={{ marginTop: 8 }} disabled={currentNum === null}
                onClick={() => currentNum !== null && addSeed(s.name, currentNum)}>
                把当前句 #{currentNum ?? '-'} 标为种子
              </Button>
            </Card>
          ))}
        </Space>
        <Space wrap>
          <Checkbox checked={protectManual} onChange={(e) => setProtectManual(e.target.checked)}>
            保护我手工改过的句子
          </Checkbox>
          <Button size="small" type="primary" onClick={propagate} loading={loading}>
            按种子传播到全片
          </Button>
          <Button size="small" onClick={() => setSeeds({})}>清空种子</Button>
          {Object.keys(data?.seeds ?? {}).length > 0 && (
            <Tag color="green">契约已保存过种子：{Object.entries(data?.seeds ?? {})
              .map(([k, v]) => `${k} ${v.length} 句`).join('、')}</Tag>
          )}
        </Space>
      </Card>

      <Card size="small" title="说话人与音色" style={{ marginBottom: 12 }}>
        <Space wrap align="start">
          {(data?.speakers ?? []).map((s) => (
            <Card key={s.name} size="small" style={{ width: 265 }}
              title={<Space size={4}><Tag color="blue">{s.name}</Tag>
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>{s.n_clips} 句</Typography.Text></Space>}
              extra={
                <Popconfirm title="删除后其句子归到人数最多的说话人" onConfirm={() => void removeSpeaker(s.name)}>
                  <Button size="small" type="text" danger>删除</Button>
                </Popconfirm>
              }>
              {s.ref_url
                ? <audio controls preload="none" src={s.ref_url} style={{ width: '100%', height: 32 }} />
                : <Tag>无代表音频</Tag>}
              <div style={{ margin: '6px 0' }}>
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  代表 #{s.ref_source ?? '-'} · {s.ref_duration ?? '-'}s{s.ref_manual ? ' · 人工指定' : ''}
                </Typography.Text>
              </div>
              <Space.Compact style={{ width: '100%' }}>
                <InputNumber size="small" min={1} placeholder="来源句编号" style={{ width: '100%' }}
                  value={refInputs[s.name]}
                  onChange={(v) => setRefInputs((x) => ({ ...x, [s.name]: v ?? undefined }))} />
                <Button size="small" onClick={() => void changeRef(s.name)}>换音色</Button>
              </Space.Compact>
              <Button size="small" block style={{ marginTop: 6 }}
                onClick={() => { setBatchSpeaker(s.name); msg.info(`已选 ${s.name}，勾选句子后点「批量改为」`) }}>
                选中句改为此人
              </Button>
            </Card>
          ))}
          <Card size="small" title="新增说话人" style={{ width: 265 }}>
            <Space direction="vertical" style={{ width: '100%' }}>
              <Input size="small" placeholder="名字，如 SPEAKER_02" value={newName}
                onChange={(e) => setNewName(e.target.value)} />
              <InputNumber size="small" min={1} placeholder="取哪句原声当音色（编号）" style={{ width: '100%' }}
                value={newSource} onChange={(v) => setNewSource(v ?? undefined)} />
              <Button size="small" type="dashed" block onClick={() => void addSpeaker()}>新增</Button>
            </Space>
          </Card>
        </Space>
      </Card>

      <Card size="small">
        <Space wrap style={{ marginBottom: 10 }}>
          <Input.Search size="small" allowClear placeholder="搜文本 / 原文 / 编号" style={{ width: 230 }}
            value={keyword} onChange={(e) => { setKeyword(e.target.value); setPage(1) }} />
          <Select size="small" style={{ width: 175 }} value={speakerFilter}
            onChange={(v) => { setSpeakerFilter(v); setPage(1) }}
            options={[{ value: 'all', label: '全部说话人' }, ...speakerOptions]} />
          <Checkbox checked={onlyAmbiguous} onChange={(e) => { setOnlyAmbiguous(e.target.checked); setPage(1) }}>
            只看模糊句
          </Checkbox>
          <Checkbox checked={onlyMixed} onChange={(e) => { setOnlyMixed(e.target.checked); setPage(1) }}>
            只看双人混句{data?.n_mixed ? ` (${data.n_mixed})` : ''}
          </Checkbox>
          <Checkbox checked={onlyManual} onChange={(e) => { setOnlyManual(e.target.checked); setPage(1) }}>
            只看人工锁定
          </Checkbox>
          <Select size="small" style={{ width: 165 }} placeholder="批量改为" value={batchSpeaker || undefined}
            onChange={(v) => setBatchSpeaker(v)} options={speakerOptions} />
          <Button size="small" onClick={applyBatch}>批量改为</Button>
          <Button size="small" onClick={() => void unassign()} disabled={!selected.length}>解除锁定</Button>
          <Button size="small" danger onClick={revoice} disabled={!selected.length}>重配选中句</Button>
          <Button size="small"
            onClick={() => rebuildChunks(selected.length ? selected : undefined)}
            disabled={!selected.length && !data?.n_mixed}>
            重建{selected.length ? `选中 ${selected.length} 行` : '子行判定'}
          </Button>
          <Button size="small" danger onClick={revoiceMixed} disabled={!data?.n_mixed}>重配全部混句</Button>
          <Button size="small" type="primary" disabled={!chunkDirty} onClick={() => void saveChunks()}>
            保存子行{chunkDirty ? ` (${chunkDirty})` : ''}
          </Button>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>已选 {selected.length} 句</Typography.Text>
        </Space>

        <Table<VoiceRow>
          size="small" rowKey="number" columns={columns} dataSource={rows}
          loading={loading} scroll={{ x: 1320 }}
          rowClassName={(r) => (r.number === currentNum ? 'voice-current-row' : '')}
          onRow={(row) => ({
            style: { cursor: 'pointer' },
            onClick: (e) => {
              const t = e.target as HTMLElement
              if (t.closest('button, input, select, audio, a, .ant-select, .ant-checkbox, .ant-btn, .ant-input, .ant-tag')) return
              seekRow(row)
            },
          })}
          expandable={{
            // v1.26：每一行都能展开。单字幕行也能改子行 0 的音色，
            // 或者按切点拆成两段——这正是「没有 + 号」的那些句子。
            rowExpandable: () => true,
            expandedRowRender: (row) => {
              const group = data?.chunks?.[String(row.number)]
              if (!group) return (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  还没有子行判定，点上方「重建子行判定」生成。
                </Typography.Text>
              )
              const draft = chunkDraft[String(row.number)] ?? {}
              return (
                <div style={{ padding: '6px 8px', background: '#fafafa' }}>
                  <Space wrap size={6} style={{ marginBottom: 4 }}>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {group.rows.length} 个子行 · 时间相对本句起点 · 来源：
                      {group.source === 'srt' ? '原始字幕时间' : group.split ? '手动/自动切点' : '等分兜底'} ·
                      每行单独合成、单独占时间轴
                    </Typography.Text>
                    <Button size="small" onClick={() => rebuildChunks([row.number])}>
                      重建此行子行判定
                    </Button>
                  </Space>
                  {!group.mixed && (
                    <div style={{ margin: '6px 0', padding: '6px 8px', background: '#fff7e6', borderRadius: 4 }}>
                      <Typography.Text style={{ fontSize: 12 }}>
                        这句只有 1 条原始字幕。可以直接在下面改它的音色；如果里面其实挤了两个人的声音，
                        填一个切点秒数（相对本句起点，可边听原声边定位）把它拆成两段分别配音。
                      </Typography.Text>
                      <Space wrap size={6} style={{ marginTop: 6 }}>
                        <InputNumber size="small" style={{ width: 130 }}
                          placeholder="留空=自动找" min={0.1} step={0.1} precision={2}
                          value={splitAt[String(row.number)] ?? undefined}
                          onChange={(v) => setSplitAt((s) => ({
                            ...s, [String(row.number)]: v ?? null,
                          }))} />
                        <Button size="small" type="primary" onClick={() => splitRow(row.number)}>
                          拆成两段并判定
                        </Button>
                      </Space>
                    </div>
                  )}
                  {group.rows.map((c: VoiceChunk) => (
                    <Space key={c.index} wrap style={{ display: 'flex', marginTop: 6 }}>
                      <Tag>#{c.index}</Tag>
                      <Typography.Text style={{ fontSize: 12, minWidth: 200, maxWidth: 380 }}
                        ellipsis={{ tooltip: c.text }}>{c.text}</Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                        {Number(c.time?.[0] ?? 0).toFixed(2)}–{Number(c.time?.[1] ?? 0).toFixed(2)}s
                      </Typography.Text>
                      <Select size="small" style={{ width: 150 }}
                        value={draft[String(c.index)] ?? c.speaker ?? undefined}
                        options={speakerOptions}
                        onChange={(v) => setChunkDraft((d) => ({
                          ...d,
                          [String(row.number)]: {
                            ...(d[String(row.number)] ?? {}), [String(c.index)]: v,
                          },
                        }))} />
                      {c.manual && <Tag color="blue">人工</Tag>}
                      {!c.manual && c.confidence < 0.25 && <Tag color="orange">低置信</Tag>}
                    </Space>
                  ))}
                </div>
              )
            },
          }}
          rowSelection={{ selectedRowKeys: selected, onChange: (keys) => setSelected(keys.map(Number)) }}
          pagination={{
            current: page, pageSize, showSizeChanger: true,
            pageSizeOptions: [20, 50, 100], showTotal: (t) => `共 ${t} 句`,
            onChange: (p, ps) => { setPage(p); setPageSize(ps) },
          }}
        />
      </Card>

      <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
        视频面板放的是原片原声：点表格行跳到该句、拖动进度条反查句子，用来判断「这句到底是谁说的」。
        听到该改的，直接在视频面板选「改归谁」或勾选后批量改，点保存即热更新、无需重启服务；
        想让已生成的句子换音色，勾选后点「重配选中句」，再到流水线重跑第 10 步。
        标「双人混句」的行点左侧箭头可展开，按原始字幕时间逐子行指定音色；改完点「保存子行」，同样要重配并第 10 步重跑才生效。
      </Typography.Text>
    </div>
  )
}
