"""
Parser MD generico per LLMWiki → ChromaDB + Neo4j.

Estende la logica già presente in bugbounty_parser.py (clean_markdown,
chunk_markdown_by_section) aggiungendo:
  - Estrazione strutturata dell'albero heading per Neo4j
  - ID deterministici per sezioni (compatibili con make_doc_id di ingestion.py)
  - Generatore di eventi per SSE (feedback real-time durante ingestion)
  - Supporto a qualsiasi file .md, non solo bugbounty

Flusso:
  .md file → clean_markdown → parse_heading_tree
           → ChromaDB (embed_chunks, upsert)
           → Neo4j (build_graph_from_sections, yield eventi SSE)

Separazione delle responsabilità:
  - Questo modulo: parsing e coordinamento
  - ingestion.py:  embedding e ChromaDB
  - neo4j_graph.py: costruzione grafo
"""
import hashlib
import re
import sys
from pathlib import Path
from typing import Generator

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.ingestion import get_chroma_collection, embed_chunks
from src.logger import get_logger

log = get_logger("md_parser")


# ---------------------------------------------------------------------------
# Pulizia markdown — riutilizza logica da bugbounty_parser
# ---------------------------------------------------------------------------

def clean_markdown(text: str) -> str:
    """
    Rimuove elementi markdown non utili per il retrieval semantico.
    Identica alla versione in bugbounty_parser — centralizzata qui
    per evitare duplicazione; bugbounty_parser la importerà da qui in futuro.
    """
    # Rimuovi immagini ![alt](url)
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    # Sostituisci link [testo](url) con solo il testo
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    # Rimuovi righe che sono solo badge/shield
    text = re.sub(r'\[!\[.*?\].*?\]', '', text)
    # Comprimi righe vuote multiple
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ---------------------------------------------------------------------------
# Parsing heading tree
# ---------------------------------------------------------------------------

def _make_section_id(source: str, chunk_index: int, title: str) -> str:
    """
    ID deterministico per una sezione.
    Stesso schema di make_doc_id in ingestion.py — SHA256 troncato a 32 char.
    Garantisce idempotenza su reingestione.
    """
    raw = f"section::{source}::{chunk_index}::{title[:80]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _make_doc_id(source: str) -> str:
    """ID deterministico per il nodo Document in Neo4j."""
    raw = f"document::{source}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def parse_heading_tree(text: str, source: str) -> list[dict]:
    """
    Estrae l'albero degli heading da un file MD.

    Ogni sezione ritornata è un dict con:
      {id, title, level (1|2|3), text, source, chunk_index}

    Algoritmo:
      Split sul pattern heading (^#{1,3} ) preservando il testo di ogni sezione.
      Il testo di una sezione include tutto il contenuto fino all'heading successivo.
      Heading di livello > 3 vengono normalizzati a 3 (profondità massima gestita).

    Complessità: O(n_caratteri) per il regex split + O(n_sezioni) per il loop.
    """
    # Split che preserva il delimitatore (heading) nel testo successivo
    # Pattern: inizio riga seguito da 1-6 # e spazio
    raw_sections = re.split(r'\n(?=#{1,6} )', text)

    sections = []
    chunk_index = 0

    for raw in raw_sections:
        raw = raw.strip()
        if not raw:
            continue

        # Determina livello e titolo dall'heading
        heading_match = re.match(r'^(#{1,6})\s+(.+?)(?:\n|$)', raw)
        if heading_match:
            hashes = heading_match.group(1)
            title  = heading_match.group(2).strip()
            level  = min(len(hashes), 3)  # normalizza a max 3 livelli
            # Testo = contenuto dopo la riga di heading
            text_body = raw[heading_match.end():].strip()
        else:
            # Contenuto prima del primo heading (introduzione/preambolo)
            title      = "Preambolo"
            level      = 1
            text_body  = raw

        # Scarta sezioni senza contenuto testuale significativo
        full_text = f"{title}\n{text_body}" if text_body else title
        if len(full_text) < 80:
            chunk_index += 1
            continue

        section_id = _make_section_id(source, chunk_index, title)

        sections.append({
            "id":          section_id,
            "title":       title,
            "level":       level,
            "text":        full_text,
            "source":      source,
            "chunk_index": chunk_index,
        })
        chunk_index += 1

    log.info(f"  Heading estratti: {len(sections)} sezioni da '{source}'")
    return sections


# ---------------------------------------------------------------------------
# Ingestion singolo file MD → ChromaDB + Neo4j
# ---------------------------------------------------------------------------

def ingest_md_file(
    md_path: Path,
    collection,
    embed_model,
    neo4j_driver=None,
) -> Generator[dict, None, None]:
    """
    Indicizza un singolo file MD in ChromaDB e (opzionale) Neo4j.
    Yield eventi dict per SSE durante tutta la pipeline:

      {"type": "start",    "file": nome_file}
      {"type": "document", "node_id": ..., "title": ..., "source": ...}
      {"type": "section",  "node_id": ..., "title": ..., "level": ..., "source": ...}
      {"type": "indexed",  "chunks": n, "source": ...}
      {"type": "error",    "message": ..., "source": ...}
      {"type": "done",     "file": nome_file, "chunks": n}

    neo4j_driver è opzionale — se None, salta la costruzione del grafo.
    Questo permette di usare il parser anche senza Neo4j attivo.
    """
    source = md_path.stem  # nome file senza estensione come identificatore
    yield {"type": "start", "file": md_path.name}

    # Lettura file
    try:
        raw_text = md_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        msg = f"Errore lettura {md_path.name}: {e}"
        log.error(msg)
        yield {"type": "error", "message": msg, "source": source}
        return

    # Pulizia markdown
    clean_text = clean_markdown(raw_text)
    if len(clean_text) < 100:
        msg = f"File troppo corto o vuoto dopo pulizia: {md_path.name}"
        log.warning(msg)
        yield {"type": "error", "message": msg, "source": source}
        return

    # Estrazione heading tree
    sections = parse_heading_tree(clean_text, source)
    if not sections:
        msg = f"Nessuna sezione valida in: {md_path.name}"
        log.warning(msg)
        yield {"type": "error", "message": msg, "source": source}
        return

    # ChromaDB: prepara chunk dalla lista sezioni
    chunks = [
        {
            "text":        s["text"],
            "source":      s["source"],
            "chunk_index": s["chunk_index"],
        }
        for s in sections
    ]

    total_indexed = 0
    try:
        for ids, embeddings, metadatas, texts in embed_chunks(chunks, embed_model):
            collection.upsert(
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas,
                documents=texts,
            )
            total_indexed += len(ids)
    except Exception as e:
        msg = f"Errore ChromaDB durante ingestion di {md_path.name}: {e}"
        log.error(msg)
        yield {"type": "error", "message": msg, "source": source}
        return

    yield {"type": "indexed", "chunks": total_indexed, "source": source}
    log.info(f"  → {total_indexed} chunk indicizzati in ChromaDB da '{md_path.name}'")

    # Neo4j: costruzione grafo (opzionale)
    if neo4j_driver is not None:
        from src.neo4j_graph import build_graph_from_sections
        doc_id   = _make_doc_id(source)
        doc_name = md_path.stem.replace("_", " ").replace("-", " ").title()

        try:
            for event in build_graph_from_sections(
                sections=sections,
                doc_id=doc_id,
                doc_name=doc_name,
                source=source,
                driver=neo4j_driver,
            ):
                yield event  # propaga eventi SSE al frontend
        except Exception as e:
            msg = f"Errore Neo4j durante ingestion di {md_path.name}: {e}"
            log.error(msg)
            yield {"type": "error", "message": msg, "source": source}
            # Non return — ChromaDB è già stato aggiornato, non bloccare

    yield {"type": "done", "file": md_path.name, "chunks": total_indexed}


# ---------------------------------------------------------------------------
# Ingestion directory MD
# ---------------------------------------------------------------------------

def ingest_md_directory(
    md_dir: Path,
    collection,
    embed_model,
    neo4j_driver=None,
) -> Generator[dict, None, None]:
    """
    Indicizza tutti i file .md in una directory (ricorsivo).
    Yield eventi aggregati per SSE.
    """
    md_files = list(md_dir.rglob("*.md"))
    if not md_files:
        msg = f"Nessun file .md trovato in: {md_dir}"
        log.warning(msg)
        yield {"type": "error", "message": msg, "source": str(md_dir)}
        return

    log.info(f"Trovati {len(md_files)} file MD in {md_dir}")
    yield {"type": "batch_start", "total_files": len(md_files), "directory": str(md_dir)}

    total_chunks = 0
    for md_file in md_files:
        for event in ingest_md_file(md_file, collection, embed_model, neo4j_driver):
            if event["type"] == "done":
                total_chunks += event.get("chunks", 0)
            yield event

    log.info(f"Ingestion directory completata: {total_chunks} chunk totali da {len(md_files)} file")
    yield {"type": "batch_done", "total_files": len(md_files), "total_chunks": total_chunks}
