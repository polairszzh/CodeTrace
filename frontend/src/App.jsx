import { useState, useEffect } from 'react'
import IconBar from './components/IconBar'
import TraceInput from './components/TraceInput'
import FileTree from './components/FileTree'
import Timeline from './components/Timeline'
import AgentPanel from './components/AgentPanel'
import Dashboard from './components/Dashboard'
import ThemeToggle from './components/ThemeToggle'

function App() {
  const [tab, setTab] = useState('trace')
  const [timeline, setTimeline] = useState(null)
  const [fileTreeOpen, setFileTreeOpen] = useState(false)
  const [currentRepoUrl, setCurrentRepoUrl] = useState('')
  const [selectedFile, setSelectedFile] = useState('')
  const [selectedFunction, setSelectedFunction] = useState('')

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('repo')) setTab('agent')
  }, [])

  const handleTraceSearch = (data) => {
    setTimeline(data)
  }

  const handleFileSelect = (path) => {
    setSelectedFile(path)
    setSelectedFunction('')
    setFileTreeOpen(false)
  }

  return (
    <div className="flex h-screen" style={{ background: 'var(--color-surface)' }}>
      {/* 左侧 icon 栏 */}
      <IconBar
        active={tab}
        onSelect={(id) => { setTab(id); if (id !== 'trace') setTimeline(null) }}
        fileTreeOpen={fileTreeOpen}
        onToggleFileTree={() => setFileTreeOpen(p => !p)}
      />

      {/* 文件树（可折叠） — 始终挂载，宽度切换来实现显示/隐藏 */}
      <div className="overflow-hidden flex flex-col transition-all duration-200" style={{
        width: fileTreeOpen ? '250px' : '0px',
        minWidth: fileTreeOpen ? '250px' : '0px',
        borderRight: fileTreeOpen ? '1px solid var(--color-border)' : 'none',
        overflow: 'hidden',
      }}>
        <div className="px-3 py-2 text-xs font-medium flex-shrink-0" style={{ color: 'var(--color-text-muted)', borderBottom: '1px solid var(--color-border)' }}>
          文件树
        </div>
        <div className="flex-1 overflow-hidden">
          <FileTree repoUrl={currentRepoUrl} onFileSelect={handleFileSelect} />
        </div>
      </div>

      {/* 主内容 */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Header */}
        <header className="flex items-center gap-3 px-6 py-3"
          style={{ borderBottom: '1px solid var(--color-border)' }}>
          <h1 className="text-lg font-semibold m-0" style={{ color: 'var(--color-text-heading)', fontFamily: 'system-ui, sans-serif', letterSpacing: '-0.5px' }}>
            CodeTrace
          </h1>
          <span className="text-sm flex-1" style={{ color: 'var(--color-text-muted)' }}>
            理解你的代码是如何演化而来
          </span>
          <ThemeToggle />
        </header>

        {/* 内容区 */}
        <div className="flex-1 overflow-hidden">
          {tab === 'trace' && (
            <div className="h-full flex flex-col">
              <TraceInput
                onSearch={handleTraceSearch}
                onRepoChange={setCurrentRepoUrl}
                externalFile={selectedFile}
                externalFunction={selectedFunction}
              />
              {timeline && <Timeline data={timeline} />}
            </div>
          )}
          {tab === 'agent' && <AgentPanel />}
          {tab === 'dashboard' && <Dashboard />}
        </div>
      </div>
    </div>
  )
}

export default App
