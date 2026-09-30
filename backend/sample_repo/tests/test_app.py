import unittest

from app import app


class TestAppRoutes(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.client.testing = True

    def test_health_route(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_get_items_route(self):
        response = self.client.get("/items")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()["items"]), 3)

    def test_get_item_route(self):
        response = self.client.get("/items/1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["item"]["name"], "Keyboard")

    def test_get_missing_item_route(self):
        response = self.client.get("/items/99")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
