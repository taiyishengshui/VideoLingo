import { useEffect, useState } from 'react'
import {
  Button, Card, Input, InputNumber, Select, Space, Switch, message,
} from 'antd'
import { SaveOutlined } from '@ant-design/icons'

interface SettingItem {
  key: string
  label: string
  type: 'select' | 'number' | 'bool' | 'text'
  options: string[] | null
  value: unknown
}

interface SettingGroup {
  id: string
  title: string
  items: SettingItem[]
}

export async function getSettings(): Promise<{ groups: SettingGroup[] }> {
  const r = await fetch('/api/settings')
  if (!r.ok) throw new Error(`GET /api/settings ${r.status}`)
  return r.json()
}

export async function putSettings(changes: Record<string, unknown>): Promise<{ applied: string[]; rejected: string[] }> {
  const r = await fetch('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ changes }),
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `put failed: ${r.status}`)
  return r.json()
}

export default function SettingsPage() {
  const [groups, setGroups] = useState<SettingGroup[]>([])
  const [changes, setChanges] = useState<Record<string, unknown>>({})
  const [msgApi, ctxHolder] = message.useMessage()
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    getSettings()
      .then((s) => { setGroups(s.groups); setChanges({}) })
      .catch((e) => msgApi.error(String(e)))
      .finally(() => setLoading(false))
  }

  useEffect(load, []) // eslint-disable-line react-hooks/exhaustive-deps

  const set = (key: string, value: unknown) => {
    setChanges((prev) => ({ ...prev, [key]: value }))
    // mirror the edit into groups so controlled inputs re-render immediately
    setGroups((prev) => prev.map((g) => ({
      ...g,
      items: g.items.map((it) => (it.key === key ? { ...it, value } : it)),
    })))
  }

  const save = async () => {
    if (!Object.keys(changes).length) { msgApi.info('没有修改'); return }
    try {
      const res = await putSettings(changes)
      if (res.rejected.length) msgApi.warning(`已保存 ${res.applied.length} 项；被拒: ${res.rejected.join('; ')}`)
      else msgApi.success(`已保存 ${res.applied.length} 项`)
      load()
    } catch (e) {
      msgApi.error(String(e))
    }
  }

  return (
    <div>
      {ctxHolder}
      <Space style={{ marginBottom: 12 }}>
        <Button type="primary" icon={<SaveOutlined />} onClick={save}
          disabled={!Object.keys(changes).length}>
          保存{Object.keys(changes).length ? ` (${Object.keys(changes).length})` : ''}
        </Button>
        <Button onClick={load} disabled={loading}>重载</Button>
      </Space>
      {groups.map((g) => (
        <Card key={g.id} title={g.title} size="small" style={{ marginBottom: 12 }} loading={loading}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '12px 24px' }}>
            {g.items.map((it) => {
              const shown = it.key in changes ? changes[it.key] : it.value
              return (
              <div key={it.key} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                <span style={{ fontSize: 13 }}>{it.label}</span>
                {it.type === 'select' && (
                  <Select
                    style={{ width: 180 }} size="small"
                    value={shown as string}
                    options={(it.options ?? []).map((o) => ({ value: o, label: o }))}
                    onChange={(v) => set(it.key, v)}
                  />
                )}
                {it.type === 'number' && (
                  <InputNumber
                    style={{ width: 140 }} size="small"
                    value={shown as number}
                    onChange={(v) => set(it.key, v)}
                  />
                )}
                {it.type === 'bool' && (
                  <Switch
                    size="small"
                    checked={Boolean(shown)}
                    onChange={(v) => set(it.key, v)}
                  />
                )}
                {it.type === 'text' && (
                  <Input
                    style={{ width: 240 }} size="small"
                    placeholder={it.key.endsWith('proxy_url') ? 'http://127.0.0.1:7890 或 socks5://...' : ''}
                    value={(shown ?? '') as string}
                    onChange={(e) => set(it.key, e.target.value)}
                  />
                )}
              </div>
              )
            })}
          </div>
        </Card>
      ))}
      <Card size="small" title="说明" style={{ marginBottom: 12 }}>
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: '#888' }}>
          <li>API Key、模型档案请在「模型」页管理（本页不含敏感信息）</li>
          <li>切句相关：切句长度阈值越大，翻译前句子越完整（当前方案 B 推荐较大值）</li>
          <li>修改立即生效于下一次任务，不影响进行中的任务</li>
        </ul>
      </Card>
    </div>
  )
}
