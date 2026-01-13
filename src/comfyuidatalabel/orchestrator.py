from __future__ import annotations

import copy
import itertools
import json
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
    # Pad shorter list for comparison
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

    def select(self, candidates: Optional[Sequence[Worker]] = None) -> Optional[Worker]:
        workers = sorted(
            candidates if candidates is not None else self.healthy_workers(),
            key=lambda w: (-w.priority, w.queue_length, w.name),
        )
        for worker in workers:
            try:
                queue_length, _, _ = self.sync_queue_length(worker)
            except Exception:
                continue
            if queue_length < worker.max_concurrent_jobs:
                return worker
        return None

    def _queue_counts(self, worker: Worker) -> tuple[int, int]:
        headers = self._headers(worker)
        queue_resp = self.http_client.get(f"{worker.base_url}/queue", headers=headers)
        queue_resp.raise_for_status()
        queue_data = queue_resp.json()

        def _length(value: object) -> int:
            if isinstance(value, list):
                return len(value)
            if isinstance(value, int):
                return value
            return 0

        pending = _length(queue_data.get("queue_pending") or queue_data.get("pending"))
        running = _length(queue_data.get("queue_running") or queue_data.get("running"))
        return pending, running

    def sync_queue_length(self, worker: Worker) -> tuple[int, int, int]:
        pending = running = 0
        try:
            pending, running = self._queue_counts(worker)
            worker.queue_length = pending + running
        except Exception:
            worker.queue_length = 0
            raise
        finally:
            self.session.add(worker)
            self.session.commit()
            self.session.refresh(worker)
        return worker.queue_length, pending, running

    def record_job(self, worker: Worker) -> None:
        worker.current_jobs += 1
        worker.queue_length = max(worker.queue_length, worker.current_jobs)
        self.session.add(worker)
        self.session.commit()
        self.session.refresh(worker)

    def complete_job(self, worker: Worker) -> None:
        worker.current_jobs = max(worker.current_jobs - 1, 0)
        worker.queue_length = max(worker.queue_length - 1, worker.current_jobs)
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
            system_resp.raise_for_status()
            system_stats = system_resp.json()
            pending, running = self._queue_counts(worker)
            worker.queue_length = pending + running
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
            worker.queue_length = 0
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
        seeds_per_prompt = payload.get("seeds_per_prompt", 1)
        if seeds_per_prompt > workflow.max_workflow_batch_size:
            raise ValueError("Seeds per prompt exceeds workflow batch capacity")

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
        pilot_batch = self._pilot_batch(workflow, task.prompts)
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
        from .main import AnnotationCreate  # local import to avoid cycle

        validated = AnnotationCreate.model_validate(payload).model_dump()
        annotation = Annotation(task_prompt_id=prompt.id, **validated)
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
        healthy = self.registry.healthy_workers()
        if not healthy:
            raise RuntimeError("No healthy workers available to run prompts")
        worker = self.registry.select(candidates=healthy)
        if worker:
            return worker
        return healthy[0]

    def _generate_prompts(self, task: Task) -> List[Dict[str, object]]:
        prompts: List[Dict[str, object]]
        if task.prompt_template or task.variable_input_mappings:
            prompts = self._prompts_from_pool(task)
        else:
            raise ValueError("Provide a prompt_template or variable_input_mappings to generate prompts from pools")
        workflow = self._get_workflow(task.workflow_id)
        prompt_batch_size = min(task.batch_size, workflow.max_workflow_batch_size, task.seeds_per_prompt)
        prompt_records: List[Dict[str, object]] = []
        for prompt in prompts:
            seed_list = [random.randint(1, 2**31 - 1) for _ in range(task.seeds_per_prompt)]
            prompt_records.append(
                {
                    "prompt": prompt["prompt"],
                    "seed": seed_list[0],
                    "seed_list": seed_list,
                    "mode": "mass",
                    "batch_size": prompt_batch_size,
                    "applied_inputs": prompt.get("applied_inputs", {}),
                }
            )
        return prompt_records

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
            applied_inputs: Dict[str, Dict[str, object]] = {}
            for mapping in task.variable_input_mappings:
                variable_name = mapping.get("variable")
                node_id = mapping.get("node_id")
                input_name = mapping.get("input_name")
                if not variable_name or variable_name not in prompt_vars:
                    continue
                if not node_id or not input_name:
                    continue
                node_inputs = applied_inputs.setdefault(str(node_id), {})
                node_inputs[input_name] = prompt_vars[variable_name]

            prompt_text: str
            if task.prompt_template:
                prompt_text = task.prompt_template.format(**prompt_vars)
            elif applied_inputs:
                prompt_text = f"Node inputs: {json.dumps(applied_inputs)}"
            else:
                prompt_text = ""

            prompts.append({"prompt": prompt_text, "applied_inputs": applied_inputs})
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
        self._wait_for_capacity(worker)
        prompt.worker_endpoint = f"{worker.base_url}/prompt"
        prompt.client_id = prompt.client_id or prompt.task.client_id or f"client-{uuid4()}"
        workflow_graph = self._with_overrides(workflow.workflow_api, prompt.applied_inputs)
        result, adjusted_graph, used_batch, used_seeds = self._submit_with_retries(
            worker, prompt, workflow, workflow_graph
        )
        prompt.batch_size = used_batch
        prompt.seed_list = used_seeds
        prompt.prompt_id = result.get("prompt_id") or prompt.prompt_id
        prompt.status = result.get("status", "queued")
        prompt.queued_at = datetime.utcnow()
        prompt.updated_at = datetime.utcnow()
        self.session.add(prompt)
        self.session.commit()
        try:
            self.registry.sync_queue_length(worker)
        except Exception:
            pass
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
            "workflow_resolution": str(self._max_resolution(adjusted_graph)),
        }

    def _pilot_batch(self, workflow: Workflow, prompts: Sequence[TaskPrompt]) -> List[TaskPrompt]:
        if not prompts:
            return []

        def _cost_entry(prompt: TaskPrompt) -> tuple[int, Dict[str, int]]:
            score, details = self._prompt_cost(workflow, prompt)
            return score, details

        scored = [(_cost_entry(p), p) for p in prompts]
        scored.sort(key=lambda entry: entry[0][0], reverse=True)
        selected = [entry[1] for entry in scored[:10]]

        pilot_prompts: List[TaskPrompt] = []
        for prompt in selected:
            pilot_prompt = TaskPrompt(
                task_id=prompt.task_id,
                prompt=prompt.prompt,
                seed=prompt.seed,
                seed_list=list(prompt.seed_list),
                client_id=prompt.client_id,
                mode="pilot",
                batch_size=1,
                applied_inputs=copy.deepcopy(prompt.applied_inputs),
            )
            self.session.add(pilot_prompt)
            pilot_prompts.append(pilot_prompt)

        self.session.commit()
        for pilot_prompt in pilot_prompts:
            self.session.refresh(pilot_prompt)
        return pilot_prompts

    def _prompt_cost(self, workflow: Workflow, prompt: TaskPrompt) -> tuple[int, Dict[str, int]]:
        graph = self._with_overrides(workflow.workflow_api, prompt.applied_inputs)
        resolution = self._max_resolution(graph)
        controlnets = self._controlnet_count(graph)
        score = resolution * max(controlnets, 1)
        return score, {"resolution": resolution, "controlnets": controlnets}

    @staticmethod
    def _max_resolution(workflow_api: Dict[str, Any]) -> int:
        nodes = workflow_api.get("nodes") if isinstance(workflow_api, dict) else None
        if isinstance(nodes, list):
            node_iter = nodes
        elif isinstance(nodes, dict):
            node_iter = nodes.values()
        else:
            return 0

        max_res = 0

        def _to_int(value: Any) -> Optional[int]:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        for node in node_iter:
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            width = _to_int(inputs.get("width") or inputs.get("Width"))
            height = _to_int(inputs.get("height") or inputs.get("Height"))
            if width and height:
                max_res = max(max_res, width * height)
        return max_res

    @staticmethod
    def _controlnet_count(workflow_api: Dict[str, Any]) -> int:
        nodes = workflow_api.get("nodes") if isinstance(workflow_api, dict) else None
        if isinstance(nodes, list):
            node_iter = nodes
        elif isinstance(nodes, dict):
            node_iter = nodes.values()
        else:
            return 0
        count = 0
        for node in node_iter:
            if not isinstance(node, dict):
                continue
            class_type = str(node.get("class_type") or "").lower()
            if "controlnet" in class_type:
                count += 1
        return count

    def _submit_with_retries(
        self,
        worker: Worker,
        prompt: TaskPrompt,
        workflow: Workflow,
        workflow_graph: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, Any], int, List[int]]:
        attempts = [
            {"scale": 1.0, "batch_size": prompt.batch_size},
            {"scale": 0.75, "batch_size": prompt.batch_size},
            {"scale": 0.5, "batch_size": max(1, prompt.batch_size // 2)},
        ]

        last_error: Optional[Exception] = None
        headers = WorkerRegistry._headers(worker)
        base_graph = workflow_graph
        seeds = prompt.seed_list or [prompt.seed]

        for attempt in attempts:
            attempt_batch = min(attempt["batch_size"], workflow.max_workflow_batch_size, len(seeds))
            seeded_graph, seeds_used = self._apply_seed_batch(base_graph, seeds, workflow.seed_nodes, attempt_batch)
            scaled_graph = self._scale_resolution(seeded_graph, attempt["scale"])
            payload = self._build_payload(prompt, scaled_graph, attempt_batch, seeds_used)
            try:
                response = self.http_client.post(prompt.worker_endpoint, json=payload, headers=headers)
                response.raise_for_status()
                return response.json(), scaled_graph, attempt_batch, seeds_used
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if self._is_oom_response(exc.response):
                    continue
                raise
            except Exception as exc:  # pragma: no cover - network or unexpected errors
                last_error = exc
                break

        raise RuntimeError(f"Prompt submission failed after retries: {last_error}")

    def _build_payload(
        self, prompt: TaskPrompt, workflow_graph: Dict[str, Any], batch_size: int, seeds: List[int]
    ) -> Dict[str, Any]:
        prompt_payload = self._graph_keyed_by_node(workflow_graph)
        payload: Dict[str, Any] = {
            "prompt": prompt_payload,
            "client_id": prompt.client_id,
            "workflow_api": workflow_graph,
            "extra_data": prompt.task.extra_data or {},
            "batch_size": batch_size,
        }
        if seeds:
            payload["seed"] = seeds[0]
            payload["seed_list"] = seeds
        return payload

    @staticmethod
    def _apply_seed_batch(
        workflow_api: Dict[str, Any], seeds: Sequence[int], seed_inputs: Sequence[str], batch_size: int
    ) -> tuple[Dict[str, Any], List[int]]:
        graph = copy.deepcopy(workflow_api) if workflow_api is not None else {}
        seeds_to_use = list(seeds)[: batch_size or len(seeds)]
        nodes = graph.get("nodes") if isinstance(graph, dict) else None
        if isinstance(nodes, list):
            node_iter = nodes
        elif isinstance(nodes, dict):
            node_iter = nodes.values()
        else:
            return graph, seeds_to_use

        seed_keys = set(str(key) for key in (seed_inputs or ["seed"]))

        for node in node_iter:
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            if node.get("class_type") == "LatentBatchSeedBehavior" and seeds_to_use:
                inputs["seed_behavior"] = inputs.get("seed_behavior") or "fixed"
                inputs["seed_list"] = seeds_to_use
            if batch_size:
                if "batch_size" in inputs:
                    inputs["batch_size"] = batch_size
            for key in seed_keys:
                if key in inputs and seeds_to_use:
                    inputs[key] = seeds_to_use[0]
            node["inputs"] = inputs
        return graph, seeds_to_use

    @staticmethod
    def _scale_resolution(workflow_api: Dict[str, Any], scale: float) -> Dict[str, Any]:
        if scale == 1.0:
            return copy.deepcopy(workflow_api)
        scaled = copy.deepcopy(workflow_api) if workflow_api is not None else {}
        nodes = scaled.get("nodes") if isinstance(scaled, dict) else None
        if isinstance(nodes, list):
            node_iter = nodes
        elif isinstance(nodes, dict):
            node_iter = nodes.values()
        else:
            return scaled

        for node in node_iter:
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}

            def _scale_value(value: Any) -> Any:
                try:
                    return max(1, int(int(value) * scale))
                except (TypeError, ValueError):
                    return value

            for key in ("width", "Width", "height", "Height"):
                if key in inputs:
                    inputs[key] = _scale_value(inputs[key])
            node["inputs"] = inputs
        return scaled

    @staticmethod
    def _is_oom_response(response: httpx.Response | None) -> bool:
        if response is None:
            return False
        text = "" if response.text is None else response.text
        return "out of memory" in text.lower()

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

    @staticmethod
    def _graph_keyed_by_node(workflow_api: Dict[str, Any]) -> Dict[str, Any]:
        nodes = workflow_api.get("nodes") if isinstance(workflow_api, dict) else None
        if isinstance(nodes, list):
            return {str(node.get("id")): node for node in nodes if isinstance(node, dict)}
        if isinstance(nodes, dict):
            return {str(node_id): node for node_id, node in nodes.items() if isinstance(node, dict)}
        return {}

    def _wait_for_capacity(self, worker: Worker, *, backoff: float = 0.5) -> None:
        while True:
            try:
                queue_length, pending, running = self.registry.sync_queue_length(worker)
            except Exception as exc:
                raise RuntimeError(f"Worker {worker.name} unavailable: {exc}") from exc
            if pending + running < worker.max_concurrent_jobs:
                return
            time.sleep(backoff)

    def _track_prompt(self, worker: Worker, prompt: TaskPrompt, timeout: float = 2.0) -> None:
        if not prompt.prompt_id:
            return
        headers = WorkerRegistry._headers(worker)
        start = time.time()
        last_status = prompt.status
        while time.time() - start < timeout:
            try:
                history_resp = self.http_client.get(
                    f"{worker.base_url}/history/{prompt.prompt_id}", headers=headers
                )
                if history_resp.status_code == 404:
                    time.sleep(0.05)
                    continue
                history_resp.raise_for_status()
                history = history_resp.json().get("history", {}).get(prompt.prompt_id, {})
                if history:
                    self._apply_history(prompt, history)
                    if history.get("status") in {"completed", "failed", "error"}:
                        break
                    last_status = history.get("status", last_status)
            except Exception:
                break
            time.sleep(0.05)
        prompt.status = prompt.status or last_status or "queued"
        prompt.updated_at = datetime.utcnow()
        self.session.add(prompt)
        self.session.commit()
        self.session.refresh(prompt)

    def _apply_history(self, prompt: TaskPrompt, history: Dict[str, Any]) -> None:
        status = history.get("status")
        if status:
            prompt.status = status
        created_at = history.get("created_at")
        started_at = history.get("started_at") or history.get("started")
        completed_at = history.get("completed_at") or history.get("completed")
        if created_at:
            prompt.queued_at = datetime.fromtimestamp(created_at)
        if started_at:
            prompt.started_at = datetime.fromtimestamp(started_at)
        if completed_at:
            prompt.completed_at = datetime.fromtimestamp(completed_at)
        prompt.updated_at = datetime.utcnow()
        if "images" in history:
            prompt.node_outputs["images"] = history.get("images")
        if "outputs" in history:
            prompt.node_outputs["outputs"] = history.get("outputs")
        if history.get("error"):
            prompt.node_outputs["error"] = history["error"]
