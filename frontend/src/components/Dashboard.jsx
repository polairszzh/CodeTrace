import { useState, useEffect } from 'react'
import RepoInput from './RepoInput'
import GitGraph from './GitGraph'

function Dashboard({ onAskAgent }) {
  const [repoUrl, setRepoUrl] = useState('')
  const [draft, setDraft] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const confirmRepo = (url) => {
    const u = (url || draft || '').trim()
    if (!u) return
    setDraft(u)
    setRepoUrl(u)
  }

  useEffect(() => {
    if (!repoUrl) { setData(null); return }
    setLoading(true)
    setError('')
    fetch(`/api/repo/dashboard?repo_url=${encodeURIComponent(repoUrl)}`)
      .then(r => r.ok ? r.json() : r.json().then(d => Promise.reject(new Error(d?.detail || `请求失败 (HTTP ${r.status})`))))
      .then(d => { setData(d); setLoading(false) })
      .catch(e => { setError(e.message || String(e)); setLoading(false) })
  }, [repoUrl])

  if (!repoUrl) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-4 p-8">
        <h2 className="text-lg font-semibold" style={{ color: 'var(--color-text-heading)' }}>项目仪表盘</h2>
        <p className="text-sm" style={{ color: 'var(--color-text-muted)' }}>输入仓库 URL 查看项目全景</p>
        <div className="flex gap-2" style={{ width: '440px' }}>
          <RepoInput value={draft} onChange={setDraft} onReady={confirmRepo} confirmOnBlur={false} />
          <button onClick={() => confirmRepo(draft)}
            className="px-3 py-2 rounded-lg text-sm font-medium flex-shrink-0"
            style={{ background: 'var(--color-accent)', color: '#fff', border: 'none' }}>
            确认
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col p-6 gap-5 overflow-y-auto">
      {/* Header */}
      <div className="flex items-center gap-2">
        <RepoInput value={draft} onChange={setDraft} onReady={confirmRepo} confirmOnBlur={false} />
        <button onClick={() => confirmRepo(draft)}
          className="px-3 py-2 rounded-lg text-sm font-medium flex-shrink-0"
          style={{ background: 'var(--color-accent)', color: '#fff', border: 'none' }}>
          确认
        </button>
      </div>

      {loading && (
        <div className="flex-1 flex items-center justify-center text-sm" style={{ color: 'var(--color-text-muted)' }}>
          加载中...
        </div>
      )}

      {error && (
        <div className="px-3 py-1.5 rounded-lg text-sm" style={{ background: '#fff5f5', color: '#e53e3e', border: '1px solid #fed7d7' }}>
          {error}
        </div>
      )}

      {data && !loading && (
        <div className="flex flex-col gap-5">
          {/* ── Summary cards ── */}
          <div className="grid grid-cols-3 gap-4">
            <SummaryCard label="总提交数" value={data.summary?.total_commits ?? 0} color="#c084fc" />
            <SummaryCard label="文件数" value={data.summary?.total_files ?? 0} color="#58a6ff" />
            <SummaryCard label="贡献者" value={data.summary?.total_authors ?? 0} color="#38a169" />
          </div>

          {/* ── Risk distribution + Top files ── */}
          <div className="grid grid-cols-2 gap-4">
            {/* Risk distribution */}
            <div className="rounded-xl p-5" style={{ background: 'var(--color-surface-card)', border: '1px solid var(--color-border)' }}>
              <h3 className="text-sm font-semibold mb-4" style={{ color: 'var(--color-text-heading)' }}>文件风险分布</h3>
              <div className="flex flex-col gap-3">
                <RiskBar label="高风险" count={data.risk_distribution?.high ?? 0} color="#e53e3e" />
                <RiskBar label="中风险" count={data.risk_distribution?.medium ?? 0} color="#d69e2e" />
                <RiskBar label="低风险" count={data.risk_distribution?.low ?? 0} color="#38a169" />
              </div>
            </div>

            {/* Top changed files */}
            <div className="rounded-xl p-5" style={{ background: 'var(--color-surface-card)', border: '1px solid var(--color-border)' }}>
              <h3 className="text-sm font-semibold mb-4" style={{ color: 'var(--color-text-heading)' }}>变更最频繁的文件</h3>
              {(data.summary?.top_files || []).length === 0 ? (
                <div className="text-xs" style={{ color: 'var(--color-text-muted)' }}>暂无数据</div>
              ) : (
                <div className="flex flex-col gap-1">
                  {data.summary.top_files.map((f, i) => (
                    <div key={f} className="flex items-center gap-2 py-1 text-xs font-mono truncate"
                      style={{ color: i < 3 ? '#e53e3e' : 'var(--color-text)' }}>
                      <span className="w-4 text-right flex-shrink-0" style={{ color: 'var(--color-text-muted)' }}>{i + 1}</span>
                      <span className="truncate">{f}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* ── Recent commits ── */}
          <div className="rounded-xl p-5" style={{ background: 'var(--color-surface-card)', border: '1px solid var(--color-border)' }}>
            <h3 className="text-sm font-semibold mb-3" style={{ color: 'var(--color-text-heading)' }}>近期提交</h3>
            <div className="flex flex-col gap-0.5">
              {(data.summary?.recent_commits || []).map((c, i) => (
                <div key={i} className="flex items-center gap-3 py-1.5 text-xs border-b"
                  style={{ borderColor: 'var(--color-border)', borderBottomWidth: i < (data.summary?.recent_commits || []).length - 1 ? '1px' : '0' }}>
                  <span className="font-mono flex-shrink-0" style={{ color: 'var(--color-text-muted)', width: '10ch' }}>
                    {c.date}
                  </span>
                  <span className="font-medium flex-shrink-0" style={{ color: '#c084fc', maxWidth: '15ch', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {c.author}
                  </span>
                  <span className="truncate" style={{ color: 'var(--color-text)' }}>
                    {c.message}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* ── Git Graph：分支拓扑 + 合入关系 ── */}
          <GitGraph repoUrl={repoUrl} onAskAgent={onAskAgent} />
        </div>
      )}
    </div>
  )
}

function SummaryCard({ label, value, color }) {
  return (
    <div className="rounded-xl p-5 flex flex-col items-start gap-1"
      style={{ background: 'var(--color-surface-card)', border: '1px solid var(--color-border)' }}>
      <span className="text-3xl font-bold" style={{ color }}>{value.toLocaleString()}</span>
      <span className="text-xs" style={{ color: 'var(--color-text-muted)' }}>{label}</span>
    </div>
  )
}

function RiskBar({ label, count, color }) {
  const total = 100
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs flex-shrink-0" style={{ color: 'var(--color-text)', width: '5ch' }}>{label}</span>
      <div className="flex-1 h-3 rounded-full" style={{ background: 'var(--color-surface-alt)', overflow: 'hidden' }}>
        <div
          className="h-full rounded-full transition-all"
          style={{
            width: `${Math.min((count / total) * 100, 100)}%`,
            background: color,
            opacity: 0.7,
          }}
        />
      </div>
      <span className="text-xs font-mono flex-shrink-0" style={{ color: 'var(--color-text-muted)', width: '4ch', textAlign: 'right' }}>
        {count}
      </span>
    </div>
  )
}

export default Dashboard
