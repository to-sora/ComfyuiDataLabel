from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from uuid import UUID
import random
import itertools
from backend.app.models.base import get_db
from backend.app.models.models import VariablePool

router = APIRouter()

class VariablePoolCreate(BaseModel):
    name: str
    mode: str # no_replacement, permutation
    items: List[str]

class VariablePoolResponse(BaseModel):
    id: UUID
    name: str
    mode: str
    items: List[str]

    class Config:
        from_attributes = True

@router.post("/variable-pools", response_model=VariablePoolResponse, status_code=status.HTTP_201_CREATED)
def create_variable_pool(pool: VariablePoolCreate, db: Session = Depends(get_db)):
    if pool.mode not in ["no_replacement", "permutation"]:
        raise HTTPException(status_code=400, detail="Invalid mode. Must be 'no_replacement' or 'permutation'")

    db_pool = VariablePool(
        name=pool.name,
        mode=pool.mode,
        items=pool.items
    )
    db.add(db_pool)
    db.commit()
    db.refresh(db_pool)
    return db_pool

@router.get("/variable-pools", response_model=List[VariablePoolResponse])
def get_variable_pools(db: Session = Depends(get_db)):
    return db.query(VariablePool).all()

@router.get("/variable-pools/{pool_id}/sample")
def sample_variable_pool(pool_id: UUID, count: int, db: Session = Depends(get_db)):
    db_pool = db.query(VariablePool).filter(VariablePool.id == pool_id).first()
    if not db_pool:
        raise HTTPException(status_code=404, detail="Variable Pool not found")

    if db_pool.mode == "no_replacement":
        if count > len(db_pool.items):
             raise HTTPException(status_code=400, detail="Requested count exceeds available items for no_replacement mode")
        samples = random.sample(db_pool.items, count)

    elif db_pool.mode == "permutation":
        # For permutation, it usually implies combining multiple pools, but here we have single pool endpoint.
        # "Permutation" in the context of a single pool might just mean allowing repetition or returning all?
        # Re-reading SRS V3: "Variable Pools: ... Permutation (排列組合)".
        # Usually this means Admin creates multiple pools (dress, lighting) and Task combines them.
        # So "Sample from ONE pool" might just be random choice WITH replacement if needed?
        # Or maybe this endpoint is just a helper.

        # However, for a single pool, "permutation" of items doesn't make much sense unless it means returning all?
        # Let's assume for this endpoint we just return random choices if permutation is implied as "free pick".
        # But wait, SRS says: "System must ensure Prompt generation function won't produce duplicate combinations."
        # This logic likely belongs in Task Creation where multiple pools are combined.

        # If this endpoint is just for previewing/fetching:
        samples = [random.choice(db_pool.items) for _ in range(count)]

    return {"samples": samples}
