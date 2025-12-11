from fastapi.testclient import TestClient
from backend.app.main import app
import unittest
from unittest.mock import MagicMock, patch

client = TestClient(app)

class TestWorkerAPI(unittest.TestCase):

    def test_create_worker(self):
        response = client.post("/api/workers", json={
            "name": "Test Worker",
            "base_url": "http://localhost:8188",
            "priority": 10
        })
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["name"], "Test Worker")
        self.assertEqual(data["status"], "UNKNOWN")

    @patch("backend.app.api.workers.ComfyUIClient")
    def test_test_worker(self, MockClient):
        # Create a worker first
        create_resp = client.post("/api/workers", json={
            "name": "Test Worker 2",
            "base_url": "http://localhost:8189"
        })
        worker_id = create_resp.json()["id"]

        # Mock ComfyUI Client
        mock_instance = MockClient.return_value
        mock_instance.get_system_stats.return_value = {}
        mock_instance.get_queue.return_value = {"queue_pending": [], "queue_running": []}

        # Test health check
        response = client.post(f"/api/workers/{worker_id}/test")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["healthy"])

if __name__ == '__main__':
    unittest.main()
