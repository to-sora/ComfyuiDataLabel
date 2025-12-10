from datetime import datetime
from typing import Dict, List, Optional
from uuid import uuid4

from sqlalchemy import JSON, Column
from sqlmodel import Field, Relationship, SQLModel


class Workflow(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str
    prompt_nodes: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    seed_nodes: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    max_workflow_batch_size: int = Field(default=4, gt=0)
    allow_controlnet: bool = False
    allow_dynamic_resolution: bool = False
    workflow_api: Dict[str, object] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    tasks: list["Task"] = Relationship(back_populates="workflow")


class VariablePool(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str
    version: str
    sampling_mode: str = Field(default="permutation")  # permutation | no_replacement
    variables: Dict[str, List[str]] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    tasks: list["Task"] = Relationship(back_populates="variable_pool")

    @property
    def key(self) -> str:
        return f"{self.name}:{self.version}"


class Worker(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str
    base_url: str
    api_key: Optional[str] = None
    enabled: bool = True
    status: str = Field(default="UNKNOWN")  # HEALTHY | UNHEALTHY | UNKNOWN
    max_concurrent_jobs: int = Field(default=1, ge=1)
    current_jobs: int = Field(default=0, ge=0)
    tags: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    last_health_check: Optional[datetime] = None


class Task(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workflow_id: str = Field(foreign_key="workflow.id")
    variable_pool_id: Optional[str] = Field(default=None, foreign_key="variablepool.id")
    batch_size: int = Field(default=1, gt=0)
    seeds_per_prompt: int = Field(default=1, gt=0)
    target_prompts: int = Field(default=1, gt=0)
    prompt_template: Optional[str] = None
    variable_input_mappings: List[Dict[str, str]] = Field(default_factory=list, sa_column=Column(JSON))
    client_id: Optional[str] = None
    extra_data: Dict[str, object] = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = Field(default="draft")  # draft -> pilot_passed -> frozen -> generating -> completed
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    workflow: Workflow = Relationship(back_populates="tasks")
    variable_pool: Optional[VariablePool] = Relationship(back_populates="tasks")
    prompts: list["TaskPrompt"] = Relationship(back_populates="task")


class TaskPrompt(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    task_id: str = Field(foreign_key="task.id")
    prompt: str
    seed: int
    mode: str = Field(default="pilot")  # pilot | mass
    worker_endpoint: Optional[str] = None
    batch_size: int = Field(default=1, gt=0)
    status: str = Field(default="queued")
    applied_inputs: Dict[str, Dict[str, object]] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    task: Task = Relationship(back_populates="prompts")
    annotations: list["Annotation"] = Relationship(back_populates="task_prompt")


class Annotation(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    task_prompt_id: str = Field(foreign_key="taskprompt.id")
    choice: str
    comment: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    task_prompt: TaskPrompt = Relationship(back_populates="annotations")
