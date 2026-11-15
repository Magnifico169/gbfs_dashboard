"""Замер одновременных SSE-подключений и явного отказа при перегрузке."""

import argparse
import asyncio
import json
import os
import time
from collections import Counter

import httpx


async def run(url, users, duration):
    results = Counter()
    latencies = []
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {os.environ['BENCH_TOKEN']}"},
        limits=httpx.Limits(max_connections=users),
        timeout=httpx.Timeout(90, read=None),
    ) as client:

        async def connect():
            started = time.perf_counter()
            try:
                async with client.stream("GET", url) as response:
                    results[str(response.status_code)] += 1
                    if response.status_code != 200:
                        return
                    initial = False
                    async for line in response.aiter_lines():
                        if line.startswith("event: snapshot"):
                            initial = True
                        if not line and initial:
                            latencies.append(time.perf_counter() - started)
                            initial = False
            except httpx.HTTPError as error:
                results[type(error).__name__] += 1

        tasks = [asyncio.create_task(connect()) for _ in range(users)]
        await asyncio.sleep(duration)
        active = sum(not task.done() for task in tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    latencies.sort()
    print(
        json.dumps(
            {
                "requested": users,
                "results": dict(results),
                "ready": len(latencies),
                "p95_connect_s": latencies[int(len(latencies) * 0.95)] if latencies else None,
                "active_at_end": active,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--users", type=int, default=1000)
    parser.add_argument("--duration", type=int, default=45)
    parser.add_argument(
        "--url", default="http://127.0.0.1:58000/api/v1/systems/m2_benchmark/stream"
    )
    args = parser.parse_args()
    asyncio.run(run(args.url, args.users, args.duration))
