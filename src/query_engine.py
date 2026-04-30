"""
Query engine: dato un input utente, recupera i chunk più rilevanti
da ChromaDB e li passa a Mistral come contesto.

Flusso:
  query (str) → embedding → ChromaDB cosine search (top-k)
  → prompt costruito con contesto → Mistral via Ollama REST API
  → risposta + fonti citate
"""
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path

from sentence_transformers import SentenceTransformer

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    OLLAMA_BASE_URL, OLLAMA_MODEL, TOP_K_RESULTS,
    EMBEDDING_MODEL, CHROMA_COLLECTION
)
from src.ingestion import get_chroma_collection
from src.logger import get_logger

log = get_logger("query")


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def retrieve(query: str, collection, model: SentenceTransformer) -> list[dict]:
    """
    Cerca i TOP_K chunk più semanticamente vicini alla query.
    Ritorna lista di dict con testo e metadati.
    """
    query_vec = model.encode(
        [query],
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).tolist()

    results = collection.query(
        query_embeddings=query_vec,
        n_results=TOP_K_RESULTS,
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        chunks.append({
            "text": doc,
            "source": meta.get("source", "unknown"),
            "score": round(1 - dist, 4),  # distanza cosine → similarità
        })

    return chunks


# ---------------------------------------------------------------------------
# Costruzione prompt
# ---------------------------------------------------------------------------

def build_prompt(query: str, chunks: list[dict]) -> str:
    """
    Costruisce il prompt per Mistral con contesto RAG iniettato.
    Il formato esplicito delle fonti migliora la capacità di citazione.
    """
    context_blocks = []
    for i, chunk in enumerate(chunks, 1):
        context_blocks.append(
            f"[Fonte {i}: {chunk['source']} | rilevanza: {chunk['score']}]\n{chunk['text']}"
        )

    context = "\n\n---\n\n".join(context_blocks)

    prompt = f"""Sei un esperto di cybersecurity. Rispondi in modo preciso e dettagliato.
Usa le fonti fornite come riferimento primario. Se le fonti non contengono informazioni
sufficienti, dillo esplicitamente invece di inventare.

FONTI DI CONTESTO:
{context}

---

DOMANDA: {query}

RISPOSTA:"""

    return prompt


# ---------------------------------------------------------------------------
# Chiamata Ollama
# ---------------------------------------------------------------------------

def call_ollama(prompt: str) -> str:
    """
    Chiama l'API REST di Ollama in modalità non-streaming.
    Gestione esplicita degli errori di rete e risposta malformata.
    """
    url = f"{OLLAMA_BASE_URL}/api/generate"
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,   # bassa per risposte più precise e meno allucinatorie
            "top_p": 0.9,
            "num_ctx": 4096,      # context window
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body.get("response", "").strip()

    except urllib.error.URLError as e:
        log.error(f"Ollama non raggiungibile: {e}")
        return "[ERRORE] Ollama non risponde. Controlla che sia attivo con: ollama serve"

    except json.JSONDecodeError as e:
        log.error(f"Risposta Ollama non valida: {e}")
        return "[ERRORE] Risposta malformata da Ollama."


# ---------------------------------------------------------------------------
# Query engine principale
# ---------------------------------------------------------------------------

class RAGQueryEngine:
    """
    Interfaccia principale del sistema RAG.
    Carica modello e collection una sola volta, riutilizza per tutte le query.
    """

    def __init__(self):
        log.info(f"Carico embedding model: {EMBEDDING_MODEL}")
        self.model = SentenceTransformer(EMBEDDING_MODEL, device="cpu")
        self.collection = get_chroma_collection()
        doc_count = self.collection.count()
        log.info(f"Collection '{CHROMA_COLLECTION}' caricata: {doc_count} chunk indicizzati")

        if doc_count == 0:
            log.warning("La collection è vuota. Esegui prima l'ingestion dei PDF.")

    def query(self, user_query: str) -> dict:
        """
        Esegue una query RAG completa.
        Ritorna dict con risposta e fonti usate per trasparenza.
        """
        if not user_query or not user_query.strip():
            return {"response": "Query vuota.", "sources": []}

        # Input validation: lunghezza massima ragionevole
        user_query = user_query.strip()[:2000]

        log.info(f"Query: {user_query[:80]}...")

        chunks = retrieve(user_query, self.collection, self.model)
        if not chunks:
            return {
                "response": "Nessun documento rilevante trovato nella knowledge base.",
                "sources": []
            }

        prompt = build_prompt(user_query, chunks)
        response = call_ollama(prompt)

        sources = list({c["source"] for c in chunks})  # deduplicazione fonti

        return {
            "response": response,
            "sources": sources,
            "chunks_used": len(chunks),
        }
