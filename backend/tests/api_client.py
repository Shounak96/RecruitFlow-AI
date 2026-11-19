import asyncio
from typing import Any

from httpx import ASGITransport, AsyncClient, Response

from app.main import app


def request(method: str, path: str, **kwargs: Any) -> Response:
    async def send() -> Response:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())

