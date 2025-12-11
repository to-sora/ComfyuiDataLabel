from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from uuid import UUID
import json
from backend.app.models.base import get_db
from backend.app.models.models import Task, Annotation, Prompt, Seed, Generation, VariablePool

router = APIRouter()

@router.get("/tasks/{task_id}/export/dpo")
def export_dpo(task_id: UUID, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    def generate_jsonl():
        # Join Annotation -> Prompt -> Generations
        # We need efficient querying. Iterating annotations is safer for 200k+ rows than loading all into memory.
        # Ideally, use server-side cursors or offset/limit batching.
        # For simplicity in this non-async driver setup, we'll iterate with yield from query.

        # Query: Get all annotations for this task that are NOT spam
        # We need to construct the DPO object for each.

        # Optimization: Pre-fetch pool version if needed.
        variable_pool_version = "v1" # Placeholder, ideally linked to VariablePool snapshots

        # Optimized query using joinedload to reduce N+1 problem
        # Note: We need to ensure models have proper relationships. I've added them in models.py.
        from sqlalchemy.orm import joinedload

        # We need to join Annotation -> Prompt -> Seeds -> Generations
        # But Annotation only links to Prompt via prompt_id (and batch_id).
        # We'll assume the model has relationship `prompt` defined in Annotation?
        # Checking models.py... Annotation does NOT have `prompt` relationship defined.
        # Let's define the join manually or rely on lazy load if relationship missing?
        # No, joinedload requires relationship.
        # We must add relationship to Annotation as well, or use explicit join().

        # Using explicit join and fetching columns might be faster but messier for JSON construction.
        # Let's assume we fixed Annotation model to have relationship too, or just query Prompt directly joined with everything
        # and filtering by Annotation existence?

        # Alternative: Query Annotation, then eager load Prompt?
        # Since I can't easily edit models.py again without context switch, let's use the explicit join approach
        # where we query the tuple structure.

        # Query: Annotation, Prompt, Seed, Generation
        # This results in Cartesian product if multiple seeds/generations.
        # Better: Query Annotation + Prompt + pre-fetched Seeds/Generations

        # Actually, I edited models.py to add Seed <-> Generation.
        # I should also add Annotation <-> Prompt to make joinedload work.
        # But let's just do it cleanly:

        # 1. Fetch Annotations (chunked)
        # 2. For each chunk, fetch Prompts + Seeds + Generations in one go.

        # But simpler logic for this exercise (given time constraints):
        # Just use the original logic but optimized slightly?
        # No, code review said fix N+1.

        # Let's use `db.query(Annotation, Prompt).join(Prompt, Annotation.prompt_id == Prompt.id)...`
        # And then for seeds, we still have N+1 if we don't join them.

        # Best approach given models:
        # Fetch Prompts that are annotated, eagerly loading Seeds and Generations.

        query = db.query(Prompt).join(Annotation, Annotation.prompt_id == Prompt.id).filter(
            Annotation.task_id == task_id,
            Annotation.spam == False
        ).options(
            joinedload(Prompt.seeds).joinedload(Seed.generations)
        ).yield_per(1000)

        # We also need the annotation data (chosen_index, rejected_index)
        # So we should select (Prompt, Annotation)

        results = db.query(Prompt, Annotation).join(
            Annotation, Annotation.prompt_id == Prompt.id
        ).filter(
            Annotation.task_id == task_id,
            Annotation.spam == False
        ).options(
            joinedload(Prompt.seeds).joinedload(Seed.generations)
        ).yield_per(1000)

        for prompt, ann in results:
            # Now prompt.seeds is populated with generations due to joinedload

            seeds = prompt.seeds # Should be loaded
            # Sort seeds by creation time just to be safe/deterministic
            seeds.sort(key=lambda s: s.created_at or datetime.min)

            # Map generation by seed
            # chosen_index refers to the index in the `seeds` list

            if ann.chosen_index >= len(seeds):
                continue

            chosen_seed = seeds[ann.chosen_index]
            # Assumes 1 generation per seed usually
            chosen_gen = chosen_seed.generations[0] if chosen_seed.generations else None

            if not chosen_gen or not chosen_gen.image_uri:
                continue

            chosen_obj = {
                "uri": chosen_gen.image_uri,
                "seed": chosen_seed.seed_value
            }

            rejected_list = []
            if ann.rejected_index is not None and ann.rejected_index < len(seeds):
                rejected_seed = seeds[ann.rejected_index]
                rejected_gen = rejected_seed.generations[0] if rejected_seed.generations else None

                if rejected_gen and rejected_gen.image_uri:
                    rejected_list.append({
                        "uri": rejected_gen.image_uri,
                        "seed": rejected_seed.seed_value
                    })

            record = {
                "prompt": prompt.text,
                "chosen": chosen_obj,
                "rejected": rejected_list,
                "metadata": {
                    "workflow_id": str(task.workflow_id),
                    "model": "SDXL",
                    "variable_pool_version": variable_pool_version,
                    "created_at": str(ann.created_at)
                }
            }

            yield json.dumps(record) + "\n"

    return StreamingResponse(generate_jsonl(), media_type="application/x-ndjson")
