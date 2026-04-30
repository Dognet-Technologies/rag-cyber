"""
Parser MITRE ATT&CK STIX 2.1 → ChromaDB + relazioni per Neo4j.

Il formato STIX (Structured Threat Information eXpression) rappresenta
oggetti cyber come grafi — nodi (technique, group, tool, malware) e
archi (uses, mitigates, detects).

Flusso dati:
  JSON STIX → parse oggetti per tipo → testo narrativo per ChromaDB
                                     → tuple relazionali per Neo4j (fase 2)

Tipi STIX estratti:
  - attack-pattern      → Tecniche e sotto-tecniche (T1234.001)
  - intrusion-set       → Gruppi APT (G0001)
  - tool                → Tool legittimi usati dagli attaccanti
  - malware             → Malware specifici
  - course-of-action    → Mitigazioni
  - relationship        → Archi del grafo (APT → usa → Tool)
"""
import json
import sys
from pathlib import Path
from typing import Generator

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import CHROMA_COLLECTION
from src.ingestion import get_chroma_collection, embed_chunks, make_doc_id
from src.logger import get_logger

log = get_logger("mitre_parser")

# ---------------------------------------------------------------------------
# Costanti STIX
# ---------------------------------------------------------------------------

STIX_TYPES_OF_INTEREST = {
    "attack-pattern",
    "intrusion-set",
    "tool",
    "malware",
    "course-of-action",
}

# ---------------------------------------------------------------------------
# Caricamento JSON STIX
# ---------------------------------------------------------------------------

def load_stix_bundle(stix_path: Path) -> list[dict]:
    """
    Carica un bundle STIX 2.1 da file JSON.
    Ritorna lista di oggetti STIX, filtrando solo i tipi rilevanti.
    """
    try:
        with open(stix_path, "r", encoding="utf-8") as f:
            bundle = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"Errore lettura {stix_path.name}: {e}")
        return []

    objects = bundle.get("objects", [])
    log.info(f"  Caricati {len(objects)} oggetti STIX da {stix_path.name}")
    return objects


# ---------------------------------------------------------------------------
# Conversione oggetti STIX → testo narrativo
# ---------------------------------------------------------------------------

def _get_external_id(obj: dict) -> str:
    """Estrae ID ATT&CK (es. T1059.001) dai riferimenti esterni."""
    for ref in obj.get("external_references", []):
        if ref.get("source_name") == "mitre-attack":
            return ref.get("external_id", "")
    return ""


def _get_platforms(obj: dict) -> str:
    platforms = obj.get("x_mitre_platforms", [])
    return ", ".join(platforms) if platforms else "N/A"


def _get_tactics(obj: dict) -> str:
    phases = obj.get("kill_chain_phases", [])
    tactics = [p.get("phase_name", "") for p in phases if p.get("kill_chain_name") == "mitre-attack"]
    return ", ".join(tactics) if tactics else "N/A"


def stix_object_to_text(obj: dict) -> str | None:
    """
    Converte un oggetto STIX in testo narrativo strutturato.
    Il formato narrativo è ottimizzato per il retrieval semantico:
    include ID, nome, descrizione, tattiche e piattaforme in chiaro.
    Ritorna None se l'oggetto non ha contenuto utile.
    """
    obj_type = obj.get("type", "")
    name = obj.get("name", "").strip()
    description = obj.get("description", "").strip()
    ext_id = _get_external_id(obj)

    if not name or not description:
        return None

    if obj_type == "attack-pattern":
        tactics = _get_tactics(obj)
        platforms = _get_platforms(obj)
        detection = obj.get("x_mitre_detection", "").strip()
        is_subtechnique = obj.get("x_mitre_is_subtechnique", False)
        tech_type = "Sub-technique" if is_subtechnique else "Technique"

        text = (
            f"MITRE ATT&CK {tech_type}: {name} [{ext_id}]\n"
            f"Tactics: {tactics}\n"
            f"Platforms: {platforms}\n"
            f"Description: {description}"
        )
        if detection:
            text += f"\nDetection: {detection}"
        return text

    elif obj_type == "intrusion-set":
        aliases = ", ".join(obj.get("aliases", [])) or "N/A"
        text = (
            f"MITRE ATT&CK APT Group: {name} [{ext_id}]\n"
            f"Aliases: {aliases}\n"
            f"Description: {description}"
        )
        return text

    elif obj_type == "tool":
        platforms = _get_platforms(obj)
        text = (
            f"MITRE ATT&CK Tool: {name} [{ext_id}]\n"
            f"Platforms: {platforms}\n"
            f"Description: {description}"
        )
        return text

    elif obj_type == "malware":
        platforms = _get_platforms(obj)
        is_family = obj.get("is_family", False)
        text = (
            f"MITRE ATT&CK Malware{'Family' if is_family else ''}: {name} [{ext_id}]\n"
            f"Platforms: {platforms}\n"
            f"Description: {description}"
        )
        return text

    elif obj_type == "course-of-action":
        text = (
            f"MITRE ATT&CK Mitigation: {name} [{ext_id}]\n"
            f"Description: {description}"
        )
        return text

    return None


# ---------------------------------------------------------------------------
# Estrazione relazioni per Neo4j (fase 2)
# ---------------------------------------------------------------------------

def extract_relationships(objects: list[dict]) -> list[dict]:
    """
    Estrae le relazioni STIX per il graph DB (Neo4j — Fase 2).
    Ritorna lista di tuple (source_id, relationship_type, target_id).

    Non usato ancora — salvato su file JSON per quando implementiamo Neo4j.
    """
    relationships = []
    for obj in objects:
        if obj.get("type") != "relationship":
            continue
        relationships.append({
            "source_ref": obj.get("source_ref", ""),
            "relationship_type": obj.get("relationship_type", ""),
            "target_ref": obj.get("target_ref", ""),
        })
    return relationships


# ---------------------------------------------------------------------------
# Ingestion MITRE → ChromaDB
# ---------------------------------------------------------------------------

def ingest_mitre_stix(stix_dir: Path) -> None:
    """
    Indicizza tutto MITRE ATT&CK Enterprise in ChromaDB.
    Cerca ricorsivamente file JSON STIX nella directory.

    Salva anche le relazioni in JSON separato per Neo4j (Fase 2).
    """
    # Cerca il file bundle principale enterprise-attack
    stix_files = list(stix_dir.rglob("enterprise-attack.json"))
    if not stix_files:
        # Fallback: cerca tutti i JSON nella directory
        stix_files = list(stix_dir.rglob("*.json"))

    if not stix_files:
        log.error(f"Nessun file STIX trovato in: {stix_dir}")
        return

    log.info(f"Trovati {len(stix_files)} file STIX")

    from sentence_transformers import SentenceTransformer
    from config.settings import EMBEDDING_MODEL

    model = SentenceTransformer(EMBEDDING_MODEL, device="cpu")
    collection = get_chroma_collection()

    all_relationships = []
    total_indexed = 0
    total_skipped = 0

    for stix_file in stix_files:
        log.info(f"Processing: {stix_file.name}")
        objects = load_stix_bundle(stix_file)

        # Separa oggetti da relazioni
        relationships = extract_relationships(objects)
        all_relationships.extend(relationships)

        # Converti oggetti rilevanti in testo
        chunks = []
        for i, obj in enumerate(objects):
            if obj.get("type") not in STIX_TYPES_OF_INTEREST:
                continue
            # Salta oggetti revocati o deprecati
            if obj.get("revoked") or obj.get("x_mitre_deprecated"):
                total_skipped += 1
                continue

            text = stix_object_to_text(obj)
            if not text:
                total_skipped += 1
                continue

            ext_id = _get_external_id(obj)
            chunks.append({
                "text": text,
                "source": f"MITRE_ATT&CK_{obj.get('type')}_{ext_id}",
                "chunk_index": i,
            })

        if not chunks:
            log.warning(f"Nessun chunk valido da: {stix_file.name}")
            continue

        # Embedding e upsert in batch
        for ids, embeddings, metadatas, texts in embed_chunks(chunks, model):
            collection.upsert(
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas,
                documents=texts,
            )
            total_indexed += len(ids)

        log.info(f"  → {len(chunks)} oggetti indicizzati da {stix_file.name}")

    # Salva relazioni per Neo4j (Fase 2)
    if all_relationships:
        rel_path = stix_dir.parent / "mitre_relationships.json"
        with open(rel_path, "w", encoding="utf-8") as f:
            json.dump(all_relationships, f, indent=2)
        log.info(f"Relazioni salvate per Neo4j: {len(all_relationships)} → {rel_path}")

    log.info(
        f"MITRE ingestion completata: {total_indexed} oggetti indicizzati, "
        f"{total_skipped} saltati (revocati/deprecati/vuoti)"
    )
    log.info(f"Collection size totale: {collection.count()} documenti")
