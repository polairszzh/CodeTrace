import { useState, useEffect } from 'react'
import RepoInput from './RepoInput'

function TraceInput({ onSearch, onRepoChange }) {
  const [repoUrl, setRepoUrl] = useState('')
  const [filePath, setFilePath] = useState('')
  const [functionName, setFunctionName] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // repo URL 变化时通知父组件（用于文件树等）
  useEffect(() => { onRepoChange?.(repoUrl) }, [repoUrl])

  const handleSearch = async () => {
    if (!repoUrl || !filePath) return
    setLoading(true)
    setError('')
    try {
      const url = functionName
        ? `/api/trace/function?function_name=${encodeURIComponent(functionName)}`
        : '/api/trace'
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_url: repoUrl, file_path: filePath }),
      })
      if (!res.ok) throw new Error('请求失败')
      const data = await res.json()
      onSearch(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleRepoReady = (url) => {
    setRepoUrl(url)
    // repo 就绪后可自动触发文件列表加载（留给后面级联扩展）
  }

  return (
    <div className="p-4" style={{ borderBottom: '1px solid var(--color-border)' }}>
      <div className="flex items-center gap-2">
        <RepoInput value={repoUrl} onChange={setRepoUrl} onReady={handleRepoReady} autoFocus />
        <input
          placeholder="文件路径"
          value={filePath}
          onChange={e => setFilePath(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSearch()}
          className="flex-1 px-3 py-2 rounded-lg text-sm outline-none transition-colors font-mono"
          style={{
            background: 'var(--color-surface-alt)',
            border: '1px solid var(--color-border)',
            color: 'var(--color-text-heading)',
          }}
        />
        <input
          placeholder="函数/类名（可选）"
          value={functionName}
          onChange={e => setFunctionName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSearch()}
          className="flex-1 px-3 py-2 rounded-lg text-sm outline-none transition-colors font-mono"
          style={{
            background: 'var(--color-surface-alt)',
            border: '1px solid var(--color-border)',
            color: 'var(--color-text-heading)',
          }}
        />
        <button
          onClick={handleSearch}
          disabled={loading || !repoUrl || !filePath}
          className="px-4 py-2 rounded-lg text-sm font-medium transition-opacity whitespace-nowrap"
          style={{
            background: loading ? 'var(--color-text-muted)' : 'var(--color-accent)',
            color: '#fff',
            border: 'none',
            cursor: loading || !repoUrl || !filePath ? 'not-allowed' : 'pointer',
            opacity: loading ? 0.6 : 1,
          }}
        >
          {loading ? '分析中...' : '分析'}
        </button>
      </div>
      {error && (
        <div className="mt-2 px-3 py-1.5 rounded-lg text-sm"
          style={{ background: '#fff5f5', color: '#e53e3e', border: '1px solid #fed7d7' }}>
          {error}
        </div>
      )}
    </div>
  )
}

export default TraceInput
