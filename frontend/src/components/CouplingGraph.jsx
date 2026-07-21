import { useRef, useEffect } from 'react'
import { Network } from 'vis-network'
import { DataSet } from 'vis-data'

const PALETTE = [
  '#3182ce', '#e53e3e', '#38a169', '#d69e2e', '#805ad5',
  '#dd6b20', '#319795', '#d53f8c', '#2b6cb0', '#2f855a',
]

function getModuleColors(nodes) {
  const modules = [...new Set(nodes.map(n => n.module))]
  const map = {}
  modules.forEach((m, i) => {
    map[m] = {
      background: PALETTE[i % PALETTE.length],
      border: '#ffffff',
      highlight: { background: PALETTE[i % PALETTE.length], border: '#ffffff' },
    }
  })
  return { modules, colorMap: map }
}

function CouplingGraph({ nodes, edges }) {
  const containerRef = useRef(null)
  const networkRef = useRef(null)

  useEffect(() => {
    if (!containerRef.current || !nodes || nodes.length === 0) return

    const { modules, colorMap } = getModuleColors(nodes)

    const dsNodes = new DataSet(nodes.map(n => ({
      id: n.id,
      label: n.label,
      group: n.module,
      title: `<b>${n.id}</b><br>
伙伴数: ${n.recent_partners} (曾 ${n.old_partners})<br>
增长率: ${n.coupling_growth}<br>
跨模块共现: ${n.boundary_crossings}<br>
风险: <b>${n.risk}</b><br>
${n.warning ? `<i>⚠ ${n.warning}</i>` : ''}`,
      value: Math.max(5, n.recent_partners),
    })))

    const dsEdges = new DataSet(edges.map(e => ({
      from: e.source,
      to: e.target,
      value: Math.max(1, e.weight),
      title: `共变 ${e.weight} 次`,
    })))

    const options = {
      groups: {},
      nodes: {
        shape: 'dot',
        size: 12,
        scaling: { min: 8, max: 30, label: { enabled: true, min: 8, max: 16 } },
        font: { size: 11, face: 'Arial, sans-serif', color: '#333' },
        borderWidth: 1.5,
      },
      edges: {
        width: 1,
        scaling: { min: 0.5, max: 5 },
        smooth: { type: 'continuous', roundness: 0.2 },
        color: { color: '#94a3b8', hover: '#64748b', highlight: '#475569' },
      },
      physics: {
        solver: 'forceAtlas2Based',
        forceAtlas2Based: {
          gravitationalConstant: -40,
          centralGravity: 0.005,
          springLength: 150,
          springConstant: 0.06,
          damping: 0.4,
        },
        stabilization: { iterations: 100 },
      },
      interaction: {
        hover: true,
        tooltipDelay: 200,
        zoomView: true,
        dragView: true,
        hoverConnectedEdges: true,
      },
      layout: { improvedLayout: true },
    }

    modules.forEach(m => {
      options.groups[m] = { color: colorMap[m] }
    })

    networkRef.current = new Network(
      containerRef.current,
      { nodes: dsNodes, edges: dsEdges },
      options,
    )

    return () => {
      if (networkRef.current) {
        networkRef.current.destroy()
        networkRef.current = null
      }
    }
  }, [nodes, edges])

  return (
    <div
      ref={containerRef}
      style={{
        width: '100%',
        height: '100%',
        border: '1px solid #e2e8f0',
        borderRadius: '8px',
        background: '#fafafa',
      }}
    />
  )
}

export default CouplingGraph
