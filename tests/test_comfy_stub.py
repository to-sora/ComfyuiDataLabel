from __future__ import annotations

import base64
import io
import time

from fastapi.testclient import TestClient
from PIL import Image

from comfyuidatalabel.comfy_server import app, stub


def test_stub_returns_blank_image_and_health():
    stub.reset()
    client = TestClient(app)
    assert client.get("/system_stats").status_code == 200
    assert client.get("/queue").status_code == 200

    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"

        payload = {"workflow_api": {}, "prompt": "hello", "seed": 42, "batch_size": 1}
        resp = client.post("/prompt", json=payload)
        assert resp.status_code == 200
        prompt_id = resp.json()["prompt_id"]

        queued = ws.receive_json()
        assert queued["prompt_id"] == prompt_id
        status_update = ws.receive_json()
        assert status_update["prompt_id"] == prompt_id

    time.sleep(0.05)
    history = client.get(f"/history/{prompt_id}").json()["history"][prompt_id]
    assert history["status"] == "completed"
    encoded = history["images"][0]
    image_bytes = base64.b64decode(encoded)
    image = Image.open(io.BytesIO(image_bytes))
    assert image.size == (512, 512)


def test_upload_and_download_round_trip():
    stub.reset()
    client = TestClient(app)
    upload_resp = client.post("/upload/image", files={"file": ("note.txt", b"hello")})
    assert upload_resp.status_code == 200
    file_id = upload_resp.json()["file_id"]

    download_resp = client.get(f"/download/{file_id}")
    assert download_resp.status_code == 200
    assert download_resp.content == b"hello"
