# -*- coding: utf-8 -*-
"""隔离运行的代码练习平台。

安全边界：
- Linux/Shell 由平台内置模拟器执行，只访问项目 sandbox/。
- Python 由浏览器中的 Pyodide(WebAssembly)执行，不启动本机 Python 子进程。
- 服务端不调用 cmd、PowerShell、Git Bash、WSL 或其他系统终端。
"""

from __future__ import annotations

import os
import hmac
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from challenges import CHALLENGES, CHALLENGES_BY_ID
from sandbox_shell import VirtualShell


BASE_DIR = Path(__file__).resolve().parent
SCRIPT_DIR = BASE_DIR / "scripts"
PYTHON_SCRIPT_DIR = BASE_DIR / "python_scripts"
SANDBOX_DIR = BASE_DIR / "sandbox"
for directory in (SCRIPT_DIR, PYTHON_SCRIPT_DIR, SANDBOX_DIR):
    directory.mkdir(parents=True, exist_ok=True)

shell = VirtualShell(SANDBOX_DIR)

app = Flask(__name__)
app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=1,
    x_proto=1,
    x_host=1,
    x_prefix=1,
)
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024
app.config["JSON_AS_ASCII"] = False
app.config["APP_USERNAME"] = os.environ.get("APP_USERNAME", "")
app.config["APP_PASSWORD"] = os.environ.get("APP_PASSWORD", "")
app.config["APP_AUTH_ENABLED"] = os.environ.get("APP_AUTH_ENABLED", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}


def _unauthorized() -> Response:
    response = Response("需要登录 CloudOps Forge", 401)
    response.headers["WWW-Authenticate"] = 'Basic realm="CloudOps Forge", charset="UTF-8"'
    return response


@app.before_request
def require_authentication() -> Response | None:
    """公网部署时可通过环境变量启用整站 HTTP Basic 登录保护。"""
    if not app.config.get("APP_AUTH_ENABLED", True):
        return None
    username = str(app.config.get("APP_USERNAME") or "")
    password = str(app.config.get("APP_PASSWORD") or "")
    if not username and not password:
        return None
    if request.endpoint == "health":
        return None

    auth = request.authorization
    if auth is None:
        return _unauthorized()
    user_ok = hmac.compare_digest(auth.username or "", username)
    password_ok = hmac.compare_digest(auth.password or "", password)
    if not (user_ok and password_ok):
        return _unauthorized()
    return None


@app.after_request
def add_security_headers(response: Response) -> Response:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


def _files(directory: Path, extension: str) -> list[dict]:
    result = []
    for path in sorted(directory.glob(f"*{extension}"), key=lambda item: item.name.lower()):
        if path.is_file():
            stat = path.stat()
            result.append({"name": path.name, "size": stat.st_size, "mtime": stat.st_mtime})
    return result


def _safe_file(directory: Path, raw_name: str, extension: str) -> tuple[Path | None, str | None]:
    name = (raw_name or "").strip()
    if not name:
        return None, "文件名不能为空"
    if not name.lower().endswith(extension):
        name += extension
    safe_name = os.path.basename(name)
    if safe_name != name or safe_name in (".", ".."):
        return None, "文件名不能包含路径"
    if not all(ch.isalnum() or ch in "._-" or "\u4e00" <= ch <= "\u9fff" for ch in safe_name):
        return None, "文件名只能包含中英文、数字、点、横线和下划线"
    return directory / safe_name, None


@app.route("/")
def index():
    return render_template(
        "index.html",
        shell_count=len(_files(SCRIPT_DIR, ".sh")),
        python_count=len(_files(PYTHON_SCRIPT_DIR, ".py")),
        challenge_count=len(CHALLENGES),
    )


@app.route("/terminal")
def terminal():
    return render_template("terminal.html")


@app.route("/editor")
def editor():
    challenge = CHALLENGES_BY_ID.get(request.args.get("challenge", ""))
    if challenge and challenge["language"] != "shell":
        challenge = None
    return render_template("editor.html", scripts=[item["name"] for item in _files(SCRIPT_DIR, ".sh")], challenge=challenge)


@app.route("/python")
def python_editor():
    challenge = CHALLENGES_BY_ID.get(request.args.get("challenge", ""))
    if challenge and challenge["language"] != "python":
        challenge = None
    return render_template("python_editor.html", scripts=[item["name"] for item in _files(PYTHON_SCRIPT_DIR, ".py")], challenge=challenge)


@app.route("/practice")
def practice():
    return render_template("practice.html", challenges=CHALLENGES)


@app.route("/api/health")
def health():
    return jsonify({
        "ok": True,
        "shell": "sandbox",
        "python": "browser-pyodide",
        "coding_challenges": len(CHALLENGES),
    })


@app.route("/api/terminal", methods=["POST"])
def api_terminal():
    payload = request.get_json(silent=True) or {}
    command = str(payload.get("command") or "").strip()
    cwd = str(payload.get("cwd") or "/home/student")
    if not command:
        return jsonify({"status": "error", "stderr": "命令不能为空", "stdout": "", "returncode": 2, "cwd": cwd})
    if len(command) > 20000:
        return jsonify({"status": "error", "stderr": "命令或脚本过长", "stdout": "", "returncode": 2, "cwd": cwd})
    return jsonify(shell.execute(command, cwd=cwd))


@app.route("/api/scripts", methods=["GET"])
def api_list_scripts():
    return jsonify(_files(SCRIPT_DIR, ".sh"))


@app.route("/api/scripts/save", methods=["POST"])
def api_save_script():
    payload = request.get_json(silent=True) or {}
    path, error = _safe_file(SCRIPT_DIR, str(payload.get("name") or ""), ".sh")
    if error:
        return jsonify({"ok": False, "error": error})
    content = str(payload.get("content") or "")
    if len(content) > 20000:
        return jsonify({"ok": False, "error": "脚本不能超过 20000 个字符"})
    path.write_text(content, encoding="utf-8")
    return jsonify({"ok": True, "name": path.name})


@app.route("/api/scripts/load", methods=["GET"])
def api_load_script():
    path, error = _safe_file(SCRIPT_DIR, request.args.get("name", ""), ".sh")
    if error:
        return jsonify({"ok": False, "error": error})
    if not path.is_file():
        return jsonify({"ok": False, "error": "脚本不存在"})
    return jsonify({"ok": True, "name": path.name, "content": path.read_text(encoding="utf-8")})


@app.route("/api/scripts/delete", methods=["POST"])
def api_delete_script():
    payload = request.get_json(silent=True) or {}
    path, error = _safe_file(SCRIPT_DIR, str(payload.get("name") or ""), ".sh")
    if error:
        return jsonify({"ok": False, "error": error})
    if not path.is_file():
        return jsonify({"ok": False, "error": "脚本不存在"})
    path.unlink()
    return jsonify({"ok": True})


@app.route("/api/scripts/run", methods=["POST"])
def api_run_script():
    payload = request.get_json(silent=True) or {}
    path, error = _safe_file(SCRIPT_DIR, str(payload.get("name") or ""), ".sh")
    if error:
        return jsonify({"ok": False, "error": error})
    if not path.is_file():
        return jsonify({"ok": False, "error": "脚本不存在"})
    result = shell.execute_script(path.read_text(encoding="utf-8"), cwd=str(payload.get("cwd") or "/home/student"))
    result["ok"] = result["returncode"] == 0
    return jsonify(result)


@app.route("/api/python-scripts", methods=["GET"])
def api_list_python_scripts():
    return jsonify(_files(PYTHON_SCRIPT_DIR, ".py"))


@app.route("/api/python-scripts/save", methods=["POST"])
def api_save_python_script():
    payload = request.get_json(silent=True) or {}
    path, error = _safe_file(PYTHON_SCRIPT_DIR, str(payload.get("name") or ""), ".py")
    if error:
        return jsonify({"ok": False, "error": error})
    content = str(payload.get("content") or "")
    if len(content) > 50000:
        return jsonify({"ok": False, "error": "脚本不能超过 50000 个字符"})
    path.write_text(content, encoding="utf-8")
    return jsonify({"ok": True, "name": path.name})


@app.route("/api/python-scripts/load", methods=["GET"])
def api_load_python_script():
    path, error = _safe_file(PYTHON_SCRIPT_DIR, request.args.get("name", ""), ".py")
    if error:
        return jsonify({"ok": False, "error": error})
    if not path.is_file():
        return jsonify({"ok": False, "error": "脚本不存在"})
    return jsonify({"ok": True, "name": path.name, "content": path.read_text(encoding="utf-8")})


@app.route("/api/python-scripts/delete", methods=["POST"])
def api_delete_python_script():
    payload = request.get_json(silent=True) or {}
    path, error = _safe_file(PYTHON_SCRIPT_DIR, str(payload.get("name") or ""), ".py")
    if error:
        return jsonify({"ok": False, "error": error})
    if not path.is_file():
        return jsonify({"ok": False, "error": "脚本不存在"})
    path.unlink()
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 56)
    print(" CloudOps Forge 已启动")
    print(f" 访问地址: http://127.0.0.1:{port}/")
    print(" Shell: 平台内置沙箱（不调用本机终端）")
    print(" Python: 浏览器 Pyodide 沙箱")
    print("=" * 56)
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
