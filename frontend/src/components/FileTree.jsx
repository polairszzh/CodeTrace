import { useState, useEffect } from 'react'

const RISK_COLORS = {
  high: { dot: '#e53e3e', bg: 'rgba(229,62,62,0.08)' },
  medium: { dot: '#d69e2e', bg: 'rgba(214,158,46,0.08)' },
  low: { dot: '#38a169', bg: 'rgba(56,161,105,0.08)' },
}

function FileTree({ repoUrl, onFileSelect }) {
  const [tree, setTree] = useState(null)
  const [expanded, setExpanded] = useState({})
  const [loading, setLoading] = useState(false)
  const [risks, setRisks] = useState({})

  // dirCache: { [path]: children[] | null }. null = loading
  const [dirCache, setDirCache] = useState({})

  // Reset cache when repo changes
  useEffect(() => { setDirCache({}) }, [repoUrl])

  useEffect(() => {
    if (!repoUrl) return
    setLoading(true)
    fetch(`/api/repo/files?repo_url=${encodeURIComponent(repoUrl)}`)
      .then(r => r.json())
      .then(data => setTree(Array.isArray(data) ? data : data?.entries || []))
      .catch(() => setTree([]))
      .finally(() => setLoading(false))
  }, [repoUrl])

  // Fetch risk data for coloring
  useEffect(() => {
    if (!repoUrl) { setRisks({}); return }
    fetch(`/api/repo/file-risks?repo_url=${encodeURIComponent(repoUrl)}`)
      .then(r => r.json())
      .then(data => setRisks(data.risks || {}))
      .catch(() => setRisks({}))
  }, [repoUrl])

  const loadChildren = async (path) => {
    const nextExpanded = !expanded[path]
    setExpanded(p => ({ ...p, [path]: nextExpanded }))

    // Collapsing → no fetch
    if (!nextExpanded) return

    // Already cached (loaded, not currently loading) → no fetch
    const cached = dirCache[path]
    if (cached && cached.length > 0) return
    if (cached === null) return // already loading

    // Mark as loading
    setDirCache(p => ({ ...p, [path]: null }))

    try {
      const res = await fetch(`/api/repo/files?repo_url=${encodeURIComponent(repoUrl)}&path=${encodeURIComponent(path)}`)
      const data = await res.json()
      setDirCache(p => ({ ...p, [path]: Array.isArray(data) ? data : data?.entries || [] }))
    } catch {
      setDirCache(p => ({ ...p, [path]: [] }))
    }
  }

  if (!repoUrl) {
    return (
      <div className="flex items-center justify-center h-full text-xs p-4 text-center"
        style={{ color: 'var(--color-text-muted)' }}>
        先输入仓库 URL
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-xs"
        style={{ color: 'var(--color-text-muted)' }}>
        加载中...
      </div>
    )
  }

  if (!tree || tree.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-xs p-4 text-center"
        style={{ color: 'var(--color-text-muted)' }}>
        暂无文件
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto p-2 text-xs font-mono">
      {tree.map(entry => (
        <FileTreeNode
          key={entry.path || entry.name}
          entry={entry}
          repoUrl={repoUrl}
          expanded={expanded}
          onToggle={loadChildren}
          onSelect={onFileSelect}
          risks={risks}
          dirCache={dirCache}
        />
      ))}
    </div>
  )
}

function FileTreeNode({ entry, repoUrl, expanded, onToggle, onSelect, risks, dirCache, depth = 0 }) {
  const isDir = entry.type === 'dir' || entry.type === 'directory' || !entry.type
  const indent = depth * 14
  const filePath = entry.path || entry.name

  const risk = isDir ? null : (risks[filePath] || 'low')
  const color = risk ? RISK_COLORS[risk] : null

  const handleClick = () => {
    if (isDir) {
      onToggle(filePath)
    } else {
      onSelect?.(filePath)
    }
  }

  return (
    <div>
      <div
        onClick={handleClick}
        className="flex items-center gap-1.5 py-1 px-2 rounded cursor-pointer transition-colors whitespace-nowrap"
        style={{
          paddingLeft: `${8 + indent}px`,
          background: color && !isDir ? color.bg : 'transparent',
        }}
        onMouseEnter={e => { if (!isDir) e.currentTarget.style.background = color?.bg || 'var(--color-surface-alt)' }}
        onMouseLeave={e => { if (!isDir) e.currentTarget.style.background = color?.bg || 'transparent' }}
      >
        {!isDir && risk && (
          <span
            className="inline-block rounded-full flex-shrink-0"
            style={{
              width: '6px', height: '6px',
              background: RISK_COLORS[risk]?.dot || 'transparent',
            }}
          />
        )}
        {isDir && (
          <span className="flex-shrink-0">{expanded[filePath] ? '📂' : '📁'}</span>
        )}
        <span
          className="truncate"
          style={{
            color: risk === 'high' ? '#e53e3e' : 'var(--color-text)',
            fontWeight: risk === 'high' ? 600 : 400,
          }}
        >
          {entry.name}
        </span>
        {!isDir && risk && risk !== 'low' && (
          <span
            className="text-[10px] px-1 rounded flex-shrink-0 ml-auto"
            style={{
              background: RISK_COLORS[risk]?.dot + '22',
              color: RISK_COLORS[risk]?.dot,
            }}
          >
            {risk}
          </span>
        )}
      </div>
      {isDir && expanded[filePath] && (
        <DirChildren
          path={filePath}
          repoUrl={repoUrl}
          expanded={expanded}
          onToggle={onToggle}
          onSelect={onSelect}
          risks={risks}
          depth={depth + 1}
          dirCache={dirCache}
        />
      )}
    </div>
  )
}

function DirChildren({ path, repoUrl, expanded, onToggle, onSelect, risks, depth, dirCache }) {
  const children = dirCache[path]

  if (!children) {
    return <div className="text-xs py-1" style={{ paddingLeft: `${8 + depth * 14}px`, color: 'var(--color-text-muted)' }}>加载中...</div>
  }

  return children.map(child => (
    <FileTreeNode
      key={child.path || child.name}
      entry={child}
      repoUrl={repoUrl}
      expanded={expanded}
      onToggle={onToggle}
      onSelect={onSelect}
      risks={risks}
      depth={depth}
      dirCache={dirCache}
    />
  ))
}

export default FileTree
