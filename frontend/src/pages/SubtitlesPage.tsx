import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Button, Card, Col, Empty, Input, InputNumber, List, Modal, Row,
  Select, Space, Switch, Tag, Typography, message,
} from 'antd'
import { batchStatus } from '../batchApi'
import {
  SubtitleContext, SubtitleRow, SubtitleScope, SubtitleTrack,
  SubtitleTrackName, getSubtitleContext, saveSubtitleTrack,
} from '../api'

const TRACK_LABEL: Record<SubtitleTrackName, string> = {
  'src.srt': '原文 src.srt',
  'trans.srt': '译文 trans.srt',
}

function clock(seconds: number): string {
  const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  const ms = Math.round(safe * 1000)
  const h = Math.floor(ms / 3600000)
  const m = Math.floor((ms % 3600000) / 60000)
  const s = Math.floor((ms % 60000) / 1000)
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(ms % 1000).padStart(3, '0')}`
}

function clamp(value: number, max: number): number {
  return Math.min(Math.max(0, Number.isFinite(value) ? value : 0), max)
}

function SubtitleScrubber({ playhead, duration, fps, snapToFrame, onSeek, waveform, waveformError }: {
  playhead: number
  duration: number
  fps: number
  snapToFrame: boolean
  onSeek: (time: number) => void
  waveform: { time: number; level: number }[]
  waveformError: string
}) {
  const [zoom, setZoom] = useState(3)
  const barRef = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)
  const dragCenter = useRef<number | null>(null)
  const lastSeek = useRef(0)
  const center = dragging.current && dragCenter.current !== null ? dragCenter.current : playhead
  const start = Math.max(0, center - zoom)
  const end = Math.min(duration || center + zoom, center + zoom)
  const span = Math.max(0.001, end - start)
  const position = (time: number) => Math.min(100, Math.max(0, ((time - start) / span) * 100))
  const timeAt = (clientX: number) => {
    const bounds = barRef.current?.getBoundingClientRect()
    if (!bounds) return playhead
    return start + Math.min(1, Math.max(0, (clientX - bounds.left) / Math.max(1, bounds.width))) * span
  }
  const seek = (clientX: number) => {
    const time = timeAt(clientX)
    onSeek(snapToFrame ? Math.round(time * fps) / fps : time)
  }

  return (
    <Space direction="vertical" size={6} style={{ width: '100%' }}>
      <Row justify="space-between" align="middle" gutter={[8, 8]}>
        <Col><Typography.Text code>{clock(playhead)} · 第 {Math.round(playhead * fps)} 帧</Typography.Text></Col>
        <Col><Select aria-label="时间轴窗口" size="small" value={zoom} onChange={setZoom}
          options={[1, 3, 10].map((value) => ({ value, label: `±${value} 秒` }))} /></Col>
      </Row>
      <div ref={barRef} role="slider" aria-label="视频逐帧时间轴" aria-valuemin={start}
        aria-valuemax={end} aria-valuenow={playhead} tabIndex={0}
        onPointerDown={(event) => {
          dragging.current = true
          dragCenter.current = playhead
          event.currentTarget.setPointerCapture(event.pointerId)
          seek(event.clientX)
        }}
        onPointerMove={(event) => {
          if (!dragging.current) return
          const bounds = barRef.current?.getBoundingClientRect()
          if (!bounds) return
          const ratio = Math.min(1, Math.max(0, (event.clientX - bounds.left) / Math.max(1, bounds.width)))
          if (ratio < 0.03 || ratio > 0.97) {
            const nextCenter = Math.min(duration, Math.max(0, (dragCenter.current ?? playhead) + (ratio < 0.03 ? -zoom : zoom) * 0.5))
            dragCenter.current = nextCenter
            onSeek(snapToFrame ? Math.round(nextCenter * fps) / fps : nextCenter)
            return
          }
          if (performance.now() - lastSeek.current < 55) return
          lastSeek.current = performance.now()
          seek(event.clientX)
        }}
        onPointerUp={(event) => {
          if (!dragging.current) return
          dragging.current = false
          dragCenter.current = null
          seek(event.clientX)
          try { event.currentTarget.releasePointerCapture(event.pointerId) } catch { /* 已释放 */ }
        }}
        onKeyDown={(event) => {
          if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
          event.preventDefault()
          const step = event.shiftKey ? Math.max(1, Math.round(fps)) : 1
          const currentFrame = Math.round(playhead * fps)
          const nextFrame = currentFrame + (event.key === 'ArrowLeft' ? -step : step)
          const next = Math.max(0, Math.min(duration, nextFrame / fps))
          onSeek(snapToFrame ? next : Math.max(0, Math.min(duration, playhead + (event.key === 'ArrowLeft' ? -step / fps : step / fps))))
        }}
        style={{ position: 'relative', height: 42, borderRadius: 4, background: '#f0f0f0',
          border: '1px solid #d9d9d9', cursor: 'crosshair', touchAction: 'none', outlineOffset: 2 }}>
        {Array.from({ length: 9 }, (_, index) => {
          const time = start + span * index / 8
          return <span key={index} style={{ position: 'absolute', left: `${index * 12.5}%`, top: 2,
            height: 8, borderLeft: '1px solid #8c8c8c' }} title={clock(time)} />
        })}
        {waveform.length > 0 && <div aria-label="音频波形" style={{ position: 'absolute', inset: '10px 2px 12px',
          pointerEvents: 'none' }}>
          {waveform.filter((point) => point.time >= start && point.time <= end).map((point, index) => (
            <span key={`${point.time}-${index}`} style={{ position: 'absolute',
              left: `${position(point.time)}%`, top: '50%', width: `${Math.max(0.35, 5 / span)}%`,
              height: `${Math.max(4, Math.round(point.level * 100))}%`, transform: 'translateY(-50%)',
              background: point.level < 0.015 ? '#bfbfbf' : '#52c41a', opacity: 0.85 }} />
          ))}
        </div>}
        <span style={{ position: 'absolute', left: `${position(playhead)}%`, top: 0, bottom: 0,
          borderLeft: '2px solid #1677ff', pointerEvents: 'none' }} />
        <div style={{ display: 'flex', justifyContent: 'space-between', position: 'absolute',
          left: 4, right: 4, bottom: 3, pointerEvents: 'none' }}>
          {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
            <Typography.Text key={ratio} type="secondary" style={{ fontSize: 10 }}>
              {clock(start + span * ratio).slice(3, 12)}
            </Typography.Text>
          ))}
        </div>
      </div>
      {waveform.length === 0 && <Typography.Text type={waveformError.startsWith('正在') ? 'secondary' : 'warning'} style={{ fontSize: 11 }}>
        {waveformError || '当前媒体无可用波形'}
      </Typography.Text>}
      <Typography.Text type="secondary" style={{ fontSize: 11 }}>
        点击或拖动定位；←/→ 单帧，Shift+←/→ 一秒。精调时间范围与波形随播放头同步。
      </Typography.Text>
    </Space>
  )
}

export default function SubtitlesPage() {
  const [scope, setScope] = useState<SubtitleScope>('single')
  const [batchVideos, setBatchVideos] = useState<string[]>([])
  const [video, setVideo] = useState<string>('')
  const [context, setContext] = useState<SubtitleContext | null>(null)
  const [trackName, setTrackName] = useState<SubtitleTrackName>('src.srt')
  const [tracks, setTracks] = useState<Record<SubtitleTrackName, SubtitleTrack | null>>({
    'src.srt': null, 'trans.srt': null,
  })
  const [selected, setSelected] = useState<number | null>(null)
  const [dirty, setDirty] = useState(false)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [playhead, setPlayhead] = useState(0)
  const [waveform, setWaveform] = useState<{ time: number; level: number }[]>([])
  const [waveformError, setWaveformError] = useState('播放时采集音频波形')
  const [subtitleDisplay, setSubtitleDisplay] = useState<'off' | 'src' | 'trans' | 'both'>('both')
  const [splitAt, setSplitAt] = useState<number | null>(null)
  const [manualFps, setManualFps] = useState<number | null>(null)
  const [snapToFrame, setSnapToFrame] = useState(true)
  const [syncTracks, setSyncTracks] = useState(true)
  const [timeDrafts, setTimeDrafts] = useState<Record<SubtitleTrackName, { start: string; end: string }>>({
    'src.srt': { start: '', end: '' },
    'trans.srt': { start: '', end: '' },
  })
  const videoRef = useRef<HTMLVideoElement>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const waveformFrameRef = useRef<number | null>(null)
  const lastWaveformSampleRef = useRef<number | null>(null)
  const textRef = useRef<HTMLTextAreaElement>(null)
  const srcTextRef = useRef<HTMLTextAreaElement>(null)
  const transTextRef = useRef<HTMLTextAreaElement>(null)
  const lastFocusedTextTrackRef = useRef<SubtitleTrackName | null>(null)
  const splitCursorRef = useRef<Record<SubtitleTrackName, { row: number; position: number } | null>>({
    'src.srt': null,
    'trans.srt': null,
  })
  const trackListRef = useRef<HTMLDivElement>(null)
  const rowElementsRef = useRef(new Map<number, HTMLDivElement>())
  const singlePlayRef = useRef<{ end: number } | null>(null)
  const programmaticPlayRef = useRef(false)
  const submittedTimeFieldRef = useRef<string | null>(null)
  const [msgApi, ctxHolder] = message.useMessage()

  const current = tracks[trackName] ?? null
  const rows = current?.rows ?? []
  const srcTrack = tracks['src.srt']
  const transTrack = tracks['trans.srt']
  const pairedTracks = !!srcTrack && !!transTrack && srcTrack.rows.length === transTrack.rows.length
  const maxDuration = context?.duration ?? 0
  const fps = manualFps ?? context?.fps ?? 30
  const frameSec = 1 / Math.max(1, fps)
  const snapTime = (value: number) => {
    const bounded = clamp(value, maxDuration)
    return snapToFrame ? Math.min(maxDuration, Math.round(bounded * fps) / fps) : bounded
  }

  const loadBatchVideos = useCallback(async () => {
    try {
      const status = await batchStatus()
      const videos = status.files?.map((f) => f.video).filter(Boolean) ?? []
      setBatchVideos(videos)
      if (videos.length && !videos.includes(video)) setVideo(videos[0])
      return videos
    } catch (e) {
      setBatchVideos([])
      throw e
    }
  }, [video])

  const load = useCallback(async (force = false) => {
    if (dirty && !force) {
      return new Promise<boolean>((resolve) => {
        Modal.confirm({
          title: '有未保存的字幕修改',
          content: '继续加载将放弃这些修改。',
          okText: '放弃并加载',
          okButtonProps: { danger: true },
          cancelText: '取消',
          onOk: async () => { resolve(await load(true)) },
          onCancel: () => resolve(false),
        })
      })
    }
    setLoading(true)
    setError('')
    try {
      const candidates = scope === 'batch' ? await loadBatchVideos() : []
      const chosen = scope === 'batch' ? (video || candidates[0]) : undefined
      const data = await getSubtitleContext(scope, chosen)
      setContext(data)
      setWaveform([])
      setWaveformError('播放时采集音频波形')
      singlePlayRef.current = null
      setManualFps(null)
      setTracks(data.tracks)
      setDirty(false)
      setSelected(null)
      setPlayhead(0)
      videoRef.current?.pause()
      if (videoRef.current) videoRef.current.currentTime = 0
      return true
    } catch (e) {
      setContext(null)
      setError(e instanceof Error ? e.message : String(e))
      return false
    } finally {
      setLoading(false)
    }
  }, [scope, video, dirty, loadBatchVideos])

  useEffect(() => {
    void load(true)
    // intentionally only on mount; scope/video changes go through guarded UI
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => {
      if (!dirty) return
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [dirty])

  const chooseRow = (index: number) => {
    setSelected(index)
    const row = rows[index]
    const videoElement = videoRef.current
    if (!row || !videoElement) return
    singlePlayRef.current = { end: row.end }
    videoElement.currentTime = row.start
    setPlayhead(row.start)
    if (videoElement.paused) programmaticPlayRef.current = true
    else programmaticPlayRef.current = false
    void videoElement.play().catch((error: unknown) => {
      programmaticPlayRef.current = false
      singlePlayRef.current = null
      msgApi.error(error instanceof Error ? `无法播放该字幕：${error.message}` : '无法播放该字幕，请先与页面交互')
    })
  }

  const seekTo = (time: number) => {
    singlePlayRef.current = null
    lastWaveformSampleRef.current = null
    if (videoRef.current) videoRef.current.currentTime = time
    setPlayhead(time)
  }

  const editTrackText = (name: SubtitleTrackName, value: string) => {
    if (selected === null) return
    setTracks((old) => {
      const track = old[name]
      if (!track || !track.rows[selected]) return old
      return {
        ...old,
        [name]: {
          ...track,
          rows: track.rows.map((row, i) => i === selected ? { ...row, text: value } : row),
        },
      }
    })
    setDirty(true)
  }

  const timeBounds = (trackRows: SubtitleRow[], index: number, key: 'start' | 'end', value: number) => {
    const row = trackRows[index]
    const next = snapTime(value)
    const bounded = key === 'start'
      ? Math.max(0, Math.min(next, row.end - frameSec))
      : Math.min(maxDuration, Math.max(next, row.start + frameSec))
    const valid = key === 'start'
      ? bounded >= 0 && bounded < row.end
      : bounded > row.start && bounded <= maxDuration
    return { bounded, valid }
  }

  const editTime = (key: 'start' | 'end', value: number | null, targetTrack: SubtitleTrackName = trackName): boolean => {
    if (selected === null || value === null) {
      msgApi.error('请输入有效的时间（秒）')
      return false
    }
    if (!Number.isFinite(value)) {
      msgApi.error('时间必须是有效数字')
      return false
    }
    const edited = tracks[targetTrack]
    if (!edited?.rows[selected]) return false
    const counterpartName: SubtitleTrackName = targetTrack === 'src.srt' ? 'trans.srt' : 'src.srt'
    const counterpart = tracks[counterpartName]
    const canSync = syncTracks && !!counterpart && counterpart.rows.length === edited.rows.length
    const primary = timeBounds(edited.rows, selected, key, value)
    if (!primary.valid) {
      msgApi.error('时间必须在媒体范围内，并满足正时长和相邻行约束')
      return false
    }
    let counterpartRows: SubtitleRow[] | null = null
    if (canSync && counterpart) {
      const paired = timeBounds(counterpart.rows, selected, key, primary.bounded)
      if (paired.valid && Math.abs(paired.bounded - primary.bounded) <= 1e-9) {
        counterpartRows = counterpart.rows.map((item, i) => i === selected ? { ...item, [key]: paired.bounded } : item)
      }
    }
    const nextRows = edited.rows.map((item, i) => i === selected ? { ...item, [key]: primary.bounded } : item)
    setTracks((old) => ({
      ...old,
      [targetTrack]: { ...edited, rows: nextRows },
      ...(counterpartRows && counterpart ? { [counterpartName]: { ...counterpart, rows: counterpartRows } } : {}),
    }))
    setDirty(true)
    if (syncTracks && !canSync) msgApi.warning('原文与译文轨道缺失或行数不同；本次仅修改当前轨道')
    else if (syncTracks && !counterpartRows) msgApi.warning('另一轨无法应用相同时间；已仅修改当前轨道')
    setTimeDrafts((old) => ({
      ...old,
      [targetTrack]: { ...old[targetTrack], [key]: String(primary.bounded) },
      ...(counterpartRows ? { [counterpartName]: { ...old[counterpartName], [key]: String(primary.bounded) } } : {}),
    }))
    return true
  }

  const nudgePlayhead = (delta: number) => {
    singlePlayRef.current = null
    const next = snapTime(playhead + delta)
    if (videoRef.current) videoRef.current.currentTime = next
    setPlayhead(next)
  }

  const setSelectedTime = (key: 'start' | 'end') => {
    if (selected === null) return
    editTime(key, playhead)
  }

  const splitRow = () => {
    if (selected === null || !pairedTracks || !srcTrack || !transTrack) return
    const srcRow = srcTrack.rows[selected]
    const transRow = transTrack.rows[selected]
    if (!srcRow || !transRow) return
    const boundary = splitAt ?? Math.min(rows[selected].end - 0.001, Math.max(rows[selected].start + 0.001, playhead))
    if (
      boundary <= srcRow.start || boundary >= srcRow.end ||
      boundary <= transRow.start || boundary >= transRow.end
    ) {
      msgApi.error('分界时间必须同时位于原文与译文字幕的起止时间之间；两轨均未修改')
      return
    }
    const primaryTrack = lastFocusedTextTrackRef.current ?? trackName
    const primaryText = primaryTrack === 'src.srt' ? srcRow.text : transRow.text
    const primaryCursor = splitCursorRef.current[primaryTrack]
    const rawPrimaryPosition = primaryCursor?.row === selected
      ? primaryCursor.position
      : (primaryTrack === 'src.srt' ? srcTextRef.current?.selectionStart : transTextRef.current?.selectionStart) ?? 0
    const primaryPosition = Math.min(primaryText.length, Math.max(0, rawPrimaryPosition))
    const counterpartTrack: SubtitleTrackName = primaryTrack === 'src.srt' ? 'trans.srt' : 'src.srt'
    const counterpartText = counterpartTrack === 'src.srt' ? srcRow.text : transRow.text
    const counterpartCursor = splitCursorRef.current[counterpartTrack]
    const counterpartPosition = counterpartCursor?.row === selected
      ? counterpartCursor.position
      : Math.round((primaryPosition / Math.max(1, primaryText.length)) * counterpartText.length)
    const splitText = (text: string, position: number) => {
      const boundedPosition = Math.min(text.length, Math.max(0, position))
      const left = text.slice(0, boundedPosition).trim()
      const right = text.slice(boundedPosition).trim()
      return left && right ? [left, right] : null
    }
    const positions: Record<SubtitleTrackName, number> = primaryTrack === 'src.srt'
      ? { 'src.srt': primaryPosition, 'trans.srt': counterpartPosition }
      : { 'src.srt': counterpartPosition, 'trans.srt': primaryPosition }
    const srcParts = splitText(srcRow.text, positions['src.srt'])
    const transParts = splitText(transRow.text, positions['trans.srt'])
    if (!srcParts || !transParts) {
      msgApi.error('原文与译文的光标两侧都必须有文字；两轨均未修改')
      return
    }
    const splitRows = (trackRows: SubtitleRow[], parts: string[]) => [
      ...trackRows.slice(0, selected),
      { start: trackRows[selected].start, end: boundary, text: parts[0] },
      { start: boundary, end: trackRows[selected].end, text: parts[1] },
      ...trackRows.slice(selected + 1),
    ]
    setTracks((old) => ({
      ...old,
      'src.srt': { ...srcTrack, rows: splitRows(srcTrack.rows, srcParts) },
      'trans.srt': { ...transTrack, rows: splitRows(transTrack.rows, transParts) },
    }))
    setDirty(true)
    setSelected(selected)
    setSplitAt(null)
  }

  const mergeWith = (offset: -1 | 1) => {
    if (selected === null || !pairedTracks || !srcTrack || !transTrack) return
    const other = selected + offset
    if (other < 0 || other >= rows.length) return
    const first = Math.min(selected, other)
    const second = Math.max(selected, other)
    const mergeRows = (trackRows: SubtitleRow[]) => {
      const a = trackRows[first]
      const b = trackRows[second]
      const merged: SubtitleRow = {
        start: a.start,
        end: Math.max(a.end, b.end),
        text: [a.text, b.text].map((text) => text.replace(/\s+/g, ' ').trim()).filter(Boolean).join(' '),
      }
      return [...trackRows.slice(0, first), merged, ...trackRows.slice(second + 1)]
    }
    setTracks((old) => ({
      ...old,
      'src.srt': { ...srcTrack, rows: mergeRows(srcTrack.rows) },
      'trans.srt': { ...transTrack, rows: mergeRows(transTrack.rows) },
    }))
    setDirty(true)
    setSelected(first)
  }

  const deleteRow = () => {
    if (selected === null || !pairedTracks || !srcTrack || !transTrack) return
    setTracks((old) => ({
      ...old,
      'src.srt': { ...srcTrack, rows: srcTrack.rows.filter((_, i) => i !== selected) },
      'trans.srt': { ...transTrack, rows: transTrack.rows.filter((_, i) => i !== selected) },
    }))
    setDirty(true)
    setSelected(null)
  }

  const save = async () => {
    if (!current) return
    const saveNames: SubtitleTrackName[] = (['src.srt', 'trans.srt'] as const)
      .filter((name) => tracks[name] !== null)
    for (const name of saveNames) {
      const track = tracks[name]
      if (!track) continue
      const invalidIndex = track.rows.findIndex((row) =>
        !Number.isFinite(row.start) || !Number.isFinite(row.end) ||
        row.start < 0 || row.end <= row.start || row.end > maxDuration + 0.05 ||
        !row.text.trim())
      if (invalidIndex >= 0) {
        const row = track.rows[invalidIndex]
        const problems = [
          !Number.isFinite(row.start) ? `开始时间无效（${row.start}）` : row.start < 0 ? `开始时间为负数（${row.start}）` : '',
          !Number.isFinite(row.end) ? `结束时间无效（${row.end}）` : row.end <= row.start ? `结束时间 ${row.end} 不晚于开始时间 ${row.start}` : row.end > maxDuration + 0.05 ? `结束时间 ${row.end} 超出媒体时长 ${maxDuration}` : '',
          !row.text.trim() ? '字幕文本为空' : '',
        ].filter(Boolean).join('；')
        const text = `${TRACK_LABEL[name]}第 ${invalidIndex + 1} 条无效：${problems}`
        setError(text)
        msgApi.error(text)
        return
      }
    }
    setSaving(true)
    setError('')
    try {
      let nextTracks = { ...tracks }
      for (const name of saveNames) {
        const track = nextTracks[name]
        if (!track) continue
        const saved = await saveSubtitleTrack(
          name, scope, scope === 'batch' ? video : undefined,
          track.revision, track.rows,
        )
        nextTracks = { ...nextTracks, [name]: saved }
        setTracks((old) => ({ ...old, [name]: saved }))
      }
      setDirty(false)
      msgApi.success(saveNames.length === 2 ? '原文与译文轨道已保存' : `${TRACK_LABEL[trackName]} 已保存`)
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e)
      setError(text)
      msgApi.error(text)
    } finally {
      setSaving(false)
    }
  }

  const switchScope = (next: SubtitleScope) => {
    if (next === scope) return
    const apply = async () => {
      setScope(next)
      setVideo('')
      setContext(null)
      setTracks({ 'src.srt': null, 'trans.srt': null })
      setDirty(false)
      setSelected(null)
      await load(true)
    }
    if (!dirty) { void apply(); return }
    Modal.confirm({
      title: '有未保存的字幕修改',
      content: '切换工作区将放弃这些修改。',
      okText: '放弃并切换', okButtonProps: { danger: true }, cancelText: '取消',
      onOk: apply,
    })
  }

  const switchTrack = (next: SubtitleTrackName) => {
    if (next === trackName) return
    const apply = () => {
      setTrackName(next)
      setSelected(null)
    }
    if (!dirty) { apply(); return }
    Modal.confirm({
      title: '有未保存的字幕修改',
      content: '切换轨道会保留两份轨道的草稿，但只保存当前轨道；建议先保存。',
      okText: '仍要切换', cancelText: '取消', onOk: apply,
    })
  }

  useEffect(() => {
    const row = selected === null ? null : rows[selected]
    const srcRow = selected === null ? null : srcTrack?.rows[selected]
    const transRow = selected === null ? null : transTrack?.rows[selected]
    setTimeDrafts({
      'src.srt': { start: srcRow ? String(srcRow.start) : '', end: srcRow ? String(srcRow.end) : '' },
      'trans.srt': { start: transRow ? String(transRow.start) : '', end: transRow ? String(transRow.end) : '' },
    })
    if (!row) singlePlayRef.current = null
  }, [selected, trackName, rows, srcTrack, transTrack])

  const sortedRows = useMemo(() => rows.map((row, index) => ({ row, index })), [rows])
  const playheadRow = useMemo(
    () => rows.findIndex((row) => row.start <= playhead && playhead < row.end),
    [rows, playhead],
  )
  const previewUrl = context?.preview_url || ''

  useEffect(() => {
    setWaveform([])
    setWaveformError(previewUrl ? '播放时采集音频波形' : '')
    return () => {
      if (waveformFrameRef.current !== null) cancelAnimationFrame(waveformFrameRef.current)
      waveformFrameRef.current = null
      lastWaveformSampleRef.current = null
      const context = audioContextRef.current
      audioContextRef.current = null
      analyserRef.current = null
      if (context) void context.close()
    }
  }, [previewUrl])

  const startWaveformCapture = () => {
    const element = videoRef.current
    if (!element) return
    try {
      let audioContext = audioContextRef.current
      if (!audioContext) {
        const AudioContextConstructor = window.AudioContext
        if (!AudioContextConstructor) throw new Error('当前浏览器不支持 Web Audio 波形分析')
        audioContext = new AudioContextConstructor()
        const source = audioContext.createMediaElementSource(element)
        const analyser = audioContext.createAnalyser()
        analyser.fftSize = 1024
        source.connect(analyser)
        analyser.connect(audioContext.destination)
        audioContextRef.current = audioContext
        analyserRef.current = analyser
      }
      void audioContext.resume()
      setWaveformError('等待音频采样…')
      const analyser = analyserRef.current
      if (!analyser) return
      const samples = new Uint8Array(analyser.fftSize)
      const tick = () => {
        if (!element.paused && !element.ended) {
          const mediaTime = element.currentTime
          if (lastWaveformSampleRef.current === null || mediaTime - lastWaveformSampleRef.current >= 0.05) {
            analyser.getByteTimeDomainData(samples)
            let sum = 0
            for (const sample of samples) {
              const amplitude = (sample - 128) / 128
              sum += amplitude * amplitude
            }
            const rms = Math.min(1, Math.sqrt(sum / samples.length) * 3)
            lastWaveformSampleRef.current = mediaTime
            setWaveform((old) => [...old.slice(-11999), { time: mediaTime, level: rms }])
          }
          setWaveformError('实时声波 · 绿色为有声，灰色为静音')
          waveformFrameRef.current = requestAnimationFrame(tick)
        } else {
          waveformFrameRef.current = null
        }
      }
      if (waveformFrameRef.current !== null) cancelAnimationFrame(waveformFrameRef.current)
      waveformFrameRef.current = requestAnimationFrame(tick)
    } catch (error) {
      setWaveformError(error instanceof Error ? error.message : '音频波形不可用')
    }
  }

  useEffect(() => {
    if (playheadRow < 0) return
    const list = trackListRef.current
    const item = rowElementsRef.current.get(playheadRow)
    if (!list || !item) return
    const listBounds = list.getBoundingClientRect()
    const itemBounds = item.getBoundingClientRect()
    if (itemBounds.top < listBounds.top) {
      list.scrollTop -= listBounds.top - itemBounds.top
    } else if (itemBounds.bottom > listBounds.bottom) {
      list.scrollTop += itemBounds.bottom - listBounds.bottom
    }
  }, [playheadRow, rows, trackName])
  const activeSubtitle = (name: SubtitleTrackName) =>
    (tracks[name]?.rows ?? []).find((row) => row.start <= playhead && playhead < row.end)?.text ?? ''
  const srcSubtitle = activeSubtitle('src.srt')
  const transSubtitle = activeSubtitle('trans.srt')

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {ctxHolder}
      <Card title="字幕编辑" extra={<Tag color={dirty ? 'orange' : 'green'}>{dirty ? '有未保存修改' : '已同步'}</Tag>}>
        <Row gutter={[12, 12]} align="middle">
          <Col><Select value={scope} onChange={switchScope} options={[
            { value: 'single', label: '单任务 output/' },
            { value: 'batch', label: '当前批量扫描' },
          ]} style={{ width: 160 }} /></Col>
          {scope === 'batch' && (
            <Col flex="auto">
              <Select value={video || undefined} onChange={(v) => setVideo(v)}
                placeholder="选择当前扫描结果中的视频" allowClear
                options={batchVideos.map((v) => ({ value: v, label: v }))}
                style={{ width: '100%' }} />
            </Col>
          )}
          <Col><Button loading={loading} onClick={() => void load()}>加载 / 刷新</Button></Col>
        </Row>
        {context && <Typography.Text type="secondary" style={{ display: 'block', marginTop: 10 }}>
          原片：{context.video ?? '单任务输入'} · 时长 {clock(context.duration)}
        </Typography.Text>}
        {error && <Alert type="error" showIcon message={error} style={{ marginTop: 12 }} />}
        {!error && context && !current && (
          <Alert type="warning" showIcon style={{ marginTop: 12 }}
            message="该轨道尚无 SRT 文件；当前版本只保存已有轨道，请先运行生成字幕步骤。" />
        )}
      </Card>

      <Row gutter={16}>
        <Col xs={24} lg={14}>
          <Card title="原片预览" loading={loading}>
            {previewUrl ? (
              <div style={{ position: 'relative', lineHeight: 0 }}>
                <video key={previewUrl} ref={videoRef} controls preload="metadata"
                  playsInline style={{ width: '100%', background: '#000', maxHeight: 420 }}
                  src={previewUrl}
                  onTimeUpdate={(e) => {
                    const element = e.currentTarget
                    const limit = singlePlayRef.current?.end
                    if (limit !== undefined && element.currentTime >= limit) {
                      element.currentTime = limit
                      setPlayhead(limit)
                      singlePlayRef.current = null
                      element.pause()
                      return
                    }
                    setPlayhead(element.currentTime)
                  }}
                  onSeeked={(e) => {
                    lastWaveformSampleRef.current = null
                    setPlayhead(e.currentTarget.currentTime)
                  }}
                  onPlay={() => {
                    if (programmaticPlayRef.current) {
                      programmaticPlayRef.current = false
                    } else {
                      singlePlayRef.current = null
                    }
                    startWaveformCapture()
                  }}
                  onPause={() => {
                    if (waveformFrameRef.current !== null) cancelAnimationFrame(waveformFrameRef.current)
                    waveformFrameRef.current = null
                  }} />
                {subtitleDisplay !== 'off' && (srcSubtitle || transSubtitle) && (
                  <div aria-live="off" style={{
                    position: 'absolute', left: '5%', right: '5%', bottom: 42,
                    display: 'flex', flexDirection: 'column', alignItems: 'center',
                    gap: 4, pointerEvents: 'none', textAlign: 'center', lineHeight: 1.4,
                    textShadow: '0 2px 4px #000, 1px 1px 2px #000',
                  }}>
                    {(subtitleDisplay === 'src' || subtitleDisplay === 'both') && srcSubtitle && (
                      <div style={{ color: '#fff', fontSize: 18, fontWeight: 600,
                        background: 'rgba(0,0,0,.68)', padding: '3px 8px', maxWidth: '100%',
                        overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>
                        {srcSubtitle}
                      </div>
                    )}
                    {(subtitleDisplay === 'trans' || subtitleDisplay === 'both') && transSubtitle && (
                      <div style={{ color: '#ffe45c', fontSize: 18, fontWeight: 600,
                        background: 'rgba(0,0,0,.68)', padding: '3px 8px', maxWidth: '100%',
                        overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>
                        {transSubtitle}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ) : <Empty description="未加载工作区" />}
            <SubtitleScrubber playhead={playhead} duration={maxDuration} fps={fps} snapToFrame={snapToFrame}
              waveform={waveform} waveformError={waveformError} onSeek={seekTo} />
            <Space style={{ marginTop: 8 }} wrap>
              <Tag color="blue">播放头 {clock(playhead)} · 第 {Math.round(playhead * fps)} 帧</Tag>
              <Select aria-label="帧率" value={fps} onChange={(value) => setManualFps(value)}
                options={[23.976, 24, 25, 29.97, 30, 50, 59.94, 60].map((value) => ({
                  value, label: `${value} fps${value === context?.fps && context?.fps_source === 'auto' ? '（实测）' : ''}`,
                }))} style={{ width: 130 }} />
              <Typography.Text type={context?.fps_source === 'fallback' && manualFps === null ? 'warning' : 'secondary'}>
                {manualFps !== null ? '手选帧率' : context?.fps_source === 'auto' ? '实测帧率' : '默认 30 fps（探测失败）'}
              </Typography.Text>
              <Switch size="small" checked={snapToFrame} onChange={setSnapToFrame} />
              <Typography.Text>吸附帧</Typography.Text>
              <Select aria-label="预览字幕" value={subtitleDisplay} onChange={setSubtitleDisplay}
                options={[
                  { value: 'both', label: '双语字幕' },
                  { value: 'src', label: '仅原文' },
                  { value: 'trans', label: '仅译文' },
                  { value: 'off', label: '关闭字幕' },
                ]} style={{ width: 130 }} />
              <Button size="small" onClick={() => setSplitAt(snapTime(playhead))} disabled={selected === null}>用播放头作为拆分点</Button>
              <Button size="small" onClick={() => setSelectedTime('start')} disabled={selected === null}>播放头设为开始</Button>
              <Button size="small" onClick={() => setSelectedTime('end')} disabled={selected === null}>播放头设为结束</Button>
              <Button size="small" onClick={() => nudgePlayhead(-frameSec)}>−1 帧</Button>
              <Button size="small" onClick={() => nudgePlayhead(frameSec)}>+1 帧</Button>
              <Button size="small" onClick={() => nudgePlayhead(-0.1)}>−0.1 秒</Button>
              <Button size="small" onClick={() => nudgePlayhead(0.1)}>+0.1 秒</Button>
              <Button size="small" onClick={() => nudgePlayhead(-1)}>−1 秒</Button>
              <Button size="small" onClick={() => nudgePlayhead(1)}>+1 秒</Button>
              <Button size="small" onClick={() => {
                const v = videoRef.current
                if (v) v.currentTime = playhead
              }}>重新定位</Button>
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title="字幕轨道" extra={<Select value={trackName} onChange={switchTrack} options={[
            { value: 'src.srt', label: TRACK_LABEL['src.srt'] },
            { value: 'trans.srt', label: TRACK_LABEL['trans.srt'] },
          ]} style={{ width: 155 }} />}>
            <List size="small" ref={trackListRef} style={{ maxHeight: 470, overflow: 'auto' }}
              dataSource={sortedRows}
              locale={{ emptyText: '轨道为空或尚未加载' }}
              renderItem={({ row, index }) => (
                <List.Item ref={(element) => {
                  if (element) rowElementsRef.current.set(index, element)
                  else rowElementsRef.current.delete(index)
                }} onClick={() => chooseRow(index)} style={{
                  cursor: 'pointer', padding: '8px 6px',
                  background: selected === index ? '#e6f4ff' : playheadRow === index ? '#f6ffed' : undefined,
                  borderInlineStart: playheadRow === index ? '3px solid #52c41a' : '3px solid transparent',
                }}>
                  <Space direction="vertical" size={2} style={{ width: '100%' }}>
                    <Typography.Text code>#{index + 1} {clock(row.start)} → {clock(row.end)}</Typography.Text>
                    <Typography.Text ellipsis={{ tooltip: row.text }}>
                      <span style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                        {row.text}
                      </span>
                    </Typography.Text>
                  </Space>
                </List.Item>
              )} />
          </Card>
        </Col>
      </Row>

      <Card title={<Space><span>编辑选中字幕</span><Button type="primary" size="small" loading={saving}
        disabled={!dirty || !current} onClick={() => void save()}>保存字幕修改</Button></Space>}>
        {selected !== null && rows[selected] ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {(['src.srt', 'trans.srt'] as const).filter((name) => tracks[name]).map((name) => (
              <Row key={name} gutter={12} align="middle">
                <Col xs={24}><Typography.Text strong>{TRACK_LABEL[name]} #{selected + 1}</Typography.Text></Col>
                {(['start', 'end'] as const).map((key) => (
                  <Col key={key} xs={12} md={6}>
                    <Typography.Text>{key === 'start' ? '开始' : '结束'}</Typography.Text>
                    <Input inputMode="decimal" value={timeDrafts[name][key]}
                      onChange={(e) => setTimeDrafts((old) => ({ ...old,
                        [name]: { ...old[name], [key]: e.target.value },
                      }))}
                      onPressEnter={() => {
                        const committed = editTime(
                          key,
                          timeDrafts[name][key].trim() === '' ? null : Number(timeDrafts[name][key]),
                          name,
                        )
                        submittedTimeFieldRef.current = committed ? `${name}:${selected}:${key}` : null
                      }}
                      onBlur={() => {
                        const field = `${name}:${selected}:${key}`
                        if (submittedTimeFieldRef.current === field) {
                          submittedTimeFieldRef.current = null
                          return
                        }
                        setTimeDrafts((old) => ({ ...old,
                          [name]: { ...old[name], [key]: String(tracks[name]?.rows[selected]?.[key] ?? '') },
                        }))
                      }} />
                  </Col>
                ))}
              </Row>
            ))}
            <Row gutter={12} align="middle">
              <Col><Switch checked={syncTracks} onChange={setSyncTracks} /> <span>同步原文 / 译文时间</span></Col>
              <Col><span>拆分时间</span><InputNumber min={rows[selected].start} max={rows[selected].end} step={frameSec}
                value={splitAt ?? undefined} placeholder="可选"
                onChange={(value) => setSplitAt(value === null ? null : snapTime(value))} /></Col>
            </Row>
            {pairedTracks ? (
              <Row gutter={12}>
                <Col xs={24} md={12}>
                  <Typography.Text strong style={{ display: 'block', marginBottom: 6 }}>原文 #{selected + 1}</Typography.Text>
                  <Input.TextArea ref={srcTextRef} rows={4} value={srcTrack?.rows[selected].text ?? ''}
                    onFocus={(e) => {
                      lastFocusedTextTrackRef.current = 'src.srt'
                      splitCursorRef.current['src.srt'] = { row: selected, position: e.currentTarget.selectionStart }
                    }}
                    onSelect={(e) => {
                      lastFocusedTextTrackRef.current = 'src.srt'
                      splitCursorRef.current['src.srt'] = { row: selected, position: e.currentTarget.selectionStart }
                    }}
                    onChange={(e) => editTrackText('src.srt', e.target.value)} />
                </Col>
                <Col xs={24} md={12}>
                  <Typography.Text strong style={{ display: 'block', marginBottom: 6 }}>译文 #{selected + 1}</Typography.Text>
                  <Input.TextArea ref={transTextRef} rows={4} value={transTrack?.rows[selected].text ?? ''}
                    onFocus={(e) => {
                      lastFocusedTextTrackRef.current = 'trans.srt'
                      splitCursorRef.current['trans.srt'] = { row: selected, position: e.currentTarget.selectionStart }
                    }}
                    onSelect={(e) => {
                      lastFocusedTextTrackRef.current = 'trans.srt'
                      splitCursorRef.current['trans.srt'] = { row: selected, position: e.currentTarget.selectionStart }
                    }}
                    onChange={(e) => editTrackText('trans.srt', e.target.value)} />
                </Col>
              </Row>
            ) : (
              <>
                <Alert type="info" showIcon message={
                  !srcTrack || !transTrack
                    ? '原文或译文轨道未加载；当前仅编辑所选轨道。'
                    : '原文与译文行数不一致，无法可靠配对；当前仅编辑所选轨道。'
                } />
                <Typography.Text strong>{TRACK_LABEL[trackName]}</Typography.Text>
                <Input.TextArea ref={textRef} rows={4} value={rows[selected].text}
                  onChange={(e) => editTrackText(trackName, e.target.value)} />
              </>
            )}
            <Space wrap>
              <Button disabled={!pairedTracks} onClick={splitRow}>在光标处拆分</Button>
              <Button disabled={!pairedTracks || selected === 0} onClick={() => mergeWith(-1)}>与上一行合并</Button>
              <Button disabled={!pairedTracks || selected >= rows.length - 1} onClick={() => mergeWith(1)}>与下一行合并</Button>
              <Button danger disabled={!pairedTracks} onClick={deleteRow}>删除</Button>
              <Button onClick={() => { setSelected(null); setSplitAt(null) }}>放弃当前选择</Button>
            </Space>
            <Typography.Text type="secondary">
              {!pairedTracks
                ? '两轨未能按编号配对，结构编辑已禁用；可继续编辑当前轨文本。'
                : '拆分、合并和删除会同步修改原文与译文；合并使用首行起点、两行最晚终点和各轨文本。'}
            </Typography.Text>
          </Space>
        ) : <Empty description="从右侧列表选择一条字幕" />}
      </Card>
    </Space>
  )
}
