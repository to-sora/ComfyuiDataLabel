"""Lightweight ComfyUI-compatible stub server.

This module exposes a FastAPI application that mirrors the minimum ComfyUI
routes we integrate with in tests and local development:

* ``GET /system_stats`` and ``GET /queue`` for health checks
* ``POST /prompt`` to accept a workflow payload and return a blank PNG image
  encoded as base64 so downstream callers can validate end-to-end behavior
  without GPU dependencies.
"""

from __future__ import annotations

import asyncio
import base64
import io
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional
from uuid import uuid4

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
    client_id: str
    extra_data: dict = Field(default_factory=dict)


@dataclass
class ComfyStub:
    app: FastAPI = FastAPI(title="Mock ComfyUI")
    api_key: Optional[str] = None
    version: str = "1.5.0"
    features: Iterable[str] = field(default_factory=lambda: ["controlnet", "dynamic_resolution"])

    jobs: Dict[str, dict] = field(default_factory=dict)
    queue_pending: List[str] = field(default_factory=list)
    queue_running: List[str] = field(default_factory=list)
    uploads: Dict[str, bytes] = field(default_factory=dict)
    websocket_connections: List[WebSocket] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.register_routes()

    # ------------------------------------------------------------------
    # Helpers
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

    def _broadcast(self, payload: dict) -> None:
        stale: List[WebSocket] = []
        for ws in self.websocket_connections:
            try:
                asyncio.create_task(ws.send_json(payload))
            except RuntimeError:
                stale.append(ws)
        for ws in stale:
            if ws in self.websocket_connections:
                self.websocket_connections.remove(ws)

    def _advance_jobs(self) -> None:
        for job_id, job in self.jobs.items():
            if job["status"] == "queued":
                job["status"] = "running"
                job["updated_at"] = time.time()
                if job_id in self.queue_pending:
                    self.queue_pending.remove(job_id)
                if job_id not in self.queue_running:
                    self.queue_running.append(job_id)
                self._broadcast({"type": "status", "prompt_id": job_id, "status": "running"})
            elif job["status"] == "running":
                job["status"] = "completed"
                job["outputs"] = self._next_outputs(job["prompt_id"])
                job["images"] = [_blank_image()]
                job["updated_at"] = time.time()
                if job_id in self.queue_running:
                    self.queue_running.remove(job_id)
                self._broadcast({"type": "status", "prompt_id": job_id, "status": "completed"})

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
        async def submit_prompt(
            body: PromptRequest, request: Request, background_tasks: BackgroundTasks
        ):
            self._validate_auth(dict(request.headers))
            prompt_id = f"job-{uuid4().hex[:8]}"
            self.jobs[prompt_id] = {
                "prompt_id": prompt_id,
                "client_id": body.client_id,
                "status": "queued",
                "submitted_at": time.time(),
                "outputs": {},
                "workflow_api": body.workflow_api,
                "prompt": body.prompt,
                "extra_data": body.extra_data,
                "images": [],
            }
            self.queue_pending.append(prompt_id)
            self._broadcast({"type": "queue", "prompt_id": prompt_id, "status": "queued"})
            background_tasks.add_task(self._simulate_progression, prompt_id)
            return {
                "prompt_id": prompt_id,
                "client_id": body.client_id,
                "status": "queued",
                "images": [_blank_image()],
            }

        @self.app.get("/history/{identifier}")
        def history(identifier: str, request: Request):
            self._validate_auth(dict(request.headers))
            self._advance_jobs()
            history_records = {}
            # identifier may be client_id or prompt_id
            if identifier in self.jobs:
                job = self.jobs[identifier]
                history_records[identifier] = {
                    "status": job["status"],
                    "outputs": job.get("outputs", {}),
                    "images": job.get("images", []),
                }
            else:
                for prompt_id, job in self.jobs.items():
                    if job.get("client_id") != identifier:
                        continue
                    history_records[prompt_id] = {
                        "status": job["status"],
                        "outputs": job.get("outputs", {}),
                        "images": job.get("images", []),
                    }
            if not history_records:
                raise HTTPException(status_code=404, detail="Prompt not found")
            return {"history": history_records}

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

    async def _simulate_progression(self, prompt_id: str) -> None:
        await asyncio.sleep(0.01)
        self._advance_jobs()
        await asyncio.sleep(0.01)
        self._advance_jobs()

    def reset(self) -> None:
        self.jobs.clear()
        self.queue_pending.clear()
        self.queue_running.clear()
        self.uploads.clear()
        self.websocket_connections.clear()


stub = ComfyStub()
app = stub.app

