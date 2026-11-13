/** 输入源草稿：视频地址 + 分辨率，刷新页面后仍在。
 *
 * 只存这两个表单字段，不碰下载状态、不存凭证。localStorage 在隐私模式 /
 * 被禁用 / JSON 损坏时都可能抛错，全部降级成「不记住」，绝不让页面崩。
 */

const DRAFT_KEY = 'videolingo.input.draft'
const DEFAULT_RESOLUTION = '1080'

export interface InputDraft {
  url: string
  resolution: string
}

const EMPTY: InputDraft = { url: '', resolution: DEFAULT_RESOLUTION }

export function loadInputDraft(): InputDraft {
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY)
    if (!raw) return { ...EMPTY }
    const parsed = JSON.parse(raw) as Partial<InputDraft> | null
    return {
      url: typeof parsed?.url === 'string' ? parsed.url : '',
      resolution: typeof parsed?.resolution === 'string' && parsed.resolution
        ? parsed.resolution
        : DEFAULT_RESOLUTION,
    }
  } catch {
    return { ...EMPTY }
  }
}

export function saveInputDraft(draft: InputDraft): void {
  try {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify(draft))
  } catch {
    // 存储不可用：只是不记住，下载功能照常可用
  }
}
