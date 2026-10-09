"""Run the durable embedding queue outside the Knowledge HTTP event loop."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv

from services.rag.embedding_queue import embedding_queue


def run_worker() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    load_dotenv(Path(__file__).resolve().parent / ".env")
    asyncio.run(embedding_queue.serve())


if __name__ == "__main__":
    run_worker()
