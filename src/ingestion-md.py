"""
Ingestion pipeline: Markdown → testo → chunk → embedding → ChromaDB.

Flusso dati:
  Markdown (disco) → parsing (strip markdown) → RecursiveCharacterTextSplitter
  → sentence-transformers (vettori R^768) → ChromaDB (HNSW index persistente)
"""

import hashlib
import sys
from pathlib import Path
from typing import Generator

import chromadb
from chromadb.config import Settings as ChromaSettings
from sentence_transformers import SentenceTransformer
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Aggiungo il root al path per import config/src
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    CHROMA_DIR, CHUNK_SIZE, CHUNK_OVERLAP,
    EMBEDDING_MODEL, CHROMA_COLLECTION
)
from src.logger import get_logger

log = get_logger("ingestion-md")


# ---------------------------------------------------------------------------
# Estrazione testo Markdown
# ---------------------------------------------------------------------------

def extract_text_from_md(md_path: Path) -> str:
    """
    Estrae testo da file Markdown.
    Rimuove elementi markdown basilari per migliorare embedding quality.
    """
    try:
        text = md_path.read_text(encoding="utf-8", errors="ignore")

        # Rimozione basilare markdown (manteniamo contenuto semantico)
        replacements = [
            ("#", ""), ("*", ""), ("`", ""),
            (">", ""), ("-", ""), ("_", " ")
        ]
        for old, new in replacements:
            text = text.replace(old, new)

        # rimuovi blocchi codice (semplice)
        lines = []
        in_code_block = False
        for line in text.splitlines():
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                continue
            if not in_code_block:
                lines.append(line)

        clean_text = "\n".join(lines)

        if not clean_text.strip():
            log.warning(f"Markdown vuoto: {md_path.name}")
            return ""

        return clean_text

    except Exception as e:
        log.error(f"Errore lettura {md_path.name}: {e}")
        return ""


# ---------------------------------------------------------------------------
# Chunking (IDENTICO)
# ---------------------------------------------------------------------------

def chunk_text(text: str, source: str) -> list[dict]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_text(text)

    result = []
    for i, chunk in enumerate(chunks):
        chunk = chunk.strip()
        if len(chunk) < 50:
            continue
        result.append({
            "text": chunk,
            "source": source,
            "chunk_index": i,
        })

    return result


# ---------------------------------------------------------------------------
# ID deterministico (IDENTICO)
# ---------------------------------------------------------------------------

def make_doc_id(source: str, chunk_index: int, text: str) -> str:
    raw = f"{source}::{chunk_index}::{text[:100]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# ChromaDB (IDENTICO)
# ---------------------------------------------------------------------------

def get_chroma_collection() -> chromadb.Collection:
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(
        path=str(CHROMA_DIR),
        settings=ChromaSettings(anonymized_telemetry=False),
    )

    return client.get_or_create_collection(
        name=CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


# ---------------------------------------------------------------------------
# Embedding (IDENTICO)
# ---------------------------------------------------------------------------

def embed_chunks(
    chunks: list[dict],
    model: SentenceTransformer,
    batch_size: int = 64,
) -> Generator[tuple[list, list, list], None, None]:

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
            normalize_embeddings=True,
        ).tolist()

        yield ids, embeddings, metadatas, texts


# ---------------------------------------------------------------------------
# Ingestion singolo file
# ---------------------------------------------------------------------------

def ingest_md(md_path: Path, collection, model) -> int:
    log.info(f"Processing: {md_path.name}")

    text = extract_text_from_md(md_path)
    if not text:
        return 0

    chunks = chunk_text(text, source=md_path.name)
    if not chunks:
        log.warning(f"Nessun chunk valido da: {md_path.name}")
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

    log.info(f"  → {total_indexed} chunk indicizzati da {md_path.name}")
    return total_indexed


# ---------------------------------------------------------------------------
# Directory ingestion
# ---------------------------------------------------------------------------

def ingest_directory(md_dir: Path) -> None:
    md_files = list(md_dir.rglob("*.md"))

    if not md_files:
        log.warning(f"Nessun file .md trovato in: {md_dir}")
        return

    log.info(f"Trovati {len(md_files)} markdown da indicizzare")
    log.info(f"Carico embedding model: {EMBEDDING_MODEL}")

    model = SentenceTransformer(EMBEDDING_MODEL, device="cpu")
    collection = get_chroma_collection()

    total = 0
    failed = 0

    for md_path in md_files:
        count = ingest_md(md_path, collection, model)
        if count == 0:
            failed += 1
        else:
            total += count

    log.info(f"Ingestion completata: {total} chunk totali, {failed} file falliti su {len(md_files)}")
    log.info(f"Collection size: {collection.count()} documenti")