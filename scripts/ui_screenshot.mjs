#!/usr/bin/env node
/**
 * 用 Chrome DevTools Protocol 驱动无头 Chrome，截取「智能馆员」对话页的关键状态。
 *
 * 为什么自己写 CDP 而不是装 Playwright：
 *   - 不额外下载几百 MB 的浏览器二进制，直接复用本机已装的 Chrome
 *   - 面试时能讲清"浏览器自动化底层就是 CDP"：Page.navigate / Runtime.evaluate / Page.captureScreenshot
 *
 * 注意（踩过的坑）：无头 Chrome 里用 `Target.createTarget` + `Target.attachToTarget`
 * 会触发 `Inspector.targetCrashed`；改成连 `/json/list` 里已存在 page target 的
 * per-target WebSocket（不需要 sessionId），稳定可用。
 *
 * 前置：
 *   1. 起全套后端/AI 服务： bash scripts/dev_up.sh
 *   2. 起前端 dev server：   cd frontend && npm run dev
 * 运行：
 *   node scripts/ui_screenshot.mjs
 *   APP_BASE=http://127.0.0.1:18080 node scripts/ui_screenshot.mjs   # 指向打包后的静态站点
 *
 * 产物（默认 docs/screenshots/）：
 *   01-welcome.png   欢迎态 + 快捷问题
 *   02-found.png     检索结果 + 工具调用轨迹
 *   03-confirm.png   写操作确认卡片（人在回路）
 *   04-success.png   确认后真实借书成功
 */

import { spawn } from 'node:child_process'
import { rmSync, mkdirSync, writeFileSync, readFileSync } from 'node:fs'
import { setTimeout as sleep } from 'node:timers/promises'

// 读取仓库根目录 .env（本地凭据，已 gitignore）：仓库里只保留种子默认值，不出现真实密码
function loadDotEnv() {
  try {
    const text = readFileSync(new URL('../.env', import.meta.url), 'utf8')
    for (const line of text.split('\n')) {
      const trimmed = line.trim()
      if (!trimmed || trimmed.startsWith('#') || !trimmed.includes('=')) continue
      const idx = trimmed.indexOf('=')
      const key = trimmed.slice(0, idx).trim()
      const value = trimmed.slice(idx + 1).trim().replace(/^["']|["']$/g, '')
      if (!(key in process.env)) process.env[key] = value
    }
  } catch {
    /* 没有 .env 就用种子默认值 */
  }
}
loadDotEnv()

const CHROME = process.env.CHROME_BIN || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const APP_BASE = process.env.APP_BASE || 'http://127.0.0.1:5173'
const OUT_DIR = process.env.OUT_DIR || 'docs/screenshots'
const CDP_PORT = Number(process.env.CDP_PORT || 9333)
const USERNAME = process.env.APP_USER || process.env.DEMO_USER || 'reader'
const PASSWORD = process.env.APP_PASSWORD || process.env.DEMO_PASSWORD || 'reader123'
const BOOK = process.env.DEMO_BOOK || '红楼梦'
const PROFILE = `/tmp/chrome-ui-shots-${process.pid}`

/** 尽力删除临时 profile：Chrome 退出瞬间可能仍在写文件，ENOTEMPTY 不该让整个脚本失败 */
function tryRemove(dir) {
  try {
    rmSync(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 120 })
  } catch {
    /* 忽略：临时目录清理失败不影响结果 */
  }
}

// ---------------------------------------------------------------------------
// 极简 CDP 客户端（Node 24 自带 WebSocket，无需第三方库）
// ---------------------------------------------------------------------------
class Cdp {
  #ws
  #seq = 0
  #pending = new Map()

  static async connect(wsUrl) {
    const client = new Cdp()
    await new Promise((resolve, reject) => {
      const ws = new WebSocket(wsUrl)
      ws.addEventListener('open', () => resolve(), { once: true })
      ws.addEventListener('error', () => reject(new Error('CDP WebSocket 连接失败')), { once: true })
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
      }
    })
    return client
  }

  send(method, params = {}, timeoutMs = 15000) {
    const id = ++this.#seq
    const task = new Promise((resolve, reject) => {
      this.#pending.set(id, { resolve, reject })
      setTimeout(() => {
        if (this.#pending.has(id)) {
          this.#pending.delete(id)
          reject(new Error(`CDP 命令超时：${method}`))
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

async function launchChrome() {
  tryRemove(PROFILE)
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
      '--window-size=1440,1000',
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
  throw new Error('Chrome CDP 未就绪，请检查 CHROME_BIN 路径')
}

async function evaluate(cdp, expression, { awaitPromise = false } = {}) {
  const res = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise, userGesture: true })
  if (res.exceptionDetails) {
    const detail = res.exceptionDetails.exception?.description || res.exceptionDetails.text
    throw new Error(`页面内 JS 异常：${detail}`)
  }
  return res.result?.value
}

async function waitFor(cdp, expression, { timeoutMs = 20000, label = '条件', intervalMs = 250 } = {}) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      if (await evaluate(cdp, expression)) return true
    } catch {
      /* 页面可能正在跳转，忽略 */
    }
    await sleep(intervalMs)
  }
  const body = await evaluate(cdp, 'document.body ? document.body.innerText.slice(0, 1200) : "(空)"').catch(() => '(读取失败)')
  throw new Error(`等待「${label}」超时（${timeoutMs}ms）。当前页面文本：\n${body}`)
}

async function shot(cdp, name) {
  const res = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
  const buffer = Buffer.from(res.data, 'base64')
  const file = `${OUT_DIR}/${name}`
  writeFileSync(file, buffer)
  console.log(`  📸 ${file}  (${(buffer.length / 1024).toFixed(0)} KB)`)
  return file
}

/** 设置输入框内容并触发 Vue 的 v-model 更新 */

/** 等待当前流式生成结束（界面在生成中会显示「停止」按钮）。真实模型比 mock 慢，必须等。 */
async function waitIdle(cdp, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const streaming = await evaluate(
      cdp,
      `[...document.querySelectorAll('button')].some((b) => /停止/.test(b.textContent || ''))`,
    )
    if (!streaming) return true
    await sleep(400)
  }
  throw new Error(`等待流式结束超时（${timeoutMs}ms）`)
}

async function fillInput(cdp, text) {
  await waitIdle(cdp)
  const result = await evaluate(
    cdp,
    `(() => {
      const el = document.querySelector('textarea') || document.querySelector('input[type="text"]');
      if (!el) return 'NO_INPUT';
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, ${JSON.stringify(text)});
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.focus();
      return 'OK';
    })()`,
  )
  if (result !== 'OK') throw new Error('找不到输入框（textarea / input[type=text]）')
  await sleep(150)
}

/** 点击发送按钮；找不到则回退为 Enter 键 */
async function clickSend(cdp) {
  const result = await evaluate(
    cdp,
    `(() => {
      const btn = [...document.querySelectorAll('button')]
        .find((b) => /发送|发送消息|Send/i.test(b.textContent || '') && !b.disabled);
      if (btn) { btn.click(); return 'BUTTON'; }
      const el = document.querySelector('textarea') || document.querySelector('input[type="text"]');
      if (!el) return 'NO_INPUT';
      el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
      return 'ENTER';
    })()`,
  )
  if (result === 'NO_INPUT') throw new Error('找不到输入框，无法发送')
  console.log(`  · 发送方式：${result}`)
}

/** 点击确认/取消按钮 */
async function clickDecision(cdp, approve = true) {
  const result = await evaluate(
    cdp,
    `(() => {
      const re = ${approve ? '/确认|确定|执行|同意/' : '/取消|拒绝/'};
      const candidates = [...document.querySelectorAll('button')]
        .filter((b) => re.test(b.textContent || '') && !b.disabled);
      if (!candidates.length) {
        return 'NOT_FOUND:' + [...document.querySelectorAll('button')].map((b) => (b.textContent || '').trim()).join('|');
      }
      candidates[0].click();
      return 'OK';
    })()`,
  )
  if (result !== 'OK') throw new Error(`找不到${approve ? '确认' : '取消'}按钮：${result}`)
}


/** 调用业务后端内部接口，把本次演示借出的书归还（保证脚本可反复执行、不污染数据） */
async function cleanupBorrow(cdp) {
  if (process.env.DEMO_CLEANUP === '0') return null
  const javaBase = process.env.JAVA_BASE || 'http://127.0.0.1:18080'
  const internalToken = process.env.AI_INTERNAL_TOKEN || 'dev-internal-token'
  const token = await evaluate(cdp, `localStorage.getItem('library_token')`)
  if (!token) return null
  const headers = { Authorization: `Bearer ${token}`, 'X-Internal-Token': internalToken }
  try {
    const listed = await (await fetch(`${javaBase}/api/ai/internal/borrows/my`, { headers })).json()
    for (const record of listed?.data || []) {
      if (!String(record.bookTitle || '').includes(BOOK)) continue
      await fetch(`${javaBase}/api/ai/internal/return`, {
        method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ borrowId: record.id }),
      })
      return record.bookTitle
    }
  } catch (err) {
    console.warn(`  ⚠ 自动归还失败（不影响截图结果）：${err.message}`)
  }
  return null
}

async function main() {
  mkdirSync(OUT_DIR, { recursive: true })
  console.log(`启动无头 Chrome… (${CHROME})`)
  const { chrome, wsUrl } = await launchChrome()
  let cdp
  try {
    cdp = await Cdp.connect(wsUrl)
    await cdp.send('Page.enable')
    await cdp.send('Runtime.enable')
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: 1440,
      height: 1000,
      deviceScaleFactor: 2,
      mobile: false,
    })

    // 1) 登录：页面内 fetch 拿 token 写 localStorage（不依赖登录表单的 DOM 细节）
    console.log(`打开 ${APP_BASE}/login 并注入登录态…`)
    await cdp.send('Page.navigate', { url: `${APP_BASE}/login` })
    await waitFor(cdp, 'document.querySelector("#app") && document.querySelector("#app").children.length > 0', {
      label: '登录页渲染',
      timeoutMs: 30000,
    })
    const loginResult = await evaluate(
      cdp,
      `(async () => {
        const resp = await fetch('/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: ${JSON.stringify(USERNAME)}, password: ${JSON.stringify(PASSWORD)} }),
        });
        const body = await resp.json();
        if (body.code !== 200 || !body.data) return 'FAIL:' + JSON.stringify(body).slice(0, 200);
        localStorage.setItem('library_token', body.data.token);
        localStorage.setItem('library_user', JSON.stringify(body.data.user));
        return 'OK:' + body.data.user.realName;
      })()`,
      { awaitPromise: true },
    )
    if (!String(loginResult).startsWith('OK')) throw new Error(`登录失败：${loginResult}`)
    console.log(`  · 登录成功 ${loginResult}`)

    // 2) 进入对话页
    await cdp.send('Page.navigate', { url: `${APP_BASE}/chat` })
    await waitFor(cdp, '!!(document.querySelector("textarea") || document.querySelector("input[type=text]"))', {
      label: '对话页渲染',
      timeoutMs: 30000,
    })
    await sleep(900)
    const health = await evaluate(cdp, `fetch('/api/ai/health').then((r) => r.json()).catch(() => null)`)
    console.log(`  · AI 服务健康：${JSON.stringify(health)}`)
    await shot(cdp, '01-welcome.png')

    // 3) 只读：检索（工具调用轨迹）
    console.log(`提问：帮我找${BOOK}`)
    await fillInput(cdp, `帮我找${BOOK}`)
    await clickSend(cdp)
    await waitFor(
      cdp,
      `document.body.innerText.includes(${JSON.stringify(BOOK)}) && /可借|找到/.test(document.body.innerText)`,
      { label: '检索结果返回', timeoutMs: 30000 },
    )
    await sleep(700)
    await shot(cdp, '02-found.png')

    // 4) 写操作：确认卡片
    console.log(`提问：帮我借${BOOK}（应出现确认卡片）`)
    await fillInput(cdp, `帮我借${BOOK}`)
    await clickSend(cdp)
    await waitFor(
      cdp,
      `[...document.querySelectorAll('button')].some((b) => /确认执行|确认借阅/.test(b.textContent || ''))`,
      { label: '确认卡片按钮出现', timeoutMs: 90000 },
    )
    await sleep(500)
    await shot(cdp, '03-confirm.png')

    // 5) 确认 → 真实落库
    console.log('点击「确认」…')
    await clickDecision(cdp, true)
    await waitFor(cdp, '/成功|应还|已借|借阅成功/.test(document.body.innerText)', {
      label: '借阅执行结果',
      timeoutMs: 90000,
    })
    await waitIdle(cdp)
    await sleep(700)
    await shot(cdp, '04-success.png')

    // 5.5) 自清理：把刚才"真借"的书还掉，避免给后续测试留下在借记录
    const cleaned = await cleanupBorrow(cdp)
    if (cleaned) console.log(`  · 已自动归还《${cleaned}》，环境保持干净`)

    // 6) 回归检查：「新对话」必须能反复点，且开场白只保留 1 条
    //    （曾经的两个缺陷：canReset 依赖"存在用户消息"导致按钮点一次就变灰；
    //      resetChat 每次新建欢迎语导致开场白重复）
    console.log('回归检查：连续点击「新对话」…')
    for (let i = 1; i <= 2; i += 1) {
      const clicked = await evaluate(
        cdp,
        `(() => {
          const btn = [...document.querySelectorAll('button')].find((b) => /新对话/.test(b.textContent || ''));
          if (!btn) return 'NO_BTN';
          if (btn.disabled) return 'DISABLED';
          btn.click();
          return 'CLICKED';
        })()`,
      )
      if (clicked !== 'CLICKED') throw new Error(`「新对话」第 ${i} 次点击失败：${clicked}`)
      await sleep(450)
      const state = JSON.parse(
        await evaluate(
          cdp,
          `JSON.stringify((() => {
            const list = [...document.querySelectorAll('.chat-list > *')];
            return {
              bubbles: list.length,
              welcome: list.filter((b) => (b.innerText || '').includes('我是云阁智能馆员')).length,
            };
          })())`,
        ),
      )
      if (state.bubbles !== 1 || state.welcome !== 1) {
        throw new Error(`「新对话」后状态异常：${JSON.stringify(state)}，预期只剩 1 条开场白`)
      }
    }
    await shot(cdp, '05-new-chat.png')
    console.log('  ✅ 新对话可反复点击，开场白保持 1 条')

    console.log(`\n✅ 截图完成：${OUT_DIR}`)
  } finally {
    cdp?.close()
    chrome.kill('SIGKILL')
    await sleep(300)
    tryRemove(PROFILE)
  }
}

main().catch((err) => {
  console.error(`\n❌ ${err.message}`)
  process.exitCode = 1
})
