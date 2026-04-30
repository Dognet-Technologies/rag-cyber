"""
FastAPI backend — layer HTTP tra frontend Docker e backend Python locale.

Endpoint:
  POST /query                  esegue una query RAG
  POST /ingest/md              ingestion file MD da MD_STAGING_DIR
  POST /ingest/pdf             ingestion PDF da PDF_STAGING_DIR
  GET  /status                 stato ChromaDB + Neo4j + risorse sistema
  GET  /models                 lista modelli disponibili
  PUT  /settings/model         cambia modello Ollama attivo
  GET  /graph/stream           SSE: aggiornamenti grafo real-time durante ingestion
  GET  /graph                  grafo completo (nodi + archi) per visualizzazione

Nota sicurezza:
  Uso locale — nessuna autenticazione, nessun rate limiting.
  CORS aperto su localhost per comunicazione con il frontend Docker.
  Se mai esposto fuori dalla rete locale, aggiungere APIKeyMiddleware.

Avvio:
  uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import AsyncGenerator

import psutil
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    API_HOST, API_PORT,
    MD_STAGING_DIR, PDF_STAGING_DIR,
    OLLAMA_AVAILABLE_MODELS, OLLAMA_MODEL,
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
    EMBEDDING_MODEL,
)
from src.logger import get_logger

log = get_logger("api")

# ---------------------------------------------------------------------------
# App FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
    title="LLMWiki API",
    description="Backend RAG + grafo per SentinelSuite",
    version="0.2.0",
)

# CORS: aperto su localhost per frontend Docker (porta 3000 default)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Stato globale — modello attivo modificabile a runtime
# ---------------------------------------------------------------------------

_active_model: str = OLLAMA_MODEL


# ---------------------------------------------------------------------------
# Lazy init: modello embedding e collection ChromaDB
# Caricati una sola volta al primo utilizzo, non all'avvio
# (sentence-transformers pesa ~500MB, meglio non bloccare il boot)
# ---------------------------------------------------------------------------

_embed_model = None
_chroma_collection = None
_neo4j_driver = None


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer
        log.info(f"Carico embedding model: {EMBEDDING_MODEL}")
        _embed_model = SentenceTransformer(EMBEDDING_MODEL, device="cpu")
    return _embed_model


def _get_collection():
    global _chroma_collection
    if _chroma_collection is None:
        from src.ingestion import get_chroma_collection
        _chroma_collection = get_chroma_collection()
    return _chroma_collection


def _get_neo4j_driver():
    """
    Restituisce il driver Neo4j o None se non disponibile.
    Non solleva eccezioni — Neo4j è opzionale per le operazioni ChromaDB-only.
    """
    global _neo4j_driver
    if _neo4j_driver is None:
        try:
            from src.neo4j_graph import _get_driver, ensure_schema
            _neo4j_driver = _get_driver()
            ensure_schema(_neo4j_driver)
            log.info("Driver Neo4j inizializzato")
        except Exception as e:
            log.warning(f"Neo4j non disponibile: {e}")
            return None
    return _neo4j_driver


# ---------------------------------------------------------------------------
# Modelli Pydantic per request/response
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


class QueryResponse(BaseModel):
    response: str
    sources: list[str]
    chunks_used: int


class ModelUpdateRequest(BaseModel):
    model: str = Field(..., min_length=1)


class StatusResponse(BaseModel):
    chroma_docs: int
    neo4j_ok: bool
    neo4j_uri: str
    cpu_percent: float
    ram_used_gb: float
    ram_total_gb: float
    gpu_available: bool
    active_model: str


# ---------------------------------------------------------------------------
# Endpoint: query RAG
# ---------------------------------------------------------------------------

@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest):
    """
    Esegue una query RAG completa.
    Usa il modello attivo corrente (_active_model).
    """
    # Aggiorna il modello nel settings runtime prima di istanziare l'engine
    import config.settings as settings_module
    settings_module.OLLAMA_MODEL = _active_model

    try:
        # RAGQueryEngine è sincrono e pesante — eseguiamo in thread pool
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _run_query, request.question)
        return QueryResponse(**result)
    except Exception as e:
        log.error(f"Errore query: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _run_query(question: str) -> dict:
    """Wrapper sincrono per RAGQueryEngine — eseguito nel thread pool."""
    from src.query_engine import RAGQueryEngine
    engine = RAGQueryEngine()
    return engine.query(question)


# ---------------------------------------------------------------------------
# Endpoint: ingestion MD (SSE)
# ---------------------------------------------------------------------------

@app.post("/ingest/md")
async def ingest_md():
    """
    Indicizza tutti i file .md presenti in MD_STAGING_DIR.
    Risposta in streaming SSE — il frontend riceve eventi real-time
    per aggiornare il grafo e il log di status.

    Formato eventi SSE:
      data: {"type": "section", "node_id": "...", "title": "...", "level": 1}
      data: {"type": "done", "file": "example.md", "chunks": 42}
    """
    return StreamingResponse(
        _ingest_md_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _ingest_md_stream() -> AsyncGenerator[str, None]:
    """Genera eventi SSE dall'ingestion MD in modo asincrono."""
    MD_STAGING_DIR.mkdir(parents=True, exist_ok=True)

    collection   = _get_collection()
    embed_model  = _get_embed_model()
    neo4j_driver = _get_neo4j_driver()

    from src.md_parser import ingest_md_directory

    loop = asyncio.get_event_loop()

    # ingest_md_directory è un generatore sincrono — lo iteriamo nel thread pool
    # usando una queue per passare gli eventi al loop asincrono
    queue: asyncio.Queue = asyncio.Queue()

    def _run_ingestion():
        try:
            for event in ingest_md_directory(
                MD_STAGING_DIR, collection, embed_model, neo4j_driver
            ):
                loop.call_soon_threadsafe(queue.put_nowait, event)
        except Exception as e:
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "message": str(e)})
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel

    loop.run_in_executor(None, _run_ingestion)

    while True:
        event = await queue.get()
        if event is None:  # sentinel — ingestion completata
            break
        yield f"data: {json.dumps(event)}\n\n"


# ---------------------------------------------------------------------------
# Endpoint: ingestion PDF
# ---------------------------------------------------------------------------

@app.post("/ingest/pdf")
async def ingest_pdf():
    """
    Indicizza tutti i PDF presenti in PDF_STAGING_DIR.
    Risposta sincrona — i PDF non alimentano Neo4j, solo ChromaDB.
    """
    PDF_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    pdf_files = list(PDF_STAGING_DIR.rglob("*.pdf"))

    if not pdf_files:
        raise HTTPException(status_code=404, detail=f"Nessun PDF trovato in {PDF_STAGING_DIR}")

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _run_pdf_ingestion)
        return result
    except Exception as e:
        log.error(f"Errore ingestion PDF: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _run_pdf_ingestion() -> dict:
    """Wrapper sincrono per ingest_directory — eseguito nel thread pool."""
    from src.ingestion import ingest_directory
    ingest_directory(PDF_STAGING_DIR)
    collection = _get_collection()
    return {"status": "ok", "chroma_docs": collection.count()}


# ---------------------------------------------------------------------------
# Endpoint: status sistema
# ---------------------------------------------------------------------------

@app.get("/status", response_model=StatusResponse)
async def status():
    """
    Ritorna lo stato di tutti i componenti:
    ChromaDB, Neo4j, CPU, RAM, GPU.
    """
    # ChromaDB
    try:
        chroma_docs = _get_collection().count()
    except Exception:
        chroma_docs = -1

    # Neo4j
    neo4j_ok = False
    try:
        driver = _get_neo4j_driver()
        if driver:
            from src.neo4j_graph import check_connection
            neo4j_ok = check_connection(driver)
    except Exception:
        pass

    # Risorse sistema
    cpu_percent  = psutil.cpu_percent(interval=0.5)
    ram          = psutil.virtual_memory()
    ram_used_gb  = round(ram.used / (1024 ** 3), 2)
    ram_total_gb = round(ram.total / (1024 ** 3), 2)

    # GPU — verifica presenza nvidia-smi senza crashare se assente
    gpu_available = False
    try:
        subprocess.run(
            ["nvidia-smi"], capture_output=True, check=True, timeout=3
        )
        gpu_available = True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return StatusResponse(
        chroma_docs=chroma_docs,
        neo4j_ok=neo4j_ok,
        neo4j_uri=NEO4J_URI,
        cpu_percent=cpu_percent,
        ram_used_gb=ram_used_gb,
        ram_total_gb=ram_total_gb,
        gpu_available=gpu_available,
        active_model=_active_model,
    )


# ---------------------------------------------------------------------------
# Endpoint: lista modelli
# ---------------------------------------------------------------------------

@app.get("/models")
async def get_models():
    """Lista modelli disponibili + modello attivo corrente."""
    return {
        "models": OLLAMA_AVAILABLE_MODELS,
        "active": _active_model,
    }


# ---------------------------------------------------------------------------
# Endpoint: cambia modello attivo
# ---------------------------------------------------------------------------

@app.put("/settings/model")
async def set_model(request: ModelUpdateRequest):
    """
    Aggiorna il modello Ollama attivo a runtime.
    Validazione contro la lista statica — rifiuta modelli non dichiarati.
    """
    global _active_model

    if request.model not in OLLAMA_AVAILABLE_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Modello '{request.model}' non in OLLAMA_AVAILABLE_MODELS. "
                   f"Disponibili: {OLLAMA_AVAILABLE_MODELS}",
        )

    _active_model = request.model
    log.info(f"Modello attivo aggiornato: {_active_model}")
    return {"active": _active_model}


# ---------------------------------------------------------------------------
# Endpoint: grafo completo (snapshot)
# ---------------------------------------------------------------------------

@app.get("/graph")
async def get_graph():
    """
    Ritorna nodi e archi del grafo Neo4j.
    Limite 500 nodi / 1000 archi — vedi neo4j_graph.get_full_graph().
    """
    driver = _get_neo4j_driver()
    if not driver:
        raise HTTPException(status_code=503, detail="Neo4j non disponibile")

    try:
        from src.neo4j_graph import get_full_graph
        loop = asyncio.get_event_loop()
        graph = await loop.run_in_executor(None, get_full_graph, driver)
        return graph
    except Exception as e:
        log.error(f"Errore lettura grafo: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Endpoint: SSE stream grafo (per polling frontend)
# ---------------------------------------------------------------------------

@app.get("/graph/stream")
async def graph_stream():
    """
    SSE endpoint per il frontend: emette lo stato corrente del grafo
    ogni 2 secondi durante le operazioni di ingestion.
    Il frontend si connette all'avvio e resta in ascolto.
    """
    return StreamingResponse(
        _graph_poll_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _graph_poll_stream() -> AsyncGenerator[str, None]:
    """Emette snapshot del grafo ogni 2s per aggiornamento real-time."""
    driver = _get_neo4j_driver()
    if not driver:
        yield f"data: {json.dumps({'type': 'error', 'message': 'Neo4j non disponibile'})}\n\n"
        return

    from src.neo4j_graph import get_full_graph

    while True:
        try:
            loop = asyncio.get_event_loop()
            graph = await loop.run_in_executor(None, get_full_graph, driver)
            yield f"data: {json.dumps({'type': 'graph', **graph})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        await asyncio.sleep(2)


# ---------------------------------------------------------------------------
# Avvio diretto (per sviluppo)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host=API_HOST,
        port=API_PORT,
        reload=True,
        log_level="info",
    )
