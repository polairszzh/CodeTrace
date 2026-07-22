import { useState, useEffect, useRef } from 'react'
import RepoInput from './RepoInput'

function TraceInput({ onSearch, onRepoChange, externalFile, externalFunction }) {
  const [repoUrl, setRepoUrl] = useState('')
  const [filePath, setFilePath] = useState('')
  const [functionName, setFunctionName] = useState('')
  const [symbolKind, setSymbolKind] = useState('') // '' | 'function' | 'class'
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // File browser state
  const [browseOpen, setBrowseOpen] = useState(false)
  const [currentDir, setCurrentDir] = useState('')
  const [entries, setEntries] = useState([])
  const [browseLoading, setBrowseLoading] = useState(false)
  const browseRef = useRef(null)

  // Symbol selector state
  const [symbols, setSymbols] = useState([])
  const [symbolOpen, setSymbolOpen] = useState(false)
  const [symLoading, setSymLoading] = useState(false)
  const symbolRef = useRef(null)

  // Externally set file path (from FileTree etc.)
  useEffect(() => {
    if (externalFile) {
      setFilePath(externalFile)
      loadSymbols(externalFile)
    }
  }, [externalFile])
  useEffect(() => { if (externalFunction) { setFunctionName(externalFunction); setSymbolKind('') } }, [externalFunction])

  // Notify parent of repo URL changes
  useEffect(() => { onRepoChange?.(repoUrl) }, [repoUrl])

  // Click-outside to close browsers
  useEffect(() => {
    const handler = (e) => {
      if (browseRef.current && !browseRef.current.contains(e.target)) setBrowseOpen(false)
      if (symbolRef.current && !symbolRef.current.contains(e.target)) setSymbolOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // ── File browser ──
  const openFileBrowser = () => {
    if (!repoUrl) return
    setBrowseOpen(true)
    loadDir('')
  }

  const loadDir = async (dir) => {
    setBrowseLoading(true)
    setCurrentDir(dir)
    try {
      const url = dir
        ? `/api/repo/files?repo_url=${encodeURIComponent(repoUrl)}&path=${encodeURIComponent(dir)}`
        : `/api/repo/files?repo_url=${encodeURIComponent(repoUrl)}`
      const res = await fetch(url)
      const data = await res.json()
      const list = data.entries || data || []
      setEntries(list)
    } catch {
      setEntries([])
    } finally {
      setBrowseLoading(false)
    }
  }

  const selectFile = (path) => {
    setFilePath(path)
    setBrowseOpen(false)
    loadSymbols(path)
  }

  // ── Symbol loader ──
  const loadSymbols = async (fp) => {
    if (!repoUrl || !fp) { setSymbols([]); return }
    setSymLoading(true)
    try {
      const res = await fetch(`/api/repo/symbols?repo_url=${encodeURIComponent(repoUrl)}&file_path=${encodeURIComponent(fp)}`)
      const data = await res.json()
      const list = []
      for (const c of (data.classes || [])) {
        list.push({ ...c, kind: 'class', label: `${c.name} (class)` })
      }
      for (const f of (data.functions || [])) {
        list.push({ ...f, kind: 'function', label: `${f.name} (L${f.start_line})` })
      }
      setSymbols(list)
    } catch {
      setSymbols([])
    } finally {
      setSymLoading(false)
    }
  }

  // ── Search ──
  const handleSearch = async () => {
    if (!repoUrl || !filePath) return
    setLoading(true)
    setError('')
    try {
      let url
      if (!functionName) {
        url = '/api/trace'
      } else if (symbolKind === 'class') {
        url = `/api/trace/class?class_name=${encodeURIComponent(functionName)}`
      } else {
        url = `/api/trace/function?function_name=${encodeURIComponent(functionName)}`
      }
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
  }

  // Breadcrumb path parts
  const dirParts = currentDir ? currentDir.split('/') : []

  return (
    <div className="p-4" style={{ borderBottom: '1px solid var(--color-border)' }}>
      {/* Row 1: Repo URL */}
      <div className="flex items-center gap-2 mb-2">
        <RepoInput value={repoUrl} onChange={setRepoUrl} onReady={handleRepoReady} autoFocus />
      </div>

      {/* Row 2: File selector + Symbol selector + Button */}
      <div className="flex items-center gap-2">
        {/* ── File selector ── */}
        <div ref={browseRef} className="relative" style={{ flex: 2 }}>
          <div
            onClick={openFileBrowser}
            className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm cursor-pointer transition-colors font-mono"
            style={{
              background: 'var(--color-surface-alt)',
              border: '1px solid var(--color-border)',
              color: filePath ? 'var(--color-text-heading)' : '#9ca3af',
              minHeight: '36px',
            }}
          >
            {filePath ? (
              <>
                <span style={{ color: 'var(--color-accent)' }}>📄</span>
                <span>{filePath}</span>
                {functionName && (
                  <span className="ml-auto text-xs" style={{ color: 'var(--color-text-muted)' }}>
                    {functionName}
                  </span>
                )}
              </>
            ) : (
              <span>选择文件...</span>
            )}
          </div>

          {/* File browser dropdown */}
          {browseOpen && (
            <div
              className="absolute top-full left-0 right-0 mt-1 rounded-lg shadow-lg z-20 overflow-hidden"
              style={{
                background: 'var(--color-surface-card)',
                border: '1px solid var(--color-border)',
                maxHeight: '320px',
                display: 'flex',
                flexDirection: 'column',
              }}
            >
              {/* Breadcrumb */}
              <div className="flex items-center gap-1 px-3 py-2 text-xs font-mono"
                style={{ borderBottom: '1px solid var(--color-border)', color: 'var(--color-text-muted)' }}>
                <span
                  onClick={() => loadDir('')}
                  className="cursor-pointer hover:underline"
                  style={{ color: currentDir ? 'var(--color-accent)' : 'var(--color-text-heading)' }}
                >
                  /
                </span>
                {dirParts.map((part, i) => {
                  const path = dirParts.slice(0, i + 1).join('/')
                  const isLast = i === dirParts.length - 1
                  return (
                    <span key={path} className="flex items-center gap-1">
                      <span style={{ color: 'var(--color-text-muted)' }}>/</span>
                      <span
                        onClick={() => !isLast && loadDir(path)}
                        className={isLast ? '' : 'cursor-pointer hover:underline'}
                        style={{ color: isLast ? 'var(--color-text-heading)' : 'var(--color-accent)' }}
                      >
                        {part}
                      </span>
                    </span>
                  )
                })}
              </div>

              {/* Entry list */}
              <div className="overflow-y-auto flex-1 py-1">
                {browseLoading ? (
                  <div className="px-3 py-4 text-xs text-center" style={{ color: 'var(--color-text-muted)' }}>
                    加载中...
                  </div>
                ) : entries.length === 0 ? (
                  <div className="px-3 py-4 text-xs text-center" style={{ color: 'var(--color-text-muted)' }}>
                    无文件
                  </div>
                ) : (
                  <>
                    {currentDir && (
                      <button
                        onClick={() => loadDir(dirParts.slice(0, -1).join('/'))}
                        className="w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors"
                        style={{ color: 'var(--color-text)', background: 'transparent', border: 'none', cursor: 'pointer' }}
                        onMouseEnter={e => e.currentTarget.style.background = 'var(--color-surface-alt)'}
                        onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                      >
                        <span>📁</span>
                        <span>..</span>
                      </button>
                    )}
                    {entries.map(e => (
                      <button
                        key={e.path || e.name}
                        onClick={() => e.type === 'dir' ? loadDir(e.path) : selectFile(e.path)}
                        className="w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors font-mono"
                        style={{
                          color: 'var(--color-text)',
                          background: (e.path || e.name) === filePath ? 'var(--color-surface-alt)' : 'transparent',
                          border: 'none', cursor: 'pointer',
                        }}
                        onMouseEnter={e => e.currentTarget.style.background = 'var(--color-surface-alt)'}
                        onMouseLeave={e => { if ((e.path || e.name) !== filePath) e.currentTarget.style.background = 'transparent' }}
                      >
                        <span>{e.type === 'dir' ? '📁' : '📄'}</span>
                        <span>{e.name}</span>
                      </button>
                    ))}
                  </>
                )}
              </div>
            </div>
          )}
        </div>

        {/* ── Symbol selector ── */}
        <div ref={symbolRef} className="relative" style={{ flex: 1.5 }}>
          <div
            onClick={() => filePath && setSymbolOpen(o => !o)}
            className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm cursor-pointer transition-colors font-mono"
            style={{
              background: 'var(--color-surface-alt)',
              border: '1px solid var(--color-border)',
              color: functionName ? 'var(--color-text-heading)' : '#9ca3af',
              minHeight: '36px',
              cursor: filePath ? 'pointer' : 'not-allowed',
              opacity: filePath ? 1 : 0.5,
            }}
          >
            {symLoading ? (
              <span style={{ color: 'var(--color-text-muted)' }}>加载符号...</span>
            ) : functionName ? (
              <>
                <span style={{ color: 'var(--color-accent)' }}>ƒ</span>
                <span>{functionName}</span>
              </>
            ) : (
              <span>函数/类名（可选）</span>
            )}
          </div>

          {/* Symbol dropdown */}
          {symbolOpen && (
            <div
              className="absolute top-full left-0 right-0 mt-1 rounded-lg shadow-lg z-20 py-1"
              style={{
                background: 'var(--color-surface-card)',
                border: '1px solid var(--color-border)',
                maxHeight: '250px',
                overflowY: 'auto',
              }}
            >
              {/* Clear selection */}
              <button
                onClick={() => { setFunctionName(''); setSymbolKind(''); setSymbolOpen(false) }}
                className="w-full text-left px-3 py-1.5 text-xs transition-colors"
                style={{ color: 'var(--color-text-muted)', background: 'transparent', border: 'none', cursor: 'pointer' }}
                onMouseEnter={e => e.currentTarget.style.background = 'var(--color-surface-alt)'}
                onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
              >
                不指定函数（文件级追溯）
              </button>

              {symbols.length === 0 && !symLoading ? (
                <div className="px-3 py-2 text-xs" style={{ color: 'var(--color-text-muted)' }}>
                  未检测到符号
                </div>
              ) : (
                symbols.map(s => (
                  <button
                    key={s.name}
                    onClick={() => { setFunctionName(s.name); setSymbolKind(s.kind); setSymbolOpen(false) }}
                    className="w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors font-mono"
                    style={{
                      color: 'var(--color-text)', background: 'transparent', border: 'none', cursor: 'pointer',
                    }}
                    onMouseEnter={e => e.currentTarget.style.background = 'var(--color-surface-alt)'}
                    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                  >
                    <span style={{ color: s.kind === 'class' ? '#c084fc' : '#58a6ff' }}>
                      {s.kind === 'class' ? '◈' : 'ƒ'}
                    </span>
                    <span>{s.name}</span>
                    {s.start_line && (
                      <span className="ml-auto" style={{ color: 'var(--color-text-muted)' }}>
                        L{s.start_line}
                      </span>
                    )}
                    {s.methods && s.methods.length > 0 && (
                      <span className="text-xs" style={{ color: 'var(--color-text-muted)' }}>
                        ({s.methods.length} methods)
                      </span>
                    )}
                  </button>
                ))
              )}
            </div>
          )}
        </div>

        {/* ── Search button ── */}
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
