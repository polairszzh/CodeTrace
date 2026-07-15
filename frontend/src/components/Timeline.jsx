import { useState } from "react"

function Timeline({ data }) {
    const [expanded, setExpanded] = useState(null)

    const toggle = (hash) => {
        setExpanded(expanded === hash ? null : hash)
    }

    if (!data) return null

    const isFunctionMode = "function_name" in data
    const nodes = isFunctionMode ? data.history : data.timeline

    return (
        <div style={{ maxWidth: '700px', margin: '20px auto' }}>
            <p style={{ color: '#666', marginBottom: '10px' }}>
                {data.repo} · {data.file_path}
                {isFunctionMode && <> · 函数 <strong>{data.function_name}</strong></>}
                · {data.commit_count} 次提交
            </p>
            {data.note && nodes.length === 0 && (
                <div style={{ padding: '20px', color: '#d69e2e', textAlign: 'center' }}>{data.note}</div>
            )}
            {nodes.map((node) => {
                const func = node.function
                const changeType = node.change_type
                const llmNote = node.llm_note

                return (
                    <div key={node.commit_hash} style={{ marginLeft: '20px', borderLeft: '2px solid #ddd', paddingLeft: '20px', marginBottom: '16px' }}>
                        <div onClick={() => toggle(node.commit_hash)} style={{ cursor: 'pointer' }}>
                            <span style={{ fontWeight: 'bold' }}>{node.commit_hash.slice(0, 7)}</span>
                            {' '}
                            <span>{node.author}</span>
                            {' '}
                            <span style={{ color: '#999', fontSize: '14px' }}>{node.date?.slice(0, 10)}</span>
                            {changeType && (
                                <>
                                    {' '}
                                    <span style={{
                                        display: 'inline-block',
                                        padding: '2px 6px',
                                        borderRadius: '4px',
                                        fontSize: '12px',
                                        color: '#fff',
                                        backgroundColor: changeType === 'feature' ? '#3182ce'
                                            : changeType === 'bugfix' ? '#e53e3e'
                                                : changeType === 'refactor' ? '#805ad5'
                                                    : '#718096',
                                    }}>
                                        {changeType}
                                    </span>
                                </>
                            )}
                            {func && (
                                <span style={{ marginLeft: '8px', fontFamily: 'monospace', fontSize: '13px', color: '#555' }}>
                                    {func.name} (L{func.start_line})
                                </span>
                            )}
                            {llmNote && (
                                <span style={{ marginLeft: '8px', fontSize: '12px', color: '#d69e2e' }}>
                                    ⚡ {llmNote}
                                </span>
                            )}
                        </div>
                        <div style={{ marginTop: '4px', color: '#444' }}>
                            {func ? func.name + ' 变更' : node.summary}
                        </div>
                        {expanded === node.commit_hash && (
                            <div style={{ marginTop: '8px', padding: '10px', background: '#f5f5f5', borderRadius: '6px', fontSize: '14px' }}>
                                <div><strong>commit:</strong> {node.commit_hash}</div>
                                <div><strong>message:</strong> {node.message}</div>
                                {func && <div><strong>函数:</strong> {func.name}（第 {func.start_line}-{func.end_line} 行）</div>}
                                {node.pr_title && <div><strong>PR:</strong> {node.pr_title}</div>}
                                {node.diff_stats && (
                                    <div><strong>改动:</strong> +{node.diff_stats.additions}/-{node.diff_stats.deletions} · {node.diff_stats.files_changed} 个文件</div>
                                )}
                                {func && (
                                    <details style={{ marginTop: '8px' }}>
                                        <summary style={{ cursor: 'pointer', color: '#666' }}>查看函数代码</summary>
                                        <pre style={{ background: '#eee', padding: '8px', borderRadius: '4px', fontSize: '12px', overflow: 'auto' }}>
                                            {func.body}
                                        </pre>
                                    </details>
                                )}
                            </div>
                        )}
                    </div>
                )
            })}
        </div>
    )
}

export default Timeline
