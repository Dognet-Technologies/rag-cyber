import { useEffect, useRef, useState } from 'react'
import cytoscape from 'cytoscape'
import { streamGraph } from '../api.js'

/**
 * GraphPanel — visualizzazione grafo Neo4j con Cytoscape.js.
 *
 * Riceve aggiornamenti real-time via SSE (streamGraph).
 * Nodi colorati per livello heading:
 *   Document → ambra
 *   Section L1 → verde
 *   Section L2 → blu
 *   Section L3 → viola
 *
 * Layout: cose (forza diretta) — mostra cluster naturali per documento.
 * Non interattivo per navigazione (come richiesto) ma pan/zoom abilitati
 * per leggere i titoli dei nodi.
 *
 * Props:
 *   liveNodes: array di eventi {type, node_id, title, level, source}
 *              ricevuti durante l'ingestion SSE — per aggiornamento immediato
 *              prima che il polling SSE del grafo aggiorni.
 */
export default function GraphPanel({ liveNodes = [] }) {
  const containerRef = useRef(null)
  const cyRef        = useRef(null)
  const [nodeCount, setNodeCount] = useState(0)
  const [edgeCount, setEdgeCount] = useState(0)

  // ---------------------------------------------------------------------------
  // Init Cytoscape
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!containerRef.current) return

    cyRef.current = cytoscape({
      container: containerRef.current,
      elements: [],
      style: cytoscapeStyle,
      layout: { name: 'preset' },
      userZoomingEnabled: true,
      userPanningEnabled: true,
      boxSelectionEnabled: false,
      autoungrabify: true,   // nodi non spostabili — solo feedback visivo
    })

    return () => {
      cyRef.current?.destroy()
      cyRef.current = null
    }
  }, [])

  // ---------------------------------------------------------------------------
  // Stream SSE grafo (polling ogni 2s dal backend)
  // ---------------------------------------------------------------------------
  useEffect(() => {
    const abort = streamGraph(({ nodes, edges }) => {
      if (!cyRef.current) return
      updateCytoscape(cyRef.current, nodes, edges)
      setNodeCount(nodes.length)
      setEdgeCount(edges.length)
    })
    return abort
  }, [])

  // ---------------------------------------------------------------------------
  // Aggiornamento live durante ingestion SSE
  // Aggiunge nodi immediatamente senza aspettare il polling
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!cyRef.current || liveNodes.length === 0) return
    const last = liveNodes[liveNodes.length - 1]
    if (!last?.node_id) return

    const cy = cyRef.current
    if (!cy.getElementById(last.node_id).length) {
      const color = nodeColor(last.level, last.type)
      cy.add({
        group: 'nodes',
        data: {
          id:    last.node_id,
          label: last.title?.slice(0, 28) || last.node_id.slice(0, 8),
          level: last.level ?? 0,
          color,
        },
      })
      runLayout(cy)
      setNodeCount(cy.nodes().length)
    }
  }, [liveNodes])

  return (
    <div style={styles.wrapper}>
      {/* Header */}
      <div style={styles.header}>
        <span style={styles.headerTitle}>KNOWLEDGE GRAPH</span>
        <div style={styles.stats}>
          <Stat label="nodes" value={nodeCount} color="var(--accent-green)" />
          <Stat label="edges" value={edgeCount} color="var(--accent-blue)" />
        </div>
      </div>

      {/* Canvas Cytoscape */}
      <div ref={containerRef} style={styles.canvas} />

      {/* Legenda */}
      <div style={styles.legend}>
        <LegendItem color="var(--node-doc)" label="Document" />
        <LegendItem color="var(--node-l1)"  label="H1" />
        <LegendItem color="var(--node-l2)"  label="H2" />
        <LegendItem color="var(--node-l3)"  label="H3" />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Aggiorna Cytoscape con diff minimo (no full re-render)
// ---------------------------------------------------------------------------
function updateCytoscape(cy, nodes, edges) {
  const existingNodeIds = new Set(cy.nodes().map(n => n.id()))
  const existingEdgeIds = new Set(cy.edges().map(e => e.id()))

  const toAdd = []

  for (const n of nodes) {
    if (!existingNodeIds.has(n.id)) {
      toAdd.push({
        group: 'nodes',
        data: {
          id:    n.id,
          label: n.title?.slice(0, 28) || n.id.slice(0, 8),
          level: n.level ?? 0,
          color: nodeColor(n.level, 'section'),
        },
      })
    }
  }

  for (const e of edges) {
    const eid = `${e.source}->${e.target}`
    if (!existingEdgeIds.has(eid)) {
      toAdd.push({
        group: 'edges',
        data: { id: eid, source: e.source, target: e.target },
      })
    }
  }

  if (toAdd.length > 0) {
    cy.add(toAdd)
    runLayout(cy)
  }
}

function runLayout(cy) {
  if (cy.nodes().length === 0) return
  cy.layout({
    name: 'cose',
    animate: true,
    animationDuration: 400,
    randomize: false,
    nodeRepulsion: () => 4500,
    idealEdgeLength: () => 50,
    edgeElasticity: () => 100,
    gravity: 0.4,
    numIter: 500,
    fit: true,
    padding: 20,
  }).run()
}

function nodeColor(level, type) {
  if (type === 'document') return 'var(--node-doc)'
  if (level === 1) return 'var(--node-l1)'
  if (level === 2) return 'var(--node-l2)'
  return 'var(--node-l3)'
}

// ---------------------------------------------------------------------------
// Cytoscape stylesheet
// ---------------------------------------------------------------------------
const cytoscapeStyle = [
  {
    selector: 'node',
    style: {
      'width': 18,
      'height': 18,
      'background-color': 'data(color)',
      'label': 'data(label)',
      'color': '#c9d8e8',
      'font-size': 8,
      'font-family': 'JetBrains Mono, monospace',
      'text-valign': 'bottom',
      'text-halign': 'center',
      'text-margin-y': 4,
      'text-outline-color': '#080b0f',
      'text-outline-width': 2,
      'border-width': 1,
      'border-color': 'data(color)',
      'border-opacity': 0.4,
    },
  },
  {
    selector: 'edge',
    style: {
      'width': 1,
      'line-color': '#1e2d3d',
      'target-arrow-color': '#1e2d3d',
      'target-arrow-shape': 'triangle',
      'curve-style': 'bezier',
      'arrow-scale': 0.6,
      'opacity': 0.6,
    },
  },
]

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------
function Stat({ label, value, color }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <span style={{ color, fontSize: 11, fontWeight: 700 }}>{value}</span>
      <span style={{ color: 'var(--text-dim)', fontSize: 10 }}>{label}</span>
    </div>
  )
}

function LegendItem({ color, label }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <div style={{
        width: 8, height: 8, borderRadius: '50%',
        background: color, flexShrink: 0,
      }} />
      <span style={{ color: 'var(--text-dim)', fontSize: 10 }}>{label}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------
const styles = {
  wrapper: {
    display: 'flex',
    flexDirection: 'column',
    background: 'var(--bg-surface)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-lg)',
    overflow: 'hidden',
    flex: '1 1 0',
    minHeight: 0,
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '8px 12px',
    borderBottom: '1px solid var(--border)',
    background: 'var(--bg-elevated)',
    flexShrink: 0,
  },
  headerTitle: {
    fontFamily: 'var(--font-display)',
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.15em',
    color: 'var(--text-secondary)',
  },
  stats: {
    display: 'flex',
    gap: 12,
  },
  canvas: {
    flex: 1,
    minHeight: 0,
    background: 'var(--bg-base)',
  },
  legend: {
    display: 'flex',
    gap: 12,
    padding: '6px 12px',
    borderTop: '1px solid var(--border)',
    background: 'var(--bg-elevated)',
    flexShrink: 0,
  },
}
