"""One-shot ingestion job: `docker compose run --rm ingest [--reparse]`. Never run from API startup."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from langgraph.checkpoint.postgres import PostgresSaver

from app.config import settings
from ingest import embed, parse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ingest")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reparse", action="store_true", help="re-run the slow PDF parse even if chunks.jsonl exists")
    args = ap.parse_args()

    chunks = Path(settings.CHUNKS_PATH)
    if args.reparse or not chunks.exists():
        parse.run()
    else:
        log.info("reusing %s (pass --reparse to rebuild)", chunks)
    embed.run(chunks)

    # Checkpointer tables are schema too: created here by the deliberate job, never by the API.
    with PostgresSaver.from_conn_string(settings.DATABASE_URL) as saver:
        saver.setup()
    log.info("checkpointer tables ready")


if __name__ == "__main__":
    main()
