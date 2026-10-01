from __future__ import annotations

import asyncio
import os

import httpx


async def main() -> None:
    ingest_url = os.environ["FOUNDATION_INGEST_URL"].strip()
    base = ingest_url.rsplit("/v1/events", 1)[0]
    url = base + "/v1/command/iren"

    async with httpx.AsyncClient(timeout=15.0) as http:
        get_response = await http.get(url)
        post_response = await http.post(url, json={"command": "status"})

    if get_response.status_code != 401:
        raise RuntimeError(
            f"unauthenticated IREN Command GET did not fail closed: {get_response.status_code}"
        )
    if post_response.status_code != 401:
        raise RuntimeError(
            f"unauthenticated IREN Command POST did not fail closed: {post_response.status_code}"
        )

    print(
        "FOUNDATION_COMMAND_ACCESS_FAIL_CLOSED_PROBE_PASSED",
        {
            "get_status": get_response.status_code,
            "post_status": post_response.status_code,
            "unauthenticated_read_allowed": False,
            "unauthenticated_write_allowed": False,
        },
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
