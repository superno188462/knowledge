"""Knowledge API entry point.

Run with ``uv run python main.py`` or pass an optional port, for example
``uv run python main.py 8010``.
"""

import argparse
import multiprocessing
import os

import uvicorn

from app import app
from worker import run_worker


def main() -> None:
    """Start the Knowledge API on the configured host and selected port."""
    parser = argparse.ArgumentParser(description="启动 Knowledge 数据中台 API")
    parser.add_argument(
        "port",
        nargs="?",
        type=int,
        default=int(os.getenv("PORT", "8001")),
        help="HTTP 监听端口；省略时读取 PORT 或使用 8001",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port 必须在 1 到 65535 之间")
    start_worker = os.getenv("KNOWLEDGE_START_EMBEDDING_WORKER", "1").strip() != "0"
    worker = None
    if start_worker:
        worker = multiprocessing.Process(target=run_worker, name="knowledge-embedding-worker", daemon=True)
        worker.start()
    try:
        uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=args.port)
    finally:
        if worker is not None:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=5)


if __name__ == "__main__":
    main()
