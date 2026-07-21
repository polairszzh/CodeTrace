import { useState, useEffect, useRef } from 'react'

const STORAGE_KEY = 'codetrace_recent_repos'
const MAX_RECENT = 5

function getRecentRepos() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
  } catch { return [] }
}

function addRecentRepo(url) {
  const list = getRecentRepos().filter(r => r !== url)
  list.unshift(url)
  if (list.length > MAX_RECENT) list.pop()
  localStorage.setItem(STORAGE_KEY, JSON.stringify(list))
}

function RepoInput({ value, onChange, onReady, autoFocus }) {
  const [recentRepos] = useState(getRecentRepos)
  const [showDropdown, setShowDropdown] = useState(false)
  const ref = useRef(null)

  // 点击外部关闭下拉
  useEffect(() => {
    function handleClick(e) {
      if (ref.current && !ref.current.contains(e.target)) setShowDropdown(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  const handleConfirm = () => {
    const url = value.trim()
    if (!url) return
    addRecentRepo(url)
    setShowDropdown(false)
    onReady?.(url)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleConfirm()
  }

  return (
    <div ref={ref} className="relative" style={{ flex: 2 }}>
      <input
        autoFocus={autoFocus}
        placeholder="GitHub 仓库 URL"
        value={value}
        onChange={e => onChange(e.target.value)}
        onFocus={() => recentRepos.length > 0 && setShowDropdown(true)}
        onBlur={() => setTimeout(() => {
          if (value.trim()) handleConfirm()
        }, 200)}
        onKeyDown={handleKeyDown}
        className="w-full px-3 py-2 rounded-lg text-sm outline-none transition-colors font-mono"
        style={{
          background: 'var(--color-surface-alt)',
          border: '1px solid var(--color-border)',
          color: 'var(--color-text-heading)',
        }}
      />

      {/* 最近 repo 下拉 */}
      {showDropdown && recentRepos.length > 0 && (
        <div className="absolute top-full left-0 right-0 mt-1 rounded-lg shadow-lg z-10 py-1"
          style={{
            background: 'var(--color-surface-card)',
            border: '1px solid var(--color-border)',
          }}>
          <div className="px-3 py-1 text-xs" style={{ color: 'var(--color-text-muted)' }}>最近使用</div>
          {recentRepos.map(r => (
            <button
              key={r}
              className="w-full text-left px-3 py-1.5 text-sm transition-colors font-mono"
              style={{ color: 'var(--color-text)', background: 'transparent', border: 'none', cursor: 'pointer', fontSize: '12px' }}
              onMouseEnter={e => e.currentTarget.style.background = 'var(--color-surface-alt)'}
              onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
              onClick={() => {
                onChange(r)
                setShowDropdown(false)
                setTimeout(() => onReady?.(r), 0)
              }}
            >
              {r}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default RepoInput
