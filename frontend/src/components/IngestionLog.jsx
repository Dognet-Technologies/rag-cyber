import { useEffect, useRef } from 'react'

/**
 * IngestionLog — card medio-destra che mostra i log real-time dell'ingestion.
 *
 * Props:
 *   events: array di oggetti evento SSE dall'ingestion MD
 */
export default function IngestionLog({ events = [] }) {
  const bottomRef = useRef(null)

  // Auto-scroll ai nuovi log
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  return (
    <div style={styles.wrapper}>
      <div style={styles.header}>
        <span style={styles.title}>INGESTION LOG</span>
        <span style={styles.count}>{events.length} eventi</span>
      </div>

      <div style={styles.log}>
        {events.length === 0 && (
          <div style={styles.empty}>
            Nessuna attività — premi + per importare file MD
          </div>
        )}
        {events.map((ev, i) => (
          <LogLine key={i} event={ev} />
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// LogLine — singola riga log con colore per tipo evento
// ---------------------------------------------------------------------------
function LogLine({ event }) {
  const { type, ...rest } = event
  const color = eventColor(type)
  const text  = formatEvent(event)

  return (
    <div style={{ ...styles.line, borderLeftColor: color }}>
      <span style={{ ...styles.tag, color }}>{type.toUpperCase()}</span>
      <span style={styles.text}>{text}</span>
    </div>
  )
}

function eventColor(type) {
  switch (type) {
    case 'start':        return 'var(--accent-amber)'
    case 'document':     return 'var(--node-doc)'
    case 'section':      return 'var(--node-l2)'
    case 'indexed':      return 'var(--accent-green)'
    case 'done':         return 'var(--accent-green)'
    case 'batch_start':  return 'var(--accent-amber)'
    case 'batch_done':   return 'var(--accent-green)'
    case 'error':        return 'var(--accent-red)'
    default:             return 'var(--text-dim)'
  }
}

function formatEvent(ev) {
  switch (ev.type) {
    case 'start':
      return `Avvio ingestion: ${ev.file}`
    case 'document':
      return `Documento: ${ev.title}`
    case 'section':
      return `${'  '.repeat((ev.level || 1) - 1)}H${ev.level} ${ev.title}`
    case 'indexed':
      return `ChromaDB: ${ev.chunks} chunk indicizzati (${ev.source})`
    case 'done':
      return `Completato: ${ev.file} — ${ev.chunks} chunk`
    case 'batch_start':
      return `Batch: ${ev.total_files} file trovati in ${ev.directory}`
    case 'batch_done':
      return `Batch completato: ${ev.total_files} file, ${ev.total_chunks} chunk totali`
    case 'error':
      return `Errore: ${ev.message}`
    default:
      return JSON.stringify(ev)
  }
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
    flex: '0 0 220px',
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
  count: {
    fontSize: 10,
    color: 'var(--text-dim)',
  },
  log: {
    flex: 1,
    overflowY: 'auto',
    padding: '8px 0',
    display: 'flex',
    flexDirection: 'column',
    gap: 1,
  },
  empty: {
    color: 'var(--text-dim)',
    fontSize: 11,
    padding: '12px 12px',
    fontStyle: 'italic',
  },
  line: {
    display: 'flex',
    gap: 8,
    padding: '3px 12px',
    borderLeft: '2px solid transparent',
    alignItems: 'baseline',
  },
  tag: {
    fontSize: 9,
    fontWeight: 700,
    letterSpacing: '0.08em',
    flexShrink: 0,
    width: 52,
  },
  text: {
    fontSize: 11,
    color: 'var(--text-secondary)',
    whiteSpace: 'pre',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
}
