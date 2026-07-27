function DetailPanel({ node, isFunctionMode, onAskAgent, repoUrl, filePath }) {
  if (!node) {
    return (
      <div className="flex items-center justify-center h-full text-sm"
        style={{ color: 'var(--color-text-muted)' }}>
        点击左侧 commit 查看详情
      </div>
    )
  }

  const func = node.function || node.klass
  const changeType = node.change_type
  const llmNote = node.llm_note || node.summary
  const diffStats = node.diff_stats
  const prTitle = node.pr_title

  const typeColors = { feature: '#3182ce', bugfix: '#e53e3e', refactor: '#805ad5', chore: '#718096', docs: '#58a6ff', test: '#79c0ff' }

  return (
    <div className="h-full overflow-y-auto p-5 text-sm space-y-4"
      style={{ color: 'var(--color-text)' }}>
      {/* header */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <code className="text-sm font-mono" style={{ color: 'var(--color-accent)' }}>
            {node.commit_hash?.slice(0, 7)}
          </code>
          {changeType && (
            <span className="px-2 py-0.5 rounded-full text-xs font-medium"
              style={{ background: (typeColors[changeType] || '#718096') + '22', color: typeColors[changeType] || '#718096' }}>
              {changeType}
            </span>
          )}
          {onAskAgent && repoUrl && node.commit_hash && (
            <button
              onClick={(e) => {
                e.stopPropagation()
                onAskAgent({
                  type: 'commit',
                  repoUrl,
                  filePath: filePath || null,
                  commitHash: node.commit_hash,
                  commitMessage: node.message,
                  functionName: func?.name,
                  summary: node.summary,
                })
              }}
              className="ml-auto px-2 py-0.5 rounded text-xs transition-colors"
              style={{ color: 'var(--color-accent)' }}
              onMouseEnter={e => { e.currentTarget.style.background = 'var(--color-surface-alt)' }}
              onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}
            >
              🤖 问 Agent
            </button>
          )}
        </div>
        <span className="text-xs" style={{ color: 'var(--color-text-muted)' }}>
          {node.author} · {node.date?.slice(0, 10)}
        </span>
      </div>

      {/* commit message */}
      <div>
        <div className="text-xs font-medium mb-1" style={{ color: 'var(--color-text-muted)' }}>COMMIT MESSAGE</div>
        <div className="text-sm leading-relaxed font-mono" style={{ color: 'var(--color-text-heading)' }}>
          {node.message}
        </div>
      </div>

      {/* diff */}
      {diffStats && (
        <div>
          <div className="text-xs font-medium mb-1" style={{ color: 'var(--color-text-muted)' }}>DIFF</div>
          <div className="flex gap-3 text-sm">
            <span style={{ color: '#38a169' }}>+{diffStats.additions}</span>
            <span style={{ color: '#e53e3e' }}>-{diffStats.deletions}</span>
            <span style={{ color: 'var(--color-text-muted)' }}>· {diffStats.files_changed} files</span>
          </div>
        </div>
      )}

      {/* PR */}
      {prTitle && (
        <div>
          <div className="text-xs font-medium mb-1" style={{ color: 'var(--color-text-muted)' }}>PULL REQUEST</div>
          <div className="text-sm">{prTitle}</div>
        </div>
      )}

      {/* LLM note */}
      {llmNote && (
        <div className="p-3 rounded-lg text-sm" style={{ background: 'var(--color-surface-alt)', borderLeft: '3px solid var(--color-accent)' }}>
          <div className="text-xs font-medium mb-1" style={{ color: 'var(--color-text-muted)' }}>LLM 分析</div>
          {llmNote}
        </div>
      )}

      {/* function / class body */}
      {func && (
        <div>
          <div className="text-xs font-medium mb-1" style={{ color: 'var(--color-text-muted)' }}>
            {node.klass ? 'CLASS' : 'FUNCTION'} — {func.name}{func.start_line ? ` (L${func.start_line})` : ''}
          </div>
          <pre className="p-3 rounded-lg text-sm overflow-x-auto leading-relaxed font-mono"
            style={{ background: 'var(--color-surface-alt)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}>
            {func.body}
          </pre>
        </div>
      )}

      {/* migration */}
      {node.migration && (
        <div className="p-3 rounded-lg text-sm"
          style={{ background: 'var(--color-surface-alt)', borderLeft: '3px solid var(--color-accent)' }}>
          迁移至 {node.file}
        </div>
      )}
    </div>
  )
}

export default DetailPanel
