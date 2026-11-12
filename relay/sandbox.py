"""Sandboxed test runner. Model-written code is untrusted.

Each patch runs in a fresh process, in a fresh temp directory, with a 10 second
timeout and no network:

* mode `docker` (best): `docker run --network none --memory ... --read-only`.
* mode `subprocess` (Linux, WSL, macOS): resource limits on CPU time, memory,
  file size and open files; on Linux the process is also put in an empty
  network namespace with `unshare -rn` when the kernel allows it. A socket
  guard at the top of the program is a second layer, not the main one.

On plain Windows only the timeout would apply, so use Docker or WSL there.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from relay.trace import sha256_text

SOCKET_GUARD = '''\
def __relay_no_network():
    import socket as _s
    def _blocked(*a, **k):
        raise OSError("network access is disabled in the Relay sandbox")
    for _name in ("socket", "create_connection", "create_server", "socketpair", "getaddrinfo"):
        if hasattr(_s, _name):
            setattr(_s, _name, _blocked)
__relay_no_network()
del __relay_no_network
'''


@dataclass
class VerifyResult:
    passed: bool
    status: str  # passed | failed | timeout | error | no_code
    test_ms: float
    stderr_tail: str
    code_hash: str | None

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "status": self.status,
            "test_ms": round(self.test_ms, 1),
            "code_hash": self.code_hash,
            "stderr_tail": self.stderr_tail,
        }


def build_program(code: str, task: dict, guard: bool = True) -> str:
    """Program = guard + the task's imports + candidate code + the task's tests."""
    imports = "\n".join(
        line for line in task["declaration"].splitlines() if line.startswith(("import ", "from "))
    )
    test = task["test"]
    entry = task["entry_point"]
    if not re.search(rf"^check\({re.escape(entry)}\)", test, re.MULTILINE):
        test = test.rstrip() + f"\n\ncheck({entry})\n"
    parts = [SOCKET_GUARD if guard else "", imports, code, "", test]
    return "\n".join(p for p in parts if p is not None) + "\n"


@lru_cache(maxsize=1)
def _unshare_works() -> bool:
    exe = shutil.which("unshare")
    if not exe or not sys.platform.startswith("linux"):
        return False
    try:
        r = subprocess.run([exe, "-rn", "true"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _limits(memory_mb: int, cpu_s: int):
    def apply():  # runs in the child before exec
        import resource

        mem = memory_mb * 1024 * 1024
        for res, val in (
            (resource.RLIMIT_AS, mem),
            (resource.RLIMIT_CPU, cpu_s),
            (resource.RLIMIT_FSIZE, 10 * 1024 * 1024),
            (resource.RLIMIT_NOFILE, 64),
            (resource.RLIMIT_CORE, 0),
        ):
            try:
                resource.setrlimit(res, (val, val))
            except (ValueError, OSError):
                pass
        os.setsid()

    return apply


def _tail(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else "..." + s[-n:]


class Sandbox:
    def __init__(self, mode: str = "subprocess", timeout_s: float = 10, memory_mb: int = 1024,
                 network: str = "off", docker_image: str = "python:3.12-slim", stderr_chars: int = 1500):
        self.mode = mode
        self.timeout_s = timeout_s
        self.memory_mb = memory_mb
        # YAML reads a bare `off` as False; normalise to "off" / "on"
        self.network = "off" if str(network).lower() in ("off", "false", "none", "0") else "on"
        self.docker_image = docker_image
        self.stderr_chars = stderr_chars

    @classmethod
    def from_config(cls, config) -> "Sandbox":
        s = config["sandbox"]
        return cls(
            mode=s.get("mode", "subprocess"),
            timeout_s=float(s.get("timeout_s", 10)),
            memory_mb=int(s.get("memory_mb", 1024)),
            network=s.get("network", "off"),
            docker_image=s.get("docker_image", "python:3.12-slim"),
            stderr_chars=int(config["generation"].get("test_output_max_chars", 1500)),
        )

    def describe(self) -> dict:
        d = {"mode": self.mode, "timeout_s": self.timeout_s, "memory_mb": self.memory_mb, "network": self.network}
        if self.mode == "subprocess":
            d["net_namespace"] = _unshare_works() and self.network == "off"
        else:
            d["image"] = self.docker_image
        return d

    def run_program(self, program: str, timeout_s: float | None = None) -> tuple[int | None, str, str, float]:
        """Run one Python program in the sandbox. Returns (returncode or None on timeout, stdout, stderr, ms)."""
        timeout_s = timeout_s or self.timeout_s
        with tempfile.TemporaryDirectory(prefix="relay-sbx-") as tmp:
            path = Path(tmp) / "solution.py"
            path.write_text(program, encoding="utf-8")
            if self.mode == "docker":
                cmd = [
                    "docker", "run", "--rm", "-i",
                    "--network", "none" if self.network == "off" else "bridge",
                    "--memory", f"{self.memory_mb}m", "--cpus", "1", "--pids-limit", "64",
                    "--read-only", "--tmpfs", "/tmp:size=16m",
                    "-v", f"{tmp}:/w:ro", "-w", "/w",
                    self.docker_image, "python", "-I", "-B", "solution.py",
                ]
                preexec = None
            else:
                cmd = [sys.executable, "-I", "-B", str(path)]
                if self.network == "off" and _unshare_works():
                    cmd = ["unshare", "-rn"] + cmd
                preexec = _limits(self.memory_mb, int(timeout_s) + 1) if os.name == "posix" else None
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONHASHSEED": "0", "HOME": tmp}
            t0 = time.perf_counter()
            try:
                proc = subprocess.run(
                    cmd, cwd=tmp, env=env, capture_output=True, text=True,
                    timeout=timeout_s, preexec_fn=preexec, stdin=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired as e:
                ms = (time.perf_counter() - t0) * 1000.0
                err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
                return None, "", err, ms
            ms = (time.perf_counter() - t0) * 1000.0
            clean = lambda x: (x or "").replace(tmp + "/", "").replace(tmp, ".")  # noqa: E731
            return proc.returncode, clean(proc.stdout), clean(proc.stderr), ms

    def run(self, code: str | None, task: dict) -> VerifyResult:
        if code is None or not code.strip():
            return VerifyResult(False, "no_code", 0.0, "No Python code block found in the model output.", None)
        program = build_program(code, task, guard=self.network == "off")
        code_hash = sha256_text(code)
        rc, stdout, stderr, ms = self.run_program(program)
        if rc is None:
            return VerifyResult(False, "timeout", ms,
                                _tail(f"Timed out after {self.timeout_s:.0f}s.\n{stderr}", self.stderr_chars), code_hash)
        if rc == 0:
            return VerifyResult(True, "passed", ms, "", code_hash)
        out = stderr + (("\n" + stdout) if stdout else "")
        status = "failed" if "AssertionError" in out else "error"
        if rc < 0 or "MemoryError" in out:
            status = "error"
        return VerifyResult(False, status, ms, _tail(out, self.stderr_chars), code_hash)
