import { useEffect, useRef, useState } from 'react'

export interface BusEvent {
  type: 'log' | 'state' | 'step_start' | 'step_done' | 'history_saved'
    | 'history_cleaned'
    | 'batch_state' | 'job_start' | 'job_steps' | 'job_step' | 'job_done' | 'job_error'
  line?: string
  step?: string | null
  id?: string
  title?: string
  state?: string
  current_step?: string | null
  queue?: string[]
  error?: string
  // history archive
  dest?: string
  moved?: number
  locked?: number
  // batch events
  video?: string
  workspace?: string
  status?: string
  total?: number
  deliver?: string | null
  failures?: [string, string][]
}

/** WebSocket client with auto-reconnect. Buffers nothing when unmounted. */
export function useBus(onEvent: (e: BusEvent) => void): { connected: boolean } {
  const [connected, setConnected] = useState(false)
  const handlerRef = useRef(onEvent)
  handlerRef.current = onEvent

  useEffect(() => {
    let ws: WebSocket | null = null
    let closed = false
    let retry = 0
    let timer: ReturnType<typeof setTimeout>

    const connect = () => {
      if (closed) return
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws`)
      ws.onopen = () => {
        retry = 0
        setConnected(true)
      }
      ws.onmessage = (m) => {
        try {
          handlerRef.current(JSON.parse(m.data) as BusEvent)
        } catch {
          /* malformed frame — ignore */
        }
      }
      ws.onclose = () => {
        setConnected(false)
        if (!closed) {
          const wait = Math.min(1000 * 2 ** retry++, 10000)
          timer = setTimeout(connect, wait)
        }
      }
    }
    connect()

    return () => {
      closed = true
      clearTimeout(timer)
      ws?.close()
    }
  }, [])

  return { connected }
}
