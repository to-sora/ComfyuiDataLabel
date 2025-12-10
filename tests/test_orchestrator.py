from __future__ import annotations

import os
from uuid import uuid4

import httpx
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
        ann = orchestrator.annotate(
            first_prompt.id,
            {"chosen_index": 0, "rejected_index": None, "spam": False, "comment": "Great"},
        )
        assert ann.chosen_index == 0
        assert ann.spam is False


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


def test_pilot_prefers_high_cost_prompt_and_forces_batch(monkeypatch):
    engine = get_engine()
    http_client = comfy_stub_client()
    with Session(engine) as session:
        orchestrator = SmartOrchestrator(session, http_client=http_client)
        workflow = orchestrator.add_workflow(
            {
                "name": "costly",
                "prompt_nodes": ["k_sampler"],
                "seed_nodes": ["seed"],
                "max_workflow_batch_size": 4,
                "workflow_api": {
                    "nodes": [
                        {"id": 1, "class_type": "KSampler", "inputs": {"width": 512, "height": 512}},
                        {"id": 2, "class_type": "ControlNetApply", "inputs": {"enabled": True}},
                    ]
                },
            }
        )
        pool = orchestrator.add_variable_pool(
            {
                "name": "resolutions",
                "version": "v1",
                "sampling_mode": "no_replacement",
                "variables": {
                    "width": [512, 1024],
                    "height": [512, 1024],
                    "control": [False, True],
                },
            }
        )
        worker = orchestrator.register_worker(
            {
                "name": "gpu-control",
                "base_url": "http://comfy-stub",
                "enabled": True,
                "max_concurrent_jobs": 1,
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
                "prompt_template": "image",
                "batch_size": 3,
                "seeds_per_prompt": 1,
                "target_prompts": 2,
                "variable_input_mappings": [
                    {"variable": "width", "node_id": 1, "input_name": "width"},
                    {"variable": "height", "node_id": 1, "input_name": "height"},
                    {"variable": "control", "node_id": 2, "input_name": "enabled"},
                ],
            }
        )

        pilot_jobs = orchestrator.run_pilot(task.id)
        assert len(pilot_jobs) == 1
        pilot_prompt = next(p for p in task.prompts if p.mode == "pilot")
        assert pilot_prompt.batch_size == 1
        assert pilot_prompt.applied_inputs["1"]["width"] == 1024
        assert pilot_prompt.applied_inputs["1"]["height"] == 1024
        assert pilot_prompt.applied_inputs["2"]["enabled"] is True
        assert pilot_jobs[0]["batch_size"] == "1"


def test_seed_lists_are_grouped_into_single_batch_submission():
    engine = get_engine()

    class CaptureClient:
        def __init__(self):
            self.payloads = []

        def post(self, url, json=None, headers=None):
            self.payloads.append(json)
            request = httpx.Request("POST", url)
            return httpx.Response(200, request=request, json={"prompt_id": "seeded-1", "status": "queued"})

        def get(self, url, headers=None):
            request = httpx.Request("GET", url)
            return httpx.Response(200, request=request, json={"queue_pending": [], "queue_running": []})

    http_client = CaptureClient()

    with Session(engine) as session:
        orchestrator = SmartOrchestrator(session, http_client=http_client)
        workflow = orchestrator.add_workflow(
            {
                "name": "seeded",
                "prompt_nodes": ["k_sampler"],
                "seed_nodes": ["seed"],
                "max_workflow_batch_size": 4,
                "workflow_api": {
                    "nodes": [
                        {"id": 1, "class_type": "KSampler", "inputs": {"seed": 0, "batch_size": 1}},
                        {"id": 2, "class_type": "LatentBatchSeedBehavior", "inputs": {}},
                    ]
                },
            }
        )
        pool = orchestrator.add_variable_pool(
            {
                "name": "seed-pool",
                "version": "v1",
                "variables": {"style": ["soft"]},
            }
        )
        worker = orchestrator.register_worker(
            {
                "name": "gpu-seeds",
                "base_url": "http://comfy-stub",
                "enabled": True,
                "max_concurrent_jobs": 1,
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
                "prompt_template": "render {style}",
                "batch_size": 3,
                "seeds_per_prompt": 3,
                "target_prompts": 1,
            }
        )

        orchestrator.run_pilot(task.id)

        assert http_client.payloads, "No payload submitted to ComfyUI"
        payload = http_client.payloads[0]
        assert payload["batch_size"] == 3
        assert len(payload.get("seed_list", [])) == 3
        sampler_inputs = payload["prompt"]["1"]["inputs"]
        behavior_inputs = payload["prompt"]["2"]["inputs"]
        assert sampler_inputs["seed"] == payload["seed_list"][0]
        assert sampler_inputs["batch_size"] == 3
        assert behavior_inputs["seed_list"] == payload["seed_list"]


def test_retries_reduce_resolution_on_oom(monkeypatch):
    engine = get_engine()

    class OOMClient:
        def __init__(self):
            self.payloads = []
            self.attempts = 0

        def post(self, url, json=None, headers=None):
            self.payloads.append(json)
            self.attempts += 1
            request = httpx.Request("POST", url)
            if self.attempts < 3:
                response = httpx.Response(500, request=request, text="CUDA out of memory")
                raise httpx.HTTPStatusError("OOM", request=request, response=response)
            return httpx.Response(200, request=request, json={"prompt_id": "p-final", "status": "queued"})

        def get(self, url, headers=None):
            request = httpx.Request("GET", url)
            return httpx.Response(404, request=request)

    http_client = OOMClient()

    with Session(engine) as session:
        orchestrator = SmartOrchestrator(session, http_client=http_client)
        workflow = orchestrator.add_workflow(
            {
                "name": "oomy",
                "prompt_nodes": ["k_sampler"],
                "seed_nodes": ["seed"],
                "max_workflow_batch_size": 4,
                "workflow_api": {"nodes": [{"id": 1, "class_type": "KSampler", "inputs": {"width": 1024, "height": 1024}}]},
            }
        )
        pool = orchestrator.add_variable_pool(
            {
                "name": "simple",
                "version": "v1",
                "variables": {"style": ["flat"]},
            }
        )
        worker = orchestrator.register_worker(
            {
                "name": "gpu-oom",
                "base_url": "http://comfy-stub",
                "enabled": True,
                "max_concurrent_jobs": 1,
            },
            check=False,
        )
        worker.status = "HEALTHY"
        session.add(worker)
        session.commit()

        orchestrator.registry.sync_queue_length = lambda worker: (0, 0, 0)

        task = orchestrator.create_task(
            {
                "workflow_id": workflow.id,
                "variable_pool_id": pool.id,
                "prompt_template": "{style}",
                "batch_size": 2,
                "seeds_per_prompt": 1,
                "target_prompts": 1,
            }
        )

        pilot_jobs = orchestrator.run_pilot(task.id)
        assert pilot_jobs[0]["prompt_id"] == "p-final"
        widths = [payload["prompt"]["1"]["inputs"]["width"] for payload in http_client.payloads]
        assert widths[0] > widths[-1]
        assert len(http_client.payloads) == 3


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
