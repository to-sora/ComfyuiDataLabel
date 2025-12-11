from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.models.base import engine, Base
from backend.app.api import workers, workflows, variable_pools, tasks, annotations, exports
from backend.app.services.health_monitor import start_health_monitor, stop_health_monitor
from contextlib import asynccontextmanager

# Create tables
Base.metadata.create_all(bind=engine)

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_health_monitor()
    yield
    stop_health_monitor()

app = FastAPI(title="ComfyUI Data Labeling Platform", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workers.router, prefix="/api", tags=["Workers"])
app.include_router(workflows.router, prefix="/api", tags=["Workflows"])
app.include_router(variable_pools.router, prefix="/api", tags=["Variable Pools"])
app.include_router(tasks.router, prefix="/api", tags=["Tasks"])
app.include_router(annotations.router, prefix="/api", tags=["Annotations"])
app.include_router(exports.router, prefix="/api", tags=["Exports"])

@app.get("/health")
def health_check():
    return {"status": "ok"}
