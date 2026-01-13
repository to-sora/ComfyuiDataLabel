# ComfyuiDataLabel

A full-stack prototype for the Human-in-the-loop Data Curation Platform described in Software Requirement V3. It now ships with a FastAPI backend, SQLite persistence, worker registry, and a mobile-friendly HTML dashboard for admin/user flows.

## Features
- Register workflows with prompt/seed metadata and batch limits.
- Manage variable pools and GPU worker registry with health checks.
- Create tasks from variable pools using prompt templates, auto-generate seeds, and enforce batch limits.
- Pilot → Freeze → Mass generation orchestration tied to ComfyUI worker endpoints.
- Capture A/B/N annotations per prompt/seed pair.
- Mobile-first dashboard for admin + task operators.
- SQLite-backed persistence with API + simulated user-flow tests.

## Getting started
```bash
pip install -r requirements.txt
export DATABASE_URL=sqlite:///./comfyui_data_label.db  # or your own PostgreSQL/SQLite URL
uvicorn comfyuidatalabel.main:app --reload
```

## Running tests
```bash
pytest
```

## Frontend
- Dashboard: http://localhost:8000/
- Task detail & annotation: http://localhost:8000/tasks/{task_id}
