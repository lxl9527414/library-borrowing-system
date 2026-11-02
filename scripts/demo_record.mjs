#!/usr/bin/env node
/**
 * 一键演示录制（W4-8）：驱动无头 Chrome 走完整业务流程，用 CDP screencast 录帧，
 * 再由 scripts/frames_to_gif.py 合成 GIF（不依赖 ffmpeg，只需要 Pillow）。
 *
 * 演示内容（真实模型 + 真实检索 + 真实数据库）：
 *   1. 说「帮我借红楼梦」→ 查馆藏 → 弹出写操作确认卡
 *   2. 点「确认执行」→ 真实借阅落库
 *   3. 问「逾期罚金怎么算？」→ 知识库检索 → 带 [1] 引用的回答 + 来源卡片
 *
 * 前置：bash scripts/dev_up.sh && cd frontend && npm run dev
 * 运行：node scripts/demo_record.mjs            # 录制帧到 /tmp/demo_frames
 *      node scripts/demo_record.mjs --no-record # 只跑流程不录帧（调试用）
 */

import { spawn } from 'node:child_process'
import { rmSync, mkdirSync, writeFileSync, readFileSync } from 'node:fs'
import { setTimeout as sleep } from 'node:timers/promises'

const CHROME = process.env.CHROME_BIN || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const APP_BASE = process.env.APP_BASE || 'http://127.0.0.1:5173'
const CDP_PORT = Number(process.env.CDP_PORT || 9391)
const JAVA_BASE = process.env.JAVA_BASE || 'http://127.0.0.1:18080'
const FRAME_DIR = process.env.FRAME_DIR || '/tmp/demo_frames'
const PROFILE = `/tmp/chrome-demo-${process.pid}`
const BOOK = process.env.DEMO_BOOK || '红楼梦'
const MAX_FRAMES = Number(process.env.MAX_FRAMES || 240)
const RECORD = !process.argv.includes('--no-record')

// ---------------------------------------------------------------------------
// 环境变量（本地演示账号）
// ---------------------------------------------------------------------------
function loadEnv() {
  const env = {}
  try {
    const text = readFileSync(new URL('../.env', import.meta.url), 'utf8')
    for (const line of text.split('\n')) {
      const t = line.trim()
      if (!t || t.startsWith('#') || !t.includes('=')) continue
      const i = t.indexOf('=')
      env[t.slice(0, i).trim()] = t.slice(i + 1).trim().replace(/^["']|["']$/g, '')
    }
  } catch {
    /* 用默认值 */
  }
  return env
}

// ---------------------------------------------------------------------------
// 极简 CDP 客户端（带事件回调，用于 screencast）
// ---------------------------------------------------------------------------
class Cdp {
  #ws
  #seq = 0
  #pending = new Map()
  #handlers = new Map()

  static async connect(url) {
    const client = new Cdp()
    await new Promise((resolve, reject) => {
      const ws = new WebSocket(url)
      ws.addEventListener('open', () => resolve(), { once: true })
      ws.addEventListener('error', () => reject(new Error('CDP 连接失败')), { once: true })
      client.#ws = ws
    })
    client.#ws.addEventListener('message', (ev) => {
      let msg
      try {
        msg = JSON.parse(String(ev.data))
      } catch {
        return
      }
      if (msg.id && client.#pending.has(msg.id)) {
        const { resolve, reject } = client.#pending.get(msg.id)
        client.#pending.delete(msg.id)
        msg.error ? reject(new Error(String(msg.error.message))) : resolve(msg.result)
        return
      }
      if (msg.method) {
        const handlers = client.#handlers.get(msg.method) || []
        for (const handler of handlers) handler(msg.params)
      }
    })
    return client
  }

  on(method, handler) {
    if (!this.#handlers.has(method)) this.#handlers.set(method, [])
    this.#handlers.get(method).push(handler)
  }

  send(method, params = {}, timeoutMs = 20000) {
    const id = ++this.#seq
    const task = new Promise((resolve, reject) => {
      this.#pending.set(id, { resolve, reject })
      setTimeout(() => {
        if (this.#pending.has(id)) {
          this.#pending.delete(id)
          reject(new Error(`CDP 超时：${method}`))
        }
      }, timeoutMs)
    })
    this.#ws.send(JSON.stringify({ id, method, params }))
    return task
  }

  close() {
    try {
      this.#ws.close()
    } catch {
      /* ignore */
    }
  }
}

async function launch() {
  try {
    rmSync(PROFILE, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 })
  } catch {
    /* ignore */
  }
  const chrome = spawn(
    CHROME,
    [
      '--headless',
      `--remote-debugging-port=${CDP_PORT}`,
      `--user-data-dir=${PROFILE}`,
      '--no-first-run',
      '--no-default-browser-check',
      '--no-sandbox',
      '--disable-gpu',
      '--hide-scrollbars',
      '--window-size=1280,860',
      'about:blank',
    ],
    { stdio: 'ignore' },
  )
  for (let i = 0; i < 80; i++) {
    try {
      const resp = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)
      if (resp.ok) {
        const pages = (await resp.json()).filter((t) => t.type === 'page' && t.webSocketDebuggerUrl)
        if (pages.length) return { chrome, wsUrl: pages[0].webSocketDebuggerUrl }
      }
    } catch {
      /* 还没起来 */
    }
    await sleep(250)
  }
  chrome.kill('SIGKILL')
  throw new Error('Chrome CDP 未就绪')
}

// ---------------------------------------------------------------------------
// 页面操作
// ---------------------------------------------------------------------------
const evaluate = (cdp, expression, awaitPromise = false) => cdp.send('Runtime.evaluate', {
  expression, returnByValue: true, awaitPromise, userGesture: true,
}).then((r) => {
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || '页面内 JS 异常')
  return r.result?.value
})

async function waitFor(cdp, expression, { timeoutMs = 90000, label = '条件' } = {}) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      if (await evaluate(cdp, expression)) return true
    } catch {
      /* 页面跳转中 */
    }
    await sleep(300)
  }
  throw new Error(`等待「${label}」超时`)
}

const waitIdle = (cdp) => waitFor(
  cdp,
  `![...document.querySelectorAll('button')].some((b) => /停止/.test(b.textContent || ''))`,
  { label: '流式结束' },
)

async function fillAndSend(cdp, text) {
  await waitIdle(cdp)
  await evaluate(cdp, `(() => {
    const el = document.querySelector('textarea') || document.querySelector('input[type=text]');
    if (!el) return 'NO_INPUT';
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, ${JSON.stringify(text)});
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.focus();
    return 'OK';
  })()`)
  await sleep(250)
  await evaluate(cdp, `(() => {
    const btn = [...document.querySelectorAll('button')].find((b) => /发送/.test(b.textContent || '') && !b.disabled);
    if (btn) { btn.click(); return 'BUTTON'; }
    const el = document.querySelector('textarea');
    el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
    return 'ENTER';
  })()`)
}

async function cleanupBorrow(cdp) {
  try {
    const token = await evaluate(cdp, `localStorage.getItem('library_token')`)
    if (!token) return null
    const headers = { Authorization: `Bearer ${token}`, 'X-Internal-Token': process.env.AI_INTERNAL_TOKEN || 'dev-internal-token' }
    const listed = await (await fetch(`${JAVA_BASE}/api/ai/internal/borrows/my`, { headers })).json()
    for (const record of listed?.data || []) {
      if (!String(record.bookTitle || '').includes(BOOK)) continue
      await fetch(`${JAVA_BASE}/api/ai/internal/return`, {
        method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ borrowId: record.id }),
      })
      return record.bookTitle
    }
  } catch (err) {
    console.warn(`  ⚠ 清理借阅失败：${err.message}`)
  }
  return null
}

// ---------------------------------------------------------------------------
// 主流程
// ---------------------------------------------------------------------------
async function main() {
  const env = loadEnv()
  const { chrome, wsUrl } = await launch()
  const cdp = await Cdp.connect(wsUrl)
  const frames = []
  try {
    await cdp.send('Page.enable')
    await cdp.send('Runtime.enable')
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: 1280, height: 860, deviceScaleFactor: 1, mobile: false,
    })

    console.log(`打开 ${APP_BASE}/login 并登录…`)
    await cdp.send('Page.navigate', { url: `${APP_BASE}/login` })
    await waitFor(cdp, 'document.querySelector("#app") && document.querySelector("#app").children.length > 0', {
      label: '登录页', timeoutMs: 30000,
    })
    const login = await evaluate(cdp, `(async () => {
      const resp = await fetch('/api/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: ${JSON.stringify(env.DEMO_USER || 'reader')}, password: ${JSON.stringify(env.DEMO_PASSWORD || 'reader123')} }),
      });
      const body = await resp.json();
      if (body.code !== 200) return 'FAIL:' + JSON.stringify(body).slice(0, 150);
      localStorage.setItem('library_token', body.data.token);
      localStorage.setItem('library_user', JSON.stringify(body.data.user));
      return 'OK:' + body.data.user.realName;
    })()`, true)
    if (!String(login).startsWith('OK')) throw new Error(`登录失败：${login}`)

    await cdp.send('Page.navigate', { url: `${APP_BASE}/chat` })
    await waitFor(cdp, '!!document.querySelector("textarea")', { label: '对话页', timeoutMs: 30000 })
    await waitIdle(cdp)
    await sleep(600)

    if (RECORD) {
      rmSync(FRAME_DIR, { recursive: true, force: true })
      mkdirSync(FRAME_DIR, { recursive: true })
      cdp.on('Page.screencastFrame', (params) => {
        if (frames.length >= MAX_FRAMES) {
          cdp.send('Page.screencastFrameAck', { sessionId: params.sessionId }).catch(() => {})
          return
        }
        frames.push({ t: Date.now(), data: params.data })
        cdp.send('Page.screencastFrameAck', { sessionId: params.sessionId }).catch(() => {})
      })
      await cdp.send('Page.startScreencast', {
        format: 'jpeg', quality: 55, maxWidth: 1000, maxHeight: 680, everyNthFrame: 2,
      })
      console.log('  录制中…')
    }

    console.log(`① 提问：帮我借${BOOK}`)
    await fillAndSend(cdp, `帮我借${BOOK}`)
    await waitFor(
      cdp,
      `[...document.querySelectorAll('button')].some((b) => /确认执行|确认借阅/.test(b.textContent || ''))`,
      { label: '确认卡片', timeoutMs: 120000 },
    )
    await sleep(1200) // 让观众看清确认卡

    console.log('② 点击「确认执行」')
    await evaluate(cdp, `(() => {
      const btn = [...document.querySelectorAll('button')].find((b) => /确认执行|确认借阅/.test(b.textContent || '') && !b.disabled);
      if (!btn) return 'NOT_FOUND';
      btn.click();
      return 'CLICKED';
    })()`)
    await waitFor(cdp, `/借阅成功|应还|成功/.test(document.body.innerText)`, { label: '借阅结果', timeoutMs: 120000 })
    await waitIdle(cdp)
    await sleep(900)

    console.log('③ 提问：逾期罚金怎么算？')
    await fillAndSend(cdp, '逾期罚金怎么算？最多封顶多少？')
    await waitFor(cdp, '/引用来源/.test(document.body.innerText)', { label: '引用来源', timeoutMs: 120000 })
    await waitIdle(cdp)
    await sleep(1500)

    if (RECORD) {
      await cdp.send('Page.stopScreencast').catch(() => {})
      console.log(`  共录到 ${frames.length} 帧`)
      const meta = []
      for (let i = 0; i < frames.length; i += 1) {
        const name = `frame-${String(i).padStart(5, '0')}.jpg`
        writeFileSync(`${FRAME_DIR}/${name}`, Buffer.from(frames[i].data, 'base64'))
        const next = frames[i + 1]?.t ?? frames[i].t + 120
        meta.push({ name, durationMs: Math.min(Math.max(next - frames[i].t, 40), 400) })
      }
      writeFileSync(`${FRAME_DIR}/meta.json`, JSON.stringify(meta, null, 2), 'utf8')
    }

    const cleaned = await cleanupBorrow(cdp)
    if (cleaned) console.log(`  · 已自动归还《${cleaned}》，数据保持干净`)
    console.log(`\n✅ 演示流程完成${RECORD ? `，帧目录：${FRAME_DIR}` : ''}`)
  } finally {
    cdp.close()
    chrome.kill('SIGKILL')
    try {
      rmSync(PROFILE, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 })
    } catch {
      /* ignore */
    }
  }
}

main().catch((err) => {
  console.error(`\n❌ ${err.message}`)
  process.exitCode = 1
})
