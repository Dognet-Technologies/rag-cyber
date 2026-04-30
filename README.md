# LLMWiki / "Addrestrare Nina" — Guida di installazione completa

## Prerequisiti di sistema

- Ubuntu 22.04 / Debian 12 (testato su entrambi)
- Python 3.12+
- Docker + Docker Compose
- Ollama installato e in esecuzione (`ollama serve`)
- GPU NVIDIA con driver installati (opzionale — il backend gira anche su CPU)

---

## Struttura del progetto

```
~/llm/
├── venv/                          # virtualenv Python condiviso
└── rag-cyber/                     # root del progetto
    ├── config/
    │   └── settings.py
    ├── src/
    │   ├── logger.py
    │   ├── ingestion.py
    │   ├── query_engine.py
    │   ├── mitre_parser.py
    │   ├── bugbounty_parser.py
    │   ├── md_parser.py
    │   └── neo4j_graph.py
    ├── api/
    │   ├── __init__.py
    │   └── main.py
    ├── frontend/
    │   ├── Dockerfile
    │   ├── package.json
    │   ├── vite.config.js
    │   ├── index.html
    │   └── src/
    │       ├── main.jsx
    │       ├── App.jsx
    │       ├── index.css
    │       ├── api.js
    │       └── components/
    │           ├── ChatPanel.jsx
    │           ├── GraphPanel.jsx
    │           ├── IngestionLog.jsx
    │           └── StatusBar.jsx
    ├── data/
    │   ├── chroma_db/
    │   ├── md_staging/
    │   ├── pdf_staging/
    │   ├── mitre-cti/
    │   ├── hackerone-reports/
    │   └── learn365/
    ├── logs/
    ├── docker-compose.yml
    ├── requirements.txt
    └── pdf_to_md.py
```

---

## Step 1 — Ollama

```bash
# Installazione
curl -fsSL https://ollama.com/install.sh | sh

# Avvio del servizio
ollama serve &

# Pull dei modelli necessari
ollama pull deepseek-coder
ollama pull qwen2.5:14b
ollama pull llama3.1:8b

# Verifica
ollama list
```

---

## Step 2 — Virtualenv Python

```bash
# Crea il venv nella directory padre del progetto
cd ~/llm
/usr/bin/python3 -m venv venv

# Attiva il venv
source venv/bin/activate

# Verifica che pip punti al venv
which pip
# output atteso: /home/<user>/llm/venv/bin/pip
```

> **Attenzione:** Se `which pip` risponde con `/usr/bin/pip` il venv non è
> agganciato correttamente. In quel caso usa sempre:
> `python -m pip install ...` oppure il path assoluto del pip nel venv.

---

## Step 3 — Dipendenze Python

```bash
cd ~/llm/rag-cyber
pip install -r requirements.txt
```

Contenuto di `requirements.txt`:

```
chromadb>=0.5.0
pymupdf>=1.24.0
sentence-transformers>=3.0.0
langchain-text-splitters>=0.2.0
datasets>=2.0.0
neo4j>=5.18.0
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
pydantic>=2.7.0
psutil>=5.9.0
```

Verifica installazione:

```bash
python -c "import chromadb; import fastapi; import neo4j; import psutil; print('OK')"
# output atteso: OK
```

---

## Step 4 — MarkItDown (per conversione PDF→MD)

```bash
pip install 'markitdown[all]'

# Verifica
python -c "from markitdown import MarkItDown; print('ok')"
# output atteso: ok
```

> Il warning `Couldn't find ffmpeg` è innocuo se non converti file audio.
> Per eliminarlo: `sudo apt install ffmpeg`

---

## Step 5 — Struttura directory

```bash
cd ~/llm/rag-cyber

# Crea directory necessarie
mkdir -p data/md_staging
mkdir -p data/pdf_staging
mkdir -p data/chroma_db
mkdir -p logs
mkdir -p api
mkdir -p docker/neo4j/{data,logs,plugins}

# Crea __init__.py per il modulo api
touch api/__init__.py
```

---

## Step 6 — Configurazione

Modifica `config/settings.py` secondo il tuo ambiente:

```python
# Parametri principali da verificare/modificare

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL    = "deepseek-coder"

OLLAMA_AVAILABLE_MODELS = [
    "deepseek-coder",
    "qwen2.5:14b",
    "llama3.1:8b",
]

NEO4J_URI      = "bolt://localhost:7687"
NEO4J_USER     = "neo4j"
NEO4J_PASSWORD = "llmwiki_local"   # deve corrispondere a docker-compose.yml

API_HOST = "0.0.0.0"
API_PORT = 8000
```

> **Nota server Proxmox (no GPU):** il backend gira identico su CPU.
> Ollama usa CPU inference automaticamente se non trova GPU.

---

## Step 7 — Docker (Neo4j + Frontend)

```bash
cd ~/llm/rag-cyber

# Avvia Neo4j
docker compose up -d neo4j

# Attendi ~60s per l'inizializzazione, poi verifica
docker compose ps
# Neo4j deve mostrare: (healthy)

# Avvia tutto lo stack (Neo4j + Frontend)
docker compose up -d

# Verifica
docker compose ps
```

Porte esposte:
- `http://localhost:3000` — Frontend React
- `http://localhost:7474` — Neo4j Browser (credenziali: neo4j / llmwiki_local)
- `bolt://localhost:7687` — Neo4j Bolt (usato dal backend)

---

## Step 8 — Backend FastAPI

```bash
# IMPORTANTE: lanciare sempre dalla root del progetto
cd ~/llm/rag-cyber

# Attiva il venv se non attivo
source ~/llm/venv/bin/activate

# Avvio con reload (sviluppo)
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Avvio senza reload (produzione/server)
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Verifica endpoint:

```bash
# Status sistema
curl http://localhost:8000/status | python -m json.tool

# Lista modelli
curl http://localhost:8000/models | python -m json.tool
```

Output atteso da `/status`:

```json
{
    "chroma_docs": 141592,
    "neo4j_ok": true,
    "neo4j_uri": "bolt://localhost:7687",
    "cpu_percent": 6.3,
    "ram_used_gb": 12.67,
    "ram_total_gb": 15.17,
    "gpu_available": true,
    "active_model": "deepseek-coder"
}
```

---

## Step 9 — Ingestion dati

### PDF diretti

```bash
# Copia i PDF in data/pdf_staging/ poi:
python main.py ingest --dir data/pdf_staging
```

### MITRE ATT&CK

```bash
# Clona il repository STIX
cd data
git clone https://github.com/mitre/cti mitre-cti
cd ..

python main.py ingest-mitre --dir data/mitre-cti/enterprise-attack
```

### HackerOne + learn365

```bash
cd data
git clone https://github.com/nicehash/hackerone-reports hackerone-reports
git clone https://github.com/tib3rius/learn365 learn365
cd ..

python main.py ingest-bugbounty --dir data
```

### File MD (LLMWiki)

Tramite frontend: tasto `+` nell'interfaccia (legge da `data/md_staging/`)

Oppure via CLI:

```bash
# Dopo aver messo i file MD in data/md_staging/
curl -X POST http://localhost:8000/ingest/md
```

---

## Step 10 — Conversione PDF→MD (opzionale, per dataset training)

```bash
# Metti pdf_to_md.py nella root del progetto
# Modifica SOURCE_DIR e OUTPUT_DIR nello script se necessario

# Dry run — verifica cosa verrebbe convertito
python pdf_to_md.py --dry-run

# Conversione completa in background
nohup python pdf_to_md.py > logs/pdf_to_md.log 2>&1 &

# Monitora il progresso
tail -f logs/pdf_to_md.log
```

---

## Verifica finale

```bash
# Status ChromaDB
python main.py status

# Query di test
python main.py query --question "What is lateral movement in cybersecurity?"

# Frontend
# Apri http://localhost:3000
```

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'api'`**
→ Stai lanciando uvicorn dalla directory sbagliata.
  Soluzione: `cd ~/llm/rag-cyber` prima di lanciare uvicorn.

**`bad interpreter: /home/.../venv/bin/python: file o directory non esistente`**
→ Il venv è stato creato con un path diverso da quello attuale (es. dopo aver spostato la directory).
  Soluzione: cancella il venv e ricrealo con `/usr/bin/python3 -m venv venv`.

**`address already in use` (porta 3000)**
→ Hai `npm run dev` attivo mentre provi ad avviare Docker.
  Sono modalità alternative — usa una sola alla volta.

**Neo4j non raggiungibile**
→ Verifica che il container sia healthy: `docker compose ps`
→ Neo4j impiega ~60s all'avvio.
→ La password in `settings.py` deve corrispondere a quella in `docker-compose.yml`.

**`which pip` risponde `/usr/bin/pip` anche con venv attivo**
→ Usa `python -m pip install ...` come workaround,
  oppure ricrea il venv con il path corretto.
