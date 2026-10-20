import base64
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402
from challenges import CHALLENGES  # noqa: E402


class PlatformTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_pages_and_assets(self):
        for route in ("/", "/terminal", "/editor", "/python", "/practice", "/api/health"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 200)
        for asset in (
            "/static/vendor/pyodide/pyodide.js",
            "/static/vendor/pyodide/pyodide.asm.js",
            "/static/vendor/pyodide/pyodide.asm.wasm",
            "/static/vendor/pyodide/python_stdlib.zip",
            "/static/vendor/pyodide/pyodide-lock.json",
        ):
            with self.subTest(asset=asset):
                response = self.client.get(asset)
                self.assertEqual(response.status_code, 200)
                self.assertGreater(len(response.data), 1000)

    def test_removed_career_content_and_coding_tasks(self):
        for route in ("/aca", "/api/aca/questions", "/interview", "/knowledge", "/skills", "/api/practice/check"):
            self.assertEqual(self.client.get(route).status_code, 404)
        self.assertGreaterEqual(len(CHALLENGES), 6)
        self.assertEqual(self.client.get("/api/health").get_json()["coding_challenges"], len(CHALLENGES))
        page = self.client.get("/practice").get_data(as_text=True)
        self.assertIn("代码任务", page)
        self.assertIn("shell-hello", page)
        self.assertIn("python-sum", page)
        self.assertNotIn("面试", page)
        shell_page = self.client.get("/editor?challenge=shell-hello").get_data(as_text=True)
        python_page = self.client.get("/python?challenge=python-sum").get_data(as_text=True)
        self.assertIn("Hello, Code Lab", shell_page)
        self.assertIn("numbers = [2, 4]", python_page)

    def test_reverse_proxy_prefix_links(self):
        response = self.client.get("/", headers={"X-Forwarded-Prefix": "/cloudops"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'href="/cloudops/terminal"', response.data)
        self.assertIn(b'href="/cloudops/static/style.css"', response.data)

    def test_optional_basic_authentication(self):
        old_auth_enabled = app.config.get("APP_AUTH_ENABLED")
        old_user = app.config.get("APP_USERNAME")
        old_password = app.config.get("APP_PASSWORD")
        app.config.update(APP_AUTH_ENABLED=True, APP_USERNAME="cloudops", APP_PASSWORD="test-password")
        try:
            response = self.client.get("/")
            self.assertEqual(response.status_code, 401)
            self.assertIn("Basic", response.headers["WWW-Authenticate"])

            token = base64.b64encode(b"cloudops:test-password").decode("ascii")
            response = self.client.get("/", headers={"Authorization": f"Basic {token}"})
            self.assertEqual(response.status_code, 200)

            self.assertEqual(self.client.get("/api/health").status_code, 200)
        finally:
            app.config.update(
                APP_AUTH_ENABLED=old_auth_enabled,
                APP_USERNAME=old_user,
                APP_PASSWORD=old_password,
            )

    def test_basic_authentication_can_be_disabled_for_tunnel_only_access(self):
        old_auth_enabled = app.config.get("APP_AUTH_ENABLED")
        old_user = app.config.get("APP_USERNAME")
        old_password = app.config.get("APP_PASSWORD")
        app.config.update(APP_AUTH_ENABLED=False, APP_USERNAME="cloudops", APP_PASSWORD="test-password")
        try:
            self.assertEqual(self.client.get("/").status_code, 200)
        finally:
            app.config.update(
                APP_AUTH_ENABLED=old_auth_enabled,
                APP_USERNAME=old_user,
                APP_PASSWORD=old_password,
            )

    def test_shell_sandbox_and_escape_block(self):
        response = self.client.post("/api/terminal", json={"command": "pwd", "cwd": "/home/student"})
        payload = response.get_json()
        self.assertEqual(payload["stdout"], "/home/student")
        self.assertIn("不调用本机终端", payload["shell"])

        response = self.client.post("/api/terminal", json={"command": "cat /../../README.md", "cwd": "/home/student"})
        payload = response.get_json()
        self.assertNotEqual(payload["returncode"], 0)
        self.assertIn("超出练习沙箱", payload["stderr"])

        response = self.client.post("/api/terminal", json={"command": "rm -rf /", "cwd": "/home/student"})
        payload = response.get_json()
        self.assertNotEqual(payload["returncode"], 0)
        self.assertIn("受保护", payload["stderr"])

    def test_shell_pipeline_and_loop(self):
        script = """#!/bin/bash
services="nginx mysql redis"
for service in $services; do
  echo "check $service"
done
cat /var/log/app.log | grep ERROR
"""
        response = self.client.post("/api/terminal", json={"command": script, "cwd": "/home/student"})
        payload = response.get_json()
        self.assertEqual(payload["returncode"], 0)
        self.assertIn("check nginx", payload["stdout"])
        self.assertIn("database connection timeout", payload["stdout"])

    def test_shell_script_crud_and_run(self):
        name = "_test_platform_temp.sh"
        try:
            response = self.client.post("/api/scripts/save", json={"name": name, "content": "echo safe-shell"})
            self.assertTrue(response.get_json()["ok"])
            response = self.client.post("/api/scripts/run", json={"name": name})
            payload = response.get_json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["stdout"], "safe-shell")
        finally:
            self.client.post("/api/scripts/delete", json={"name": name})

    def test_python_script_crud(self):
        name = "_test_platform_temp.py"
        try:
            response = self.client.post("/api/python-scripts/save", json={"name": name, "content": "print('browser sandbox')"})
            self.assertTrue(response.get_json()["ok"])
            response = self.client.get("/api/python-scripts/load", query_string={"name": name})
            self.assertIn("browser sandbox", response.get_json()["content"])
        finally:
            self.client.post("/api/python-scripts/delete", json={"name": name})


if __name__ == "__main__":
    unittest.main(verbosity=2)
