"""Run the durable embedding queue outside the Knowledge HTTP event loop."""

from __future__ import annotations

import asyncio
import logging
from app.modules.embedding_jobs.worker import embedding_queue


def run_worker() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(embedding_queue.serve())


if __name__ == "__main__":
    run_worker()
