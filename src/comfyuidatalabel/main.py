from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Dict, List, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, model_validator
from sqlmodel import Session, select

from .database import get_engine, init_db
from .models import Annotation, Task, TaskPrompt, VariablePool, Worker, Workflow
from .orchestrator import SmartOrchestrator, WorkerRegistry

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    http_client = httpx.Client(timeout=2.5)
    app.state.http_client = http_client
    try:
        yield
    finally:
        http_client.close()


app = FastAPI(title="Comfyui Data Label Platform", version="1.0.0", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


def get_session():
    with Session(get_engine()) as session:
        yield session


def get_orchestrator(session: Session = Depends(get_session)) -> SmartOrchestrator:
    http_client = getattr(app.state, "http_client", None)
    return SmartOrchestrator(session, http_client=http_client)

# Schemas
class WorkflowCreate(BaseModel):
    name: str
    prompt_nodes: List[str] = Field(default_factory=list)
    seed_nodes: List[str] = Field(default_factory=list)
    max_workflow_batch_size: int = Field(gt=0)
    allow_controlnet: bool = False
    allow_dynamic_resolution: bool = False
    workflow_api: Dict[str, object] = Field(default_factory=dict)


class VariablePoolCreate(BaseModel):
    name: str
    version: str
    sampling_mode: str = Field(default="permutation")
    variables: Dict[str, List[str]] = Field(default_factory=dict)


class WorkerCreate(BaseModel):
    name: str
    base_url: str
    api_key: Optional[str] = None
    enabled: bool = True
    max_concurrent_jobs: int = Field(default=1, ge=1)
    tags: List[str] = Field(default_factory=list)


class TaskCreate(BaseModel):
    workflow_id: str
    variable_pool_id: str
    prompt_template: Optional[str] = None
    variable_input_mappings: List[Dict[str, str]] = Field(default_factory=list)
    client_id: Optional[str] = None
    extra_data: Dict[str, object] = Field(default_factory=dict)
    batch_size: int = Field(gt=0)
    seeds_per_prompt: int = Field(default=1, gt=0)
    target_prompts: int = Field(default=1, gt=0)


class AnnotationCreate(BaseModel):
    chosen_index: Optional[int] = Field(default=None, ge=0)
    rejected_index: Optional[int] = Field(default=None, ge=0)
    spam: bool = False
    comment: Optional[str] = None

    @model_validator(mode="after")
    def validate_annotation(self):  # type: ignore[override]
        if not self.spam and self.chosen_index is None:
            raise ValueError("chosen_index is required unless marked as spam")
        if (
            self.chosen_index is not None
            and self.rejected_index is not None
            and self.chosen_index == self.rejected_index
        ):
            raise ValueError("rejected_index cannot be the same as chosen_index")
        return self


# Admin endpoints
@app.post("/admin/workflows", response_model=Workflow)
def add_workflow(
    metadata: WorkflowCreate, orchestrator: SmartOrchestrator = Depends(get_orchestrator)
):
    return orchestrator.add_workflow(metadata.dict())


@app.post("/admin/variable-pools", response_model=VariablePool)
def add_variable_pool(
    pool: VariablePoolCreate, orchestrator: SmartOrchestrator = Depends(get_orchestrator)
):
    return orchestrator.add_variable_pool(pool.dict())


@app.post("/admin/workers", response_model=Worker)
def add_worker(
    worker: WorkerCreate,
    skip_check: bool = False,
    orchestrator: SmartOrchestrator = Depends(get_orchestrator),
):
    created = orchestrator.register_worker(worker.dict(), check=not skip_check)
    return created


@app.post("/admin/workers/health")
def trigger_health_check(session: Session = Depends(get_session)):
    registry = WorkerRegistry(session)
    registry.periodic_health_check()
    workers = session.exec(select(Worker)).all()
    return {"workers": workers}


# Task endpoints
@app.post("/tasks", response_model=Task)
def create_task(
    config: TaskCreate, orchestrator: SmartOrchestrator = Depends(get_orchestrator)
):
    try:
        task = orchestrator.create_task(config.dict())
        return task
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/tasks", response_model=List[Task])
def list_tasks(session: Session = Depends(get_session)):
    return session.exec(select(Task)).all()


@app.get("/tasks/{task_id}/prompts", response_model=List[TaskPrompt])
def list_prompts(task_id: str, session: Session = Depends(get_session)):
    return session.exec(select(TaskPrompt).where(TaskPrompt.task_id == task_id)).all()


@app.post("/tasks/{task_id}/pilot")
def run_pilot(task_id: str, orchestrator: SmartOrchestrator = Depends(get_orchestrator)):
    try:
        jobs = orchestrator.run_pilot(task_id)
        return {"jobs": jobs}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/tasks/{task_id}/freeze", response_model=Task)
def freeze_task(
    task_id: str, orchestrator: SmartOrchestrator = Depends(get_orchestrator)
):
    try:
        task = orchestrator.freeze_task(task_id)
        return task
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/tasks/{task_id}/generate")
def run_generation(
    task_id: str, orchestrator: SmartOrchestrator = Depends(get_orchestrator)
):
    try:
        jobs = orchestrator.generate(task_id)
        return {"jobs": jobs}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/prompts/{prompt_id}/annotations", response_model=Annotation)
def annotate(
    prompt_id: str,
    annotation: AnnotationCreate,
    orchestrator: SmartOrchestrator = Depends(get_orchestrator),
):
    return orchestrator.annotate(prompt_id, annotation.dict())


# UI endpoints
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    workflows = session.exec(select(Workflow)).all()
    pools = session.exec(select(VariablePool)).all()
    workers = session.exec(select(Worker)).all()
    tasks = session.exec(select(Task)).all()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "workflows": workflows,
            "pools": pools,
            "workers": workers,
            "tasks": tasks,
        },
    )


@app.get("/tasks/{task_id}", response_class=HTMLResponse)
def task_detail(task_id: str, request: Request, session: Session = Depends(get_session)):
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    prompts = session.exec(select(TaskPrompt).where(TaskPrompt.task_id == task.id)).all()
    return templates.TemplateResponse(
        "task.html",
        {
            "request": request,
            "task": task,
            "prompts": prompts,
        },
    )
