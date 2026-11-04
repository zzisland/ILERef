from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from pipeline.contracts import validate_contract


_VOLATILE_KEYS = {"requested_at", "started_at", "completed_at", "duration_ms"}


def canonical_payload(value: Any) -> Any:
    """Return a deterministic, credential-free value suitable for fingerprints."""
    if isinstance(value, dict):
        return {
            key: canonical_payload(item)
            for key, item in sorted(value.items())
            if key not in _VOLATILE_KEYS and not _sensitive(key)
        }
    if isinstance(value, (list, tuple)):
        return [canonical_payload(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value


def stable_sha256(value: Any) -> str:
    encoded = json.dumps(
        canonical_payload(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def stage_fingerprint(*, stage: str, request: dict[str, Any], identity: dict[str, Any]) -> str:
    payload = request.get('input') or {}
    inventory_identity = {}
    smell = (payload.get('instance') or {}).get('smell_type') or payload.get('target_smell') or ((payload.get('refactoring_result') or {}).get('result') or {}).get('smell')
    if smell in {'Assertion_Roulette', 'Redundant_Assertion', 'Exception_Catching_Throwing'}:
        from .agents.api_scope import API_BINDING_RULE_VERSION
        inventory_identity.update(api_binding_rule_version=API_BINDING_RULE_VERSION,
            api_binding_source_sha256=file_sha256(Path(__file__).parent / 'agents' / 'api_scope.py'))
    if smell == 'Redundant_Assertion':
        from .agents.ra_contract import VERSION as ra_version, RULE_VERSION as ra_rule_version
        inventory_identity.update(ra_contract_version=ra_version, ra_rule_version=ra_rule_version,
            ra_contract_sha256=file_sha256(Path(__file__).parent / 'agents' / 'ra_contract.py'))
        if stage in {'risk', 'refactoring'}:
            from .agents.safety_contracts import analyze
            inventory_identity['ra_current_proof'] = analyze(payload.get('instance') or {}, payload.get('context') or {})
    if smell == 'Conditional_Test_Logic':
        from .agents.ctl_contract import VERSION as ctl_version
        inventory_identity['ctl_rule_version'] = ctl_version
        inventory_identity['ctl_contract_sha256'] = file_sha256(Path(__file__).parent / 'agents' / 'ctl_contract.py')
    if smell == 'Exception_Catching_Throwing':
        from .agents.safety_contracts import (
            ECT_DISPATCH_CONTRACT_VERSION, ECT_MUST_THROW_CONTRACT_VERSION,
            ECT_NOTHROW_CONTRACT_VERSION,
        )
        inventory_identity.update(
            ect_dispatch_contract_version=ECT_DISPATCH_CONTRACT_VERSION,
            ect_must_throw_contract_version=ECT_MUST_THROW_CONTRACT_VERSION,
            ect_nothrow_contract_version=ECT_NOTHROW_CONTRACT_VERSION,
            ect_contract_sha256=file_sha256(Path(__file__).parent / 'agents' / 'safety_contracts.py'),
        )
    if stage == 'revalidation':
        from .agents.test_method_counter import METHOD_INVENTORY_VERSION
        inventory_identity['method_inventory_version'] = METHOD_INVENTORY_VERSION
        inventory_identity['method_inventory_sha256'] = file_sha256(Path(__file__).parent / 'agents' / 'test_method_counter.py')
    # Include current bytes even when detector records and embedded context are stale.
    source_identity = {}
    if stage in {'risk', 'refactoring'}:
        instance = payload.get('instance') or {}
        source = Path(instance.get('test_file_path') or '')
        source_identity = {'current_source_sha256': file_sha256(source)}
    return stable_sha256({
        **source_identity,
        **inventory_identity,
        "safety_contract_version": __import__('pipeline.agents.safety_contracts', fromlist=['VERSION']).VERSION,
        "mnt_contract_version": __import__('pipeline.agents.java_type_evidence', fromlist=['MNT_RULE_VERSION']).MNT_RULE_VERSION,
        "mnt_validation_version": __import__('pipeline.validation_evidence', fromlist=['MNT_VALIDATION_VERSION']).MNT_VALIDATION_VERSION,
        "validation_evidence_version": __import__("pipeline.validation_evidence",fromlist=["VALIDATION_EVIDENCE_VERSION"]).VALIDATION_EVIDENCE_VERSION,
        "ra_oracle_version": "2",
        "stage": stage,
        "run_id": request["run_id"],
        "instance_id": request["instance_id"],
        "agent": request["agent"],
        "agent_version": request["agent_version"],
        "request_input": request["input"],
        "identity": identity,
    })


class StageCache:
    """Conservative same-run cache backed by result.json plus execution.json."""

    def __init__(self, instance_root: Path):
        self.instance_root = Path(instance_root).resolve()

    def load(
        self, stage: str, fingerprint: str, request: dict[str, Any],
        verify: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        result_path = self.instance_root / stage / "result.json"
        metadata_path = self.instance_root / stage / "execution.json"
        if not result_path.is_file() or not metadata_path.is_file():
            return None, "MISSING_RESULT_OR_FINGERPRINT"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            payload = request.get('input') or {}
            smell = (payload.get('instance') or {}).get('smell_type') or payload.get('target_smell') or (
                (payload.get('refactoring_result') or {}).get('result') or {}).get('smell')
            if smell == 'Conditional_Test_Logic':
                from .agents.ctl_contract import VERSION as ctl_version
                if metadata.get('ctl_rule_version') != ctl_version:
                    return None, 'CTL_RULE_VERSION_REAUDIT_REQUIRED'
            if smell == 'Redundant_Assertion':
                from .agents.ra_contract import VERSION as ra_version, RULE_VERSION as ra_rule_version
                if metadata.get('ra_contract_version') != ra_version or metadata.get('ra_rule_version') != ra_rule_version:
                    return None, 'RA_RULE_VERSION_REAUDIT_REQUIRED'
            if smell == 'Exception_Catching_Throwing':
                from .agents.safety_contracts import ECT_DISPATCH_CONTRACT_VERSION
                if metadata.get('ect_dispatch_contract_version') != ECT_DISPATCH_CONTRACT_VERSION:
                    return None, 'ECT_CONTRACT_VERSION_REAUDIT_REQUIRED'
            from .agents.java_type_evidence import MNT_RULE_VERSION
            if smell == 'Magic_Number_Test' and metadata.get('mnt_contract_version') != MNT_RULE_VERSION:
                return None, 'MNT_RULE_VERSION_REAUDIT_REQUIRED'
            if smell=='Magic_Number_Test' and stage in {'validation','revalidation'}:
                from .validation_evidence import MNT_VALIDATION_VERSION
                if metadata.get('mnt_validation_version')!=MNT_VALIDATION_VERSION:
                    return None,'MNT_VALIDATION_EVIDENCE_REAUDIT_REQUIRED'
            from .agents.safety_contracts import VERSION
            if metadata.get('safety_contract_version') != VERSION:
                return None, 'RULE_VERSION_REAUDIT_REQUIRED'
            result = json.loads(result_path.read_text(encoding="utf-8"))
            validate_contract(result, "agent-result")
            if metadata.get("input_fingerprint") != fingerprint:
                return None, "INPUT_FINGERPRINT_CHANGED"
            if result.get("run_id") != request["run_id"] or result.get("instance_id") != request["instance_id"]:
                return None, "RESULT_IDENTITY_CHANGED"
            if result.get("agent") != request["agent"] or result.get("agent_version") != request["agent_version"]:
                return None, "AGENT_IDENTITY_CHANGED"
            if metadata.get("result_sha256") != file_sha256(result_path):
                return None, "RESULT_HASH_CHANGED"
            self._verify_artifacts(result)
            if stage in {'validation','revalidation'} and result['result'].get('decision') not in {'VALIDATION_PASSED','REVALIDATION_PASSED'}:
                return None, 'ENGINEERING_RECHECK_REQUIRED'
            if stage in {'validation','revalidation'} and result['result'].get('decision') in {'VALIDATION_PASSED','REVALIDATION_PASSED'}:
                from .validation_evidence import verify_cached_mnt
                verify_cached_mnt(stage,result,payload)
            if verify:
                verify(result)
            return result, None
        except Exception as exc:
            return None, f"CACHE_VALIDATION_FAILED:{type(exc).__name__}"

    def record(self, stage: str, fingerprint: str, result_path: Path, *, decision: str | None) -> Path:
        metadata = {
            "mnt_validation_version": __import__('pipeline.validation_evidence', fromlist=['MNT_VALIDATION_VERSION']).MNT_VALIDATION_VERSION,
            "mnt_contract_version": __import__('pipeline.agents.java_type_evidence', fromlist=['MNT_RULE_VERSION']).MNT_RULE_VERSION,
            "safety_contract_version": __import__('pipeline.agents.safety_contracts', fromlist=['VERSION']).VERSION,
            "cache_contract_version": "1.0",
            "stage": stage,
            "input_fingerprint": fingerprint,
            "result_ref": f"{stage}/result.json",
            "result_sha256": file_sha256(result_path),
            "decision": decision,
        }
        metadata['ect_dispatch_contract_version'] = __import__(
            'pipeline.agents.safety_contracts', fromlist=['ECT_DISPATCH_CONTRACT_VERSION']
        ).ECT_DISPATCH_CONTRACT_VERSION
        metadata['ctl_rule_version'] = __import__('pipeline.agents.ctl_contract', fromlist=['VERSION']).VERSION
        # Additive RA namespace; only RA cache loads consult these versions.
        from .agents.ra_contract import VERSION as ra_version, RULE_VERSION as ra_rule_version
        metadata.update(ra_contract_version=ra_version, ra_rule_version=ra_rule_version)
        path = self.instance_root / stage / "execution.json"
        path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def _verify_artifacts(self, result: dict[str, Any]) -> None:
        for artifact in result.get("artifacts") or []:
            relative = Path(artifact["path"])
            target = (self.instance_root / relative).resolve()
            if relative.is_absolute() or not target.is_relative_to(self.instance_root) or not target.is_file():
                raise ValueError("Cached artifact is missing or outside the instance root.")
            expected = artifact.get("sha256")
            # Existing artifact manifests hash logical UTF-8 text before Windows
            # newline translation, so preserve that established semantic here.
            actual = hashlib.sha256(target.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            if expected and actual != expected:
                raise ValueError("Cached artifact hash mismatch.")


def _sensitive(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in {'max_tokens', 'input_tokens', 'output_tokens', 'total_tokens'}:
        return False
    return any(token in normalized for token in ("api_key", "apikey", "token", "secret", "authorization", "password"))
