from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from comfyuidatalabel.comfy_server import app as comfy_app, stub


def comfy_stub_client() -> httpx.Client:
    stub.reset()
    client = TestClient(comfy_app)

    def _handler(request: httpx.Request) -> httpx.Response:
        response = client.request(
            request.method,
            request.url.path,
            headers=request.headers,
            content=request.content,
            json=None,
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=response.content,
        )

    return httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://comfy-stub")
