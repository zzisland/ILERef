from __future__ import annotations

from typing import Any

from pipeline import CONTRACT_VERSION

from .validation import validate_contract


AGENTS = {
    "risk_assessment",
    "refactoring",
    "iterative_repair",
    "build_test_validation",
    "smell_revalidation",
}


def make_agent_request(
    *,
    run_id: str,
    instance_id: str,
    agent: str,
    agent_version: str,
    input_payload: dict[str, Any],
    artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    _require_agent(agent)
    envelope = {
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "instance_id": instance_id,
        "agent": agent,
        "agent_version": agent_version,
        "status": "NOT_EXECUTED",
        "input": input_payload,
        "errors": [],
        "artifacts": list(artifacts or []),
    }
    validate_contract(envelope, "agent-request")
    return envelope


def make_agent_result(
    *,
    run_id: str,
    instance_id: str,
    agent: str,
    agent_version: str,
    status: str,
    result: dict[str, Any],
    errors: list[dict[str, str]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    _require_agent(agent)
    envelope = {
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "instance_id": instance_id,
        "agent": agent,
        "agent_version": agent_version,
        "status": status,
        "result": result,
        "errors": list(errors or []),
        "artifacts": list(artifacts or []),
    }
    validate_contract(envelope, "agent-result")
    return envelope


def _require_agent(agent: str) -> None:
    if agent not in AGENTS:
        raise ValueError(f"Unknown pipeline agent: {agent}")
