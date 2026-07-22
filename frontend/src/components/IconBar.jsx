function IconBar({ active, onSelect, fileTreeOpen, onToggleFileTree }) {
  const views = [
    { id: 'trace', icon: '🔍', label: '代码追溯' },
    { id: 'agent', icon: '🤖', label: 'Agent 分析' },
    { id: 'dashboard', icon: '📊', label: '仪表盘' },
  ]

  return (
    <div className="flex flex-col items-center gap-2 py-3"
      style={{ width: '48px', minWidth: '48px', borderRight: '1px solid var(--color-border)' }}>
      {/* 文件树 toggle */}
      <button
        title="文件树"
        onClick={onToggleFileTree}
        className="flex items-center justify-center rounded-lg transition-colors"
        style={{
          width: '36px', height: '36px', fontSize: '16px',
          border: 'none', cursor: 'pointer',
          background: fileTreeOpen ? 'var(--color-surface-alt)' : 'transparent',
          opacity: 0.7,
        }}
        onMouseEnter={e => e.currentTarget.style.background = 'var(--color-surface-alt)'}
        onMouseLeave={e => { if (!fileTreeOpen) e.currentTarget.style.background = 'transparent' }}
      >
        📂
      </button>

      <div style={{ width: '28px', height: '1px', background: 'var(--color-border)', margin: '4px 0' }} />

      {/* 视图切换 */}
      {views.map(v => (
        <button
          key={v.id}
          title={v.label}
          disabled={v.disabled}
          onClick={() => onSelect(v.id)}
          className="flex items-center justify-center rounded-lg transition-colors"
          style={{
            width: '36px', height: '36px', fontSize: '18px',
            border: 'none',
            cursor: v.disabled ? 'not-allowed' : 'pointer',
            background: active === v.id ? 'var(--color-accent)' : 'transparent',
            opacity: v.disabled ? 0.3 : active === v.id ? 1 : 0.6,
          }}
          onMouseEnter={e => { if (!v.disabled && active !== v.id) e.currentTarget.style.background = 'var(--color-surface-alt)' }}
          onMouseLeave={e => { if (active !== v.id) e.currentTarget.style.background = 'transparent' }}
        >
          {v.icon}
        </button>
      ))}
    </div>
  )
}

export default IconBar
