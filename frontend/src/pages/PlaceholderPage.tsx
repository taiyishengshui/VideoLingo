import { Empty } from 'antd'

export default function PlaceholderPage({ title, note }: { title: string; note: string }) {
  return (
    <div style={{ paddingTop: 80 }}>
      <Empty description={
        <span>
          <strong>{title}</strong> · {note}
        </span>
      } />
    </div>
  )
}
