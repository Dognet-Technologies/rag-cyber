"""
Configurazione centralizzata del sistema RAG + LLMWiki.
Tutti i parametri modificabili stanno qui — nessun valore hardcodato nel codice.
"""
from pathlib import Path

# --- Percorsi ---
BASE_DIR        = Path(__file__).parent.parent
DATA_DIR        = BASE_DIR / "data"
CHROMA_DIR      = DATA_DIR / "chroma_db"
PDF_STAGING_DIR = DATA_DIR / "pdf_staging"
MD_STAGING_DIR  = DATA_DIR / "md_staging"   # directory per i file .md da indicizzare
LOG_DIR         = BASE_DIR / "logs"

# --- Chunking ---
# Chunk size: 512 token è il balance ottimale per documenti tecnici densi.
# Overlap 10% (50 token) evita troncamento di concetti a cavallo di chunk.
CHUNK_SIZE    = 512
CHUNK_OVERLAP = 50

# --- Embedding ---
# paraphrase-multilingual-MiniLM-L12-v2: 768 dim, ottimale per testi tecnici ITA/ENG.
# ATTENZIONE: cambiare questo valore richiede reindicizzazione completa di ChromaDB.
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# --- ChromaDB ---
CHROMA_COLLECTION = "cyber_knowledge"

# --- Ollama ---
# OLLAMA_BASE_URL: punta a localhost quando il backend gira fuori Docker.
# Se in futuro il backend venisse containerizzato, cambiare in http://ollama:11434
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL    = "deepseek-coder"   # modello attivo — modificabile a runtime via API

# Modelli disponibili: lista statica dei modelli già scaricati via ollama pull.
# Aggiungere qui ogni nuovo modello dopo averlo scaricato.
OLLAMA_AVAILABLE_MODELS = [
    "deepseek-coder",
    "qwen2.5:14b",
    "llama3.1:8b",
]

# --- Retrieval ---
# Top-k chunks da passare al modello come contesto.
# 5 è il limite ragionevole per non saturare la context window.
TOP_K_RESULTS = 5

# --- Neo4j ---
# Neo4j gira come servizio Docker (docker-compose).
# Le credenziali sono solo per uso locale — non vanno in produzione.
NEO4J_URI      = "bolt://localhost:7687"
NEO4J_USER     = "neo4j"
NEO4J_PASSWORD = "llmwiki_local"   # cambiare in docker-compose.yml se necessario

# --- API ---
# FastAPI backend — host e porta esposti localmente.
API_HOST = "0.0.0.0"
API_PORT = 8000

# --- Logging ---
LOG_LEVEL = "INFO"
LOG_FILE  = LOG_DIR / "rag.log"