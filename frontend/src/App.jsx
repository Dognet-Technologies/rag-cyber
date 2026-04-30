import { useState } from 'react'
import ChatPanel    from './components/ChatPanel.jsx'
import GraphPanel   from './components/GraphPanel.jsx'
import IngestionLog from './components/IngestionLog.jsx'
import StatusBar    from './components/StatusBar.jsx'

/**
 * App — layout radice.
 *
 * Layout:
 *   ┌──────────────┬──────────────────────────────────┐
 *   │              │  GraphPanel   (flex: 1 1 0)       │
 *   │  ChatPanel   ├──────────────────────────────────┤
 *   │  (1/3)       │  IngestionLog (flex: 0 0 220px)  │
 *   │              ├──────────────────────────────────┤
 *   │              │  StatusBar    (flex: 0 0 auto)    │
 *   └──────────────┴──────────────────────────────────┘
 *
 * Stato ingestion centralizzato qui — gli eventi SSE arrivano da ChatPanel
 * e vengono distribuiti a GraphPanel (liveNodes) e IngestionLog (events).
 */
export default function App() {
  const [ingestionEvents, setIngestionEvents] = useState([])
  const [liveNodes, setLiveNodes]             = useState([])

  function handleIngestionEvent(event) {
    // Accumula tutti gli eventi per il log
    setIngestionEvents(prev => [...prev, event])

    // Propaga solo i nodi al grafo per l'aggiornamento live
    if (event.type === 'section' || event.type === 'document') {
      setLiveNodes(prev => [...prev, event])
    }
  }

  function handleIngestionDone() {
    // Nessuna azione aggiuntiva necessaria —
    // il GraphPanel si aggiorna via SSE polling autonomamente
  }

  return (
    <div style={styles.root}>
      {/* Barra titolo */}
      <div style={styles.titlebar}>
        <div style={styles.titleLeft}>
          <span style={styles.logo}>⬡</span>
          <span style={styles.appName}>SENTINELSUITE</span>
          <span style={styles.separator}>·</span>
          <span style={styles.appSub}>LLMWiki</span>
        </div>
        <div style={styles.titleRight}>
          <div style={styles.trafficLight} data-color="red"   />
          <div style={styles.trafficLight} data-color="amber" />
          <div style={styles.trafficLight} data-color="green" />
        </div>
      </div>

      {/* Layout principale */}
      <div style={styles.main}>
        {/* Pannello sinistro — 1/3 */}
        <div style={styles.left}>
          <ChatPanel
            onIngestionEvent={handleIngestionEvent}
            onIngestionDone={handleIngestionDone}
          />
        </div>

        {/* Pannello destro — 2/3 */}
        <div style={styles.right}>
          <GraphPanel liveNodes={liveNodes} />
          <IngestionLog events={ingestionEvents} />
          <StatusBar />
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------
const styles = {
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100vh',
    background: 'var(--bg-base)',
    overflow: 'hidden',
  },
  titlebar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '0 16px',
    height: 36,
    background: 'var(--bg-elevated)',
    borderBottom: '1px solid var(--border)',
    flexShrink: 0,
  },
  titleLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  logo: {
    fontSize: 14,
    color: 'var(--accent-green)',
    lineHeight: 1,
  },
  appName: {
    fontFamily: 'var(--font-display)',
    fontSize: 12,
    fontWeight: 800,
    letterSpacing: '0.2em',
    color: 'var(--text-primary)',
  },
  separator: {
    color: 'var(--text-dim)',
    fontSize: 12,
  },
  appSub: {
    fontFamily: 'var(--font-display)',
    fontSize: 11,
    fontWeight: 400,
    letterSpacing: '0.1em',
    color: 'var(--text-secondary)',
  },
  titleRight: {
    display: 'flex',
    gap: 6,
    alignItems: 'center',
  },
  trafficLight: {
    width: 10,
    height: 10,
    borderRadius: '50%',
    background: 'var(--border-bright)',
  },
  main: {
    flex: 1,
    display: 'flex',
    overflow: 'hidden',
    gap: 0,
  },
  left: {
    width: '33.333%',
    flexShrink: 0,
    overflow: 'hidden',
  },
  right: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    padding: '8px 8px 8px 8px',
    overflow: 'hidden',
    minHeight: 0,
  },
}
