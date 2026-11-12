#!/usr/bin/env python3
"""Local state and recovery history for codex-thread-title-manager.

The helper reads exactly one JSON object from stdin and writes one JSON object to
stdout. It never reads Codex transcripts, rollouts, caches, or databases.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_HISTORY = 5000
MAX_TITLE_CHARS = 200
MAX_LABEL_CHARS = 120


class StateError(Exception):
    pass


def state_dir() -> Path:
    override = os.environ.get("CODEX_TITLE_MANAGER_STATE_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise StateError("LOCALAPPDATA is not set")
    return Path(base) / "CodexThreadTitleManager"


def state_path() -> Path:
    return state_dir() / "state.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def clean_text(value: Any, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise StateError(f"{field} must be a string")
    value = value.strip()
    if not value and not allow_empty:
        raise StateError(f"{field} must not be empty")
    if len(value) > maximum:
        raise StateError(f"{field} exceeds {maximum} characters")
    if any(ord(char) < 32 and char not in "\t" for char in value):
        raise StateError(f"{field} contains control characters")
    return value


def empty_state() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "threads": {}, "history": []}


def load_state() -> dict[str, Any]:
    path = state_path()
    if not path.exists():
        return empty_state()
    size = path.stat().st_size
    if size > MAX_STATE_BYTES:
        raise StateError("state file exceeds the 2 MiB safety limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"cannot read valid state: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise StateError("unsupported state schema")
    if not isinstance(value.get("threads"), dict) or not isinstance(value.get("history"), list):
        raise StateError("invalid state structure")
    return value


class StateLock:
    def __init__(self) -> None:
        self.path = state_dir() / "state.json.lock"
        self.fd: int | None = None

    def __enter__(self) -> "StateLock":
        state_dir().mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self.fd, json.dumps({"pid": os.getpid(), "created_at": now_iso()}).encode("utf-8"))
            os.close(self.fd)
            self.fd = None
        except FileExistsError as exc:
            raise StateError("state is locked by another title-manager process") from exc
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def save_state(value: dict[str, Any]) -> None:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_STATE_BYTES:
        raise StateError("refusing to write state above the 2 MiB safety limit")
    fd, temporary = tempfile.mkstemp(prefix="state-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, state_path())
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def request_value(request: dict[str, Any], key: str, maximum: int, *, allow_empty: bool = False) -> str:
    return clean_text(request.get(key), key, maximum, allow_empty=allow_empty)


def inspect(state: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    thread_id = request_value(request, "thread_id", MAX_LABEL_CHARS)
    current_title = request_value(request, "current_title", MAX_TITLE_CHARS)
    entry = state["threads"].get(thread_id)
    if not entry:
        status = "unmanaged"
    elif entry.get("locked"):
        status = "locked"
    elif entry.get("last_applied_title_hash") == digest(current_title):
        status = "managed"
    else:
        status = "changed_since_manager"
    return {
        "ok": True,
        "command": "inspect",
        "thread_id": thread_id,
        "status": status,
        "last_applied_at": entry.get("last_applied_at") if entry else None,
    }


def record(state: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    thread_id = request_value(request, "thread_id", MAX_LABEL_CHARS)
    old_title = request_value(request, "old_title", MAX_TITLE_CHARS)
    new_title = request_value(request, "new_title", MAX_TITLE_CHARS)
    project = request_value(request, "project", MAX_LABEL_CHARS, allow_empty=True)
    reason = request_value(request, "reason", MAX_LABEL_CHARS)
    source = request_value(request, "source", 32)
    if source not in {"current", "batch", "rollback"}:
        raise StateError("source must be current, batch, or rollback")
    timestamp = now_iso()
    state["threads"][thread_id] = {
        "last_applied_title_hash": digest(new_title),
        "last_applied_title": new_title,
        "last_applied_at": timestamp,
        "locked": False,
        "project": project,
    }
    state["history"].append(
        {
            "thread_id": thread_id,
            "old_title": old_title,
            "new_title": new_title,
            "project": project,
            "reason": reason,
            "source": source,
            "timestamp": timestamp,
        }
    )
    if len(state["history"]) > MAX_HISTORY:
        state["history"] = state["history"][-MAX_HISTORY:]
    return {"ok": True, "command": "record", "thread_id": thread_id, "recorded_at": timestamp}


def set_lock(state: dict[str, Any], request: dict[str, Any], locked: bool) -> dict[str, Any]:
    thread_id = request_value(request, "thread_id", MAX_LABEL_CHARS)
    entry = state["threads"].get(thread_id)
    if not entry:
        entry = {
            "last_applied_title_hash": None,
            "last_applied_title": None,
            "last_applied_at": None,
            "project": "",
        }
        state["threads"][thread_id] = entry
    entry["locked"] = locked
    entry["lock_updated_at"] = now_iso()
    return {"ok": True, "command": "lock" if locked else "unlock", "thread_id": thread_id, "locked": locked}


def history(state: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    thread_id = request.get("thread_id")
    if thread_id is not None:
        thread_id = clean_text(thread_id, "thread_id", MAX_LABEL_CHARS)
    limit = request.get("limit", 50)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
        raise StateError("limit must be an integer from 1 to 200")
    rows = state["history"]
    if thread_id:
        rows = [row for row in rows if row.get("thread_id") == thread_id]
    return {"ok": True, "command": "history", "entries": rows[-limit:]}


def stats(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "command": "stats",
        "schema_version": SCHEMA_VERSION,
        "thread_count": len(state["threads"]),
        "history_count": len(state["history"]),
        "state_path": str(state_path()),
    }


def handle(request: dict[str, Any]) -> dict[str, Any]:
    command = clean_text(request.get("command"), "command", 32)
    if command in {"inspect", "history", "stats"}:
        state = load_state()
        if command == "inspect":
            return inspect(state, request)
        if command == "history":
            return history(state, request)
        return stats(state)
    if command not in {"record", "lock", "unlock"}:
        raise StateError("unsupported command")
    with StateLock():
        state = load_state()
        if command == "record":
            response = record(state, request)
        else:
            response = set_lock(state, request, command == "lock")
        save_state(state)
        return response


def main() -> int:
    try:
        raw = input()
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise StateError("request must be a JSON object")
        response = handle(request)
        print(json.dumps(response, ensure_ascii=False, sort_keys=True))
        return 0
    except (EOFError, json.JSONDecodeError, StateError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

