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
Use the official ComfyUI HTTP routes (no need to rely on https://www.comfy.org/zh-cn/). Each endpoint below includes the exact
docs.comfy.org page so developers can implement without any other reference:

| Endpoint | Purpose | Key Payload/Response Notes | Reference |
| --- | --- | --- | --- |
| `POST {worker.base_url}/prompt` | Submit a workflow graph to run. | JSON body `{ "prompt": {<node graph>}, "client_id": "uuid" }`; returns `{ "prompt_id": "..." }`. Use the same graph format as downloaded `workflow_api.json`. | https://docs.comfy.org/development/comfyui-server/comms_routes#post-prompt |
| `GET {worker.base_url}/queue` | Inspect pending and running jobs. | Response `{ "queue_running": [...], "queue_pending": [...] }` used by queue-depth gating. | https://docs.comfy.org/development/comfyui-server/comms_routes#get-queue |
| `GET {worker.base_url}/history/{prompt_id}` | Fetch outputs and status for a finished prompt. | Returns node-level outputs and `status` for the given `prompt_id`; used to resolve CDN upload targets. | https://docs.comfy.org/development/comfyui-server/comms_routes#get-historyprompt_id |
| `POST {worker.base_url}/interrupt` | Interrupt all running jobs on the worker. | Empty body; returns 200 on success. Use when admin pauses a worker. | https://docs.comfy.org/development/comfyui-server/comms_routes#post-interrupt |
| `POST {worker.base_url}/free` | Ask the worker to free VRAM/resources. | Empty body; returns 200; run after large batches to avoid OOM. | https://docs.comfy.org/development/comfyui-server/comms_routes#post-free |

The linked routes cover every ComfyUI interaction used by this system, and the payload/response summaries above mirror the
official definitions. With these details in place, engineers can complete integrations without ever reading
https://www.comfy.org/zh-cn/.

### 2.7.1 ComfyUI Integration FAQ (edge cases & community-learned pitfalls)
Below are 30 focused Q&A items sourced from common community issues to ensure V3 can be implemented without any external reference beyond docs.comfy.org:
1) **Q: History payload is empty.** A: Call `GET /history/{prompt_id}` only after the job leaves `queue_running`; if the request races, wait 500ms and retry once using exponential backoff.
2) **Q: Images missing in history outputs.** A: Use the `outputs` map in history response; pick entries whose `type` is `output` and whose `subfolder`/`filename` pair exists—never assume `/output` paths.
3) **Q: Need download URL for CDN upload.** A: Combine worker `base_url` + `/view?filename=<name>&subfolder=<sub>` from history to fetch bytes; then push to S3/CDN and delete the temp file.
4) **Q: Worker returns 413 (payload too large).** A: Compress JSON with gzip or reduce embedded base64; ensure nginx/proxy `client_max_body_size` accommodates typical workflow graphs (1–5 MB).
5) **Q: Prompt submission sometimes times out.** A: Use 30s client timeout; server may queue. If HTTP times out, poll `/queue` for the `prompt_id` before re-submitting to avoid duplicates.
6) **Q: Duplicate prompt execution after network blip.** A: Track `client_id`; reuse the same `prompt_id` to dedupe. Only resubmit when `/queue` and `/history/{prompt_id}` both lack the id.
7) **Q: Need to cancel one task without stopping others.** A: Use `POST /interrupt` only when the worker is dedicated; otherwise mark generation as failed in DB and let the scheduler drain naturally—ComfyUI interrupt is global.
8) **Q: VRAM leak after large batches.** A: Call `POST /free` after each native batch to force cleanup; staggering `free` every N prompts reduces OOM risk in long runs.
9) **Q: Progressive results not visible.** A: ComfyUI HTTP API does not stream partials; use WebSocket routes only if strictly needed—otherwise rely on `history` after completion.
10) **Q: Need deterministic seeds across retries.** A: Persist seeds in DB and send them explicitly in the graph; never allow ComfyUI default random seeds.
11) **Q: LatentBatchSeedBehavior unsupported in workflow.** A: Fallback to `batch_size=1` loop per seed as mandated by V3; do not mutate the graph automatically.
12) **Q: ControlNet nodes requiring API keys.** A: Validate on upload and reject; forbidden nodes must be recorded in `workflows.forbidden_nodes` with a clear error to the Admin UI.
13) **Q: Queue depth gating accuracy.** A: Compute `pending + running` from `/queue`; treat missing keys as zero. Gating happens right before `POST /prompt`, not at task start.
14) **Q: Worker health flaps.** A: Mark worker UNHEALTHY after two consecutive failed `/system_stats` or `/queue` calls; require one clean pass to return to HEALTHY.
15) **Q: OOM during pilot.** A: Auto-retry up to three times lowering resolution or batch size per attempt; record retries in pilot response for UI visibility.
16) **Q: Need to resume after worker crash.** A: Re-query `/queue` to confirm emptiness, then re-run failed prompt_ids with the same graph; avoid resubmitting completed ones by checking `history` first.
17) **Q: Mixed precision mismatch.** A: Precision is a static parameter; ensure all workflow nodes expecting float16/32 align and do not switch mid-run.
18) **Q: Custom nodes not installed.** A: Workflow upload validation must detect missing node types and block task creation until installed; do not attempt dynamic installation.
19) **Q: Slow startup on cold GPU.** A: Warm-up by running a single small prompt before mass generation; exclude warm-up outputs from annotations/export.
20) **Q: Need per-variant CDN domains for A/B tests.** A: Use AB assignment result to pick CDN base URL when constructing annotation batch payloads; keep prompt/seed metadata identical across variants.
21) **Q: User retries annotation submit.** A: Idempotency via `(task_id, batch_id, user_id)` unique constraint; update row instead of inserting a duplicate when conflict occurs.
22) **Q: JSON schema drift between workflows.** A: Store `workflow.version` and `raw_definition`; upon freeze, capture snapshot to avoid later edits affecting frozen tasks.
23) **Q: Multi-worker ordering differences.** A: Ordering is not guaranteed; use `created_at` and `prompt_id` to sort when presenting batches to annotators.
24) **Q: Storage path collisions.** A: Include `task_id/prompt_id/seed` in CDN key; compute checksum on upload and persist for later integrity verification.
25) **Q: Seed overflow in databases.** A: Use `bigint` for seeds; avoid relying on 32-bit ints.
26) **Q: Need to throttle user-triggered pilots.** A: Rate-limit per user (e.g., 1 pilot per 30s) and reject with 429; never queue pilots when no HEALTHY worker exists.
27) **Q: Detect partially failed batches.** A: When `history` includes some but not all expected outputs, mark generation as `partial_failed` and resubmit only the missing seeds.
28) **Q: Handling stale websocket clients.** A: Frontend should re-fetch `/progress` every 5s; websocket disconnects must not block orchestrator logic.
29) **Q: Ensuring mobile 60 FPS during annotation.** A: Preload next batch images over CDN, use thumbnail rail for instant navigation, and defer non-critical analytics until after user action.
30) **Q: Ensuring compliance without comfy.org/zh-cn.** A: All Comms routes are fully specified at docs.comfy.org; rely on the URLs in the table above plus these FAQs—no other reference is required.

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

