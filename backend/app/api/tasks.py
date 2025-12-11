from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from uuid import UUID
import json
import random
import itertools
from datetime import datetime
from backend.app.models.base import get_db
from backend.app.models.models import Task, Workflow, VariablePool, Prompt, Seed, Worker
from backend.app.services.comfy_client import ComfyUIClient

router = APIRouter()

class TaskCreate(BaseModel):
    workflow_id: UUID
    variable_pool_ids: List[UUID]
    target_prompts: int
    seeds_per_prompt: int
    notes: Optional[str] = None

class TaskResponse(BaseModel):
    id: UUID
    state: str
    prompts_count: int
    seeds_count: int

    class Config:
        from_attributes = True

@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(task_create: TaskCreate, db: Session = Depends(get_db)):
    workflow = db.query(Workflow).filter(Workflow.id == task_create.workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    pools = db.query(VariablePool).filter(VariablePool.id.in_(task_create.variable_pool_ids)).all()
    if len(pools) != len(task_create.variable_pool_ids):
        raise HTTPException(status_code=404, detail="Some variable pools not found")

    # Generate Prompts
    # Logic: Combine items from pools.
    # If multiple pools, we need to know how to combine them.
    # SRS implies: "System generates K independent prompts from Variable Pools".
    # Assuming standard combinatorial or random sampling to get K prompts.

    # Flatten items for simple random sampling if just 1 pool, or cartesian product if multiple?
    # SRS 3.1.2: "No-replacement Random Sampling" or "Permutation".
    # Let's assume for V3 we generate K combinations.

    all_items_lists = [p.items for p in pools]

    # Check total possible combinations without materializing
    total_combinations = 1
    for items in all_items_lists:
        total_combinations *= len(items)

    selected_combinations = []

    if total_combinations <= task_create.target_prompts:
        # If request asks for more than possible, give all
        selected_combinations = list(itertools.product(*all_items_lists))
    else:
        # Random sampling without replacement from cartesian product space
        # For huge spaces, we can sample indices or use rejection sampling.
        # Since we just need K distinct combinations, rejection sampling is safe enough
        # as long as K << total_combinations.

        seen_combos = set()
        while len(selected_combinations) < task_create.target_prompts:
            combo = tuple(random.choice(items) for items in all_items_lists)
            if combo not in seen_combos:
                seen_combos.add(combo)
                selected_combinations.append(combo)

    db_task = Task(
        workflow_id=task_create.workflow_id,
        state="DRAFT",
        notes=task_create.notes
    )
    db.add(db_task)
    db.commit()
    db.refresh(db_task)

    prompts_created = 0
    seeds_created = 0

    for combo in selected_combinations:
        # Construct prompt text - naive join for now, real system might need templates
        prompt_text = ", ".join(combo)

        db_prompt = Prompt(
            task_id=db_task.id,
            text=prompt_text,
            variables={"combo": combo}
        )
        db.add(db_prompt)
        db.commit() # Commit to get ID
        db.refresh(db_prompt)
        prompts_created += 1

        # Generate Seeds
        for _ in range(task_create.seeds_per_prompt):
            seed_val = random.randint(0, 2**32 - 1)
            db_seed = Seed(
                prompt_id=db_prompt.id,
                seed_value=seed_val
            )
            db.add(db_seed)
            seeds_created += 1

    db.commit()

    return TaskResponse(
        id=db_task.id,
        state=db_task.state,
        prompts_count=prompts_created,
        seeds_count=seeds_created
    )

@router.post("/tasks/{task_id}/pilot")
def pilot_run(task_id: UUID, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Pick heaviest prompt (simplified: just pick first for now)
    prompt = db.query(Prompt).filter(Prompt.task_id == task_id).first()
    if not prompt:
        raise HTTPException(status_code=400, detail="No prompts in task")

    # Find a healthy worker
    worker = db.query(Worker).filter(Worker.status == "HEALTHY", Worker.enabled == True).first()
    if not worker:
        raise HTTPException(status_code=503, detail="No healthy workers available")

    client = ComfyUIClient(base_url=worker.base_url)

    # Prepare workflow
    workflow = task.workflow
    workflow_graph = workflow.raw_definition.get("prompt", workflow.raw_definition)

    # Inject Prompt Text (Need to know which node is text)
    # This requires 'prompt_nodes' metadata from workflow
    # For Pilot, we use batch_size=1

    # SIMPLIFIED: Just submitting as is for structure check,
    # Real impl needs to modify graph based on prompt text and seed.

    try:
        # OOM Retry Logic (Mocked)
        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                # In real world: Modify graph to reduce resolution if retrying
                resp = client.submit_prompt(workflow_graph)
                return {"success": True, "prompt_id": resp.get("prompt_id"), "worker": worker.name}
            except Exception as e:
                # Check if OOM
                retries += 1
                if retries >= max_retries:
                     raise e
                # Logic to downscale would go here

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tasks/{task_id}/freeze")
def freeze_task(task_id: UUID, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Snapshot everything
    # In real impl, we'd query all prompts/seeds and store them in `frozen_snapshot` JSON
    # For now, just mark state

    task.state = "FROZEN"
    # task.frozen_snapshot = ... (Serialization of all prompts/seeds)

    db.commit()
    return {"state": "FROZEN", "frozen_at": datetime.now()}

from fastapi import BackgroundTasks
from backend.app.models.base import SessionLocal

def background_task_runner(task_id: UUID):
    # Create a fresh DB session for the background task
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task: return

        # Worker Selection
        workers = db.query(Worker).filter(Worker.enabled == True, Worker.status == "HEALTHY").all()
        if not workers:
            # Mark task as failed or stalled
            print("No workers available")
            return

        workers.sort(key=lambda w: (-w.priority, w.current_queue_len))
        selected_worker = workers[0]
        client = ComfyUIClient(base_url=selected_worker.base_url)

        # Process Prompts in Batches using yield_per for memory efficiency
        prompts_query = db.query(Prompt).filter(Prompt.task_id == task_id).yield_per(100)

        for prompt in prompts_query:
            seeds = db.query(Seed).filter(Seed.prompt_id == prompt.id).all()

            # Mock Workflow Injection Logic
            workflow_graph = task.workflow.raw_definition.get("prompt", task.workflow.raw_definition).copy()

            # Queue Depth Control
            try:
                client.wait_until_queue_below(selected_worker.max_concurrent_jobs)

                # Submit to ComfyUI
                # resp = client.submit_prompt(workflow_graph)

                # Record Generations (Mocked)
                # In real system, we would parse response or listen to webhook/ws
                # Here we just assume success and Create Generation entries
                for seed in seeds:
                    # Mock generation URI and create record
                    # In V3 requirements, this happens after image generation via listener or history polling
                    # But for this MVP flow demonstration:
                    pass

            except Exception as e:
                print(f"Failed prompt {prompt.id}: {e}")
                # Continue to next prompt
    except Exception as e:
        print(f"Background Task Error: {e}")
    finally:
        db.close()

@router.post("/tasks/{task_id}/run")
def run_task(task_id: UUID, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.state != "FROZEN":
        raise HTTPException(status_code=400, detail="Task must be FROZEN before running")

    # Update state immediately
    task.state = "RUNNING"
    db.commit()

    # Launch background task
    # IMPORTANT: Do NOT pass the `db` session from the request.
    background_tasks.add_task(background_task_runner, task_id)

    return {"state": "RUNNING", "message": "Mass generation started in background"}
