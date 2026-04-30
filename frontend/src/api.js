/**
 * api.js — wrapper centralizzato per tutte le chiamate al backend FastAPI.
 * L'URL base viene letto dalla variabile d'ambiente Vite (settata nel docker-compose).
 * Fallback a localhost per sviluppo locale senza Docker.
 */

const BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

// ---------------------------------------------------------------------------
// Query RAG
// ---------------------------------------------------------------------------

/**
 * Invia una domanda al backend RAG.
 * @param {string} question
 * @returns {Promise<{response: string, sources: string[], chunks_used: number}>}
 */
export async function sendQuery(question) {
  const res = await fetch(`${BASE}/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || 'Errore query')
  }
  return res.json()
}

// ---------------------------------------------------------------------------
// Ingestion MD — SSE stream
// ---------------------------------------------------------------------------

/**
 * Avvia l'ingestion dei file MD tramite SSE.
 * Chiama onEvent per ogni evento ricevuto dallo stream.
 * Chiama onDone quando lo stream si chiude.
 * Ritorna una funzione per chiudere la connessione (abort).
 *
 * @param {(event: object) => void} onEvent
 * @param {() => void} onDone
 * @returns {() => void} abort function
 */
export function startMdIngestion(onEvent, onDone) {
  const controller = new AbortController()

  fetch(`${BASE}/ingest/md`, {
    method: 'POST',
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok) throw new Error(`Ingestion fallita: ${res.statusText}`)
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() // tieni l'eventuale riga incompleta

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const event = JSON.parse(line.slice(6))
              onEvent(event)
            } catch {
              // linea malformata — ignora
            }
          }
        }
      }
      onDone()
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        onEvent({ type: 'error', message: err.message })
        onDone()
      }
    })

  return () => controller.abort()
}

// ---------------------------------------------------------------------------
// Status sistema
// ---------------------------------------------------------------------------

/**
 * Recupera lo stato del sistema (ChromaDB, Neo4j, CPU, RAM, GPU).
 * @returns {Promise<object>}
 */
export async function fetchStatus() {
  const res = await fetch(`${BASE}/status`)
  if (!res.ok) throw new Error('Status non disponibile')
  return res.json()
}

// ---------------------------------------------------------------------------
// Modelli disponibili
// ---------------------------------------------------------------------------

/**
 * @returns {Promise<{models: string[], active: string}>}
 */
export async function fetchModels() {
  const res = await fetch(`${BASE}/models`)
  if (!res.ok) throw new Error('Modelli non disponibili')
  return res.json()
}

/**
 * Aggiorna il modello attivo.
 * @param {string} model
 * @returns {Promise<{active: string}>}
 */
export async function setModel(model) {
  const res = await fetch(`${BASE}/settings/model`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || 'Cambio modello fallito')
  }
  return res.json()
}

// ---------------------------------------------------------------------------
// Grafo Neo4j — snapshot
// ---------------------------------------------------------------------------

/**
 * Recupera il grafo completo (nodi + archi) da Neo4j.
 * @returns {Promise<{nodes: object[], edges: object[]}>}
 */
export async function fetchGraph() {
  const res = await fetch(`${BASE}/graph`)
  if (!res.ok) throw new Error('Grafo non disponibile')
  return res.json()
}

// ---------------------------------------------------------------------------
// Grafo Neo4j — SSE stream (polling real-time)
// ---------------------------------------------------------------------------

/**
 * Si connette allo stream SSE del grafo.
 * Chiama onGraph ogni volta che arriva un aggiornamento.
 * Ritorna funzione di abort.
 *
 * @param {(graph: {nodes: object[], edges: object[]}) => void} onGraph
 * @returns {() => void} abort function
 */
export function streamGraph(onGraph) {
  const controller = new AbortController()

  fetch(`${BASE}/graph/stream`, { signal: controller.signal })
    .then(async (res) => {
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const event = JSON.parse(line.slice(6))
              if (event.type === 'graph') {
                onGraph({ nodes: event.nodes, edges: event.edges })
              }
            } catch {
              // ignora
            }
          }
        }
      }
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        console.warn('Graph stream disconnesso:', err.message)
      }
    })

  return () => controller.abort()
}
