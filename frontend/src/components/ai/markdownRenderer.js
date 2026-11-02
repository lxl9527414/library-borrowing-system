/**
 * markdown-it 渲染器（全站 AI 正文统一入口，纯函数便于 Node 单测）。
 *
 * 引用标注的处理方式（重点）：
 * - 在 markdown-it 的 `text` 渲染规则里做替换，而不是对最终 HTML 字符串做正则。
 *   这样只会命中“文本节点”，`code_inline`（行内代码）、`fence`（代码块）、
 *   链接的 href/属性都由各自的渲染规则输出，不会被误伤 ——
 *   例如 `` `[1]` `` 与 ```[2]``` 会原样显示，`[1](url)` 仍渲染为普通链接。
 * - 替换发生在 escapeHtml 之后，插入内容只含数字，不存在 HTML 注入风险。
 * - 是否启用由每次渲染的 env.cite 决定（不影响其它调用方的纯 markdown 渲染）。
 */

import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

// 外部链接新窗口打开，并加 noopener
const defaultLinkOpen =
  md.renderer.rules.link_open ||
  ((tokens, index, options, env, self) => self.renderToken(tokens, index, options))
md.renderer.rules.link_open = (tokens, index, options, env, self) => {
  tokens[index].attrSet('target', '_blank')
  tokens[index].attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen(tokens, index, options, env, self)
}

const defaultTextRule =
  md.renderer.rules.text || ((tokens, index) => md.utils.escapeHtml(tokens[index].content))

// [1] / [12] → <sup class="cite-mark" data-no="n">n</sup>
// 仅文本 token 走这里：代码块、行内代码、链接目标都不经过该规则
md.renderer.rules.text = (tokens, index, options, env) => {
  const content = tokens[index].content
  if (!env?.cite) return defaultTextRule(tokens, index, options, env, md)
  return md.utils
    .escapeHtml(content)
    .replace(
      /\[(\d{1,3})\]/g,
      (match, no) =>
        `<sup class="cite-mark" data-no="${no}" role="button" tabindex="0" title="查看引用 ${no}">${no}</sup>`,
    )
}

/**
 * 渲染 markdown 正文。
 * @param {string} content
 * @param {{cite?: boolean}} [options] cite=true 时把 [n] 渲染成可交互引用标记
 */
export function renderMarkdown(content, options = {}) {
  return md.render(String(content || ''), { cite: Boolean(options.cite) })
}

export { md }
