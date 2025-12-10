"""Lightweight ComfyUI-compatible stub server.

This module exposes a FastAPI application that mirrors the minimum ComfyUI
routes we integrate with in tests and local development:

* ``GET /system_stats`` and ``GET /queue`` for health checks
* ``POST /prompt`` to accept a workflow payload and return a blank PNG image
  encoded as base64 so downstream callers can validate end-to-end behavior
  without GPU dependencies.
* ``/history``, ``/ws`` and simple upload/download routes to emulate common
  ComfyUI surface area with realistic status transitions.
"""

from __future__ import annotations

import asyncio
import base64
import io
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from fastapi import (
    BackgroundTasks,
    FastAPI,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import StreamingResponse
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
    client_id: str = Field(default_factory=lambda: "client-unknown")
    extra_data: dict = Field(default_factory=dict)
    seed: int | None = None
    batch_size: int = 1


@dataclass
class ComfyStub:
    app: FastAPI = FastAPI(title="Mock ComfyUI")
    api_key: Optional[str] = None
    version: str = "1.5.0"
    features: Iterable[str] = field(default_factory=lambda: ["controlnet", "dynamic_resolution"])

    prompts: Dict[str, dict] = field(default_factory=dict)
    queue_pending: List[str] = field(default_factory=list)
    queue_running: List[str] = field(default_factory=list)
    uploads: Dict[str, bytes] = field(default_factory=dict)
    websocket_connections: List[WebSocket] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.register_routes()

    # ------------------------------------------------------------------
    # Route registration and helpers
    # ------------------------------------------------------------------

    def _validate_auth(self, request_headers: dict) -> None:
        if not self.api_key:
            return
        normalized = {k.lower(): v for k, v in request_headers.items()}
        token = normalized.get("authorization", "").replace("Bearer", "").strip()
        if token == self.api_key:
            return
        if normalized.get("x-api-key") == self.api_key:
            return
        raise HTTPException(status_code=401, detail="Missing or invalid API key")

    def _broadcast(self, payload: dict) -> None:
        """Send queue updates to connected websocket clients."""

        stale: List[WebSocket] = []
        for ws in self.websocket_connections:
            try:
                asyncio.create_task(ws.send_json(payload))
            except RuntimeError:
                stale.append(ws)
        for ws in stale:
            if ws in self.websocket_connections:
                self.websocket_connections.remove(ws)

    def _update_status(self, prompt_id: str, status: str, images: Optional[List[str]] = None) -> None:
        prompt = self.prompts[prompt_id]
        prompt["status"] = status
        prompt["updated_at"] = time.time()
        if status == "running":
            prompt["started_at"] = prompt.get("started_at") or prompt["updated_at"]
        if status == "completed":
            prompt["completed_at"] = prompt.get("completed_at") or prompt["updated_at"]
        if images is not None:
            prompt["images"] = images
        if status == "running":
            if prompt_id in self.queue_pending:
                self.queue_pending.remove(prompt_id)
            if prompt_id not in self.queue_running:
                self.queue_running.append(prompt_id)
        if status == "completed":
            if prompt_id in self.queue_running:
                self.queue_running.remove(prompt_id)
        self._broadcast({"type": "status", "prompt_id": prompt_id, "status": status})

    async def _simulate_progression(self, prompt_id: str) -> None:
        await asyncio.sleep(0.01)
        self._update_status(prompt_id, "running")
        await asyncio.sleep(0.01)
        self._update_status(prompt_id, "completed", images=[_blank_image()])

    def register_routes(self) -> None:
        @self.app.get("/system_stats")
        def system_stats(request: Request):
            self._validate_auth(dict(request.headers))
            return {
                "status": "ok",
                "uptime": 1,
                "version": self.version,
                "features": list(self.features),
            }

        @self.app.get("/queue")
        def queue(request: Request):
            self._validate_auth(dict(request.headers))
            return {"queue_running": self.queue_running, "queue_pending": self.queue_pending}

        @self.app.post("/prompt")
        async def submit_prompt(
            body: PromptRequest, request: Request, background_tasks: BackgroundTasks
        ):
            self._validate_auth(dict(request.headers))
            prompt_id = f"job-{(body.seed or 0)}-{len(self.prompts) + 1}"
            self.prompts[prompt_id] = {
                "prompt_id": prompt_id,
                "status": "queued",
                "workflow_api": body.workflow_api,
                "prompt": body.prompt,
                "seed": body.seed,
                "batch_size": body.batch_size,
                "client_id": body.client_id,
                "extra_data": body.extra_data,
                "created_at": time.time(),
            }
            self.queue_pending.append(prompt_id)
            self._broadcast({"type": "queue", "prompt_id": prompt_id, "status": "queued"})
            background_tasks.add_task(self._simulate_progression, prompt_id)
            return self.prompts[prompt_id]

        @self.app.get("/history/{prompt_id}")
        def history(prompt_id: str):
            if prompt_id not in self.prompts:
                raise HTTPException(status_code=404, detail="Prompt not found")
            return {"history": {prompt_id: self.prompts[prompt_id]}}

        @self.app.websocket("/ws")
        async def websocket_updates(websocket: WebSocket):
            await websocket.accept()
            self.websocket_connections.append(websocket)
            try:
                await websocket.send_json(
                    {
                        "type": "hello",
                        "queue_pending": self.queue_pending,
                        "queue_running": self.queue_running,
                    }
                )
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                if websocket in self.websocket_connections:
                    self.websocket_connections.remove(websocket)

        @self.app.post("/upload/image")
        async def upload_image(file: UploadFile):
            data = await file.read()
            file_id = f"upload-{len(self.uploads) + 1}"
            self.uploads[file_id] = data
            return {"name": file.filename, "file_id": file_id, "size": len(data)}

        @self.app.get("/download/{file_id}")
        def download(file_id: str):
            if file_id not in self.uploads:
                raise HTTPException(status_code=404, detail="File not found")
            return StreamingResponse(
                io.BytesIO(self.uploads[file_id]), media_type="application/octet-stream"
            )

    def reset(self) -> None:
        self.prompts.clear()
        self.queue_pending.clear()
        self.queue_running.clear()
        self.uploads.clear()
        self.websocket_connections.clear()


stub = ComfyStub()
app = stub.app

