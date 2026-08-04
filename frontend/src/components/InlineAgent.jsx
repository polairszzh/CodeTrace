import { useState, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export default function InlineAgent({ context, onClose }) {
  const [history, setHistory] = useState([])
  const [error, setError] = useState('')
  const [followUp, setFollowUp] = useState('')
  const [initialLoading, setInitialLoading] = useState(true)
  const [streamStatus, setStreamStatus] = useState('')
  const [visibleText, setVisibleText] = useState('')
  const [typing, setTyping] = useState(false)
  const contentRef = useRef(null)
  const pendingRef = useRef('')
  const shownRef = useRef(0)
  const timerRef = useRef(null)
  const controllerRef = useRef(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    if (contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight
    }
  })

  // 组件卸载时中止请求并清理定时器，避免关闭面板后继续 setState
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      controllerRef.current?.abort()
      if (timerRef.current) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
    }
  }, [])

  const stopTypewriter = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    if (mountedRef.current) setTyping(false)
  }

  const flushTypewriter = () => {
    stopTypewriter()
    shownRef.current = pendingRef.current.length
    if (mountedRef.current) setVisibleText(pendingRef.current)
  }

  const startTypewriter = () => {
    if (timerRef.current) return
    if (mountedRef.current) setTyping(true)
    // 打字机：每 20ms 显示两个字符；追上已接收内容时保持等待，
    // 后续 report 块继续追加，流结束才 flush
    timerRef.current = setInterval(() => {
      if (!mountedRef.current) return
      const total = pendingRef.current.length
      if (shownRef.current >= total) {
        return
      }
      // 按 Unicode code point 推进，避免 emoji/代理对半个字符闪烁
      let steps = 0
      while (steps < 2 && shownRef.current < total) {
        const cp = pendingRef.current.codePointAt(shownRef.current)
        shownRef.current += (cp !== undefined && cp > 0xFFFF) ? 2 : 1
        steps += 1
      }
      setVisibleText(pendingRef.current.slice(0, shownRef.current))
    }, 20)
  }

  const runAnalysis = async (question) => {
    // 中止上一个请求，避免旧流内容混入新问题
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller

    setError('')
    setInitialLoading(history.length === 0)
    setStreamStatus('')
    pendingRef.current = ''
    shownRef.current = 0
    setVisibleText('')
    stopTypewriter()

    // 添加问题到对话
    setHistory(p => [...p, { q: question, a: '', id: Date.now() }])

    try {
      const res = await fetch('/api/agent/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          repo_url: context.repoUrl,
          file_path: context.filePath || '',
          function_name: context.functionName || '',
          commit_hash: context.commitHash || '',
          question,
        }),
      })
      if (!res.ok) throw new Error(`请求失败: ${res.status}`)

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let firstChunk = true

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        if (!mountedRef.current) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          const trimmed = line.trim()
          if (!trimmed || !trimmed.startsWith('data: ')) continue
          const payload = trimmed.slice(6)
          if (payload === '[DONE]') continue
          try {
            const ev = JSON.parse(payload)
            if (ev.type === 'report') {
              pendingRef.current += ev.content
              setStreamStatus('')
              startTypewriter()
              if (firstChunk) {
                firstChunk = false
                setInitialLoading(false)
              }
            } else if (ev.type === 'tool') {
              const TOOL_LABELS = {
                get_commit_diff: '查看代码 diff',
                read_file_at_head: '读取文件内容',
                get_file_commits: '查看 commit 历史',
                trace_function: '追踪函数演变',
                get_pr_info: '查询 PR 信息',
              }
              const names = (ev.name || '').split(', ').map(n => TOOL_LABELS[n] || n).join('、')
              setStreamStatus(`正在${names}...`)
            } else if (ev.type === 'thinking') {
              setStreamStatus('思考中...')
            }
          } catch { /* skip */ }
        }
      }

      // 最终写入完整内容
      flushTypewriter()
      if (mountedRef.current) {
        setHistory(p => {
          const h = [...p]
          const last = { ...h[h.length - 1] }
          last.a = pendingRef.current
          h[h.length - 1] = last
          return h
        })
      }
    } catch (e) {
      stopTypewriter()
      if (e.name !== 'AbortError' && mountedRef.current) setError(e.message)
    } finally {
      if (mountedRef.current) {
        if (firstChunk) setInitialLoading(false)
      }
    }
  }

  // 自动触发首次分析
  useEffect(() => {
    if (context) {
      const map = { commit: '分析这次变更的原因和潜在影响', file: '分析这个文件的健康状况和风险' }
      const q = context.functionName
        ? '分析这个函数的演化历史和变更原因'
        : map[context.type] || '分析这个上下文的代码变更情况'
      runAnalysis(q)
    }
  }, [])

  const handleFollowUp = () => {
    if (!followUp.trim()) return
    const q = followUp
    setFollowUp('')
    runAnalysis(q)
  }

  const contextLines = []
  if (context?.filePath) contextLines.push(['📄', context.filePath])
  if (context?.functionName) contextLines.push(['🔧', context.functionName])
  if (context?.commitHash) contextLines.push(['📌', context.commitHash.slice(0, 7)])

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end"
      style={{ background: 'rgba(0,0,0,0.35)' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="h-full w-[480px] flex flex-col inlane-agent-panel"
        style={{
          background: 'var(--color-surface)',
          borderLeft: '1px solid var(--color-border)',
          boxShadow: '-4px 0 24px rgba(0,0,0,0.15)',
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 flex-shrink-0"
          style={{ borderBottom: '1px solid var(--color-border)' }}>
          <div className="flex items-center gap-2">
            <span className="text-lg">🤖</span>
            <span className="text-sm font-semibold" style={{ color: 'var(--color-text-heading)' }}>问 Agent</span>
          </div>
          <button onClick={onClose} className="px-2 py-1 rounded text-sm transition-colors"
            style={{ color: 'var(--color-text-muted)' }}
            onMouseEnter={e => { e.currentTarget.style.background = 'var(--color-surface-alt)' }}
            onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}>
            ✕
          </button>
        </div>

        {/* Context bar */}
        {contextLines.length > 0 && (
          <div className="px-4 py-2 text-xs space-y-0.5 flex-shrink-0"
            style={{ background: 'var(--color-surface-alt)', color: 'var(--color-text-muted)', borderBottom: '1px solid var(--color-border)' }}>
            {contextLines.map(([icon, text], i) => <div key={i}>{icon} {text}</div>)}
          </div>
        )}

        {/* Content */}
        <div ref={contentRef} className="flex-1 overflow-y-auto p-4 space-y-4">
          {initialLoading && (
            <div className="flex items-center justify-center h-full text-sm" style={{ color: 'var(--color-text-muted)' }}>
              <div className="text-center">
                <div className="w-6 h-6 border-2 rounded-full mx-auto mb-3 animate-spin"
                  style={{ borderColor: 'var(--color-border)', borderTopColor: 'var(--color-accent)' }} />
                {streamStatus || '分析中...'}
              </div>
            </div>
          )}

          {error && !initialLoading && (
            <div className="p-3 rounded-lg text-sm" style={{ background: 'var(--color-surface-alt)', color: '#e53e3e' }}>
              {error}
            </div>
          )}

          {history.map((item, i) => {
            const isLast = i === history.length - 1
            const streaming = isLast && typing && !initialLoading
            const waiting = isLast && item.a === '' && !typing
            // 首次加载时全屏 spinner 覆盖，不显示 inline spinner
            if (isLast && initialLoading) return null
            return (
              <div key={item.id} className="space-y-3">
                {i > 0 && (
                  <div className="px-3 py-2 rounded-lg text-sm"
                    style={{ background: 'var(--color-surface-alt)', color: 'var(--color-text-heading)' }}>
                    <span style={{ color: 'var(--color-accent)' }}>Q:</span> {item.q}
                  </div>
                )}
                {waiting ? (
                  <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--color-text-muted)' }}>
                    <div className="w-4 h-4 border-2 rounded-full animate-spin flex-shrink-0"
                      style={{ borderColor: 'var(--color-border)', borderTopColor: 'var(--color-accent)' }} />
                    {streamStatus || '分析中...'}
                  </div>
                ) : streaming ? (
                  <div className="text-sm leading-relaxed markdown-report"
                    style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                    {visibleText}
                    <div className="mt-2 h-4 w-2 animate-pulse" style={{ background: 'var(--color-accent)' }} />
                  </div>
                ) : item.a ? (
                  <div className="text-sm leading-relaxed markdown-report">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.a}</ReactMarkdown>
                    {isLast && !initialLoading && (
                      <div className="mt-2 h-4 w-2 animate-pulse" style={{ background: 'var(--color-accent)' }} />
                    )}
                  </div>
                ) : null}
              </div>
            )
          })}
        </div>

        {/* Follow-up input */}
        <div className="p-3 flex gap-2 flex-shrink-0" style={{ borderTop: '1px solid var(--color-border)' }}>
          <input value={followUp} onChange={e => setFollowUp(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleFollowUp()}
            placeholder="追问 Agent..."
            className="flex-1 px-3 py-2 rounded-lg text-sm outline-none"
            style={{
              background: 'var(--color-surface-alt)', border: '1px solid var(--color-border)',
              color: 'var(--color-text-heading)',
            }} />
          <button onClick={handleFollowUp}
            disabled={!followUp.trim()}
            className="px-3 py-2 rounded-lg text-sm font-medium"
            style={{
              background: followUp.trim() ? 'var(--color-accent)' : 'var(--color-text-muted)',
              color: '#fff', border: 'none', opacity: followUp.trim() ? 1 : 0.5,
            }}>
            发送
          </button>
        </div>
      </div>
    </div>
  )
}
