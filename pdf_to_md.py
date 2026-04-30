"""
pdf_to_md.py — Conversione batch PDF → Markdown con MarkItDown.

Flusso:
  /home/simone/Libri/**/*.pdf
      → filtro (escludi backup, archivi, immagini, audio)
      → MarkItDown.convert()
      → data/md_staging/<categoria>/<nome>.md
      → log progresso + report finale

Gestione errori:
  - PDF corrotti o non estraibili → skippati con log, non bloccano la pipeline
  - File già convertiti → skippati per idempotenza (--force per ri-convertire)
  - Directory output create automaticamente

Uso:
  python pdf_to_md.py                    # converte tutto
  python pdf_to_md.py --force            # riconverte anche file già esistenti
  python pdf_to_md.py --dry-run          # mostra cosa verrebbe convertito senza farlo
  python pdf_to_md.py --source /altro    # sorgente alternativa
"""
import argparse
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------

SOURCE_DIR = Path("/home/simone/Libri")
OUTPUT_DIR = Path("/home/simone/Repos/Progetti/llm/rag-cyber/data/md_staging")

# Estensioni da escludere — tutto ciò che non è testo estraibile
EXCLUDED_EXTENSIONS = {
    ".7z", ".zip", ".rar", ".tar", ".gz", ".bz2",  # archivi
    ".png", ".jpg", ".jpeg", ".gif", ".webp",        # immagini
    ".mp3", ".mp4", ".wav", ".ogg", ".flac",         # audio/video
    ".epub", ".mobi",                                 # ebook (formato non supportato bene)
    ".txt",                                           # testo plain — non serve conversione
}

# Pattern nomi da escludere
EXCLUDED_PATTERNS = [
    "*_backup.pdf",
    "*_backup_*.pdf",
]


# ---------------------------------------------------------------------------
# Filtro file
# ---------------------------------------------------------------------------

def should_skip(pdf_path: Path) -> tuple[bool, str]:
    """
    Ritorna (True, motivo) se il file deve essere saltato.
    Controlla estensione, pattern nome, e dimensione minima.
    """
    # Estensione non supportata
    if pdf_path.suffix.lower() in EXCLUDED_EXTENSIONS:
        return True, f"estensione esclusa ({pdf_path.suffix})"

    # Solo PDF da qui in poi
    if pdf_path.suffix.lower() != ".pdf":
        return True, "non è un PDF"

    # Pattern backup
    for pattern in EXCLUDED_PATTERNS:
        if pdf_path.match(pattern):
            return True, "file backup"

    # File troppo piccolo — probabilmente corrotto o vuoto
    size = pdf_path.stat().st_size
    if size < 1024:  # < 1KB
        return True, f"file troppo piccolo ({size} bytes)"

    return False, ""


# ---------------------------------------------------------------------------
# Calcolo path output
# ---------------------------------------------------------------------------

def output_path(pdf_path: Path, source_dir: Path, output_dir: Path) -> Path:
    """
    Calcola il path MD di output mantenendo la gerarchia della sorgente.

    Esempio:
      source:  /home/simone/Libri/Hacking/Kali Linux Cookbook.pdf
      output:  data/md_staging/Hacking/Kali Linux Cookbook.md
    """
    relative = pdf_path.relative_to(source_dir)
    md_name  = relative.with_suffix(".md")
    return output_dir / md_name


# ---------------------------------------------------------------------------
# Conversione singolo file
# ---------------------------------------------------------------------------

def convert_pdf(
    pdf_path: Path,
    out_path: Path,
    md_converter,
    force: bool = False,
) -> tuple[bool, str]:
    """
    Converte un singolo PDF in MD.
    Ritorna (successo, messaggio).

    Idempotente: se out_path esiste e force=False, skippa.
    """
    # Skip se già convertito
    if out_path.exists() and not force:
        return True, "già convertito (skip)"

    # Crea directory output se non esiste
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = md_converter.convert(str(pdf_path))

        # Verifica che il risultato abbia contenuto utile
        text = result.text_content if hasattr(result, "text_content") else str(result)
        if not text or len(text.strip()) < 100:
            return False, "contenuto estratto troppo corto (PDF probabilmente scansionato)"

        # Aggiungi header con metadati sorgente per tracciabilità
        header = (
            f"---\n"
            f"source_pdf: {pdf_path.name}\n"
            f"source_path: {pdf_path}\n"
            f"converted: {time.strftime('%Y-%m-%d')}\n"
            f"---\n\n"
        )

        out_path.write_text(header + text, encoding="utf-8")
        return True, f"ok ({len(text):,} caratteri)"

    except Exception as e:
        return False, f"errore: {e}"


# ---------------------------------------------------------------------------
# Pipeline principale
# ---------------------------------------------------------------------------

def run(source_dir: Path, output_dir: Path, force: bool, dry_run: bool) -> None:
    """
    Scansiona source_dir, filtra i file, converte i PDF in MD.
    Stampa progresso inline e report finale.
    """
    from markitdown import MarkItDown

    # Raccogli tutti i file nella sorgente (ricorsivo)
    all_files = list(source_dir.rglob("*"))
    pdf_candidates = [f for f in all_files if f.is_file()]

    # Applica filtri
    to_convert = []
    skipped_pre = []

    for f in pdf_candidates:
        skip, reason = should_skip(f)
        if skip:
            skipped_pre.append((f, reason))
        else:
            to_convert.append(f)

    print(f"\n{'[DRY RUN] ' if dry_run else ''}PDF da convertire: {len(to_convert)}")
    print(f"File saltati (pre-filtro): {len(skipped_pre)}")
    print(f"Output dir: {output_dir}\n")

    if dry_run:
        print("--- File che verrebbero convertiti ---")
        for f in to_convert:
            out = output_path(f, source_dir, output_dir)
            exists = "✓ esiste" if out.exists() else "  nuovo"
            print(f"  [{exists}] {f.relative_to(source_dir)}")
        return

    if not to_convert:
        print("Nessun PDF da convertire.")
        return

    # Inizializza MarkItDown una sola volta
    md_converter = MarkItDown()

    # Contatori
    ok       = 0
    skipped  = 0
    failed   = 0
    failures = []

    start_time = time.time()

    for i, pdf_path in enumerate(to_convert, 1):
        out  = output_path(pdf_path, source_dir, output_dir)
        name = pdf_path.relative_to(source_dir)

        # Progresso inline
        print(f"[{i:3d}/{len(to_convert)}] {name}", end=" ... ", flush=True)

        success, msg = convert_pdf(pdf_path, out, md_converter, force=force)

        if "skip" in msg:
            print(f"⏭  {msg}")
            skipped += 1
        elif success:
            print(f"✓  {msg}")
            ok += 1
        else:
            print(f"✗  {msg}")
            failed += 1
            failures.append((str(name), msg))

    elapsed = time.time() - start_time

    # Report finale
    print(f"\n{'─' * 60}")
    print(f"Completato in {elapsed:.1f}s")
    print(f"  Convertiti:  {ok}")
    print(f"  Skippati:    {skipped} (già esistenti)")
    print(f"  Falliti:     {failed}")

    if failures:
        print(f"\nErrori ({len(failures)}):")
        for name, reason in failures:
            print(f"  ✗ {name}")
            print(f"    → {reason}")

    print(f"\nMD pronti in: {output_dir}")
    if ok > 0:
        print("Prossimo passo: python pdf_to_md.py --dry-run per verificare,")
        print("poi avvia l'ingestion con il tasto + nell'interfaccia.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Converti PDF in Markdown per LLMWiki",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--source", type=Path, default=SOURCE_DIR,
        help=f"Directory sorgente PDF (default: {SOURCE_DIR})"
    )
    parser.add_argument(
        "--output", type=Path, default=OUTPUT_DIR,
        help=f"Directory output MD (default: {OUTPUT_DIR})"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Riconverte anche file MD già esistenti"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Mostra cosa verrebbe convertito senza fare nulla"
    )

    args = parser.parse_args()

    # Validazione input
    if not args.source.exists():
        print(f"Errore: directory sorgente non trovata: {args.source}")
        sys.exit(1)

    run(
        source_dir=args.source,
        output_dir=args.output,
        force=args.force,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()