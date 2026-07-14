import { useState } from "react";
import SearchBar from "./components/SearchBar"
import Timeline from "./components/Timeline"

function App() {
  const [timeline, setTimeline] = useState(null)

  return (
    <div>
      <h1 style={{ textAlign: 'center' }}>CodeTrace</h1>
      <SearchBar onSearch={setTimeline} />
      {timeline && <Timeline data={timeline} />}
    </div>
  )
}

export default App