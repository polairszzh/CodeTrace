import { useState, useEffect } from 'react'

const PALETTE = [
  '#c084fc', '#3182ce', '#38a169', '#e53e3e', '#d69e2e',
  '#805ad5', '#dd6b20', '#319795', '#d53f8c', '#2b6cb0',
]

function repoFull(repoUrl) {
  const m = (repoUrl || '').replace(/\.git$/, '').match(/github\.com[/:]([^/]+)\/([^/?#]+)/)
  return m ? `${m[1]}/${m[2]}` : ''
}

function buildLayout(data) {
  const { branches = [], graph = {} } = data
  const nodes = graph.nodes || []
  const edges = graph.edges || []
  const nodeById = {}
  nodes.forEach(n => { nodeById[n.id] = n })

  const parentsOf = {}
  edges.forEach(e => {
    ;(parentsOf[e.source] = parentsOf[e.source] || []).push(e.target)
  })

  const orderedBranches = [...branches].sort((a, b) => {
    if (a.is_default !== b.is_default) return a.is_default ? -1 : 1
    return (b.head_date || '').localeCompare(a.head_date || '')
  })

  // 泳道：从每个分支头沿 first-parent 链下钻，先到先占（默认分支优先）
  const laneOf = {}
  orderedBranches.forEach((b, i) => {
    let cur = nodeById[b.head]
    while (cur && laneOf[cur.id] === undefined) {
      laneOf[cur.id] = i
      const parents = parentsOf[cur.id] || []
      cur = parents.length ? nodeById[parents[0]] : null
    }
  })
  nodes.forEach(n => { if (laneOf[n.id] === undefined) laneOf[n.id] = 0 })

  const rows = nodes.map(n => ({ ...n, lane: laneOf[n.id] }))
  return { rows, orderedBranches }
}

function GitGraph({ repoUrl, onAskAgent }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!repoUrl) { setData(null); return }
    setLoading(true)
    setError('')
    fetch(`/api/repo/git-graph?repo_url=${encodeURIComponent(repoUrl)}`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })
      .then(d => { setData(d); setLoading(false) })
      .catch(e => { setError(e.message); setLoading(false) })
  }, [repoUrl])

  if (!repoUrl) return null

  return (
    <div className="rounded-xl p-5 flex flex-col gap-4"
      style={{ background: 'var(--color-surface-card)', border: '1px solid var(--color-border)' }}>
      <h3 className="text-sm font-semibold m-0" style={{ color: 'var(--color-text-heading)' }}>
        分支拓扑与合入关系
      </h3>

      {loading && (
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
          <BranchSummary data={data} />
          <CommitTimeline data={data} repoUrl={repoUrl} onAskAgent={onAskAgent} />
        </>
      )}
    </div>
  )
}

function BranchSummary({ data }) {
  const branches = data.branches || []
  if (branches.length === 0) {
    return <div className="text-xs" style={{ color: 'var(--color-text-muted)' }}>暂无分支数据</div>
  }
  return (
    <div className="flex flex-wrap gap-2">
      {branches.map(b => (
        <span key={b.name} className="flex items-center gap-2 px-2.5 py-1 rounded-lg text-xs"
          style={{
            background: b.is_default ? 'rgba(192,132,252,0.15)' : 'var(--color-surface-alt)',
            border: b.is_default ? '1px solid #c084fc' : '1px solid var(--color-border)',
          }}>
          <span className="font-medium" style={{ color: b.is_default ? '#c084fc' : 'var(--color-text)' }}>
            {b.name}
          </span>
          {b.is_default
            ? <span style={{ color: 'var(--color-text-muted)' }}>默认</span>
            : <span className="font-mono" style={{ color: 'var(--color-text-muted)' }}>+{b.ahead} / -{b.behind}</span>}
          <span className="font-mono" style={{ color: 'var(--color-text-muted)' }}>{b.total_commits}</span>
        </span>
      ))}
    </div>
  )
}

function CommitTimeline({ data, repoUrl, onAskAgent }) {
  const { rows, orderedBranches } = buildLayout(data)
  const [expanded, setExpanded] = useState(null)
  const [prInfos, setPrInfos] = useState({})
  const full = repoFull(repoUrl)

  if (rows.length === 0) {
    return <div className="text-xs" style={{ color: 'var(--color-text-muted)' }}>暂无提交数据</div>
  }

  const colorOf = i => PALETTE[i % PALETTE.length]

  const toggleRow = (n) => {
    const next = expanded === n.id ? null : n.id
    setExpanded(next)
    // 展开且带 PR 号时，应用内拉取 PR 详情（一次性，结果缓存）
    if (next && n.pr_number && prInfos[n.id] === undefined) {
      setPrInfos(p => ({ ...p, [n.id]: { loading: true } }))
      fetch(`/api/repo/pr-info?repo_url=${encodeURIComponent(repoUrl)}&pr_number=${n.pr_number}`)
        .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
        .then(info => setPrInfos(p => ({ ...p, [n.id]: { loading: false, info } })))
        .catch(e => setPrInfos(p => ({ ...p, [n.id]: { loading: false, error: e.message } })))
    }
  }

  return (
    <div className="rounded-lg overflow-auto" style={{ maxHeight: 480, border: '1px solid var(--color-border)' }}>
      {rows.map((n, i) => {
        const color = colorOf(n.lane)
        const isOpen = expanded === n.id
        const prLink = n.pr_number
          ? `https://github.com/${full}/pull/${n.pr_number}`
          : null
        const commitLink = full ? `https://github.com/${full}/commit/${n.id}` : null
        return (
          <div key={n.id}
            className="cursor-pointer select-none"
            style={{ borderLeft: `3px solid ${color}` }}
            onClick={() => toggleRow(n)}>
            <div className="flex items-start gap-3 px-3 py-2 border-b"
              style={{ borderColor: 'var(--color-border)', borderBottomWidth: i < rows.length - 1 ? '1px' : '0' }}>
              {/* 节点标记 */}
              <span className="flex-shrink-0 mt-0.5" style={{ color }}>
                {n.is_merge ? '◆' : '●'}
              </span>
              {/* 内容 */}
              <div className="flex-1 min-w-0 flex flex-col gap-0.5">
                <div className="flex items-center gap-2 text-xs flex-wrap">
                  <span className="font-mono" style={{ color: 'var(--color-text-muted)' }}>{n.short}</span>
                  <span style={{ color: 'var(--color-text-muted)' }}>{n.date?.slice(0, 10)}</span>
                  <span className="font-medium" style={{ color: 'var(--color-text-muted)' }}>{n.author}</span>
                  {n.refs.map(r => (
                    <span key={r} className="px-1 py-px rounded text-[10px] font-medium" style={{ background: color, color: '#fff' }}>
                      {r}
                    </span>
                  ))}
                  {n.is_merge && (
                    <span className="text-[10px]" style={{ color: 'var(--color-text-muted)' }}>合并提交</span>
                  )}
                  {isOpen
                    ? <span className="ml-auto" style={{ color: 'var(--color-text-muted)' }}>收起 ▲</span>
                    : <span className="ml-auto" style={{ color: 'var(--color-text-muted)' }}>展开 ▼</span>}
                </div>
                <div className="text-xs break-all" style={{ color: 'var(--color-text)' }}>{n.message}</div>
              </div>
            </div>

            {isOpen && (
              <div className="px-3 py-2 border-b text-xs flex flex-col gap-1.5"
                style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface-alt)' }}>
                {/* PR 详情（应用内） */}
                {n.pr_number && (
                  <div className="rounded-lg p-2.5 flex flex-col gap-1"
                    style={{ background: 'var(--color-surface-card)', border: '1px solid var(--color-border)' }}>
                    {prInfos[n.id]?.loading && (
                      <span style={{ color: 'var(--color-text-muted)' }}>加载 PR #{n.pr_number} 信息...</span>
                    )}
                    {prInfos[n.id]?.error && (
                      <span style={{ color: '#e53e3e' }}>PR 信息获取失败：{prInfos[n.id].error}</span>
                    )}
                    {prInfos[n.id]?.info && (
                      <>
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-semibold" style={{ color: 'var(--color-text-heading)' }}>
                            PR #{n.pr_number}：{prInfos[n.id].info.title}
                          </span>
                          <span className="px-1.5 py-px rounded text-[10px] font-medium"
                            style={{
                              background: prInfos[n.id].info.state === 'open' ? '#38a169' : '#718096',
                              color: '#fff',
                            }}>
                            {prInfos[n.id].info.state}
                          </span>
                        </div>
                        <span style={{ color: 'var(--color-text-muted)' }}>
                          {prInfos[n.id].info.author} · {prInfos[n.id].info.created_at?.slice(0, 10)}
                        </span>
                        {prInfos[n.id].info.body && (
                          <div className="line-clamp-3 whitespace-pre-wrap" style={{ color: 'var(--color-text)' }}>
                            {prInfos[n.id].info.body}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )}

                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  <span className="font-mono" style={{ color: 'var(--color-text-muted)' }}>hash: {n.id}</span>
                  {n.is_merge && n.parents && (
                    <span className="font-mono" style={{ color: 'var(--color-text-muted)' }}>
                      parents: {n.parents.map(p => p.slice(0, 7)).join(', ')}
                    </span>
                  )}
                </div>
                <div className="flex gap-2">
                  {onAskAgent && (
                    <button
                      className="px-2 py-1 rounded text-xs font-medium border-none cursor-pointer"
                      style={{ background: 'var(--color-accent)', color: '#fff' }}
                      onClick={e => {
                        e.stopPropagation()
                        onAskAgent({ repoUrl, commitHash: n.id, type: 'commit' })
                      }}>
                      问 Agent
                    </button>
                  )}
                  {prLink && (
                    <a className="px-2 py-1 rounded text-xs no-underline"
                      style={{ background: 'var(--color-surface-card)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
                      href={prLink} target="_blank" rel="noreferrer"
                      onClick={e => e.stopPropagation()}>
                      GitHub PR #{n.pr_number}
                    </a>
                  )}
                  {commitLink && (
                    <a className="px-2 py-1 rounded text-xs no-underline"
                      style={{ background: 'var(--color-surface-card)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
                      href={commitLink} target="_blank" rel="noreferrer"
                      onClick={e => e.stopPropagation()}>
                      GitHub commit
                    </a>
                  )}
                </div>
              </div>
            )}
          </div>
        )
      })}
      <div className="px-3 py-1.5 text-[10px]" style={{ color: 'var(--color-text-muted)' }}>
        共 {rows.length} 条 · 点击行可展开详情 · {orderedBranches.length} 个本地分支
      </div>
    </div>
  )
}

export default GitGraph
