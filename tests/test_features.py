# -*- coding: utf-8 -*-
"""判题、Shell 语法、进度持久化的回归测试。"""

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402
from app import app, rate_limited  # noqa: E402
from challenges import CHALLENGES, CHALLENGES_BY_ID  # noqa: E402
from judge import accepted_outputs, judge, normalize  # noqa: E402
from progress import ProgressStore  # noqa: E402
from sandbox_shell import VirtualShell  # noqa: E402


class JudgeTests(unittest.TestCase):
    def test_normalize_ignores_crlf_and_trailing_spaces(self):
        self.assertEqual(normalize("\ufeffa  \r\nb\r\n\r\n"), "a\nb")
        self.assertEqual(normalize(None), "")

    def test_accepts_multiple_outputs(self):
        challenge = {"expected": ["2", "2.0"]}
        self.assertTrue(judge(challenge, "2.0\n")["passed"])
        self.assertFalse(judge(challenge, "3")["passed"])

    def test_regex_output(self):
        self.assertTrue(judge({"expected_regex": r"^PID=\d+$"}, "PID=42")["passed"])
        self.assertFalse(judge({"expected_regex": r"^PID=\d+$"}, "PID=abc")["passed"])

    def test_strict_stderr_and_nonzero_exit(self):
        self.assertFalse(judge({"expected": "ok", "strict_stderr": True}, "ok", "warn")["passed"])
        self.assertFalse(judge({"expected": "ok"}, "ok", "", 2)["passed"])
        self.assertTrue(judge({"expected": "ok", "allow_nonzero": True}, "ok", "", 2)["passed"])


class ShellSyntaxTests(unittest.TestCase):
    def setUp(self):
        self.shell = VirtualShell(tempfile.mkdtemp(prefix="codenova-shell-"))

    def run_script(self, script):
        return self.shell.execute_script(script)

    def test_and_or_chains(self):
        result = self.run_script("echo a && echo b")
        self.assertEqual(result["stdout"], "a\nb")
        result = self.run_script("cat /nope.txt || echo fallback")
        self.assertEqual(result["stdout"], "fallback")
        self.assertEqual(result["returncode"], 0)

    def test_glob_and_command_substitution(self):
        self.assertEqual(self.run_script("cd /home/student && ls *.txt")["stdout"], "README.txt")
        self.assertEqual(self.run_script("echo $(whoami)")["stdout"], "student")

    def test_arithmetic_expansion(self):
        self.assertEqual(self.run_script("echo $((2+3))")["stdout"], "5")
        self.assertEqual(
            self.run_script("i=0; while [ $i -lt 3 ]; do echo $i; i=$((i+1)); done")["stdout"],
            "0\n1\n2",
        )

    def test_if_block(self):
        script = "if [ -f /var/log/app.log ]; then echo has-log; else echo no-log; fi"
        self.assertEqual(self.run_script(script)["stdout"], "has-log")
        script = "if [ -d /nope ]; then echo dir; else echo not-dir; fi"
        self.assertEqual(self.run_script(script)["stdout"], "not-dir")

    def test_stderr_redirect(self):
        script = "ls /nope.txt 2> /tmp/err.txt; cat /tmp/err.txt"
        result = self.run_script(script)
        self.assertEqual(result["stderr"], "")
        self.assertIn("No such file", result["stdout"])

    def test_text_tools_read_files(self):
        self.assertEqual(
            self.run_script("sort /home/student/projects/services.txt")["stdout"],
            "mysql active\nnginx active\nredis inactive",
        )
        self.assertEqual(self.run_script("grep -c ERROR /var/log/app.log")["stdout"], "1")
        self.assertEqual(
            self.run_script("head -n 2 /home/student/projects/services.txt")["stdout"],
            "nginx active\nmysql active",
        )
        self.assertEqual(self.run_script("echo -e 'a\\nb' | head -n 2")["stdout"], "a\nb")

    def test_systemctl_keeps_state(self):
        self.assertIn("inactive", self.run_script("systemctl stop nginx; systemctl status nginx")["stdout"])
        self.assertIn("active", self.run_script("systemctl start nginx; systemctl status nginx")["stdout"])

    def test_history_and_man(self):
        self.run_script("echo first")
        self.assertIn("echo first", self.run_script("history")["stdout"])
        self.assertIn("ls", self.run_script("man ls")["stdout"])

    def test_unknown_command_hint(self):
        result = self.run_script("definitely-not-a-command")
        self.assertEqual(result["returncode"], 127)
        self.assertIn("command not found", result["stderr"])


class ProgressApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self._old_store = app_module.store
        app_module.store = ProgressStore(Path(self.tempdir.name) / "progress.sqlite3")
        self.addCleanup(self._restore_store)
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def _restore_store(self):
        app_module.store = self._old_store

    def _python_challenge(self):
        return next(item for item in CHALLENGES if item["language"] == "python")

    def test_challenges_api_hides_answers(self):
        items = self.client.get("/api/challenges").get_json()
        self.assertEqual(len(items), len(CHALLENGES))
        for item in items:
            for key in ("expected", "expected_regex", "strict_stderr", "allow_nonzero"):
                self.assertNotIn(key, item)
            self.assertIn("url", item)

    def test_check_records_progress(self):
        challenge = self._python_challenge()
        answer = accepted_outputs(challenge)[0]
        payload = self.client.post(
            "/api/check",
            json={"challenge_id": challenge["id"], "source": "print(1)", "stdout": answer},
        ).get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["passed"])
        self.assertIn(challenge["id"], payload["progress"]["solved"])
        self.assertEqual(payload["status"], "passed")

        failed = self.client.post(
            "/api/check",
            json={"challenge_id": challenge["id"], "source": "print(2)", "stdout": "definitely-wrong"},
        ).get_json()
        self.assertFalse(failed["passed"])
        self.assertEqual(failed["status"], "passed")  # 通过后不会被后续失败覆盖
        self.assertEqual(failed["attempts"], 2)

    def test_progress_reset(self):
        challenge = self._python_challenge()
        answer = accepted_outputs(challenge)[0]
        self.client.post(
            "/api/check",
            json={"challenge_id": challenge["id"], "source": "", "stdout": answer},
        )
        self.assertGreater(self.client.get("/api/progress").get_json()["solved_count"], 0)
        reset = self.client.post("/api/progress/reset").get_json()
        self.assertTrue(reset["ok"])
        self.assertEqual(self.client.get("/api/progress").get_json()["solved_count"], 0)

    def test_unknown_challenge_rejected(self):
        response = self.client.post("/api/check", json={"challenge_id": "nope", "stdout": ""})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.get_json()["passed"])

    def test_shell_challenge_is_executed_server_side(self):
        challenge = next(item for item in CHALLENGES if item["language"] == "shell")
        payload = self.client.post(
            "/api/check",
            json={"challenge_id": challenge["id"], "source": challenge["starter"]},
        ).get_json()
        self.assertTrue(payload["ok"])
        self.assertIsNotNone(payload["run"])
        self.assertIn("returncode", payload["run"])


class IsolationTests(unittest.TestCase):
    def test_multi_user_paths_are_separated(self):
        old = app.config.get("MULTI_USER")
        app.config["MULTI_USER"] = True
        try:
            alice = app_module._shell_root("alice")
            bob = app_module._shell_root("bob")
            self.assertNotEqual(alice, bob)
            self.assertIn("users", str(alice))
            self.assertEqual(app_module._slug("../etc/passwd"), "etc_passwd")
            self.assertEqual(app_module._slug(""), "guest")
        finally:
            app.config["MULTI_USER"] = old

    def test_rate_limit_blocks_burst(self):
        key = "test-rate-limit"
        self.assertFalse(rate_limited(key, limit=2))
        self.assertFalse(rate_limited(key, limit=2))
        self.assertTrue(rate_limited(key, limit=2))


class ChallengeDataTests(unittest.TestCase):
    def test_challenge_ids_are_unique_and_linked(self):
        ids = [item["id"] for item in CHALLENGES]
        self.assertEqual(len(ids), len(set(ids)))
        for item in CHALLENGES:
            for key in ("id", "language", "title", "goal", "starter"):
                self.assertTrue(str(item.get(key) or "").strip(), f"{item.get('id')} 缺少 {key}")
            self.assertIn(item["language"], {"shell", "python"})
            self.assertTrue("expected" in item or item.get("expected_regex"))
        self.assertEqual(set(ids), set(CHALLENGES_BY_ID))


if __name__ == "__main__":
    unittest.main(verbosity=2)
