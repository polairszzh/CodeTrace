import { useState, useEffect } from 'react'

function FileTree({ repoUrl, onFileSelect }) {
  const [tree, setTree] = useState(null)
  const [expanded, setExpanded] = useState({})
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!repoUrl) return
    setLoading(true)
    fetch(`/api/repo/files?repo_url=${encodeURIComponent(repoUrl)}`)
      .then(r => r.json())
      .then(data => {
        setTree(Array.isArray(data) ? data : data?.entries || [])
      })
      .catch(() => setTree([]))
      .finally(() => setLoading(false))
  }, [repoUrl])

  const loadChildren = async (path) => {
    setExpanded(p => ({ ...p, [path]: !p[path] }))
    if (expanded[path]) return

    try {
      const res = await fetch(`/api/repo/files?repo_url=${encodeURIComponent(repoUrl)}&path=${encodeURIComponent(path)}`)
      const data = await res.json()
      return Array.isArray(data) ? data : data?.entries || []
    } catch { return [] }
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
        />
      ))}
    </div>
  )
}

function FileTreeNode({ entry, repoUrl, expanded, onToggle, onSelect, depth = 0 }) {
  const isDir = entry.type === 'dir' || entry.type === 'directory' || !entry.type
  const indent = depth * 14

  const handleClick = () => {
    if (isDir) {
      onToggle(entry.path || entry.name)
    } else {
      onSelect?.(entry.path || entry.name)
    }
  }

  return (
    <div>
      <div
        onClick={handleClick}
        className="flex items-center gap-1.5 py-1 px-2 rounded cursor-pointer transition-colors whitespace-nowrap"
        style={{ paddingLeft: `${8 + indent}px` }}
        onMouseEnter={e => e.currentTarget.style.background = 'var(--color-surface-alt)'}
        onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
      >
        <span>{isDir ? (expanded[entry.path || entry.name] ? '📂' : '📁') : '📄'}</span>
        <span style={{ color: 'var(--color-text)' }}>{entry.name}</span>
        {entry.commit_count && (
          <span className="text-xs ml-auto" style={{ color: 'var(--color-text-muted)' }}>
            {entry.commit_count}
          </span>
        )}
      </div>
      {isDir && expanded[entry.path || entry.name] && (
        <DirChildren
          path={entry.path || entry.name}
          repoUrl={repoUrl}
          expanded={expanded}
          onToggle={onToggle}
          onSelect={onSelect}
          depth={depth + 1}
        />
      )}
    </div>
  )
}

function DirChildren({ path, repoUrl, expanded, onToggle, onSelect, depth }) {
  const [children, setChildren] = useState(null)

  useEffect(() => {
    fetch(`/api/repo/files?repo_url=${encodeURIComponent(repoUrl)}&path=${encodeURIComponent(path)}`)
      .then(r => r.json())
      .then(data => setChildren(Array.isArray(data) ? data : data?.entries || []))
      .catch(() => setChildren([]))
  }, [path, repoUrl])

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
      depth={depth}
    />
  ))
}

export default FileTree
