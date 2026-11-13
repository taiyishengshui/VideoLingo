import { useCallback, useEffect, useState } from 'react'
import { Button, Card, List, Space, Table, Tag, Typography, message } from 'antd'
import {
  ArrowLeftOutlined, DownloadOutlined, FileOutlined, FolderOpenOutlined,
  HomeOutlined, ReloadOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'

interface Entry {
  name: string
  path: string
  type: 'dir' | 'video' | 'audio' | 'file'
  size?: number
  log?: boolean
}

interface BrowseResult {
  path: string | null
  parent: string | null
  dirs: Entry[]
  files: Entry[]
}

async function browseArtifacts(path: string | null): Promise<BrowseResult> {
  const r = await fetch('/api/batch/artifacts/browse', {
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

function fmtSize(n?: number): string {
  if (n == null) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${(n / 1024 ** 3).toFixed(2)} GB`
}

export default function ArtifactsPage() {
  const [data, setData] = useState<BrowseResult | null>(null)
  const [err, setErr] = useState('')
  const [, ctxHolder] = message.useMessage()

  const load = useCallback((path: string | null) => {
    setErr('')
    browseArtifacts(path).then(setData).catch((e) => setErr(String(e)))
  }, [])

  useEffect(() => { load(null) }, [load])

  const fileColumns: ColumnsType<Entry> = [
    {
      title: '文件名', dataIndex: 'name',
      render: (n: string) => (
        <Space>
          <FileOutlined style={{ color: '#888' }} />
          <Typography.Text copyable={{ text: undefined }} ellipsis style={{ maxWidth: 380 }}>{n}</Typography.Text>
        </Space>
      ),
    },
    { title: '大小', dataIndex: 'size', width: 110, render: (s: number) => fmtSize(s) },
    {
      title: '', width: 90,
      render: (_: unknown, row: Entry) => (
        <Button size="small" icon={<DownloadOutlined />}
          onClick={() => { window.open(`/api/batch/artifacts/download?path=${encodeURIComponent(row.path)}`, '_blank') }}>
          下载
        </Button>
      ),
    },
  ]

  return (
    <div>
      {ctxHolder}
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space style={{ marginBottom: 8 }}>
          <Button size="small" icon={<HomeOutlined />} onClick={() => load(null)}>根</Button>
          <Button size="small" icon={<ArrowLeftOutlined />} disabled={!data?.parent}
            onClick={() => data?.parent && load(data.parent)}>
            上一级
          </Button>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => data?.path !== undefined && load(data?.path ?? null)}>
            刷新
          </Button>
          <Typography.Text code ellipsis style={{ fontSize: 12, maxWidth: 480 }}>
            {data?.path ?? '（选择目录）'}
          </Typography.Text>
        </Space>
        {err && <Typography.Text type="danger" style={{ fontSize: 12 }}>{err}</Typography.Text>}

        <List
          size="small"
          header={<Typography.Text strong style={{ fontSize: 13 }}>子目录</Typography.Text>}
          dataSource={data?.dirs ?? []}
          locale={{ emptyText: '（无子目录）' }}
          renderItem={(d) => (
            <List.Item style={{ cursor: 'pointer', padding: '6px 12px' }}
              onClick={() => load(d.path)}>
              <Space>
                <FolderOpenOutlined style={{ color: '#1677ff' }} />
                <Typography.Text>{d.name}</Typography.Text>
              </Space>
            </List.Item>
          )}
        />
        <div style={{ marginTop: 8 }}>
          <Table
            size="small"
            rowKey="path"
            columns={fileColumns}
            dataSource={data?.files ?? []}
            pagination={false}
            locale={{ emptyText: '（无文件）' }}
            title={() => (
              <Typography.Text strong style={{ fontSize: 13 }}>
                文件 <Tag>{(data?.files ?? []).length}</Tag>
              </Typography.Text>
            )}
          />
        </div>
      </Card>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        可浏览：output/（单任务产物）· history/（归档）· 批量工作区（*_log，需先在「批量处理」页扫描）
      </Typography.Text>
    </div>
  )
}
