# -*- coding: utf-8 -*-
"""隔离运行的代码练习平台（Codenova 实验室）。

安全边界：
- Linux/Shell 由平台内置模拟器执行，只访问项目 sandbox/。
- Python 由浏览器中的 Pyodide(WebAssembly)执行，不启动本机 Python 子进程。
- 服务端不调用 cmd、PowerShell、Git Bash、WSL 或其他系统终端。

本轮新增：
- 服务端判题 ``/api/check``（多答案 / 正则 / 归一化），前端不再各写一份比较逻辑；
- 练习进度持久化 ``/api/progress``（SQLite，按用户隔离）；
- 配置了账号密码时，沙箱、脚本目录均按用户隔离；
- ``/api/terminal`` 限流，静态资源长期缓存，可选 gzip。
"""

from __future__ import annotations

import hmac
import os
import re
import secrets
import threading
import time
from pathlib import Path

from flask import Flask, Response, g, jsonify, render_template, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from challenges import CHALLENGES, CHALLENGES_BY_ID, by_language, next_challenge
from judge import judge
from progress import ProgressStore
from sandbox_shell import VirtualShell

BASE_DIR = Path(__file__).resolve().parent
SCRIPT_DIR = BASE_DIR / "scripts"
PYTHON_SCRIPT_DIR = BASE_DIR / "python_scripts"
SANDBOX_DIR = BASE_DIR / "sandbox"
DATA_DIR = BASE_DIR / "data"
for directory in (SCRIPT_DIR, PYTHON_SCRIPT_DIR, SANDBOX_DIR, DATA_DIR):
    directory.mkdir(parents=True, exist_ok=True)

SITE_NAME = os.environ.get("SITE_NAME", "Codenova 实验室")
USER_COOKIE = "codenova_user"

store = ProgressStore(os.environ.get("PROGRESS_DB") or DATA_DIR / "progress.sqlite3")

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
# 多用户模式：同一个站点的每个浏览器（cookie）各自拥有独立的进度、脚本目录与 Sandbox。
# 默认关闭时，登录名（或固定的 guest）就是唯一身份，适合自己一个人用。
app.config["MULTI_USER"] = os.environ.get("MULTI_USER", "0").strip().lower() not in {
    "",
    "0",
    "false",
    "no",
    "off",
}
app.config["TERMINAL_RATE_LIMIT"] = int(os.environ.get("TERMINAL_RATE_LIMIT", "60") or 60)

try:  # 可选依赖：没装也能跑，只是不压缩
    from flask_compress import Compress  # type: ignore

    # 注意：Flask 的静态文件（含 8MB 的 pyodide.asm.wasm）走 send_file 直通，这里压不了，
    # 那部分交给 Nginx 的 gzip_types application/wasm 处理；这里压的是 HTML / JSON 等。
    Compress(app)
except Exception:  # pragma: no cover - 依赖缺失时的降级
    pass


# --------------------------------------------------------------------------- #
# 用户身份
# --------------------------------------------------------------------------- #
def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", value or "").strip("._")
    return (cleaned or "guest")[:48]


def current_user() -> str:
    """多用户模式下用 cookie 里的匿名 id 区分每个人；单用户模式用登录名或固定 guest。"""
    if getattr(g, "user", None):
        return g.user

    multi_user = bool(app.config.get("MULTI_USER"))
    cookie_id = str(request.cookies.get(USER_COOKIE) or "").strip()
    username = ""
    if not multi_user and app.config.get("APP_AUTH_ENABLED") and request.authorization:
        username = str(request.authorization.username or "").strip()

    user = cookie_id or username
    if not user:
        # 多用户模式：给每个新浏览器分配一个随机身份，之后的进度和沙箱都挂在它下面。
        user = "u" + secrets.token_hex(8) if multi_user else "guest"
        g.new_cookie = True
    g.user = user
    return user


def _shell_root(user: str) -> Path:
    if app.config.get("MULTI_USER"):
        return SANDBOX_DIR / "users" / _slug(user)
    return SANDBOX_DIR


def _script_dirs(user: str) -> tuple[Path, Path]:
    if app.config.get("MULTI_USER"):
        shell_dir = SCRIPT_DIR / "users" / _slug(user)
        python_dir = PYTHON_SCRIPT_DIR / "users" / _slug(user)
    else:
        shell_dir, python_dir = SCRIPT_DIR, PYTHON_SCRIPT_DIR
    shell_dir.mkdir(parents=True, exist_ok=True)
    python_dir.mkdir(parents=True, exist_ok=True)
    return shell_dir, python_dir


_shells: dict[str, VirtualShell] = {}
_shells_lock = threading.Lock()
_MAX_SHELLS = 64


def shell_for(user: str) -> VirtualShell:
    key = _slug(user)
    with _shells_lock:
        instance = _shells.get(key)
        if instance is None:
            if len(_shells) >= _MAX_SHELLS:
                _shells.pop(next(iter(_shells)))
            instance = VirtualShell(_shell_root(user))
            _shells[key] = instance
        return instance


_hits: dict[str, list[float]] = {}
_hits_lock = threading.Lock()


def rate_limited(key: str, limit: int | None = None, window: float = 10.0) -> bool:
    limit = int(limit or app.config["TERMINAL_RATE_LIMIT"])
    now = time.monotonic()
    with _hits_lock:
        stamps = [item for item in _hits.get(key, []) if now - item < window]
        if len(stamps) >= limit:
            _hits[key] = stamps
            return True
        stamps.append(now)
        _hits[key] = stamps
        return False


# --------------------------------------------------------------------------- #
# 登录 / 响应头
# --------------------------------------------------------------------------- #
def _unauthorized() -> Response:
    response = Response(f"需要登录 {SITE_NAME}", 401)
    response.headers["WWW-Authenticate"] = f'Basic realm="{SITE_NAME}", charset="UTF-8"'
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
    if request.path.startswith("/static/vendor/"):
        # Pyodide 运行时有十几 MB，长期缓存可以显著减少重复加载。
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif request.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "public, max-age=3600")
    if getattr(g, "new_cookie", False):
        response.set_cookie(
            USER_COOKIE,
            g.user,
            max_age=365 * 24 * 3600,
            httponly=True,
            samesite="Lax",
        )
    return response


@app.context_processor
def inject_globals() -> dict:
    return {
        "site_name": SITE_NAME,
        "current_user": current_user() if request.endpoint not in (None, "static") else "",
    }


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _files(directory: Path, extension: str) -> list[dict]:
    result = []
    if not directory.is_dir():
        return result
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


def _challenge_public(challenge: dict) -> dict:
    """给练习列表/API 用的公开视图：不暴露标准答案。"""
    item = dict(challenge)
    item.pop("expected", None)
    item.pop("expected_regex", None)
    item.pop("strict_stderr", None)
    item.pop("allow_nonzero", None)
    item["url"] = url_for(
        "python_editor" if challenge["language"] == "python" else "editor",
        challenge=challenge["id"],
    )
    return item


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    user = current_user()
    summary = store.summary(user, len(CHALLENGES))
    return render_template(
        "index.html",
        shell_count=len(_files(_script_dirs(user)[0], ".sh")),
        python_count=len(_files(_script_dirs(user)[1], ".py")),
        challenge_count=len(CHALLENGES),
        progress=summary,
    )


@app.route("/terminal")
def terminal():
    return render_template("terminal.html")


@app.route("/editor")
def editor():
    user = current_user()
    challenge = CHALLENGES_BY_ID.get(request.args.get("challenge", ""))
    if challenge and challenge["language"] != "shell":
        challenge = None
    summary = store.summary(user, len(CHALLENGES))
    status = store.get(user).get(challenge["id"], {}) if challenge else {}
    return render_template(
        "editor.html",
        scripts=[item["name"] for item in _files(_script_dirs(user)[0], ".sh")],
        challenge=challenge,
        challenge_status=status,
        next_challenge_item=next_challenge(challenge["id"]) if challenge else None,
        progress=summary,
    )


@app.route("/python")
def python_editor():
    user = current_user()
    challenge = CHALLENGES_BY_ID.get(request.args.get("challenge", ""))
    if challenge and challenge["language"] != "python":
        challenge = None
    summary = store.summary(user, len(CHALLENGES))
    status = store.get(user).get(challenge["id"], {}) if challenge else {}
    return render_template(
        "python_editor.html",
        scripts=[item["name"] for item in _files(_script_dirs(user)[1], ".py")],
        challenge=challenge,
        challenge_status=status,
        next_challenge_item=next_challenge(challenge["id"]) if challenge else None,
        progress=summary,
    )


@app.route("/practice")
def practice():
    user = current_user()
    summary = store.summary(user, len(CHALLENGES))
    solved = set(summary["solved"])
    return render_template(
        "practice.html",
        challenges=CHALLENGES,
        solved=solved,
        progress=summary,
        challenge_groups={"shell": by_language("shell"), "python": by_language("python")},
    )


@app.route("/api/health")
def health():
    return jsonify({
        "ok": True,
        "app": SITE_NAME,
        "shell": "sandbox",
        "python": "browser-pyodide",
        "coding_challenges": len(CHALLENGES),
        "multi_user": app.config.get("MULTI_USER", False),
    })


@app.route("/api/challenges")
def api_challenges():
    return jsonify([_challenge_public(item) for item in CHALLENGES])


# --------------------------------------------------------------------------- #
# 判题与进度
# --------------------------------------------------------------------------- #
@app.route("/api/check", methods=["POST"])
def api_check():
    user = current_user()
    if rate_limited(f"check:{user}", limit=30):
        return jsonify({"ok": False, "passed": False, "error": "提交太快了，休息几秒再试"}), 429

    payload = request.get_json(silent=True) or {}
    challenge = CHALLENGES_BY_ID.get(str(payload.get("challenge_id") or ""))
    if challenge is None:
        return jsonify({"ok": False, "passed": False, "error": "题目不存在"}), 404

    source = str(payload.get("source") or "")
    run: dict | None = None
    if challenge["language"] == "shell" and source.strip():
        result = shell_for(user).execute_script(
            source, cwd=str(payload.get("cwd") or "/home/student")
        )
        stdout, stderr, code = result["stdout"], result["stderr"], result["returncode"]
        run = {"stdout": stdout, "stderr": stderr, "returncode": code, "cwd": result["cwd"]}
    else:
        stdout = str(payload.get("stdout") or "")
        stderr = str(payload.get("stderr") or "")
        try:
            code = int(payload.get("returncode") or 0)
        except (TypeError, ValueError):
            code = 0

    verdict = judge(challenge, stdout, stderr, code)
    record = store.record(
        user,
        challenge["id"],
        language=challenge["language"],
        source=source,
        stdout=verdict["actual"],
        passed=verdict["passed"],
    )

    following = next_challenge(challenge["id"]) if verdict["passed"] else None
    return jsonify({
        "ok": True,
        "passed": verdict["passed"],
        "message": verdict["reason"],
        "hint": verdict["hint"],
        "expected": verdict["expected"],
        "actual": verdict["actual"],
        "run": run,
        "status": record.get("status"),
        "attempts": record.get("attempts"),
        "next_challenge": _challenge_public(following) if following else None,
        "progress": store.summary(user, len(CHALLENGES)),
    })


@app.route("/api/progress")
def api_progress():
    user = current_user()
    return jsonify(store.summary(user, len(CHALLENGES)))


@app.route("/api/progress/reset", methods=["POST"])
def api_progress_reset():
    user = current_user()
    removed = store.reset(user)
    return jsonify({"ok": True, "removed": removed, "progress": store.summary(user, len(CHALLENGES))})


# --------------------------------------------------------------------------- #
# Shell 沙箱
# --------------------------------------------------------------------------- #
@app.route("/api/terminal", methods=["POST"])
def api_terminal():
    user = current_user()
    if rate_limited(f"terminal:{user}"):
        return jsonify({
            "status": "error",
            "stdout": "",
            "stderr": "命令执行太频繁，请稍后再试",
            "returncode": 429,
            "cwd": "/home/student",
        }), 429
    payload = request.get_json(silent=True) or {}
    command = str(payload.get("command") or "").strip()
    cwd = str(payload.get("cwd") or "/home/student")
    if not command:
        return jsonify({"status": "error", "stderr": "命令不能为空", "stdout": "", "returncode": 2, "cwd": cwd})
    if len(command) > 20000:
        return jsonify({"status": "error", "stderr": "命令或脚本过长", "stdout": "", "returncode": 2, "cwd": cwd})
    return jsonify(shell_for(user).execute(command, cwd=cwd))


@app.route("/api/sandbox/reset", methods=["POST"])
def api_sandbox_reset():
    user = current_user()
    root = _shell_root(user)
    with _shells_lock:
        _shells.pop(_slug(user), None)
    if root.exists():
        import shutil

        shutil.rmtree(root, ignore_errors=True)
    shell_for(user)
    return jsonify({"ok": True, "cwd": "/home/student"})


# --------------------------------------------------------------------------- #
# 脚本 CRUD
# --------------------------------------------------------------------------- #
@app.route("/api/scripts", methods=["GET"])
def api_list_scripts():
    return jsonify(_files(_script_dirs(current_user())[0], ".sh"))


@app.route("/api/scripts/save", methods=["POST"])
def api_save_script():
    payload = request.get_json(silent=True) or {}
    path, error = _safe_file(_script_dirs(current_user())[0], str(payload.get("name") or ""), ".sh")
    if error:
        return jsonify({"ok": False, "error": error})
    content = str(payload.get("content") or "")
    if len(content) > 20000:
        return jsonify({"ok": False, "error": "脚本不能超过 20000 个字符"})
    path.write_text(content, encoding="utf-8")
    return jsonify({"ok": True, "name": path.name})


@app.route("/api/scripts/load", methods=["GET"])
def api_load_script():
    path, error = _safe_file(_script_dirs(current_user())[0], request.args.get("name", ""), ".sh")
    if error:
        return jsonify({"ok": False, "error": error})
    if not path.is_file():
        return jsonify({"ok": False, "error": "脚本不存在"})
    return jsonify({"ok": True, "name": path.name, "content": path.read_text(encoding="utf-8")})


@app.route("/api/scripts/delete", methods=["POST"])
def api_delete_script():
    payload = request.get_json(silent=True) or {}
    path, error = _safe_file(_script_dirs(current_user())[0], str(payload.get("name") or ""), ".sh")
    if error:
        return jsonify({"ok": False, "error": error})
    if not path.is_file():
        return jsonify({"ok": False, "error": "脚本不存在"})
    path.unlink()
    return jsonify({"ok": True})


@app.route("/api/scripts/run", methods=["POST"])
def api_run_script():
    user = current_user()
    payload = request.get_json(silent=True) or {}
    path, error = _safe_file(_script_dirs(user)[0], str(payload.get("name") or ""), ".sh")
    if error:
        return jsonify({"ok": False, "error": error})
    if not path.is_file():
        return jsonify({"ok": False, "error": "脚本不存在"})
    result = shell_for(user).execute_script(
        path.read_text(encoding="utf-8"),
        cwd=str(payload.get("cwd") or "/home/student"),
    )
    result["ok"] = result["returncode"] == 0
    return jsonify(result)


@app.route("/api/python-scripts", methods=["GET"])
def api_list_python_scripts():
    return jsonify(_files(_script_dirs(current_user())[1], ".py"))


@app.route("/api/python-scripts/save", methods=["POST"])
def api_save_python_script():
    payload = request.get_json(silent=True) or {}
    path, error = _safe_file(_script_dirs(current_user())[1], str(payload.get("name") or ""), ".py")
    if error:
        return jsonify({"ok": False, "error": error})
    content = str(payload.get("content") or "")
    if len(content) > 50000:
        return jsonify({"ok": False, "error": "脚本不能超过 50000 个字符"})
    path.write_text(content, encoding="utf-8")
    return jsonify({"ok": True, "name": path.name})


@app.route("/api/python-scripts/load", methods=["GET"])
def api_load_python_script():
    path, error = _safe_file(_script_dirs(current_user())[1], request.args.get("name", ""), ".py")
    if error:
        return jsonify({"ok": False, "error": error})
    if not path.is_file():
        return jsonify({"ok": False, "error": "脚本不存在"})
    return jsonify({"ok": True, "name": path.name, "content": path.read_text(encoding="utf-8")})


@app.route("/api/python-scripts/delete", methods=["POST"])
def api_delete_python_script():
    payload = request.get_json(silent=True) or {}
    path, error = _safe_file(_script_dirs(current_user())[1], str(payload.get("name") or ""), ".py")
    if error:
        return jsonify({"ok": False, "error": error})
    if not path.is_file():
        return jsonify({"ok": False, "error": "脚本不存在"})
    path.unlink()
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 56)
    print(f" {SITE_NAME} 已启动")
    print(f" 访问地址: http://127.0.0.1:{port}/")
    print(" Shell: 平台内置沙箱（不调用本机终端）")
    print(" Python: 浏览器 Pyodide 沙箱")
    print(f" 题库: {len(CHALLENGES)} 题 · 进度: {store.path}")
    print("=" * 56)
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
