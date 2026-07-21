import { useState } from 'react'
import DetailPanel from './DetailPanel'

const TYPE_COLORS = { feature: '#3182ce', bugfix: '#e53e3e', refactor: '#805ad5', chore: '#718096', docs: '#58a6ff', test: '#79c0ff' }

function Timeline({ data }) {
  const [selectedHash, setSelectedHash] = useState(null)

  if (!data) return null

  const isFunctionMode = 'function_name' in data || 'class_name' in data
  const nodes = data.history || data.timeline || []
  const selected = nodes.find(n => n.commit_hash === selectedHash)

  if (nodes.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm p-8"
        style={{ color: 'var(--color-text-muted)' }}>
        {data.note || '无数据'}
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* 文件概览条 */}
      <div className="flex items-center gap-3 px-5 py-2 text-xs"
        style={{ borderBottom: '1px solid var(--color-border)', color: 'var(--color-text-muted)' }}>
        <span className="font-mono" style={{ color: 'var(--color-text)' }}>{data.file_path}</span>
        <span>·</span>
        <span>{data.commit_count || nodes.length} 次提交</span>
        {isFunctionMode && (
          <>
            <span>·</span>
            <span className="font-mono" style={{ color: 'var(--color-accent)' }}>
              {data.function_name || data.class_name}
            </span>
          </>
        )}
        {data.note && data.migration_path?.length > 0 && (
          <>
            <span>·</span>
            <span style={{ color: '#805ad5' }}>⚠ {data.note}</span>
          </>
        )}
      </div>

      {/* 主区域：timeline + 详情 */}
      <div className="flex-1 flex min-h-0">
        {/* timeline 列表 */}
        <div className="overflow-y-auto" style={{ width: '340px', minWidth: '340px', borderRight: '1px solid var(--color-border)' }}>
          {nodes.map((n, i) => {
            const func = n.function || n.klass
            const ct = n.change_type
            const isSelected = n.commit_hash === selectedHash
            return (
              <div
                key={n.commit_hash}
                onClick={() => setSelectedHash(n.commit_hash)}
                className="px-4 py-2.5 cursor-pointer transition-colors"
                style={{
                  background: isSelected ? 'var(--color-surface-alt)' : 'transparent',
                  borderBottom: '1px solid var(--color-border)',
                  borderLeft: isSelected ? '2px solid var(--color-accent)' : '2px solid transparent',
                }}
                onMouseEnter={e => { if (!isSelected) e.currentTarget.style.background = 'var(--color-surface-alt)' }}
                onMouseLeave={e => { if (!isSelected) e.currentTarget.style.background = 'transparent' }}
              >
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-base" style={{ color: isSelected ? 'var(--color-accent)' : 'var(--color-text-muted)' }}>
                    {isSelected ? '●' : '○'}
                  </span>
                  <code className="text-xs font-mono" style={{ color: 'var(--color-accent)', fontSize: '11px' }}>
                    {n.commit_hash?.slice(0, 7)}
                  </code>
                  {ct && (
                    <span className="px-1.5 py-0.5 rounded-full text-xs font-medium"
                      style={{ background: (TYPE_COLORS[ct] || '#718096') + '22', color: TYPE_COLORS[ct] || '#718096', fontSize: '10px' }}>
                      {ct}
                    </span>
                  )}
                  {n.migration && <span className="text-xs" style={{ color: '#805ad5' }}>↗</span>}
                </div>
                <div className="text-xs ml-5 leading-snug truncate" style={{ color: 'var(--color-text)' }}>
                  {func ? `${func.name} 变更` : (n.summary || n.message?.split('\n')[0] || '')}
                </div>
                <div className="ml-5 mt-0.5 text-xs" style={{ color: 'var(--color-text-muted)' }}>
                  {n.author} · {n.date?.slice(0, 10)}
                  {n.migration_path && n.migration_path.length > 0 && ` · 迁移`}
                </div>
                {func && func.name && (
                  <div className="ml-5 text-xs font-mono" style={{ color: 'var(--color-accent-light)' }}>
                    {func.name}
                  </div>
                )}
              </div>
            )
          })}
        </div>

        {/* 详情面板 */}
        <div className="flex-1 overflow-y-auto bg-[var(--color-surface)]">
          <DetailPanel node={selected || nodes[0]} isFunctionMode={isFunctionMode} />
        </div>
      </div>
    </div>
  )
}

export default Timeline
