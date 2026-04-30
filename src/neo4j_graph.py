"""
Client Neo4j per la costruzione del grafo LLMWiki.

Modello del grafo:
  Nodi:
    (:Document  {id, name, source, created_at})
    (:Section   {id, title, level, text, source, chunk_index})

  Archi:
    (:Document)-[:HAS_SECTION]->(:Section)         gerarchia documento→sezione
    (:Section)-[:PARENT_OF]->(:Section)            gerarchia heading h1→h2→h3
    (:Section)-[:RELATED_TO {weight}]->(:Section)  co-occorrenza entità (fase 2)

Flusso:
  MD file → parse heading tree → upsert nodi Document + Section
          → crea archi HAS_SECTION e PARENT_OF dalla gerarchia
          → yield eventi per SSE (feedback real-time al frontend)

Complessità:
  - Upsert nodi:   O(n_sezioni) — MERGE su id evita duplicati (idempotente)
  - Archi parent:  O(n_sezioni) — albero heading, al più profondità 3
  - Query grafo:   O(1) per nodo singolo, O(n) per traversal completo

Nota scalabilità:
  Gli archi RELATED_TO (co-occorrenza entità NER) sono previsti in Fase 2.
  Lo schema è già predisposto — basta aggiungere il metodo add_cooccurrence_edges().
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
from src.logger import get_logger

log = get_logger("neo4j_graph")

# ---------------------------------------------------------------------------
# Importazione lazy del driver — evita errore se neo4j non è installato
# ---------------------------------------------------------------------------

def _get_driver():
    """
    Importa e restituisce il driver Neo4j.
    Lazy import per non bloccare l'avvio se il pacchetto manca.
    """
    try:
        from neo4j import GraphDatabase
        return GraphDatabase.driver(
            NEO4J_URI,
            auth=(NEO4J_USER, NEO4J_PASSWORD),
            connection_timeout=10,
            max_connection_lifetime=3600,
        )
    except ImportError:
        log.error("Pacchetto 'neo4j' non installato. Esegui: pip install neo4j")
        raise
    except Exception as e:
        log.error(f"Connessione Neo4j fallita ({NEO4J_URI}): {e}")
        raise


# ---------------------------------------------------------------------------
# Inizializzazione schema (indici e constraint)
# ---------------------------------------------------------------------------

_SCHEMA_QUERIES = [
    # Constraint di unicità — garantiscono idempotenza dei MERGE
    "CREATE CONSTRAINT doc_id_unique IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE",
    "CREATE CONSTRAINT section_id_unique IF NOT EXISTS FOR (s:Section) REQUIRE s.id IS UNIQUE",
    # Indici per ricerche frequenti
    "CREATE INDEX section_source IF NOT EXISTS FOR (s:Section) ON (s.source)",
    "CREATE INDEX section_level IF NOT EXISTS FOR (s:Section) ON (s.level)",
]

def ensure_schema(driver) -> None:
    """
    Crea constraint e indici se non esistono.
    Idempotente — sicuro da chiamare a ogni avvio.
    """
    with driver.session() as session:
        for query in _SCHEMA_QUERIES:
            try:
                session.run(query)
            except Exception as e:
                # Alcuni constraint già esistenti generano warning, non errori bloccanti
                log.warning(f"Schema query warning: {e}")
    log.info("Schema Neo4j verificato")


# ---------------------------------------------------------------------------
# Upsert Document
# ---------------------------------------------------------------------------

def upsert_document(session, doc_id: str, name: str, source: str) -> None:
    """
    Crea o aggiorna un nodo Document.
    MERGE su id garantisce idempotenza.
    """
    session.run(
        """
        MERGE (d:Document {id: $id})
        SET d.name       = $name,
            d.source     = $source,
            d.updated_at = $updated_at
        """,
        id=doc_id,
        name=name,
        source=source,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Upsert Section + arco Document→Section
# ---------------------------------------------------------------------------

def upsert_section(
    session,
    section_id: str,
    doc_id: str,
    title: str,
    level: int,
    text: str,
    source: str,
    chunk_index: int,
) -> None:
    """
    Crea o aggiorna un nodo Section e l'arco HAS_SECTION dal Document padre.
    MERGE su id garantisce idempotenza — reingestire lo stesso file
    aggiorna i dati senza creare duplicati.
    """
    session.run(
        """
        MERGE (s:Section {id: $id})
        SET s.title       = $title,
            s.level       = $level,
            s.text        = $text,
            s.source      = $source,
            s.chunk_index = $chunk_index,
            s.updated_at  = $updated_at
        WITH s
        MATCH (d:Document {id: $doc_id})
        MERGE (d)-[:HAS_SECTION]->(s)
        """,
        id=section_id,
        doc_id=doc_id,
        title=title,
        level=level,
        text=text,
        source=source,
        chunk_index=chunk_index,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Archi PARENT_OF tra sezioni (gerarchia heading)
# ---------------------------------------------------------------------------

def upsert_parent_edge(session, parent_id: str, child_id: str) -> None:
    """
    Crea l'arco PARENT_OF tra due sezioni.
    Modella la gerarchia: h1 → h2 → h3.
    MERGE evita archi duplicati su reingestione.
    """
    session.run(
        """
        MATCH (parent:Section {id: $parent_id})
        MATCH (child:Section  {id: $child_id})
        MERGE (parent)-[:PARENT_OF]->(child)
        """,
        parent_id=parent_id,
        child_id=child_id,
    )


# ---------------------------------------------------------------------------
# Costruzione grafo da heading tree
# ---------------------------------------------------------------------------

def build_graph_from_sections(
    sections: list[dict],
    doc_id: str,
    doc_name: str,
    source: str,
    driver,
) -> Generator[dict, None, None]:
    """
    Costruisce il grafo Neo4j da una lista di sezioni estratte da un MD.

    Ogni sezione è un dict con:
      {id, title, level (1|2|3), text, chunk_index}

    Algoritmo heading tree — stack-based O(n):
      Mantiene uno stack dei nodi aperti per livello.
      Quando incontriamo un heading di livello L, il parent è
      l'ultimo nodo con livello < L nello stack.
      Questo modella correttamente la gerarchia anche con heading
      non consecutivi (es. h1 → h3 senza h2 intermedio).

    Yield: dict evento per SSE → {type, node_id, title, level, source}
    """
    with driver.session() as session:
        # Upsert nodo Document
        upsert_document(session, doc_id, doc_name, source)
        yield {"type": "document", "node_id": doc_id, "title": doc_name, "source": source}

        # Stack per gerarchia: lista di (level, section_id)
        stack: list[tuple[int, str]] = []

        for section in sections:
            sid         = section["id"]
            level       = section["level"]
            title       = section["title"]
            text        = section["text"]
            chunk_index = section["chunk_index"]

            # Upsert nodo Section + arco Document→Section
            upsert_section(
                session,
                section_id=sid,
                doc_id=doc_id,
                title=title,
                level=level,
                text=text,
                source=source,
                chunk_index=chunk_index,
            )

            # Trova parent nello stack: ultimo nodo con level < corrente
            # Svuota lo stack dai nodi di livello >= corrente
            while stack and stack[-1][0] >= level:
                stack.pop()

            if stack:
                parent_id = stack[-1][1]
                upsert_parent_edge(session, parent_id, sid)

            stack.append((level, sid))

            yield {
                "type":    "section",
                "node_id": sid,
                "title":   title,
                "level":   level,
                "source":  source,
            }

    log.info(f"Grafo costruito: {len(sections)} sezioni da '{doc_name}'")


# ---------------------------------------------------------------------------
# Lettura grafo per frontend (nodi + archi)
# ---------------------------------------------------------------------------

def get_graph_for_source(source: str, driver) -> dict:
    """
    Recupera nodi e archi del grafo per una sorgente specifica.
    Usato dal frontend per la visualizzazione statica post-ingestion.

    Ritorna: {nodes: [...], edges: [...]}
    """
    with driver.session() as session:
        nodes_result = session.run(
            """
            MATCH (s:Section {source: $source})
            RETURN s.id AS id, s.title AS title, s.level AS level
            """,
            source=source,
        )
        nodes = [{"id": r["id"], "title": r["title"], "level": r["level"]}
                 for r in nodes_result]

        edges_result = session.run(
            """
            MATCH (a:Section {source: $source})-[r:PARENT_OF]->(b:Section {source: $source})
            RETURN a.id AS source, b.id AS target
            """,
            source=source,
        )
        edges = [{"source": r["source"], "target": r["target"]}
                 for r in edges_result]

    return {"nodes": nodes, "edges": edges}


def get_full_graph(driver) -> dict:
    """
    Recupera l'intero grafo (tutti i documenti e sezioni).
    Usato per la visualizzazione globale nel frontend.
    Limite di 500 nodi per non saturare il rendering.
    """
    with driver.session() as session:
        nodes_result = session.run(
            """
            MATCH (s:Section)
            RETURN s.id AS id, s.title AS title, s.level AS level, s.source AS source
            LIMIT 500
            """,
        )
        nodes = [
            {"id": r["id"], "title": r["title"], "level": r["level"], "source": r["source"]}
            for r in nodes_result
        ]

        edges_result = session.run(
            """
            MATCH (a:Section)-[:PARENT_OF]->(b:Section)
            RETURN a.id AS source, b.id AS target
            LIMIT 1000
            """,
        )
        edges = [{"source": r["source"], "target": r["target"]} for r in edges_result]

    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def check_connection(driver) -> bool:
    """
    Verifica che Neo4j sia raggiungibile.
    Ritorna True se OK, False altrimenti — non solleva eccezioni.
    """
    try:
        with driver.session() as session:
            session.run("RETURN 1")
        return True
    except Exception as e:
        log.warning(f"Neo4j non raggiungibile: {e}")
        return False


# ---------------------------------------------------------------------------
# Context manager per uso standalone
# ---------------------------------------------------------------------------

class Neo4jGraph:
    """
    Context manager per gestione del driver Neo4j.
    Uso consigliato per operazioni singole o test:

        with Neo4jGraph() as graph:
            graph.ensure_schema()
            ok = graph.check_connection()
    """

    def __init__(self):
        self.driver = None

    def __enter__(self):
        self.driver = _get_driver()
        ensure_schema(self.driver)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.driver:
            self.driver.close()
        return False  # non sopprime le eccezioni

    def check_connection(self) -> bool:
        return check_connection(self.driver)

    def get_full_graph(self) -> dict:
        return get_full_graph(self.driver)
