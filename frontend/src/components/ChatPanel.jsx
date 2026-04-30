import { useState, useRef, useEffect } from 'react'
import { sendQuery, startMdIngestion, fetchModels, setModel } from '../api.js'

/**
 * ChatPanel — pannello sinistro 1/3.
 * Contiene:
 *   - Cronologia messaggi (chat)
 *   - Input domanda + tasto invio
 *   - Tasto "+" per triggherare ingestion MD
 *   - Dropdown selezione modello
 *
 * Props:
 *   onIngestionEvent(event) — propaga eventi SSE ingestion al parent
 *   onIngestionDone()       — segnala fine ingestion al parent
 */
export default function ChatPanel({ onIngestionEvent, onIngestionDone }) {
  const [messages, setMessages]     = useState([])
  const [input, setInput]           = useState('')
  const [loading, setLoading]       = useState(false)
  const [models, setModels]         = useState([])
  const [activeModel, setActiveModel] = useState('')
  const [ingesting, setIngesting]   = useState(false)
  const bottomRef                   = useRef(null)
  const abortIngestionRef           = useRef(null)

  // Carica lista modelli all'avvio
  useEffect(() => {
    fetchModels()
      .then(({ models, active }) => {
        setModels(models)
        setActiveModel(active)
      })
      .catch(() => {})
  }, [])

  // Auto-scroll ai nuovi messaggi
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // ---------------------------------------------------------------------------
  // Invio query
  // ---------------------------------------------------------------------------
  async function handleSubmit(e) {
    e.preventDefault()
    const q = input.trim()
    if (!q || loading) return

    setInput('')
    setMessages(prev => [...prev, { role: 'user', text: q }])
    setLoading(true)

    try {
      const result = await sendQuery(q)
      setMessages(prev => [...prev, {
        role: 'assistant',
        text: result.response,
        sources: result.sources,
        chunks: result.chunks_used,
      }])
    } catch (err) {
      setMessages(prev => [...prev, {
        role: 'error',
        text: err.message,
      }])
    } finally {
      setLoading(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Ingestion MD
  // ---------------------------------------------------------------------------
  function handleIngestion() {
    if (ingesting) {
      // Abort in corso
      abortIngestionRef.current?.()
      setIngesting(false)
      return
    }

    setIngesting(true)
    const abort = startMdIngestion(
      (event) => onIngestionEvent(event),
      () => {
        setIngesting(false)
        onIngestionDone()
      }
    )
    abortIngestionRef.current = abort
  }

  // ---------------------------------------------------------------------------
  // Cambio modello
  // ---------------------------------------------------------------------------
  async function handleModelChange(e) {
    const model = e.target.value
    try {
      await setModel(model)
      setActiveModel(model)
    } catch (err) {
      console.error('Cambio modello fallito:', err.message)
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  return (
    <div style={styles.panel}>
      {/* Header */}
      <div style={styles.header}>
        <span style={styles.headerDot} />
        <span style={styles.headerTitle}>SENTINEL CHAT</span>
      </div>

      {/* Cronologia messaggi */}
      <div style={styles.messages}>
        {messages.length === 0 && (
          <div style={styles.empty}>
            <div style={styles.emptyIcon}>⬡</div>
            <div style={styles.emptyText}>Inizia una conversazione</div>
            <div style={styles.emptyHint}>o importa file MD con il tasto +</div>
          </div>
        )}
        {messages.map((msg, i) => (
          <MessageBubble key={i} msg={msg} />
        ))}
        {loading && <ThinkingIndicator />}
        <div ref={bottomRef} />
      </div>

      {/* Input area */}
      <div style={styles.inputArea}>
        {/* Riga input + tasto + */}
        <form onSubmit={handleSubmit} style={styles.inputRow}>
          <textarea
            style={styles.textarea}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                handleSubmit(e)
              }
            }}
            placeholder="Scrivi una domanda..."
            rows={3}
            disabled={loading}
          />
          <div style={styles.inputButtons}>
            <button
              type="button"
              onClick={handleIngestion}
              style={{
                ...styles.btnPlus,
                ...(ingesting ? styles.btnPlusActive : {}),
              }}
              title={ingesting ? 'Annulla ingestion' : 'Importa file MD da data/md_staging/'}
            >
              {ingesting ? '×' : '+'}
            </button>
            <button
              type="submit"
              style={{
                ...styles.btnSend,
                ...(loading ? styles.btnSendDisabled : {}),
              }}
              disabled={loading}
            >
              ▶
            </button>
          </div>
        </form>

        {/* Selezione modello */}
        <div style={styles.modelRow}>
          <span style={styles.modelLabel}>MODEL</span>
          <select
            value={activeModel}
            onChange={handleModelChange}
            style={styles.modelSelect}
          >
            {models.map(m => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// MessageBubble
// ---------------------------------------------------------------------------
function MessageBubble({ msg }) {
  if (msg.role === 'user') {
    return (
      <div style={styles.bubbleUser}>
        <span style={styles.roleLabel}>YOU</span>
        <div style={styles.bubbleUserText}>{msg.text}</div>
      </div>
    )
  }
  if (msg.role === 'assistant') {
    return (
      <div style={styles.bubbleAssistant}>
        <span style={styles.roleLabelGreen}>AI</span>
        <div style={styles.bubbleAssistantText}>{msg.text}</div>
        {msg.sources?.length > 0 && (
          <div style={styles.sources}>
            <span style={styles.sourcesLabel}>
              {msg.chunks} chunk · {msg.sources.length} fonti
            </span>
            {msg.sources.map((s, i) => (
              <span key={i} style={styles.sourceTag}>{s}</span>
            ))}
          </div>
        )}
      </div>
    )
  }
  if (msg.role === 'error') {
    return (
      <div style={styles.bubbleError}>
        <span style={styles.roleLabelRed}>ERR</span>
        <div style={styles.bubbleErrorText}>{msg.text}</div>
      </div>
    )
  }
  return null
}

// ---------------------------------------------------------------------------
// ThinkingIndicator
// ---------------------------------------------------------------------------
function ThinkingIndicator() {
  return (
    <div style={styles.thinking}>
      <span style={styles.roleLabelGreen}>AI</span>
      <div style={styles.thinkingDots}>
        <span style={{ ...styles.dot, animationDelay: '0ms' }} />
        <span style={{ ...styles.dot, animationDelay: '200ms' }} />
        <span style={{ ...styles.dot, animationDelay: '400ms' }} />
      </div>
      <style>{`
        @keyframes blink {
          0%, 80%, 100% { opacity: 0.2; transform: scale(0.8); }
          40% { opacity: 1; transform: scale(1); }
        }
      `}</style>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------
const styles = {
  panel: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    background: 'var(--bg-surface)',
    borderRight: '1px solid var(--border)',
    overflow: 'hidden',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '12px 16px',
    borderBottom: '1px solid var(--border)',
    background: 'var(--bg-elevated)',
    flexShrink: 0,
  },
  headerDot: {
    width: 6,
    height: 6,
    borderRadius: '50%',
    background: 'var(--accent-green)',
    boxShadow: '0 0 6px var(--accent-green)',
  },
  headerTitle: {
    fontFamily: 'var(--font-display)',
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: '0.15em',
    color: 'var(--text-secondary)',
  },
  messages: {
    flex: 1,
    overflowY: 'auto',
    padding: '16px 12px',
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
  },
  empty: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    padding: '40px 0',
  },
  emptyIcon: {
    fontSize: 32,
    color: 'var(--text-dim)',
  },
  emptyText: {
    color: 'var(--text-secondary)',
    fontSize: 12,
  },
  emptyHint: {
    color: 'var(--text-dim)',
    fontSize: 11,
  },
  bubbleUser: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    alignSelf: 'flex-end',
    maxWidth: '85%',
  },
  bubbleUserText: {
    background: 'var(--bg-elevated)',
    border: '1px solid var(--border-bright)',
    borderRadius: 'var(--radius-lg)',
    padding: '8px 12px',
    color: 'var(--text-primary)',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  },
  bubbleAssistant: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    alignSelf: 'flex-start',
    maxWidth: '95%',
  },
  bubbleAssistantText: {
    background: 'rgba(61, 220, 132, 0.04)',
    border: '1px solid rgba(61, 220, 132, 0.15)',
    borderRadius: 'var(--radius-lg)',
    padding: '8px 12px',
    color: 'var(--text-primary)',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    lineHeight: 1.7,
  },
  bubbleError: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    alignSelf: 'flex-start',
    maxWidth: '90%',
  },
  bubbleErrorText: {
    background: 'rgba(224, 82, 82, 0.06)',
    border: '1px solid rgba(224, 82, 82, 0.2)',
    borderRadius: 'var(--radius-lg)',
    padding: '8px 12px',
    color: 'var(--accent-red)',
    whiteSpace: 'pre-wrap',
  },
  roleLabel: {
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.1em',
    color: 'var(--text-secondary)',
    alignSelf: 'flex-end',
  },
  roleLabelGreen: {
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.1em',
    color: 'var(--accent-green)',
  },
  roleLabelRed: {
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.1em',
    color: 'var(--accent-red)',
  },
  sources: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 4,
    paddingTop: 4,
    alignItems: 'center',
  },
  sourcesLabel: {
    fontSize: 10,
    color: 'var(--text-dim)',
    marginRight: 4,
  },
  sourceTag: {
    fontSize: 10,
    padding: '2px 6px',
    borderRadius: 2,
    background: 'var(--bg-elevated)',
    border: '1px solid var(--border)',
    color: 'var(--text-secondary)',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    maxWidth: 120,
  },
  thinking: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    alignSelf: 'flex-start',
  },
  thinkingDots: {
    display: 'flex',
    gap: 4,
    padding: '10px 14px',
    background: 'rgba(61, 220, 132, 0.04)',
    border: '1px solid rgba(61, 220, 132, 0.15)',
    borderRadius: 'var(--radius-lg)',
  },
  dot: {
    width: 6,
    height: 6,
    borderRadius: '50%',
    background: 'var(--accent-green)',
    animation: 'blink 1.2s infinite ease-in-out',
    display: 'inline-block',
  },
  inputArea: {
    flexShrink: 0,
    padding: '12px',
    borderTop: '1px solid var(--border)',
    background: 'var(--bg-elevated)',
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  inputRow: {
    display: 'flex',
    gap: 8,
    alignItems: 'flex-end',
  },
  textarea: {
    flex: 1,
    background: 'var(--bg-base)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius)',
    color: 'var(--text-primary)',
    fontFamily: 'var(--font-mono)',
    fontSize: 12,
    padding: '8px 10px',
    resize: 'none',
    outline: 'none',
    lineHeight: 1.5,
    transition: 'border-color var(--transition)',
  },
  inputButtons: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  btnPlus: {
    width: 32,
    height: 32,
    borderRadius: 'var(--radius)',
    border: '1px solid var(--border-bright)',
    background: 'var(--bg-base)',
    color: 'var(--accent-amber)',
    fontSize: 18,
    fontWeight: 300,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'all var(--transition)',
    lineHeight: 1,
  },
  btnPlusActive: {
    background: 'rgba(240, 165, 0, 0.1)',
    borderColor: 'var(--accent-amber)',
    color: 'var(--accent-amber)',
  },
  btnSend: {
    width: 32,
    height: 32,
    borderRadius: 'var(--radius)',
    border: '1px solid var(--accent-green)',
    background: 'rgba(61, 220, 132, 0.1)',
    color: 'var(--accent-green)',
    fontSize: 12,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'all var(--transition)',
  },
  btnSendDisabled: {
    opacity: 0.4,
    cursor: 'not-allowed',
  },
  modelRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  modelLabel: {
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.12em',
    color: 'var(--text-dim)',
    flexShrink: 0,
  },
  modelSelect: {
    flex: 1,
    background: 'var(--bg-base)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius)',
    color: 'var(--text-secondary)',
    fontFamily: 'var(--font-mono)',
    fontSize: 11,
    padding: '4px 8px',
    outline: 'none',
    cursor: 'pointer',
  },
}
