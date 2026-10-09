"""Validate the unified adapter contract and portability outside Knowledge."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import pytest

from app.integrations.gateway import GatewayClient


def test_gateway_package_imports_without_knowledge_or_environment():
    import gateway_client
    source = Path(gateway_client.__file__).resolve().parent
    with tempfile.TemporaryDirectory() as directory:
        shutil.copytree(source, Path(directory) / "portable_gateway", ignore=shutil.ignore_patterns("__pycache__"))
        script = """
import sys
sys.path.insert(0, sys.argv[1])
from portable_gateway import GatewayClient
client = GatewayClient('https://example.com/gateway/v1', 'mock-key', 'kb')
assert client.vectors.collection == 'kb'
assert not any(n == 'app' or n.startswith('app.') for n in sys.modules)
assert 'sqlalchemy' not in sys.modules
assert 'fastapi' not in sys.modules
assert 'dotenv' not in sys.modules
"""
        result = subprocess.run([sys.executable, "-I", "-c", script, directory], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_unified_client_uses_correct_capability_paths_and_auth():
    import asyncio
    seen = []
    def handler(request):
        seen.append(request)
        assert request.headers["authorization"] == "Bearer mock-key"
        assert request.headers["x-trace-id"]
        if request.url.path.endswith("/profile"):
            return httpx.Response(200, json={"profile": {"language": "zh"}, "version": 1})
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json={"upserted": [{"external_id": "c1"}]})
    async def exercise():
        client = GatewayClient("https://example.com/gateway/v1", "mock-key", "docs",
                               transport=httpx.MockTransport(handler))
        await client.add_vectors([{"id": "c1", "content": "chunk", "metadata": {"source": "a.md"}}])
        await client.similarity_search("question", metadata={"source": "a.md"})
        profile = await client.context.get_profile("verified-user")
        assert profile["profile"] == {"language": "zh"}
    asyncio.run(exercise())
    assert [r.url.path for r in seen] == [
        "/gateway/v1/vector-stores/collections/docs/upsert",
        "/gateway/v1/vector-stores/collections/docs/query",
        "/gateway/v1/context/users/verified-user/profile",
    ]
    assert json.loads(seen[1].content)["metadata"] == {"source": "a.md"}
