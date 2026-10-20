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
        self.reset_if_empty()

    def reset_if_empty(self) -> None:
        (self.root / "home" / "student" / "projects").mkdir(parents=True, exist_ok=True)
        (self.root / "var" / "log").mkdir(parents=True, exist_ok=True)
        (self.root / "tmp").mkdir(parents=True, exist_ok=True)
        samples = {
            self.root / "home" / "student" / "README.txt": (
                "CloudOps Linux 练习沙箱\n"
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

    @staticmethod
    def _expand(text: str, env: dict[str, str], last_code: int) -> str:
        text = text.replace("$?", str(last_code))
        pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
        return pattern.sub(lambda m: env.get(m.group(1) or m.group(2), ""), text)

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
            "HOSTNAME": "cloudops-lab",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
        }
        cleaned = "\n".join(
            line for line in script.replace("\r\n", "\n").split("\n")
            if not line.lstrip().startswith("#!")
        )
        stdout, stderr, last_code, clear_screen = [], [], 0, False

        def run_block(block: str, current: Path) -> Path:
            nonlocal last_code, clear_screen
            loop_re = re.compile(
                r"for\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\s+(.+?)\s*;?\s*do\s+(.*?)\s*;?\s*done",
                re.DOTALL,
            )
            match = loop_re.search(block)
            if match:
                current = run_block(block[: match.start()], current)
                values = shlex.split(self._expand(match.group(2).strip().rstrip(";"), env, last_code), posix=True)
                for value in values[:100]:
                    env[match.group(1)] = value
                    current = run_block(match.group(3), current)
                return run_block(block[match.end() :], current)

            statements = []
            for line in block.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                statements.extend(self._split_unquoted(stripped, ";"))

            for statement in statements[:300]:
                expanded = self._expand(statement, env, last_code).strip()
                assignment = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", expanded)
                if assignment:
                    env[assignment.group(1)] = assignment.group(2).strip("\"'")
                    last_code = 0
                    continue
                if expanded.startswith("export "):
                    item = expanded[7:].strip()
                    assignment = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", item)
                    if assignment:
                        env[assignment.group(1)] = assignment.group(2).strip("\"'")
                        last_code = 0
                    else:
                        stderr.append("export: 请使用 NAME=value")
                        last_code = 2
                    continue
                result = self._run_pipeline(expanded, current, env)
                current = result["cwd"]
                if result["stdout"]:
                    stdout.append(result["stdout"])
                if result["stderr"]:
                    stderr.append(result["stderr"])
                last_code = result["code"]
                clear_screen = clear_screen or result.get("clear", False)
            return current

        try:
            real_cwd = run_block(cleaned, real_cwd)
        except (SandboxError, ValueError, OSError) as exc:
            stderr.append(str(exc))
            last_code = 1
        return {
            "status": "ok" if last_code == 0 else "error",
            "stdout": "\n".join(part.rstrip("\n") for part in stdout if part).rstrip(),
            "stderr": "\n".join(part.rstrip("\n") for part in stderr if part).rstrip(),
            "returncode": last_code,
            "cwd": self._virtual(real_cwd),
            "shell": "CloudOps Sandbox Shell（不调用本机终端）",
            "clear": clear_screen,
        }

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

        redirect, append = None, False
        for marker in (">>", ">"):
            if marker in tokens:
                idx = tokens.index(marker)
                if idx + 1 >= len(tokens):
                    return {"stdout": "", "stderr": "重定向缺少文件名", "code": 2, "cwd": cwd}
                redirect = tokens[idx + 1]
                append = marker == ">>"
                tokens = tokens[:idx]
                break

        name, args = tokens[0], tokens[1:]
        handler = getattr(self, f"_cmd_{name.replace('-', '_')}", None)
        if handler is None:
            return {"stdout": "", "stderr": f"{name}: command not found（输入 help 查看沙箱命令）", "code": 127, "cwd": cwd}
        try:
            out, err, code, new_cwd, clear = handler(args, cwd, env, stdin)
            if redirect and code == 0:
                target = self._resolve(self._virtual(new_cwd), redirect)
                target.parent.mkdir(parents=True, exist_ok=True)
                mode = "a" if append else "w"
                with target.open(mode, encoding="utf-8") as stream:
                    stream.write(out)
                    if out and not out.endswith("\n"):
                        stream.write("\n")
                out = ""
            return {"stdout": out, "stderr": err, "code": code, "cwd": new_cwd, "clear": clear}
        except (SandboxError, OSError) as exc:
            return {"stdout": "", "stderr": str(exc), "code": 1, "cwd": cwd, "clear": False}

    @staticmethod
    def _ok(out="", cwd=None, clear=False):
        return out, "", 0, cwd, clear

    @staticmethod
    def _fail(err, cwd, code=1):
        return "", err, code, cwd, False

    def _cmd_help(self, args, cwd, env, stdin):
        text = (
            "文件: pwd ls cd tree mkdir touch cat head tail wc cp mv rm find\n"
            "文本: echo printf grep sort uniq\n"
            "系统: whoami hostname uname date env df free ps systemctl journalctl\n"
            "网络: ip ss ping curl\n"
            "练习: docker kubectl clear help\n"
            "支持管道 |、覆盖 >、追加 >>、变量 NAME=value、$NAME 和基础 for 循环。\n"
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
        paths = [a for a in args if not a.startswith("-")]
        target = self._resolve(self._virtual(cwd), paths[-1] if paths else ".")
        if not target.exists():
            return self._fail(f"ls: {paths[-1] if paths else target.name}: No such file or directory", cwd)
        entries = [target] if target.is_file() else sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        entries = [p for p in entries if show_all or not p.name.startswith(".")]
        if long:
            lines = []
            for p in entries:
                kind = "d" if p.is_dir() else "-"
                size = 0 if p.is_dir() else p.stat().st_size
                lines.append(f"{kind}rw-r--r-- 1 student student {size:>7} {p.name}{'/' if p.is_dir() else ''}")
            return self._ok("\n".join(lines), cwd)
        return self._ok("  ".join(p.name + ("/" if p.is_dir() else "") for p in entries), cwd)

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
        newline = "-n" not in args
        values = [a for a in args if a != "-n"]
        return self._ok(" ".join(values) + ("" if not newline else ""), cwd)

    def _cmd_printf(self, args, cwd, env, stdin):
        return self._ok(" ".join(args).replace("\\n", "\n").replace("\\t", "\t"), cwd)

    def _cmd_grep(self, args, cwd, env, stdin):
        ignore_case, show_num = "-i" in args, "-n" in args
        values = [a for a in args if a not in ("-i", "-n")]
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
        return self._ok("\n".join(lines), cwd) if lines else ("", "", 1, cwd, False)

    def _cmd_sort(self, args, cwd, env, stdin):
        reverse = "-r" in args
        return self._ok("\n".join(sorted(stdin.splitlines(), reverse=reverse)), cwd)

    def _cmd_uniq(self, args, cwd, env, stdin):
        count = "-c" in args
        output, last, n = [], None, 0
        for line in stdin.splitlines() + [None]:
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
        return self._ok("cloudops-lab", cwd)

    def _cmd_uname(self, args, cwd, env, stdin):
        return self._ok("Linux cloudops-lab 6.8.0-sandbox x86_64 GNU/Linux" if "-a" in args else "Linux", cwd)

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

    def _cmd_systemctl(self, args, cwd, env, stdin):
        action = args[0] if args else "status"
        service = args[1] if len(args) > 1 else "nginx"
        if action in ("status", "is-active"):
            return self._ok("active" if action == "is-active" else f"● {service}.service - Sandbox {service}\n   Loaded: loaded\n   Active: active (running)", cwd)
        if action in ("start", "stop", "restart", "reload", "enable"):
            return self._ok(f"[sandbox] {action} {service}: 操作已模拟", cwd)
        return self._fail(f"systemctl: 不支持的练习动作 {action}", cwd)

    def _cmd_journalctl(self, args, cwd, env, stdin):
        service = args[args.index("-u") + 1] if "-u" in args and args.index("-u") + 1 < len(args) else "system"
        return self._ok(f"Sep 22 09:00:01 cloudops-lab {service}[214]: service started\nSep 22 09:08:09 cloudops-lab {service}[214]: request completed", cwd)

    def _cmd_docker(self, args, cwd, env, stdin):
        if args[:1] == ["ps"]:
            return self._ok("CONTAINER ID  IMAGE        STATUS       PORTS\na1b2c3d4e5f6  nginx:alpine Up 2 hours   0.0.0.0:8080->80/tcp", cwd)
        return self._ok("[sandbox] Docker 命令为教学模拟，不会调用本机 Docker。可练习：docker ps", cwd)

    def _cmd_kubectl(self, args, cwd, env, stdin):
        if args[:2] == ["get", "pods"]:
            return self._ok("NAME                      READY   STATUS    RESTARTS\nweb-6d8f7c9d8b-demo       1/1     Running   0", cwd)
        return self._ok("[sandbox] kubectl 命令为教学模拟。可练习：kubectl get pods", cwd)

    def _cmd_chmod(self, args, cwd, env, stdin):
        return self._ok("[sandbox] 权限修改已模拟；沙箱文件不会影响电脑系统权限。", cwd)

    def _cmd_chown(self, args, cwd, env, stdin):
        return self._ok("[sandbox] 所有者修改已模拟。", cwd)
