from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel

CURRENT_DB = None

from comfyuidatalabel.database import get_engine, init_db  # noqa: E402
from comfyuidatalabel.models import TaskPrompt  # noqa: E402
from comfyuidatalabel.orchestrator import SmartOrchestrator  # noqa: E402
from comfyuidatalabel.comfy_server import stub  # noqa: E402
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


def test_missing_healthy_workers_blocks_generation():
    engine = get_engine()
    http_client = comfy_stub_client()
    with Session(engine) as session:
        orchestrator = SmartOrchestrator(session, http_client=http_client)
        workflow = orchestrator.add_workflow(
            {
                "name": "Blocked",
                "prompt_nodes": ["k_sampler"],
                "seed_nodes": ["seed"],
                "max_workflow_batch_size": 1,
            }
        )
        pool = orchestrator.add_variable_pool(
            {
                "name": "simple",
                "version": "v1",
                "variables": {"style": ["flat"], "color": ["blue"]},
            }
        )
        task = orchestrator.create_task(
            {
                "workflow_id": workflow.id,
                "variable_pool_id": pool.id,
                "prompt_template": "{style}-{color}",
                "batch_size": 1,
                "seeds_per_prompt": 1,
                "target_prompts": 1,
            }
        )

        with pytest.raises(RuntimeError, match="healthy workers"):
            orchestrator.run_pilot(task.id)


def test_waits_for_worker_capacity_before_submitting():
    engine = get_engine()
    http_client = comfy_stub_client()
    with Session(engine) as session:
        orchestrator = SmartOrchestrator(session, http_client=http_client)
        workflow = orchestrator.add_workflow(
            {
                "name": "Capacity",
                "prompt_nodes": ["k_sampler"],
                "seed_nodes": ["seed"],
                "max_workflow_batch_size": 1,
            }
        )
        pool = orchestrator.add_variable_pool(
            {
                "name": "tiny",
                "version": "v1",
                "variables": {"style": ["simple"]},
            }
        )
        worker = orchestrator.register_worker(
            {
                "name": "gpu-busy",
                "base_url": "http://comfy-stub",
                "enabled": True,
                "max_concurrent_jobs": 1,
            },
            check=False,
        )
        worker.status = "HEALTHY"
        session.add(worker)
        session.commit()

        stub.queue_running.append("existing-job")

        def clear_queue():
            import time

            time.sleep(0.1)
            stub.queue_running.clear()

        import threading

        threading.Thread(target=clear_queue, daemon=True).start()

        task = orchestrator.create_task(
            {
                "workflow_id": workflow.id,
                "variable_pool_id": pool.id,
                "prompt_template": "image {style}",
                "batch_size": 1,
                "seeds_per_prompt": 1,
                "target_prompts": 1,
                "variable_input_mappings": [],
            }
        )

        pilot_jobs = orchestrator.run_pilot(task.id)
        assert pilot_jobs
        refreshed_worker = session.get(type(worker), worker.id)
        assert refreshed_worker.queue_length >= 0


def test_worker_selection_respects_priority_and_queue_length(monkeypatch):
    engine = get_engine()
    with Session(engine) as session:
        orchestrator = SmartOrchestrator(session)
        w1 = orchestrator.register_worker(
            {
                "name": "slow",
                "base_url": "http://comfy-stub",
                "max_concurrent_jobs": 3,
                "priority": 1,
            },
            check=False,
        )
        w2 = orchestrator.register_worker(
            {
                "name": "fast",
                "base_url": "http://comfy-stub",
                "max_concurrent_jobs": 3,
                "priority": 2,
            },
            check=False,
        )
        for worker in (w1, w2):
            worker.status = "HEALTHY"
            worker.queue_length = 1 if worker is w1 else 2
            session.add(worker)
        session.commit()

        orchestrator.registry.sync_queue_length = lambda worker: (
            worker.queue_length,
            worker.queue_length,
            0,
        )

        selected = orchestrator.registry.select()
        assert selected == w2
