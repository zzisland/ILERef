from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


class ContractValidationError(ValueError):
    pass


def load_schema(schema_name: str) -> dict[str, Any]:
    directory = Path(__file__).resolve().parents[1] / "instances" / "schemas" if schema_name == "pipeline-instance" else SCHEMA_DIR
    path = directory / f"{schema_name}.schema.json"
    if not path.is_file():
        raise FileNotFoundError(f"Contract schema does not exist: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_contract(payload: dict[str, Any], schema_name: str) -> None:
    validator = Draft202012Validator(load_schema(schema_name))
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    if not errors:
        return
    messages = []
    for error in errors:
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        messages.append(f"{location}: {error.message}")
    raise ContractValidationError(
        f"Contract '{schema_name}' validation failed: " + "; ".join(messages)
    )

