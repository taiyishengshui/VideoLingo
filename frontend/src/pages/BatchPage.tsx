import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Badge, Button, Card, Checkbox, Input, List, Modal, Popconfirm, Select, Space,
  Table, Tag, Typography, message,
} from 'antd'
import {
  FolderOpenOutlined, ReloadOutlined, CaretRightOutlined, StopOutlined,
  RetweetOutlined, ArrowLeftOutlined, FileOutlined, PlayCircleFilled,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import {
  BatchFileRow, ScanResult, StepState, OverallState, BrowseResult,
  scanBatch, startBatch, stopBatch, batchStatus, tailBatchLog, browse,
} from '../batchApi'
import { BusEvent, useBus } from '../ws'

const MAX_LOG_LINES = 800

const OVERALL_LABEL: Record<OverallState, { color: string; text: string }> = {
  pending: { color: 'default', text: '未处理' },
  partial: { color: 'processing', text: '进行中/部分' },
  done: { color: 'success', text: '已完成' },
  delivered: { color: 'success', text: '已交付' },
  failed: { color: 'error', text: '失败' },
}

const STEP_LABEL: Record<string, string> = {
  transcribe: '转录', asr_correct: '幻觉修正', split: '切句', translate: '翻译',
  align: '对齐', merge_sub: '字幕烧录', audio_task: '配音任务',
  refer_audio: '参考音频', gen_audio: '生成配音', merge_audio: '合并音频',
  dub_to_vid: '配音合成',
}

function StepDots({ steps }: { steps: BatchFileRow['steps'] }) {
  return (
    <Space size={3} style={{ flexWrap: 'wrap' }}>
      {steps.map((s) => {
        const bg =
          s.status === 'done' ? '#52c41a'
          : s.status === 'running' ? '#1677ff'
          : s.status === 'failed' ? '#faad14'
          : '#d9d9d9'
        return (
          <TooltipColor key={s.id} label={STEP_LABEL[s.id] ?? s.id} status={s.status} color={bg} />
        )
      })}
    </Space>
  )
}

function TooltipColor({ label, status, color }: { label: string; status: StepState; color: string }) {
  return (
    <span title={`${label} (${status})`} style={{
      display: 'inline-block', width: 14, height: 8, borderRadius: 2,
      background: color,
    }} />
  )
}

interface RerunTarget {
  video: string
}

/** Server-side directory browser: pick a directory OR a single media file.
 * Clicking a dir navigates into it; "选择此目录" confirms the current dir;
 * clicking a media file confirms that file directly. */
function FileBrowserModal({
  open, onClose, onPick,
}: {
  open: boolean
  onClose: () => void
  onPick: (path: string) => void
}) {
  const [data, setData] = useState<BrowseResult | null>(null)
  const [err, setErr] = useState('')
  const [msgApi, ctxHolder] = message.useMessage()

  const load = useCallback((path: string | null) => {
    setErr('')
    browse(path)
      .then(setData)
      .catch((e) => setErr(String(e)))
  }, [])

  useEffect(() => {
    if (open) load(null)
  }, [open, load])

  const current = data?.path ?? null
  const pick = (p: string) => { onPick(p); onClose() }

  return (
    <Modal
      open={open}
      title="浏览 — 选择目录或媒体文件"
      onCancel={onClose}
      footer={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" icon={<FolderOpenOutlined />}
            disabled={!current}
            onClick={() => current && pick(current)}>
            选择此目录
          </Button>
        </Space>
      }
      width={560}
    >
      {ctxHolder}
      <div style={{ marginBottom: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
        <Button size="small" icon={<ArrowLeftOutlined />}
          disabled={!data?.parent}
          onClick={() => load(data?.parent ?? null)}>
          上一级
        </Button>
        <Typography.Text code ellipsis style={{ flex: 1, fontSize: 12 }}>
          {current ?? '（选择磁盘）'}
        </Typography.Text>
      </div>
      {err && <Typography.Text type="danger" style={{ fontSize: 12 }}>{err}</Typography.Text>}
      <div style={{ maxHeight: 380, overflow: 'auto', border: '1px solid #f0f0f0', borderRadius: 6 }}>
        <List
          size="small"
          dataSource={[
            ...(data?.dirs ?? []).map((d) => ({ key: d.path, entry: d })),
            ...(data?.files ?? []).map((f) => ({ key: f.path, entry: f })),
          ]}
          locale={{ emptyText: '空目录' }}
          renderItem={({ entry }) => (
            <List.Item
              style={{ cursor: 'pointer', padding: '6px 12px' }}
              onClick={() => {
                if (entry.type === 'dir') {
                  if (entry.log) {
                    msgApi.warning('日志目录（*_log）不能作为处理目录')
                    return
                  }
                  load(entry.path)
                } else {
                  pick(entry.path)
                }
              }}
            >
              <Space>
                {entry.type === 'dir'
                  ? <FolderOpenOutlined style={{ color: entry.log ? '#bbb' : '#1677ff' }} />
                  : entry.type === 'video'
                    ? <PlayCircleFilled style={{ color: '#52c41a' }} />
                    : <FileOutlined style={{ color: '#faad14' }} />}
                <Typography.Text style={{ color: entry.log ? '#bbb' : undefined }}>
                  {entry.name}
                </Typography.Text>
                {entry.type !== 'dir' && (
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    点击选用此文件
                  </Typography.Text>
                )}
              </Space>
            </List.Item>
          )}
        />
      </div>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        点目录进入 · 点媒体文件直接选用 · 「选择此目录」批量处理当前目录（含子目录）
      </Typography.Text>
    </Modal>
  )
}

export default function BatchPage() {
  const [root, setRoot] = useState('')
  const [scan, setScan] = useState<ScanResult | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [batchState, setBatchState] = useState<string>('idle')
  const [lines, setLines] = useState<string[]>([])
  const [rerun, setRerun] = useState<RerunTarget | null>(null)
  const [rerunFrom, setRerunFrom] = useState<string | undefined>()
  const [rerunTo, setRerunTo] = useState<string | undefined>()
  const [browserOpen, setBrowserOpen] = useState(false)
  const [logRef] = [useRef<HTMLDivElement>(null)]
  const [msgApi, ctxHolder] = message.useMessage()
  const busyRef = useRef(false)

  const refreshRows = useCallback(async () => {
    if (!scan?.root) return
    try {
      const s = await scanBatch(scan.root)
      setScan(s)
    } catch { /* transient */ }
  }, [scan?.root])

  const onEvent = useCallback(
    (e: BusEvent) => {
      if (e.type === 'log' && e.line != null) {
        setLines((prev) => {
          const next = prev.length >= MAX_LOG_LINES ? prev.slice(prev.length - MAX_LOG_LINES + 1) : prev
          return [...next, e.line as string]
        })
      } else if (['batch_state', 'job_start', 'job_step', 'job_done', 'job_error'].includes(e.type)) {
        if (e.type === 'batch_state' && e.state) setBatchState(e.state as string)
        // throttle row refresh: job_step events can be rapid
        if (!busyRef.current) {
          busyRef.current = true
          setTimeout(() => {
            busyRef.current = false
            refreshRows()
          }, 800)
        }
      }
    },
    [refreshRows],
  )

  useBus(onEvent)
  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [lines])

  useEffect(() => {
    batchStatus().then((s) => {
      if (s.root) {
        setRoot(s.root)
        scanBatch(s.root).then(setScan).catch(() => {})
      }
      setBatchState(s.state)
    }).catch(() => {})
  }, [])

  const running = batchState === 'running'

  const act = async (fn: () => Promise<void>, ok?: string) => {
    try {
      await fn()
      if (ok) msgApi.success(ok)
    } catch (e) {
      msgApi.error(String(e))
    }
  }

  const doScan = () => act(async () => {
    const s = await scanBatch(root)
    setScan(s)
    // default selection: not-yet-done files
    setSelected(s.files.filter((f) => f.overall !== 'delivered' && f.overall !== 'done').map((f) => f.video))
  }, '扫描完成')

  const doScanPath = (p: string) => {
    setRoot(p)
    act(async () => {
      const s = await scanBatch(p)
      setScan(s)
      setSelected(s.files.filter((f) => f.overall !== 'delivered' && f.overall !== 'done').map((f) => f.video))
    }, '扫描完成')
  }

  const doStart = () => act(async () => {
    await startBatch({ files: selected })
  }, '批量任务已启动')

  const doRerunAll = () => act(async () => {
    await startBatch({ files: scan!.files.map((f) => f.video), force_restart: true })
  }, '全部重跑已启动')

  const doRerunFailed = () => act(async () => {
    const failed = scan!.files.filter((f) => f.overall === 'failed').map((f) => f.video)
    if (!failed.length) { msgApi.info('没有失败的文件'); return }
    await startBatch({ files: failed, force_restart: false })
  }, '失败重跑已启动')

  const submitRerun = () => act(async () => {
    if (!rerun) return
    const whole = !rerunFrom
    await startBatch({
      files: [rerun.video],
      force_restart: whole,
      reset_from: whole ? undefined : rerunFrom,
      reset_to: whole ? undefined : (rerunTo ?? undefined),
    })
    setRerun(null)
    setRerunFrom(undefined)
    setRerunTo(undefined)
  }, '重跑已启动')

  const stepIdOptions = useMemo(() => {
    const first = scan?.files[0]?.steps.map((s) => ({ value: s.id, label: STEP_LABEL[s.id] ?? s.id })) ?? []
    return first
  }, [scan])

  const columns: ColumnsType<BatchFileRow> = [
    {
      title: '', width: 36, dataIndex: 'video',
      render: (_, row) => (
        <Checkbox
          checked={selected.includes(row.video)}
          disabled={running}
          onChange={(e) => setSelected((prev) =>
            e.target.checked ? [...prev, row.video] : prev.filter((v) => v !== row.video))}
        />
      ),
    },
    {
      title: '文件', dataIndex: 'video',
      render: (v: string) => {
        const name = v.split(/[\\/]/).pop()
        const dir = v.slice(0, v.length - (name?.length ?? 0))
        return (
          <div style={{ minWidth: 0 }}>
            <Typography.Text strong ellipsis style={{ maxWidth: 260 }}>{name}</Typography.Text>
            <div><Typography.Text type="secondary" style={{ fontSize: 11 }} ellipsis>{dir}</Typography.Text></div>
          </div>
        )
      },
    },
    {
      title: '进度', dataIndex: 'steps', width: 190,
      render: (_: unknown, row) => (
        <div>
          <StepDots steps={row.steps} />
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              {row.done_count}/{row.total}
            </Typography.Text>
          </div>
        </div>
      ),
    },
    {
      title: '状态', dataIndex: 'overall', width: 90,
      render: (v: OverallState) => {
        const o = OVERALL_LABEL[v]
        return <Badge status={o.color as never} text={o.text} />
      },
    },
    {
      title: '操作', width: 130,
      render: (_: unknown, row) => (
        <Space size={4}>
          <Button size="small" icon={<FolderOpenOutlined />}
            onClick={() => act(async () => {
              const ls = await tailBatchLog(`${row.workspace}/output/log/asr_correct_session.log`, 100)
              setLines([`--- ${row.video} ---`, ...ls])
            })}>
            日志
          </Button>
          <Button size="small" icon={<RetweetOutlined />} disabled={running}
            onClick={() => setRerun({ video: row.video })}>
            重跑
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <div>
      {ctxHolder}
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space.Compact style={{ width: '100%', marginBottom: 8 }}>
          <Input
            placeholder="输入或浏览选择目录（含子目录批量）或单个视频/音频文件"
            value={root}
            onChange={(e) => setRoot(e.target.value)}
            onPressEnter={doScan}
            disabled={running}
          />
          <Button icon={<FolderOpenOutlined />} onClick={() => setBrowserOpen(true)} disabled={running}>
            浏览...
          </Button>
          <Button icon={<ReloadOutlined />} onClick={doScan} disabled={running || !root}>扫描</Button>
        </Space.Compact>
        {scan && (
          <Space size={16} wrap style={{ fontSize: 12 }}>
            <Typography.Text type="secondary">
              共 {scan.files.length} 个文件 · 日志目录 <code>{scan.log_root}</code>
            </Typography.Text>
            <span>状态：<Tag color={running ? 'processing' : 'default'}>{batchState}</Tag></span>
            <Typography.Link onClick={() => setSelected(scan.files.map((f) => f.video))}>全选</Typography.Link>
            <Typography.Link onClick={() => setSelected([])}>清空</Typography.Link>
          </Space>
        )}
      </Card>

      <Table
        size="small"
        rowKey="video"
        columns={columns}
        dataSource={scan?.files ?? []}
        pagination={false}
        locale={{ emptyText: '尚未扫描 — 输入目录后点击「扫描」' }}
      />

      <div style={{ display: 'flex', gap: 8, marginTop: 12, alignItems: 'center' }}>
        <Button type="primary" icon={<CaretRightOutlined />}
          disabled={running || !selected.length} onClick={doStart}>
          开始处理 ({selected.length} 个已选)
        </Button>
        <Popconfirm title="全部从头重跑（清空各文件工作区）？" disabled={running || !scan}
          onConfirm={doRerunAll}>
          <Button danger disabled={running || !scan?.files.length}>全部重跑</Button>
        </Popconfirm>
        <Button icon={<RetweetOutlined />} disabled={running || !scan?.files.length} onClick={doRerunFailed}>
          重跑失败的
        </Button>
        {running && (
          <Button danger icon={<StopOutlined />} onClick={() => act(stopBatch)}>停止</Button>
        )}
        <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 'auto' }}>
          重跑 = 可选步骤范围；完成后自动回送 &lt;文件名&gt;_trans_src.srt
        </Typography.Text>
      </div>

      <Card size="small" title="实时日志" style={{ marginTop: 12 }}>
        <div ref={logRef} style={{
          maxHeight: 260, overflow: 'auto', background: '#111', color: '#ccc',
          fontFamily: 'monospace', fontSize: 12, padding: '8px 10px',
          borderRadius: 6, whiteSpace: 'pre-wrap',
        }}>
          {lines.length ? lines.join('\n') : '（等待任务日志…）'}
        </div>
      </Card>

      <Modal
        open={!!rerun}
        title={`重跑 ${rerun?.video.split(/[\\/]/).pop() ?? ''}`}
        onCancel={() => { setRerun(null); setRerunFrom(undefined); setRerunTo(undefined) }}
        onOk={submitRerun}
        okText="开始重跑" cancelText="取消"
      >
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <div>
            <Typography.Text strong>整个重来</Typography.Text>
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0 }}>
              清空该文件工作区，从第一步重新开始
            </Typography.Paragraph>
            <Button danger style={{ marginTop: 4 }} onClick={() => { setRerunFrom(undefined); submitRerun() }}>
              整个重来
            </Button>
          </div>
          <div>
            <Typography.Text strong>选步骤范围</Typography.Text>
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 4 }}>
              前面步骤的产物直接复用（如换翻译模型：4→末尾）
            </Typography.Paragraph>
            <Space>
              <Select
                style={{ width: 160 }} placeholder="起始步骤"
                options={stepIdOptions} value={rerunFrom}
                onChange={setRerunFrom}
              />
              <span>→</span>
              <Select
                style={{ width: 160 }} placeholder="结束步骤（默认末尾）"
                options={stepIdOptions} value={rerunTo}
                onChange={setRerunTo}
              />
            </Space>
            <div style={{ marginTop: 8 }}>
              <Button type="primary" disabled={!rerunFrom} onClick={submitRerun}>
                按范围重跑
              </Button>
            </div>
          </div>
        </Space>
      </Modal>

      <FileBrowserModal
        open={browserOpen}
        onClose={() => setBrowserOpen(false)}
        onPick={doScanPath}
      />
    </div>
  )
}
