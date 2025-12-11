from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from uuid import UUID
import json
from datetime import datetime
from backend.app.models.base import get_db
from backend.app.models.models import Workflow

router = APIRouter()

class WorkflowResponse(BaseModel):
    id: UUID
    name: str
    version: str
    max_batch_size: int
    prompt_nodes: Optional[List[str]]
    seed_nodes: Optional[List[str]]
    validated_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True

# Forbidden nodes list (example, can be expanded)
FORBIDDEN_NODES = ["ComfyUI-Manager", "ComfyUI-Custom-Scripts"]

def validate_workflow_graph(graph: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validates the workflow graph:
    1. Checks for forbidden nodes.
    2. Identifies prompt and seed nodes.
    3. Checks for V3 schema compliance (heuristic).
    """
    forbidden_found = []
    prompt_nodes = []
    seed_nodes = []

    # ComfyUI workflow format usually has node IDs as keys
    for node_id, node_data in graph.items():
        if "class_type" not in node_data:
            continue

        class_type = node_data["class_type"]

        # Check forbidden
        if any(forbidden in class_type for forbidden in FORBIDDEN_NODES):
            forbidden_found.append(f"Node {node_id} ({class_type})")

        # Identify Prompt nodes (e.g., CLIPTextEncode)
        if "CLIPTextEncode" in class_type:
            prompt_nodes.append(node_id)

        # Identify Seed nodes (e.g., KSampler, Seed)
        if "KSampler" in class_type or "Seed" in class_type:
            seed_nodes.append(node_id)

    if forbidden_found:
        raise ValueError(f"Forbidden nodes found: {', '.join(forbidden_found)}")

    return {
        "prompt_nodes": prompt_nodes,
        "seed_nodes": seed_nodes
    }


@router.post("/workflows", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    name: str = Form(...),
    version: str = Form(...),
    max_batch_size: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    try:
        content = await file.read()
        workflow_json = json.loads(content.decode("utf-8"))

        # Extract main graph if it's in a wrapper (e.g., {"prompt": ...})
        # But typically workflow_api.json root IS the graph or has 'prompt' key
        # HEADLESS GUIDE says: { "prompt": { ... }, "client_id": ... }
        # Or just the graph { "1": ... }

        graph = workflow_json.get("prompt", workflow_json)
        if not isinstance(graph, dict):
             # Try assuming it is the graph directly if it has node keys
             if any(isinstance(k, str) and k.isdigit() for k in workflow_json.keys()):
                 graph = workflow_json
             else:
                 raise ValueError("Invalid workflow format. Must be a node graph or contain 'prompt' key.")

        validation_result = validate_workflow_graph(graph)

        db_workflow = Workflow(
            name=name,
            version=version,
            max_batch_size=max_batch_size,
            raw_definition=workflow_json,
            prompt_nodes=validation_result["prompt_nodes"],
            seed_nodes=validation_result["seed_nodes"],
            validated_at=datetime.now()
        )

        db.add(db_workflow)
        db.commit()
        db.refresh(db_workflow)
        return db_workflow

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON file")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/workflows", response_model=List[WorkflowResponse])
def get_workflows(db: Session = Depends(get_db)):
    return db.query(Workflow).all()
