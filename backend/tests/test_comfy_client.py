import unittest
from unittest.mock import MagicMock, patch
from backend.app.services.comfy_client import ComfyUIClient

class TestComfyUIClient(unittest.TestCase):
    def setUp(self):
        self.base_url = "http://localhost:8188"
        self.client = ComfyUIClient(base_url=self.base_url)

    @patch("requests.post")
    def test_submit_prompt(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {"prompt_id": "12345", "number": 1}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        workflow = {"3": {"class_type": "KSampler"}}
        result = self.client.submit_prompt(workflow, client_id="test-client")

        self.assertEqual(result["prompt_id"], "12345")
        mock_post.assert_called_once_with(
            f"{self.base_url}/prompt",
            json={"prompt": workflow, "client_id": "test-client"},
            timeout=30
        )

    @patch("requests.get")
    def test_get_queue(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"queue_pending": [], "queue_running": []}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = self.client.get_queue()
        self.assertEqual(result["queue_pending"], [])
        mock_get.assert_called_once_with(f"{self.base_url}/queue", timeout=30)

    @patch("requests.post")
    def test_submit_prompt_failure(self, mock_post):
        import requests
        mock_post.side_effect = requests.exceptions.RequestException("Connection Error")

        with self.assertRaises(RuntimeError):
            self.client.submit_prompt({})

if __name__ == '__main__':
    unittest.main()
