from __future__ import annotations

from pipeline.instances.legacy_adapter import LegacyInstanceAdapter

from typing import Any

from pipeline.contracts import make_agent_result, validate_contract, UnifiedRefactoringInstance

from .base import AgentDisabledError, BaseAgent, load_risk_agent_config
from .risk_assessors import LLMRiskAssessor, RuleBasedRiskAssessor
from .risk_evidence import extract_rule_evidence
from .risk_policy import RiskPolicyGuard
from pipeline.applicability import ApplicabilityEvidenceExtractor, InstanceLevelApplicabilityPolicy


class RiskAssessmentAgent(BaseAgent):
    """Rule-first risk evaluation for one in-scope smell instance."""

    agent_name = "risk_assessment"
    agent_version = "3.0"

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        mode: str | None = None,
        rule_assessor: RuleBasedRiskAssessor | None = None,
        llm_assessor: LLMRiskAssessor | None = None,
        policy_guard: RiskPolicyGuard | None = None,
        config_path=None,
        budget_path=None,
        evidence_extractor=None,
        applicability_policy=None,
        strategy_selection=None,
    ):
        from pipeline.ablation_config import load_ablation_config
        self.strategy_selection = strategy_selection or load_ablation_config()
        self.strategy_selection.require_executable()
        config = load_risk_agent_config(config_path)
        super().__init__(enabled=config["enabled"] if enabled is None else enabled)
        self.mode = mode or config["mode"]
        if self.mode not in {'rule_only', 'rule_llm'}:
            raise ValueError('RiskAssessmentAgent supports rule_only and rule_llm.')
        self.rule_assessor = rule_assessor or RuleBasedRiskAssessor()
        from .decision_support import DecisionBudget, load_decision_settings
        self.llm_assessor = llm_assessor or LLMRiskAssessor(budget=DecisionBudget(
            load_decision_settings()['max_requests_per_run'], budget_path))
        self.policy_guard = policy_guard or RiskPolicyGuard()
        self.evidence_extractor = evidence_extractor or ApplicabilityEvidenceExtractor(extract_rule_evidence)
        self.applicability_policy = applicability_policy or self.strategy_selection.applicability_policy(self.rule_assessor, self.policy_guard)

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise AgentDisabledError(f"Agent '{self.agent_name}' is disabled and was not executed.")
        self.validate_input(request)
        instance = request["input"].get("instance")
        if not isinstance(instance, dict):
            raise ValueError("Risk request input.instance must be a pipeline instance object.")
        instance = LegacyInstanceAdapter().adapt(instance)
        validate_contract(instance, "pipeline-instance")
        request = {**request, 'input': {**request['input'], 'instance': instance}}
        context = request["input"].get("context") or {}
        if not isinstance(context, dict):
            raise ValueError("Risk request input.context must be an object.")
        evidence = self.evidence_extractor.extract(instance, context)
        final = self.applicability_policy.evaluate(request, evidence, context)
        status = 'SUCCESS'
        errors = []
        if self.mode == 'rule_llm':
            from .decision_support import arbitrate
            from pipeline.model_config import safe_failure
            try:
                opinion = self.llm_assessor.assess(request, evidence, {**context, 'rule_result': final})
                final = arbitrate(final, opinion, request, context)
            except Exception as exc:
                opinion = {'status': 'TECHNICAL_FAILURE', 'requests': 0, 'advice': None,
                           'error_type': type(exc).__name__, 'error': safe_failure(exc)}
                final = arbitrate(final, opinion, request, context)
            if opinion.get('status') != 'SUCCESS':
                status = 'FAILED'
                errors = [{'code': 'DECISION_TECHNICAL_FAILURE', 'message': opinion.get('error', 'Decision model failed.')}]
        self.rule_assessor.validate_result(final)
        result = make_agent_result(
            run_id=request["run_id"],
            instance_id=request["instance_id"],
            agent=self.agent_name,
            agent_version=self.agent_version,
            status=status,
            result=final,
            errors=errors,
        )
        validate_contract(result, "agent-result")
        return result

    @property
    def input_contract(self) -> tuple[str, ...]:
        return ("input/instance.json",)

    @property
    def output_contract(self) -> str:
        return "risk/result.json"

    @property
    def allowed_actions(self) -> tuple[str, ...]:
        return ("REFACTOR", "REVIEW", "SKIP", "REMOVE")
