import { useState } from "react"

function Timeline({ data }) {
    const [expanded, setExpanded] = useState(null)

    const toggle = (hash) => {
        setExpanded(expanded === hash ? null : hash)
    }

    if (!data) return null

    return (
        <div style={{ maxWidth: '700px', margin: '20px auto' }}>
            <p style={{ color: '#666', marginBottom: '10px' }}>
                {data.repo} · {data.file_path} · {data.commit_count} 次提交
            </p>
            {data.timeline.map((node) => (
                <div key={node.commit_hash} style={{ marginLeft: '20px', borderLeft: '2px solid #ddd', paddingLeft: '20px', marginBottom: '16px' }}>
                    <div onClick={() => toggle(node.commit_hash)} style={{ cursor: 'pointer' }}>
                        <span style={{ fontWeight: 'bold' }}>{node.commit_hash.slice(0, 7)}</span>
                        {' '}
                        <span>{node.author}</span>
                        {' '}
                        <span style={{ color: '#999', fontSize: '14px' }}>{node.date.slice(0, 10)}</span>
                        {' '}
                        <span style={{
                            display: 'inline-block',
                            padding: '2px 6px',
                            borderRadius: '4px',
                            fontSize: '12px',
                            color: '#fff',
                            backgroundColor: node.change_type === 'feature' ? '#3182ce'
                                : node.change_type === 'bugfix' ? '#e53e3e'
                                    : node.change_type === 'refactor' ? '#805ad5'
                                        : '#718096',
                        }}>
                            {node.change_type}
                        </span>
                    </div>
                    <div style={{ marginTop: '4px', color: '#444' }}>{node.summary}</div>
                    {expanded === node.commit_hash && (
                        <div style={{ marginTop: '8px', padding: '10px', background: '#f5f5f5', borderRadius: '6px', fontSize: '14px' }}>
                            <div><strong>commit:</strong> {node.commit_hash}</div>
                            <div><strong>message:</strong> {node.message}</div>
                            {node.pr_title && <div><strong>PR:</strong> {node.pr_title}</div>}
                            {node.diff_stats && (
                                <div><strong>改动:</strong> +{node.diff_stats.additions}/-{node.diff_stats.deletions} · {node.diff_stats.files_changed} 个文件</div>
                            )}
                        </div>
                    )}
                </div>
            ))}
        </div>
    )
}

export default Timeline