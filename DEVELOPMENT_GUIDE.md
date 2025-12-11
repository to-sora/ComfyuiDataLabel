# ComfyuiDataLabel Developer Guide (V3 Reference)

> V3 requirements supersede V2. This guide gives backend endpoints, frontend surfaces, database schemas, JSON formats, storage strategy for 200k+ images, CDN preload, and A/B testing APIs. All ComfyUI calls must go through the worker registry and use worker.base_url (never hardcode localhost).

## 1. Architecture Overview
- **Admin module**: workflow upload/validation, worker registry, variable pools, safety limits.
- **Task module**: task setup → pilot run → freeze → mass generation, status dashboards.
- **Smart Orchestrator**: worker selection (priority → queue length), queue-depth gating, dual-loop batching, retries, health checks.
- **Annotation Workbench**: mobile-first labeling with prefetch, thumbnails, spam/chosen/rejected, and A/B/N.

## 2. Backend API (example contracts)
_All endpoints return `{ "success": boolean, "data": any, "error": string | null }` unless specified._

### 2.1 Worker Registry & Health
- `GET /api/workers`
  - **Response data**: array of `{ id, name, base_url, tags, status, enabled, priority, max_concurrent_jobs, current_queue_len, last_checked_at }`.
- `POST /api/workers`
  - **Payload**: `{ name, base_url, api_key?, priority: number, max_concurrent_jobs: number, tags?: string[] }`.
- `PATCH /api/workers/{id}`
  - **Payload**: any of `{ enabled, priority, max_concurrent_jobs, tags }`.
- `POST /api/workers/{id}/test`
  - Calls worker `GET /system_stats` and `GET /queue`; **response**: `{ healthy: boolean, queue: { pending, running } }`.
- Health job (30s) updates status; if no HEALTHY worker, pilot/mass generation requests must fail with 409 + message.

### 2.2 Workflow Management
- `POST /api/workflows`
  - Multipart with `workflow_api.json`, metadata. **Validates**: v3 schema, forbidden/API-key nodes, `Max_Workflow_Batch_Size`.
  - **Response data**: `{ id, name, version, max_batch_size, prompt_nodes: string[], seed_nodes: string[] }`.
- `GET /api/workflows`
  - List workflows with validation status.

### 2.3 Variable Pools
- `POST /api/variable-pools`
  - **Payload**: `{ name, mode: "no_replacement" | "permutation", items: string[] }`.
- `GET /api/variable-pools/{id}/sample?count=K`
  - Returns `K` prompts or variable combinations respecting mode (no duplication in permutation).

### 2.4 Task Lifecycle
- `POST /api/tasks`
  - **Payload**: `{ workflow_id, variable_pool_ids: string[], target_prompts: number, seeds_per_prompt: number, notes?: string }`.
  - **Response data**: `{ id, state: "DRAFT", prompts: Prompt[], seeds: number[][] }`.
- `POST /api/tasks/{id}/pilot`
  - Picks heaviest prompt, submits `/prompt` to selected worker with `batch_size=1`. On OOM retries up to 3 with lowered resolution/batch.
  - **Response**: `{ samples: ImageSample[10], worker_id, retries_used }`.
- `POST /api/tasks/{id}/freeze`
  - Locks prompts/seeds/workflow snapshot. **Response**: `{ state: "FROZEN", frozen_at }`.
- `POST /api/tasks/{id}/run`
  - Starts mass generation dual-loop. Queue gating: before each `/prompt`, ensure `pending + running < worker.max_concurrent_jobs` (0.5s polling). **Response**: `{ state: "RUNNING", worker_ids: string[] }`.
- `GET /api/tasks/{id}/progress`
  - **Response data**: `{ total, done, running, pending, failed, per_prompt: [{ prompt_id, done, failed, retries_used }] }`.

### 2.5 Annotation & A/B/N Testing
- `GET /api/tasks/{id}/batches?cursor=...`
  - Returns paged groups: `{ batch_id, prompt, seeds: number[], thumbnails: string[], fullres: string[] }`.
- `POST /api/annotations`
  - **Payload**: `{ task_id, batch_id, chosen_index: number, rejected_index?: number, spam: boolean }`.
- **A/B Testing API** (bucket allocation)
  - `POST /api/ab-tests`
    - Payload: `{ name, variants: [{ key: "A", weight: 0.5 }, { key: "B", weight: 0.5 }], expires_at }`.
  - `POST /api/ab-tests/assign`
    - Payload: `{ user_id, test_name }`; Response: `{ variant: "A" | "B" }` (sticky assignment).
  - Use variants to toggle UI prefetch strategies or CDN domains.

### 2.6 Export
- `GET /api/tasks/{id}/export/dpo`
  - Streams JSONL; each line `{ prompt, chosen: { uri, seed }, rejected: { uri, seed }[], workflow_id, model, variable_pool_version, created_at }`.

### 2.7 ComfyUI Passthrough (per worker)
- `POST {worker.base_url}/prompt`
- `GET {worker.base_url}/queue`
- `GET {worker.base_url}/history/{prompt_id}`
- `POST {worker.base_url}/interrupt`
- `POST {worker.base_url}/free`

## 3. Frontend Surfaces
- **Admin**
  - Worker list/detail with health badge, test button.
  - Workflow uploader + validation report (forbidden nodes, v3 schema).
  - Variable pool manager.
- **Task User**
  - Task creator (workflow, pools, K/N inputs, preview of generated prompts/seeds).
  - Pilot review page (10 samples with OOM retry notes).
  - Freeze confirmation snapshot (locked badge).
  - Mass generation dashboard (per-prompt progress, worker info, retry counts).
- **Annotation Workbench**
  - Mobile-first gallery with preloaded next/previous via CDN; swipe, pinch-to-zoom, thumbnail rail.
  - A/B/N selector capturing chosen/rejected/spam; displays prompt/seed metadata.
- **Exports**
  - Download DPO JSONL with URI previews and checksum status.

## 4. Database Schema (example)
Use PostgreSQL with partitioning for scale.

### 4.1 workers
```
id uuid PK
name text
base_url text
api_key text null
enabled boolean default true
status text check (HEALTHY/UNHEALTHY)
priority int default 0
max_concurrent_jobs int default 1
current_queue_len int default 0
last_checked_at timestamptz
created_at timestamptz
updated_at timestamptz
tags text[]
```

### 4.2 workflows
```
id uuid PK
name text
version text
max_batch_size int
prompt_nodes jsonb
seed_nodes jsonb
forbidden_nodes jsonb
raw_definition jsonb
validated_at timestamptz
created_at timestamptz
```

### 4.3 variable_pools
```
id uuid PK
name text
mode text -- no_replacement | permutation
items text[]
created_at timestamptz
```

### 4.4 tasks
```
id uuid PK
workflow_id uuid FK
state text -- DRAFT/FROZEN/RUNNING/DONE/FAILED
notes text
frozen_snapshot jsonb -- prompts, seeds, workflow version
created_at timestamptz
updated_at timestamptz
```

### 4.5 prompts
```
id uuid PK
task_id uuid FK
text text
variables jsonb
created_at timestamptz
```

### 4.6 seeds
```
id uuid PK
prompt_id uuid FK
seed_value bigint
created_at timestamptz
```

### 4.7 generations (partition by month)
```
id uuid PK
prompt_id uuid FK
seed_id uuid FK
worker_id uuid FK
state text -- pending/running/done/failed
image_uri text -- CDN/S3 path
checksum text
retries_used int default 0
metadata jsonb -- workflow version, model, resolution
created_at timestamptz
updated_at timestamptz
```

### 4.8 annotations
```
id uuid PK
batch_id uuid -- logical group per prompt+seed set
task_id uuid FK
prompt_id uuid FK
chosen_index int
rejected_index int null
spam boolean default false
created_at timestamptz
user_id uuid
variant_key text -- A/B bucket
```

### 4.9 ab_tests & ab_assignments
```
-- ab_tests
id uuid PK
name text unique
variants jsonb -- [{key:"A", weight:0.5}, ...]
expires_at timestamptz
created_at timestamptz

-- ab_assignments
id uuid PK
test_id uuid FK
user_id uuid
variant_key text
created_at timestamptz
unique (test_id, user_id)
```

## 5. JSON Payload Examples
### 5.1 Task Creation
```json
{
  "workflow_id": "2c3f...",
  "variable_pool_ids": ["pool-dress", "pool-lighting"],
  "target_prompts": 1000,
  "seeds_per_prompt": 4,
  "notes": "summer dresses"
}
```

### 5.2 Pilot Response
```json
{
  "success": true,
  "data": {
    "samples": [
      {"uri": "https://cdn.example.com/tasks/123/pilot/img_001.jpg", "seed": 12345678},
      {"uri": "https://cdn.example.com/tasks/123/pilot/img_002.jpg", "seed": 22345678}
    ],
    "worker_id": "a100-01",
    "retries_used": 1
  },
  "error": null
}
```

### 5.3 Annotation Submission
```json
{
  "task_id": "task-123",
  "batch_id": "batch-555",
  "chosen_index": 0,
  "rejected_index": 1,
  "spam": false
}
```

### 5.4 DPO JSONL Line
```json
{
  "prompt": "a model wearing summer dress, soft lighting",
  "chosen": {"uri": "s3://bucket/tasks/123/outputs/0001.jpg", "seed": 12345678},
  "rejected": [{"uri": "s3://bucket/tasks/123/outputs/0002.jpg", "seed": 22345678}],
  "workflow_id": "2c3f...",
  "model": "sdxl",
  "variable_pool_version": "v5",
  "created_at": "2025-01-01T12:00:00Z"
}
```

## 6. Storage & Scale (>200k Images)
- **Object storage**: S3/NAS with prefixes `tasks/{task_id}/YYYY/MM/DD/` and hashed shards to avoid hot prefixes.
- **CDN**: front storage with signed URLs; keep thumbnails (e.g., 256px) and full-res paths. Precompute checksums (SHA256) after upload.
- **Ingestion**: stream from ComfyUI temp to storage; do not rely on local `/output`. Use multipart upload for large files.
- **Partitioning**: `generations` table monthly partitions; nightly vacuum/compaction jobs; index on `(task_id, prompt_id)`.
- **Metadata cache**: Redis for per-prompt status to keep UI responsive when listing 200k+ images.
- **Prefetch**: UI fetches manifest `batches` containing thumbnail URLs for next/previous 20 items; use CDN `preconnect`/`prefetch` headers.

## 7. Orchestrator Rules
- Select workers: enabled + HEALTHY → sort by priority then `current_queue_len` → pick first.
- Queue gating: before `/prompt`, ensure `pending + running < max_concurrent_jobs`; poll every 0.5s.
- Batching: outer loop per prompt; inner native batch size = seeds_per_prompt using `LatentBatchSeedBehavior`; if node missing, fall back to batch_size=1 loop.
- Retries: ≤3 per prompt; failures isolated per prompt.
- Health: 30s checks via worker `/system_stats` and `/queue`; block pilot/mass if none healthy.
- No prompt mutation or hidden batch-size increases beyond admin `Max_Workflow_Batch_Size`.

## 8. Testing Standard (minimal)
- Health probe integration test (`/system_stats`, `/queue`) updates worker status and blocks runs when none healthy.
- Queue-depth gate test: submission refused when `pending + running >= max_concurrent_jobs`.
- Pilot OOM retry flow capped at 3 with resolution/batch downshift.
- Batch seed handling respects `LatentBatchSeedBehavior`; fallback loop works when node absent.
- Freeze immutability: prompts/seeds/workflow snapshot cannot change post-freeze; DPO export matches stored metadata/URIs.
- CDN preload test: next-20 thumbnails fetched; verify signed-URL validity and cache headers.

## 9. Deployment Notes
- External ComfyUI workers act as GPU nodes; orchestrator must never submit when no HEALTHY worker.
- Configure admin `Max_Workflow_Batch_Size`, ControlNet allowances, and resolution guardrails server-side.
- Default queue-depth threshold: 1 (configurable). Alerts on worker UNHEALTHY or queue backlog.

