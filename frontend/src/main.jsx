import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './app.css'
import App from './App.jsx'

// 部署时通过 VITE_CODETRACE_API_KEY 注入；本地未配置时不加请求头，行为不变
// trim 与后端 CODETRACE_API_KEY 的 strip 对齐，避免环境值含空格/换行导致 401
const apiKey = (import.meta.env.VITE_CODETRACE_API_KEY || '').trim()
const apiBase = import.meta.env.VITE_CODETRACE_API_BASE || '/api'
if (apiKey) {
  // 基址统一解析为 origin + path（补尾斜杠），相对/绝对配置走同一套匹配；
  // base 用 location.href，与浏览器 fetch 对相对 URL 的解析基准保持一致
  let apiBaseUrl
  try {
    apiBaseUrl = new URL(apiBase, window.location.href)
  } catch {
    console.warn('[CodeTrace] VITE_CODETRACE_API_BASE 配置无效，已跳过请求头注入')
  }
  const apiBasePath = apiBaseUrl
    ? (apiBaseUrl.pathname.endsWith('/') ? apiBaseUrl.pathname : apiBaseUrl.pathname + '/')
    : ''
  if (!apiBaseUrl || apiBasePath === '/') {
    // 根路径会命中所有同源请求，禁止自动注入，避免 key 外泄
    if (apiBasePath === '/') {
      console.warn('[CodeTrace] VITE_CODETRACE_API_BASE 为根路径，已跳过请求头注入')
    }
  } else {
    const apiOrigin = apiBaseUrl.origin
    const basePath = apiBasePath.slice(0, -1)
    const crossOrigin = apiOrigin !== window.location.origin
    const originalFetch = window.fetch
    // 跨域配置：把相对 /api 请求改写为基址（保留 pathname 后缀与查询/哈希）
    const resolveApiUrl = (raw) => {
      const parsed = new URL(raw, window.location.href)
      const suffix = parsed.pathname === basePath ? '' : parsed.pathname.slice(basePath.length)
      // fetch 不会发送 fragment，无需保留 hash
      return apiOrigin + basePath + suffix + parsed.search
    }
    window.fetch = (input, init) => {
      const reqInit = init ?? {}
      const rawUrl = typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.href
          : input?.url ?? ''
      // 解析 URL 后校验「同源 + pathname 前缀」，忽略查询参数/哈希；
      // 精确匹配基址或基址下的子路径，避免 /apiary、/apix 等误判注入 key
      const isApiRequest = (raw) => {
        if (!raw) return false
        let parsed
        try {
          parsed = new URL(raw, window.location.href)
        } catch {
          return false
        }
        const pathMatches = parsed.pathname === basePath || parsed.pathname.startsWith(apiBasePath)
        if (!pathMatches) return false
        // 同源配置：仅接受基址所在源的请求；跨域配置：相对路径请求视为 API 请求（会改写为基址）
        if (parsed.origin !== apiOrigin) {
          return crossOrigin && parsed.origin === window.location.origin
        }
        return true
      }
      if (!isApiRequest(rawUrl)) {
        return originalFetch(input, reqInit)
      }
      // 先复制 input.headers，再应用 init.headers（与 fetch 标准一致：init 优先）
      const headers = new Headers()
      if (input instanceof Request) {
        input.headers.forEach((value, key) => headers.set(key, value))
      }
      if (reqInit.headers) {
        new Headers(reqInit.headers).forEach((value, key) => headers.set(key, value))
      }
      headers.set('X-API-Key', apiKey)
      if (crossOrigin) {
        if (typeof input === 'string' || input instanceof URL) {
          return originalFetch(resolveApiUrl(rawUrl), { ...reqInit, headers })
        }
        if (input instanceof Request) {
          // 复制原 Request 到改写后的地址（method/headers/body 一并带过去）
          return originalFetch(new Request(resolveApiUrl(rawUrl), input), { ...reqInit, headers })
        }
      }
      return originalFetch(input, { ...reqInit, headers })
    }
  }
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
