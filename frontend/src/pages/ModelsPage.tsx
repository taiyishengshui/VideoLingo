import { useEffect, useState } from 'react'
import {
  AutoComplete, Badge, Button, Card, Input, InputNumber, List, Modal, Popconfirm, Select,
  Space, Switch, Tag, Typography, message,
} from 'antd'
import {
  ApiOutlined, AppstoreOutlined, ArrowRightOutlined, CheckOutlined, DeleteOutlined,
  EditOutlined, EyeOutlined, EyeInvisibleOutlined,
  PlusOutlined, SaveOutlined, OrderedListOutlined, KeyOutlined,
} from '@ant-design/icons'

// ---------------- types & API ----------------

interface PoolStats { total: number; dead: number }

interface Endpoint {
  base_url: string
  key_file?: string
  _has_key?: boolean
  _pool?: PoolStats
}

interface ModelEntry {
  endpoint: string
  model: string
  api_type?: 'chat_completions' | 'responses'
  llm_support_json?: boolean
  thinking_type?: string
  _has_key?: boolean
  _key_count?: number
}

interface ChainEntry {
  label: string
  models: string[]
}

interface ModelsData {
  endpoints: Record<string, Endpoint>
  models: Record<string, ModelEntry>
  chains: Record<string, ChainEntry>   // id -> {label, models}
  tasks: Record<string, string>        // task -> chain id
  retries_per_model: number
}

async function getModels(): Promise<ModelsData> {
  const r = await fetch('/api/models')
  if (!r.ok) throw new Error(`GET /api/models ${r.status}`)
  return r.json()
}

async function upsertEndpoint(p: { name: string; base_url: string; key?: string; key_file?: string }): Promise<void> {
  const r = await fetch('/api/models/endpoint', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(p),
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `upsert failed: ${r.status}`)
}

async function deleteEndpoint(name: string): Promise<void> {
  const r = await fetch('/api/models/endpoint', {
    method: 'DELETE', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `delete failed: ${r.status}`)
}

async function resetKeys(name: string): Promise<{ cleared: number }> {
  const r = await fetch('/api/models/endpoint/reset-keys', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `reset failed: ${r.status}`)
  return r.json()
}

async function revealKeys(name: string): Promise<string[]> {
  const r = await fetch('/api/models/endpoint/reveal-key', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `reveal failed: ${r.status}`)
  const j = await r.json()
  return j.keys ?? []
}

async function upsertModel(p: { name: string; endpoint: string; model: string; api_type: 'chat_completions' | 'responses'; llm_support_json: boolean; thinking_type?: string }): Promise<void> {
  const r = await fetch('/api/models/model', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(p),
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `upsert failed: ${r.status}`)
}

async function deleteModel(name: string): Promise<void> {
  const r = await fetch('/api/models/model', {
    method: 'DELETE', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `delete failed: ${r.status}`)
}

async function putChains(chains: Record<string, { label: string; models: string[] }>, tasks: Record<string, string>, retries: number): Promise<void> {
  const r = await fetch('/api/models/chains', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chains, tasks, retries_per_model: retries }),
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `put chains failed: ${r.status}`)
}

async function testModel(name: string): Promise<{
  ok: boolean; reply?: string; error?: string; via?: string
  rotated?: boolean; dead_marked?: number[]; key_total?: number; key_index?: number
}> {
  const r = await fetch('/api/models/test', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ profile: name }),
  })
  if (!r.ok) throw new Error(`test failed: ${r.status}`)
  return r.json()
}

async function fetchModels(base_url: string, opts: { key?: string; endpoint?: string; useProxy?: boolean }): Promise<{ ok: boolean; models?: string[]; error?: string; hint?: string }> {
  const r = await fetch('/api/models/fetch-models', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ base_url, key: opts.key || undefined, endpoint: opts.endpoint || undefined, use_proxy: opts.useProxy }),
  })
  if (!r.ok) {
    const d = await r.json().catch(() => ({}))
    throw new Error(d.detail || `fetch-models failed: ${r.status}`)
  }
  return r.json()
}

// ---------------- constants ----------------

const KNOWN_TASKS: { value: string; label: string }[] = [
  { value: 'asr_hints', label: 'asr_hints — 转录领域提示词' },
  { value: 'asr_sentence_fix', label: 'asr_sentence_fix — 幻觉词修正' },
  { value: 'split_by_meaning', label: 'split_by_meaning — 语义切句' },
  { value: 'summary', label: 'summary — 总结 + 术语表' },
  { value: 'translate_faith', label: 'translate_faith — 直译' },
  { value: 'translate_express', label: 'translate_express — 意译' },
  { value: 'translate_verify', label: 'translate_verify — 译文校验 (L4)' },
  { value: 'align_subs', label: 'align_subs — 字幕对齐' },
  { value: 'sub_trim', label: 'sub_trim — 配音截断校对' },
  { value: 'tts_correct_text', label: 'tts_correct_text — TTS 文本纠错' },
  { value: 'bili_meta', label: 'bili_meta — B站投稿文案' },
  { value: 'ad_detect', label: 'ad_detect — 广告位识别' },
]

interface EndpointEdit {
  open: boolean
  name: string
  isNew: boolean
  base_url: string
  key: string
  key_file: string
}

const EMPTY_EP: EndpointEdit = { open: false, name: '', isNew: true, base_url: '', key: '', key_file: '' }

interface ModelEditState {
  open: boolean
  name: string
  isNew: boolean
  endpoint: string
  model: string
  api_type: 'chat_completions' | 'responses'
  llm_support_json: boolean
  thinking_type: string
}

const EMPTY_MODEL: ModelEditState = { open: false, name: '', isNew: true, endpoint: '', model: '', api_type: 'chat_completions', llm_support_json: true, thinking_type: '' }

// ---------------- page ----------------

export default function ModelsPage() {
  const [data, setData] = useState<ModelsData | null>(null)
  const [epEdit, setEpEdit] = useState<EndpointEdit>(EMPTY_EP)
  const [modelEdit, setModelEdit] = useState<ModelEditState>(EMPTY_MODEL)
  const [chainsDraft, setChainsDraft] = useState<Record<string, ChainEntry>>({})
  const [tasksDraft, setTasksDraft] = useState<Record<string, string>>({})
  const [retriesDraft, setRetriesDraft] = useState<number>(2)
  const [activeChain, setActiveChain] = useState<string>('')
  const [dragOverIdx, setDragOverIdx] = useState<number | null>(null)
  const [chainEdit, setChainEdit] = useState<{ open: boolean; isNew: boolean; id: string; label: string }>({ open: false, isNew: true, id: '', label: '' })
  const [testing, setTesting] = useState('')
  const [modelOptions, setModelOptions] = useState<string[]>([])
  const [fetching, setFetching] = useState(false)
  const [useProxy, setUseProxy] = useState<boolean | undefined>(undefined)
  const [msgApi, ctxHolder] = message.useMessage()

  const load = () => {
    getModels().then((d) => {
      setData(d)
      setChainsDraft(JSON.parse(JSON.stringify(d.chains ?? {})))
      setTasksDraft(JSON.parse(JSON.stringify(d.tasks ?? {})))
      setRetriesDraft(d.retries_per_model ?? 2)
      setActiveChain((prev) => prev && (d.chains ?? {})[prev] ? prev : (Object.keys(d.chains ?? {})[0] ?? ''))
    }).catch((e) => msgApi.error(String(e)))
  }

  useEffect(load, []) // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (fn: () => Promise<void>, ok?: string) => {
    try {
      await fn()
      if (ok) msgApi.success(ok)
      load()
    } catch (e) {
      msgApi.error(String(e))
    }
  }

  const endpointNames = Object.keys(data?.endpoints ?? {})
  const modelNames = Object.keys(data?.models ?? {})
  const chainIds = Object.keys(chainsDraft)
  const taskEntries = Object.entries(tasksDraft)   // task -> chain id

  const chainLabel = (cid: string) => chainsDraft[cid]?.label || cid
  const chainSeq = (cid: string) => chainsDraft[cid]?.models ?? []

  const setChainSeq = (cid: string, models: string[]) =>
    setChainsDraft((prev) => ({ ...prev, [cid]: { ...(prev[cid] ?? { label: cid }), models } }))

  /** 删链后任务绑定的兜底：优先保留 default 指向的链，否则取剩余第一条。 */
  const removalFallback = (cid: string) => {
    const rest = chainIds.filter((c) => c !== cid)
    return (tasksDraft.default && tasksDraft.default !== cid && rest.includes(tasksDraft.default))
      ? tasksDraft.default
      : (rest[0] ?? '')
  }

  /** 删除链（含被任务绑定的链）：同时把引用它的任务改绑，避免保存时
      又撞上「任务 X 绑定的链不存在」或「链不能为空」的死循环。
      最后一条链不允许删 —— 运行时要求至少一条链且 default 必须有绑定。 */
  const removeChain = (cid: string) => {
    if (chainIds.length <= 1) {
      msgApi.warning('至少要保留一条链：可先新建一条带模型的链，或点「撤销修改」恢复')
      return
    }
    const fallback = removalFallback(cid)
    const affected = Object.keys(tasksDraft).filter((t) => tasksDraft[t] === cid)
    const from = chainLabel(cid)
    const to = chainLabel(fallback)
    setChainsDraft((prev) => { const n = { ...prev }; delete n[cid]; return n })
    setTasksDraft((prev) => {
      const n = { ...prev }
      for (const t of Object.keys(n)) if (n[t] === cid) n[t] = fallback
      return n
    })
    if (activeChain === cid) setActiveChain(fallback)
    msgApi.info(affected.length
      ? `已移除链「${from}」，${affected.length} 个任务改绑到「${to}」，点「保存链与绑定」生效`
      : `已移除链「${from}」，点「保存链与绑定」生效`)
  }
  const moveInChain = (cid: string, from: number, to: number) => {
    const seq = [...chainSeq(cid)]
    if (to < 0 || to >= seq.length) return
    const [item] = seq.splice(from, 1)
    seq.splice(to, 0, item)
    setChainSeq(cid, seq)
  }
  const toggleInActiveChain = (name: string) => {
    if (!activeChain) return
    const seq = chainSeq(activeChain)
    if (seq.includes(name)) setChainSeq(activeChain, seq.filter((n) => n !== name))
    else setChainSeq(activeChain, [...seq, name])
  }
  const dropOnChainIdx = (cid: string, idx: number, name: string) => {
    setDragOverIdx(null)
    const seq = [...chainSeq(cid)]
    if (seq.includes(name)) return
    if (idx < 0 || idx > seq.length) seq.push(name)
    else seq.splice(idx, 0, name)
    setChainSeq(cid, seq)
  }
  const reorderInChain = (cid: string, from: number, to: number) => {
    if (from === to || to < 0) return
    const seq = [...chainSeq(cid)]
    if (to >= seq.length) return
    const [item] = seq.splice(from, 1)
    seq.splice(to, 0, item)
    setChainSeq(cid, seq)
  }
  const chainUsedBy = (cid: string) =>
    taskEntries.filter(([, c]) => c === cid).map(([t]) => t === 'default' ? '默认' : t)
  const dirty = data && (
    JSON.stringify(chainsDraft) !== JSON.stringify(data.chains ?? {}) ||
    JSON.stringify(tasksDraft) !== JSON.stringify(data.tasks ?? {}) ||
    retriesDraft !== data.retries_per_model
  )

  const saveChains = () => {
    const unbound = chainIds.filter((c) => !Object.values(tasksDraft).includes(c))
    const doSave = () =>
      act(() => putChains(chainsDraft, tasksDraft, retriesDraft), '链与绑定已保存')
    if (!tasksDraft.default) { msgApi.error('必须为 default 绑定一条链'); return }
    // 空链 / 悬空绑定在前端就拦下并给出可操作指引，别让用户反复撞后端 400
    const emptyChains = chainIds.filter((c) => (chainsDraft[c]?.models ?? []).length === 0)
    if (emptyChains.length) {
      msgApi.error(`链「${emptyChains.map(chainLabel).join('、')}」没有模型：点选该链后从上方模型池添加，或直接删除该链（绑定任务会自动改绑）`)
      return
    }
    const dangling = Object.entries(tasksDraft).filter(
      ([, c]) => typeof c === 'string' && !chainIds.includes(c))
    if (dangling.length) {
      msgApi.error(`任务 ${dangling.map(([t]) => t).join('、')} 绑定的链已不存在，请重新选择`)
      return
    }
    if (unbound.length) {
      Modal.confirm({
        title: '有链未绑定任何任务',
        content: `「${unbound.map(chainLabel).join('、')}」没有被任何任务使用，保存后仍会保留（可随时绑定）。继续保存？`,
        okText: '保存', cancelText: '返回',
        onOk: doSave,
      })
      return
    }
    doSave()
  }

  const saveChainEdit = () => {
    const id = chainEdit.id.trim()
    if (!/^[a-z0-9][a-z0-9-]{0,31}$/.test(id)) {
      msgApi.error('链 id 只能是小写字母/数字/-（最长 32）'); return
    }
    if (chainEdit.isNew && chainsDraft[id]) {
      msgApi.error('同 id 的链已存在'); return
    }
    setChainsDraft((prev) => ({
      ...prev,
      [id]: { label: chainEdit.label.trim() || id, models: prev[id]?.models ?? [] },
    }))
    // renaming an existing chain (id changed): migrate bindings + selection
    if (!chainEdit.isNew && chainEdit.id !== id && tasksDraft) {
      // not expected (id input disabled when editing) — no-op
    }
    setActiveChain(id)
    if (chainEdit.isNew) {
      msgApi.info(`链「${id}」已创建：请点上方模型池里的模型加入该链（空链无法保存），不需要就直接删除`)
    }
    setChainEdit({ open: false, isNew: true, id: '', label: '' })
  }

  const saveEndpoint = () => act(async () => {
    await upsertEndpoint({
      name: epEdit.name, base_url: epEdit.base_url,
      key: epEdit.key || undefined,
      key_file: epEdit.key_file || undefined,
    })
    setEpEdit(EMPTY_EP)
  }, `凭证 ${epEdit.name} 已保存`)

  const saveModel = () => act(async () => {
    await upsertModel({
      name: modelEdit.name, endpoint: modelEdit.endpoint, model: modelEdit.model,
      api_type: modelEdit.api_type,
      llm_support_json: modelEdit.llm_support_json,
      thinking_type: modelEdit.thinking_type || undefined,
    })
    setModelEdit(EMPTY_MODEL)
  }, `模型 ${modelEdit.name} 已保存`)

  const doTest = (name: string) => {
    setTesting(name)
    testModel(name)
      .then((res) => {
        if (res.ok) {
          const rotatedNote = res.rotated
            ? `（跳过并标记了失效 key #${res.dead_marked?.join(', #')}，当前 key #${res.key_index}/${res.key_total}）`
            : ''
          msgApi.success(`${name} 连通 ✓ 回复: ${res.reply}（${res.via === 'proxy' ? '走代理' : '直连'}）${rotatedNote}`)
        } else {
          const deadNote = res.dead_marked?.length
            ? `已标记失效 key #${res.dead_marked.join(', #')}（共 ${res.key_total} 个）。`
            : ''
          msgApi.error(`${name} 失败: ${res.error} ${deadNote}`)
        }
      })
      .catch((e) => msgApi.error(String(e)))
      .finally(() => setTesting(''))
  }

  const doFetchModels = async () => {
    if (!modelEdit.endpoint) { msgApi.warning('先选平台凭证'); return }
    const ep = data?.endpoints[modelEdit.endpoint]
    if (!ep?.base_url) { msgApi.warning('该凭证没有 base_url'); return }
    setFetching(true)
    try {
      const res = await fetchModels(ep.base_url, {
        endpoint: modelEdit.endpoint, useProxy,
      })
      if (res.ok && res.models?.length) {
        setModelOptions(res.models)
        msgApi.success(`获取到 ${res.models.length} 个模型，请在下拉框选择`)
      } else {
        msgApi.error({ content: `获取失败: ${res.error || '返回为空'} — ${res.hint || '可切换代理开关重试，或手填模型名'}`, duration: 8 })
      }
    } catch (e) {
      msgApi.error(`${String(e)}（可切换代理开关重试，或手填模型名）`)
    } finally {
      setFetching(false)
    }
  }

  return (
    <div>
      {ctxHolder}

      {/* ---- card 1: endpoints ---- */}
      <Card size="small" title="平台凭证 (llm.endpoints)" style={{ marginBottom: 12 }}
        extra={
          <Button size="small" icon={<PlusOutlined />}
            onClick={() => setEpEdit({ ...EMPTY_EP, open: true })}>
            新建凭证
          </Button>
        }>
        <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
          平台 = base_url + key。同平台不同 key 建多条；key 文件模式支持多 key 自动轮换（失效自动换下一个，坏 key 永久标记）
        </Typography.Text>
        <List
          size="small"
          dataSource={endpointNames}
          locale={{ emptyText: '（无凭证）' }}
          renderItem={(name) => {
            const ep = data!.endpoints[name]
            const pool = ep._pool
            const usedBy = modelNames.filter((m) => data!.models[m].endpoint === name)
            return (
              <List.Item
                actions={[
                  <Button key="e" size="small" icon={<EditOutlined />}
                    onClick={() => setEpEdit({
                      open: true, name, isNew: false,
                      base_url: ep.base_url,
                      key: '', key_file: ep.key_file ?? '',
                    })}>
                    编辑
                  </Button>,
                  <Button key="k" size="small" icon={<EyeOutlined />}
                    onClick={() => act(async () => {
                      const keys = await revealKeys(name)
                      if (keys.length) msgApi.info({ content: keys.map((k, i) => `${i + 1}. ${k}`).join('\n'), duration: 10 })
                      else msgApi.warning('该凭证没有 key')
                    })}>
                    Keys
                  </Button>,
                  ...(pool && pool.dead > 0 ? [
                    <Popconfirm key="r" title={`重置该凭证的 ${pool.dead} 个坏 key 标记？（重新参与轮换）`}
                      onConfirm={() => act(async () => {
                        const r = await resetKeys(name)
                        msgApi.success(`已重置 ${r.cleared} 个坏 key 标记`)
                      })}>
                      <Button size="small" danger icon={<KeyOutlined />}>重置{pool.dead}坏key</Button>
                    </Popconfirm>,
                  ] : []),
                  <Popconfirm key="d" title={`删除凭证 ${name}？`}
                    onConfirm={() => act(() => deleteEndpoint(name), `已删除 ${name}`)}>
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>,
                ]}
              >
                <Space size={12} wrap>
                  <Typography.Text strong>{name}</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>{ep.base_url}</Typography.Text>
                  {ep.key_file
                    ? <Tag color={pool && pool.dead >= pool.total ? 'red' : 'purple'} style={{ fontSize: 11 }}>
                      Key池 {pool ? `${pool.total - pool.dead}/${pool.total}` : '-'} {pool && pool.dead > 0 ? `(${pool.dead}失效)` : ''}
                    </Tag>
                    : <Tag color={ep._has_key ? 'green' : 'red'} style={{ fontSize: 11 }}>
                      {ep._has_key ? '单 Key' : 'Key 未配置'}
                    </Tag>}
                  {usedBy.length > 0 && (
                    <Badge status="processing" text={<span style={{ fontSize: 12 }}>模型: {usedBy.join(', ')}</span>} />
                  )}
                </Space>
              </List.Item>
            )
          }}
        />
      </Card>

      {/* ---- card 2: models ---- */}
      <Card size="small" title="模型 (llm.models)" style={{ marginBottom: 12 }}
        extra={
          <Button size="small" icon={<PlusOutlined />} disabled={!endpointNames.length}
            onClick={() => { setModelOptions([]); setModelEdit({ ...EMPTY_MODEL, open: true }) }}>
            新建模型
          </Button>
        }>
        <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
          模型 = 凭证 + 模型名。同凭证不同模型只需建多条模型记录，key 只存一份
        </Typography.Text>
        <List
          size="small"
          dataSource={modelNames}
          locale={{ emptyText: '（无模型——先建平台凭证再建模型）' }}
          renderItem={(name) => {
            const m = data!.models[name]
            const inChains = Object.entries(chainsDraft)
              .filter(([, entry]) => entry.models.includes(name))
              .map(([c]) => chainsDraft[c].label || c)
            return (
              <List.Item
                actions={[
                  <Button key="t" size="small" icon={<ApiOutlined />}
                    loading={testing === name} onClick={() => doTest(name)}>
                    测试
                  </Button>,
                  <Button key="e" size="small" icon={<EditOutlined />}
                    onClick={() => { setModelOptions([]); setModelEdit({
                      open: true, name, isNew: false,
                      endpoint: m.endpoint, model: m.model,
                      api_type: m.api_type ?? 'chat_completions',
                      llm_support_json: m.llm_support_json !== false,
                      thinking_type: m.thinking_type ?? '',
                    }) }}>
                    编辑
                  </Button>,
                  <Popconfirm key="d" title={`删除模型 ${name}？`}
                    onConfirm={() => act(() => deleteModel(name), `已删除 ${name}`)}>
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>,
                ]}
              >
                <Space size={12} wrap>
                  <Typography.Text strong>{name}</Typography.Text>
                  <Tag>{m.model}</Tag>
                  <Tag color="cyan" style={{ fontSize: 11 }}>@{m.endpoint}</Tag>
                  {m._key_count && m._key_count > 1 && (
                    <Tag color="purple" style={{ fontSize: 11 }}>key池×{m._key_count}</Tag>
                  )}
                  {m._has_key === false && <Tag color="red" style={{ fontSize: 11 }}>无可用 key</Tag>}
                  {inChains.length > 0 && (
                    <Badge status="processing" text={<span style={{ fontSize: 12 }}>用于链: {inChains.join(', ')}</span>} />
                  )}
                </Space>
              </List.Item>
            )
          }}
        />
      </Card>

      {/* ---- card 3: named chains + task bindings ---- */}
      <Card size="small" title="任务链 (llm.chains + llm.tasks)" style={{ marginBottom: 12 }}>
        <Space direction="vertical" style={{ width: '100%' }} size={12}>

          {/* model pool: click to toggle in the selected chain, drag to a position */}
          <div>
            <Space style={{ marginBottom: 4 }}>
              <AppstoreOutlined />
              <Typography.Text strong>模型池</Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                点击「{activeChain ? chainLabel(activeChain) : '？'}」链中的模型可加入/移出；拖动可插入指定位置
              </Typography.Text>
            </Space>
            <Space wrap size={6}>
              {modelNames.length === 0 && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  （先在上方「模型」卡片创建模型）
                </Typography.Text>
              )}
              {modelNames.map((name) => {
                const m = data?.models[name]
                const inActive = activeChain ? chainSeq(activeChain).includes(name) : false
                return (
                  <div
                    key={name}
                    draggable
                    onDragStart={(e) => e.dataTransfer.setData('text/plain', name)}
                    onClick={() => toggleInActiveChain(name)}
                    title={inActive
                      ? `点击从「${activeChain}」链移出${m ? ` — ${m.model} @${m.endpoint}` : ''}`
                      : `点击加入「${activeChain}」链${m ? ` — ${m.model} @${m.endpoint}` : ''}`}
                    style={{
                      padding: '3px 10px', borderRadius: 6, cursor: 'pointer',
                      border: `1px solid ${inActive ? '#1677ff' : '#d9d9d9'}`,
                      background: inActive ? '#e6f4ff' : '#fafafa',
                      fontSize: 13, userSelect: 'none',
                    }}
                  >
                    {name}
                    <span style={{ fontSize: 11, color: '#999', marginLeft: 6 }}>
                      {m ? `${m.model}` : ''}
                    </span>
                    {inActive && <CheckOutlined style={{ fontSize: 11, color: '#1677ff', marginLeft: 4 }} />}
                  </div>
                )
              })}
            </Space>
          </div>

          {/* named chains: click to select, then pool clicks append here */}
          <div>
            <Space style={{ marginBottom: 4 }} wrap>
              <OrderedListOutlined />
              <Typography.Text strong>链</Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                点链选中（点模型池即加入）；链内模型 ↑↓ 或拖拽排序；改一条链，所有绑定任务同时生效
              </Typography.Text>
              <Button size="small" icon={<PlusOutlined />}
                onClick={() => setChainEdit({ open: true, isNew: true, id: '', label: '' })}>
                新建链
              </Button>
            </Space>
            <Space direction="vertical" style={{ width: '100%' }} size={6}>
              {chainIds.length === 0 && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  （无链 — 点上方「新建链」按钮创建）
                </Typography.Text>
              )}
              {chainIds.map((cid) => {
                const entry = chainsDraft[cid] ?? { label: cid, models: [] }
                const seq = entry.models
                const usedBy = chainUsedBy(cid)
                return (
                  <div
                    key={cid}
                    onClick={(e) => {
                      if ((e.target as HTMLElement).closest('button,a,.ant-tag-close-x,input')) return
                      setActiveChain(cid)
                    }}
                    style={{
                      border: `2px solid ${activeChain === cid ? '#1677ff' : '#e8e8e8'}`,
                      borderRadius: 6, padding: '6px 10px',
                      background: activeChain === cid ? '#f0f7ff' : undefined,
                      cursor: 'pointer',
                    }}
                  >
                    <div
                      onDragOver={(e) => { e.preventDefault(); setDragOverIdx(-1) }}
                      onDragLeave={() => setDragOverIdx(null)}
                      onDrop={(e) => {
                        e.preventDefault(); e.stopPropagation()
                        const name = e.dataTransfer.getData('text/plain')
                        if (name && !name.startsWith('#move:')) dropOnChainIdx(cid, -1, name)
                      }}
                    >
                      <Space wrap size={6} align="center">
                        <Typography.Text strong style={{ fontSize: 13 }}>{entry.label}</Typography.Text>
                        <Typography.Text type="secondary" style={{ fontSize: 11 }}>{cid}</Typography.Text>
                        {activeChain === cid && <Tag color="blue" style={{ fontSize: 11, marginInlineEnd: 0 }}>选中</Tag>}
                        {usedBy.length > 0 && (
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            用于: {usedBy.join(' · ')}
                          </Typography.Text>
                        )}
                        {seq.length === 0 && <Tag color="orange" style={{ fontSize: 11, marginInlineEnd: 0 }}>空链 · 加模型或删除</Tag>}
                        <Button size="small" type="text" icon={<EditOutlined />}
                          title="改别名（label）"
                          onClick={() => setChainEdit({ open: true, isNew: false, id: cid, label: entry.label })} />
                        <Popconfirm
                          title={usedBy.length
                            ? `删除链「${entry.label}」并把 ${usedBy.length} 个任务（${usedBy.join('、')}）改绑到「${chainLabel(removalFallback(cid))}」？`
                            : `删除链「${entry.label}」？`}
                          description={chainIds.length <= 1 ? '至少要保留一条链' : '保存后生效；绑定任务会自动改绑'}
                          disabled={chainIds.length <= 1}
                          onConfirm={() => removeChain(cid)}>
                          <Button size="small" danger icon={<DeleteOutlined />}
                            disabled={chainIds.length <= 1}
                            title={chainIds.length <= 1 ? '至少要保留一条链' : '删除链（绑定任务会自动改绑）'} />
                        </Popconfirm>
                        {seq.map((name, i) => (
                          <span key={`${cid}-${name}`} style={{ display: 'inline-flex', alignItems: 'center' }}>
                            {dragOverIdx === i && <ArrowRightOutlined style={{ color: '#1677ff', fontSize: 11 }} />}
                            <Tag
                              color="geekblue" closable
                              draggable
                              onDragStart={(e) => e.dataTransfer.setData('text/plain', `#move:${i}`)}
                              onDragOver={(e) => { e.preventDefault(); setDragOverIdx(i) }}
                              onDrop={(e) => {
                                e.preventDefault(); e.stopPropagation()
                                const d = e.dataTransfer.getData('text/plain')
                                if (d.startsWith('#move:')) reorderInChain(cid, Number(d.slice(6)), i)
                                else if (d) dropOnChainIdx(cid, i, d)
                              }}
                              onClose={() => setChainSeq(cid, seq.filter((_, j) => j !== i))}
                              style={{ marginInlineEnd: 0 }}
                            >
                              {i + 1}. {name}
                              <Space size={2} style={{ marginLeft: 4 }}>
                                <a style={{ fontSize: 10 }} onClick={() => moveInChain(cid, i, i - 1)}>↑</a>
                                <a style={{ fontSize: 10 }} onClick={() => moveInChain(cid, i, i + 1)}>↓</a>
                              </Space>
                            </Tag>
                          </span>
                        ))}
                      </Space>
                    </div>
                  </div>
                )
              })}
            </Space>
          </div>

          {/* task -> chain bindings */}
          <div>
            <Space style={{ marginBottom: 4 }}>
              <Typography.Text strong>任务 → 链绑定</Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                未绑定的任务走「默认」
              </Typography.Text>
            </Space>
            <Space direction="vertical" size={4} style={{ display: 'flex' }}>
              {/* default binding row */}
              <Space>
                <Typography.Text style={{ fontSize: 13, width: 180, display: 'inline-block' }}>默认（其余全部任务）</Typography.Text>
                <Select
                  size="small" style={{ width: 200 }}
                  value={tasksDraft.default}
                  options={chainIds.map((c) => ({ value: c, label: `${chainLabel(c)} (${c})` }))}
                  onChange={(v) => setTasksDraft((prev) => ({ ...prev, default: v }))} />
              </Space>
              {taskEntries.filter(([t]) => t !== 'default').map(([task, bound]) => {
                const desc = KNOWN_TASKS.find((t) => t.value === task)?.label
                  .split('—')[1]?.trim()
                return (
                  <Space key={task}>
                    <Typography.Text style={{ fontSize: 13, width: 180, display: 'inline-block' }}>
                      {task}{desc ? <span style={{ fontSize: 11, color: '#999' }}>（{desc}）</span> : null}
                    </Typography.Text>
                    <Select
                      size="small" style={{ width: 200 }}
                      value={bound}
                      allowClear placeholder="（走默认）"
                      options={chainIds.map((c) => ({ value: c, label: `${chainLabel(c)} (${c})` }))}
                      onChange={(v) => setTasksDraft((prev) => {
                        const n = { ...prev }
                        if (v) n[task] = v
                        else delete n[task]
                        return n
                      })} />
                  </Space>
                )
              })}
              <Select
                size="small" style={{ width: 360 }} placeholder="+ 绑定一个任务到链"
                value={undefined}
                options={KNOWN_TASKS.filter((t) => !(t.value in tasksDraft)).map((t) => ({ value: t.value, label: t.label }))}
                onChange={(v) => {
                  if (!v) return
                  setTasksDraft((prev) => ({ ...prev, [v]: tasksDraft.default ?? chainIds[0] ?? '' }))
                }} />
            </Space>
          </div>

          <Space>
            <Typography.Text style={{ fontSize: 13 }}>每模型重试次数</Typography.Text>
            <InputNumber size="small" min={0} max={10} value={retriesDraft}
              onChange={(v) => setRetriesDraft(v ?? 2)} />
            <Button size="small" type="primary" icon={<SaveOutlined />}
              disabled={!dirty}
              onClick={saveChains}>
              保存链与绑定
            </Button>
            <Button size="small" disabled={!dirty}
              onClick={() => {
                setChainsDraft(JSON.parse(JSON.stringify(data?.chains ?? {})))
                setTasksDraft(JSON.parse(JSON.stringify(data?.tasks ?? {})))
                setRetriesDraft(data?.retries_per_model ?? 2)
              }}>
              撤销修改
            </Button>
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            失败切换顺序：同凭证 key 池轮换 → 链中下一个模型；任务绑定优先于默认
          </Typography.Text>
        </Space>
      </Card>

      {/* ---- endpoint modal ---- */}
      <Modal
        open={epEdit.open}
        title={epEdit.isNew ? '新建平台凭证' : `编辑凭证 ${epEdit.name}`}
        onCancel={() => setEpEdit(EMPTY_EP)}
        onOk={saveEndpoint}
        okText="保存" cancelText="取消"
      >
        <Space direction="vertical" style={{ width: '100%' }} size={10}>
          <div>
            <Typography.Text strong>凭证名</Typography.Text>
            <Input value={epEdit.name} disabled={!epEdit.isNew}
              placeholder="如 yhshu / llmpm / yhshu-backup"
              onChange={(e) => setEpEdit({ ...epEdit, name: e.target.value })} />
          </div>
          <div>
            <Typography.Text strong>Base URL</Typography.Text>
            <Input value={epEdit.base_url} placeholder="https://api.example.com（/v1 可省略）"
              onChange={(e) => setEpEdit({ ...epEdit, base_url: e.target.value })} />
          </div>
          <div>
            <Typography.Text strong>单个 API Key {!epEdit.isNew && '（留空保留原 key）'}</Typography.Text>
            <Input.Password value={epEdit.key}
              placeholder={epEdit.isNew ? 'sk-...' : '留空 = 不修改'}
              onChange={(e) => setEpEdit({ ...epEdit, key: e.target.value })}
              iconRender={(visible) => visible ? <EyeOutlined /> : <EyeInvisibleOutlined />} />
          </div>
          <div>
            <Typography.Text strong>或 Key 文件路径（多 key 轮换池）</Typography.Text>
            <Input value={epEdit.key_file}
              placeholder="D:/keys/xxx.txt — 每行 名字----key，失效自动换下一个"
              onChange={(e) => setEpEdit({ ...epEdit, key_file: e.target.value })} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              填了 key 文件则忽略上面的单 key；两处都留空且是编辑 = 保留原配置
            </Typography.Text>
          </div>
        </Space>
      </Modal>

      {/* ---- model modal ---- */}
      <Modal
        open={modelEdit.open}
        title={modelEdit.isNew ? '新建模型' : `编辑模型 ${modelEdit.name}`}
        onCancel={() => setModelEdit(EMPTY_MODEL)}
        onOk={saveModel}
        okText="保存" cancelText="取消"
      >
        <Space direction="vertical" style={{ width: '100%' }} size={10}>
          <div>
            <Typography.Text strong>模型名（本工具内的别名）</Typography.Text>
            <Input value={modelEdit.name} disabled={!modelEdit.isNew}
              placeholder="如 glm-flash / gemini / grok"
              onChange={(e) => setModelEdit({ ...modelEdit, name: e.target.value })} />
          </div>
          <div>
            <Typography.Text strong>平台凭证</Typography.Text>
            <Select style={{ width: '100%' }} value={modelEdit.endpoint || undefined}
              placeholder="选择凭证（key/base_url 复用）"
              options={endpointNames.map((n) => ({ value: n, label: n }))}
              onChange={(v) => setModelEdit({ ...modelEdit, endpoint: v })} />
          </div>
          <div>
            <Space style={{ width: '100%', justifyContent: 'space-between' }}>
              <Typography.Text strong>模型 ID</Typography.Text>
              <Space size={4}>
                <span style={{ fontSize: 12 }}>走代理</span>
                <Switch size="small" checked={useProxy === true}
                  onChange={(checked) => setUseProxy(checked)} />
                {useProxy === undefined && <span style={{ fontSize: 11, color: '#999' }}>跟随设置页</span>}
                <Button size="small" icon={<ApiOutlined />} loading={fetching}
                  onClick={doFetchModels}>
                  获取模型列表
                </Button>
              </Space>
            </Space>
            <AutoComplete
              style={{ width: '100%' }}
              value={modelEdit.model}
              options={modelOptions
                .filter((m) => !modelEdit.model || m.toLowerCase().includes(modelEdit.model.toLowerCase()))
                .slice(0, 50)
                .map((m) => ({ value: m }))}
              onChange={(v) => setModelEdit({ ...modelEdit, model: v })}
              placeholder="点「获取模型列表」或手填，如 glm-5.3-flash"
            />
          </div>
          <div>
            <Typography.Text strong>API 协议</Typography.Text>
            <Select style={{ width: '100%' }} value={modelEdit.api_type}
              options={[
                { value: 'chat_completions', label: 'Chat Completions' },
                { value: 'responses', label: 'Responses API' },
              ]}
              onChange={(v: 'chat_completions' | 'responses') => setModelEdit({ ...modelEdit, api_type: v })} />
          </div>
          <Space>
            <Typography.Text style={{ fontSize: 13 }}>支持 JSON 输出</Typography.Text>
            <Switch size="small" checked={modelEdit.llm_support_json}
              onChange={(v) => setModelEdit({ ...modelEdit, llm_support_json: v })} />
            <Typography.Text style={{ fontSize: 13 }}>思考模式</Typography.Text>
            <Select size="small" style={{ width: 120 }} value={modelEdit.thinking_type || undefined}
              allowClear placeholder="无"
              options={[{ value: 'enabled', label: 'enabled' }, { value: 'disabled', label: 'disabled' }]}
              onChange={(v) => setModelEdit({ ...modelEdit, thinking_type: v ?? '' })} />
          </Space>
        </Space>
      </Modal>

      {/* ---- chain new/edit modal ---- */}
      <Modal
        open={chainEdit.open}
        title={chainEdit.isNew ? '新建链' : `编辑链 ${chainEdit.id}`}
        onCancel={() => setChainEdit({ open: false, isNew: true, id: '', label: '' })}
        onOk={saveChainEdit}
        okText="保存" cancelText="取消"
      >
        <Space direction="vertical" style={{ width: '100%' }} size={10}>
          <div>
            <Typography.Text strong>链 ID（英文，绑定引用用，不可改）</Typography.Text>
            <Input value={chainEdit.id} disabled={!chainEdit.isNew}
              placeholder="如 fast-cheap / quality-first"
              onChange={(e) => setChainEdit({ ...chainEdit, id: e.target.value.toLowerCase() })} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              小写字母/数字/-，最长 32；日志与任务绑定都引用它
            </Typography.Text>
          </div>
          <div>
            <Typography.Text strong>别名（显示用，可随时改）</Typography.Text>
            <Input value={chainEdit.label}
              placeholder="如 省钱快跑 / 质量优先（留空则显示 ID）"
              onChange={(e) => setChainEdit({ ...chainEdit, label: e.target.value })} />
          </div>
        </Space>
      </Modal>
    </div>
  )
}
