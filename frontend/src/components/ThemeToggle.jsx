import { useState, useEffect } from 'react'

function ThemeToggle() {
  const [light, setLight] = useState(() =>
    document.documentElement.classList.contains('light')
  )

  useEffect(() => {
    const root = document.documentElement
    if (light) {
      root.classList.add('light')
    } else {
      root.classList.remove('light')
    }
    localStorage.setItem('codetrace_theme', light ? 'light' : 'dark')
  }, [light])

  return (
    <button
      onClick={() => setLight(p => !p)}
      title={light ? '切换暗色模式' : '切换亮色模式'}
      className="flex items-center justify-center rounded-lg transition-colors"
      style={{
        width: '32px', height: '32px', fontSize: '16px',
        border: '1px solid var(--color-border)',
        cursor: 'pointer',
        background: 'transparent',
      }}
      onMouseEnter={e => e.currentTarget.style.background = 'var(--color-surface-alt)'}
      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
    >
      {light ? '🌙' : '☀️'}
    </button>
  )
}

export default ThemeToggle
