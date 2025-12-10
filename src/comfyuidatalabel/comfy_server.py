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
import time
from dataclasses import dataclass, field
from uuid import uuid4

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
    prompt: dict
    client_id: str
    extra_data: dict = Field(default_factory=dict)


@dataclass
class ComfyStub:
    app: FastAPI = FastAPI(title="Mock ComfyUI")
    jobs: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.register_routes()

    def _next_outputs(self, prompt_id: str) -> dict:
        filename = f"{prompt_id}.png"
        return {
            "images": [
                {
                    "filename": filename,
                    "subfolder": "outputs",
                    "type": "output",
                }
            ]
        }

    def _advance_jobs(self) -> None:
        for job in self.jobs.values():
            if job["status"] == "queued":
                job["status"] = "running"
            elif job["status"] == "running":
                job["status"] = "completed"
                job["outputs"] = self._next_outputs(job["prompt_id"])

    def register_routes(self) -> None:
        @self.app.get("/system_stats")
        def system_stats():
            return {"status": "ok", "uptime": 1}

        @self.app.get("/queue")
        def queue():
            self._advance_jobs()
            running = [
                {"prompt_id": pid, "status": job["status"]}
                for pid, job in self.jobs.items()
                if job["status"] in {"running", "completed"}
            ]
            pending = [
                {"prompt_id": pid, "status": job["status"]}
                for pid, job in self.jobs.items()
                if job["status"] == "queued"
            ]
            return {"queue_running": running, "queue_pending": pending}

        @self.app.post("/prompt")
        def submit_prompt(body: PromptRequest):
            prompt_id = f"job-{uuid4().hex[:8]}"
            self.jobs[prompt_id] = {
                "prompt_id": prompt_id,
                "client_id": body.client_id,
                "status": "queued",
                "submitted_at": time.time(),
                "outputs": {},
            }
            return {
                "prompt_id": prompt_id,
                "client_id": body.client_id,
                "status": "queued",
                "images": [_blank_image()],
            }

        @self.app.get("/history/{client_id}")
        def history(client_id: str):
            self._advance_jobs()
            history_records = {}
            for prompt_id, job in self.jobs.items():
                if job.get("client_id") != client_id:
                    continue
                history_records[prompt_id] = {
                    "status": job["status"],
                    "outputs": job.get("outputs", {}),
                }
            return {"client_id": client_id, "history": history_records}


stub = ComfyStub()
app = stub.app

