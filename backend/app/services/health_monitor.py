from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
from datetime import datetime
from backend.app.models.base import SessionLocal
from backend.app.models.models import Worker
from backend.app.services.comfy_client import ComfyUIClient

scheduler = BackgroundScheduler()

def check_workers_health():
    """
    Periodic job to check health of all enabled workers.
    """
    db = SessionLocal()
    try:
        workers = db.query(Worker).filter(Worker.enabled == True).all()
        for worker in workers:
            client = ComfyUIClient(base_url=worker.base_url)
            try:
                # 30s timeout is implicit in client, maybe shorter for health check?
                client.get_system_stats()
                # If success
                worker.status = "HEALTHY"
                queue_data = client.get_queue()
                worker.current_queue_len = len(queue_data.get("queue_pending", [])) + len(queue_data.get("queue_running", []))
            except Exception:
                worker.status = "UNHEALTHY"

            worker.last_checked_at = datetime.now()

        db.commit()
    except Exception as e:
        print(f"Health Monitor Error: {e}")
    finally:
        db.close()

def start_health_monitor():
    if not scheduler.running:
        scheduler.add_job(check_workers_health, 'interval', seconds=30)
        scheduler.start()
        print("Worker Health Monitor started.")

def stop_health_monitor():
    if scheduler.running:
        scheduler.shutdown()
        print("Worker Health Monitor stopped.")
