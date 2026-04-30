import { useState, useEffect } from 'react'
import { fetchStatus } from '../api.js'

/**
 * StatusBar — card in basso a destra con stato sistema.
 * Polling ogni 5s — non sincrono, non blocca il thread principale.
 *
 * Mostra: CPU · RAM · GPU · ChromaDB · Neo4j · Ollama · Modello attivo
 */
export default function StatusBar() {
  const [status, setStatus]   = useState(null)
  const [error, setError]     = useState(false)
  const [lastUpdate, setLastUpdate] = useState(null)

  useEffect(() => {
    let cancelled = false

    async function poll() {
      try {
        const s = await fetchStatus()
        if (!cancelled) {
          setStatus(s)
          setError(false)
          setLastUpdate(new Date())
        }
      } catch {
        if (!cancelled) setError(true)
      }
    }

    poll()
    const interval = setInterval(poll, 5000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  return (
    <div style={styles.wrapper}>
      <div style={styles.header}>
        <span style={styles.title}>SYSTEM STATUS</span>
        {lastUpdate && (
          <span style={styles.updated}>
            {lastUpdate.toLocaleTimeString()}
          </span>
        )}
      </div>

      <div style={styles.grid}>
        {error || !status ? (
          <div style={styles.offline}>Backend non raggiungibile</div>
        ) : (
          <>
            <StatItem
              label="CPU"
              value={`${status.cpu_percent.toFixed(1)}%`}
              color={status.cpu_percent > 80 ? 'var(--accent-red)' : 'var(--accent-green)'}
              bar={status.cpu_percent}
            />
            <StatItem
              label="RAM"
              value={`${status.ram_used_gb}/${status.ram_total_gb} GB`}
              color="var(--accent-blue)"
              bar={(status.ram_used_gb / status.ram_total_gb) * 100}
            />
            <StatItem
              label="GPU"
              value={status.gpu_available ? 'RTX 4050' : 'N/A'}
              color={status.gpu_available ? 'var(--accent-green)' : 'var(--text-dim)'}
            />
            <StatItem
              label="ChromaDB"
              value={`${status.chroma_docs.toLocaleString()} docs`}
              color="var(--accent-amber)"
            />
            <StatItem
              label="Neo4j"
              value={status.neo4j_ok ? 'online' : 'offline'}
              color={status.neo4j_ok ? 'var(--accent-green)' : 'var(--accent-red)'}
            />
            <StatItem
              label="Ollama"
              value={status.active_model}
              color="var(--accent-blue)"
            />
          </>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// StatItem
// ---------------------------------------------------------------------------
function StatItem({ label, value, color, bar }) {
  return (
    <div style={styles.item}>
      <div style={styles.itemHeader}>
        <span style={styles.itemLabel}>{label}</span>
        <span style={{ ...styles.itemValue, color }}>{value}</span>
      </div>
      {bar !== undefined && (
        <div style={styles.barTrack}>
          <div style={{
            ...styles.barFill,
            width: `${Math.min(bar, 100)}%`,
            background: color,
          }} />
        </div>
      )}
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
    flex: '0 0 auto',
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
  title: {
    fontFamily: 'var(--font-display)',
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.15em',
    color: 'var(--text-secondary)',
  },
  updated: {
    fontSize: 10,
    color: 'var(--text-dim)',
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(3, 1fr)',
    gap: 1,
    background: 'var(--border)',
    padding: 0,
  },
  offline: {
    gridColumn: '1 / -1',
    padding: '10px 12px',
    color: 'var(--accent-red)',
    fontSize: 11,
    background: 'var(--bg-surface)',
  },
  item: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    padding: '8px 10px',
    background: 'var(--bg-surface)',
  },
  itemHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: 4,
  },
  itemLabel: {
    fontSize: 9,
    fontWeight: 700,
    letterSpacing: '0.1em',
    color: 'var(--text-dim)',
  },
  itemValue: {
    fontSize: 11,
    fontWeight: 500,
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    maxWidth: 90,
  },
  barTrack: {
    height: 2,
    background: 'var(--bg-elevated)',
    borderRadius: 1,
    overflow: 'hidden',
  },
  barFill: {
    height: '100%',
    borderRadius: 1,
    transition: 'width 0.5s ease',
    opacity: 0.7,
  },
}
