from __future__ import annotations

import copy
import itertools
import random
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence
from uuid import uuid4

import httpx
from httpx import Client
from sqlmodel import Session, select

from .models import Annotation, Task, TaskPrompt, VariablePool, Worker, Workflow


def _version_at_least(version: str, minimum: str) -> bool:
    def _parts(value: str) -> List[int]:
        return [int(part) for part in value.split(".") if part.isdigit()]

    current_parts = _parts(version)
    minimum_parts = _parts(minimum)
    length = max(len(current_parts), len(minimum_parts))
    current_parts.extend([0] * (length - len(current_parts)))
    minimum_parts.extend([0] * (length - len(minimum_parts)))
    return current_parts >= minimum_parts


class WorkerRegistry:
    def __init__(self, session: Session, http_client: Client | None = None):
        self.session = session
        self.http_client = http_client or httpx.Client(timeout=2.5)

    @staticmethod
    def _headers(worker: Worker) -> Dict[str, str]:
        if not worker.api_key:
            return {}
        return {"Authorization": f"Bearer {worker.api_key}", "X-API-Key": worker.api_key}

    def healthy_workers(self) -> List[Worker]:
        result = self.session.exec(
            select(Worker).where(Worker.enabled.is_(True), Worker.status == "HEALTHY")
        )
        return list(result)

    def select(self) -> Optional[Worker]:
        workers = sorted(self.healthy_workers(), key=lambda w: (w.current_jobs, w.name))
        for worker in workers:
            if worker.current_jobs < worker.max_concurrent_jobs:
                return worker
        return None

    def record_job(self, worker: Worker) -> None:
        worker.current_jobs += 1
        self.session.add(worker)
        self.session.commit()
        self.session.refresh(worker)

    def complete_job(self, worker: Worker) -> None:
        worker.current_jobs = max(worker.current_jobs - 1, 0)
        self.session.add(worker)
        self.session.commit()
        self.session.refresh(worker)

    def check_worker_health(
        self,
        worker: Worker,
        timeout: float = 2.5,
        *,
        min_version: str | None = None,
        required_features: Sequence[str] | None = None,
    ) -> Worker:
        headers = self._headers(worker)
        try:
            system_resp = self.http_client.get(f"{worker.base_url}/system_stats", headers=headers)
            queue_resp = self.http_client.get(f"{worker.base_url}/queue", headers=headers)
            system_resp.raise_for_status()
            queue_resp.raise_for_status()
            system_stats = system_resp.json()
            if min_version and not _version_at_least(system_stats.get("version", "0.0.0"), min_version):
                raise RuntimeError("Worker version is below the minimum supported")
            if required_features:
                features = set(system_stats.get("features", []))
                missing = set(required_features) - features
                if missing:
                    raise RuntimeError(f"Missing required features: {', '.join(sorted(missing))}")
            worker.status = "HEALTHY"
        except Exception:
            worker.status = "UNHEALTHY"
        worker.last_health_check = datetime.utcnow()
        self.session.add(worker)
        self.session.commit()
        self.session.refresh(worker)
        return worker

    def periodic_health_check(self) -> None:
        for worker in self.session.exec(select(Worker).where(Worker.enabled.is_(True))):
            self.check_worker_health(worker)
            time.sleep(0.1)


class SmartOrchestrator:
    def __init__(self, session: Session, http_client: Client | None = None):
        self.session = session
        self.http_client = http_client or httpx.Client(timeout=2.5)
        self.registry = WorkerRegistry(session, http_client=self.http_client)

    # Admin operations
    def add_workflow(self, metadata: Dict[str, object]) -> Workflow:
        workflow = Workflow(**metadata)
        self.session.add(workflow)
        self.session.commit()
        self.session.refresh(workflow)
        return workflow

    def add_variable_pool(self, payload: Dict[str, object]) -> VariablePool:
        pool = VariablePool(**payload)
        self.session.add(pool)
        self.session.commit()
        self.session.refresh(pool)
        return pool

    def register_worker(self, payload: Dict[str, object], *, check: bool = True) -> Worker:
        worker = Worker(**payload)
        self.session.add(worker)
        self.session.commit()
        self.session.refresh(worker)
        if check:
            self.registry.check_worker_health(worker)
        return worker

    # Task operations
    def create_task(self, payload: Dict[str, object]) -> Task:
        workflow = self._get_workflow(payload["workflow_id"])
        batch_size = payload.get("batch_size", 1)
        if batch_size > workflow.max_workflow_batch_size:
            raise ValueError("Batch size exceeds workflow limit")

        task = Task(**payload)
        task.updated_at = datetime.utcnow()
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)
        prompts = self._generate_prompts(task)
        self._persist_prompts(task, prompts)
        return task

    def run_pilot(self, task_id: str) -> List[Dict[str, str]]:
        task = self._get_task(task_id)
        workflow = self._get_workflow(task.workflow_id)
        if task.status != "draft":
            raise RuntimeError("Pilot can only run from draft state")
        worker = self._select_worker_or_raise()
        self.registry.record_job(worker)
        pilot_batch = [p for p in task.prompts if p.mode == "pilot"]
        if not pilot_batch:
            pilot_batch = task.prompts[: min(2, len(task.prompts))]
        jobs = [self._simulate_comfy_call(worker, workflow, p) for p in pilot_batch]
        task.status = "pilot_passed"
        task.updated_at = datetime.utcnow()
        self.session.add(task)
        self.session.commit()
        self.registry.complete_job(worker)
        return jobs

    def freeze_task(self, task_id: str) -> Task:
        task = self._get_task(task_id)
        if task.status != "pilot_passed":
            raise RuntimeError("Cannot freeze task before pilot passes")
        task.status = "frozen"
        task.updated_at = datetime.utcnow()
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)
        return task

    def generate(self, task_id: str) -> List[Dict[str, str]]:
        task = self._get_task(task_id)
        workflow = self._get_workflow(task.workflow_id)
        if task.status != "frozen":
            raise RuntimeError("Task must be frozen before generation")
        worker = self._select_worker_or_raise()
        if task.batch_size > workflow.max_workflow_batch_size:
            raise RuntimeError("Batch size exceeds workflow maximum")
        mass_prompts = [p for p in task.prompts if p.mode == "mass"]
        self.registry.record_job(worker)
        jobs = [self._simulate_comfy_call(worker, workflow, p) for p in mass_prompts]
        task.status = "completed"
        task.updated_at = datetime.utcnow()
        self.session.add(task)
        self.session.commit()
        self.registry.complete_job(worker)
        return jobs

    def annotate(self, task_prompt_id: str, payload: Dict[str, object]) -> Annotation:
        prompt = self._get_prompt(task_prompt_id)
        annotation = Annotation(task_prompt_id=prompt.id, **payload)
        self.session.add(annotation)
        self.session.commit()
        self.session.refresh(annotation)
        return annotation

    # Helpers
    def _get_task(self, task_id: str) -> Task:
        task = self.session.get(Task, task_id)
        if not task:
            raise ValueError("Task not found")
        return task

    def _get_workflow(self, workflow_id: str) -> Workflow:
        workflow = self.session.get(Workflow, workflow_id)
        if not workflow:
            raise ValueError("Workflow not found")
        return workflow

    def _get_prompt(self, prompt_id: str) -> TaskPrompt:
        prompt = self.session.get(TaskPrompt, prompt_id)
        if not prompt:
            raise ValueError("Prompt not found")
        return prompt

    def _select_worker_or_raise(self) -> Worker:
        worker = self.registry.select()
        if not worker:
            raise RuntimeError("No healthy workers available")
        return worker

    def _generate_prompts(self, task: Task) -> List[Dict[str, object]]:
        prompts: List[Dict[str, object]]
        if task.prompt_template:
            prompts = self._prompts_from_pool(task)
        else:
            raise ValueError("prompt_template is required to generate prompts from pools")
        workflow = self._get_workflow(task.workflow_id)
        seeds: List[int] = [random.randint(1, 2**31 - 1) for _ in range(task.seeds_per_prompt * len(prompts))]
        expanded = prompts * task.seeds_per_prompt
        return [
            {
                "prompt": prompt.get("prompt", ""),
                "applied_inputs": prompt.get("applied_inputs", {}),
                "seed": seeds[idx],
                "mode": "pilot" if idx < task.seeds_per_prompt else "mass",
                "batch_size": min(task.batch_size, workflow.max_workflow_batch_size),
            }
            for idx, prompt in enumerate(expanded)
        ]

    def _prompts_from_pool(self, task: Task) -> List[Dict[str, object]]:
        if not task.variable_pool_id:
            raise ValueError("Variable pool is required when using prompt templates")
        pool = self.session.get(VariablePool, task.variable_pool_id)
        if not pool:
            raise ValueError("Variable pool not found")
        variables = pool.variables
        slots = list(variables.keys())
        if pool.sampling_mode == "permutation":
            combos = itertools.product(*[variables[slot] for slot in slots])
        else:
            combos = zip(*[variables[slot] for slot in slots])
        prompts: List[Dict[str, object]] = []
        for combo in combos:
            prompt_vars = dict(zip(slots, combo))
            prompts.append({"prompt": task.prompt_template.format(**prompt_vars), "applied_inputs": {}})
            if len(prompts) >= task.target_prompts:
                break
        if len(prompts) < task.target_prompts:
            raise ValueError("Variable pool does not contain enough unique combinations")
        return prompts

    def _persist_prompts(self, task: Task, prompts: Iterable[Dict[str, object]]) -> None:
        records = [TaskPrompt(task_id=task.id, **payload) for payload in prompts]
        for record in records:
            self.session.add(record)
        self.session.commit()
        self.session.refresh(task)

    def _simulate_comfy_call(self, worker: Worker, workflow: Workflow, prompt: TaskPrompt) -> Dict[str, str]:
        prompt.worker_endpoint = f"{worker.base_url}/prompt"
        client_id = f"client-{uuid4().hex}"
        workflow_graph = workflow.workflow_api.get("workflow", workflow.workflow_api)
        workflow_inputs = workflow.workflow_api.get("inputs", {})
        extra_data = workflow.workflow_api.get("extra_data", {})
        workflow_with_overrides = self._with_overrides(workflow_inputs, prompt.applied_inputs)
        payload = {
            "prompt": workflow_graph,
            "client_id": client_id,
            "workflow_api": workflow_with_overrides,
            "extra_data": extra_data,
        }
        response = self.http_client.post(
            prompt.worker_endpoint,
            json=payload,
            headers=WorkerRegistry._headers(worker),
        )
        response.raise_for_status()
        prompt.status = "queued"
        prompt.client_id = client_id
        prompt.prompt_id = response.json().get("prompt_id")
        prompt.queued_at = datetime.utcnow()
        prompt.updated_at = datetime.utcnow()
        self.session.add(prompt)
        self.session.commit()
        self._track_prompt(worker, prompt)
        return {
            "worker": worker.base_url,
            "workflow": workflow.name,
            "prompt": prompt.prompt,
            "seed": str(prompt.seed),
            "mode": prompt.mode,
            "batch_size": str(prompt.batch_size),
            "endpoint": prompt.worker_endpoint,
            "prompt_id": prompt.prompt_id or "",
            "client_id": prompt.client_id or "",
            "status": prompt.status,
        }

    @staticmethod
    def _with_overrides(workflow_api: Dict[str, Any], overrides: Dict[str, Dict[str, object]]) -> Dict[str, object]:
        workflow = copy.deepcopy(workflow_api) if workflow_api is not None else {}
        if not overrides:
            return workflow
        nodes = workflow.get("nodes") if isinstance(workflow, dict) else None
        if isinstance(nodes, list):
            node_map = {str(node.get("id")): node for node in nodes if isinstance(node, dict)}
        elif isinstance(nodes, dict):
            node_map = {str(node_id): node for node_id, node in nodes.items() if isinstance(node, dict)}
        else:
            node_map = {}

        for node_id, inputs in overrides.items():
            node = node_map.get(str(node_id))
            if not node:
                continue
            current_inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            current_inputs.update(inputs)
            node["inputs"] = current_inputs
        return workflow

    def _track_prompt(self, worker: Worker, prompt: TaskPrompt) -> None:
        queue_endpoint = f"{worker.base_url}/queue"
        history_endpoint = f"{worker.base_url}/history/{prompt.client_id}"
        headers = WorkerRegistry._headers(worker)
        attempts = 0
        max_attempts = 10
        poll_interval = 0.25
        while attempts < max_attempts:
            attempts += 1
            queue_status = {}
            try:
                queue_status = self.http_client.get(queue_endpoint, headers=headers).json()
            except Exception:
                pass
            self._update_status_from_queue(prompt, queue_status)
            history_payload = {}
            try:
                history_payload = self.http_client.get(history_endpoint, headers=headers).json()
            except Exception:
                pass
            prompt_history = self._extract_prompt_history(history_payload, prompt.prompt_id)
            if prompt_history:
                status = prompt_history.get("status")
                outputs = prompt_history.get("outputs", {})
                error = prompt_history.get("error")
                if status:
                    prompt.status = status
                    if status == "running" and not prompt.started_at:
                        prompt.started_at = datetime.utcnow()
                    if status in {"completed", "success"}:
                        prompt.completed_at = datetime.utcnow()
                    if status in {"failed", "error"}:
                        prompt.failed_at = datetime.utcnow()
                        prompt.error = error or prompt_history.get("status_text")
                if outputs:
                    prompt.node_outputs = outputs
                if error and not prompt.error:
                    prompt.error = error
                prompt.updated_at = datetime.utcnow()
                self.session.add(prompt)
                self.session.commit()
                if status in {"completed", "success", "failed", "error"}:
                    break
            else:
                prompt.updated_at = datetime.utcnow()
                self.session.add(prompt)
                self.session.commit()
            time.sleep(poll_interval)

    def _extract_prompt_history(self, payload: Dict[str, object], prompt_id: Optional[str]) -> Dict[str, object]:
        history = payload.get("history") if isinstance(payload, dict) else None
        if isinstance(history, dict) and prompt_id:
            record = history.get(prompt_id)
            if isinstance(record, dict):
                return record
        if isinstance(payload, dict) and prompt_id and payload.get("prompt_id") == prompt_id:
            return payload
        if isinstance(payload, dict) and not prompt_id:
            return payload
        return {}

    def _update_status_from_queue(self, prompt: TaskPrompt, queue_status: Dict[str, object]) -> None:
        if not queue_status or not prompt.prompt_id:
            return
        running = queue_status.get("queue_running", []) or []
        pending = queue_status.get("queue_pending", []) or []
        if any(item.get("prompt_id") == prompt.prompt_id for item in running if isinstance(item, dict)):
            prompt.status = "running"
            prompt.started_at = prompt.started_at or datetime.utcnow()
        elif any(item.get("prompt_id") == prompt.prompt_id for item in pending if isinstance(item, dict)):
            prompt.status = "queued"
            prompt.queued_at = prompt.queued_at or datetime.utcnow()
        prompt.updated_at = datetime.utcnow()
        self.session.add(prompt)
        self.session.commit()
