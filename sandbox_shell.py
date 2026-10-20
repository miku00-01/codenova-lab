# -*- coding: utf-8 -*-
"""A small Linux/Shell practice sandbox.

No operating-system shell is spawned. Every file operation is confined to the
platform-owned sandbox directory and every command is implemented in Python.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import shlex
import shutil
from pathlib import Path


class SandboxError(Exception):
    pass


class VirtualShell:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.home = "/home/student"
        # 可变的「系统状态」：让 systemctl / docker / kubectl 前后一致，而不是永远回同一段假数据。
        self.state: dict = {
            "services": {"nginx": True, "mysql": True, "redis": False, "sshd": True},
            "enabled": {"nginx": True, "mysql": True, "redis": False, "sshd": True},
            "containers": [
                {
                    "id": "a1b2c3d4e5f6",
                    "image": "nginx:alpine",
                    "name": "web",
                    "status": "Up 2 hours",
                    "ports": "0.0.0.0:8080->80/tcp",
                }
            ],
            "pods": [
                {"name": "web-6d8f7c9d8b-demo", "ready": "1/1", "status": "Running", "restarts": 0}
            ],
        }
        self.history: list[str] = []
        self.reset_if_empty()

    def reset_if_empty(self) -> None:
        (self.root / "home" / "student" / "projects").mkdir(parents=True, exist_ok=True)
        (self.root / "var" / "log").mkdir(parents=True, exist_ok=True)
        (self.root / "tmp").mkdir(parents=True, exist_ok=True)
        samples = {
            self.root / "home" / "student" / "README.txt": (
                "Codenova Linux 练习沙箱\n"
                "这里的命令由平台模拟，不会调用 Windows、Git Bash 或 WSL。\n"
                "输入 help 查看支持的命令。\n"
            ),
            self.root / "var" / "log" / "app.log": (
                "2026-09-22 09:00:01 INFO service started\n"
                "2026-09-22 09:03:18 INFO GET /health 200 12ms\n"
                "2026-09-22 09:05:42 WARN response slow 830ms\n"
                "2026-09-22 09:08:09 ERROR database connection timeout\n"
            ),
            self.root / "home" / "student" / "projects" / "services.txt": (
                "nginx active\nmysql active\nredis inactive\n"
            ),
        }
        for path, content in samples.items():
            if not path.exists():
                path.write_text(content, encoding="utf-8")

    def _virtual(self, real: Path) -> str:
        rel = real.resolve().relative_to(self.root)
        value = "/" + rel.as_posix()
        return value.rstrip("/") or "/"

    def _resolve(self, cwd: str, raw: str | None = None) -> Path:
        raw = raw or "."
        if raw == "~" or raw.startswith("~/"):
            raw = self.home + raw[1:]
        if raw.startswith("/"):
            candidate = self.root.joinpath(raw.lstrip("/"))
        else:
            base = self.root.joinpath(cwd.lstrip("/"))
            candidate = base.joinpath(raw)
        candidate = candidate.resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise SandboxError("路径超出练习沙箱，已拦截") from exc
        return candidate

    @staticmethod
    def _split_unquoted(text: str, delimiter: str) -> list[str]:
        parts, buf = [], []
        quote = None
        escape = False
        for char in text:
            if escape:
                buf.append(char)
                escape = False
                continue
            if char == "\\" and quote != "'":
                buf.append(char)
                escape = True
                continue
            if char in ("'", '"'):
                if quote == char:
                    quote = None
                elif quote is None:
                    quote = char
                buf.append(char)
                continue
            if char == delimiter and quote is None:
                parts.append("".join(buf).strip())
                buf = []
            else:
                buf.append(char)
        parts.append("".join(buf).strip())
        return [p for p in parts if p]

    def _expand(self, text: str, env: dict[str, str], last_code: int, cwd: Path | None = None) -> str:
        """展开 $?、$VAR、${VAR}、$(命令) 与 `命令`。

        命令替换只运行沙箱内的模拟命令，并有深度上限，避免无限递归。
        """
        result = str(text).replace("$?", str(last_code))
        if cwd is None:
            cwd = self._resolve(self.home)
        if "$((" in result:
            result = re.sub(r"\$\(\((.*?)\)\)", lambda m: str(self._arith(m.group(1), env)), result)
        substitution = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
        for _ in range(3):
            updated = substitution.sub(
                lambda match: self._capture(match.group(1) or match.group(2) or "", cwd, env, last_code),
                result,
            )
            if updated == result:
                break
            result = updated
        pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
        return pattern.sub(lambda m: env.get(m.group(1) or m.group(2), ""), result)

    def _capture(self, command: str, cwd: Path, env: dict[str, str], last_code: int = 0) -> str:
        """执行 $(...) 里的命令，把输出当成一个值。"""
        try:
            result = self._run_pipeline(self._expand(command.strip(), env, last_code, cwd), cwd, env)
        except (SandboxError, ValueError, OSError):
            return ""
        return result["stdout"].replace("\n", " ")

    def _arith(self, expression: str, env: dict[str, str]) -> int:
        """计算 $((1+2)) 这类整数表达式，只允许数字与 + - * / % 括号。"""
        text = re.sub(r"\$?\{?([A-Za-z_][A-Za-z0-9_]*)\}?", lambda m: str(env.get(m.group(1), "0")), expression)
        if not re.fullmatch(r"[0-9+\-*/%() ]*", text):
            return 0
        try:
            return int(eval(text.replace("/", "//"), {"__builtins__": {}}, {}))  # noqa: S307 - 已限定字符集
        except (SyntaxError, ZeroDivisionError, TypeError, ValueError):
            return 0

    # -- 语句切分 --------------------------------------------------------- #
    @classmethod
    def _split_statements(cls, text: str) -> list[str]:
        keywords = ("then", "do", "done", "fi", "else")
        statements: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            for chunk in cls._split_unquoted(stripped, ";"):
                chunk = chunk.strip()
                while chunk:
                    head, _, rest = chunk.partition(" ")
                    if head in keywords:
                        statements.append(head)
                        chunk = rest.strip()
                    else:
                        statements.append(chunk)
                        break
        return statements

    @classmethod
    def _split_operators(cls, text: str) -> list[tuple[str, str]]:
        """按 && / || 切分，引号内的不算。返回 [(前置操作符, 语句)]。"""
        parts: list[tuple[str, str]] = []
        buf: list[str] = []
        operator = ""
        quote = None
        escape = False
        index = 0
        while index < len(text):
            char = text[index]
            if escape:
                buf.append(char)
                escape = False
                index += 1
                continue
            if char == "\\" and quote != "'":
                buf.append(char)
                escape = True
                index += 1
                continue
            if char in ("'", '"'):
                if quote == char:
                    quote = None
                elif quote is None:
                    quote = char
                buf.append(char)
                index += 1
                continue
            if quote is None and text.startswith("&&", index):
                parts.append((operator, "".join(buf).strip()))
                buf, operator = [], "&&"
                index += 2
                continue
            if quote is None and text.startswith("||", index):
                parts.append((operator, "".join(buf).strip()))
                buf, operator = [], "||"
                index += 2
                continue
            buf.append(char)
            index += 1
        parts.append((operator, "".join(buf).strip()))
        return [(op, value) for op, value in parts if value]

    # -- 执行 ------------------------------------------------------------- #
    def execute(self, command: str, cwd: str = "/home/student") -> dict:
        return self.execute_script(command, cwd=cwd)

    def execute_script(self, script: str, cwd: str = "/home/student") -> dict:
        try:
            real_cwd = self._resolve(cwd)
            if not real_cwd.is_dir():
                real_cwd = self._resolve(self.home)
        except (SandboxError, OSError):
            real_cwd = self._resolve(self.home)

        env = {
            "USER": "student",
            "HOME": self.home,
            "SHELL": "/bin/bash",
            "HOSTNAME": "codenova-lab",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
        }
        cleaned = "\n".join(
            line for line in script.replace("\r\n", "\n").split("\n")
            if not line.lstrip().startswith("#!")
        )
        stdout: list[str] = []
        stderr: list[str] = []
        state = {"code": 0, "cwd": real_cwd, "clear": False}

        try:
            self._exec_statements(self._split_statements(cleaned), 0, state, env, stdout, stderr)
        except (SandboxError, ValueError, OSError) as exc:
            stderr.append(str(exc))
            state["code"] = 1

        return {
            "status": "ok" if state["code"] == 0 else "error",
            "stdout": "\n".join(part.rstrip("\n") for part in stdout if part).rstrip(),
            "stderr": "\n".join(part.rstrip("\n") for part in stderr if part).rstrip(),
            "returncode": state["code"],
            "cwd": self._virtual(state["cwd"]),
            "shell": "Codenova Sandbox Shell（不调用本机终端）",
            "clear": state["clear"],
        }

    _BLOCK_HEADS = ("for", "while", "until", "if")

    def _take_block(self, statements: list[str], start: int, stops: tuple[str, ...]) -> tuple[list[str], int]:
        """收集一段语句体，遇到顶层 stop 关键字时返回其下标（不消费）。"""
        body: list[str] = []
        depth = 0
        index = start
        while index < len(statements):
            statement = statements[index]
            head = statement.split()[0] if statement.split() else ""
            if depth == 0 and (statement in stops or any(statement.startswith(stop + " ") for stop in stops)):
                return body, index
            if head in self._BLOCK_HEADS:
                depth += 1
            elif head in ("done", "fi"):
                depth -= 1
                if depth < 0:
                    return body, index
            body.append(statement)
            index += 1
        return body, index

    def _exec_statements(
        self,
        statements: list[str],
        start: int,
        state: dict,
        env: dict[str, str],
        stdout: list[str],
        stderr: list[str],
    ) -> int:
        index = start
        while index < len(statements):
            statement = statements[index]
            if statement in ("then", "do", "else", "fi", "done") or statement.startswith("elif"):
                index += 1
                continue
            if statement.startswith("for "):
                index = self._exec_for(statements, index, state, env, stdout, stderr)
            elif statement.startswith("while ") or statement.startswith("until "):
                index = self._exec_while(statements, index, state, env, stdout, stderr)
            elif statement == "if" or statement.startswith("if "):
                index = self._exec_if(statements, index, state, env, stdout, stderr)
            else:
                self._exec_chain(statement, state, env, stdout, stderr)
                index += 1
        return index

    def _exec_for(self, statements, index, state, env, stdout, stderr) -> int:
        match = re.match(r"for\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\s+(.*)", statements[index], re.DOTALL)
        if not match:
            raise SandboxError("for: 请使用 for 变量 in 列表; do ... done")
        index += 1
        if index < len(statements) and statements[index] == "do":
            index += 1
        body, index = self._take_block(statements, index, ("done",))
        if index < len(statements) and statements[index] == "done":
            index += 1
        raw = self._expand(match.group(2).strip().rstrip(";"), env, state["code"], state["cwd"])
        try:
            values = shlex.split(raw, posix=True)
        except ValueError as exc:
            raise SandboxError(f"for: 列表解析失败（{exc}）") from exc
        for value in values[:100]:
            env[match.group(1)] = value
            self._exec_statements(body, 0, state, env, stdout, stderr)
        return index

    def _exec_while(self, statements, index, state, env, stdout, stderr) -> int:
        header = statements[index]
        until = header.startswith("until ")
        condition = header[6:].strip() if until else header[5:].strip()
        index += 1
        if index < len(statements) and statements[index] == "do":
            index += 1
        body, index = self._take_block(statements, index, ("done",))
        if index < len(statements) and statements[index] == "done":
            index += 1
        guard = 0
        last_code = 0
        while guard < 100:
            ok = self._condition(condition, state, env)
            if ok == until:  # while 在条件为真时继续；until 反之
                break
            self._exec_statements(body, 0, state, env, stdout, stderr)
            last_code = state["code"]
            guard += 1
        state["code"] = last_code
        return index

    def _exec_if(self, statements, index, state, env, stdout, stderr) -> int:
        condition = statements[index][2:].strip()
        index += 1
        if index < len(statements) and statements[index] == "then":
            index += 1
        branches: list[tuple[str | None, list[str]]] = []
        while True:
            body, stop_index = self._take_block(statements, index, ("elif", "else", "fi"))
            branches.append((condition, body))
            if stop_index >= len(statements):
                index = stop_index
                break
            stop = statements[stop_index]
            if stop == "fi":
                index = stop_index + 1
                break
            if stop == "else":
                else_body, close_index = self._take_block(statements, stop_index + 1, ("fi",))
                branches.append((None, else_body))
                index = close_index + 1 if close_index < len(statements) else close_index
                break
            condition = stop[4:].strip()
            index = stop_index + 1
            if index < len(statements) and statements[index] == "then":
                index += 1
        for branch_condition, body in branches:
            if branch_condition is None or self._condition(branch_condition, state, env):
                self._exec_statements(body, 0, state, env, stdout, stderr)
                break
        else:
            state["code"] = 0
        return index

    def _condition(self, text: str, state: dict, env: dict[str, str]) -> bool:
        expanded = self._expand(text, env, state["code"], state["cwd"]).strip()
        if expanded.startswith("[") and expanded.endswith("]"):
            ok = self._test(shlex.split(expanded[1:-1], posix=True), state["cwd"])
            state["code"] = 0 if ok else 1
            return ok
        if expanded.startswith("test "):
            ok = self._test(shlex.split(expanded[5:], posix=True), state["cwd"])
            state["code"] = 0 if ok else 1
            return ok
        if not expanded:
            return False
        self._exec_chain(expanded, state, env, [], [])
        return state["code"] == 0

    def _test(self, args: list[str], cwd: Path) -> bool:
        values = list(args)
        negate = False
        while values and values[0] == "!":
            negate = not negate
            values = values[1:]
        result = self._test_raw(values, cwd) if values else False
        return (not result) if negate else result

    def _test_raw(self, args: list[str], cwd: Path) -> bool:
        def path_of(name: str) -> Path:
            return self._resolve(self._virtual(cwd), name)

        if len(args) == 1:
            return bool(args[0])
        if len(args) == 2:
            operator, operand = args
            if operator == "-z":
                return operand == ""
            if operator == "-n":
                return operand != ""
            if operator in ("-e", "-r", "-w", "-x"):
                return path_of(operand).exists()
            if operator == "-f":
                return path_of(operand).is_file()
            if operator == "-d":
                return path_of(operand).is_dir()
            if operator == "-s":
                path = path_of(operand)
                return path.is_file() and path.stat().st_size > 0
            raise SandboxError(f"[: 不支持的操作符 {operator}")
        if len(args) == 3:
            left, operator, right = args
            if operator in ("=", "=="):
                return left == right
            if operator == "!=":
                return left != right
            try:
                lhs, rhs = int(left), int(right)
            except ValueError as exc:
                raise SandboxError(f"[: {operator} 需要整数参数") from exc
            if operator == "-eq":
                return lhs == rhs
            if operator == "-ne":
                return lhs != rhs
            if operator == "-lt":
                return lhs < rhs
            if operator == "-le":
                return lhs <= rhs
            if operator == "-gt":
                return lhs > rhs
            if operator == "-ge":
                return lhs >= rhs
            raise SandboxError(f"[: 不支持的操作符 {operator}")
        raise SandboxError("[: 参数数量不对，例如 [ -f file ]")

    def _exec_chain(self, statement: str, state: dict, env: dict[str, str], stdout: list[str], stderr: list[str]) -> None:
        for operator, text in self._split_operators(statement):
            if operator == "&&" and state["code"] != 0:
                continue
            if operator == "||" and state["code"] == 0:
                continue
            self._exec_simple(text, state, env, stdout, stderr)

    def _exec_simple(self, statement: str, state: dict, env: dict[str, str], stdout: list[str], stderr: list[str]) -> None:
        expanded = self._expand(statement, env, state["code"], state["cwd"]).strip()
        if not expanded:
            return
        self.history.append(expanded)
        del self.history[:-200]

        assignment = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", expanded)
        if assignment:
            env[assignment.group(1)] = assignment.group(2).strip("\"'")
            state["code"] = 0
            return

        if expanded.startswith("export "):
            item = expanded[7:].strip()
            match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", item)
            if match:
                env[match.group(1)] = match.group(2).strip("\"'")
                state["code"] = 0
            else:
                stderr.append("export: 请使用 NAME=value")
                state["code"] = 2
            return

        if expanded.startswith("unset "):
            env.pop(expanded[6:].strip(), None)
            state["code"] = 0
            return

        result = self._run_pipeline(expanded, state["cwd"], env)
        state["cwd"] = result["cwd"]
        state["code"] = result["code"]
        state["clear"] = state["clear"] or result.get("clear", False)
        if result["stdout"]:
            stdout.append(result["stdout"])
        if result["stderr"]:
            stderr.append(result["stderr"])

    def _run_pipeline(self, statement: str, cwd: Path, env: dict[str, str]) -> dict:
        stages = self._split_unquoted(statement, "|")
        stdin = ""
        result = {"stdout": "", "stderr": "", "code": 0, "cwd": cwd, "clear": False}
        for stage in stages:
            result = self._run_command(stage, result["cwd"], env, stdin)
            stdin = result["stdout"]
            if result["code"] != 0:
                break
        return result

    def _run_command(self, command: str, cwd: Path, env: dict[str, str], stdin: str = "") -> dict:
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError as exc:
            return {"stdout": "", "stderr": f"解析错误: {exc}", "code": 2, "cwd": cwd}
        if not tokens:
            return {"stdout": "", "stderr": "", "code": 0, "cwd": cwd}

        redirect, append, redirect_stderr, redirect_both = None, False, False, False
        found = None
        for marker in ("&>>", "&>", "2>>", "2>", ">>", ">"):
            if marker in tokens:
                idx = tokens.index(marker)
                if found is None or idx < found[0]:
                    found = (idx, marker)
        if found:
            idx, marker = found
            if idx + 1 >= len(tokens):
                return {"stdout": "", "stderr": "重定向缺少文件名", "code": 2, "cwd": cwd}
            redirect = tokens[idx + 1]
            append = ">>" in marker
            redirect_stderr = marker.startswith(("2", "&"))
            if marker.startswith("&"):
                redirect_both = True
            tokens = tokens[:idx]

        tokens = self._glob_tokens(tokens, cwd)
        if not tokens:
            return {"stdout": "", "stderr": "", "code": 0, "cwd": cwd}

        name, args = tokens[0], tokens[1:]
        if name == "[":
            if args and args[-1] == "]":
                args = args[:-1]
            return {
                "stdout": "",
                "stderr": "",
                "code": 0 if self._test(args, cwd) else 1,
                "cwd": cwd,
                "clear": False,
            }
        if name == "test":
            return {
                "stdout": "",
                "stderr": "",
                "code": 0 if self._test(args, cwd) else 1,
                "cwd": cwd,
                "clear": False,
            }
        handler = getattr(self, f"_cmd_{name.replace('-', '_')}", None)
        if handler is None:
            return {"stdout": "", "stderr": f"{name}: command not found（输入 help 查看沙箱命令）", "code": 127, "cwd": cwd}
        try:
            out, err, code, new_cwd, clear = handler(args, cwd, env, stdin)
            if redirect:
                target = self._resolve(self._virtual(new_cwd), redirect)
                target.parent.mkdir(parents=True, exist_ok=True)
                mode = "a" if append else "w"
                with target.open(mode, encoding="utf-8") as stream:
                    if redirect_stderr:
                        payload = err or (out if redirect_both else "")
                        if payload:
                            stream.write(payload if payload.endswith("\n") else payload + "\n")
                        if redirect_both:
                            out = ""
                        err = ""
                    else:
                        payload = out or (err if redirect_both else "")
                        if payload:
                            stream.write(payload if payload.endswith("\n") else payload + "\n")
                        out = ""
                        if redirect_both:
                            err = ""
            return {"stdout": out, "stderr": err, "code": code, "cwd": new_cwd, "clear": clear}
        except (SandboxError, OSError) as exc:
            return {"stdout": "", "stderr": str(exc), "code": 1, "cwd": cwd, "clear": False}

    def _glob_tokens(self, tokens: list[str], cwd: Path) -> list[str]:
        """把 *.log 这类通配符先展开成实际存在的文件。"""
        result: list[str] = []
        for token in tokens:
            if token and ("*" in token or "?" in token) and not token.startswith("-"):
                matches = self._match_glob(token, cwd)
                result.extend(matches or [token])
            else:
                result.append(token)
        return result

    def _match_glob(self, pattern: str, cwd: Path) -> list[str]:
        import fnmatch

        if "/" in pattern:
            head, _, tail = pattern.rpartition("/")
            base = self._resolve(self._virtual(cwd), head or "/")
        else:
            head, tail = "", pattern
            base = self._resolve(self._virtual(cwd), ".")
        if not base.is_dir():
            return []
        names = sorted(
            item.name
            for item in base.iterdir()
            if fnmatch.fnmatch(item.name, tail) and not item.name.startswith(".")
        )
        return [f"{head}/{name}" if head else name for name in names]

    @staticmethod
    def _ok(out="", cwd=None, clear=False):
        return out, "", 0, cwd, clear

    @staticmethod
    def _fail(err, cwd, code=1):
        return "", err, code, cwd, False

    def _cmd_help(self, args, cwd, env, stdin):
        text = (
            "文件: pwd ls cd tree mkdir touch cat head tail wc cp mv rm find\n"
            "文本: echo printf grep sort uniq seq\n"
            "系统: whoami hostname uname date env df free ps systemctl journalctl history man sleep true false\n"
            "网络: ip ss ping curl\n"
            "练习: docker kubectl clear help\n"
            "语法: 管道 |、重定向 > >>、&& 和 ||、变量 NAME=value、$NAME、${NAME}、$?、\n"
            "      通配符 * ?、for/while/if 语句块、[ ] 和 test 判断、$(命令) 子命令。\n"
            "所有命令只在平台 sandbox/ 目录中运行，不会访问电脑其他文件。"
        )
        return self._ok(text, cwd)

    def _cmd_clear(self, args, cwd, env, stdin):
        return self._ok("", cwd, True)

    def _cmd_pwd(self, args, cwd, env, stdin):
        return self._ok(self._virtual(cwd), cwd)

    def _cmd_ls(self, args, cwd, env, stdin):
        show_all = any("a" in a[1:] for a in args if a.startswith("-"))
        long = any("l" in a[1:] for a in args if a.startswith("-"))
        paths = [a for a in args if not a.startswith("-")] or ["."]
        blocks = []
        for raw in paths:
            target = self._resolve(self._virtual(cwd), raw)
            if not target.exists():
                return self._fail(f"ls: {raw}: No such file or directory", cwd)
            if target.is_file():
                blocks.append(raw)
                continue
            entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            entries = [p for p in entries if show_all or not p.name.startswith(".")]
            if long:
                lines = []
                for p in entries:
                    size = 0 if p.is_dir() else p.stat().st_size
                    lines.append(f"{'d' if p.is_dir() else '-'}rw-r--r-- 1 student student {size:>7} {p.name}{'/' if p.is_dir() else ''}")
                blocks.append("\n".join(lines))
            else:
                blocks.append("  ".join(p.name + ("/" if p.is_dir() else "") for p in entries))
        if not blocks:
            return self._ok("", cwd)
        if all(block == raw for raw, block in zip(paths, blocks)):
            return self._ok("  ".join(blocks), cwd)
        if len(paths) > 1:
            return self._ok("\n\n".join(f"{raw}:\n{block}" if block else f"{raw}:" for raw, block in zip(paths, blocks)), cwd)
        return self._ok(blocks[0], cwd)

    def _cmd_cd(self, args, cwd, env, stdin):
        target = self._resolve(self._virtual(cwd), args[0] if args else self.home)
        if not target.is_dir():
            return self._fail("cd: 目录不存在", cwd)
        return self._ok("", target)

    def _cmd_mkdir(self, args, cwd, env, stdin):
        names = [a for a in args if not a.startswith("-")]
        if not names:
            return self._fail("mkdir: 缺少目录名", cwd, 2)
        parents = "-p" in args
        for name in names:
            self._resolve(self._virtual(cwd), name).mkdir(parents=parents, exist_ok=parents)
        return self._ok("", cwd)

    def _cmd_touch(self, args, cwd, env, stdin):
        if not args:
            return self._fail("touch: 缺少文件名", cwd, 2)
        for name in args:
            path = self._resolve(self._virtual(cwd), name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        return self._ok("", cwd)

    def _cmd_cat(self, args, cwd, env, stdin):
        if not args or args == ["-"]:
            return self._ok(stdin, cwd)
        chunks = []
        for name in args:
            path = self._resolve(self._virtual(cwd), name)
            if not path.is_file():
                return self._fail(f"cat: {name}: No such file", cwd)
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        return self._ok("".join(chunks).rstrip("\n"), cwd)

    def _read_text(self, args, cwd, stdin):
        names = [a for a in args if not a.startswith("-") and not a.isdigit()]
        if names:
            path = self._resolve(self._virtual(cwd), names[-1])
            if not path.is_file():
                raise SandboxError(f"{names[-1]}: No such file")
            return path.read_text(encoding="utf-8", errors="replace")
        return stdin

    def _count_arg(self, args, default=10):
        if "-n" in args:
            idx = args.index("-n")
            if idx + 1 < len(args):
                return max(0, min(int(args[idx + 1]), 1000))
        for arg in args:
            if arg.startswith("-") and arg[1:].isdigit():
                return min(int(arg[1:]), 1000)
        return default

    def _cmd_head(self, args, cwd, env, stdin):
        text = self._read_text(args, cwd, stdin)
        return self._ok("\n".join(text.splitlines()[: self._count_arg(args)]), cwd)

    def _cmd_tail(self, args, cwd, env, stdin):
        text = self._read_text(args, cwd, stdin)
        return self._ok("\n".join(text.splitlines()[-self._count_arg(args) :]), cwd)

    def _cmd_wc(self, args, cwd, env, stdin):
        text = self._read_text(args, cwd, stdin)
        if "-l" in args:
            value = len(text.splitlines())
        elif "-w" in args:
            value = len(text.split())
        elif "-c" in args:
            value = len(text.encode("utf-8"))
        else:
            value = f"{len(text.splitlines())} {len(text.split())} {len(text.encode('utf-8'))}"
        return self._ok(str(value), cwd)

    def _cmd_echo(self, args, cwd, env, stdin):
        interpret = "-e" in args
        values = [item for item in args if item not in ("-e", "-n")]
        text = " ".join(values)
        if interpret:
            text = (
                text.replace("\\t", "\t")
                .replace("\\n", "\n")
                .replace("\\\\", "\\")
            )
        return self._ok(text, cwd)

    def _cmd_printf(self, args, cwd, env, stdin):
        return self._ok(" ".join(args).replace("\\n", "\n").replace("\\t", "\t"), cwd)

    def _cmd_grep(self, args, cwd, env, stdin):
        ignore_case, show_num = "-i" in args, "-n" in args
        count_only = "-c" in args
        values = [a for a in args if a not in ("-i", "-n", "-c")]
        if not values:
            return self._fail("grep: 缺少匹配模式", cwd, 2)
        pattern = values[0]
        text = stdin
        if len(values) > 1:
            path = self._resolve(self._virtual(cwd), values[1])
            if not path.is_file():
                return self._fail(f"grep: {values[1]}: No such file", cwd)
            text = path.read_text(encoding="utf-8", errors="replace")
        flags = re.IGNORECASE if ignore_case else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as exc:
            return self._fail(f"grep: 正则错误: {exc}", cwd, 2)
        lines = []
        for idx, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                lines.append(f"{idx}:{line}" if show_num else line)
        if count_only:
            return self._ok(str(len(lines)), cwd)
        return self._ok("\n".join(lines), cwd) if lines else ("", "", 1, cwd, False)

    def _cmd_sort(self, args, cwd, env, stdin):
        reverse = "-r" in args
        text = self._read_text([item for item in args if item != "-r"], cwd, stdin)
        return self._ok("\n".join(sorted(text.splitlines(), reverse=reverse)), cwd)

    def _cmd_uniq(self, args, cwd, env, stdin):
        count = "-c" in args
        text = self._read_text([item for item in args if item != "-c"], cwd, stdin)
        output, last, n = [], None, 0
        for line in text.splitlines() + [None]:
            if line == last:
                n += 1
                continue
            if last is not None:
                output.append(f"{n:>7} {last}" if count else last)
            last, n = line, 1
        return self._ok("\n".join(output), cwd)

    def _cmd_cp(self, args, cwd, env, stdin):
        values = [a for a in args if not a.startswith("-")]
        if len(values) != 2:
            return self._fail("cp: 需要源和目标", cwd, 2)
        src, dst = (self._resolve(self._virtual(cwd), x) for x in values)
        if not src.is_file():
            return self._fail("cp: 源文件不存在或不是普通文件", cwd)
        if dst.is_dir():
            dst = dst / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return self._ok("", cwd)

    def _cmd_mv(self, args, cwd, env, stdin):
        if len(args) != 2:
            return self._fail("mv: 需要源和目标", cwd, 2)
        src, dst = (self._resolve(self._virtual(cwd), x) for x in args)
        if not src.exists():
            return self._fail("mv: 源不存在", cwd)
        if dst.is_dir():
            dst = dst / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return self._ok("", cwd)

    def _cmd_rm(self, args, cwd, env, stdin):
        recursive = "-r" in args or "-rf" in args or "-fr" in args
        values = [a for a in args if not a.startswith("-")]
        if not values:
            return self._fail("rm: 缺少目标", cwd, 2)
        protected = {self.root, self._resolve(self.home)}
        for value in values:
            path = self._resolve(self._virtual(cwd), value)
            if path in protected:
                return self._fail("rm: 练习沙箱根目录受保护", cwd)
            if not path.exists():
                continue
            if path.is_dir():
                if not recursive:
                    return self._fail(f"rm: {value}: Is a directory", cwd)
                shutil.rmtree(path)
            else:
                path.unlink()
        return self._ok("", cwd)

    def _cmd_find(self, args, cwd, env, stdin):
        target_raw = next((a for a in args if not a.startswith("-") and a not in ("f", "d")), ".")
        target = self._resolve(self._virtual(cwd), target_raw)
        if not target.exists():
            return self._fail("find: 路径不存在", cwd)
        type_filter = None
        if "-type" in args and args.index("-type") + 1 < len(args):
            type_filter = args[args.index("-type") + 1]
        name_pattern = None
        if "-name" in args and args.index("-name") + 1 < len(args):
            name_pattern = args[args.index("-name") + 1]
        import fnmatch
        found = []
        candidates = [target] + list(target.rglob("*")) if target.is_dir() else [target]
        for path in candidates[:2000]:
            if type_filter == "f" and not path.is_file():
                continue
            if type_filter == "d" and not path.is_dir():
                continue
            if name_pattern and not fnmatch.fnmatch(path.name, name_pattern):
                continue
            found.append(self._virtual(path))
        return self._ok("\n".join(found), cwd)

    def _cmd_tree(self, args, cwd, env, stdin):
        target = self._resolve(self._virtual(cwd), args[0] if args else ".")
        lines = [self._virtual(target)]
        for path in sorted(target.rglob("*"))[:300]:
            depth = len(path.relative_to(target).parts)
            lines.append("    " * (depth - 1) + "└── " + path.name + ("/" if path.is_dir() else ""))
        return self._ok("\n".join(lines), cwd)

    def _cmd_whoami(self, args, cwd, env, stdin):
        return self._ok("student", cwd)

    def _cmd_hostname(self, args, cwd, env, stdin):
        return self._ok("codenova-lab", cwd)

    def _cmd_uname(self, args, cwd, env, stdin):
        return self._ok("Linux codenova-lab 6.8.0-sandbox x86_64 GNU/Linux" if "-a" in args else "Linux", cwd)

    def _cmd_date(self, args, cwd, env, stdin):
        return self._ok(_dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"), cwd)

    def _cmd_env(self, args, cwd, env, stdin):
        return self._ok("\n".join(f"{k}={v}" for k, v in sorted(env.items())), cwd)

    def _cmd_df(self, args, cwd, env, stdin):
        return self._ok("Filesystem      Size  Used Avail Use% Mounted on\nsandboxfs        1.0G   84M  940M   9% /", cwd)

    def _cmd_free(self, args, cwd, env, stdin):
        return self._ok("              total        used        free\nMem:           2.0Gi       640Mi       1.3Gi\nSwap:          1.0Gi          0B       1.0Gi", cwd)

    def _cmd_ps(self, args, cwd, env, stdin):
        return self._ok("PID TTY          TIME CMD\n  1 ?        00:00:01 init\n214 ?        00:00:00 nginx\n327 ?        00:00:02 python-app", cwd)

    def _cmd_ip(self, args, cwd, env, stdin):
        return self._ok("1: lo: <LOOPBACK,UP> mtu 65536\n    inet 127.0.0.1/8 scope host lo\n2: eth0: <BROADCAST,UP> mtu 1500\n    inet 10.0.2.15/24 scope global eth0", cwd)

    def _cmd_ss(self, args, cwd, env, stdin):
        return self._ok("State  Local Address:Port  Process\nLISTEN 0.0.0.0:80         nginx\nLISTEN 0.0.0.0:22         sshd\nLISTEN 127.0.0.1:3306     mysqld", cwd)

    def _cmd_ping(self, args, cwd, env, stdin):
        host = next((a for a in args if not a.startswith("-")), "127.0.0.1")
        return self._ok(f"PING {host} (203.0.113.10): 56 data bytes\n64 bytes from 203.0.113.10: time=12.4 ms\n--- {host} ping statistics ---\n1 packets transmitted, 1 received, 0% packet loss", cwd)

    def _cmd_curl(self, args, cwd, env, stdin):
        url = next((a for a in args if not a.startswith("-")), "http://localhost")
        return self._ok(f"HTTP/1.1 200 OK\nServer: sandbox-nginx\nContent-Type: text/plain\n\nSimulated response from {url}", cwd)

    def _service_name(self, name: str) -> str:
        return name[:-8] if name.endswith(".service") else name

    def _cmd_systemctl(self, args, cwd, env, stdin):
        values = [a for a in args if not a.startswith("-")]
        services = self.state["services"]
        enabled = self.state["enabled"]
        if not values:
            rows = ["UNIT               LOAD   ACTIVE   SUB"]
            for name, on in sorted(services.items()):
                rows.append(f"{name + '.service':<18} loaded {'active' if on else 'inactive':<8} {'running' if on else 'dead'}")
            return self._ok("\n".join(rows), cwd)

        action = values[0]
        service = self._service_name(values[1] if len(values) > 1 else "nginx")
        if action == "list-units":
            rows = ["UNIT               LOAD   ACTIVE   SUB"]
            for name, on in sorted(services.items()):
                rows.append(f"{name + '.service':<18} loaded {'active' if on else 'inactive':<8} {'running' if on else 'dead'}")
            return self._ok("\n".join(rows), cwd)

        if action in ("status", "is-active", "is-enabled"):
            if service not in services:
                return self._fail(f"Unit {service}.service could not be found.", cwd, 4)
            on = services[service]
            if action == "is-enabled":
                return self._ok("enabled" if enabled.get(service) else "disabled", cwd)
            if action == "is-active":
                return self._ok("active", cwd) if on else ("inactive", "", 3, cwd, False)
            state_line = "active (running)" if on else "inactive (dead)"
            return self._ok(
                f"● {service}.service - Sandbox {service}\n"
                f"   Loaded: loaded (/etc/systemd/system/{service}.service; {'enabled' if enabled.get(service) else 'disabled'})\n"
                f"   Active: {state_line}\n"
                f"   Main PID: {214 if on else 0} ({service})",
                cwd,
            )

        if action in ("start", "restart", "reload", "stop"):
            if service not in services:
                return self._fail(f"Failed to {action} {service}.service: Unit {service}.service not found.", cwd, 5)
            services[service] = action != "stop"
            word = "inactive (dead)" if action == "stop" else "active (running)"
            return self._ok(f"[sandbox] {action} {service}: 状态已改为 {word}（可用 status 验证）", cwd)

        if action in ("enable", "disable"):
            if service not in services:
                return self._fail(f"Failed to {action} unit: Unit file {service}.service does not exist.", cwd)
            enabled[service] = action == "enable"
            return self._ok(f"[sandbox] {action} {service}: 已{'设为' if action == 'enable' else '取消'}开机自启", cwd)

        return self._fail(f"systemctl: 不支持的练习动作 {action}", cwd)

    def _cmd_journalctl(self, args, cwd, env, stdin):
        service = args[args.index("-u") + 1] if "-u" in args and args.index("-u") + 1 < len(args) else "system"
        name = self._service_name(service)
        lines = [f"Sep 22 09:00:01 codenova-lab {name}[214]: service started"]
        if name in self.state["services"] and not self.state["services"][name]:
            lines.append(f"Sep 22 09:06:30 codenova-lab systemd[1]: Stopped Sandbox {name}.")
        else:
            lines.append(f"Sep 22 09:08:09 codenova-lab {name}[214]: request completed")
        return self._ok("\n".join(lines), cwd)

    def _cmd_docker(self, args, cwd, env, stdin):
        containers = self.state["containers"]
        if args[:2] == ["container", "ls"]:
            args = ["ps"] + args[2:]
        if args[:1] == ["ps"]:
            show_all = any(a.startswith("-") and "a" in a[1:] for a in args)
            rows = ["CONTAINER ID   IMAGE           STATUS                  PORTS                   NAMES"]
            for item in containers:
                if not show_all and item["status"].startswith("Exited"):
                    continue
                rows.append(
                    f"{item['id']:<14} {item['image']:<15} {item['status']:<23} {item['ports']:<23} {item['name']}"
                )
            return self._ok("\n".join(rows) if len(rows) > 1 else "", cwd)

        def find(target: str):
            return next((c for c in containers if target in (c["id"], c["name"])), None)

        if args[:1] in (["stop"], ["start"], ["rm"]):
            if len(args) < 2:
                return self._fail(f"docker: {args[0]} 需要一个容器名或 ID", cwd, 2)
            target = args[1]
            item = find(target)
            if item is None:
                return self._fail(f"Error response from daemon: No such container: {target}", cwd)
            if args[0] == "rm":
                containers.remove(item)
                return self._ok(target, cwd)
            item["status"] = "Exited (0) 1 second ago" if args[0] == "stop" else "Up 1 second"
            return self._ok(target, cwd)

        if args[:1] == ["run"]:
            image = next((a for a in args[1:] if not a.startswith("-")), None)
            if image is None:
                return self._fail("docker: run 需要镜像名，例如 docker run -d --name web nginx:alpine", cwd, 2)
            name = args[args.index("--name") + 1] if "--name" in args and args.index("--name") + 1 < len(args) else f"ctr{len(containers) + 1}"
            if find(name) is not None:
                return self._fail(f'docker: 容器名 "{name}" 已被使用', cwd)
            item = {
                "id": f"{0x5f0000 + len(containers) * 131 + 17:012x}",
                "image": image,
                "name": name,
                "status": "Up 1 second",
                "ports": "-",
            }
            containers.append(item)
            return self._ok(item["id"], cwd)

        if args[:1] == ["logs"] and len(args) > 1:
            item = find(args[1])
            if item is None:
                return self._fail(f"Error response from daemon: No such container: {args[1]}", cwd)
            return self._ok(f"[sandbox] {item['image']} 启动日志：listening on port 80", cwd)

        return self._ok("[sandbox] Docker 命令为教学模拟，不会调用本机 Docker。可练习：docker ps -a / run / stop / start / rm / logs", cwd)

    def _cmd_kubectl(self, args, cwd, env, stdin):
        pods = self.state["pods"]
        if args[:2] in (["get", "pods"], ["get", "pod"], ["get", "po"]):
            rows = ["NAME                        READY   STATUS    RESTARTS"]
            for pod in pods:
                rows.append(f"{pod['name']:<27} {pod['ready']:<7} {pod['status']:<9} {pod['restarts']}")
            return self._ok("\n".join(rows), cwd)
        if args[:2] in (["get", "deployments"], ["get", "deploy"]):
            ready = sum(1 for pod in pods if pod["status"] == "Running")
            return self._ok("NAME    READY   UP-TO-DATE   AVAILABLE\nweb     " + f"{ready}/{len(pods)}" + "     " + f"{ready}" + "            " + f"{ready}", cwd)
        if args[:1] == ["scale"]:
            raw = ""
            if "--replicas" in args and args.index("--replicas") + 1 < len(args):
                raw = args[args.index("--replicas") + 1]
            else:
                raw = next((a.split("=", 1)[1] for a in args if a.startswith("--replicas=")), "")
            try:
                target = int(raw)
            except ValueError:
                return self._fail("kubectl scale: --replicas 需要一个数字，例如 kubectl scale deployment web --replicas=2", cwd, 2)
            while len(pods) > target:
                pods.pop()
            while len(pods) < target:
                pods.append({"name": f"web-6d8f7c9d8b-{len(pods)}", "ready": "1/1", "status": "Running", "restarts": 0})
            return self._ok(f"deployment.apps/web scaled to {target}", cwd)
        if args[:1] == ["delete"] and len(args) > 1:
            target = args[-1]
            for pod in list(pods):
                if pod["name"] == target:
                    pods.remove(pod)
                    return self._ok(f'pod "{target}" deleted', cwd)
            return self._fail(f'Error from server (NotFound): pods "{target}" not found', cwd)
        return self._ok("[sandbox] kubectl 命令为教学模拟。可练习：kubectl get pods / get deployments / scale --replicas=2 / delete pod <名字>", cwd)

    def _cmd_history(self, args, cwd, env, stdin):
        if not self.history:
            return self._ok("", cwd)
        return self._ok("\n".join(f"{idx:>4}  {line}" for idx, line in enumerate(self.history[-100:], 1)), cwd)

    def _cmd_seq(self, args, cwd, env, stdin):
        values = [a for a in args if not a.startswith("-")]
        try:
            numbers = [int(v) for v in values]
        except ValueError:
            return self._fail("seq: 参数必须是整数，例如 seq 1 5", cwd, 2)
        if len(numbers) == 1:
            start, step, end = 1, 1, numbers[0]
        elif len(numbers) == 2:
            start, step, end = numbers[0], 1, numbers[1]
        elif len(numbers) == 3:
            start, step, end = numbers
        else:
            return self._fail("seq: 请使用 seq END、seq START END 或 seq START STEP END", cwd, 2)
        if step == 0:
            return self._fail("seq: 步长不能为 0", cwd, 2)
        count = 0
        current = start
        out = []
        while (step > 0 and current <= end) or (step < 0 and current >= end):
            out.append(str(current))
            current += step
            count += 1
            if count > 1000:
                break
        return self._ok("\n".join(out), cwd)

    def _cmd_true(self, args, cwd, env, stdin):
        return self._ok("", cwd)

    def _cmd_false(self, args, cwd, env, stdin):
        return ("", "", 1, cwd, False)

    def _cmd_sleep(self, args, cwd, env, stdin):
        return self._ok("[sandbox] sleep 为教学模拟，不会真的等待。", cwd)

    def _cmd_man(self, args, cwd, env, stdin):
        pages = {
            "ls": "ls - 列出目录内容。常用：ls -a 显示隐藏文件，ls -l 显示详细信息。",
            "cd": "cd - 切换目录。cd ~ 回到主目录，cd .. 返回上一级。",
            "grep": "grep - 在文本中搜索。常用：grep -i 忽略大小写，grep -n 显示行号，grep -c 只输出匹配行数。",
            "find": "find - 查找文件。例如：find . -name \"*.log\"。",
            "systemctl": "systemctl - 管理服务。常用：status / start / stop / restart / enable / disable。",
            "docker": "docker - 容器练习。常用：ps -a / run / stop / start / rm / logs。",
            "kubectl": "kubectl - Kubernetes 练习。常用：get pods / get deployments / scale / delete pod。",
            "chmod": "chmod - 修改权限（沙箱中仅模拟）。",
            "for": "for - 循环。写法：for i in 1 2 3; do echo $i; done。",
            "while": "while - 条件循环。写法：while [ -f a.txt ]; do ...; done。",
            "if": "if - 条件判断。写法：if [ -f a.txt ]; then echo yes; else echo no; fi。",
        }
        name = next((a for a in args if not a.startswith("-")), None)
        if name is None:
            return self._ok("用法：man 命令名（例如 man grep）。也可以用 help 查看全部命令。", cwd)
        if name in pages:
            return self._ok(pages[name], cwd)
        return self._ok(f"{name}: 沙箱里没有这个命令的手册页。输入 help 查看支持的命令。", cwd)

    def _cmd_chmod(self, args, cwd, env, stdin):
        return self._ok("[sandbox] 权限修改已模拟；沙箱文件不会影响电脑系统权限。", cwd)

    def _cmd_chown(self, args, cwd, env, stdin):
        return self._ok("[sandbox] 所有者修改已模拟。", cwd)
