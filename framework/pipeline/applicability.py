"""Applicability seams; Full delegates to the established instance-level rules."""
from typing import Protocol


class ApplicabilityEvidenceExtractor:
    def __init__(self, extract=None):
        if extract is None:
            from .agents.risk_evidence import extract_rule_evidence
            extract = extract_rule_evidence
        self._extract = extract

    def extract(self, instance, context):
        # Legacy proof annotations include decisions; do not reinterpret them.
        return self._extract(instance, context)


class ApplicabilityPolicy(Protocol):
    def evaluate(self, request, evidence, context) -> dict: ...


class InstanceLevelApplicabilityPolicy:
    version = 'instance-level-applicability-1.0'

    def __init__(self, rule_assessor, policy_guard):
        self.rule_assessor = rule_assessor
        self.policy_guard = policy_guard

    def evaluate(self, request, evidence, context):
        preliminary = self.rule_assessor.assess(request, evidence, context)
        final = self.policy_guard.apply(preliminary, request=request, provider_kind='rule')
        from .agents.safety_contracts import current_rule_version
        final['rule_version'] = current_rule_version(request['input']['instance']['smell_type'])
        return final


class CategoryLevelApplicabilityPolicy:
    """Generation authorization only; never an instance safety approval."""
    version = 'category-admission-1'

    def evaluate(self, request, evidence, context):
        from .ablation_runtime import category_authorization
        return category_authorization(request['input']['instance'])
