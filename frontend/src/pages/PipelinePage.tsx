import {
  useCallback, useEffect, useRef, useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Input,
  InputNumber,
  List,
  Popconfirm,
  Radio,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  CheckCircleFilled,
  ClockCircleOutlined,
  ClearOutlined,
  CopyOutlined,
  DeleteOutlined,
  DownloadOutlined,
  PlusOutlined,
  FileTextOutlined,
  LoadingOutlined,
  PauseOutlined,
  CaretRightOutlined,
  StopOutlined,
  PlayCircleOutlined,
  RedoOutlined,
  ReloadOutlined,
  SaveOutlined,
  YoutubeOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import {
  AdContext,
  AdMediaInfo,
  AdSegment,
  BiliContext,
  BiliMetaResult,
  BiliTranscript,
  CookieStatus,
  InputInfo,
  PipelineStatus,
  RunPipelineBody,
  StepStatus,
  adPreviewUrl,
  clearInput,
  control,
  cutVideo,
  detectAds,
  downloadVideo,
  getAdContext,
  getAdMediaInfo,
  getBiliContext,
  getCookieStatus,
  getInput,
  getPipeline,
  generateBiliMeta,
  releasePreviewStreams,
  runPipeline,
  saveAdSegments,
  saveHistory,
  tailLog,
} from '../api'
import { BusEvent, useBus } from '../ws'
import { loadInputDraft, saveInputDraft } from '../inputDraft'

import type { ColumnsType } from 'antd/es/table'

const MAX_LOG_LINES = 1500

const AD_BASIS: Record<string, string> = {
  prev_end: '上一句正文结束',
  scene_cut: '画面切点',
  silence: '静音中点',
  word_gap: '词级空隙',
  fallback: '保守兜底',
  manual: '手动指定',
}

function fmtClock(sec: number, withMs = false): string {
  const s = Math.max(0, sec || 0)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const rest = s % 60
  const head = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
  return withMs
    ? `${head}:${rest.toFixed(3).padStart(6, '0')}`
    : `${head}:${String(Math.floor(rest)).padStart(2, '0')}`
}

/* ---------------------------------------------------------------------------
 * FR-28 精细时间轴（2026-11-10）
 * 原生 <video> 进度条铺的是整部片（47 分钟 / 几百像素 ≈ 一点跳 5 秒），
 * 根本没法把裁剪点定到某一帧。这里只显示边界附近的一个**局部窗口**
 * （默认 ±3s，可切 ±1 / ±10），于是 1 像素 ≈ 10ms，配合逐帧按钮、
 * 方向键和帧网格吸附，精度就是「帧」而不是「秒」。
 * 轴上同时画出正文末词 / 画面切点 / 广告开口 / 当前裁剪区间，
 * 让「该切在哪一帧」直接看得见，不用靠猜。
 * ------------------------------------------------------------------------- */
interface ScrubMark {
  t: number
  label: string
  color: string
  strong?: boolean
}

interface FrameScrubberProps {
  center: number
  zoom: number
  playhead: number
  fps: number
  fpsOptions: number[]
  fpsSource: 'auto' | 'manual' | 'fallback'
  snap: boolean
  playing: boolean
  marks: ScrubMark[]
  activeLabel: string
  onSeek: (t: number) => void
  onNudge: (delta: number) => void
  onZoom: (z: number) => void
  onSnap: (v: boolean) => void
  onFps: (v: number) => void
  onTogglePlay: () => void
  onPick: (which: 'start' | 'end') => void
  onRecenter: () => void
}

const SCRUB_ZOOMS = [1, 3, 10]

function FrameScrubber(p: FrameScrubberProps) {
  const barRef = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)
  const lastSeek = useRef(0)
  const t0 = Math.max(0, p.center - p.zoom)
  const t1 = p.center + p.zoom
  const span = Math.max(0.001, t1 - t0)
  const pct = (t: number) => Math.min(100, Math.max(0, ((t - t0) / span) * 100))
  const frameNo = Math.round(p.playhead * p.fps)
  const frameSec = 1 / Math.max(1, p.fps)

  const timeAt = (clientX: number) => {
    const el = barRef.current
    if (!el) return p.center
    const r = el.getBoundingClientRect()
    const ratio = Math.min(1, Math.max(0, (clientX - r.left) / Math.max(1, r.width)))
    return t0 + ratio * span
  }
  const onDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    dragging.current = true
    e.currentTarget.focus()
    e.currentTarget.setPointerCapture(e.pointerId)
    p.onSeek(timeAt(e.clientX))
  }
  const onMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return
    const now = performance.now()
    if (now - lastSeek.current < 55) return   // 拖动时 ~18 次/秒，够看又不把解码压死
    lastSeek.current = now
    p.onSeek(timeAt(e.clientX))
  }
  const onUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return
    dragging.current = false
    p.onSeek(timeAt(e.clientX))
    try { e.currentTarget.releasePointerCapture(e.pointerId) } catch { /* 已释放 */ }
  }
  const onKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    let delta = 0
    if (e.key === 'ArrowLeft') delta = e.shiftKey ? -1 : e.altKey ? -0.1 : -frameSec
    else if (e.key === 'ArrowRight') delta = e.shiftKey ? 1 : e.altKey ? 0.1 : frameSec
    else if (e.key === ' ') { e.preventDefault(); p.onTogglePlay(); return }
    else return
    e.preventDefault()
    p.onNudge(delta)
  }

  const tickStep = p.zoom <= 1 ? 0.1 : p.zoom <= 3 ? 0.5 : 2
  const ticks: number[] = []
  const firstTick = Math.ceil(t0 / tickStep) * tickStep
  for (let i = 0; i <= 240; i += 1) {
    const t = firstTick + i * tickStep
    if (t > t1) break
    ticks.push(t)
  }
  const visibleMarks = p.marks.filter((m) => m.t >= t0 && m.t <= t1)
  // 图例只列当前窗口里真的画出来的线，否则列表一长串、轴上一根都没有
  const legend = visibleMarks.filter((m, i, arr) =>
    arr.findIndex((x) => x.label.replace(/ \d+$/, '') === m.label.replace(/ \d+$/, '')) === i)
    .slice(0, 7)

  // 点完按钮立刻把焦点还给时间轴，方向键可以接着逐帧走
  const refocusBar = () => document.getElementById('ad-scrub-bar')?.focus()
  const nudgeBtn = (label: string, delta: number, title: string) => (
    <Button size="small" style={{ minWidth: 54, padding: '0 6px' }}
      title={title} onClick={() => { p.onNudge(delta); refocusBar() }}>{label}</Button>
  )

  return (
    <Space direction="vertical" size={5} style={{ width: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 6 }}>
        <Space size={8} wrap>
          <Typography.Text code style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>
            {fmtClock(p.playhead, true)}
          </Typography.Text>
          <Tooltip title={p.fpsSource === 'auto'
            ? '帧率由 ffprobe 从预览源实测得到，逐帧步长 = 1/fps'
            : p.fpsSource === 'manual'
              ? '当前帧率为手选值，逐帧步长 = 1/fps'
              : '帧率自动探测失败（后端可能刚更新未重启），当前是默认 30 fps；请手选正确帧率或重启服务后刷新'}>
            <Typography.Text type={p.fpsSource === 'fallback' ? 'warning' : 'secondary'} style={{ fontSize: 11 }}>
              第 {frameNo} 帧 · {Number(p.fps.toFixed(3))} fps
              （{p.fpsSource === 'auto' ? '实测' : p.fpsSource === 'manual' ? '手选' : '默认'}）
            </Typography.Text>
          </Tooltip>
          {p.activeLabel
            ? <Tag color="orange" style={{ margin: 0 }}>{p.activeLabel}</Tag>
            : <Tag style={{ margin: 0 }}>未选边界 · 先点表格「跳到」</Tag>}
        </Space>
        <Space size={6} wrap>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>窗口</Typography.Text>
          <Radio.Group size="small" optionType="button" buttonStyle="solid"
            value={p.zoom} onChange={(e) => p.onZoom(Number(e.target.value))}
            options={SCRUB_ZOOMS.map((z) => ({ label: `±${z}s`, value: z }))} />
          <Select size="small" style={{ width: 100 }} value={p.fps} onChange={p.onFps}
            options={p.fpsOptions.map((f) => ({ value: f, label: `${f} fps` }))} />
          <Tooltip title="把时间吸附到最近的整帧，保证 EDL 里的秒数与真实帧边界一致">
            <Space size={3}>
              <Switch size="small" checked={p.snap} onChange={p.onSnap} />
              <Typography.Text style={{ fontSize: 11 }}>吸附帧</Typography.Text>
            </Space>
          </Tooltip>
        </Space>
      </div>

      <Space size={4} wrap>
        {nudgeBtn('−1s', -1, '后退 1 秒（Shift+←）')}
        {nudgeBtn('−0.1s', -0.1, '后退 0.1 秒（Alt+←）')}
        {nudgeBtn('−1帧', -frameSec, '后退一帧（←）')}
        <Button size="small" type="primary" icon={p.playing ? <PauseOutlined /> : <CaretRightOutlined />}
          onClick={() => { p.onTogglePlay(); refocusBar() }}>{p.playing ? '暂停' : '播放'}</Button>
        {nudgeBtn('+1帧', frameSec, '前进一帧（→）')}
        {nudgeBtn('+0.1s', 0.1, '前进 0.1 秒（Alt+→）')}
        {nudgeBtn('+1s', 1, '前进 1 秒（Shift+→）')}
        <Typography.Text type="secondary" style={{ fontSize: 11, marginLeft: 4 }}>
          点轴定位 · ←/→ 一帧 · Shift 一秒 · Alt 0.1 秒 · 空格播放
        </Typography.Text>
      </Space>

      <div id="ad-scrub-bar" ref={barRef} tabIndex={0}
        onKeyDown={onKey} onPointerDown={onDown} onPointerMove={onMove}
        onPointerUp={onUp} onPointerCancel={onUp}
        style={{
          position: 'relative', height: 54, background: '#fafafa',
          border: '1px solid #e5e7eb', borderRadius: 6, cursor: 'crosshair',
          touchAction: 'none', outline: 'none', userSelect: 'none', overflow: 'hidden',
        }}>
        {ticks.map((t) => (
          <div key={t} style={{
            position: 'absolute', left: `${pct(t)}%`, top: 0, bottom: 15,
            borderLeft: '1px solid #eceef1', pointerEvents: 'none',
          }} />
        ))}
        {visibleMarks.map((m, i) => (
          <Tooltip key={`${m.label}-${i}`} title={`${m.label} ${fmtClock(m.t, true)}`}>
            <div style={{
              position: 'absolute', left: `${pct(m.t)}%`, top: 0, bottom: 15,
              width: m.strong ? 3 : 2, marginLeft: m.strong ? -1 : 0,
              background: m.color, opacity: m.strong ? 1 : 0.78,
            }} />
          </Tooltip>
        ))}
        <div style={{
          position: 'absolute', left: `${pct(p.playhead)}%`, top: 0, bottom: 0,
          width: 2, marginLeft: -1, background: '#1677ff', pointerEvents: 'none',
        }}>
          <div style={{
            position: 'absolute', top: 0, left: -5, width: 12, height: 8,
            background: '#1677ff', clipPath: 'polygon(0 0,100% 0,50% 100%)',
          }} />
        </div>
        <Typography.Text style={{ position: 'absolute', left: 4, bottom: 1, fontSize: 10 }}>{fmtClock(t0, true)}</Typography.Text>
        <Typography.Text style={{
          position: 'absolute', left: '50%', bottom: 1, fontSize: 10,
          transform: 'translateX(-50%)', color: '#8c8c8c',
        }}>{fmtClock((t0 + t1) / 2, true)}</Typography.Text>
        <Typography.Text style={{ position: 'absolute', right: 4, bottom: 1, fontSize: 10 }}>{fmtClock(t1, true)}</Typography.Text>
      </div>

      <Space size={4} wrap>
        {legend.map((m, i) => (
          <Space key={`${m.label}-${i}`} size={3}>
            <span style={{ width: 8, height: 8, background: m.color, display: 'inline-block', borderRadius: 1 }} />
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>{m.label}</Typography.Text>
          </Space>
        ))}
        <Button size="small" onClick={() => { p.onPick('start'); refocusBar() }}>设为起点</Button>
        <Button size="small" onClick={() => { p.onPick('end'); refocusBar() }}>设为终点</Button>
        <Button size="small" type="link" style={{ padding: 0 }}
          onClick={() => { p.onRecenter(); refocusBar() }}>以播放头居中</Button>
      </Space>
    </Space>
  )
}

function stepBadge(step: StepStatus, current: string | null, state: string) {
  if (step.id === current && (state === 'running' || state === 'paused')) {
    return <Badge status="processing" text={<span>运行中</span>} />
  }
  if (step.done) return <Badge status="success" text="已完成" />
  return <Badge status="default" text={<span style={{ color: '#999' }}>未运行</span>} />
}

export default function PipelinePage() {
  const [status, setStatus] = useState<PipelineStatus | null>(null)
  const [lines, setLines] = useState<string[]>([])
  const [inputInfo, setInputInfo] = useState<InputInfo | null>(null)
  const [cookies, setCookies] = useState<CookieStatus | null>(null)
  /* 输入源草稿：懒加载只会在挂载时读一次 localStorage */
  const [initialDraft] = useState(loadInputDraft)
  const [url, setUrl] = useState(initialDraft.url)
  const [resolution, setResolution] = useState(initialDraft.resolution)
  const [fromStep, setFromStep] = useState<string | null>(null)
  const [toStep, setToStep] = useState<string | null>(null)
  const [runSubmitting, setRunSubmitting] = useState(false)
  const runSubmittingRef = useRef(false)
  // 用户一旦手动选过起点/终点，就不再被「烧录开关」的默认值覆盖
  const endTouched = useRef(false)
  const logRef = useRef<HTMLDivElement>(null)
  const [msgApi, ctxHolder] = message.useMessage()

  const refresh = useCallback(() => {
    getPipeline().then(setStatus).catch((e) => msgApi.error(String(e)))
  }, [msgApi])

  const refreshInput = useCallback(() => {
    getInput().then(setInputInfo).catch(() => setInputInfo(null))
  }, [])

  // Cookie 体检只在页面加载时查一次：读取浏览器 cookie 有开销，且配置是手改的
  useEffect(() => {
    getCookieStatus().then(setCookies).catch(() => setCookies(null))
  }, [])

  useEffect(refresh, [refresh])
  useEffect(refreshInput, [refreshInput])

  /* 每次输入都落盘；下载后同样保留（方便改分辨率重下）。
     点输入框的 × 清空时会写回空值，等于手动删除草稿。 */
  useEffect(() => {
    saveInputDraft({ url, resolution })
  }, [url, resolution])

  /* 默认值来自服务层：开始=转录；结束按烧录开关（关→对齐字幕，开→合并字幕到视频）。
     用户在设置页改了开关后，下一次刷新状态就会同步（除非手动选过结束步骤）。 */
  const defaultFrom = status?.defaults?.from_step
  const defaultTo = status?.defaults?.to_step
  useEffect(() => {
    if (!defaultFrom || !defaultTo) return
    setFromStep((prev) => prev ?? defaultFrom)
    if (!endTouched.current) setToStep(defaultTo)
  }, [defaultFrom, defaultTo])

  const onEvent = useCallback(
    (e: BusEvent) => {
      if (e.type === 'log' && e.line != null) {
        setLines((prev) => {
          const next = prev.length >= MAX_LOG_LINES ? prev.slice(prev.length - MAX_LOG_LINES + 1) : prev
          return [...next, e.line as string]
        })
      } else if (e.type === 'state' || e.type === 'step_start' || e.type === 'step_done') {
        refresh()
        refreshInput()
        if (e.type === 'step_done') refreshAd()
      } else if (e.type === 'history_saved') {
        // 归档结束：output/ 已清空 → 输入区和广告卡片状态要立刻跟着变
        refreshInput()
        // refreshAd / resetAdTask 是稳定引用，不必进 deps（它们在下方声明，
        // 进 deps 会因 TDZ 在渲染期直接崩）
        resetAdTask()
        const locked = Number(e.locked ?? 0)
        // 不能在这里直接复位 previewOff：此刻 adCtx.sources 还是归档前的旧
        // 列表，会用旧 src 立刻重新挂载 <video>，把刚清不掉的
        // output_sub.mp4 再锁一次（旧 bug：只能刷新页面再保存一次）。
        // 等 refreshAd() 拿回新 sources 再恢复；仍有残留文件时保持断流，
        // 不让页面自己把文件重新占用。只动 previewOff，不动 previewVisible：
        // 用户主动隐藏的预览不能被后台事件擅自打开。
        archiveHoldRef.current = true
        setPreviewHold(locked > 0)
        refreshAd().finally(() => {
          if (!locked) {
            archiveHoldRef.current = false
            setPreviewOff(false)
          }
          if (locked) {
            msgApi.warning(`已归档 ${e.moved ?? 0} 个文件，另有 ${locked} 个仍被占用`
              + '（多为外部播放器）；正在后台自动清出，预览暂时保持关闭')
          } else if (e.dest) {
            msgApi.success(`已归档 ${e.moved ?? 0} 个文件 → ${e.dest}`)
          }
        })
      } else if (e.type === 'history_cleaned') {
        // 后台把上次归档残留的占用文件清出：解除挂起、恢复自动预览
        if (!e.locked) {
          archiveHoldRef.current = false
          setPreviewHold(false)
          setPreviewOff(false)
          refreshAd()
          msgApi.success('残留文件已自动清出，output/ 已清空')
        } else {
          msgApi.warning(`仍有 ${e.locked} 个文件被占用，关掉占用程序后再保存一次即可清出`)
        }
      }
    },
    [refresh, refreshInput, msgApi],
  )

  const { connected } = useBus(onEvent)

  // ---- B站投稿文案（三件套：标题/简介/标签） ----
  const [biliCtx, setBiliCtx] = useState<BiliContext | null>(null)
  const [biliTitle, setBiliTitle] = useState('')
  const [biliDesc, setBiliDesc] = useState('')
  const [speaker, setSpeaker] = useState('')
  const speakerTouched = useRef(false)
  /* EP 手填 + 记住上次成功值：预填 = 上次 + 1 */
  const [ep, setEp] = useState<number | null>(() => {
    try {
      const v = localStorage.getItem('videolingo.bili.ep')
      return v ? Number(v) + 1 : null
    } catch {
      return null
    }
  })
  const [extraHint, setExtraHint] = useState('')
  const [generating, setGenerating] = useState(false)
  const [biliResult, setBiliResult] = useState<BiliMetaResult | null>(null)
  const [chosenTitle, setChosenTitle] = useState('')

  const refreshBili = useCallback(() => {
    getBiliContext()
      .then((c) => {
        setBiliCtx(c)
        setBiliTitle(c.title)
        setBiliDesc(c.description)
        if (!speakerTouched.current) setSpeaker(c.speaker_suggest)
      })
      .catch(() => setBiliCtx(null))
  }, [])

  /* 下载完成 / 转录产出 / 删除输入 / 归档清空 output 后，素材状态都要跟着变。
     任务结束时 status.state 会变，这时重查一次就能看到新生成的 trans.srt。 */
  useEffect(() => {
    refreshBili()
  }, [inputInfo?.path, status?.state, refreshBili])

  const doGenerateBili = () => {
    if (!ep || !speaker.trim()) return
    setGenerating(true)
    generateBiliMeta({
      ep,
      speaker: speaker.trim(),
      extra_hint: extraHint.trim() || undefined,
      title: biliTitle.trim() || undefined,
      description: biliDesc,
    })
      .then((res) => {
        setBiliResult(res)
        setChosenTitle(res.titles[0] ?? '')
        try {
          localStorage.setItem('videolingo.bili.ep', String(ep))
        } catch {
          /* 隐私模式下不记忆，无妨 */
        }
        msgApi.success(`已生成 ${res.ep_tag} 三件套 → ${res.saved_to}`)
      })
      .catch((e) => msgApi.error({ content: String(e), duration: 8 }))
      .finally(() => setGenerating(false))
  }

  const copyBili = (kind: 'title' | 'all') => {
    if (!biliResult) return
    const text = kind === 'title'
      ? chosenTitle
      : `${chosenTitle}\n\n${biliResult.desc}\n\n${biliResult.tags.join(', ')}`
    navigator.clipboard?.writeText(text).then(
      () => msgApi.success('已复制'),
      () => msgApi.error('复制失败（浏览器限制），请手动选择文本'),
    )
  }

  // ---- 广告位提醒：识别 → 视频预览里微调 → 按选中的文件裁剪 ----
  const [adCtx, setAdCtx] = useState<AdContext | null>(null)
  const [adSegments, setAdSegments] = useState<AdSegment[]>([])
  // 被手动删掉的识别段：重新识别不带回来，点「恢复已删除」加回
  const [adDismissed, setAdDismissed] = useState<AdSegment[]>([])
  // 有未保存编辑时刷新卡片不许用服务端旧 EDL 覆盖（删光最后一段会被读回来）
  const adDirtyRef = useRef(false)
  const [adHint, setAdHint] = useState('')
  const [adBusy, setAdBusy] = useState(false)
  const [adCutBusy, setAdCutBusy] = useState(false)
  const [adSource, setAdSource] = useState('burned')
  // Preserve explicit choices; automatic fallbacks follow the server default.
  const manualSourceRef = useRef<string | null>(null)
  const adSourceKeyRef = useRef('')
  const adInputRef = useRef<string | null | undefined>(undefined)
  const adContextRequestRef = useRef(0)
  const adPlayheadRef = useRef(0)
  const pendingSeekRef = useRef<number | null>(null)
  const [adSaved, setAdSaved] = useState('')
  const [adCutResult, setAdCutResult] = useState<{ saved_to: string; duration: number; removed: number } | null>(null)
  // 精细时间轴：局部窗口 + 帧级步进（原生 video 进度条太粗，一点跳好几秒）
  const [adPlayhead, setAdPlayhead] = useState(0)
  const [adAnchor, setAdAnchor] = useState(0)
  const [adZoom, setAdZoom] = useState(3)
  const [adFps, setAdFps] = useState(30)
  const [adFpsTouched, setAdFpsTouched] = useState(false)
  const [adFpsAuto, setAdFpsAuto] = useState(false)
  const [adMedia, setAdMedia] = useState<AdMediaInfo | null>(null)
  const [adSnap, setAdSnap] = useState(true)
  const [adPlaying, setAdPlaying] = useState(false)
  const [adActiveId, setAdActiveId] = useState<number | null>(null)
  const [adActiveWhich, setAdActiveWhich] = useState<'start' | 'end'>('start')
  const videoRef = useRef<HTMLVideoElement>(null)
  /* 预览视频被主动断开（归档/重跑前）：output_sub.mp4 正被 <video> 流式
     加载时，服务端 FileResponse 持有读句柄，Windows 不许删/改名被占用的
     文件 —— 必须先撤掉 src 让浏览器中止流、服务端关句柄。 */
  const [previewOff, setPreviewOff] = useState(false)
  /* 归档结束后挂起「自动恢复预览」：先用新 sources 刷新，再决定是否重新
     挂载播放器；残留文件仍被占用时一直挂起，避免页面把它重新锁住。 */
  const [previewHold, setPreviewHold] = useState(false)
  const archiveHoldRef = useRef(false)
  const [previewVisible, setPreviewVisible] = useState(() => {
    try { return localStorage.getItem('videolingo.ad.previewVisible') !== 'false' }
    catch { return true }
  })
  const adTaskBusy = runSubmitting || status?.state === 'running' || status?.state === 'paused'
  const adSources = adCtx?.sources ?? []
  const adCurrentSource = adSources.find((s) => s.id === adSource) ?? null
  const adSourceVersion = adCurrentSource?.version
  adPlayheadRef.current = adPlayhead

  useEffect(() => {
    try { localStorage.setItem('videolingo.ad.previewVisible', String(previewVisible)) }
    catch { /* Storage is optional. */ }
  }, [previewVisible])

  const releasePreview = useCallback(() => {
    const v = videoRef.current
    if (v) {
      if (v.readyState > 0 && pendingSeekRef.current == null) {
        pendingSeekRef.current = v.currentTime
      }
      try { v.pause() } catch { /* 已停止时可能抛错，忽略 */ }
      v.removeAttribute('src')
      v.load()
    }
    setAdPlaying(false)
    setPreviewOff(true)
  }, [])

  const resetAdTask = useCallback(() => {
    manualSourceRef.current = null
    adSourceKeyRef.current = ''
    pendingSeekRef.current = null
    adPlayheadRef.current = 0
    adDirtyRef.current = false
    setAdPlayhead(0)
    setAdAnchor(0)
    setAdActiveId(null)
    setAdFpsTouched(false)
    setAdMedia(null)
    setAdSaved('')
    setAdCutResult(null)
  }, [])

  const changeAdSource = useCallback((id: string, version?: string) => {
    const key = `${id}:${version ?? ''}`
    if (adSourceKeyRef.current === key) return
    if (adSourceKeyRef.current && pendingSeekRef.current == null) {
      const v = videoRef.current
      pendingSeekRef.current = v && v.readyState > 0 ? v.currentTime : adPlayheadRef.current
    }
    videoRef.current?.pause()
    setAdPlaying(false)
    adSourceKeyRef.current = key
    setAdSource(id)
  }, [])

  useEffect(() => {
    if (status?.state === 'running' || status?.state === 'paused') releasePreview()
    // 归档刚结束时 state 也会回到空闲，别抢在 refreshAd() 之前把播放器挂回去
    else if (!archiveHoldRef.current && !previewHold) setPreviewOff(false)
  }, [status?.state, releasePreview, previewHold])

  const refreshAd = useCallback(async () => {
    const request = ++adContextRequestRef.current
    try {
      const c = await getAdContext()
      if (request !== adContextRequestRef.current) return
      const input = c.video || null
      if (adInputRef.current !== input) {
        resetAdTask()
        adInputRef.current = input
      }
      const sources = c.sources ?? []
      // 残留文件清出后 output/ 已无可预览视频 → 解除归档挂起，恢复自动预览
      if (!sources.length) setPreviewHold(false)
      const manual = manualSourceRef.current
      if (manual && !sources.some((s) => s.id === manual)) manualSourceRef.current = null
      const next = manualSourceRef.current ?? c.default_source ?? sources[0]?.id ?? 'burned'
      changeAdSource(next, sources.find((s) => s.id === next)?.version)
      setAdCtx(c)
      // Keep unsaved edits while refreshing the same task.
      if (!adDirtyRef.current) {
        setAdSegments(c.edl?.segments ?? [])
        setAdDismissed(c.edl?.dismissed ?? [])
      }
    } catch {
      if (request === adContextRequestRef.current) setAdCtx(null)
    }
  }, [changeAdSource, resetAdTask, setPreviewHold])

  const pickAdSource = (id: string) => {
    manualSourceRef.current = id
    changeAdSource(id, adSources.find((s) => s.id === id)?.version)
  }

  const toggleAdPreview = (visible: boolean) => {
    if (!visible) releasePreview()
    else {
      if (adTaskBusy || !adCurrentSource) return
      pendingSeekRef.current ??= adPlayheadRef.current
      // 用户主动打开 = 明确解除归档挂起（关掉占用程序后自己决定恢复）
      archiveHoldRef.current = false
      setPreviewHold(false)
      setPreviewOff(false)
    }
    setPreviewVisible(visible)
  }

  useEffect(() => {
    refreshAd()
  }, [inputInfo?.path, status?.state, refreshAd])

  const patchSeg = (id: number, patch: Partial<AdSegment>) => {
    adDirtyRef.current = true
    setAdSegments((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)))
  }

  /** 吸附到真实帧网格：ffmpeg trim 也是按帧切的，秒数对齐后所见即所得 */
  const snapFrame = (t: number) => {
    const v = Math.max(0, Number(t) || 0)
    return adSnap ? Math.round(v * adFps) / adFps : Math.round(v * 1000) / 1000
  }

  /** 精确定位：默认暂停（跳到边界是为了看那一帧，不是继续播） */
  const seekExact = (t: number, recenter = false, play = false) => {
    const v = videoRef.current
    const target = snapFrame(t)
    if (v) {
      v.pause()
      setAdPlaying(false)
      if (v.readyState === 0) pendingSeekRef.current = target
      else {
        pendingSeekRef.current = null
        v.currentTime = target
      }
      if (play) v.play().catch(() => { /* 自动播放可能被拦截 */ })
    } else pendingSeekRef.current = target
    setAdPlayhead(target)
    if (recenter) setAdAnchor(target)
  }

  /** 表格「跳到」：把局部时间轴窗口搬到这个边界，并让方向键立即生效 */
  const focusBoundary = (id: number, which: 'start' | 'end', t: number) => {
    setAdActiveId(id)
    setAdActiveWhich(which)
    seekExact(t, true)
    window.setTimeout(() => document.getElementById('ad-scrub-bar')?.focus(), 0)
  }

  // 从播放器读基准，连按方向键/长按不会累积漂移
  const nudge = (delta: number) => {
    const v = videoRef.current
    seekExact((v ? v.currentTime : adPlayhead) + delta)
  }
  const togglePlay = () => {
    const v = videoRef.current
    if (!v) return
    if (v.paused) v.play().catch(() => { /* 忽略 */ })
    else v.pause()
  }

  const setFromPlayer = (id: number, which: 'start' | 'end') => {
    const v = videoRef.current
    const t = snapFrame(v ? v.currentTime : adPlayhead)
    patchSeg(id, { [which]: t })
    setAdActiveId(id)
    setAdActiveWhich(which)
    msgApi.success(`已设为${which === 'start' ? '起点' : '终点'} ${fmtClock(t, true)}（第 ${Math.round(t * adFps)} 帧）`)
  }

  // 播放时 ~20fps 更新播放头（比 timeupdate 的 4Hz 顺滑，又不至于每帧重渲染整页）
  useEffect(() => {
    if (!adPlaying) return
    let raf = 0
    let last = 0
    const tick = (now: number) => {
      if (now - last >= 50) {
        last = now
        const v = videoRef.current
        if (v) setAdPlayhead(v.currentTime)
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [adPlaying])

  // 播放头走出窗口边缘才滚动窗口，拖动微调时窗口保持不动
  useEffect(() => {
    if (!adPlaying) return
    if (adPlayhead > adAnchor + adZoom * 0.92 || adPlayhead < adAnchor - adZoom * 0.92) {
      setAdAnchor(adPlayhead)
    }
  }, [adPlayhead, adPlaying, adAnchor, adZoom])

  // Probe only settled files; ignore responses for a previous source/version.
  useEffect(() => {
    if (!adCurrentSource || adTaskBusy) return
    let cancelled = false
    setAdMedia(null)
    setAdFpsAuto(false)
    getAdMediaInfo(adSource, adSourceVersion)
      .then((info) => {
        if (cancelled || !info) return
        setAdMedia(info)
        if (info.fps > 0) {
          setAdFpsAuto(true)
          if (!adFpsTouched) setAdFps(info.fps)
        }
      })
      .catch(() => { /* Keep the manual/default frame rate. */ })
    return () => { cancelled = true }
  }, [adSource, adSourceVersion, !!adCurrentSource, adTaskBusy, adFpsTouched])

  const doDetect = () => {
    setAdBusy(true)
    detectAds(adHint.trim())
      .then((res) => {
        setAdSegments(res.segments)
        setAdDismissed(res.dismissed ?? [])
        adDirtyRef.current = false
        setAdSaved(res.saved_to)
        setAdCutResult(null)
        msgApi.success(`识别到 ${res.segments.length} 段赞助 → ${res.saved_to}`)
      })
      .catch((e) => msgApi.error({ content: String(e), duration: 8 }))
      .finally(() => setAdBusy(false))
  }

  const doSave = () => {
    setAdBusy(true)
    saveAdSegments(adSegments)
      .then((edl) => {
        setAdSegments(edl.segments)
        setAdDismissed(edl.dismissed ?? [])
        adDirtyRef.current = false
        msgApi.success(edl.segments.length ? '区间已保存到 EDL' : '已清空所有切点')
      })
      .catch((e) => msgApi.error({ content: String(e), duration: 8 }))
      .finally(() => setAdBusy(false))
  }

  // ---- FR-30 自定义切点：片头 / 片尾 / 中间任意段，与广告段共用一张 EDL ----
  const adDuration = adMedia?.duration ?? 0

  const makeManualSeg = (start: number, end: number, note: string): AdSegment => {
    const a = Math.max(0, Number(start.toFixed(3)))
    const b = Math.max(a + 0.1, Number(end.toFixed(3)))
    return {
      id: Math.max(0, ...adSegments.map((s) => s.id)) + 1,
      brand: '手动切点', note, confidence: 'manual', enabled: true,
      start: a, end: b, transcript_start: a, transcript_end: b,
      prev_end: null, next_start: null,
      start_line: 0, end_line: 0, start_gap: 0,
      start_basis: 'manual', end_basis: 'manual', scene_cuts: [],
    }
  }

  const addManualSeg = (kind: 'custom' | 'head' | 'tail') => {
    const v = videoRef.current
    const now = v ? v.currentTime : adPlayhead
    if (kind !== 'custom' && (!adDuration || now < 0.1)) {
      msgApi.warning('先在预览里把播放头拖到要切的位置')
      return
    }
    const seg = kind === 'head'
      ? makeManualSeg(0, now, '片头')
      : kind === 'tail'
        ? makeManualSeg(now, adDuration, '片尾')
        : makeManualSeg(now, Math.min(adDuration || now + 30, now + 30), '中间片段')
    adDirtyRef.current = true
    setAdSegments((prev) => [...prev, seg])
    setAdActiveId(seg.id)
    setAdActiveWhich('start')
    seekExact(seg.start, true)
    msgApi.success(`已添加${seg.note}切点 ${fmtClock(seg.start, true)} – ${fmtClock(seg.end, true)}`)
  }

  /** FR-32：识别出来的广告段也能删；删掉的自动段记进 dismissed，
   *  重新识别不会自行回来，点「恢复已删除」才加回。 */
  const removeSeg = (id: number) => {
    const gone = adSegments.find((s) => s.id === id)
    const auto = gone && gone.confidence !== 'manual' && gone.start_basis !== 'manual'
    adDirtyRef.current = true
    if (auto && gone) {
      setAdDismissed((prev) => prev.some((x) => x.brand === gone.brand
        && Math.abs(x.start - gone.start) < 0.6 && Math.abs(x.end - gone.end) < 0.6)
        ? prev : [...prev, gone])
    }
    setAdSegments((prev) => prev.filter((s) => s.id !== id))
    if (adActiveId === id) setAdActiveId(null)
  }

  const restoreDismissed = () => {
    if (!adDismissed.length) return
    const maxId = Math.max(0, ...adSegments.map((s) => s.id))
    const restored = adDismissed.map((s, i) => ({ ...s, id: maxId + 1 + i, enabled: true }))
    const next = [...adSegments, ...restored].sort((a, b) => a.start - b.start)
    setAdSegments(next)
    setAdDismissed([])
    adDirtyRef.current = true
    setAdBusy(true)
    saveAdSegments(next, [])
      .then((edl) => {
        setAdSegments(edl.segments)
        setAdDismissed(edl.dismissed ?? [])
        adDirtyRef.current = false
        msgApi.success(`已恢复 ${restored.length} 段识别结果`)
      })
      .catch((e) => msgApi.error({ content: String(e), duration: 8 }))
      .finally(() => setAdBusy(false))
  }

  const doCut = () => {
    if (adTaskBusy || !adCurrentSource || adCutBusy) return
    setAdCutBusy(true)
    cutVideo(adSource, adSegments)
      .then((r) => {
        adDirtyRef.current = false   // 裁剪接口已把页面区间回写 EDL
        setAdCutResult(r)
        msgApi.success(`无广告成片已生成 → ${r.saved_to}`)
      })
      .catch((e) => msgApi.error({ content: String(e), duration: 10 }))
      .finally(() => setAdCutBusy(false))
  }

  const adColumns: ColumnsType<AdSegment> = [
    {
      title: '裁剪', dataIndex: 'enabled', width: 58,
      render: (_, r) => (
        <Checkbox checked={r.enabled}
          onChange={(e) => patchSeg(r.id, { enabled: e.target.checked })} />
      ),
    },
    {
      title: '品牌', dataIndex: 'brand', width: 150,
      render: (_, r) => {
        const manual = r.confidence === 'manual' || r.start_basis === 'manual'
        return (
          <Space direction="vertical" size={0}>
            <Space size={4}>
              <Typography.Text strong style={{ fontSize: 12 }}>{r.brand}</Typography.Text>
              {manual && <Tag color="purple" style={{ fontSize: 10, margin: 0 }}>手动</Tag>}
            </Space>
            <Space size={2}>
              {!manual && (
                <Tag color={r.confidence === 'high' ? 'green' : r.confidence === 'medium' ? 'gold' : 'default'}
                  style={{ fontSize: 10, margin: 0 }}>{r.confidence}</Tag>
              )}
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>{r.note}</Typography.Text>
              <Popconfirm
                title={manual ? '删除这个手动切点？' : `删除识别出的「${r.brand}」？`}
                description={manual ? undefined
                  : '保存修改后不再参与裁剪；重新识别也不会自动加回，可用「恢复已删除」找回。'}
                okText="删除" cancelText="取消"
                onConfirm={() => removeSeg(r.id)}>
                <Button size="small" type="link" danger icon={<DeleteOutlined />}
                  title={manual ? '删除切点' : '删除这段识别结果'}
                  style={{ padding: 0 }} />
              </Popconfirm>
            </Space>
          </Space>
        )
      },
    },
    {
      title: '开始（秒）', dataIndex: 'start', width: 250,
      render: (_, r) => (
        <Space direction="vertical" size={2}>
          <Space size={2}>
            <InputNumber size="small" min={0} max={Number((r.end - 0.1).toFixed(3))}
              step={Number((1 / adFps).toFixed(3))} precision={3} style={{ width: 116 }} value={r.start}
              onChange={(n) => { if (n !== null) patchSeg(r.id, { start: Number(n) }) }} />
            <Button size="small" type="link" style={{ padding: 0 }} onClick={() => focusBoundary(r.id, 'start', r.start)}>跳到</Button>
            <Button size="small" type="link" style={{ padding: 0 }}
              onClick={() => setFromPlayer(r.id, 'start')}>取当前</Button>
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            {fmtClock(r.start, true)}
            {r.start_basis === 'manual' ? ' · 手动指定' : ` · 转录开口 ${fmtClock(r.transcript_start, true)}`}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '结束（秒）', dataIndex: 'end', width: 210,
      render: (_, r) => (
        <Space direction="vertical" size={2}>
          <Space size={2}>
            <InputNumber size="small" min={Number((r.start + 0.1).toFixed(3))}
              step={Number((1 / adFps).toFixed(3))} precision={3} style={{ width: 116 }} value={r.end}
              onChange={(n) => { if (n !== null) patchSeg(r.id, { end: Number(n) }) }} />
            <Button size="small" type="link" style={{ padding: 0 }} onClick={() => focusBoundary(r.id, 'end', r.end)}>跳到</Button>
            <Button size="small" type="link" style={{ padding: 0 }}
              onClick={() => setFromPlayer(r.id, 'end')}>取当前</Button>
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>{fmtClock(r.end, true)}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '时长', width: 78,
      render: (_, r) => <Typography.Text style={{ fontSize: 12 }}>{(r.end - r.start).toFixed(1)}s</Typography.Text>,
    },
    {
      title: '起点依据', dataIndex: 'start_basis', width: 130,
      render: (_, r) => (
        <Space direction="vertical" size={0}>
          <Tag color={r.start_basis === 'manual' ? 'purple' : 'blue'} style={{ fontSize: 10, margin: 0 }}>
            {AD_BASIS[r.start_basis] ?? r.start_basis}
          </Tag>
          {r.start_basis !== 'manual' && (
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              比转录早 {(r.transcript_start - r.start).toFixed(2)}s
            </Typography.Text>
          )}
        </Space>
      ),
    },
  ]

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [lines])

  const state = status?.state ?? 'idle'
  const active = state === 'running' || state === 'paused'
  const runBusy = active || runSubmitting
  const biliTr: BiliTranscript | null = biliCtx?.transcript ?? null
  const transcriptReady = !!biliTr?.available
  const adReady = !!adCtx?.transcript.available
  const adVideoReady = previewVisible && !previewOff && !previewHold
    && !adTaskBusy && !!adCurrentSource
  const adCurrentSubtitled = !!adCurrentSource?.subtitled
  const adSubtitledSource = adSources.find((s) => s.subtitled) ?? null
  // 精细时间轴上要画的标记：正文末词 / 画面切点 / 广告开口 / 裁剪区间
  const adActiveSeg = adSegments.find((s) => s.id === adActiveId) ?? null
  const adMarks: ScrubMark[] = []
  const pushMark = (t: number | null | undefined, label: string, color: string, strong = false) => {
    if (typeof t === 'number' && Number.isFinite(t) && t >= 0) adMarks.push({ t, label, color, strong })
  }
  if (adActiveSeg) {
    const manual = adActiveSeg.confidence === 'manual' || adActiveSeg.start_basis === 'manual'
    if (!manual) {
      pushMark(adActiveSeg.prev_end, '上一句正文末词', '#52c41a')
      const cuts = adActiveSeg.scene_cuts ?? []
      cuts.forEach((c, i) => pushMark(c, `画面切点 ${i + 1}`, '#722ed1'))
      pushMark(adActiveSeg.transcript_start, '广告开口', '#ff4d4f')
      pushMark(adActiveSeg.next_start, '下一句正文', '#8c8c8c')
    }
    pushMark(adActiveSeg.start, '裁剪起点', '#fa8c16', true)
    pushMark(adActiveSeg.end, '裁剪终点', '#fa8c16', true)
  } else {
    adSegments.forEach((s) => {
      pushMark(s.start, `${s.brand} 起点`, '#fa8c16', true)
      pushMark(s.end, `${s.brand} 终点`, '#fa8c16')
    })
  }
  const adActiveLabel = adActiveSeg
    ? `${adActiveSeg.brand} · ${adActiveWhich === 'start' ? '起点' : '终点'}`
    : ''
  const adFpsOptions = Array.from(new Set([23.976, 24, 25, 29.97, 30, 50, 60, adFps])).sort((a, b) => a - b)
  const adFpsSource: 'auto' | 'manual' | 'fallback' =
    adFpsTouched ? 'manual' : adFpsAuto ? 'auto' : 'fallback'
  const steps = status?.steps ?? []
  const ids = steps.map((s) => s.id)
  /* 开始/结束都可手选，默认值由后端按设置算好（开始固定「转录」）。
     起点越过终点时自动把终点提到起点，避免提交非法范围。 */
  const startId = (fromStep && ids.includes(fromStep))
    ? fromStep
    : (defaultFrom && ids.includes(defaultFrom) ? defaultFrom : ids[0] ?? null)
  const endId = (toStep && ids.includes(toStep))
    ? toStep
    : (defaultTo && ids.includes(defaultTo) ? defaultTo : ids[ids.length - 1] ?? null)
  const startIdx = startId ? ids.indexOf(startId) : -1
  const endIdx = endId ? ids.indexOf(endId) : -1
  const rangeValid = startIdx >= 0 && endIdx >= startIdx
  const startTitle = steps[startIdx]?.title ?? '—'
  const endTitle = steps[endIdx]?.title ?? '—'
  const runRange = { from_step: startId as string, to_step: endId as string }
  const onStartChange = (v: string) => {
    setFromStep(v)
    // 手选了起点 = 接管整个范围，别再让默认值把终点改回去（可能变成非法范围）
    endTouched.current = true
    if (endIdx >= 0 && ids.indexOf(v) > endIdx) setToStep(v)
  }

  /* 「完整跑阶段一」是固定快捷方式：从转录跑到【默认】结束步骤，
     不受顶部两个下拉的临时选择影响。不烧录时默认停在「对齐字幕」，
     不会去生成 0 秒黑屏占位视频。 */
  const subSteps = steps.filter((s) => s.stage === 'sub')
  const stageFirstId = subSteps[0]?.id
  const stageEndId = (defaultTo && subSteps.some((s) => s.id === defaultTo))
    ? defaultTo
    : subSteps[subSteps.length - 1]?.id
  const stageRange = stageFirstId && stageEndId
    ? { from_step: stageFirstId, to_step: stageEndId }
    : null
  const stageStartTitle = subSteps[0]?.title ?? '转录 whisperX'
  const stageEndTitle = steps.find((s) => s.id === stageEndId)?.title ?? '—'
  const stageDisabled = runBusy || !stageRange || !inputInfo?.exists || inputInfo.ambiguous

  const act = async (fn: () => Promise<void>) => {
    try {
      await fn()
      refresh()
    } catch (e) {
      msgApi.error(String(e))
    }
  }

  const submitRun = async (body: RunPipelineBody) => {
    if (active || runSubmittingRef.current) return
    runSubmittingRef.current = true
    setRunSubmitting(true)
    // force 会删产物（含 output_sub.mp4）：先断开预览流，否则文件被
    // 服务端句柄占用，清理失败还烧钱
    releasePreview()
    try {
      // 先让后端关掉预览读句柄，再提交 force 清理：句柄没关就删不掉
      // output_sub.mp4，force 重跑会拿旧产物继续跑还白烧 LLM 费用
      await releasePreviewStreams()
      await runPipeline(body)
      setStatus(await getPipeline())
    } catch (e) {
      msgApi.error(String(e))
      refresh()
      // 提交失败＝任务没启动，立即恢复预览；成功时交给 status.state 驱动
      // 的 useEffect：运行期间保持断流，结束后才恢复，避免抢锁待写文件
      setPreviewOff(false)
    } finally {
      runSubmittingRef.current = false
      setRunSubmitting(false)
    }
  }

  const doDownload = () =>
    act(async () => {
      const r = await downloadVideo(url, resolution)
      msgApi.success(r.message || '下载任务已启动')
      // 地址不清空：草稿要一直在，方便改分辨率重下；手动点 × 才清掉
      refreshInput()
    })

  const doClearInput = () =>
    act(async () => {
      await clearInput(true)
      refreshInput()
    })

  /* 归档：产物搬到 history/<视频名>/，output/ 目录保留、内容清空 */
  const doSaveHistory = () =>
    act(async () => {
      releasePreview()
      try {
        // 归档 job 里后端也会兜底关一次；这里先关，保证 job 启动前句柄已释放
        await releasePreviewStreams()
        const r = await saveHistory()
        msgApi.success(r.message || '已开始保存历史记录')
        refreshInput()
      } catch (e) {
        setPreviewOff(false)
        throw e
      }
    })

  /* 完整日志同时落盘（页面日志刷新就没了），这里直接下载那个文件 */
  const doDownloadLog = () =>
    act(async () => {
      const path = status?.log_file
      if (!path) return
      const lines = await tailLog(path)
      if (!lines.length) {
        msgApi.warning('日志文件为空 —— 先跑一个步骤')
        return
      }
      const blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `videolingo-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')}.log`
      a.click()
      URL.revokeObjectURL(a.href)
    })

  const inputName = inputInfo?.path ? inputInfo.path.split('/').pop() : null

  const stages: { key: 'sub' | 'dub'; label: string }[] = [
    { key: 'sub', label: '阶段一 · 字幕' },
    { key: 'dub', label: '阶段二 · 配音' },
  ]

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {ctxHolder}

      <Card size="small" title={<Space size={6}><YoutubeOutlined /><span>输入源</span></Space>}>
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Space wrap size={8}>
            <Input
              allowClear
              style={{ width: 470 }}
              placeholder="粘贴视频链接（YouTube / Bilibili / X 等，yt-dlp 支持的站点）"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onPressEnter={doDownload}
              disabled={runBusy}
            />
            <Select
              style={{ width: 120 }}
              value={resolution}
              onChange={setResolution}
              disabled={runBusy}
              options={[
                { value: '1080', label: '1080p' },
                { value: '720', label: '720p' },
                { value: '480', label: '480p' },
                { value: '360', label: '360p' },
                { value: 'best', label: '最佳画质' },
              ]}
            />
            <Button
              type="primary"
              icon={<DownloadOutlined />}
              disabled={runBusy || !url.trim()}
              onClick={doDownload}
            >
              下载
            </Button>
            {inputInfo?.exists && (
              <Popconfirm
                title="删除当前输入文件？"
                description={`将删除 ${inputName ?? ''}，此操作不可撤销`}
                okText="删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={doClearInput}
              >
                <Button danger icon={<ClearOutlined />} disabled={runBusy}>
                  删除输入
                </Button>
              </Popconfirm>
            )}
          </Space>

          <Space size={6} wrap>
            {inputInfo?.ambiguous ? (
              <Tooltip title={inputInfo.message ?? ''}>
                <Tag color="red">输入不唯一，请先清理 output/ 目录</Tag>
              </Tooltip>
            ) : inputInfo?.exists ? (
              <>
                <Tag color="blue">{inputInfo.type === 'audio' ? '音频' : '视频'}</Tag>
                <Typography.Text code>{inputName}</Typography.Text>
              </>
            ) : (
              <Tag>尚未选择输入文件 — 可下载，或手动放进 output/ 目录</Tag>
            )}
          </Space>

          {/* 登录态 cookie 缺失 = YouTube 必报 bot 挑战，下载前就先说清楚 */}
          {cookies && cookies.verdict !== 'ok' && cookies.hint && (
            <Alert
              type="warning"
              showIcon
              message={`Cookie 状态：${cookies.verdict}`}
              description={
                <Space direction="vertical" size={2}>
                  <span>{cookies.hint}</span>
                  {cookies.cookies_path && (
                    <Typography.Text type="secondary">
                      文件：{cookies.cookies_path}
                      {cookies.cookies_file_exists ? '（存在）' : '（不存在）'}
                      {cookies.browser ? ` · 浏览器：${cookies.browser}` : ''}
                    </Typography.Text>
                  )}
                </Space>
              }
            />
          )}
        </Space>
      </Card>

      {/* 广告位 / 自定义切点：识别 + 手动 → 视频预览里微调起止 → 按选中的文件裁剪 */}
      <Card
        size="small"
        title={<Space size={6}><WarningOutlined /><span>广告位 / 自定义切点</span></Space>}
        extra={
          <Space size={4}>
            <Tooltip title="广告识别以 trans.srt 转录文字为依据；起点由本地 ffmpeg 找画面切点，不额外调用模型">
              <Tag color={adReady ? 'green' : 'red'}>
                {adReady ? `转录就绪 · ${adCtx?.transcript.lines ?? 0} 行` : '缺少转录'}
              </Tag>
            </Tooltip>
            <Tooltip title={adCtx?.scene_cut_ready
              ? '找到输入视频：可在过场空隙里用画面切点定准起点'
              : '没找到输入视频：边界自动退回「上一句正文结束 + padding」'}>
              <Tag color={adCtx?.scene_cut_ready ? 'blue' : 'orange'}>
                {adCtx?.scene_cut_ready ? '画面切点定点' : '无视频 · 保守定点'}
              </Tag>
            </Tooltip>
            <Button size="small" type="text" icon={<ReloadOutlined />}
              title="重新检查转录与视频" onClick={refreshAd} />
          </Space>
        }
      >
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          {!adReady && (
            <Alert type="info" showIcon message="还没有转录结果，自动识别广告位不可用"
              description="自动识别要以 trans.srt 为依据（先跑到「对齐字幕」再刷新）；但手动切点、预览微调、保存和裁剪都不需要转录，可直接使用。" />
          )}
          <Space wrap size={8}>
            <Input style={{ width: 250 }} placeholder="补充要求（可选），如：只标中插、忽略片尾"
              value={adHint} onChange={(e) => setAdHint(e.target.value)} />
            <Button type="primary" icon={<WarningOutlined />} loading={adBusy}
              disabled={!adReady || adBusy || adCutBusy} onClick={doDetect}>
              识别广告位
            </Button>
            <Button icon={<SaveOutlined />} loading={adBusy}
              disabled={adBusy || adCutBusy} onClick={doSave}>
              {adSegments.length ? '保存修改' : '保存（清空）'}
            </Button>
            {adDismissed.length > 0 && (
              <Button size="small" disabled={adBusy || adCutBusy}
                onClick={restoreDismissed}>
                恢复已删除的 {adDismissed.length} 段
              </Button>
            )}
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {adSegments.length
                ? `已标 ${adSegments.length} 段`
                : adDismissed.length ? '无待裁剪区间（删除的识别段可恢复）' : '无待裁剪区间'}
            </Typography.Text>
          </Space>

          {/* FR-30：不跑 LLM 也能手动挖洞，片头/片尾/中间共用一张 EDL */}
          <Space wrap size={6}>
            <Button size="small" icon={<PlusOutlined />}
              disabled={!adVideoReady || adBusy || adCutBusy}
              onClick={() => addManualSeg('custom')}>+ 添加切点</Button>
            <Tooltip title="把播放头拖到片头结束处，自动填 [0, 当前播放头]">
              <Button size="small" disabled={!adVideoReady || adBusy || adCutBusy}
                onClick={() => addManualSeg('head')}>切掉片头到当前</Button>
            </Tooltip>
            <Tooltip title="把播放头拖到片尾开始处，自动填 [当前播放头, 片尾]">
              <Button size="small" disabled={!adVideoReady || adBusy || adCutBusy}
                onClick={() => addManualSeg('tail')}>从当前切到片尾</Button>
            </Tooltip>
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              {adDuration > 0
                ? `源片时长 ${fmtClock(adDuration)} · 播放头 ${fmtClock(adPlayhead, true)}`
                : '正在读取源片时长…'}
            </Typography.Text>
          </Space>

          <Space wrap size={8}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>预览 / 裁剪视频</Typography.Text>
            <Select aria-label="预览 / 裁剪视频" style={{ width: 300 }}
              value={adCurrentSource?.id} onChange={pickAdSource}
              placeholder="暂无可用视频" disabled={!adSources.length || adTaskBusy || adCutBusy}
              options={adSources.map((s) => ({ value: s.id, label: s.label }))} />
            <Space size={6}>
              <Switch aria-label="显示预览" size="small" checked={previewVisible}
                disabled={!previewVisible && (adTaskBusy || !adCurrentSource)}
                onChange={toggleAdPreview} />
              <Typography.Text style={{ fontSize: 12 }}>显示预览</Typography.Text>
            </Space>
            {adCurrentSource && (
              <Tag color={adCurrentSubtitled ? 'green' : 'orange'}>
                {adCurrentSubtitled ? '画面带字幕 · 可边看边切' : '画面无字幕'}
              </Tag>
            )}
            {!adCurrentSubtitled && adSubtitledSource && (
              <Button size="small" disabled={adTaskBusy || adCutBusy}
                onClick={() => pickAdSource(adSubtitledSource.id)}>
                切到带字幕版
              </Button>
            )}
            {!!adSources.length && !adSubtitledSource && (
              <Typography.Text type="warning" style={{ fontSize: 12 }}>
                尚无字幕视频，完成「合并字幕到视频」后即可选择
              </Typography.Text>
            )}
          </Space>

          {adVideoReady && (
            <video ref={videoRef} controls preload="metadata"
              onPlay={() => setAdPlaying(true)}
              onPause={() => setAdPlaying(false)}
              onLoadedMetadata={(e) => {
                const t = pendingSeekRef.current ?? adPlayheadRef.current
                const d = e.currentTarget.duration
                const target = Number.isFinite(d) ? Math.min(t, Math.max(0, d - 0.05)) : t
                if (target > 0) e.currentTarget.currentTime = target
                setAdPlayhead(target)
                pendingSeekRef.current = null
              }}
              onTimeUpdate={(e) => setAdPlayhead(e.currentTarget.currentTime)}
              onSeeked={(e) => {
                const t = e.currentTarget.currentTime
                setAdPlayhead(t)
                // 用原生进度条跳到远处时，局部窗口自动跟过去（剪映式跟随）
                if (Math.abs(t - adAnchor) > adZoom) setAdAnchor(t)
              }}
              style={{ width: '100%', maxHeight: 340, background: '#000', borderRadius: 6 }}
              src={adPreviewUrl(adSource, adSourceVersion)} />
          )}
          {!adVideoReady && (
            <Alert type="info" showIcon
              message={!previewVisible ? '视频预览已隐藏'
                : previewHold ? '预览保持关闭，避免再次占用文件'
                  : adTaskBusy || previewOff ? '任务处理中，视频预览暂时关闭'
                    : '没有可预览的视频文件'}
              description={!previewVisible
                ? '打开「显示预览」可恢复播放位置；隐藏不影响现有切点、保存和裁剪。'
                : previewHold
                  ? '归档时仍有文件被外部程序占用；关掉占用程序后重新打开「显示预览」即可恢复。'
                  : adTaskBusy || previewOff
                    ? '任务结束后自动恢复；仍可编辑和保存切点。'
                    : '完成「合并字幕到视频」后可选择带字幕版本。'} />
          )}

          {adVideoReady && (
            <div style={{ border: '1px solid #e5e7eb', borderRadius: 8, padding: 10, background: '#fff' }}>
              <FrameScrubber
                center={adAnchor} zoom={adZoom} playhead={adPlayhead} fps={adFps}
                fpsOptions={adFpsOptions} fpsSource={adFpsSource} snap={adSnap} playing={adPlaying}
                marks={adMarks} activeLabel={adActiveLabel}
                onSeek={(t) => seekExact(t)}
                onNudge={nudge}
                onZoom={setAdZoom}
                onSnap={setAdSnap}
                onFps={(f) => { setAdFps(f); setAdFpsTouched(true) }}
                onTogglePlay={togglePlay}
                onPick={(which) => {
                  if (adActiveSeg) setFromPlayer(adActiveSeg.id, which)
                  else msgApi.warning('先点表格里的「跳到」选中一个边界')
                }}
                onRecenter={() => setAdAnchor(adPlayhead)}
              />
            </div>
          )}

          {adVideoReady && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              原生进度条是整部片（一点跳好几秒），精调请用上方局部时间轴：拖到位置后用「+ 添加切点 /
              切掉片头 / 从当前切到片尾」建区间，或点表格「跳到」把窗口搬到边界 → ←/→ 逐帧、
              Shift 一秒、Alt 0.1 秒 → 「设为起点 / 终点」写回。
              轴上紫线是 ffmpeg 画面切点、红线是广告开口，改完记得保存或直接裁剪。
            </Typography.Text>
          )}
          {adSegments.length > 0 && (
            <Table<AdSegment> rowKey="id" size="small" pagination={false}
              dataSource={adSegments} columns={adColumns} scroll={{ x: 950 }} />
          )}

          {adSegments.length > 0 && (
            <Space wrap size={8}>
              <Popconfirm title="按当前区间裁剪并拼接？"
                description={`视频：${adCurrentSource?.label ?? adSource}。会重编码一次，长视频可能需要较长时间。`}
                onConfirm={doCut} okText="开始裁剪" cancelText="再看看">
                <Button type="primary" danger icon={<CaretRightOutlined />} loading={adCutBusy}
                  disabled={adCutBusy || adBusy || adTaskBusy || !adCurrentSource}>
                  按此裁剪
                </Button>
              </Popconfirm>
              <Typography.Text type={adCurrentSubtitled ? 'secondary' : 'warning'} style={{ fontSize: 12 }}>
                {adCurrentSubtitled
                  ? '字幕已烧进画面，切完直接成片，不用改 SRT 时间戳'
                  : `当前源 ${adCurrentSource?.label ?? adSource} 没有烧录字幕，切出来的成片也不会有字幕；要带字幕成片请切回烧录版`}
              </Typography.Text>
            </Space>
          )}

          {adSaved && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              表格已存 {adSaved} · EDL：output/ad_segments.json（自动裁剪的唯一输入，可在「产物」页下载）
            </Typography.Text>
          )}
          {adCutResult && (
            <Alert type="success" showIcon message="无广告成片已生成"
              description={`${adCutResult.saved_to} · 时长 ${adCutResult.duration.toFixed(1)}s`
                + `（去掉 ${adCutResult.removed.toFixed(1)}s 广告）`} />
          )}
          {!!adCtx?.sponsorblock?.length && (
            <Alert type="info" showIcon
              message={`SponsorBlock 社区标记 ${adCtx.sponsorblock.length} 段（仅交叉校验，不参与定点）`}
              description={adCtx.sponsorblock.map((s) =>
                `${fmtClock(s.start)}–${fmtClock(s.end)}`).join('，')} />
          )}
        </Space>
      </Card>

      {/* B站投稿文案：转录结果是一手素材，原视频信息只作参考 */}
      <Card
        size="small"
        title={<Space size={6}><FileTextOutlined /><span>B站投稿文案</span></Space>}
        extra={
          <Space size={4}>
            {biliCtx && (
              <Tooltip title={
                biliCtx.source === 'downloaded'
                  ? '次要参考：下载时自动保存的 YouTube 原标题/简介（output/video_meta.json），只用于校准主题和人名'
                  : biliCtx.source === 'filename'
                    ? '次要参考：本地视频的标题从文件名推断，简介可手动粘贴'
                    : 'output/ 里还没有视频，先下载或放入文件'
              }>
                <Tag color={biliCtx.source === 'downloaded' ? 'blue' : biliCtx.source === 'filename' ? 'orange' : 'default'}>
                  {biliCtx.source === 'downloaded' ? '原视频信息 · 仅参考' : biliCtx.source === 'filename' ? '文件名推断 · 仅参考' : '无参考'}
                </Tag>
              </Tooltip>
            )}
            <Tooltip title={biliTr && transcriptReady
              ? `决定性素材：${biliTr.label}（${biliTr.lang}）· ${biliTr.sampled
                  ? `正文 ${biliTr.total_chars} 字超过 ${biliTr.budget} 字预算，按时间均匀节选 ${biliTr.samples} 段、头尾加权`
                  : `全文直送 ${biliTr.total_chars} 字（未超 ${biliTr.budget} 字预算）`}${biliTr.has_summary ? ' · 附全局摘要' : ''}`
              : '还没有转录结果：标题必须以视频真实内容为依据'}>
              <Tag color={transcriptReady ? 'green' : 'red'}>
                {biliTr && transcriptReady
                  ? `转录就绪 · ${biliTr.source === 'trans_srt' ? 'trans.srt' : biliTr.source} · ${biliTr.sampled ? `节选 ${biliTr.samples} 段` : `全文 ${biliTr.total_chars} 字`}`
                  : '缺少转录'}
              </Tag>
            </Tooltip>
            <Button size="small" type="text" icon={<ReloadOutlined />}
              title="重新检查素材（跑完转录后点这里）" onClick={refreshBili} />
          </Space>
        }
      >
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          {!transcriptReady && (
            <Alert type="warning" showIcon message="还没有转录结果"
              description="文案以视频真实内容为依据：请先跑到第 6 步「对齐字幕」产出 trans.srt（中文译文，最佳素材）；或至少跑完第 2 步「转录 whisperX」。跑完点右上角刷新。" />
          )}
          {biliTr && transcriptReady && biliTr.sampled && (
            <Alert type="info" showIcon
              message={`转录 ${biliTr.total_chars} 字超过 ${biliTr.budget} 字预算，已按时间均匀节选 ${biliTr.samples} 段（头尾加权）${biliTr.has_summary ? ' + 全局摘要' : ''}`} />
          )}
          <Space wrap size={8}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>EP 序号</Typography.Text>
            <InputNumber min={1} max={999} style={{ width: 88 }}
              value={ep ?? undefined} onChange={(v) => setEp(v ?? null)} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>讲者</Typography.Text>
            <Input style={{ width: 170 }} placeholder="如 Matthew Hussey"
              value={speaker}
              onChange={(e) => { setSpeaker(e.target.value); speakerTouched.current = true }} />
            <Input style={{ width: 230 }} placeholder="补充要求（可选）"
              value={extraHint} onChange={(e) => setExtraHint(e.target.value)} />
            <Button type="primary" icon={<FileTextOutlined />}
              loading={generating}
              disabled={!ep || !speaker.trim() || generating || !transcriptReady}
              onClick={doGenerateBili}>
              生成文案
            </Button>
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            以下原视频信息仅作参考（校准主题 / 人名）；标题钩子、简介金句一律取自上方转录素材
          </Typography.Text>
          <Input
            placeholder="参考标题（来自 YouTube / 文件名，可修改）"
            value={biliTitle} onChange={(e) => setBiliTitle(e.target.value)} />
          <Input.TextArea
            rows={3}
            placeholder="参考简介（来自 YouTube；本地视频可粘贴简介或内容摘要）"
            value={biliDesc} onChange={(e) => setBiliDesc(e.target.value)} />

          {biliResult && (
            <Space direction="vertical" size={6} style={{ width: '100%' }}>
              <Radio.Group value={chosenTitle} onChange={(e) => setChosenTitle(e.target.value)}>
                <Space direction="vertical" size={2}>
                  {biliResult.titles.map((t) => (
                    <Radio key={t} value={t} style={{ whiteSpace: 'normal' }}>{t}</Radio>
                  ))}
                </Space>
              </Radio.Group>
              <Typography.Paragraph
                style={{ whiteSpace: 'pre-wrap', margin: 0, fontSize: 13, background: '#fafafa', padding: 8 }}>
                {biliResult.desc}
              </Typography.Paragraph>
              <Space wrap size={4}>
                {biliResult.tags.map((t) => <Tag key={t}>{t}</Tag>)}
              </Space>
              <Space size={8}>
                <Button size="small" icon={<CopyOutlined />} onClick={() => copyBili('title')}>
                  复制选中标题
                </Button>
                <Button size="small" icon={<CopyOutlined />} onClick={() => copyBili('all')}>
                  复制全部
                </Button>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {biliResult.ep_tag}｜{biliResult.speaker} · 已存 {biliResult.saved_to}
                  {biliResult.transcript && (
                    <> · 素材 {biliResult.transcript.label}
                      （{biliResult.transcript.sampled ? `节选 ${biliResult.transcript.samples} 段` : '全文'}）</>
                  )}
                </Typography.Text>
              </Space>
            </Space>
          )}
        </Space>
      </Card>

      <Card size="small">
        <Space wrap>
          <Tag color={connected ? 'green' : 'red'}>{connected ? '已连接' : '连接断开'}</Tag>
          <Tag>{state}</Tag>
          {status?.current_step && <Tag color="blue">{status.current_step}</Tag>}
          <Space size={4}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>开始</Typography.Text>
            <Select
              style={{ width: 165 }}
              placeholder="起始步骤"
              value={startId ?? undefined}
              onChange={onStartChange}
              disabled={runBusy}
              options={steps.map((s) => ({ value: s.id, label: s.title }))}
            />
          </Space>
          <Space size={4}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>结束</Typography.Text>
            <Tooltip title="默认跟随设置里的「烧录字幕到视频」：关闭时停在「对齐字幕」（生成 srt），开启时到「合并字幕到视频」">
              <Select
                style={{ width: 165 }}
                placeholder="结束步骤"
                value={endId ?? undefined}
                onChange={(v) => { endTouched.current = true; setToStep(v) }}
                disabled={runBusy}
                options={steps.map((s, i) => ({
                  value: s.id, label: s.title, disabled: i < startIdx,
                }))}
              />
            </Tooltip>
          </Space>
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            disabled={runBusy || !rangeValid}
            /* force 会删掉该范围的产物和断点（含 translate_ckpt.json），
               语义是"重做这些步骤"；这里是"接着跑"，必须能续跑。 */
            onClick={() => submitRun(runRange)}
          >
            从「{startTitle}」跑到「{endTitle}」
          </Button>
          {/* 强制版：先清掉所选范围内所有步骤的产物/断点/LLM 缓存再跑。
              普通版是"接着跑"，遇到已完成的守卫步骤会直接跳过。 */}
          <Popconfirm
            title={`强制重跑：从「${startTitle}」到「${endTitle}」？`}
            description={
              <span>
                会先删除所选范围内所有步骤的产物、断点与 LLM 缓存（翻译缓存也会失效，需要重新翻译），
                再从头跑到「{endTitle}」。已完成的步骤会被真正重做，不会跳过。
              </span>
            }
            okText="强制重跑"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            disabled={runBusy || !rangeValid}
            onConfirm={() => submitRun({ ...runRange, force: true })}
          >
            <Button danger icon={<RedoOutlined />} disabled={runBusy || !rangeValid}>
              强制重跑
            </Button>
          </Popconfirm>
          {state === 'running' && (
            <Button icon={<PauseOutlined />} onClick={() => act(() => control('pause'))}>
              暂停
            </Button>
          )}
          {state === 'paused' && (
            <Button icon={<CaretRightOutlined />} onClick={() => act(() => control('resume'))}>
              继续
            </Button>
          )}
          {active && (
            <Button danger icon={<StopOutlined />} onClick={() => act(() => control('stop'))}>
              停止
            </Button>
          )}
          <Button icon={<ClearOutlined />} onClick={() => setLines([])}>
            清空日志
          </Button>
          {/* 收尾动作：一条视频跑完就归档，output/ 清空但目录保留 */}
          <Popconfirm
            title="保存历史记录？"
            description={
              <span>
                把 output/ 里的成品、字幕、表格、日志与 LLM 缓存搬到
                history/&lt;视频名&gt;/，然后清空 output/（目录本身保留）。
                同一视频重复保存会覆盖旧归档。
              </span>
            }
            okText="保存"
            cancelText="取消"
            disabled={runBusy}
            onConfirm={doSaveHistory}
          >
            <Button icon={<SaveOutlined />} disabled={runBusy}>
              保存历史
            </Button>
          </Popconfirm>
        </Space>
      </Card>

      {stages.map(({ key, label }) => {
        const steps = status?.steps.filter((s) => s.stage === key) ?? []
        if (!steps.length) return null
        return (
          <Card
            key={key}
            size="small"
            title={
              <Space size={8} wrap>
                <span>{label}</span>
                {key === 'sub' && (
                  <>
                    <Tooltip title={`固定从「${stageStartTitle}」跑到「${stageEndTitle}」，跟随设置里的烧录开关，不受上方下拉选择影响；不带强制重跑，已有断点与缓存会保留`}>
                      <Button
                        size="small"
                        type="primary"
                        icon={<PlayCircleOutlined />}
                        disabled={stageDisabled}
                        onClick={() => stageRange && submitRun(stageRange)}
                      >
                        完整跑阶段一
                      </Button>
                    </Tooltip>
                    <Popconfirm
                      title="强制重跑一阶段？"
                      description={
                        <div style={{ maxWidth: 360 }}>
                          从「{stageStartTitle}」重新跑到「{stageEndTitle}」。
                          会清除该范围的产物、断点与模型缓存，翻译将重新调用并产生费用。
                          输入媒体保留，阶段二与广告裁剪成片不变。
                        </div>
                      }
                      okText="确认强制重跑"
                      cancelText="取消"
                      okButtonProps={{ danger: true, loading: runSubmitting }}
                      disabled={stageDisabled}
                      onConfirm={() => !stageDisabled && stageRange && submitRun({ ...stageRange, force: true })}
                    >
                      <Button
                        size="small"
                        danger
                        icon={<RedoOutlined />}
                        disabled={stageDisabled}
                        loading={runSubmitting}
                      >
                        强制重跑一阶段
                      </Button>
                    </Popconfirm>
                  </>
                )}
              </Space>
            }
          >
            <List
              size="small"
              dataSource={steps}
              renderItem={(step) => (
                <List.Item
                  actions={[
                    <Popconfirm
                      key="force"
                      title={`强制重跑「${step.title}」？`}
                      description={
                        <span>
                          会删除这一步的产物、断点与 LLM 缓存后从头重做。
                          下游步骤的产物不会自动清除——若它们依赖这一步的结果，请用顶部的「强制重跑」。
                        </span>
                      }
                      okText="强制重跑"
                      cancelText="取消"
                      okButtonProps={{ danger: true }}
                      disabled={runBusy}
                      onConfirm={() => submitRun({ step_id: step.id, force: true })}
                    >
                      <Button size="small" danger icon={<RedoOutlined />} disabled={runBusy}>
                        强制重跑
                      </Button>
                    </Popconfirm>,
                    <Button
                      key="run"
                      size="small"
                      disabled={runBusy}
                      /* 已完成=重做（清产物+断点）；未完成=续跑/起跑。
                         之前一直 force:true，未完成时会把断点（如 translate_ckpt.json）也删掉 */
                      onClick={() => submitRun({ step_id: step.id, force: step.done })}
                    >
                      {step.done ? '重跑' : '运行'}
                    </Button>,
                  ]}
                >
                  <List.Item.Meta
                    avatar={
                      step.id === status?.current_step && active ? (
                        <LoadingOutlined style={{ fontSize: 18, color: '#1677ff' }} />
                      ) : step.done ? (
                        <CheckCircleFilled style={{ fontSize: 18, color: '#52c41a' }} />
                      ) : (
                        <ClockCircleOutlined style={{ fontSize: 18, color: '#bbb' }} />
                      )
                    }
                    title={step.title}
                    description={stepBadge(step, status?.current_step ?? null, state)}
                  />
                </List.Item>
              )}
            />
          </Card>
        )
      })}

      <Card
        size="small"
        title={
          <Space wrap>
            <Typography.Text style={{ fontSize: 13 }}>实时日志</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {lines.length} 行
            </Typography.Text>
            {status?.log_file && (
              <>
                <Typography.Text type="secondary" style={{ fontSize: 12 }} code>
                  {status.log_file}
                </Typography.Text>
                <Button size="small" icon={<DownloadOutlined />} onClick={doDownloadLog}>
                  下载完整日志
                </Button>
              </>
            )}
          </Space>
        }
      >
        <div
          ref={logRef}
          style={{
            background: '#1e1e1e',
            color: '#d4d4d4',
            fontFamily: 'Consolas, "Courier New", monospace',
            fontSize: 12,
            lineHeight: 1.7,
            padding: '8px 12px',
            borderRadius: 8,
            height: 320,
            overflowY: 'auto',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}
        >
          {lines.length ? lines.join('\n') : '等待任务输出…（流水线步骤、广告位识别、视频裁剪、文案生成的日志都会实时出现在这里）'}
        </div>
      </Card>
    </Space>
  )
}
