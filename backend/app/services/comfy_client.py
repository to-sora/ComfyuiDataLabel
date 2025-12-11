import requests
import json
import time
from typing import Dict, Any, Optional, List

class ComfyUIClient:
    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout

    def submit_prompt(self, workflow_graph: Dict[str, Any], client_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Submits a workflow graph to run.
        POST {worker.base_url}/prompt
        """
        payload = {"prompt": workflow_graph}
        if client_id:
            payload["client_id"] = client_id

        try:
            resp = requests.post(f"{self.base_url}/prompt", json=payload, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            # Handle potential connection errors or timeouts
            raise RuntimeError(f"Failed to submit prompt to {self.base_url}: {e}")

    def get_queue(self) -> Dict[str, Any]:
        """
        Inspect pending and running jobs.
        GET {worker.base_url}/queue
        """
        try:
            resp = requests.get(f"{self.base_url}/queue", timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to get queue from {self.base_url}: {e}")

    def get_history(self, prompt_id: str) -> Dict[str, Any]:
        """
        Fetch outputs and status for a finished prompt.
        GET {worker.base_url}/history/{prompt_id}
        """
        try:
            resp = requests.get(f"{self.base_url}/history/{prompt_id}", timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to get history for {prompt_id} from {self.base_url}: {e}")

    def interrupt(self) -> bool:
        """
        Interrupt all running jobs on the worker.
        POST {worker.base_url}/interrupt
        """
        try:
            resp = requests.post(f"{self.base_url}/interrupt", timeout=self.timeout)
            resp.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to interrupt worker {self.base_url}: {e}")

    def free_memory(self) -> bool:
        """
        Ask the worker to free VRAM/resources.
        POST {worker.base_url}/free
        """
        try:
            resp = requests.post(f"{self.base_url}/free", timeout=self.timeout)
            resp.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to free memory on {self.base_url}: {e}")

    def get_system_stats(self) -> Dict[str, Any]:
        """
        Get system stats for health check.
        GET {worker.base_url}/system_stats
        """
        try:
            resp = requests.get(f"{self.base_url}/system_stats", timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to get system stats from {self.base_url}: {e}")

    def wait_until_queue_below(self, threshold: int, interval: float = 0.5) -> None:
        """
        Blocks until pending + running < threshold.
        """
        while True:
            queue_data = self.get_queue()
            pending = len(queue_data.get("queue_pending", []))
            running = len(queue_data.get("queue_running", []))
            if pending + running < threshold:
                break
            time.sleep(interval)
