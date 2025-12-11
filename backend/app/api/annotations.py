from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from backend.app.models.base import get_db
from backend.app.models.models import Annotation, Task, Prompt, Seed, Generation

router = APIRouter()

class BatchResponse(BaseModel):
    batch_id: UUID
    prompt_text: str
    seeds: List[int]
    thumbnails: List[str] # URLs
    fullres: List[str] # URLs

    class Config:
        from_attributes = True

class AnnotationCreate(BaseModel):
    task_id: UUID
    batch_id: UUID
    chosen_index: int
    rejected_index: Optional[int] = None
    spam: bool = False
    user_id: Optional[UUID] = None

@router.get("/tasks/{task_id}/batches", response_model=List[BatchResponse])
def get_task_batches(task_id: UUID, cursor: Optional[int] = 0, limit: int = 10, db: Session = Depends(get_db)):
    # In a real scenario, we group prompts and their generations into "batches".
    # A "batch" here logically means 1 Prompt + N Seeds (which is 1 Task Loop iteration).

    # Pagination via offset for simplicity
    prompts = db.query(Prompt).filter(Prompt.task_id == task_id).offset(cursor).limit(limit).all()

    batches = []
    for prompt in prompts:
        # Strict ordering by created_at to ensure consistent index mapping
        seeds = db.query(Seed).filter(Seed.prompt_id == prompt.id).order_by(Seed.created_at).all()
        seed_ids = [s.id for s in seeds]

        # Get generations for these seeds
        generations = db.query(Generation).filter(Generation.seed_id.in_(seed_ids)).all()
        # Map seed_id to image_uri
        gen_map = {g.seed_id: g.image_uri for g in generations}

        # Construct Batch
        # Note: batch_id logic needs to be consistent.
        # Using prompt_id as batch_id effectively since 1 prompt = 1 batch in V3 Dual-Loop

        # Mocking URIs if not present
        thumbnails = [gen_map.get(s.id, "http://placeholder/thumb.jpg") for s in seeds]
        fullres = [gen_map.get(s.id, "http://placeholder/full.jpg") for s in seeds]

        batches.append(BatchResponse(
            batch_id=prompt.id, # Using prompt_id as logical batch_id
            prompt_text=prompt.text,
            seeds=[s.seed_value for s in seeds],
            thumbnails=thumbnails,
            fullres=fullres
        ))

    return batches

@router.post("/annotations", status_code=status.HTTP_201_CREATED)
def create_annotation(annotation: AnnotationCreate, db: Session = Depends(get_db)):
    # Validate task
    task = db.query(Task).filter(Task.id == annotation.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Validate batch (prompt)
    prompt = db.query(Prompt).filter(Prompt.id == annotation.batch_id).first()
    if not prompt:
        raise HTTPException(status_code=404, detail="Batch (Prompt) not found")

    db_annotation = Annotation(
        task_id=annotation.task_id,
        batch_id=annotation.batch_id,
        prompt_id=annotation.batch_id, # redundant but explicit
        chosen_index=annotation.chosen_index,
        rejected_index=annotation.rejected_index,
        spam=annotation.spam,
        user_id=annotation.user_id,
        # variant_key logic for A/B testing would go here
    )
    db.add(db_annotation)
    db.commit()
    return {"status": "success"}
