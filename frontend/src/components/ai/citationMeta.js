/**
 * RAG 引用来源（citation）辅助函数。
 *
 * 契约（W3-5 `search_knowledge` 工具）：
 * tool_result.data.chunks = [{
 *   citation_no, chunk_id, source_type: 'book' | 'rule' | 'faq',
 *   title, text, book_id, url, score
 * }]
 *
 * 本文件是纯函数模块（不依赖 Vue / DOM），便于 Node 直接单测：
 * - 归一化后端可能缺失/为 null 的字段，保证渲染层不用到处判空；
 * - 多个知识检索调用时取“最后一次且 chunks 非空”的结果；
 * - 把 [n] 与来源卡片关联（citation_no 可能重复/越界，越界时由 UI 静默忽略）。
 */

export const SOURCE_TYPE_LABELS = {
  book: '馆藏',
  rule: '规则',
  faq: '常见问题',
}

/** 正文摘要截断长度（字符） */
export const SNIPPET_LIMIT = 120

function toNumber(value) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

/**
 * 来源类型 → 中文名。未登记的类型统一显示“知识库”，保证前端不出现英文枚举。
 * 同时兼容后端直接下发中文（馆藏/规则/常见问题）的情况。
 */
export function sourceTypeLabel(type) {
  const raw = String(type || '').trim()
  if (!raw) return '知识库'
  const key = raw.toLowerCase()
  if (SOURCE_TYPE_LABELS[key]) return SOURCE_TYPE_LABELS[key]
  if (Object.prototype.hasOwnProperty.call(SOURCE_TYPE_LABELS, raw)) return SOURCE_TYPE_LABELS[raw]
  if (raw === '馆藏' || raw === '规则' || raw === '常见问题') return raw
  return '知识库'
}

/**
 * 把 tool_result.data 归一化成引用数组。
 * 兼容两种形态：`{ chunks: [...] }`（契约）与直接数组 `[...]`（容错）。
 * 非法条目（null/非对象）直接过滤；缺 citation_no 时用下标 +1 兜底。
 */
export function normalizeChunks(data) {
  let raw = []
  if (Array.isArray(data)) {
    raw = data
  } else if (data && typeof data === 'object' && Array.isArray(data.chunks)) {
    raw = data.chunks
  } else {
    return []
  }

  const chunks = []
  raw.forEach((chunk, index) => {
    if (!chunk || typeof chunk !== 'object') return

    const no = toNumber(chunk.citation_no ?? chunk.citationNo)
    const bookIdRaw = chunk.book_id ?? chunk.bookId
    const bookId = bookIdRaw === null || bookIdRaw === undefined || bookIdRaw === '' ? null : bookIdRaw
    const title = typeof chunk.title === 'string' ? chunk.title.trim() : ''
    const text =
      typeof chunk.text === 'string'
        ? chunk.text
        : typeof chunk.content === 'string'
          ? chunk.content
          : ''

    chunks.push({
      citationNo: no !== null && no > 0 ? no : index + 1,
      chunkId: String(chunk.chunk_id ?? chunk.chunkId ?? ''),
      sourceType: String(chunk.source_type ?? chunk.sourceType ?? '').trim().toLowerCase(),
      title: title || '未命名知识片段',
      text,
      bookId,
      url: typeof chunk.url === 'string' ? chunk.url.trim() : '',
      score: toNumber(chunk.score),
    })
  })
  return chunks
}

/** 是否调用过知识检索工具（即使返回空 chunks，也说明这是一次“找依据”的回答） */
export function hasKnowledgeCall(tools) {
  return Array.isArray(tools) && tools.some((tool) => tool && tool.name === 'search_knowledge')
}

/**
 * 从 message.tools 取最后一次 `search_knowledge` 且 chunks 非空的结果。
 * 多次检索（多跳问题）时，以最后一次有内容的结果为准。
 */
export function pickCitationChunks(tools) {
  if (!Array.isArray(tools)) return []
  for (let index = tools.length - 1; index >= 0; index -= 1) {
    const tool = tools[index]
    if (!tool || tool.name !== 'search_knowledge') continue
    const chunks = normalizeChunks(tool.data)
    if (chunks.length) return chunks
  }
  return []
}

/**
 * 引用卡片的跳转目标：
 * - 有 book_id → 图书详情 `/books?id=<book_id>`；
 * - url 为 http(s) → 外部链接；
 * - url 为站内路径（/xxx 或 xxx）→ 站内路由；
 * - url 是其他 scheme（如 `book:3`）且有 book_id → 回落图书详情；
 * - 都没有 → 不可跳转。
 * @returns {{href: string, external: boolean} | null}
 */
export function citationLink(chunk) {
  if (!chunk) return null

  const bookHref =
    chunk.bookId !== null && chunk.bookId !== undefined
      ? `/books?id=${encodeURIComponent(chunk.bookId)}`
      : null
  const url = typeof chunk.url === 'string' ? chunk.url.trim() : ''

  if (url) {
    const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(url)
    if (scheme) {
      const name = scheme[1].toLowerCase()
      if (name === 'http' || name === 'https') return { href: url, external: true }
      return bookHref ? { href: bookHref, external: false } : null
    }
    return { href: url.startsWith('/') ? url : `/${url}`, external: false }
  }

  return bookHref ? { href: bookHref, external: false } : null
}

/**
 * 卡片摘要：压平空白 + 截断到 limit（默认 120 字），避免长文本撑爆布局。
 * 完整正文由“展开全文”展示。
 */
export function formatChunkSummary(text, limit = SNIPPET_LIMIT) {
  const clean = String(text || '')
    .replace(/\s+/g, ' ')
    .trim()
  if (!clean) return ''
  if (clean.length <= limit) return clean
  return `${clean.slice(0, limit)}…`
}

/**
 * 正文里是否存在 [n] 引用标注（用于“无 chunks 时是否提示”的判断）。
 * 排除 markdown 链接语法 `[1](url)`，避免把普通链接误判成引用。
 */
export function hasCitationMarkers(content) {
  return /(^|[^\[\]])[\[［](\d{1,3})[\]］](?!\()/.test(String(content || ''))
}
