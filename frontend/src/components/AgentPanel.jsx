import { useState, useRef, useEffect } from 'react'

function AgentPanel() {
    const [repoUrl, setRepoUrl] = useState('')
    const [goal, setGoal] = useState('')
    const [running, setRunning] = useState(false)
    const [steps, setSteps] = useState([])
    const [finalReport, setFinalReport] = useState(null)
    const [error, setError] = useState('')

    // 从 URL 参数读取仓库地址（由扩展打开时自动填入）
    useEffect(() => {
        const params = new URLSearchParams(window.location.search)
        const repo = params.get('repo')
        if (repo) {
            setRepoUrl(repo)
        }
    }, [])

    const handleAnalyze = async () => {
        setRunning(true)
        setSteps([])
        setFinalReport(null)
        setError('')

        try {
            const params = goal
                ? `?goal=${encodeURIComponent(goal)}`
                : ''
            const res = await fetch(`/api/agent/analyze${params}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ repo_url: repoUrl, file_path: '.' }),
            })

            const reader = res.body.getReader()
            const decoder = new TextDecoder()
            let buffer = ''

            while (true) {
                const { done, value } = await reader.read()
                if (done) break

                buffer += decoder.decode(value, { stream: true })
                const lines = buffer.split('\n')
                buffer = lines.pop()

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        const chunk = line.slice(6)
                        if (chunk === '[DONE]') continue

                        try {
                            const parsed = JSON.parse(chunk)
                            setSteps(prev => [...prev, parsed])
                            if (parsed.final) {
                                try {
                                    setFinalReport(JSON.parse(parsed.final))
                                } catch {
                                    setFinalReport({ summary: parsed.final })
                                }
                            }
                        } catch { }
                    }
                }
            }
        } catch (e) {
            setError(e.message)
        } finally {
            setRunning(false)
        }
    }

    return (
        <div style={{ maxWidth: '900px', margin: '20px auto', padding: '0 20px' }}>
            <div style={{ display: 'flex', gap: '10px', marginBottom: '10px' }}>
                <input
                    placeholder="GitHub 仓库 URL"
                    value={repoUrl}
                    onChange={e => setRepoUrl(e.target.value)}
                    style={{ flex: 1, padding: '8px' }}
                />
                <input
                    placeholder="分析目标（可留空）"
                    value={goal}
                    onChange={e => setGoal(e.target.value)}
                    style={{ flex: 1, padding: '8px' }}
                />
                <button onClick={handleAnalyze} disabled={running}
                    style={{ padding: '8px 16px', cursor: 'pointer' }}>
                    {running ? 'Agent 分析中...' : '开始分析'}
                </button>
            </div>

            {error && <div style={{ color: '#e53e3e', marginBottom: '10px' }}>{error}</div>}

            {steps.length > 0 && (
                <div style={{ marginTop: '20px' }}>
                    <h3>Agent 探索过程</h3>
                    {steps.map((s, i) => {
                        if (s.status) {
                            return (
                                <div key={i} style={{
                                    padding: '8px 12px', marginBottom: '6px', background: '#fffbeb',
                                    borderRadius: '6px', fontSize: '13px', borderLeft: '3px solid #d69e2e'
                                }}>
                                    <span style={{ color: '#975a16' }}>{s.status}</span>
                                </div>
                            )
                        }
                        if (!s.tool) return null
                        return (
                        <div key={i} style={{
                            padding: '8px 12px', marginBottom: '6px', background: '#f0f4f8',
                            borderRadius: '6px', fontSize: '13px', borderLeft: '3px solid #3182ce'
                        }}>
                            <span style={{ fontWeight: 'bold', color: '#3182ce' }}>Step {s.step}: {s.tool}</span>
                            <details style={{ marginTop: '4px', fontSize: '12px', color: '#555' }}>
                                <summary>查看详情</summary>
                                <pre style={{ background: '#fff', padding: '6px', borderRadius: '4px', overflow: 'auto', maxHeight: '200px' }}>
                                    {JSON.stringify(s.args, null, 2)}
                                </pre>
                            </details>
                        </div>
                        )
                    })}
                </div>
            )}

            {finalReport && (
                <div style={{ marginTop: '20px', background: '#f7fafc', padding: '20px', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
                    <h3 style={{ marginTop: 0 }}>分析报告: {finalReport.repo || repoUrl}</h3>
                    {finalReport.summary && (
                        <div style={{
                            whiteSpace: 'pre-wrap', color: '#333', fontSize: '14px', lineHeight: '1.6',
                            maxHeight: '600px', overflow: 'auto'
                        }}>
                            {finalReport.summary}
                        </div>
                    )}

                    {finalReport.hotspots && finalReport.hotspots.length > 0 && (
                        <div style={{ marginTop: '12px' }}>
                            <strong>热点模块</strong>
                            {finalReport.hotspots.map((h, i) => (
                                <div key={i} style={{ padding: '6px 10px', marginTop: '4px', background: '#fff', borderRadius: '4px', fontSize: '13px' }}>
                                    {h.file} — {h.changes} 次变更
                                    {h.bug_ratio !== undefined && (
                                        <span style={{ color: h.bug_ratio > 0.3 ? '#e53e3e' : '#38a169', marginLeft: '8px' }}>
                                            bug 修复率 {Math.round(h.bug_ratio * 100)}%
                                        </span>
                                    )}
                                </div>
                            ))}
                        </div>
                    )}

                    {finalReport.at_risk && finalReport.at_risk.length > 0 && (
                        <div style={{ marginTop: '12px' }}>
                            <strong style={{ color: '#e53e3e' }}>风险模块</strong>
                            {finalReport.at_risk.map((r, i) => (
                                <div key={i} style={{ padding: '6px 10px', marginTop: '4px', background: '#fff5f5', borderRadius: '4px', fontSize: '13px', color: '#c53030' }}>
                                    {r.file} — {r.reason}
                                </div>
                            ))}
                        </div>
                    )}

                    {finalReport.recommendations && finalReport.recommendations.length > 0 && (
                        <div style={{ marginTop: '12px' }}>
                            <strong>建议</strong>
                            <ul style={{ margin: '4px 0', paddingLeft: '20px', fontSize: '13px' }}>
                                {finalReport.recommendations.map((rec, i) => (
                                    <li key={i}>{rec}</li>
                                ))}
                            </ul>
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}

export default AgentPanel