"""
Parser sorgenti bugbounty → ChromaDB.

Gestisce tre sorgenti con strutture diverse:

1. hackerone-reports/data.csv
   Filtro qualità: upvotes >= MIN_UPVOTES OR bounty >= MIN_BOUNTY
   I top report già selezionati (tops_100/) vengono tutti inclusi.

2. hackerone-reports/tops_100/*.md
   File markdown con i 100 report più pagati e più votati.
   Inclusi integralmente senza filtro — sono già selezionati.

3. learn365/days/*.md
   Contenuto tecnico giornaliero in markdown.
   Inclusi integralmente.

Awesome-Bugbounty-Writeups contiene solo URL — richiede fetching
separato (non implementato qui, gestito da un futuro web_fetcher.py).
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.ingestion import get_chroma_collection, embed_chunks
from src.logger import get_logger

log = get_logger("bugbounty_parser")

# ---------------------------------------------------------------------------
# Soglie di qualità per il CSV HackerOne
# ---------------------------------------------------------------------------
MIN_UPVOTES = 10   # report con almeno 10 upvotes dalla community
MIN_BOUNTY  = 500  # oppure bounty >= $500


# ---------------------------------------------------------------------------
# Utilità markdown
# ---------------------------------------------------------------------------

def clean_markdown(text: str) -> str:
    """
    Rimuove elementi markdown non utili per il retrieval semantico:
    link URL, immagini, badge, righe vuote multiple.
    Mantiene il contenuto testuale e i titoli come contesto.
    """
    import re
    # Rimuovi immagini ![alt](url)
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    # Sostituisci link [testo](url) con solo il testo
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    # Rimuovi righe che sono solo badge/shield
    text = re.sub(r'\[!\[.*?\].*?\]', '', text)
    # Comprimi righe vuote multiple
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def chunk_markdown_by_section(text: str, source: str) -> list[dict]:
    """
    Divide markdown in chunk rispettando i titoli (# ## ###).
    Ogni sezione diventa un chunk con il titolo come contesto.
    Chunk troppo lunghi vengono ulteriormente spezzati.
    """
    import re
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    sections = re.split(r'\n(?=#{1,3} )', text)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=512,
        chunk_overlap=50,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    chunks = []
    for i, section in enumerate(sections):
        section = section.strip()
        if len(section) < 80:  # salta sezioni troppo corte
            continue
        # Se la sezione è lunga, spezzala ulteriormente
        if len(section) > 600:
            sub_chunks = splitter.split_text(section)
            for j, sub in enumerate(sub_chunks):
                if len(sub.strip()) >= 80:
                    chunks.append({
                        "text": sub.strip(),
                        "source": source,
                        "chunk_index": i * 100 + j,
                    })
        else:
            chunks.append({
                "text": section,
                "source": source,
                "chunk_index": i,
            })
    return chunks


# ---------------------------------------------------------------------------
# Parser 1: HackerOne CSV
# ---------------------------------------------------------------------------

def parse_hackerone_csv(csv_path: Path) -> list[dict]:
    """
    Legge data.csv e filtra i report di qualità.
    Criteri: upvotes >= MIN_UPVOTES OR bounty >= MIN_BOUNTY.
    Converte ogni report in testo narrativo per il retrieval.
    """
    chunks = []
    total = 0
    accepted = 0

    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                total += 1
                try:
                    upvotes = int(float(row.get("upvotes", 0) or 0))
                    bounty  = float(row.get("bounty", 0) or 0)
                except ValueError:
                    continue

                if upvotes < MIN_UPVOTES and bounty < MIN_BOUNTY:
                    continue

                accepted += 1
                program   = row.get("program", "").strip()
                title     = row.get("title", "").strip()
                link      = row.get("link", "").strip()
                vuln_type = row.get("vuln_type", "").strip() or "N/A"

                if not title:
                    continue

                text = (
                    f"HackerOne Bug Bounty Report\n"
                    f"Program: {program}\n"
                    f"Vulnerability Type: {vuln_type}\n"
                    f"Title: {title}\n"
                    f"Upvotes: {upvotes} | Bounty: ${bounty:.0f}\n"
                    f"Reference: {link}"
                )
                chunks.append({
                    "text": text,
                    "source": f"hackerone_csv_{vuln_type.replace(' ', '_')}",
                    "chunk_index": i,
                })

    except (OSError, csv.Error) as e:
        log.error(f"Errore lettura CSV: {e}")
        return []

    log.info(f"HackerOne CSV: {accepted}/{total} report accettati (upvotes>={MIN_UPVOTES} OR bounty>=${MIN_BOUNTY})")
    return chunks


# ---------------------------------------------------------------------------
# Parser 2: Markdown tops_100 e learn365
# ---------------------------------------------------------------------------

def parse_markdown_files(md_dir: Path, source_prefix: str) -> list[dict]:
    """
    Indicizza file markdown da una directory.
    Usato per tops_100/*.md e learn365/days/*.md.
    """
    md_files = list(md_dir.rglob("*.md"))
    if not md_files:
        log.warning(f"Nessun file .md trovato in: {md_dir}")
        return []

    all_chunks = []
    for md_file in md_files:
        try:
            text = md_file.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            log.error(f"Errore lettura {md_file.name}: {e}")
            continue

        text = clean_markdown(text)
        if len(text) < 100:
            continue

        source = f"{source_prefix}_{md_file.stem}"
        chunks = chunk_markdown_by_section(text, source)
        all_chunks.extend(chunks)
        log.info(f"  {md_file.name}: {len(chunks)} chunk")

    return all_chunks


# ---------------------------------------------------------------------------
# Ingestion principale
# ---------------------------------------------------------------------------

def ingest_bugbounty(data_dir: Path) -> None:
    """
    Indicizza tutte le sorgenti bugbounty disponibili.
    Tollerante ai path mancanti — salta le sorgenti non trovate.
    """
    from sentence_transformers import SentenceTransformer
    from config.settings import EMBEDDING_MODEL

    model     = SentenceTransformer(EMBEDDING_MODEL, device="cpu")
    collection = get_chroma_collection()

    all_chunks = []

    # --- HackerOne CSV ---
    csv_path = data_dir / "hackerone-reports" / "data.csv"
    if csv_path.exists():
        log.info("Parsing HackerOne CSV...")
        all_chunks.extend(parse_hackerone_csv(csv_path))
    else:
        log.warning(f"CSV non trovato: {csv_path}")

    # --- HackerOne tops_100 markdown ---
    tops_dir = data_dir / "hackerone-reports" / "tops_100"
    if tops_dir.exists():
        log.info("Parsing HackerOne tops_100 markdown...")
        all_chunks.extend(parse_markdown_files(tops_dir, "hackerone_top100"))
    else:
        log.warning(f"tops_100 non trovata: {tops_dir}")

    # --- learn365 ---
    learn_dir = data_dir / "learn365" / "days"
    if learn_dir.exists():
        log.info("Parsing learn365 days...")
        all_chunks.extend(parse_markdown_files(learn_dir, "learn365"))
    else:
        log.warning(f"learn365/days non trovata: {learn_dir}")

    if not all_chunks:
        log.error("Nessun chunk prodotto da nessuna sorgente.")
        return

    log.info(f"Totale chunk da indicizzare: {len(all_chunks)}")

    # Embedding e upsert in batch
    total_indexed = 0
    for ids, embeddings, metadatas, texts in embed_chunks(all_chunks, model):
        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=texts,
        )
        total_indexed += len(ids)

    log.info(f"Bugbounty ingestion completata: {total_indexed} chunk indicizzati")
    log.info(f"Collection size totale: {collection.count()} documenti")
