from __future__ import annotations

from pathlib import Path
from typing import Any

from pipeline.contracts import make_agent_result, validate_contract

from .base import AgentDisabledError, BaseAgent, load_refactoring_agent_config
from .candidate_validation import CandidateValidator
from .prompt_service import RefactoringPromptService
from .refactoring_contract import build_refactoring_contract, sha256_text
from pipeline.hashing import METHOD_HASH_VERSION, sha256_method_text, sha256_utf8_lf
from .refactoring_policy import RefactoringPolicyGuard
from .refactoring_providers import ExistingLLMRefactoringProvider, safe_exception_diagnostic
from .refactoring_types import REFACTORING_DECISIONS, RefactoringRejected
from .rag_service import OptionalRagService
from pipeline.contract_builders import ExecutableContractBuilder


class RefactoringAgent(BaseAgent):
    agent_name = "refactoring"
    agent_version = "3.0"

    def __init__(self, *, enabled: bool | None = None, provider=None, config_path=None, prompt_service=None, validator=None, policy_guard=None, rag_service=None, contract_builder=None, strategy_selection=None):
        from pipeline.ablation_config import load_ablation_config
        self.strategy_selection = strategy_selection or load_ablation_config()
        self.strategy_selection.require_executable()
        config = load_refactoring_agent_config(config_path)
        super().__init__(enabled=config["enabled"] if enabled is None else enabled)
        self.config = config
        self.provider = provider or ExistingLLMRefactoringProvider()
        self.test_provider_injected = provider is not None and getattr(provider, "provider_type", None) == "test_double"
        self.prompt_service = prompt_service or RefactoringPromptService()
        self.validator = validator or CandidateValidator()
        self.policy_guard = policy_guard or RefactoringPolicyGuard()
        self.rag_service_explicit = rag_service is not None
        self.rag_service = rag_service or OptionalRagService()
        self.contract_builder = contract_builder or self.strategy_selection.contract_builder()

    @property
    def input_contract(self) -> tuple[str, ...]:
        return ("input/instance.json", "risk/result.json", "rag/retrieved_cases.json")

    @property
    def output_contract(self) -> str:
        return "refactoring/result.json"

    def execute(self, request: dict[str, Any], *, saved_result_path: Path | None = None) -> dict[str, Any]:
        if not self.enabled:
            raise AgentDisabledError("Agent 'refactoring' is disabled and was not executed.")
        self.validate_input(request)
        from pipeline.instances import LegacyInstanceAdapter
        request = {**request, 'input': {**request['input'],
                   'instance': LegacyInstanceAdapter().adapt(request['input']['instance'])}}
        contract = self.contract_builder.build(request)
        source = Path(contract["source_file"]).read_text(encoding="utf-8")
        saved = None
        if saved_result_path is not None:
            import json
            from .refactoring_contract import normalized_source
            saved = json.loads(Path(saved_result_path).read_text(encoding='utf-8'))
            validate_contract(saved, 'agent-result')
            old = saved['result']; previous = old.get('risk_contract') or {}
            prior_instance = previous.get('safety_instance') or {}
            current_instance = request['input']['instance']
            if (saved.get('agent') != self.agent_name
                    or saved.get('instance_id') != request['instance_id']
                    or old.get('decision') not in {'CANDIDATE_GENERATED', 'REFACTORING_REJECTED'}
                    or old.get('provider_type') != self.provider.provider_type
                    or not old.get('raw_response')
                    or previous.get('source_hash') != contract['source_hash']
                    or any(prior_instance.get(k) != current_instance.get(k) for k in
                           ('test_relative_path', 'method_signature', 'smell_type', 'project_version'))
                    or normalized_source(prior_instance.get('test_code') or '') != normalized_source(current_instance['test_code'])):
                raise RefactoringRejected('SAVED_RESPONSE_SOURCE_IDENTITY_MISMATCH')
        # Deterministic test providers must not accidentally reach the real RAG
        # service merely because production RAG is enabled. Tests that exercise
        # retrieval can still opt in by explicitly injecting a RAG service.
        rag_execution_enabled = saved is None and self.config["rag_enabled"] and (
            not self.test_provider_injected or self.rag_service_explicit
        )
        if rag_execution_enabled:
            cases, rag = self.rag_service.retrieve(
                enabled=True, top_k=self.config["rag_top_k"], request=request,
                required=self.config["rag_required"],
                failure_policy=self.config["rag_failure_policy"],
            )
        else:
            status = "DISABLED" if not self.config["rag_enabled"] else "SKIPPED_TEST_PROVIDER"
            cases, rag = [], {"attempted": False, "request_built":False, "retrieval_completed":False,
                              "response_schema_valid":False,"used": False, "status": status,
                              "query_hash": None, "case_ids": [], "retrieved_case_count":0,
                              "top_k": self.config["rag_top_k"],"required":self.config["rag_required"],
                              "fallback_used":False,"blocked":False,"error_type":None,"error_message":None}
        prompt, prompt_hash = self.prompt_service.build(request, contract, request["input"].get("context") or {}, cases)
        metadata = self._metadata(contract, prompt_hash, rag)
        if saved is not None:
            from pipeline.hashing import sha256_file_bytes
            metadata.update(generation_attempts=0, candidate_generation_mode='REPLAY')
            metadata['rag'].update(attempted=False, status='DISABLED')
            contract['saved_response_provenance'] = {
                'source_result': str(Path(saved_result_path).resolve()),
                'source_result_sha256': sha256_file_bytes(saved_result_path),
                'source_run_id': saved['run_id'], 'source_decision': saved['result']['decision'],
                'raw_response_sha256': sha256_text(saved['result']['raw_response']),
                'model_calls': 0, 'rag_calls': 0,
            }
        if rag.get("blocked"):
            result = {
                **metadata, "decision":"REFACTORING_FAILED", "execution_status":"FAILED",
                "risk_contract":contract, "risk_evidence_ids":contract["risk_evidence_ids"],
                "changes":[], "validation_checks":[], "policy_checks":[],
                "source":{"path":contract["source_file"],"sha256":contract["source_hash"]},
                "candidate":{"method_code":None,"file_code":None,"method_sha256":None,"file_sha256":None,"parsed":False},
                "raw_response":"", "prompt":prompt,
                "reason":"RAG is required but unavailable; candidate generation was not attempted.",
                "next_action":"STOP",
                "agent_errors":[{"code":"RAG_REQUIRED_BUT_UNAVAILABLE","message":str(rag.get("error_message") or "RAG unavailable.")}],
            }
            return self._result(request,"FAILED",result,errors=result["agent_errors"])
        try:
            if saved is None:
                response = self.provider.generate(request, contract, {"prompt": prompt})
                self.provider.validate_response(response)
                raw = response.get("content") or ""
            else:
                raw = saved['result']['raw_response']
            validation = self.validator.validate(raw_response=raw, request=request, contract=contract, original_file=source)
            policy_checks = self.policy_guard.apply(request=request, contract=contract, validation=validation, provider_type=self.provider.provider_type, test_provider_injected=self.test_provider_injected)
            accepted = validation["passed"] and self.policy_guard.passed(policy_checks)
            if accepted and contract['smell'] == 'Magic_Number_Test':
                # Candidate-specific data is separate from the immutable Risk proof.
                # Every resume / build / revalidation consumer rechecks it.
                contract['mnt_naming_proposals'] = validation.get('mnt_naming_proposals') or []
                contract['mnt_selection'] = validation.get('mnt_selection')
            decision = "CANDIDATE_GENERATED" if accepted else "REFACTORING_REJECTED"
            comment_review = contract['smell'] == 'Magic_Number_Test' and validation.get('review_required') is True
            if contract['smell'] == 'Magic_Number_Test' and validation.get('mnt_comment_check'):
                contract['mnt_comment_check'] = validation['mnt_comment_check']
            result = {
                **metadata, "decision": decision,
                "execution_status": "COMPLETED",
                "risk_contract": contract,
                "risk_evidence_ids": contract["risk_evidence_ids"],
                "changes": _changes(contract, validation),
                "validation_checks": validation["checks"], "policy_checks": policy_checks,
                "source": {"path": contract["source_file"], "sha256": contract["source_hash"]},
                "candidate": {
                    "method_code": validation.get("candidate_method") if accepted else None,
                    "file_code": validation.get("candidate_file") if accepted else None,
                    "method_sha256": sha256_text(validation["candidate_method"]) if accepted else None,
                    "method_sha256_lf": sha256_utf8_lf(validation["candidate_method"]) if accepted else None,
                    "method_hash_version": METHOD_HASH_VERSION,
                    "source_baseline_hash": contract["source_baseline_hash"],
                    "candidate_original_hash": contract["source_baseline_hash"],
                    "candidate_refactored_hash": sha256_method_text(validation["candidate_method"]) if accepted else None,
                    "file_sha256": sha256_text(validation["candidate_file"]) if accepted else None,
                    "parsed": validation.get("candidate_method") is not None,
                },
                "raw_response": raw,
                "prompt": prompt,
                "reason": "Candidate passed static contract checks." if accepted else
                          "MNT naming or comment information requires manual review." if comment_review else
                          "Candidate violated one or more static contract checks.",
                "next_action": "SAVE_CANDIDATE" if accepted else "MANUAL_REVIEW" if comment_review else "STOP",
                "agent_errors": [],
            }
            return self._result(request, "SUCCESS", result)
        except RefactoringRejected:
            raise
        except Exception as exc:
            diagnostic = safe_exception_diagnostic(exc)
            result = {
                **metadata, "decision": "REFACTORING_FAILED", "execution_status": "FAILED",
                "risk_contract": contract, "risk_evidence_ids": contract["risk_evidence_ids"],
                "changes": [], "validation_checks": [], "policy_checks": [],
                "source": {"path": contract["source_file"], "sha256": contract["source_hash"]},
                "candidate": {"method_code": None, "file_code": None, "method_sha256": None, "file_sha256": None, "parsed": False},
                "raw_response": "", "prompt": prompt,
                "reason": "Candidate provider or internal generation failed.", "next_action": "STOP",
                "agent_errors": [{"code": type(exc).__name__, "message": diagnostic}],
            }
            return self._result(request, "FAILED", result, errors=result["agent_errors"])

    def _metadata(self, contract, prompt_hash, rag):
        return {
            "smell": contract["smell"], "provider_name": self.provider.provider_name,
            "provider_version": self.provider.provider_version, "provider_type": self.provider.provider_type,
            "generation_mode": self.config["mode"], "prompt_version": contract.get('ra_prompt_version') or self.prompt_service.prompt_version,
            "prompt_hash": prompt_hash, "generation_attempts": 1, "rag": rag,
            "candidate_generation_mode": self._candidate_generation_mode(rag),
        }

    def validate_existing_mnt_response(self, request, *, source_result_path: Path):
        """Revalidate a source-bound saved MNT response without any provider/RAG call.

        Reuse is explicit and has zero generation attempts. A previous approval
        (or rejection) is provenance, not authority under the current contract.
        """
        import copy
        import hashlib
        import json
        if not self.enabled:
            raise AgentDisabledError('Refactoring agent is disabled.')
        self.validate_input(request)
        if request['input']['instance']['smell_type'] != 'Magic_Number_Test':
            raise RefactoringRejected('Existing-response entry is limited to MNT.')
        source_result_path = Path(source_result_path).resolve()
        saved = json.loads(source_result_path.read_text(encoding='utf-8'))
        validate_contract(saved, 'agent-result')
        old = saved['result']; instance = request['input']['instance']
        if (saved['agent'] != 'refactoring' or saved['instance_id'] != instance['instance_id']
                or old['risk_contract']['safety_instance']['test_code'] != instance['test_code']):
            raise RefactoringRejected('MNT_REPLAY_SOURCE_IDENTITY_MISMATCH')
        root = source_result_path.parent.parent
        references = [a for a in saved.get('artifacts', []) if a['kind'] == 'raw_response']
        if len(references) != 1:
            raise RefactoringRejected('MNT_REPLAY_RAW_RESPONSE_REFERENCE_REQUIRED')
        response_path = (root / references[0]['path']).resolve()
        if not response_path.is_relative_to(root):
            raise RefactoringRejected('MNT_REPLAY_ARTIFACT_OUTSIDE_OWNER')
        raw_hash = hashlib.sha256(response_path.read_bytes()).hexdigest()
        if raw_hash != references[0]['sha256']:
            raise RefactoringRejected('MNT_REPLAY_RESPONSE_HASH_MISMATCH')
        raw = response_path.read_text(encoding='utf-8')
        contract = self.contract_builder.build(request)
        contract['mnt_replay_provenance'] = {'source_result': str(source_result_path),
            'source_result_sha256': hashlib.sha256(source_result_path.read_bytes()).hexdigest(),
            'raw_response_path': str(response_path), 'raw_response_sha256': raw_hash,
            'source_run_id': saved['run_id'], 'source_decision': old['decision'], 'generation_calls': 0}
        source = Path(contract['source_file']).read_text(encoding='utf-8')
        validation = self.validator.validate(raw_response=raw, request=request, contract=contract, original_file=source)
        policy = self.policy_guard.apply(request=request, contract=contract, validation=validation,
                                        provider_type=old['provider_type'], test_provider_injected=False)
        accepted = validation['passed'] and self.policy_guard.passed(policy)
        review = validation.get('review_required') is True
        contract['mnt_comment_check'] = validation.get('mnt_comment_check')
        if accepted:
            contract['mnt_naming_proposals'] = validation.get('mnt_naming_proposals') or []
            contract['mnt_selection'] = validation.get('mnt_selection')
        method = validation.get('candidate_method') if accepted else None
        file_code = validation.get('candidate_file') if accepted else None
        prompt = 'EXISTING_RESPONSE_REVALIDATION: no generation prompt submitted.'
        result = copy.deepcopy(old)
        result.update(decision='CANDIDATE_GENERATED' if accepted else 'REFACTORING_REJECTED', execution_status='COMPLETED',
            provider_name='existing_mnt_response_revalidation', provider_version='1.0',
            candidate_generation_mode='REPLAY', generation_attempts=0, prompt_version='MNT_REPLAY_V1',
            prompt=prompt, prompt_hash=sha256_text(prompt), raw_response=raw,
            risk_contract=contract, risk_evidence_ids=contract['risk_evidence_ids'], changes=_changes(contract, validation),
            validation_checks=validation['checks'], policy_checks=policy,
            source={'path': contract['source_file'], 'sha256': contract['source_hash']},
            candidate={'method_code': method, 'file_code': file_code, 'method_sha256': sha256_text(method) if method else None,
                'method_sha256_lf': sha256_utf8_lf(method) if method else None, 'method_hash_version': METHOD_HASH_VERSION,
                'source_baseline_hash': contract['source_baseline_hash'], 'candidate_original_hash': contract['source_baseline_hash'],
                'candidate_refactored_hash': sha256_method_text(method) if method else None,
                'file_sha256': sha256_text(file_code) if file_code else None, 'parsed': validation.get('candidate_method') is not None},
            reason='Current MNT static contract passed.' if accepted else 'MNT naming or comment information requires review.' if review else 'Current MNT contract rejected the saved candidate.',
            next_action='SAVE_CANDIDATE' if accepted else 'MANUAL_REVIEW' if review else 'STOP', agent_errors=[])
        result['rag'].update(attempted=False, request_built=False, retrieval_completed=False, response_schema_valid=False,
                             used=False, status='DISABLED', case_ids=[], retrieved_case_count=0, fallback_used=False, blocked=False)
        return self._result(request, 'SUCCESS', result)

    def _candidate_generation_mode(self, rag: dict[str, Any]) -> str:
        if self.test_provider_injected:
            return "REPLAY" if "replay" in str(self.provider.provider_name).lower() else "MOCK"
        if rag.get("status") == "RAG_SUCCEEDED" and rag.get("retrieved_case_count", 0) > 0:
            return "RAG_AUGMENTED_LLM"
        if rag.get("fallback_used"):
            return "LLM_ONLY_FALLBACK"
        return "LLM_ONLY_CONFIGURED"

    def _result(self, request, status, result, errors=None):
        if result["decision"] not in REFACTORING_DECISIONS:
            raise ValueError("Invalid Refactoring Agent decision.")
        envelope = make_agent_result(run_id=request["run_id"], instance_id=request["instance_id"], agent=self.agent_name, agent_version=self.agent_version, status=status, result=result, errors=errors)
        validate_contract(envelope, "agent-result")
        return envelope


def _changes(contract, validation):
    if not validation.get("passed"): return []
    return [{"scope": "METHOD", "smell": contract["smell"], "start_line": contract["method_start_line"], "end_line": contract["method_end_line"], "allowed_changes": contract["allowed_changes"]}]
