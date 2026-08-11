import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './app.css'
import App from './App.jsx'

// 部署时通过 VITE_CODETRACE_API_KEY 注入；本地未配置时不加请求头，行为不变
const apiKey = import.meta.env.VITE_CODETRACE_API_KEY
const apiBase = import.meta.env.VITE_CODETRACE_API_BASE || '/api'
if (apiKey) {
  const originalFetch = window.fetch
  window.fetch = (input, init = {}) => {
    const url = typeof input === 'string' ? input : input.url
    const apiPrefixes = apiBase.startsWith('http')
      ? [apiBase]
      : [apiBase, window.location.origin + apiBase]
    // 精确匹配基址或基址下的子路径，避免 /apiary、/apix 等误判注入 key
    if (!apiPrefixes.some((p) => url === p || url.startsWith(p + '/'))) {
      return originalFetch(input, init)
    }
    const headers = new Headers(init.headers)
    if (input instanceof Request) {
      input.headers.forEach((value, key) => headers.set(key, value))
    }
    headers.set('X-API-Key', apiKey)
    return originalFetch(input, { ...init, headers })
  }
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
