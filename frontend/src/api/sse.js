/**
 * SSE 解析工具（无浏览器依赖，可直接用 Node 单测）
 *
 * 设计要点（对应验收要求）：
 * 1. 跨 chunk 分片：解析器内部保留 buffer，只处理完整的行（以 \n 结尾），
 *    未结束的半行留在 buffer 里等待下一个 chunk；因此即使一个 UTF-8 字符、
 *    甚至 "\r\n" 被拆到两个 chunk 也不会解析错。
 * 2. 多行 data：同一个事件内出现多个 `data:` 行时按规范用 "\n" 连接。
 * 3. CRLF / LF 混用：每行去掉尾部 \r（兼容 sse_starlette 实际输出的 \r\n）。
 * 4. 注释行：以 ":" 开头的行（含服务端 15s 一次的 ping）直接忽略。
 * 5. 末尾无空行：流结束时 flush() 会补处理残留半行并强制分发最后一个事件，
 *    避免服务端在 done/error 后立刻关闭连接导致最后一个事件丢失。
 * 6. abort：调用方传入 AbortSignal 时，流被中止视为正常结束（不抛错），
 *    解析器不会残留状态，也不会把 AbortError 当成网络错误。
 */

/**
 * 创建一个逐行消费的 SSE 解析器。
 * @param {(event: {event: string, data: string, id: string}) => void} onEvent
 */
export function createSSEParser(onEvent) {
  let buffer = ''
  let started = false
  let eventName = ''
  let dataLines = []
  let lastEventId = ''
  let hasDataLine = false

  function dispatch() {
    const name = eventName || 'message'
    const data = dataLines.join('\n')
    const hadData = hasDataLine
    eventName = ''
    dataLines = []
    hasDataLine = false
    // 规范：只有 data 行的事件才分发；单独出现的 `event:` 行忽略
    if (!hadData) return
    onEvent({ event: name, data, id: lastEventId })
  }

  function handleLine(line) {
    // 空行 = 事件边界
    if (line === '') {
      dispatch()
      return
    }
    // 注释 / ping
    if (line.charCodeAt(0) === 58 /* ':' */) return

    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    // 规范：冒号后可有且仅有一个前导空格需要去掉
    if (value.charCodeAt(0) === 32 /* ' ' */) value = value.slice(1)

    if (field === 'event') {
      eventName = value
    } else if (field === 'data') {
      dataLines.push(value)
      hasDataLine = true
    } else if (field === 'id') {
      lastEventId = value
    } else {
      // retry / 未知字段：忽略，避免脏数据污染后续解析
    }
  }

  return {
    /**
     * 喂入一段文本（来自 TextDecoder 的解码结果）
     * @param {string} chunk
     */
    feed(chunk) {
      if (!chunk) return
      if (!started) {
        started = true
        if (chunk.charCodeAt(0) === 0xfeff) chunk = chunk.slice(1)
      }
      buffer += chunk
      let index = buffer.indexOf('\n')
      while (index !== -1) {
        let line = buffer.slice(0, index)
        buffer = buffer.slice(index + 1)
        if (line.endsWith('\r')) line = line.slice(0, -1)
        handleLine(line)
        index = buffer.indexOf('\n')
      }
    },

    /**
     * 流结束：处理末尾没有换行/没有空行的残留内容，并补发最后一个事件
     */
    flush() {
      if (buffer) {
        let line = buffer
        buffer = ''
        if (line.endsWith('\r')) line = line.slice(0, -1)
        if (line) handleLine(line)
      }
      dispatch()
    },

    reset() {
      buffer = ''
      started = false
      eventName = ''
      dataLines = []
      lastEventId = ''
      hasDataLine = false
    },
  }
}

/**
 * 消费一个 fetch Response 的 SSE 流。
 * @param {Response} response
 * @param {{ onEvent: Function, signal?: AbortSignal }} options
 */
export async function consumeSSE(response, { onEvent, signal } = {}) {
  if (!response || !response.body) {
    throw new Error('当前浏览器不支持流式响应（response.body 为空）')
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  const parser = createSSEParser(onEvent)

  let aborted = false
  const handleAbort = () => {
    aborted = true
    // 主动取消底层读取，让 await reader.read() 立即返回
    reader.cancel().catch(() => {})
  }

  if (signal) {
    if (signal.aborted) {
      await reader.cancel().catch(() => {})
      return
    }
    signal.addEventListener('abort', handleAbort, { once: true })
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      parser.feed(decoder.decode(value, { stream: true }))
    }
    // 补上解码器内部可能残留的多字节字符
    parser.feed(decoder.decode())
    parser.flush()
  } catch (error) {
    // 主动中止属于正常路径，不要把 AbortError 抛给上层
    if (aborted || signal?.aborted || error?.name === 'AbortError') return
    throw error
  } finally {
    if (signal) signal.removeEventListener('abort', handleAbort)
    try {
      reader.releaseLock()
    } catch (error) {
      /* 读取被取消时 releaseLock 可能抛错，忽略即可 */
    }
  }
}

/**
 * 把 SSE data 字符串解析成对象：优先 JSON.parse，失败时保留原始字符串。
 * （本项目所有事件都是单行 JSON，但多行 data 拼接后同样能解析）
 */
export function parseEventData(raw) {
  if (raw === '') return null
  try {
    return JSON.parse(raw)
  } catch (error) {
    return raw
  }
}
