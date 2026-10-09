"""Knowledge API entry point.

Run with ``uv run python main.py`` or pass an optional port, for example
``uv run python main.py 8010``.
"""

import argparse

import uvicorn

from app.bootstrap import app
from app.settings import get_settings


def main() -> None:
    """Start the Knowledge API on the configured host and selected port."""
    parser = argparse.ArgumentParser(description="启动 Knowledge 数据中台 API")
    settings = get_settings()
    parser.add_argument(
        "port",
        nargs="?",
        type=int,
        default=settings.port,
        help="HTTP 监听端口；省略时读取 PORT 或使用 8001",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port 必须在 1 到 65535 之间")
    uvicorn.run(app, host=settings.host, port=args.port)


if __name__ == "__main__":
    main()
