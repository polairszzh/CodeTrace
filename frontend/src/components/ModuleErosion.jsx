function riskBadge(risk) {
  const colors = {
    high: { bg: '#fed7d7', text: '#c53030' },
    medium: { bg: '#fefcbf', text: '#975a16' },
    low: { bg: '#c6f6d5', text: '#276749' },
  }
  const c = colors[risk] || colors.low
  return (
    <span className="px-1.5 py-0.5 rounded-full text-xs font-bold"
      style={{ background: c.bg, color: c.text }}>
      {risk}
    </span>
  )
}

function ModuleErosion({ nodes }) {
  if (!nodes || nodes.length === 0) {
    return <div className="text-xs p-3 text-center rounded-xl" style={{ color: 'var(--color-text-muted)', border: '1px solid var(--color-border)' }}>暂无耦合数据</div>
  }

  const sorted = [...nodes].sort((a, b) => {
    return (b.coupling_growth * (b.boundary_crossings + 1)) - (a.coupling_growth * (a.boundary_crossings + 1))
  })

  return (
    <div className="overflow-auto rounded-xl text-xs" style={{ maxHeight: '280px', border: '1px solid var(--color-border)' }}>
      <table className="w-full border-collapse">
        <thead>
          <tr className="text-xs font-medium sticky top-0" style={{ color: 'var(--color-text-muted)', background: 'var(--color-surface-card)' }}>
            <th className="p-2 text-left">文件</th>
            <th className="p-2 text-left">模块</th>
            <th className="p-2 text-center">伙伴</th>
            <th className="p-2 text-center">增长率</th>
            <th className="p-2 text-center">跨模块</th>
            <th className="p-2 text-left">风险</th>
            <th className="p-2 text-left">建议</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map(n => (
            <tr key={n.id} className="border-t" style={{ borderColor: 'var(--color-border)', background: n.risk === 'high' ? 'rgba(229,62,62,0.04)' : 'transparent' }}>
              <td className="p-2 font-mono" style={{ color: 'var(--color-text)' }} title={n.id}>{n.label}</td>
              <td className="p-2" style={{ color: 'var(--color-text-muted)' }}>{n.module}</td>
              <td className="p-2 text-center font-mono" style={{ color: 'var(--color-text)' }}>{n.recent_partners}</td>
              <td className="p-2 text-center font-mono" style={{ color: n.coupling_growth > 0.3 ? '#e53e3e' : '#38a169' }}>
                {n.coupling_growth > 0 ? `+${n.coupling_growth}` : n.coupling_growth}
              </td>
              <td className="p-2 text-center font-mono" style={{ color: 'var(--color-text)' }}>{n.boundary_crossings}</td>
              <td className="p-2">{riskBadge(n.risk)}</td>
              <td className="p-2 truncate max-w-[180px]" style={{ color: 'var(--color-text-muted)' }}>{n.suggestion || n.warning || '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default ModuleErosion
