from __future__ import annotations

import base64
import io

from fastapi.testclient import TestClient
from PIL import Image

from comfyuidatalabel.comfy_server import app, stub


def test_stub_returns_blank_image_and_health():
    stub.reset()
    client = TestClient(app)
    assert client.get("/system_stats").status_code == 200
    assert client.get("/queue").status_code == 200

    payload = {
        "workflow_api": {},
        "prompt": {"1": {"class_type": "Empty", "inputs": {}}},
        "client_id": "client-123",
        "extra_data": {},
    }
    resp = client.post("/prompt", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["prompt_id"].startswith("job-")
    encoded = body["images"][0]
    image_bytes = base64.b64decode(encoded)
    image = Image.open(io.BytesIO(image_bytes))
    assert image.size == (512, 512)
    history = client.get(f"/history/{payload['client_id']}").json()
    assert body["prompt_id"] in history["history"]
