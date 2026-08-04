import { useState, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

function repoFull(repoUrl) {
  const m = (repoUrl || '').replace(/\.git$/, '').match(/github\.com[/:]([^/]+)\/([^/?#]+)/)
  return m ? `${m[1]}/${m[2]}` : ''
}

function TrackingCard({ repoUrl }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const timerRef = useRef(null)
  const abortRef = useRef(null)
  const repoRef = useRef(repoUrl)
  const full = repoFull(repoUrl)

  const load = (showLoading = true) => {
    if (!repoUrl) return
    const url = repoUrl
    if (timerRef.current) clearTimeout(timerRef.current)
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    if (showLoading) setLoading(true)
    setError('')
    fetch(`/api/repo/tracking?repo_url=${encodeURIComponent(url)}`, { signal: controller.signal })
      .then(r => r.ok ? r.json() : r.json().then(d => Promise.reject(new Error(d?.detail || `请求失败 (HTTP ${r.status})`))))
      .then(d => {
        if (repoRef.current !== url) return
        setData(d)
        setLoading(false)
        // 后台仍在生成 → 自调度下一次轮询（不依赖 effect 依赖值，避免只触发一次）
        if (d.status === 'refreshing') {
          timerRef.current = setTimeout(() => load(false), 3000)
        } else if (d.status === 'error' && d.retry_after > 0) {
          // 退避中：按后端给出的剩余秒数调度一次自动重试
          timerRef.current = setTimeout(() => load(false), Math.min(d.retry_after, 300) * 1000)
        }
      })
      .catch(e => {
        if (e.name === 'AbortError') return
        if (repoRef.current === url) { setError(e.message || String(e)); setLoading(false) }
      })
  }

  useEffect(() => {
    repoRef.current = repoUrl
    if (repoUrl) load()
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
      abortRef.current?.abort()
    }
  }, [repoUrl])

  if (!repoUrl) return null

  const report = data?.latest_report
  const newPrs = report?.structured?.new_prs || []
  const snapshot = data?.snapshots?.length > 0 ? data.snapshots[data.snapshots.length - 1] : null

  return (
    <div className="rounded-xl p-5 flex flex-col gap-3"
      style={{ background: 'var(--color-surface-card)', border: '1px solid var(--color-border)' }}>
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold m-0 flex-1" style={{ color: 'var(--color-text-heading)' }}>
          持续追踪
        </h3>
        {data?.status === 'refreshing' && (
          <span className="text-xs flex items-center gap-1" style={{ color: 'var(--color-text-muted)' }}>
            <span className="w-3 h-3 border-2 rounded-full inline-block animate-spin"
              style={{ borderColor: 'var(--color-border)', borderTopColor: 'var(--color-accent)' }} />
            正在生成增量报告…
          </span>
        )}
        <button onClick={() => load()}
          className="px-2 py-1 rounded text-xs font-medium"
          style={{ background: 'var(--color-surface-alt)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}>
          刷新
        </button>
      </div>

      {loading && !data && (
        <div className="text-xs" style={{ color: 'var(--color-text-muted)' }}>加载中...</div>
      )}
      {error && (
        <div className="px-3 py-1.5 rounded-lg text-xs"
          style={{ background: '#fff5f5', color: '#e53e3e', border: '1px solid #fed7d7' }}>
          {error}
        </div>
      )}

      {data && !loading && (
        <>
          <div className="text-xs flex flex-wrap gap-x-4 gap-y-1" style={{ color: 'var(--color-text-muted)' }}>
            {snapshot && (
              <span>快照：{snapshot.created_at?.slice(0, 16).replace('T', ' ')} · {snapshot.head?.slice(0, 7)}</span>
            )}
            {report && <span>生成方式：{report.generated_by === 'llm' ? 'AI 分析' : report.generated_by === 'baseline' ? '基线' : '结构化摘要'}</span>}
            {data?.head && <span>当前 HEAD：{data.head.slice(0, 7)}</span>}
          </div>

          {data?.status === 'error' ? (
            <div className="text-xs" style={{ color: '#e53e3e' }}>{data.message || '追踪不可用'}</div>
          ) : report ? (
            <div className="text-sm leading-relaxed markdown-report">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{report.markdown || ''}</ReactMarkdown>
            </div>
          ) : (
            <div className="text-xs" style={{ color: 'var(--color-text-muted)' }}>
              暂无报告，正在生成基线…
            </div>
          )}

          {newPrs.length > 0 && (
            <div className="flex flex-col gap-1">
              <div className="text-xs font-medium" style={{ color: 'var(--color-text-muted)' }}>本次合入</div>
              {newPrs.map(p => (
                <div key={p.hash} className="flex items-center gap-2 text-xs">
                  {full && p.pr_number ? (
                    <a className="no-underline font-medium" style={{ color: '#c084fc' }}
                      href={`https://github.com/${full}/pull/${p.pr_number}`} target="_blank" rel="noreferrer">
                      PR #{p.pr_number}
                    </a>
                  ) : (
                    <span className="font-medium" style={{ color: '#c084fc' }}>#{p.pr_number}</span>
                  )}
                  <span className="truncate" style={{ color: 'var(--color-text)' }}>{p.subject}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default TrackingCard
