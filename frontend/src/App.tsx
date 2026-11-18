import { useEffect, useState } from 'react'
import { Layout, Menu, Typography, Button, Tooltip, Modal } from 'antd'
import {
  PlaySquareOutlined, RobotOutlined, FolderOpenOutlined,
  SettingOutlined, AppstoreOutlined, BookOutlined, ReloadOutlined,
  PoweroffOutlined, SoundOutlined, EditOutlined,
} from '@ant-design/icons'
import { getVersion, type VersionInfo } from './api'

// Vite injects the git short hash at build time (FR-37 / v1.39).
declare const __BUILD_ID__: string
const FRONTEND_HASH = typeof __BUILD_ID__ === 'undefined' ? 'unknown' : __BUILD_ID__
import PipelinePage from './pages/PipelinePage'
import BatchPage from './pages/BatchPage'
import ModelsPage from './pages/ModelsPage'
import ArtifactsPage from './pages/ArtifactsPage'
import VoiceMapPage from './pages/VoiceMapPage'
import SubtitlesPage from './pages/SubtitlesPage'
import SettingsPage from './pages/SettingsPage'
import TermsPromptsPage from './pages/TermsPromptsPage'

const { Header, Content } = Layout

type PageKey = 'pipeline' | 'batch' | 'models' | 'terms' | 'artifacts' | 'voices' | 'subtitles' | 'settings'
const PAGE_KEYS: PageKey[] = ['pipeline', 'batch', 'models', 'terms', 'artifacts', 'voices', 'subtitles', 'settings']

// page state lives in the URL hash (#/models, sub-routes like #/terms/prompts
// keep the "terms" page active) — refresh / shareable
function pageFromHash(): PageKey {
  const h = window.location.hash.replace(/^#\/?/, '').split('/')[0]
  return (PAGE_KEYS as string[]).includes(h) ? (h as PageKey) : 'pipeline'
}

async function postRestart(): Promise<void> {
  const r = await fetch('/api/restart', { method: 'POST' })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `restart failed: ${r.status}`)
}

/** Wait until the backend answers again (poll every 0.5s, max 60s). */
async function waitServerBack(timeoutMs = 60000): Promise<void> {
  const t0 = Date.now()
  for (;;) {
    if (Date.now() - t0 > timeoutMs) throw new Error('等待服务恢复超时（60s）')
    try {
      const r = await fetch('/api/pipeline', { cache: 'no-store' })
      if (r.ok) return
    } catch { /* still down */ }
    await new Promise((res) => setTimeout(res, 500))
  }
}

export default function App() {
  const [page, setPage] = useState<PageKey>(pageFromHash)
  const [restarting, setRestarting] = useState(false)
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null)

  useEffect(() => {
    getVersion().then(setVersionInfo).catch(() => setVersionInfo(null))
  }, [])

  // back/forward buttons keep working
  useEffect(() => {
    const onHashChange = () => setPage(pageFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const go = (key: PageKey) => {
    window.location.hash = `/${key}`  // fires hashchange -> setPage
    setPage(key)                       // also set directly for same-hash clicks
  }

  const doRestart = () => {
    Modal.confirm({
      title: '重启后端服务？',
      content: '流水线/批量运行中会被拒绝。重启约 3-5 秒，页面会自动恢复。',
      okText: '重启',
      cancelText: '取消',
      onOk: async () => {
        setRestarting(true)
        try {
          await postRestart()
          await waitServerBack()
          window.location.reload()
        } catch (e) {
          setRestarting(false)
          Modal.error({ title: '重启失败', content: String(e) })
        }
      },
    })
  }

  const doStop = () => {
    Modal.confirm({
      title: '停止后端服务？',
      content: '停止后页面将不可用，需要到终端重新启动（python -m server 或 OneKeyStart）。流水线/批量运行中会被拒绝。',
      okText: '停止服务',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        try {
          const r = await fetch('/api/stop', { method: 'POST' })
          if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `stop failed: ${r.status}`)
        } catch (e) {
          // 409 = pipeline/batch running; anything else also surfaced
          Modal.error({ title: '停止失败', content: String(e) })
        }
      },
    })
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', gap: 24 }}>
        <Typography.Text strong style={{ color: '#fff', fontSize: 16, whiteSpace: 'nowrap' }}>
          VideoLingo
          {versionInfo && (
            <span style={{ marginLeft: 8, fontSize: 12, fontWeight: 400, opacity: 0.65, whiteSpace: 'nowrap' }}>
              v{versionInfo.version}
              {versionInfo.build && (
                <> · 后端 {versionInfo.build.hash}{versionInfo.build.dirty ? '*' : ''} · 前端 {FRONTEND_HASH}</>
              )}
            </span>
          )}
          {versionInfo?.build && FRONTEND_HASH !== 'unknown' &&
            versionInfo.build.hash !== 'unknown' &&
            versionInfo.build.hash !== FRONTEND_HASH && (
            <Typography.Text style={{ fontSize: 12, color: '#faad14', whiteSpace: 'nowrap' }}>
              前端 dist 过期，请 npm run build + Ctrl+F5
            </Typography.Text>
          )}
        </Typography.Text>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={[page]}
          onClick={(e) => go(e.key as PageKey)}
          style={{ flex: 1, minWidth: 0 }}
          items={[
            { key: 'pipeline', icon: <PlaySquareOutlined />, label: '流水线' },
            { key: 'batch', icon: <AppstoreOutlined />, label: '批量处理' },
            { key: 'models', icon: <RobotOutlined />, label: '模型' },
            { key: 'terms', icon: <BookOutlined />, label: '术语 & 提示词' },
            { key: 'artifacts', icon: <FolderOpenOutlined />, label: '产物' },
            { key: 'voices', icon: <SoundOutlined />, label: '配音分配' },
            { key: 'subtitles', icon: <EditOutlined />, label: '字幕编辑' },
            { key: 'settings', icon: <SettingOutlined />, label: '设置' },
          ]}
        />
        <Tooltip title="重启后端服务（应用代码改动）">
          <Button type="text" style={{ color: '#fff' }} loading={restarting}
            icon={<ReloadOutlined />} onClick={doRestart} />
        </Tooltip>
        <Tooltip title="停止后端服务（需终端重新启动）">
          <Button type="text" style={{ color: '#fff' }}
            icon={<PoweroffOutlined />} onClick={doStop} />
        </Tooltip>
      </Header>
      <Content style={{ padding: '16px 24px', maxWidth: 1200, width: '100%', margin: '0 auto' }}>
        {page === 'pipeline' && <PipelinePage />}
        {page === 'batch' && <BatchPage />}
        {page === 'models' && <ModelsPage />}
        {page === 'terms' && <TermsPromptsPage />}
        {page === 'artifacts' && <ArtifactsPage />}
        {page === 'voices' && <VoiceMapPage />}
        {page === 'subtitles' && <SubtitlesPage />}
        {page === 'settings' && <SettingsPage />}
      </Content>
    </Layout>
  )
}
