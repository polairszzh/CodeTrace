import { useState, useEffect } from 'react'
import CouplingGraph from './CouplingGraph'
import ModuleErosion from './ModuleErosion'

function AgentPanel() {
    const [repoUrl, setRepoUrl] = useState('')
    const [goal, setGoal] = useState('')
    const [running, setRunning] = useState(false)
    const [finalReport, setFinalReport] = useState(null)
    const [couplingData, setCouplingData] = useState(null)
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
        setFinalReport(null)
        setCouplingData(null)
        setError('')

        try {
            const params = goal
                ? `?goal=${encodeURIComponent(goal)}`
                : ''
            const res = await fetch(`/api/graph/analyze${params}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ repo_url: repoUrl, file_path: '.' }),
            })

            if (!res.ok) throw new Error(`请求失败: ${res.status}`)
            const data = await res.json()

            if (data.report && data.report.error) {
                setError(data.report.error)
            }
            if (data.report && data.report.final_report) {
                setFinalReport({ summary: data.report.final_report })
            }
            if (data.coupling) {
                setCouplingData(data.coupling)
            }
        } catch (e) {
            setError(e.message)
        } finally {
            setRunning(false)
        }
    }

    return (
        <div style={{ padding: '0 20px', marginTop: '20px' }}>
            {/* 输入栏 */}
            <div style={{ display: 'flex', gap: '10px', marginBottom: '20px' }}>
                <input
                    placeholder="GitHub 仓库 URL"
                    value={repoUrl}
                    onChange={e => setRepoUrl(e.target.value)}
                    style={{ flex: 2, padding: '8px', borderRadius: '6px', border: '1px solid #cbd5e0' }}
                />
                <input
                    placeholder="分析目标（可留空）"
                    value={goal}
                    onChange={e => setGoal(e.target.value)}
                    style={{ flex: 1, padding: '8px', borderRadius: '6px', border: '1px solid #cbd5e0' }}
                />
                <button onClick={handleAnalyze} disabled={running}
                    style={{
                        padding: '8px 20px', borderRadius: '6px', cursor: running ? 'not-allowed' : 'pointer',
                        background: running ? '#a0aec0' : '#3182ce', color: '#fff', border: 'none',
                        fontWeight: '600', whiteSpace: 'nowrap',
                    }}>
                    {running ? '分析中...' : '开始分析'}
                </button>
            </div>

            {error && (
                <div style={{ color: '#e53e3e', marginBottom: '12px', padding: '8px 12px',
                    background: '#fff5f5', borderRadius: '6px', border: '1px solid #fed7d7', fontSize: '13px' }}>
                    {error}
                </div>
            )}

            {/* 左右分栏 */}
            <div style={{ display: 'flex', gap: '20px', minHeight: '600px' }}>
                {/* 左栏：报告 */}
                <div style={{ flex: '1 1 45%', minWidth: 0 }}>
                    {finalReport && (
                        <div style={{
                            background: '#f7fafc', padding: '20px', borderRadius: '8px',
                            border: '1px solid #e2e8f0', height: '100%', overflow: 'auto',
                        }}>
                            <h3 style={{ margin: '0 0 12px 0', fontSize: '16px' }}>分析报告</h3>
                            <div style={{
                                whiteSpace: 'pre-wrap', color: '#333', fontSize: '14px',
                                lineHeight: '1.7',
                            }}>
                                {finalReport.summary}
                            </div>
                        </div>
                    )}
                    {!finalReport && !running && !error && (
                        <div style={{ color: '#a0aec0', textAlign: 'center', paddingTop: '120px', fontSize: '14px' }}>
                            输入仓库 URL 开始分析
                        </div>
                    )}
                    {running && !finalReport && (
                        <div style={{ color: '#718096', textAlign: 'center', paddingTop: '120px', fontSize: '14px' }}>
                            Agent 正在探索仓库...
                        </div>
                    )}
                </div>

                {/* 右栏：耦合可视化 */}
                <div style={{ flex: '1 1 55%', minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
                    {couplingData && couplingData.nodes && couplingData.nodes.length > 0 ? (
                        <>
                            <div style={{ height: '460px', marginBottom: '12px' }}>
                                <h4 style={{ margin: '0 0 8px 0', fontSize: '14px' }}>
                                    共变耦合图
                                    <span style={{ fontWeight: 'normal', fontSize: '12px', color: '#718096', marginLeft: '10px' }}>
                                        {couplingData.total_files} 个文件, {couplingData.edges.length} 条边
                                        {couplingData.high_risk_count > 0 && `, ${couplingData.high_risk_count} 高风险`}
                                    </span>
                                </h4>
                                <CouplingGraph
                                    nodes={couplingData.nodes}
                                    edges={couplingData.edges}
                                />
                            </div>
                            <div style={{ flex: '0 0 auto' }}>
                                <h4 style={{ margin: '0 0 8px 0', fontSize: '14px' }}>模块侵蚀风险</h4>
                                <ModuleErosion nodes={couplingData.nodes} />
                            </div>
                        </>
                    ) : couplingData && (
                        <div style={{ padding: '20px', color: '#718096', textAlign: 'center', fontSize: '14px' }}>
                            {couplingData.note || '暂无耦合数据'}
                        </div>
                    )}
                    {!couplingData && running && (
                        <div style={{ padding: '20px', color: '#718096', textAlign: 'center', fontSize: '14px' }}>
                            耦合分析准备中...
                        </div>
                    )}
                    {!couplingData && !running && !error && (
                        <div style={{ color: '#a0aec0', textAlign: 'center', paddingTop: '120px', fontSize: '14px' }}>
                            分析完成后将显示耦合关系图
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}

export default AgentPanel
