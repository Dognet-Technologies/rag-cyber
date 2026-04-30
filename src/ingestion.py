"""
Ingestion pipeline: PDF → testo → chunk → embedding → ChromaDB.

Flusso dati:
  PDF (disco) → PyMuPDF (estrazione testo) → RecursiveCharacterTextSplitter
  → sentence-transformers (vettori R^768) → ChromaDB (HNSW index persistente)

Complessità:
  - Estrazione testo:  O(n_pagine)
  - Chunking:          O(n_caratteri)
  - Embedding:         O(n_chunk) — batch processing per efficienza GPU
  - Indexing HNSW:     O(n_chunk * log n_chunk) insert, O(log n) query
"""
import hashlib
import sys
from pathlib import Path
from typing import Generator

import fitz  # PyMuPDF
import chromadb
from chromadb.config import Settings as ChromaSettings
from sentence_transformers import SentenceTransformer
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Aggiungo il root al path per import config/src
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    CHROMA_DIR, CHUNK_SIZE, CHUNK_OVERLAP,
    EMBEDDING_MODEL, CHROMA_COLLECTION, TOP_K_RESULTS
)
from src.logger import get_logger

log = get_logger("ingestion")


# ---------------------------------------------------------------------------
# Estrazione testo
# ---------------------------------------------------------------------------

def extract_text_from_pdf(pdf_path: Path) -> str:
    """
    Estrae testo da PDF nativo digitale con PyMuPDF.
    Gestisce PDF malformati senza crashare l'intera pipeline.
    """
    try:
        doc = fitz.open(str(pdf_path))
        pages = []
        for page in doc:
            text = page.get_text("text")
            if text.strip():
                pages.append(text)
        doc.close()

        full_text = "\n".join(pages)
        if not full_text.strip():
            log.warning(f"PDF vuoto o non estraibile: {pdf_path.name}")
            return ""
        return full_text

    except Exception as e:
        log.error(f"Errore estrazione {pdf_path.name}: {e}")
        return ""


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str, source: str) -> list[dict]:
    """
    Divide il testo in chunk con overlap.
    Usa RecursiveCharacterTextSplitter che rispetta struttura paragrafi
    prima di spezzare su caratteri arbitrari.

    Ritorna lista di dict con testo e metadata.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_text(text)

    result = []
    for i, chunk in enumerate(chunks):
        chunk = chunk.strip()
        if len(chunk) < 50:  # scarta chunk troppo corti (rumore)
            continue
        result.append({
            "text": chunk,
            "source": source,
            "chunk_index": i,
        })

    return result


# ---------------------------------------------------------------------------
# ID deterministico per deduplicazione
# ---------------------------------------------------------------------------

def make_doc_id(source: str, chunk_index: int, text: str) -> str:
    """
    ID deterministico basato su hash SHA256 del contenuto.
    Permette di reingestire senza duplicati (idempotente).
    """
    raw = f"{source}::{chunk_index}::{text[:100]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Client ChromaDB
# ---------------------------------------------------------------------------

def get_chroma_collection() -> chromadb.Collection:
    """
    Ritorna la collection ChromaDB persistente.
    Crea directory e collection se non esistono.
    """
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(
        path=str(CHROMA_DIR),
        settings=ChromaSettings(anonymized_telemetry=False),
    )

    collection = client.get_or_create_collection(
        name=CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},  # cosine similarity per testi
    )
    return collection


# ---------------------------------------------------------------------------
# Embedding batch
# ---------------------------------------------------------------------------

def embed_chunks(
    chunks: list[dict],
    model: SentenceTransformer,
    batch_size: int = 64,
) -> Generator[tuple[list, list, list], None, None]:
    """
    Genera embedding in batch per efficienza memoria.
    Yield: (ids, embeddings, metadatas) per batch.

    Batch size 64 è il trade-off ottimale per RTX 4050 6GB VRAM.
    """
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        texts = [c["text"] for c in batch]
        ids = [make_doc_id(c["source"], c["chunk_index"], c["text"]) for c in batch]
        metadatas = [{"source": c["source"], "chunk_index": c["chunk_index"]} for c in batch]

        embeddings = model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,  # normalizzazione L2 ottimale per cosine
        ).tolist()

        yield ids, embeddings, metadatas, texts


# ---------------------------------------------------------------------------
# Ingestion principale
# ---------------------------------------------------------------------------

def ingest_pdf(pdf_path: Path, collection: chromadb.Collection, model: SentenceTransformer) -> int:
    """
    Processa un singolo PDF: estrazione → chunking → embedding → upsert ChromaDB.
    Ritorna il numero di chunk indicizzati (0 se fallisce).

    Usa upsert (non insert) per idempotenza: reingestire lo stesso file
    non crea duplicati.
    """
    log.info(f"Processing: {pdf_path.name}")

    text = extract_text_from_pdf(pdf_path)
    if not text:
        return 0

    chunks = chunk_text(text, source=pdf_path.name)
    if not chunks:
        log.warning(f"Nessun chunk valido da: {pdf_path.name}")
        return 0

    total_indexed = 0
    for ids, embeddings, metadatas, texts in embed_chunks(chunks, model):
        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=texts,
        )
        total_indexed += len(ids)

    log.info(f"  → {total_indexed} chunk indicizzati da {pdf_path.name}")
    return total_indexed


def ingest_directory(pdf_dir: Path) -> None:
    """
    Indicizza tutti i PDF in una directory (ricorsivo).
    Carica il modello di embedding una sola volta per efficienza.
    """
    pdf_files = list(pdf_dir.rglob("*.pdf"))
    if not pdf_files:
        log.warning(f"Nessun PDF trovato in: {pdf_dir}")
        return

    log.info(f"Trovati {len(pdf_files)} PDF da indicizzare")
    log.info(f"Carico embedding model: {EMBEDDING_MODEL}")

    model = SentenceTransformer(EMBEDDING_MODEL, device="cpu")
    collection = get_chroma_collection()

    total = 0
    failed = 0
    for pdf_path in pdf_files:
        count = ingest_pdf(pdf_path, collection, model)
        if count == 0:
            failed += 1
        else:
            total += count

    log.info(f"Ingestion completata: {total} chunk totali, {failed} PDF falliti su {len(pdf_files)}")
    log.info(f"Collection size: {collection.count()} documenti")
