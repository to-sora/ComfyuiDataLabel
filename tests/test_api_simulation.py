from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel
from uuid import uuid4

CURRENT_DB = None

from comfyuidatalabel.main import app  # noqa: E402
from comfyuidatalabel.database import get_engine, init_db  # noqa: E402
from comfyuidatalabel.models import Worker  # noqa: E402
from .helpers import comfy_stub_client  # noqa: E402


@pytest.fixture(autouse=True)
def reset_db():
    global CURRENT_DB
    db_path = f"/tmp/test_{uuid4().hex}.db"
    CURRENT_DB = db_path
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    SQLModel.metadata.drop_all(get_engine())
    init_db()
    yield
    if CURRENT_DB and os.path.exists(CURRENT_DB):
        os.remove(CURRENT_DB)


def test_end_to_end_api_flow():
    app.state.http_client = comfy_stub_client()
    client = TestClient(app)

    workflow = client.post(
        "/admin/workflows",
        json={
            "name": "SDXL",
            "prompt_nodes": ["k_sampler"],
            "seed_nodes": ["seed"],
            "max_workflow_batch_size": 4,
        },
    ).json()
    pool_resp = client.post(
        "/admin/variable-pools",
        json={
            "name": "style",
            "version": "v1",
            "sampling_mode": "permutation",
            "variables": {"style": ["photo", "anime"], "lighting": ["soft", "hard"]},
        },
    )
    assert pool_resp.status_code == 200
    pool = pool_resp.json()

    worker = client.post(
        "/admin/workers",
        params={"skip_check": True},
        json={
            "name": "gpu-1",
            "base_url": "http://comfy-stub",
            "enabled": True,
            "max_concurrent_jobs": 1,
            "tags": ["sdxl"],
        },
    ).json()
    # Manually mark healthy for simulation
    with Session(get_engine()) as session:
        db_worker = session.get(Worker, worker["id"])
        db_worker.status = "HEALTHY"
        session.add(db_worker)
        session.commit()

    task_resp = client.post(
        "/tasks",
        json={
            "workflow_id": workflow["id"],
            "variable_pool_id": pool["id"],
            "prompt_template": "a {style} portrait with {lighting} light",
            "batch_size": 2,
            "seeds_per_prompt": 1,
            "target_prompts": 2,
        },
    )
    assert task_resp.status_code == 200
    task = task_resp.json()

    pilot = client.post(f"/tasks/{task['id']}/pilot")
    assert pilot.status_code == 200

    freeze = client.post(f"/tasks/{task['id']}/freeze")
    assert freeze.status_code == 200

    mass = client.post(f"/tasks/{task['id']}/generate")
    assert mass.status_code == 200

    prompts = client.get(f"/tasks/{task['id']}/prompts").json()
    ann = client.post(
        f"/prompts/{prompts[0]['id']}/annotations",
        json={"choice": "A", "comment": "good"},
    )
    assert ann.status_code == 200
