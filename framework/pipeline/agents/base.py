from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

import yaml

from pipeline.contracts import make_agent_result, validate_contract


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AGENT_CONFIG = PROJECT_ROOT / "config" / "agent_config.yaml"
KNOWN_AGENT_NAMES = {
    "risk_assessment",
    "refactoring",
    "iterative_repair",
    "build_test_validation",
    "smell_revalidation",
}
KNOWN_SERVICE_NAMES = {"candidate_recovery"}


class UnknownAgentError(ValueError):
    pass


class AgentDisabledError(RuntimeError):
    pass


class AgentExecutionNotImplemented(NotImplementedError):
    pass


class BaseAgent(ABC):
    """Common execution and contract interface for pipeline agents."""

    agent_name: ClassVar[str]
    agent_version: ClassVar[str] = "0.1"

    def __init__(self, *, enabled: bool = True):
        if self.agent_name not in KNOWN_AGENT_NAMES:
            raise UnknownAgentError(f"Unknown pipeline agent name: {self.agent_name}")
        self.enabled = bool(enabled)

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise AgentDisabledError(f"Agent '{self.agent_name}' is disabled and was not executed.")
        self.validate_input(request)
        raise AgentExecutionNotImplemented(
            f"Agent '{self.agent_name}' business execution is not implemented."
        )

    def validate_input(self, request: dict[str, Any]) -> None:
        validate_contract(request, "agent-request")
        if request["agent"] != self.agent_name:
            raise ValueError(
                f"Request agent '{request['agent']}' does not match "
                f"'{self.agent_name}'."
            )

    def generate_result(
        self,
        request: dict[str, Any],
        *,
        status: str = "NOT_IMPLEMENTED",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Generate a contract-valid architecture placeholder, never a fake success."""
        self.validate_input(request)
        result = {
            "reason": reason
            or f"Agent '{self.agent_name}' business execution is not implemented."
        }
        return make_agent_result(
            run_id=request["run_id"],
            instance_id=request["instance_id"],
            agent=self.agent_name,
            agent_version=self.agent_version,
            status=status,
            result=result,
        )

    @property
    @abstractmethod
    def input_contract(self) -> tuple[str, ...]:
        """Logical input artifact names expected by the implementation."""

    @property
    @abstractmethod
    def output_contract(self) -> str:
        """Logical output artifact path produced by the implementation."""


def _load_agent_payload(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else DEFAULT_AGENT_CONFIG
    if not config_path.is_absolute():
        config_path = (PROJECT_ROOT / config_path).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"Agent config does not exist: {config_path}")
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    agents = payload.get("agents")
    if not isinstance(agents, dict):
        raise ValueError("agent_config.yaml must contain an 'agents' mapping.")

    unknown = sorted(set(agents) - KNOWN_AGENT_NAMES - KNOWN_SERVICE_NAMES)
    if unknown:
        raise UnknownAgentError("Unknown agents in config: " + ", ".join(unknown))

    return agents


def load_agent_config(path: str | Path | None = None) -> dict[str, bool]:
    agents = _load_agent_payload(path)
    result: dict[str, bool] = {}
    for name in KNOWN_AGENT_NAMES:
        entry = agents.get(name)
        if not isinstance(entry, dict) or not isinstance(entry.get("enabled"), bool):
            raise ValueError(f"Agent '{name}' must define boolean enabled.")
        result[name] = entry["enabled"]
    return result


def load_risk_agent_config(path: str | Path | None = None) -> dict[str, Any]:
    from .decision_support import load_decision_settings
    entry = _load_agent_payload(path).get("risk_assessment")
    if not isinstance(entry, dict):
        raise ValueError('risk_assessment agent configuration must be a mapping.')
    if entry.get('mode', 'rule_only') not in {'rule_only', 'rule_llm'}:
        raise ValueError('Unsupported legacy risk mode; use decision.mode.')
    mode = load_decision_settings()['mode']
    supported_modes = {"rule_only", "rule_llm"}
    if mode not in supported_modes:
        raise ValueError(
            f"Unsupported risk assessment mode '{mode}'. Expected one of: "
            + ", ".join(sorted(supported_modes))
        )
    fallback = entry.get("llm_fallback") or {}
    if not isinstance(fallback, dict) or not isinstance(fallback.get("enabled", False), bool):
        raise ValueError("risk_assessment.llm_fallback.enabled must be boolean.")
    if fallback.get("enabled", False):
        raise ValueError(
            "Use decision.mode=rule_llm; legacy llm_fallback must remain disabled."
        )
    return {
        "enabled": entry["enabled"],
        "mode": mode,
        "llm_fallback_enabled": fallback.get("enabled", False),
    }


def load_refactoring_agent_config(path: str | Path | None = None) -> dict[str, Any]:
    entry = _load_agent_payload(path).get("refactoring")
    if not isinstance(entry, dict):
        raise ValueError("refactoring agent configuration must be a mapping.")
    mode = str(entry.get("mode") or "llm")
    if mode != "llm":
        raise ValueError("Phase 3 production refactoring mode must be 'llm'.")
    attempts = entry.get("max_generation_attempts", 1)
    if not isinstance(attempts, int) or attempts != 1:
        raise ValueError("Phase 3 max_generation_attempts must be exactly 1.")
    rag = entry.get("rag") or {}
    if not isinstance(rag, dict) or not isinstance(rag.get("enabled", False), bool):
        raise ValueError("refactoring.rag.enabled must be boolean.")
    top_k = rag.get("top_k", 3)
    if not isinstance(top_k, int) or top_k < 1:
        raise ValueError("refactoring.rag.top_k must be a positive integer.")
    required = rag.get("required", False)
    if not isinstance(required, bool):
        raise ValueError("refactoring.rag.required must be boolean.")
    failure_policy = str(rag.get("failure_policy") or "llm_only_fallback")
    if failure_policy not in {"llm_only_fallback", "block"}:
        raise ValueError("refactoring.rag.failure_policy must be llm_only_fallback or block.")
    from pipeline.model_config import local_role_enabled
    local_rag_enabled = local_role_enabled('rag')
    return {
        "enabled": entry.get("enabled", True),
        "mode": mode,
        "max_generation_attempts": attempts,
        "rag_enabled": rag.get("enabled", False) and local_rag_enabled is not False,
        "rag_top_k": top_k,
        "rag_required": required,
        "rag_failure_policy": failure_policy,
    }


def load_iterative_repair_agent_config(path: str | Path | None = None) -> dict[str, Any]:
    entry = _load_agent_payload(path).get("iterative_repair")
    if not isinstance(entry, dict):
        raise ValueError("iterative_repair agent configuration must be a mapping.")
    attempts = entry.get("max_attempts", 1)
    if attempts != 1:
        raise ValueError("Iterative repair V1 max_attempts must be exactly 1.")
    allowed = tuple(entry.get("allowed_failure_codes") or ())
    expected = {"MESSAGES_MEANINGFUL", "ONLY_TARGET_MESSAGES_ADDED"}
    if set(allowed) != expected:
        raise ValueError("Iterative repair V1 failure-code whitelist is fixed.")
    if entry.get("allowed_edit_scope") != "ASSERTION_MESSAGES_ONLY":
        raise ValueError("Iterative repair V1 edit scope must be ASSERTION_MESSAGES_ONLY.")
    return {"enabled": bool(entry.get("enabled", False)), "supported_smells": tuple(entry.get("supported_smells") or ()),
            "allowed_failure_codes": allowed, "max_attempts": 1, "allowed_edit_scope": "ASSERTION_MESSAGES_ONLY"}


def load_candidate_recovery_config(path: str | Path | None = None) -> dict[str, Any]:
    entry = _load_agent_payload(path).get("candidate_recovery")
    if not isinstance(entry, dict):
        raise ValueError("candidate_recovery configuration must be a mapping.")
    if entry.get("mode") != "BASELINE_ASSERTION_RECONSTRUCTION":
        raise ValueError("Candidate Recovery V1 mode is fixed.")
    if entry.get("max_attempts", 1) != 1:
        raise ValueError("Candidate Recovery V1 max_attempts must be exactly 1.")
    supported = tuple(entry.get("supported_smells") or ())
    if supported != ("Assertion_Roulette",):
        raise ValueError("Candidate Recovery V1 supports only Assertion_Roulette.")
    return {"enabled": bool(entry.get("enabled", False)), "mode": entry["mode"],
            "max_attempts": 1, "supported_smells": supported}


def load_validation_agent_config(path: str | Path | None = None) -> dict[str, Any]:
    entry = _load_agent_payload(path).get("build_test_validation")
    if not isinstance(entry, dict):
        raise ValueError("build_test_validation configuration must be a mapping.")
    levels = entry.get("validation_levels") or {}
    timeouts = entry.get("timeouts") or {}
    for name in ("structure", "compile", "target_test", "module_test"):
        if not isinstance(levels.get(name), bool):
            raise ValueError(f"validation_levels.{name} must be boolean.")
    if not all(levels.get(name) for name in ("structure", "compile", "target_test")):
        raise ValueError("Phase 4A requires structure, compile, and target_test levels.")
    for name in ("workspace_seconds", "compile_seconds", "target_test_seconds", "module_test_seconds"):
        if not isinstance(timeouts.get(name), int) or timeouts[name] < 1:
            raise ValueError(f"timeouts.{name} must be a positive integer.")
    if entry.get("isolation_mode") != "copy":
        raise ValueError("Phase 4A supports only controlled copy isolation.")
    return {
        "enabled": entry.get("enabled", True), "isolation_mode": "copy",
        "validation_levels": levels, "timeouts": timeouts,
        "preserve_failed_workspace": bool(entry.get("preserve_failed_workspace", False)),
        "allow_class_level_test_fallback": bool(entry.get("allow_class_level_test_fallback", True)),
        "allow_module_test_fallback": bool(entry.get("allow_module_test_fallback", False)),
        "max_output_chars": int(entry.get("max_output_chars", 20000)),
    }


def load_revalidation_agent_config(path: str | Path | None = None) -> dict[str, Any]:
    entry = _load_agent_payload(path).get("smell_revalidation")
    if not isinstance(entry, dict): raise ValueError("smell_revalidation configuration must be a mapping.")
    if entry.get("isolation_mode") != "copy": raise ValueError("Phase 5 supports only controlled copy isolation.")
    if entry.get("require_validation_passed") is not True or entry.get("use_same_detector_config") is not True:
        raise ValueError("Phase 5 validation and detector compatibility gates cannot be disabled.")
    mode=entry.get("mode") or ("count_only" if entry.get("report_only") is True else "per_instance_identity")
    if mode not in {"count_only","per_instance_identity"}:
        raise ValueError("Phase 5 supports count_only compatibility or per_instance_identity mode.")
    timeouts=entry.get("timeouts") or {}
    if any(not isinstance(timeouts.get(x),int) or timeouts[x]<1 for x in ("workspace_seconds","detector_seconds")):
        raise ValueError("Phase 5 timeouts must be positive integers.")
    report_only=bool(entry.get("report_only",mode=="count_only"))
    accept_on_pass=bool(entry.get("accept_on_pass",mode=="per_instance_identity"))
    if mode=="count_only" and (not report_only or accept_on_pass):
        raise ValueError("Phase 5 count_only compatibility mode must remain report-only.")
    if mode=="per_instance_identity" and (report_only or not accept_on_pass):
        raise ValueError("Phase 5 per-instance identity mode must gate final acceptance.")
    return {"enabled":entry.get("enabled",True),"mode":mode,"isolation_mode":"copy","timeouts":timeouts,
            "preserve_failed_workspace":bool(entry.get("preserve_failed_workspace",False)),"max_output_chars":int(entry.get("max_output_chars",20000)),
            "report_only":report_only,"accept_on_pass":accept_on_pass}


def create_agent(agent_name: str, *, enabled: bool = True) -> BaseAgent:
    if agent_name not in KNOWN_AGENT_NAMES:
        raise UnknownAgentError(f"Unknown pipeline agent name: {agent_name}")

    # Local imports avoid circular imports with pipeline.agents.__init__.
    from .refactoring_agent import RefactoringAgent
    from .iterative_repair import IterativeRepairAgent
    from .revalidation_agent import SmellRevalidationAgent
    from .risk_agent import RiskAssessmentAgent
    from .validation_agent import BuildTestValidationAgent

    registry: dict[str, type[BaseAgent]] = {
        "risk_assessment": RiskAssessmentAgent,
        "refactoring": RefactoringAgent,
        "iterative_repair": IterativeRepairAgent,
        "build_test_validation": BuildTestValidationAgent,
        "smell_revalidation": SmellRevalidationAgent,
    }
    return registry[agent_name](enabled=enabled)
