import { useState, useEffect } from "react";
import SearchBar from "./components/SearchBar"
import Timeline from "./components/Timeline"
import AgentPanel from "./components/AgentPanel"

function App() {
  const [timeline, setTimeline] = useState(null)
  const [tab, setTab] = useState("trace")

  // URL 有 repo 参数时默认切到 Agent 分析 tab
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('repo')) {
      setTab('agent')
    }
  }, [])

  return (
    <div>
      <h1 style={{ textAlign: 'center' }}>CodeTrace</h1>
      <div style={{ display: 'flex', justifyContent: 'center', gap: '10px', marginBottom: '20px' }}>
        <button onClick={() => setTab('trace')}
          style={{ padding: '6px 20px', background: tab === 'trace' ? '#3182ce' : '#e2e8f0', color: tab === 'trace' ? '#fff' : '#333', border: 'none', borderRadius: '6px', cursor: 'pointer' }}>
          搜索追溯
        </button>
        <button onClick={() => setTab('agent')}
          style={{ padding: '6px 20px', background: tab === 'agent' ? '#3182ce' : '#e2e8f0', color: tab === 'agent' ? '#fff' : '#333', border: 'none', borderRadius: '6px', cursor: 'pointer' }}>
          Agent 分析
        </button>
      </div>
      {tab === 'trace' && (
        <>
          <SearchBar onSearch={setTimeline} />
          {timeline && <Timeline data={timeline} />}
        </>
      )}
      {tab === 'agent' && <AgentPanel />}
    </div>
  )
}

export default App
