import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Button, Card, Checkbox, Col, InputNumber, Modal, Popconfirm,
  Row, Space, Spin, Table, Tabs, Tag, Typography, Input, Progress, Select,
  Tooltip, message,
} from 'antd'
import { PlusOutlined, ReloadOutlined, ExperimentOutlined, StopOutlined, SafetyOutlined, RedoOutlined, ImportOutlined } from '@ant-design/icons'
import {
  type LanguageDefaults, type PromptInfo, type ProbeResult, type Term, type TermSetInfo,
  type JobTermsReview, type CallLogEntry, type VerifyResult, getCallLog, getPrompts,
  getTermSet, getTermSets, previewSplit, probeOneChunk, resetPrompt, savePrompt,
  saveTermSet, setActiveTermSets, deleteTermSet, previewPrompt, verifyTranslation,
  writeBackTerms, type PreviewResult,
  type TermCatalog, type TermCatalogRow, type TermSource, type TermVariant,
} from '../translateApi'

const { TextArea } = Input
const { Text } = Typography

/** Sub-tab state lives in the URL hash (#/terms/prompts) so a refresh
 * keeps you where you were — same principle as the top-level pages. */
const SUB_KEYS = ['terms', 'prompts', 'playground', 'calls'] as const
type SubKey = typeof SUB_KEYS[number]

function subKeyFromHash(): SubKey {
  const h = window.location.hash.replace(/^#\/?/, '')  // e.g. "terms/prompts"
  const sub = h.split('/')[1]
  return (SUB_KEYS as readonly string[]).includes(sub) ? (sub as SubKey) : 'terms'
}

// ================= 术语库 =================

/** 旧后端（未重启）没有 catalog 字段时的兜底，避免页面直接崩 */
const EMPTY_CATALOG: TermCatalog = {
  rows: [],
  sources: [],
  stats: {
    total: 0, sets: 0, active: 0, base_entries: 0, job_available: false,
    job_count: 0, effective: 0, job_fresh: 0, conflicts: 0,
  },
}

type TermScope = 'all' | 'effective' | 'inactive' | 'fresh' | 'conflict'

function TermsTab() {
  const [sets, setSets] = useState<TermSetInfo[]>([])
  const [active, setActive] = useState<string[]>([])
  const [editing, setEditing] = useState<string | null>(null)  // null=列表, ''=新建
  const [editName, setEditName] = useState('')
  const [editDesc, setEditDesc] = useState('')
  const [terms, setTerms] = useState<Term[]>([])
  const [loading, setLoading] = useState(false)
  const [job, setJob] = useState<JobTermsReview | null>(null)
  const [catalog, setCatalog] = useState<TermCatalog>(EMPTY_CATALOG)
  const [showAll, setShowAll] = useState(true)
  const [termQuery, setTermQuery] = useState('')
  const [termScope, setTermScope] = useState<TermScope>('all')
  const [termSet, setTermSet] = useState('__all__')
  // 默认落在「任务术语表」：这就是 4.2 翻译用的完整表
  const [termTab, setTermTab] = useState('job')
  const [msgApi, ctx] = message.useMessage()
  const isNew = editing === ''

  const [wbOpen, setWbOpen] = useState(false)
  const [wbTarget, setWbTarget] = useState('')
  const [wbNewName, setWbNewName] = useState('')
  const [wbExisting, setWbExisting] = useState<Set<string>>(new Set())
  const [wbSelected, setWbSelected] = useState<string[]>([])
  const [wbBusy, setWbBusy] = useState(false)
  const plate = useMemo(() => new Set(job?.plate_srcs ?? []), [job])
  const sources = catalog.sources ?? []
  const baseSources = useMemo(
    () => sources.filter((s) => s.kind === 'base'), [catalog])
  const jobSource = useMemo(
    () => sources.find((s) => s.kind === 'job'), [catalog])
  // 旧后端（未重启）没有 sources 字段时退回 job.terms，页面不至于空白
  const jobTerms = useMemo<Term[]>(
    () => (jobSource ? jobSource.terms : job?.terms ?? []), [jobSource, job])
  const rowByKey = useMemo(
    () => new Map(catalog.rows.map((r) => [r.key, r])), [catalog])
  const freshFromPlate = useMemo(
    () => jobTerms.filter((t) => !plate.has(t.src.trim().toLowerCase())).length,
    [jobTerms, plate])

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const d = await getTermSets()
      setSets(d.sets); setActive(d.active); setJob(d.job)
      setCatalog(d.catalog ?? EMPTY_CATALOG)
      // 库被删后当前筛选 / 来源页签可能指向已不存在的库，回落到默认
      setTermSet((cur) => cur !== '__all__' && cur !== '__job__'
        && !d.sets.some((s) => s.name === cur) ? '__all__' : cur)
      setTermTab((cur) => !cur.startsWith('base:')
        || (d.catalog?.sources ?? []).some((s) => s.id === cur) ? cur : 'job')
    } catch (e) { msgApi.error(String(e)) } finally { setLoading(false) }
  }, [msgApi])

  useEffect(() => { reload() }, [reload])

  // 目标库切换时读一次已有 src，给表格标「已存在·跳过」
  useEffect(() => {
    if (!wbOpen) return
    const name = wbTarget === '__new__' ? wbNewName.trim() : wbTarget
    if (!name || !job?.sets.includes(name)) { setWbExisting(new Set()); return }
    let cancelled = false
    getTermSet(name).then((d) => {
      if (!cancelled) setWbExisting(new Set((d.terms || []).map((t) => t.src.trim().toLowerCase())))
    }).catch(() => { if (!cancelled) setWbExisting(new Set()) })
    return () => { cancelled = true }
  }, [wbOpen, wbTarget, wbNewName, job])

  const openEdit = async (name: string) => {
    try {
      const d = await getTermSet(name)
      setEditing(name); setEditName(name); setEditDesc(d.desc || ''); setTerms(d.terms || [])
    } catch (e) { msgApi.error(String(e)) }
  }

  const commitSave = async (overwrite: boolean) => {
    const name = editName.trim()
    if (!name) { msgApi.error('请填写术语库名'); return }
    try {
      const r = await saveTermSet(name, editDesc, terms, isNew, overwrite)
      msgApi.success(`已保存 ${r.name}（${r.count} 条）`)
      setEditing(null); reload()
    } catch (e) {
      if (isNew && !overwrite && String(e).includes('已存在')) {
        Modal.confirm({
          title: '覆盖同名术语库？',
          content: `${name} 已存在，覆盖会替换它的全部词条。`,
          okText: '覆盖', okButtonProps: { danger: true }, cancelText: '取消',
          onOk: () => { void commitSave(true) },
        })
        return
      }
      msgApi.error(String(e))
    }
  }

  const doSave = () => { if (editing !== null) void commitSave(false) }

  const toggleActive = async (name: string, checked: boolean) => {
    const next = checked ? [...active, name] : active.filter((n) => n !== name)
    try {
      await setActiveTermSets(next); setActive(next)
      // 勾选只是记下底板；任务术语表在 4.1 冻结，别让用户以为立刻变了
      msgApi.success(checked ? `已勾选 ${name}：重跑第 4.1 步才会把它写进 output/log/terminology.json`
        : `已取消勾选 ${name}：重跑第 4.1 步才会更新任务术语表`, 5)
    } catch (e) { msgApi.error(String(e)) }
  }

  const openWriteBack = (picked?: string[]) => {
    if (!job?.available) {
      msgApi.warning('当前没有任务术语表：请先跑第 4.1 步（总结与抽术语）')
      return
    }
    // 没传就用「4.1 新抽」做默认勾选；从任务表清单点进来则沿用页面上勾好的行
    setWbSelected(picked ?? job.terms
      .filter((t) => !plate.has(t.src.trim().toLowerCase())).map((t) => t.src))
    setWbTarget(job.sets[0] ?? '__new__')
    setWbNewName(''); setWbExisting(new Set()); setWbOpen(true)
  }

  const doWriteBack = async () => {
    const name = (wbTarget === '__new__' ? wbNewName : wbTarget).trim()
    if (!name) { msgApi.error('请选择或填写目标术语库'); return }
    if (!wbSelected.length) { msgApi.error('请勾选至少一条术语'); return }
    setWbBusy(true)
    try {
      const r = await writeBackTerms({ target: name, srcs: wbSelected })
      msgApi.success(`已回写 ${r.added} 条到 ${r.target}${r.skipped ? `，跳过 ${r.skipped} 条已存在` : ''}`)
      setWbOpen(false); reload()
    } catch (e) { msgApi.error(String(e)) } finally { setWbBusy(false) }
  }

  const wbColumns = [
    { title: '原文 src', dataIndex: 'src', width: '24%' },
    { title: '译文 tgt', dataIndex: 'tgt', width: '24%' },
    { title: '备注 note', dataIndex: 'note', ellipsis: true },
    { title: '来源', width: 96, render: (_: unknown, r: Term) =>
      <Tag color={plate.has(r.src.trim().toLowerCase()) ? 'default' : 'blue'}>
        {plate.has(r.src.trim().toLowerCase()) ? '来自底板' : '4.1 新抽'}
      </Tag> },
    { title: '目标库', width: 112, render: (_: unknown, r: Term) =>
      wbExisting.has(r.src.trim().toLowerCase())
        ? <Tag color="orange">已存在·跳过</Tag> : <Tag color="green">可写入</Tag> },
  ]

  // ---------- 全部术语总表（所有基础库 + 当前任务术语表） ----------
  const filteredRows = useMemo(() => {
    const q = termQuery.trim().toLowerCase()
    return catalog.rows.filter((r) => {
      if (q && ![r.src, r.tgt, r.note, ...r.sets]
        .some((v) => String(v).toLowerCase().includes(q))) return false
      if (termScope === 'effective' && !r.effective) return false
      if (termScope === 'inactive' && r.effective) return false
      if (termScope === 'fresh' && !r.job_fresh) return false
      if (termScope === 'conflict' && !(r.conflict || r.drift)) return false
      if (termSet === '__job__' && !r.in_job) return false
      if (termSet !== '__all__' && termSet !== '__job__'
          && !r.sets.includes(termSet)) return false
      return true
    })
  }, [catalog, termQuery, termScope, termSet])

  const catalogColumns = [
    { title: '原文 src', dataIndex: 'src', width: '20%',
      render: (v: string, r: TermCatalogRow) => <Space size={4} wrap>
        <Text strong>{v}</Text>
        {r.conflict && <Tooltip title="多套基础库对这个 src 的译文不一致，展开可逐库对照">
          <Tag color="orange">冲突</Tag></Tooltip>}
        {r.drift && <Tooltip title="4.1 冻结的译文与当前所有基础库取值都不同；4.2 仍用任务术语表里的值">
          <Tag color="red">已漂移</Tag></Tooltip>}
      </Space> },
    { title: '4.2 生效译文', dataIndex: 'tgt', width: '20%', ellipsis: true },
    { title: '备注 note', dataIndex: 'note', ellipsis: true },
    { title: '来源库 / 任务表', width: 200,
      render: (_: unknown, r: TermCatalogRow) => <Space size={4} wrap>
        {r.sets.map((n) => <Tooltip key={n} title="点击编辑该基础库">
          <Tag color={active.includes(n) ? 'green' : 'default'}
            style={{ cursor: 'pointer' }} onClick={() => openEdit(n)}>{n}</Tag>
        </Tooltip>)}
        {r.job_fresh && <Tag color="blue">4.1 新抽</Tag>}
      </Space> },
    { title: '状态', width: 108, render: (_: unknown, r: TermCatalogRow) =>
      r.effective ? <Tag color="green">4.2 生效</Tag> : <Tag>未生效</Tag> },
  ]

  const keyOf = (t: Term) => t.src.trim().toLowerCase()

  // 任务术语表 = 完整清单（底板 + 新抽），每行可直接勾选去回写
  const jobColumns = [
    { title: '原文 src', dataIndex: 'src', width: '20%',
      render: (v: string) => <Text strong>{v}</Text> },
    { title: '译文 tgt', dataIndex: 'tgt', width: '22%', ellipsis: true },
    { title: '备注 note', dataIndex: 'note', ellipsis: true },
    { title: '来源', width: 140, render: (_: unknown, r: Term) =>
      <Tag color={plate.has(keyOf(r)) ? 'default' : 'blue'}>
        {plate.has(keyOf(r)) ? '来自勾选底板' : '4.1 新抽'}
      </Tag> },
    { title: '已沉淀到基础库', width: 180, render: (_: unknown, r: Term) => {
      const names = rowByKey.get(keyOf(r))?.sets ?? []
      return names.length
        ? <Space size={4} wrap>{names.map((n) => <Tag key={n}>{n}</Tag>)}</Space>
        : <Tag color="orange">仅任务表</Tag>
    } },
  ]

  // 每一套基础库自己的完整清单（不与其他库折叠）
  const sourceColumns = (s: TermSource) => [
    { title: '原文 src', dataIndex: 'src', width: '22%',
      render: (v: string) => <Text strong>{v}</Text> },
    { title: '译文 tgt', dataIndex: 'tgt', width: '24%', ellipsis: true },
    { title: '备注 note', dataIndex: 'note', ellipsis: true },
    { title: '4.2 生效译文', width: '20%', ellipsis: true,
      render: (_: unknown, r: Term) => {
        const row = rowByKey.get(keyOf(r))
        return row?.effective
          ? <Text>{row.tgt || '—'}</Text> : <Text type="secondary">— 未生效</Text>
      } },
    { title: '状态', width: 150, render: (_: unknown, r: Term) => {
      const row = rowByKey.get(keyOf(r))
      return <Space size={4} wrap>
        <Tag color={s.active ? 'green' : 'default'}>{s.active ? '已勾选' : '未勾选'}</Tag>
        {row?.in_job && <Tag color="blue">在任务表</Tag>}
      </Space>
    } },
  ]

  if (editing !== null) {
    return (
      <Card title={isNew ? '新建术语库' : `编辑术语库：${editing}`} extra={
        <Space>
          <Button onClick={() => setTerms([...terms, { src: '', tgt: '', note: '' }])} icon={<PlusOutlined />}>加一条</Button>
          <Button onClick={() => setEditing(null)}>取消</Button>
          <Button type="primary" onClick={doSave}>保存</Button>
        </Space>
      }>
        {ctx}
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Input addonBefore="库名" value={editName} disabled={!isNew}
            onChange={(e) => setEditName(e.target.value)}
            placeholder="中文/字母/数字/-/_，最长 48 字符" />
          <Input addonBefore="描述" value={editDesc}
            onChange={(e) => setEditDesc(e.target.value)} placeholder="描述（可选）" />
          <Table<Term> rowKey={(_, i) => String(i)} dataSource={terms} pagination={false} size="small"
            columns={[
              { title: '原文 src', dataIndex: 'src', width: '28%',
                render: (_, r, i) => <TextArea autoSize rows={1} value={r.src}
                  onChange={(e) => setTerms(terms.map((t, j) => j === i ? { ...t, src: e.target.value } : t))} /> },
              { title: '译文 tgt', dataIndex: 'tgt', width: '28%',
                render: (_, r, i) => <TextArea autoSize rows={1} value={r.tgt}
                  onChange={(e) => setTerms(terms.map((t, j) => j === i ? { ...t, tgt: e.target.value } : t))} /> },
              { title: '备注 note', dataIndex: 'note',
                render: (_, r, i) => <TextArea autoSize rows={1} value={r.note}
                  onChange={(e) => setTerms(terms.map((t, j) => j === i ? { ...t, note: e.target.value } : t))} /> },
              { title: '', width: 70, render: (_, __, i) =>
                <Button danger size="small" onClick={() => setTerms(terms.filter((_, j) => j !== i))}>删除</Button> },
            ]} />
        </Space>
      </Card>
    )
  }

  return (
    <Card title="基础术语库（terms/*.json，多套激活时按顺序合并，同 src 后者覆盖）"
      extra={<Space>
        <Button onClick={() => setShowAll((v) => !v)}>
          {showAll ? '收起全部术语' : '查看全部术语'}
        </Button>
        <Button icon={<ImportOutlined />} onClick={() => openWriteBack()}>从任务术语表回写</Button>
        <Button icon={<ReloadOutlined />} onClick={reload} />
        <Button icon={<PlusOutlined />} onClick={() => {
          setEditing(''); setEditName(''); setEditDesc(''); setTerms([])
        }}>新建</Button>
      </Space>}>
      {ctx}
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        message="术语表怎么跑：基础库 → 任务术语表 → 翻译 → 回写"
        description={<Space direction="vertical" size={4} style={{ width: '100%' }}>
          <Text>① <Text code>terms/*.json</Text> 是基础术语库，可以按分类 / 人物 / 行业分成多套，长期保存、不随任务清空。
            ② 每接一个新文本，勾选要用的套当「底板」；4.1 生成 <Text code>output/log/terminology.json</Text>
            = 底板 + 从新文本重新抽到的术语（按 src 去重，底板的人工译文优先）。
            ③ 4.2 翻译只用这张完整术语表。④ 表里的词条可以勾选回写到你指定的基础库。</Text>
          <Space wrap size={4}>
            <Tag color={active.length ? 'green' : 'default'}>
              当前勾选 {active.length} 套：{active.join(' / ') || '无（0 套）'}
            </Tag>
            <Tag>底板 {plate.size} 条</Tag>
            <Tag color={job?.available ? 'blue' : 'default'}>
              任务术语表 {jobTerms.length} 条
            </Tag>
            <Tag>其中本次新抽 {freshFromPlate} 条</Tag>
            <Tag color="orange">尚未沉淀进任何基础库 {catalog.stats.job_fresh} 条</Tag>
          </Space>
          <Text type="secondary">勾选只是在 config.yaml 里记下底板，不会立刻改动当前任务术语表；
            要让它变成「这套库 + 新抽」，必须重跑第 4.1 步（会重新调用 LLM 抽术语，术语一变 4.2 的断点与 GPT 缓存都会作废）。
            回写只写基础库，不改任务术语表、不影响本次翻译。</Text>
        </Space>} />
      <Spin spinning={loading}>
        <Table<TermSetInfo> rowKey="name" dataSource={sets} pagination={false} size="middle"
          columns={[
            { title: '名称', dataIndex: 'name', render: (n) => <Text strong>{n}</Text> },
            { title: '描述', dataIndex: 'desc', ellipsis: true },
            { title: '词条数', dataIndex: 'count', width: 90 },
            { title: '激活', width: 90, render: (_, r) =>
              <Checkbox checked={active.includes(r.name)} onChange={(e) => toggleActive(r.name, e.target.checked)} /> },
            { title: '', width: 150, render: (_, r) => <Space>
              <Button size="small" onClick={() => openEdit(r.name)}>编辑</Button>
              <Popconfirm title={`删除 ${r.name}？`} onConfirm={async () => {
                try { await deleteTermSet(r.name); msgApi.success('已删除'); reload() }
                catch (e) { msgApi.error(String(e)) }
              }}>
                <Button size="small" danger disabled={active.includes(r.name)}>删除</Button>
              </Popconfirm>
            </Space> },
          ]} />
      </Spin>
      {showAll && (
        <Card type="inner" size="small" style={{ marginTop: 16 }}
          title={<Space wrap size={4}>
            <Text strong>全部术语（按文件完整展示）</Text>
            <Tag color="blue">{catalog.stats.total} 个不同原文</Tag>
            <Tag>{catalog.stats.sets} 套基础库 / {catalog.stats.base_entries} 条</Tag>
            <Tag color={catalog.stats.job_available ? 'green' : 'default'}>
              {catalog.stats.job_available
                ? `任务术语表 ${catalog.stats.job_count} 条` : '无任务术语表'}
            </Tag>
            <Tag color="green">4.2 生效 {catalog.stats.effective}</Tag>
            {catalog.stats.job_fresh > 0 && <Tag color="blue">4.1 新抽 {catalog.stats.job_fresh}</Tag>}
            {catalog.stats.conflicts > 0 && <Tag color="orange">冲突 {catalog.stats.conflicts}</Tag>}
          </Space>}>
          <Tabs activeKey={termTab} onChange={setTermTab} items={[
            {
              key: 'job',
              label: <Space size={4}>任务术语表（4.2 使用）<Tag color="blue">{jobTerms.length}</Tag></Space>,
              children: <Space direction="vertical" style={{ width: '100%' }} size="small">
                <Alert type={job?.available ? 'success' : 'warning'} showIcon
                  message={job?.available
                    ? `output/log/terminology.json · 共 ${jobTerms.length} 条，4.2 翻译只用这张表`
                    : '当前没有任务术语表'}
                  description={job?.available
                    ? `= 勾选底板 ${jobTerms.length - freshFromPlate} 条 + 4.1 从新文本抽到 ${freshFromPlate} 条，按 src 去重（底板译文优先）。改勾选或改基础库后，要重跑第 4.1 步才会重新生成这份文件。`
                    : '跑完第 4.1 步（总结与抽术语）后，这里会列出「勾选底板 + 新抽」的完整术语表；那时才能勾选回写进基础库。'} />
                <Space wrap>
                  <Button type="primary" icon={<ImportOutlined />} onClick={() => {
                    if (!wbSelected.length) { msgApi.warning('先在下面表格勾选要回写的词条'); return }
                    openWriteBack(wbSelected)
                  }}>勾选回写到基础库</Button>
                  <Button onClick={() => setWbSelected(
                    jobTerms.filter((t) => !plate.has(keyOf(t))).map((t) => t.src))}>全选 4.1 新抽</Button>
                  <Button onClick={() => setWbSelected(jobTerms.map((t) => t.src))}>全选</Button>
                  <Button onClick={() => setWbSelected([])}>清空选择</Button>
                  <Text type="secondary">已勾选 {wbSelected.length} 条 · 回写只改基础库，不动这张表</Text>
                </Space>
                <Spin spinning={loading}>
                  <Table<Term> rowKey={(r) => r.src} dataSource={jobTerms} size="small"
                    locale={{ emptyText: '还没有任务术语表：请先跑第 4.1 步（总结与抽术语）' }}
                    pagination={{ pageSize: 20, showSizeChanger: true,
                      showTotal: (t) => `共 ${t} 条` }}
                    rowSelection={{ selectedRowKeys: wbSelected,
                      onChange: (keys) => setWbSelected(keys.map(String)) }}
                    columns={jobColumns} />
                </Spin>
              </Space>,
            },
            ...baseSources.map((s) => ({
              key: s.id,
              label: <Space size={4}>{s.name}<Tag>{s.count}</Tag></Space>,
              children: <Space direction="vertical" style={{ width: '100%' }} size="small">
                <Space wrap>
                  <Text code>{s.path}</Text>
                  <Tag color={s.active ? 'green' : 'default'}>
                    {s.active ? '已勾选为底板' : '未勾选'}
                  </Tag>
                  <Tag>{s.count} 条</Tag>
                  <Button size="small" onClick={() => openEdit(s.name)}>编辑该库</Button>
                </Space>
                <Alert type="info" showIcon message="基础术语库（按分类 / 人物 / 行业分套存放）"
                  description={s.active
                    ? '已勾选：重跑 4.1 时这些词条会作为底板并入 output/log/terminology.json，再叠加新抽术语并按 src 去重。'
                    : '未勾选：词条完整保留在库里，但不会进入本次任务的翻译；勾选后重跑 4.1 才生效。'} />
                <Table<Term> rowKey={(r) => r.src} dataSource={s.terms} size="small"
                  pagination={{ pageSize: 20, showSizeChanger: true,
                    showTotal: (t) => `共 ${t} 条` }}
                  columns={sourceColumns(s)} />
              </Space>,
            })),
            {
              key: 'union',
              label: <Space size={4}>并集总表<Tag>{catalog.stats.total}</Tag></Space>,
              children: <>
                <Alert type="info" showIcon style={{ marginBottom: 10 }}
                  message="按 src 去重后的全局视图"
                  description="同一个原文出现在多套基础库里时只占一行，展开可逐库对照；「生效」只指 4.2 真正会注入 prompt 的值。要看某一个文件的每一条，请切到上面的来源页签。" />
                <Space wrap style={{ marginBottom: 10 }}>
                  <Input.Search allowClear style={{ width: 240 }} value={termQuery}
                    onChange={(e) => setTermQuery(e.target.value)}
                    placeholder="搜 原文 / 译文 / 备注 / 库名" />
                  <Select style={{ width: 150 }} value={termScope}
                    onChange={(v) => setTermScope(v as TermScope)}
                    options={[
                      { label: '全部范围', value: 'all' },
                      { label: '仅 4.2 生效', value: 'effective' },
                      { label: '仅未生效', value: 'inactive' },
                      { label: '仅 4.1 新抽', value: 'fresh' },
                      { label: '仅冲突 / 漂移', value: 'conflict' },
                    ]} />
                  <Select style={{ width: 180 }} value={termSet}
                    onChange={(v) => setTermSet(v)}
                    options={[
                      { label: '全部来源', value: '__all__' },
                      { label: '任务术语表', value: '__job__' },
                      ...sets.map((s) => ({ label: s.name, value: s.name })),
                    ]} />
                  <Text type="secondary">点来源库标签可直接编辑；展开行可逐库对照译文。</Text>
                </Space>
                <Spin spinning={loading}>
                  <Table<TermCatalogRow> rowKey="key" dataSource={filteredRows} size="small"
                    pagination={{ pageSize: 20, showSizeChanger: true,
                      showTotal: (t) => `共 ${t} 条` }}
                    expandable={{
                      expandedRowRender: (r) => <Space direction="vertical" size="small"
                        style={{ width: '100%' }}>
                        <Text type="secondary">
                          4.2 生效值（{catalog.stats.job_available ? '任务术语表' : '当前勾选底板'}）：
                          <Text strong>{r.tgt || '—'}</Text>
                          {r.note ? ` — ${r.note}` : ''}
                        </Text>
                        {r.variants.length ? (
                          <Table<TermVariant> rowKey={(v) => `${v.set}:${v.src}`}
                            dataSource={r.variants} size="small" pagination={false}
                            columns={[
                              { title: '基础库', dataIndex: 'set', width: 150,
                                render: (n) => <Text strong>{n}</Text> },
                              { title: '原文', dataIndex: 'src', width: '22%' },
                              { title: '译文', dataIndex: 'tgt', width: '22%' },
                              { title: '备注', dataIndex: 'note', ellipsis: true },
                              { title: '状态', width: 100, render: (_: unknown, v: TermVariant) =>
                                v.active ? <Tag color="green">已激活</Tag> : <Tag>未激活</Tag> },
                            ]} />
                        ) : (
                          <Alert type="info" showIcon
                            message="只存在于任务术语表（4.1 新抽），尚未回写任何基础库"
                            description="点上方「从任务术语表回写」，勾选后即可沉淀进某个基础库。" />
                        )}
                      </Space>,
                    }}
                    columns={catalogColumns} />
                </Spin>
              </>,
            },
          ]} />
        </Card>
      )}
      <Modal title="从任务术语表回写" open={wbOpen} onCancel={() => setWbOpen(false)}
        width={880} onOk={doWriteBack} confirmLoading={wbBusy}
        okText="回写" cancelText="取消">
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Alert type="warning" showIcon message="只写基础库，不改任务术语表"
            description="本次翻译、断点指纹、GPT 缓存都不受影响（零费用）；下次勾选该库并跑到 4.1 才生效。目标库里已存在的同名 src 默认跳过，保留你手工维护的译文。" />
          <Space wrap>
            <span>目标库</span>
            <Select style={{ width: 220 }} value={wbTarget} onChange={(v) => setWbTarget(v)}
              options={[...(job?.sets ?? []).map((n) => ({ label: n, value: n })),
                { label: '＋ 新建术语库…', value: '__new__' }]} />
            {wbTarget === '__new__' && <Input style={{ width: 220 }} value={wbNewName}
              onChange={(e) => setWbNewName(e.target.value)}
              placeholder="新库名（中文/字母/数字/-/_）" />}
          </Space>
          <Table<Term> rowKey={(r) => r.src} dataSource={job?.terms ?? []} size="small"
            pagination={{ pageSize: 8 }} columns={wbColumns}
            rowSelection={{ selectedRowKeys: wbSelected,
              onChange: (keys) => setWbSelected(keys.map(String)) }} />
          <Text type="secondary">已勾选 {wbSelected.length} 条 · 默认只勾「4.1 新抽」，来自底板的词条通常无需重复入库</Text>
        </Space>
      </Modal>
    </Card>
  )
}

// ================= 提示词 =================

function PromptsTab() {
  const [prompts, setPrompts] = useState<PromptInfo[]>([])
  const [langDefaults, setLangDefaults] = useState<LanguageDefaults>({ src_lang: '', tgt_lang: '' })
  const [editing, setEditing] = useState<PromptInfo | null>(null)
  const [content, setContent] = useState('')
  const [sample, setSample] = useState('')
  const [srcLang, setSrcLang] = useState('')
  const [tgtLang, setTgtLang] = useState('')
  const [rendered, setRendered] = useState('')
  const [rendering, setRendering] = useState(false)
  const [msgApi, ctx] = message.useMessage()

  const reload = useCallback(async () => {
    try {
      const d = await getPrompts()
      setPrompts(d.prompts)
      setLangDefaults(d.language_defaults)
    }
    catch (e) { msgApi.error(String(e)) }
  }, [msgApi])
  useEffect(() => { reload() }, [reload])

  const doPreviewPrompt = async () => {
    if (!editing) return
    setRendering(true)
    try {
      // preview renders the CURRENT editor content in memory — no save
      const out = await previewPrompt({
        name: editing.name, sample, content,
        src_lang: srcLang || undefined, tgt_lang: tgtLang || undefined,
      })
      setRendered(out.prompt)
      setLangDefaults(out.language_defaults)
      msgApi.success('已按当前编辑内容渲染（未保存）')
    } catch (e) { msgApi.error(String(e)) } finally { setRendering(false) }
  }

  if (editing) {
    return (
      <Card title={`提示词：${editing.name}`} extra={
        <Space>
          <Button onClick={() => setEditing(null)}>取消</Button>
          <Popconfirm title="删除覆盖文件，恢复内置默认？" onConfirm={async () => {
            try { await resetPrompt(editing.name); msgApi.success('已恢复默认'); setEditing(null); reload() }
            catch (e) { msgApi.error(String(e)) }
          }}>
            <Button danger>恢复默认</Button>
          </Popconfirm>
          <Button type="primary" onClick={async () => {
            try { await savePrompt(editing.name, content); msgApi.success('已保存'); setEditing(null); reload() }
            catch (e) { msgApi.error(String(e)) }
          }}>保存</Button>
        </Space>
      }>
        {ctx}
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="占位符 {{var}} 会被替换成运行时变量（写错会原样保留，不会崩溃）。留空保存 = 恢复默认。" />
        <TextArea rows={18} value={content} onChange={(e) => setContent(e.target.value)}
          style={{ fontFamily: 'monospace' }} />
        <Card type="inner" title="预览效果 — 用示例句子渲染出实际发给 LLM 的完整请求（免费，不调 LLM，不保存）"
          style={{ marginTop: 16 }}>
          <TextArea rows={3} value={sample} onChange={(e) => setSample(e.target.value)}
            placeholder="输入示例句子（每行一句），留空用内置示例" style={{ marginBottom: 8 }} />
          <Space wrap style={{ marginBottom: 8 }}>
            <Text type="secondary">源语言 src_lang</Text>
            <Input style={{ width: 140 }} value={srcLang}
              onChange={(e) => setSrcLang(e.target.value)}
              placeholder={langDefaults.src_lang || 'English'} />
            <Text type="secondary">目标语言 tgt_lang</Text>
            <Input style={{ width: 140 }} value={tgtLang}
              onChange={(e) => setTgtLang(e.target.value)}
              placeholder={langDefaults.tgt_lang || '简体中文'} />
            <Button loading={rendering} onClick={doPreviewPrompt}>渲染预览</Button>
          </Space>
          {rendered && (
            <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontFamily: 'monospace',
              background: '#fafafa', border: '1px solid #eee', padding: 12,
              borderRadius: 6, maxHeight: 400, overflow: 'auto' }}>
              {rendered}
            </pre>
          )}
        </Card>
      </Card>
    )
  }

  return (
    <Card title="提示词（prompts/*.md，存在即覆盖内置默认；删除文件 = 恢复默认）">
      {ctx}
      <Table<PromptInfo> rowKey="name" dataSource={prompts} pagination={false} size="middle"
        columns={[
          { title: '名称', dataIndex: 'name', render: (n) => <Text strong>{n}</Text> },
          { title: '用途', dataIndex: 'desc' },
          { title: '状态', dataIndex: 'overridden', width: 120, render: (o) =>
            o ? <Tag color="orange">已自定义</Tag> : <Tag>默认</Tag> },
          { title: '', width: 90, render: (_, r) => <Button size="small" onClick={() => {
            setEditing(r); setContent(r.content)
            setSrcLang(langDefaults.src_lang); setTgtLang(langDefaults.tgt_lang)
          }}>编辑</Button> },
        ]} />
    </Card>
  )
}

// ================= 切分实验场 =================

function PlaygroundTab() {
  const [text, setText] = useState('')
  const [useSplitFile, setUseSplitFile] = useState(false)
  const [chunkSize, setChunkSize] = useState<number>(2400)
  const [maxSents, setMaxSents] = useState<number>(10)
  const [preview, setPreview] = useState<PreviewResult | null>(null)
  const [selected, setSelected] = useState<number[]>([])
  const [previewing, setPreviewing] = useState(false)
  const previewAbort = useRef<AbortController | null>(null)
  // progressive probe: one request per chunk, results stream in
  const [probe, setProbe] = useState<ProbeResult | null>(null)
  const [probing, setProbing] = useState(false)
  const [probeProgress, setProbeProgress] = useState({ done: 0, total: 0 })
  const probeAbort = useRef<AbortController | null>(null)
  const [useCache, setUseCache] = useState(false)
  const [msgApi, ctx] = message.useMessage()

  const doPreview = async () => {
    // a new preview cancels the one in flight — no stacking of requests
    previewAbort.current?.abort()
    const ac = new AbortController()
    previewAbort.current = ac
    setPreviewing(true)
    try {
      const d = await previewSplit({
        text: useSplitFile ? undefined : text, use_split_file: useSplitFile,
        chunk_size: chunkSize, max_sentences: maxSents,
      }, ac.signal)
      setPreview(d); setSelected([]); setProbe(null)
    } catch (e) {
      if ((e as Error).name !== 'AbortError') msgApi.error(String(e))
    } finally {
      if (previewAbort.current === ac) { setPreviewing(false); previewAbort.current = null }
    }
  }

  const doProbe = async () => {
    if (!selected.length) { msgApi.warning('先勾选要实测的块'); return }
    probeAbort.current?.abort()
    const ac = new AbortController()
    probeAbort.current = ac
    setProbing(true)
    setProbeProgress({ done: 0, total: selected.length })
    setProbe({ results: [], summary: { probed: 0, ok: 0, failed: 0, chunk_total: preview?.stats.count || 0, params: { chunk_size: chunkSize, max_sentences: maxSents, reflect: false } } })
    try {
      // one request per chunk — each returns as soon as its LLM call ends,
      // so progress is visible chunk-by-chunk and abort works cleanly
      const results: ProbeResult['results'] = []
      for (let i = 0; i < selected.length; i++) {
        const d = await probeOneChunk({
          text: useSplitFile ? undefined : text, use_split_file: useSplitFile,
          chunk_size: chunkSize, max_sentences: maxSents, chunk_index: selected[i],
          use_cache: useCache,
        }, ac.signal)
        results.push(...d.results)
        setProbeProgress({ done: i + 1, total: selected.length })
        setProbe({ results: [...results], summary: { ...d.summary, probed: results.length, chunk_total: preview?.stats.count || d.summary.chunk_total } })
      }
      const ok = results.filter((r) => r.ok).length
      msgApi[ok === results.length ? 'success' : 'warning'](`实测 ${results.length} 块：${ok} 成功 / ${results.length - ok} 失败`)
    } catch (e) {
      if ((e as Error).name !== 'AbortError') msgApi.error(String(e))
      else msgApi.info('已停止实测')
    } finally {
      if (probeAbort.current === ac) { setProbing(false); probeAbort.current = null }
    }
  }

  const stopProbe = () => probeAbort.current?.abort()

  // L4 verification, per result row: {chunkIndex -> verify result}
  const [verifies, setVerifies] = useState<Record<number, VerifyResult>>({})
  const [verifyingIdx, setVerifyingIdx] = useState<number | null>(null)
  const [verifyUseCache, setVerifyUseCache] = useState(true)

  const doVerify = async (idx: number, source: string, translation: string, fresh = false) => {
    setVerifyingIdx(idx)
    try {
      const v = await verifyTranslation({ source, translation, use_cache: !fresh })
      setVerifies((prev) => ({ ...prev, [idx]: v }))
      if (!v.ok) msgApi.error(`校验失败: ${v.error?.slice(0, 120)}`)
    } catch (e) { msgApi.error(String(e)) } finally { setVerifyingIdx(null) }
  }

  return (
    <Card title="切分实验场 — L1 预览(免费) / L2 成本(免费) / L3 实测(按块计费，走 translate_faith 链，不写正式产物)">
      {ctx}
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Space wrap>
          <Checkbox checked={useSplitFile} onChange={(e) => setUseSplitFile(e.target.checked)}>
            载入本任务切句结果 (split_by_nlp.txt)
          </Checkbox>
          <Text type="secondary">否则用下方粘贴文本</Text>
        </Space>
        {!useSplitFile && (
          <TextArea rows={6} value={text} onChange={(e) => setText(e.target.value)}
            placeholder="粘贴要测试的文本（每行一句）" />
        )}
        <Space wrap>
          <Text>分块字符数</Text>
          <InputNumber min={200} max={16000} step={200} value={chunkSize} onChange={(v) => setChunkSize(v || 2400)} />
          <Text>每块句数上限</Text>
          <InputNumber min={1} max={100} value={maxSents} onChange={(v) => setMaxSents(v || 10)} />
          <Button type="primary" loading={previewing} onClick={doPreview}>
            {previewing ? '切分中…' : '预览切分 (L1/L2)'}
          </Button>
          <Popconfirm title="清空输入和所有结果？（含实测/校验结果）" onConfirm={() => {
            setText(''); setUseSplitFile(false); setChunkSize(2400); setMaxSents(10)
            setPreview(null); setSelected([]); setProbe(null); setVerifies({})
            setProbeProgress({ done: 0, total: 0 })
          }}>
            <Button danger type="text">清空</Button>
          </Popconfirm>
        </Space>

        {previewing && (
          <Progress percent={100} status="active"
            format={() => '正在切分并渲染上传估算…'} />
        )}

        {preview && !previewing && (
          <>
            <Row gutter={12}>
              <Col><Alert type="info" message={`共 ${preview.stats.count} 块 · 字符 avg ${preview.stats.chars.avg} / max ${preview.stats.chars.max} · 句数 max ${preview.stats.sentences.max} · 术语 ${preview.estimate.terms_count} 条（勾选底板 + 4.1 新抽）`} /></Col>
              <Col><Alert type="warning" showIcon message={`预计 LLM 调用 ${preview.estimate.llm_calls} 次（每块 ${preview.estimate.calls_per_chunk} 次${preview.estimate.reflect ? '，含意译' : ''}，仅直译）`} /></Col>
              <Col><Alert type="success" showIcon message={`上传总量约 ${preview.estimate.prompt_total_chars.toLocaleString()} 字符（avg ${preview.estimate.prompt_avg_chars.toLocaleString()} / 块，其中共享上下文 ${preview.estimate.context_chars.toLocaleString()} 字符/块）`} /></Col>
            </Row>
            <Table size="small" rowKey="index" pagination={{ pageSize: 10 }}
              dataSource={preview.chunks}
              rowSelection={{ selectedRowKeys: selected, onChange: (keys) => setSelected(keys as number[]) }}
              expandable={{
                expandedRowRender: (r) => (
                  <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontFamily: 'inherit', maxHeight: 300, overflow: 'auto' }}>
                    {r.preview}
                  </pre>
                ),
                rowExpandable: () => true,
              }}
              columns={[
                { title: '#', dataIndex: 'index', width: 60 },
                { title: '字符', dataIndex: 'chars', width: 80 },
                { title: '句数', dataIndex: 'sentences', width: 70 },
                { title: '上传字符', dataIndex: 'prompt_chars', width: 90,
                  render: (v) => <Text type="secondary">{v.toLocaleString()}</Text> },
                { title: '内容预览', dataIndex: 'preview', ellipsis: true },
              ]} />
            <Space wrap>
              {!probing ? (
                <Button icon={<ExperimentOutlined />} onClick={doProbe}>
                  实测选中 {selected.length} 块 (L3)
                </Button>
              ) : (
                <Button icon={<StopOutlined />} danger onClick={stopProbe}>
                  停止实测
                </Button>
              )}
              <Tooltip title="勾选：prompt 相同的块直接回放缓存结果（免费、秒回）；不勾：每次都发真实请求（花钱、记调用日志）。改过参数/术语/总结后请关掉重测。">
                <Checkbox checked={useCache} onChange={(e) => setUseCache(e.target.checked)}>
                  用缓存（免费回放）
                </Checkbox>
              </Tooltip>
              {probing ? (
                <Space>
                  <Progress type="circle" size={20}
                    percent={Math.round((probeProgress.done / Math.max(probeProgress.total, 1)) * 100)} />
                  <Text>实测中 {probeProgress.done}/{probeProgress.total} 块（当前块 LLM 调用返回后即更新）</Text>
                </Space>
              ) : (
                <Text type="secondary">
                  {selected.length > 0
                    ? `选中 ${selected.length} 块 · 预计上传 ${(selected.reduce((s, i) => s + (preview.chunks[i]?.prompt_chars || 0), 0)).toLocaleString()} 字符（勾选即估，未计 LLM 调用费）`
                    : '实测走正式 translate_faith 链，验证大块下行对齐能力；点行首 ▶ 展开完整内容'}
                </Text>
              )}
            </Space>
          </>
        )}

        {probe && (
          <Card type="inner" title={`实测结果：${probe.summary.ok}/${probe.summary.probed || probe.results.length} 成功${probing ? '（进行中…）' : ''}（点行首 ▶ 展开原文/译文上下对照；可单块 AI 校验）`}
            extra={
              <Tooltip title="勾选：同内容校验结果免费回放；不勾：每次真实调用（换模型重验时用）">
                <Checkbox checked={verifyUseCache} onChange={(e) => setVerifyUseCache(e.target.checked)}>
                  校验用缓存（免费回放）
                </Checkbox>
              </Tooltip>
            }>
            <Table size="small" rowKey="index" pagination={false} dataSource={probe.results}
              expandable={{
                expandedRowRender: (r) => (
                  <div>
                    <Text strong>原文（{r.line_count_src} 行）</Text>
                    <pre style={{ margin: '4px 0 12px', whiteSpace: 'pre-wrap', fontFamily: 'inherit',
                      background: '#fafafa', border: '1px solid #eee', padding: 12,
                      borderRadius: 6, maxHeight: 400, overflow: 'auto' }}>
                      {r.source || '—'}
                    </pre>
                    <Text strong>译文（{r.line_count_trans} 行）</Text>
                    <pre style={{ margin: '4px 0', whiteSpace: 'pre-wrap', fontFamily: 'inherit',
                      background: '#fafafa', border: '1px solid #eee', padding: 12,
                      borderRadius: 6, maxHeight: 400, overflow: 'auto' }}>
                      {r.ok ? (r.translation || '—') : `❌ ${r.error || '失败'}`}
                    </pre>
                    {verifies[r.index] && (
                      <div style={{ marginTop: 8 }}>
                        <Text strong>AI 校验结果</Text>{' '}
                        {verifies[r.index]!.ok ? (
                          <Tag color={verifies[r.index]!.score! >= 8 ? 'green' : verifies[r.index]!.score! >= 6 ? 'gold' : 'red'}>
                            {verifies[r.index]!.score} / 10
                          </Tag>
                        ) : <Tag color="red">校验失败</Tag>}
                        {verifies[r.index]!.ok && (verifies[r.index]!.issues?.length
                          ? <ul style={{ margin: '8px 0', paddingLeft: 20 }}>
                              {verifies[r.index]!.issues!.map((it, j) => (
                                <li key={j}>
                                  <Text type="secondary">第{it.line ?? '?'}行·{it.type}：</Text>{it.detail}
                                </li>
                              ))}
                            </ul>
                          : <Text type="secondary">无问题</Text>)}
                        {verifies[r.index]!.suggestion && (
                          <div><Text type="secondary">建议：</Text>{verifies[r.index]!.suggestion}</div>
                        )}
                      </div>
                    )}
                  </div>
                ),
              }}
              columns={[
                { title: '#', dataIndex: 'index', width: 60 },
                { title: '结果', dataIndex: 'ok', width: 90, render: (ok) =>
                  ok ? <Tag color="green">✓ 成功</Tag> : <Tag color="red">✗ 失败</Tag> },
                { title: '校验', width: 150, render: (_, r) => {
                  if (!r.ok) return <Text type="secondary">—</Text>
                  const v = verifies[r.index]
                  if (verifyingIdx === r.index) return <Spin size="small" />
                  if (v?.ok) return (
                    <Space size={4}>
                      <Tag color={v.score! >= 8 ? 'green' : v.score! >= 6 ? 'gold' : 'red'}>{v.score}/10</Tag>
                      <Button size="small" type="text" icon={<RedoOutlined />}
                        title="重新校验（跳过缓存，真实调用）"
                        onClick={() => doVerify(r.index, r.source || '', r.translation || '', true)} />
                    </Space>
                  )
                  return (
                    <Button size="small" icon={<SafetyOutlined />}
                      onClick={() => doVerify(r.index, r.source || '', r.translation || '')}>
                      校验
                    </Button>
                  )
                } },
                { title: '行数(原文/译文)', width: 150, render: (_, r) =>
                  `${r.line_count_src} / ${r.line_count_trans}` },
                { title: '详情', ellipsis: true, render: (_, r) =>
                  r.ok ? (r.translation || '').slice(0, 100) : (r.error || '') },
              ]} />
          </Card>
        )}
      </Space>
    </Card>
  )
}

// ================= 调用日志 =================

function CallsTab() {
  const [calls, setCalls] = useState<CallLogEntry[]>([])
  const [total, setTotal] = useState(0)
  const [task, setTask] = useState('')
  const [onlyFail, setOnlyFail] = useState(false)
  const [loading, setLoading] = useState(false)
  const [msgApi, ctx] = message.useMessage()

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const d = await getCallLog({ task: task || undefined, limit: 200, only_fail: onlyFail })
      setCalls(d.calls); setTotal(d.total)
    } catch (e) { msgApi.error(String(e)) } finally { setLoading(false) }
  }, [task, onlyFail, msgApi])
  useEffect(() => { reload() }, [reload])

  return (
    <Card title={`LLM 调用日志（共 ${total} 条记录，显示最新在前；文件 output/gpt_log/calls.jsonl，追加式，成功与失败都记录）`}
      extra={<Space>
        <Input style={{ width: 180 }} allowClear value={task}
          onChange={(e) => setTask(e.target.value)} placeholder="按任务过滤，如 translate_faith" />
        <Checkbox checked={onlyFail} onChange={(e) => setOnlyFail(e.target.checked)}>只看失败</Checkbox>
        <Button icon={<ReloadOutlined />} loading={loading} onClick={reload} />
      </Space>}>
      {ctx}
      <Table<CallLogEntry> rowKey={(_, i) => String(i)} dataSource={calls} size="small"
        pagination={{ pageSize: 20 }}
        columns={[
          { title: '时间', dataIndex: 'ts', width: 150 },
          { title: '任务', dataIndex: 'task', width: 130 },
          { title: '任务链', dataIndex: 'chain', width: 170, ellipsis: true, render: (v, r) =>
            v ? <Tooltip title={v}><Text type="secondary" style={{ fontSize: 12 }}>{v}</Text></Tooltip>
              : (r.cached ? '' : <Text type="secondary">—</Text>) },
          { title: '结果', dataIndex: 'ok', width: 90, render: (ok, r) =>
            r.cached ? <Tag color="blue">缓存</Tag>
              : ok ? <Tag color="green">✓</Tag> : <Tag color="red">✗</Tag> },
          { title: '接入点/模型', width: 200, render: (_, r) =>
            <span>{r.profile || '?'} <Text type="secondary">{r.model}</Text></span> },
          { title: 'key', dataIndex: 'key', width: 220, render: (v) =>
            <Text code copyable style={{ fontSize: 11 }}>{v || '—'}</Text> },
          { title: '耗时', dataIndex: 'secs', width: 80, render: (v) => v != null ? `${v}s` : '—' },
          { title: '上下字符', width: 110, render: (_, r) =>
            r.ok ? `${r.in_chars ?? '—'} / ${r.out_chars ?? '—'}` : '—' },
          { title: '错误', ellipsis: true, render: (_, r) =>
            r.ok ? '' : (
              <Text type="danger" ellipsis style={{ maxWidth: 260, cursor: 'pointer' }}
                onClick={() => Modal.info({
                  title: `调用失败详情 — ${r.ts} ${r.task}`,
                  width: 720,
                  content: (
                    <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                      maxHeight: 400, overflow: 'auto', fontSize: 12 }}>
                      {r.error || '（无详情）'}
                    </pre>
                  ),
                })}>
                {r.error}
              </Text>
            ) },
        ]} />
    </Card>
  )
}

// ================= 页面 =================

export default function TermsPromptsPage() {
  const [sub, setSub] = useState<SubKey>(subKeyFromHash)

  // keep the sub-tab in the URL hash (#/terms/prompts) — refresh persists
  useEffect(() => {
    const onHashChange = () => setSub(subKeyFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const goSub = (key: string) => {
    // preserve the top-level page key, replace the sub key
    const top = window.location.hash.replace(/^#\/?/, '').split('/')[0] || 'terms'
    window.location.hash = `/${top}/${key}`
    setSub(key as SubKey)
  }

  return (
    <Tabs activeKey={sub} onChange={goSub}
      // forceRender: keep every sub-tab mounted after first visit —
      // switching to 调用日志 and back keeps the playground's preview/
      // probe/selection state. (Upgrade path if refresh-persistence is
      // ever needed: lift state up + sessionStorage — see work log.)
      items={[
        { key: 'terms', label: '术语库', children: <TermsTab />, forceRender: true },
        { key: 'prompts', label: '提示词', children: <PromptsTab />, forceRender: true },
        { key: 'playground', label: '切分实验场', children: <PlaygroundTab />, forceRender: true },
        { key: 'calls', label: '调用日志', children: <CallsTab />, forceRender: true },
      ]} />
  )
}
