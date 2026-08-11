import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './app.css'
import App from './App.jsx'

// 部署时通过 VITE_CODETRACE_API_KEY 注入；本地未配置时不加请求头，行为不变
const apiKey = import.meta.env.VITE_CODETRACE_API_KEY
const apiBase = import.meta.env.VITE_CODETRACE_API_BASE || '/api'
if (apiKey) {
  // 基址统一解析为 origin + path（补尾斜杠），相对/绝对配置走同一套匹配
  const apiBaseUrl = new URL(apiBase, window.location.origin)
  const apiBasePath = apiBaseUrl.pathname.endsWith('/')
    ? apiBaseUrl.pathname
    : apiBaseUrl.pathname + '/'
  const originalFetch = window.fetch
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
        parsed = new URL(raw, window.location.origin)
      } catch {
        return false
      }
      if (parsed.origin !== apiBaseUrl.origin) return false
      return parsed.pathname === apiBasePath.slice(0, -1) || parsed.pathname.startsWith(apiBasePath)
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
    return originalFetch(input, { ...reqInit, headers })
  }
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
