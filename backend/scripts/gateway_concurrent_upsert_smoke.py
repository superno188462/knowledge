"""在临时 Gateway collection 中模拟并发写入切片。

该脚本会调用真实 Embedding 服务并消耗额度。请显式设置环境变量后手动运行。
不会读取 Knowledge 的业务文档，也不会输出 API Key 或请求内容。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

import httpx


def _error_summary(response: httpx.Response) -> str:
    """只返回安全的错误标识，不打印请求或认证信息。"""

    try:
        payload = response.json()
    except ValueError:
        payload = None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict) and detail.get("code"):
        return str(detail["code"])
    return "gateway_error"


async def run(args: argparse.Namespace) -> int:
    base_url = os.getenv("GATEWAY_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("GATEWAY_API_KEY", "")
    model = os.getenv("GATEWAY_TEST_MODEL", "volc/doubao-embedding-vision").strip()
    try:
        chunk_size = int(os.getenv("GATEWAY_TEST_CHUNK_SIZE", "1000"))
    except ValueError:
        print("配置错误：GATEWAY_TEST_CHUNK_SIZE 必须是整数。")
        return 2

    if not base_url or not api_key:
        print("请先设置 GATEWAY_BASE_URL 和 GATEWAY_API_KEY 环境变量。")
        return 2
    if not model:
        print("配置错误：GATEWAY_TEST_MODEL 不能为空。")
        return 2
    if not 100 <= chunk_size <= 6000:
        print("配置错误：GATEWAY_TEST_CHUNK_SIZE 必须在 100 到 6000 之间。")
        return 2
    if args.chunks < 1 or args.concurrency < 1:
        print("--chunks 和 --concurrency 必须大于 0。")
        return 2
    if not 1 <= args.chunk_chars <= chunk_size:
        print(f"--chunk-chars 必须在 1 到 collection 的 chunk_size（{chunk_size}）之间。")
        return 2

    suffix = uuid.uuid4().hex[:10]
    collection = f"knowledge-concurrency-smoke-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{suffix}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Trace-ID": f"knowledge-smoke-{uuid.uuid4().hex}",
    }
    collection_url = f"{base_url}/vector-stores/collections"
    item_url = f"{collection_url}/{quote(collection, safe='')}"
    created = False
    success_count = 0
    failures: list[str] = []

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(args.timeout, connect=min(10.0, args.timeout)),
        trust_env=False,
    ) as client:
        try:
            create_response = await client.post(
                collection_url,
                headers=headers,
                json={
                    "name": collection,
                    "description": "Temporary concurrency diagnostic collection; safe to delete.",
                    "model": model,
                    "chunk_size": chunk_size,
                    "chunk_overlap": 0,
                },
            )
            if create_response.is_error:
                print(
                    f"创建临时集合失败：HTTP {create_response.status_code} "
                    f"{_error_summary(create_response)}"
                )
                return 1
            created = True
            print(f"临时集合已创建：{collection}；模型={model}；模式={args.mode}")

            semaphore = asyncio.Semaphore(args.concurrency)

            def make_record(index: int) -> dict[str, object]:
                if args.mode == "same-id":
                    record_id = f"race-{suffix}"
                else:
                    record_id = f"chunk-{suffix}-{index:05d}"
                seed_text = f"Synthetic diagnostic chunk {index}. No user data. "
                return {
                    "id": record_id,
                    "title": f"Concurrent smoke chunk {index}",
                    "content": (seed_text * (args.chunk_chars // len(seed_text) + 1))[:args.chunk_chars],
                    "metadata": {
                        "source": "diagnostic/concurrency-smoke.txt",
                        "record_type": "gateway_concurrency_smoke",
                        "chunk_index": index,
                        "run_id": suffix,
                    },
                }

            async def send_one(index: int) -> tuple[int, int, str | None, str | None]:
                async with semaphore:
                    try:
                        response = await client.post(
                            f"{item_url}/upsert",
                            headers={**headers, "X-Trace-ID": f"knowledge-smoke-{uuid.uuid4().hex}"},
                            json={"vectors": [make_record(index)]},
                        )
                    except httpx.TimeoutException:
                        return index, 0, "timeout", None
                    except httpx.HTTPError:
                        return index, 0, "network_error", None

                trace_id = response.headers.get("X-Trace-ID")
                if response.is_error:
                    return index, response.status_code, _error_summary(response), trace_id
                return index, response.status_code, None, trace_id

            if args.mode == "same-id":
                seed_response = await client.post(
                    f"{item_url}/upsert",
                    headers=headers,
                    json={
                        "vectors": [
                            {
                                "id": f"race-{suffix}",
                                "title": "Seed record for concurrent replacement",
                                "content": "Synthetic seed record for a concurrency diagnostic.",
                                "metadata": {
                                    "source": "diagnostic/concurrency-smoke.txt",
                                    "record_type": "gateway_concurrency_smoke",
                                    "run_id": suffix,
                                },
                            }
                        ]
                    },
                )
                if seed_response.is_error:
                    print(
                        f"建立竞态测试初始记录失败：HTTP {seed_response.status_code} "
                        f"{_error_summary(seed_response)}"
                    )
                    return 1
                print("初始记录已写入；接下来并发覆盖同一 ID。")

            if args.mode == "batch":
                batch_response = await client.post(
                    f"{item_url}/upsert",
                    headers={**headers, "X-Trace-ID": f"knowledge-smoke-{uuid.uuid4().hex}"},
                    json={"vectors": [make_record(i) for i in range(args.chunks)]},
                )
                trace_id = batch_response.headers.get("X-Trace-ID") or "unknown"
                if batch_response.is_error:
                    print(
                        f"批量写入失败：HTTP {batch_response.status_code} "
                        f"{_error_summary(batch_response)} trace_id={trace_id}；"
                        "失败请求可能已部分写入。"
                    )
                    return 1
                payload = batch_response.json()
                upserted = payload.get("upserted") if isinstance(payload, dict) else None
                written = str(len(upserted)) if isinstance(upserted, list) else "未知"
                print(f"批量写入完成：一个请求包含 {args.chunks} 条切片，Gateway 报告写入 {written} 条。")
                return 0

            outcomes = await asyncio.gather(*(send_one(i) for i in range(args.chunks)))
            for index, status, error, trace_id in outcomes:
                if error is None:
                    success_count += 1
                else:
                    trace_suffix = f" trace_id={trace_id}" if trace_id else ""
                    failures.append(f"#{index}: HTTP {status} {error}{trace_suffix}")

            print(
                f"并发写入完成：成功 {success_count}/{args.chunks}，"
                f"失败 {len(failures)}/{args.chunks}，并发上限={args.concurrency}。"
            )
            for failure in failures:
                print(f"  {failure}")
            return 0 if not failures else 1
        except httpx.TimeoutException:
            print("Gateway 请求超时。")
            return 1
        except httpx.HTTPError:
            print("Gateway 网络请求失败。")
            return 1
        finally:
            if created:
                try:
                    cleanup = await client.delete(item_url, headers=headers)
                    if cleanup.is_error:
                        print(
                            f"清理临时集合失败：HTTP {cleanup.status_code} "
                            f"{_error_summary(cleanup)}；请手动删除集合 {collection}。"
                        )
                    else:
                        print("临时集合已清理。")
                except httpx.HTTPError:
                    print(f"清理请求失败；请手动删除临时集合 {collection}。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=int, default=8, help="并发请求/切片数，默认 8")
    parser.add_argument("--concurrency", type=int, default=4, help="最大并发请求数，默认 4")
    parser.add_argument("--chunk-chars", type=int, default=400, help="每条测试切片字符数，默认 400")
    parser.add_argument(
        "--mode",
        choices=("unique", "same-id", "batch"),
        default="unique",
        help="unique 并发写不同 ID；same-id 并发覆盖同一个 ID；batch 单请求批量写入",
    )
    parser.add_argument("--timeout", type=float, default=90.0, help="单次 HTTP 超时秒数")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
