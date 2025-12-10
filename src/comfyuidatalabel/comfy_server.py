"""Lightweight ComfyUI-compatible stub server.

This module exposes a FastAPI application that mirrors the minimum ComfyUI
routes we integrate with in tests and local development:

* ``GET /system_stats`` and ``GET /queue`` for health checks
* ``POST /prompt`` to accept a workflow payload and return a blank PNG image
  encoded as base64 so downstream callers can validate end-to-end behavior
  without GPU dependencies.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

from fastapi import FastAPI
from pydantic import BaseModel, Field
from PIL import Image


def _blank_image(width: int = 512, height: int = 512) -> str:
    """Return a base64 encoded blank PNG."""

    image = Image.new("RGB", (width, height), (0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class PromptRequest(BaseModel):
    workflow_api: dict = Field(default_factory=dict)
    prompt: str
    seed: int
    batch_size: int = 1


@dataclass
class ComfyStub:
    app: FastAPI = FastAPI(title="Mock ComfyUI")

    def __post_init__(self) -> None:
        self.register_routes()

    def register_routes(self) -> None:
        @self.app.get("/system_stats")
        def system_stats():
            return {"status": "ok", "uptime": 1}

        @self.app.get("/queue")
        def queue():
            return {"queue_running": [], "queue_pending": []}

        @self.app.post("/prompt")
        def submit_prompt(body: PromptRequest):
            return {
                "prompt_id": f"job-{body.seed}",
                "status": "submitted",
                "images": [_blank_image()],
                "seed": body.seed,
                "batch_size": body.batch_size,
            }


stub = ComfyStub()
app = stub.app

