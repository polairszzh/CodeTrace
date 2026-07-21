import { useState, useEffect } from 'react'
import RepoInput from './RepoInput'
import CouplingGraph from './CouplingGraph'
import ModuleErosion from './ModuleErosion'

function AgentPanel() {
  const [repoUrl, setRepoUrl] = useState('')
  const [goal, setGoal] = useState('')
  const [running, setRunning] = useState(false)
  const [finalReport, setFinalReport] = useState(null)
  const [couplingData, setCouplingData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const repo = params.get('repo')
    if (repo) setRepoUrl(repo)
  }, [])

  const handleAnalyze = async () => {
    if (!repoUrl) return
    setRunning(true)
    setFinalReport(null)
    setCouplingData(null)
    setError('')

    try {
      const params = goal ? `?goal=${encodeURIComponent(goal)}` : ''
      const res = await fetch(`/api/graph/analyze${params}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_url: repoUrl, file_path: '.' }),
      })
      if (!res.ok) throw new Error(`请求失败: ${res.status}`)
      const data = await res.json()

      if (data.report?.error) setError(data.report.error)
      if (data.report?.final_report) setFinalReport({ summary: data.report.final_report })
      if (data.coupling) setCouplingData(data.coupling)
    } catch (e) {
      setError(e.message)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="h-full flex flex-col p-4">
      {/* 输入栏 */}
      <div className="flex items-center gap-2 mb-4">
        <RepoInput value={repoUrl} onChange={setRepoUrl} onReady={() => {}} />
        <input
          placeholder="分析目标（可选）"
          value={goal}
          onChange={e => setGoal(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleAnalyze()}
          className="flex-1 px-3 py-2 rounded-lg text-sm outline-none transition-colors"
          style={{
            background: 'var(--color-surface-alt)',
            border: '1px solid var(--color-border)',
            color: 'var(--color-text-heading)',
          }}
        />
        <button
          onClick={handleAnalyze}
          disabled={running || !repoUrl}
          className="px-4 py-2 rounded-lg text-sm font-medium transition-opacity whitespace-nowrap"
          style={{
            background: running ? 'var(--color-text-muted)' : 'var(--color-accent)',
            color: '#fff', border: 'none',
            cursor: running || !repoUrl ? 'not-allowed' : 'pointer',
            opacity: running ? 0.6 : 1,
          }}
        >
          {running ? '分析中...' : '开始分析'}
        </button>
      </div>

      {error && (
        <div className="mb-4 px-3 py-1.5 rounded-lg text-sm"
          style={{ background: '#fff5f5', color: '#e53e3e', border: '1px solid #fed7d7' }}>
          {error}
        </div>
      )}

      {/* 左右分栏 */}
      <div className="flex-1 flex gap-4 min-h-0">
        {/* 左栏：报告 */}
        <div className="flex-1 flex flex-col min-w-0">
          {finalReport ? (
            <div className="flex-1 overflow-y-auto p-5 rounded-xl text-sm leading-relaxed"
              style={{
                background: 'var(--color-surface-card)',
                border: '1px solid var(--color-border)',
                color: 'var(--color-text)',
                whiteSpace: 'pre-wrap',
              }}>
              <h3 className="text-base font-semibold mt-0 mb-3" style={{ color: 'var(--color-text-heading)' }}>分析报告</h3>
              {finalReport.summary}
            </div>
          ) : !running && !error ? (
            <div className="flex-1 flex items-center justify-center text-sm rounded-xl"
              style={{ background: 'var(--color-surface-card)', border: '1px solid var(--color-border)', color: 'var(--color-text-muted)' }}>
              输入仓库 URL 开始分析
            </div>
          ) : running ? (
            <div className="flex-1 flex items-center justify-center text-sm rounded-xl"
              style={{ background: 'var(--color-surface-card)', border: '1px solid var(--color-border)', color: 'var(--color-text-muted)' }}>
              Agent 正在探索仓库...
            </div>
          ) : null}
        </div>

        {/* 右栏：耦合图 + 侵蚀表 */}
        <div className="flex-1 flex flex-col gap-4 min-w-0">
          {couplingData?.nodes?.length > 0 ? (
            <>
              <div className="flex-1 flex flex-col min-h-0">
                <div className="flex items-center gap-2 mb-2 text-sm">
                  <span style={{ color: 'var(--color-text-heading)' }}>共变耦合图</span>
                  <span className="text-xs" style={{ color: 'var(--color-text-muted)' }}>
                    {couplingData.total_files} 文件 · {couplingData.edges.length} 边
                    {couplingData.high_risk_count > 0 && ` · ${couplingData.high_risk_count} 高风险`}
                  </span>
                </div>
                <div className="flex-1 rounded-xl overflow-hidden"
                  style={{ border: '1px solid var(--color-border)' }}>
                  <CouplingGraph nodes={couplingData.nodes} edges={couplingData.edges} />
                </div>
              </div>
              <div>
                <div className="mb-2 text-sm" style={{ color: 'var(--color-text-heading)' }}>模块侵蚀风险</div>
                <ModuleErosion nodes={couplingData.nodes} />
              </div>
            </>
          ) : couplingData ? (
            <div className="flex-1 flex items-center justify-center text-sm rounded-xl"
              style={{ border: '1px solid var(--color-border)', color: 'var(--color-text-muted)' }}>
              {couplingData.note || '暂无耦合数据'}
            </div>
          ) : running ? (
            <div className="flex-1 flex items-center justify-center text-sm rounded-xl"
              style={{ border: '1px solid var(--color-border)', color: 'var(--color-text-muted)' }}>
              耦合分析准备中...
            </div>
          ) : (
            <div className="flex-1 flex items-center justify-center text-sm rounded-xl"
              style={{ border: '1px solid var(--color-border)', color: 'var(--color-text-muted)' }}>
              分析完成后将显示耦合关系图
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default AgentPanel
