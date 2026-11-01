/**
 * AI 工具元信息：中文名、参数展示、状态文案。
 * 工具名以契约 §3 为准，前端只做展示映射，不参与决策。
 */

export const TOOL_LABELS = {
  search_books: '查馆藏',
  get_book_detail: '查详情',
  get_my_borrows: '查我的借阅',
  borrow_book: '借阅',
  return_book: '归还',
  reserve_book: '预约',
}

const ARG_LABELS = {
  keyword: '关键词',
  category: '分类',
  size: '条数',
  bookId: '图书 ID',
  borrowId: '借阅记录 ID',
}

/** 工具中文名；未登记的工具名兜底返回原名 */
export function toolLabel(name) {
  if (!name) return '工具调用'
  return TOOL_LABELS[name] || name
}

/** 把参数对象格式化成一行可读文本，例如 `关键词=红楼梦` */
export function formatToolArgs(args) {
  if (!args || typeof args !== 'object') return ''
  if (Array.isArray(args.toolCalls)) {
    return `${args.toolCalls.length} 个操作`
  }
  const parts = []
  for (const [key, value] of Object.entries(args)) {
    if (value === null || value === undefined || value === '') continue
    const label = ARG_LABELS[key] || key
    const text = typeof value === 'object' ? JSON.stringify(value) : String(value)
    parts.push(`${label}=${text}`)
  }
  return parts.join(' · ')
}

/** 轨迹条目主文案：tool_call 时"正在查馆藏"、完成后"查馆藏完成" */
export function toolStatusText(name, status) {
  const label = toolLabel(name)
  if (status === 'ok') return `${label}完成`
  if (status === 'error') return `${label}失败`
  return `正在${label}`
}

/** 原始 data 的展示文本，避免超大 JSON 撑爆页面 */
export function formatRawData(data) {
  if (data === null || data === undefined) return ''
  let text
  if (typeof data === 'string') {
    text = data
  } else {
    try {
      text = JSON.stringify(data, null, 2)
    } catch (error) {
      text = String(data)
    }
  }
  const LIMIT = 4000
  if (text.length > LIMIT) {
    return `${text.slice(0, LIMIT)}\n…（已截断，共 ${text.length} 字符）`
  }
  return text
}
