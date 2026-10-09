/**
 * Spec 003 T016：阅读器唯一 Markdown 渲染口径（单例）。
 *
 * 管线：markdown-it(html:true, linkify:false) → markdown-it-texmath(KaTeX)
 *   → 图片规则去 src、只留 data-art → DOMPurify 白名单消毒。
 * 安全红线：
 *  - 渲染结果不含任何真实图片 src；图片由 ReaderView 带 Bearer 换 objectURL；
 *  - 所有 on* 事件属性、javascript: 一律不输出；
 *  - href 仅放行页内锚点与同源相对路径，任意外链一律摘除；
 *  - KaTeX 样式/字体随包本地打包，禁止 CDN。
 */
import MarkdownIt from 'markdown-it'
import texmath from 'markdown-it-texmath'
import katex from 'katex'
import DOMPurify from 'dompurify'
import 'katex/dist/katex.min.css'

const ART_NAME_RE = /^[A-Za-z0-9._-]+$/
const ART_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp'])

/**
 * 成品图片名校验：与后端 resolve_image 口径一致（svg 拒绝、无目录段）。
 */
export function isSafeArtName(name) {
  if (typeof name !== 'string' || !ART_NAME_RE.test(name)) {
    return false
  }
  const dot = name.lastIndexOf('.')
  if (dot <= 0) {
    return false
  }
  return ART_EXTENSIONS.has(name.slice(dot).toLowerCase())
}

/** 从 Markdown/HTML 图片地址中提取 images/ 下的扁平文件名。 */
function artNameFromSrc(src) {
  if (typeof src !== 'string' || !src) {
    return null
  }
  // 同时兼容 / 与历史 Windows 反斜杠写法；只取末段
  const base = src.split(/[/\\]/).pop()
  return isSafeArtName(base) ? base : null
}

function escapeAttr(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

const md = new MarkdownIt({
  html: true,
  linkify: false,
  breaks: false,
  typographer: false,
})

md.use(texmath, {
  engine: katex,
  delimiters: 'dollars',
  katexOptions: {
    throwOnError: false,
    output: 'htmlAndMathml',
  },
})

// Markdown 语法图片：不输出 src，改写为 data-art 占位
md.renderer.rules.image = (tokens, idx, options, env, self) => {
  const token = tokens[idx]
  const rawSrc = token.attrGet('src') || ''
  const alt = self.renderInlineAsText(token.children || [], options, env)
  const name = artNameFromSrc(rawSrc)
  const altAttr = alt ? ` alt="${escapeAttr(alt)}"` : ' alt=""'
  if (!name) {
    // 非法/外部图片：保留 alt 的空 img，由 ReaderView 统一渲染失败占位
    return `<img${altAttr} />`
  }
  return `<img${altAttr} data-art="${escapeAttr(name)}" />`
}

// --------------------------------------------------------------------------
// DOMPurify 白名单
// --------------------------------------------------------------------------

const ALLOWED_TAGS = [
  // 基础排版
  'p', 'br', 'hr', 'span', 'div', 'blockquote', 'pre', 'code', 'em', 'strong',
  'b', 'i', 'u', 'del', 's', 'sub', 'sup', 'mark', 'small', 'abbr', 'cite',
  'ul', 'ol', 'li', 'dl', 'dt', 'dd',
  // 标题（章节标题由后端切走，正文内仍允许）
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  // 表格
  'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col',
  // 折叠区（附录）
  'details', 'summary',
  // 图片（无 src）
  'img',
  // 仅允许同源/锚点链接
  'a',
  // KaTeX HTML
  'annotation',
  // MathML（KaTeX htmlAndMathml 输出）
  'math', 'semantics', 'mrow', 'mi', 'mo', 'mn', 'ms', 'msup', 'msub',
  'msubsup', 'mfrac', 'msqrt', 'mroot', 'mtable', 'mtr', 'mtd', 'mtext',
  'mspace', 'mstyle', 'merror', 'mphantom', 'menclose', 'mfenced',
  'munder', 'mover', 'munderover', 'mpadded',
]

const ALLOWED_ATTR = [
  'class', 'style', 'aria-hidden',
  'alt', 'data-art',
  // 表格
  'colspan', 'rowspan', 'align', 'scope',
  // 链接（值由 hook 二次校验）
  'href',
  // MathML
  'encoding', 'columnalign', 'columnspacing', 'rowspacing', 'stretchy',
  'width', 'height', 'voffset', 'maxsize', 'minsize', 'mathvariant',
  'displaystyle', 'notation', 'open', 'close', 'side', 'linethickness',
  'numalign', 'denalign', 'lspace', 'rspace', 'largeop', 'movablelimits',
  'accent', 'accentunder', 'separator', 'fence', 'separators',
]

let purifyHooked = false

function ensurePurifyHooks() {
  if (purifyHooked) {
    return
  }
  purifyHooked = true

  // 原始 HTML 中的 <img> 同样去 src、改 data-art（html:true 绕过了渲染规则）
  DOMPurify.addHook('uponSanitizeNode', (node) => {
    if (node.nodeName && node.nodeName.toLowerCase() === 'img') {
      const name = artNameFromSrc(node.getAttribute('src') || '')
      node.removeAttribute('src')
      node.removeAttribute('srcset')
      if (name) {
        node.setAttribute('data-art', name)
      }
    }
  })

  // href 仅放行页内锚点（#...）与同源相对路径（/ 开头但非协议相对 //）；
  // 外站、javascript:/mailto:/tel: 等一律摘除
  DOMPurify.addHook('uponSanitizeAttribute', (node, data) => {
    if (data.attrName !== 'href') {
      return
    }
    const value = (data.attrValue || '').trim()
    const safe =
      value.startsWith('#') ||
      (value.startsWith('/') && !value.startsWith('//'))
    if (!safe) {
      data.keepAttr = false
    }
  })
}

/**
 * 渲染成品 Markdown 片段为消毒后的 HTML 字符串。
 */
export function renderMarkdown(src) {
  const text = typeof src === 'string' ? src : ''
  const rawHtml = md.render(text)
  ensurePurifyHooks()
  return DOMPurify.sanitize(rawHtml, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
    // 允许：#锚点、/开头的相对路径、纯安全文件名（data-art 形如 sample.png
    // 也会被 DOMPurify 按此正则校验 data-* 属性值）；禁止任何协议与 // 协议相对
    ALLOWED_URI_REGEXP: /^(?:#|\/(?!\/)|[A-Za-z0-9][A-Za-z0-9._-]*)$/i,
    FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed', 'form'],
  })
}
