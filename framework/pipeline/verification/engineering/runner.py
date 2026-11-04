from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

from pipeline.agents.failure_classifier import classify_failure

SECRET_PATTERN = re.compile(r"(?i)(api[_-]?key|token|authorization|password)(\s*[:=]\s*)([^\s]+)")


def utc_now() -> str: return datetime.now(timezone.utc).isoformat()


def redact(value: str) -> str:
    return SECRET_PATTERN.sub(lambda m: m.group(1) + m.group(2) + "[REDACTED]", value or "")


class MavenValidationRunner:
    runner_version = "4A.0"

    def __init__(self, *, max_output_chars: int = 20000): self.max_output_chars = max_output_chars

    @staticmethod
    def resolve_maven() -> str | None: return shutil.which("mvn")

    def run(self, *, stage: str, args: list[str], cwd: Path, timeout_seconds: int) -> tuple[dict[str, Any], str, str]:
        started, clock = utc_now(), monotonic(); executable = self.resolve_maven()
        command = [executable or "mvn", *args]
        if executable is None:
            decision, status, category = classify_failure(stage, "", command_missing=True)
            return _stage(stage, status, command, started, utc_now(), int((monotonic()-clock)*1000), None, False, category, "Maven executable was not found."), "", ""
        try:
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            process = subprocess.Popen(command, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", shell=False, env=_safe_environment(), creationflags=creationflags)
            try:
                raw_stdout, raw_stderr = process.communicate(timeout=timeout_seconds)
            except KeyboardInterrupt:
                _terminate_process_tree(process)
                process.communicate()
                raise
            except subprocess.TimeoutExpired:
                _terminate_process_tree(process)
                raw_stdout, raw_stderr = process.communicate()
                stdout, stderr = redact(raw_stdout), redact(raw_stderr); _, status, category = classify_failure(stage, stdout+stderr, timed_out=True)
                return _stage(stage, status, command, started, utc_now(), int((monotonic()-clock)*1000), None, True, category, "Command timed out."), stdout, stderr
            stdout, stderr = redact(raw_stdout), redact(raw_stderr); combined = stdout + "\n" + stderr
            if process.returncode == 0:
                status, category = "PASS", None
            else:
                _, status, category = classify_failure(stage, combined)
            result = _stage(stage, status, command, started, utc_now(), int((monotonic()-clock)*1000), process.returncode, False, category, _tail(combined, self.max_output_chars))
            return result, stdout, stderr
        except OSError as exc:
            _, status, category = classify_failure(stage, str(exc), command_missing=isinstance(exc, FileNotFoundError))
            return _stage(stage, status, command, started, utc_now(), int((monotonic()-clock)*1000), None, False, category, str(exc)), "", redact(str(exc))


def _stage(stage, status, command, started, completed, duration, exit_code, timed_out, category, summary):
    return {"stage": stage, "status": status, "command_ref": stage.lower(), "sanitized_command": [Path(command[0]).name, *command[1:]], "started_at": started, "completed_at": completed, "duration_ms": duration, "exit_code": exit_code, "timed_out": timed_out, "summary": summary, "failure_category": category, "artifacts": []}


def _tail(value: str, limit: int) -> str: return value[-limit:]
def _as_text(value): return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
def _safe_environment():
    blocked = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH")
    return {k: v for k, v in os.environ.items() if not any(word in k.upper() for word in blocked)}


def _terminate_process_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False, shell=False)
    else:
        process.kill()
