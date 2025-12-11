from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from backend.app.models.base import get_db
from backend.app.models.models import Worker
from backend.app.services.comfy_client import ComfyUIClient

router = APIRouter()

class WorkerCreate(BaseModel):
    name: str
    base_url: str
    api_key: Optional[str] = None
    priority: int = 0
    max_concurrent_jobs: int = 1
    tags: Optional[List[str]] = []

class WorkerUpdate(BaseModel):
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    max_concurrent_jobs: Optional[int] = None
    tags: Optional[List[str]] = None

class WorkerResponse(BaseModel):
    id: UUID
    name: str
    base_url: str
    enabled: bool
    status: str
    priority: int
    max_concurrent_jobs: int
    current_queue_len: int
    last_checked_at: Optional[datetime]
    tags: Optional[List[str]]

    class Config:
        from_attributes = True

@router.get("/workers", response_model=List[WorkerResponse])
def get_workers(db: Session = Depends(get_db)):
    return db.query(Worker).all()

@router.post("/workers", response_model=WorkerResponse, status_code=status.HTTP_201_CREATED)
def create_worker(worker: WorkerCreate, db: Session = Depends(get_db)):
    # Validate base_url format (basic check)
    if not worker.base_url.startswith("http"):
         raise HTTPException(status_code=400, detail="base_url must start with http")

    db_worker = Worker(
        name=worker.name,
        base_url=worker.base_url,
        api_key=worker.api_key,
        priority=worker.priority,
        max_concurrent_jobs=worker.max_concurrent_jobs,
        tags=worker.tags,
        status="UNKNOWN" # Initial status
    )
    db.add(db_worker)
    db.commit()
    db.refresh(db_worker)
    return db_worker

@router.patch("/workers/{worker_id}", response_model=WorkerResponse)
def update_worker(worker_id: UUID, worker_update: WorkerUpdate, db: Session = Depends(get_db)):
    db_worker = db.query(Worker).filter(Worker.id == worker_id).first()
    if not db_worker:
        raise HTTPException(status_code=404, detail="Worker not found")

    update_data = worker_update.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_worker, key, value)

    db.commit()
    db.refresh(db_worker)
    return db_worker

@router.post("/workers/{worker_id}/test")
def test_worker(worker_id: UUID, db: Session = Depends(get_db)):
    db_worker = db.query(Worker).filter(Worker.id == worker_id).first()
    if not db_worker:
        raise HTTPException(status_code=404, detail="Worker not found")

    client = ComfyUIClient(base_url=db_worker.base_url)
    try:
        # Check system stats
        client.get_system_stats()
        # Check queue
        queue_data = client.get_queue()

        db_worker.status = "HEALTHY"
        db_worker.last_checked_at = datetime.now()
        db_worker.current_queue_len = len(queue_data.get("queue_pending", [])) + len(queue_data.get("queue_running", []))

        db.commit()
        return {"healthy": True, "queue": queue_data}
    except Exception as e:
        db_worker.status = "UNHEALTHY"
        db_worker.last_checked_at = datetime.now()
        db.commit()
        return {"healthy": False, "error": str(e)}
