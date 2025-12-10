from __future__ import annotations

import os

import pytest
from sqlmodel import Session, SQLModel
from uuid import uuid4

CURRENT_DB = None

from comfyuidatalabel.database import get_engine, init_db  # noqa: E402
from comfyuidatalabel.models import TaskPrompt  # noqa: E402
from comfyuidatalabel.orchestrator import SmartOrchestrator  # noqa: E402
from .helpers import comfy_stub_client  # noqa: E402


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


def test_task_lifecycle_with_variable_pool():
    engine = get_engine()
    http_client = comfy_stub_client()
    with Session(engine) as session:
        orchestrator = SmartOrchestrator(session, http_client=http_client)
        workflow = orchestrator.add_workflow(
            {
                "name": "SDXL",
                "prompt_nodes": ["k_sampler"],
                "seed_nodes": ["seed"],
                "max_workflow_batch_size": 4,
            }
        )
        pool = orchestrator.add_variable_pool(
            {
                "name": "style",
                "version": "v1",
                "sampling_mode": "permutation",
                "variables": {"style": ["anime", "photo"], "lighting": ["soft", "hard"]},
            }
        )
        worker = orchestrator.register_worker(
            {
                "name": "gpu-a",
                "base_url": "http://comfy-stub",
                "enabled": True,
                "max_concurrent_jobs": 2,
            },
            check=False,
        )
        worker.status = "HEALTHY"
        session.add(worker)
        session.commit()

        task = orchestrator.create_task(
            {
                "workflow_id": workflow.id,
                "variable_pool_id": pool.id,
                "prompt_template": "a {style} render with {lighting} light",
                "batch_size": 2,
                "seeds_per_prompt": 1,
                "target_prompts": 3,
            }
        )

        pilot_jobs = orchestrator.run_pilot(task.id)
        assert len(pilot_jobs) >= 1

        orchestrator.freeze_task(task.id)
        mass_jobs = orchestrator.generate(task.id)
        assert len(mass_jobs) == len(task.prompts) - len(pilot_jobs)
        first_prompt = session.get(TaskPrompt, task.prompts[0].id)
        ann = orchestrator.annotate(first_prompt.id, {"choice": "A", "comment": "Great"})
        assert ann.choice == "A"


def test_variable_pool_insufficient_combinations():
    engine = get_engine()
    with Session(engine) as session:
        orchestrator = SmartOrchestrator(session)
        workflow = orchestrator.add_workflow(
            {
                "name": "Mini",
                "prompt_nodes": [],
                "seed_nodes": [],
                "max_workflow_batch_size": 2,
            }
        )
        pool = orchestrator.add_variable_pool(
            {
                "name": "simple",
                "version": "v1",
                "sampling_mode": "permutation",
                "variables": {"color": ["red"], "style": ["a"]},
            }
        )
        orchestrator.register_worker(
            {
                "name": "gpu-b",
                "base_url": "http://localhost:8188",
                "enabled": True,
                "max_concurrent_jobs": 1,
            },
            check=False,
        )
        with pytest.raises(ValueError):
            orchestrator.create_task(
                {
                    "workflow_id": workflow.id,
                    "variable_pool_id": pool.id,
                    "prompt_template": "{color}-{style}",
                    "batch_size": 1,
                    "seeds_per_prompt": 1,
                    "target_prompts": 5,
                }
            )
