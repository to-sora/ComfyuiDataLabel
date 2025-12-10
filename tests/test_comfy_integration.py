from __future__ import annotations

import os

import pytest
import os
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel

from comfyuidatalabel.database import get_engine, init_db
from comfyuidatalabel.orchestrator import SmartOrchestrator
from .helpers import comfy_stub_client


CURRENT_DB = None


def setup_function() -> None:
    global CURRENT_DB
    db_path = f"/tmp/test_{uuid4().hex}.db"
    CURRENT_DB = db_path
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    SQLModel.metadata.drop_all(get_engine())
    init_db()


def teardown_function() -> None:
    if CURRENT_DB and os.path.exists(CURRENT_DB):
        os.remove(CURRENT_DB)


def test_orchestrator_hits_comfy_stub():
    http_client = comfy_stub_client()

    engine = get_engine()
    with Session(engine) as session:
        orchestrator = SmartOrchestrator(session, http_client=http_client)
        workflow = orchestrator.add_workflow(
            {
                "name": "stubbed",
                "prompt_nodes": ["text"],
                "seed_nodes": ["seed"],
                "max_workflow_batch_size": 2,
            }
        )
        pool = orchestrator.add_variable_pool(
            {
                "name": "pose",
                "version": "v1",
                "sampling_mode": "permutation",
                "variables": {"pose": ["front", "side"]},
            }
        )
        worker = orchestrator.register_worker(
            {
                "name": "stub-worker",
                "base_url": "http://comfy-stub",
                "enabled": True,
                "max_concurrent_jobs": 1,
            },
            check=True,
        )

        assert worker.status == "HEALTHY"

        task = orchestrator.create_task(
            {
                "workflow_id": workflow.id,
                "variable_pool_id": pool.id,
                "prompt_template": "pose {pose}",
                "batch_size": 1,
                "seeds_per_prompt": 1,
                "target_prompts": 2,
            }
        )

        pilot_jobs = orchestrator.run_pilot(task.id)
        assert pilot_jobs[0]["prompt_id"].startswith("job-")
        orchestrator.freeze_task(task.id)
        mass_jobs = orchestrator.generate(task.id)
        assert len(mass_jobs) == len(task.prompts) - len(pilot_jobs)


def test_no_workers_available():
    engine = get_engine()
    with Session(engine) as session:
        orchestrator = SmartOrchestrator(session)
        workflow = orchestrator.add_workflow(
            {
                "name": "noworkers",
                "prompt_nodes": [],
                "seed_nodes": [],
                "max_workflow_batch_size": 1,
            }
        )
        pool = orchestrator.add_variable_pool(
            {
                "name": "style",
                "version": "v1",
                "sampling_mode": "permutation",
                "variables": {"style": ["soft"]},
            }
        )
        task = orchestrator.create_task(
            {
                "workflow_id": workflow.id,
                "variable_pool_id": pool.id,
                "prompt_template": "{style}",
                "batch_size": 1,
                "seeds_per_prompt": 1,
                "target_prompts": 1,
            }
        )
        with pytest.raises(RuntimeError):
            orchestrator.run_pilot(task.id)
