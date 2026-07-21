const TH = {
  padding: '6px 8px',
  textAlign: 'left',
  borderBottom: '2px solid #cbd5e0',
  whiteSpace: 'nowrap',
  fontSize: '11px',
  fontWeight: '600',
  color: '#4a5568',
  background: '#f7fafc',
  position: 'sticky',
  top: 0,
}

const TD = {
  padding: '5px 8px',
  verticalAlign: 'top',
  borderBottom: '1px solid #edf2f7',
  fontSize: '12px',
  color: '#333',
}

function riskBadge(risk) {
  const colors = {
    high: { bg: '#fed7d7', text: '#c53030' },
    medium: { bg: '#fefcbf', text: '#975a16' },
    low: { bg: '#c6f6d5', text: '#276749' },
  }
  const c = colors[risk] || colors.low
  return (
    <span style={{
      padding: '1px 8px',
      borderRadius: '10px',
      fontSize: '11px',
      fontWeight: 'bold',
      background: c.bg,
      color: c.text,
    }}>
      {risk}
    </span>
  )
}

function ModuleErosion({ nodes }) {
  if (!nodes || nodes.length === 0) {
    return <div style={{ padding: '10px', color: '#a0aec0', fontSize: '13px' }}>暂无耦合数据</div>
  }

  const sorted = [...nodes].sort((a, b) => {
    const sa = a.coupling_growth * (a.boundary_crossings + 1)
    const sb = b.coupling_growth * (b.boundary_crossings + 1)
    return sb - sa
  })

  return (
    <div style={{ overflow: 'auto', maxHeight: '300px', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th style={TH}>文件</th>
            <th style={TH}>模块</th>
            <th style={{ ...TH, textAlign: 'center' }}>伙伴</th>
            <th style={{ ...TH, textAlign: 'center' }}>增长率</th>
            <th style={{ ...TH, textAlign: 'center' }}>跨模块</th>
            <th style={TH}>风险</th>
            <th style={TH}>建议</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map(n => (
            <tr key={n.id} style={{ background: n.risk === 'high' ? '#fff5f5' : 'transparent' }}>
              <td style={TD} title={n.id}>{n.label}</td>
              <td style={{ ...TD, color: '#718096', fontSize: '11px' }}>{n.module}</td>
              <td style={{ ...TD, textAlign: 'center' }}>{n.recent_partners}</td>
              <td style={{ ...TD, textAlign: 'center', color: n.coupling_growth > 0.3 ? '#e53e3e' : '#38a169' }}>
                {n.coupling_growth > 0 ? `+${n.coupling_growth}` : n.coupling_growth}
              </td>
              <td style={{ ...TD, textAlign: 'center' }}>{n.boundary_crossings}</td>
              <td style={TD}>{riskBadge(n.risk)}</td>
              <td style={{ ...TD, color: '#718096', fontSize: '11px', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {n.suggestion || n.warning || '-'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default ModuleErosion
