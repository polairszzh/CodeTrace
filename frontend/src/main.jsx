import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './app.css'
import App from './App.jsx'

// 部署时通过 VITE_CODETRACE_API_KEY 注入；本地未配置时不加请求头，行为不变
const apiKey = import.meta.env.VITE_CODETRACE_API_KEY
if (apiKey) {
  const originalFetch = window.fetch
  window.fetch = (input, init = {}) => {
    const url = typeof input === 'string' ? input : input.url
    const apiPrefix = window.location.origin + '/api'
    if (!url.startsWith('/api') && !url.startsWith(apiPrefix)) return originalFetch(input, init)
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
