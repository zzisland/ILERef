"""Final decisions consume evidence, independently of validation execution."""
from dataclasses import dataclass
from typing import Protocol
from pipeline.validation_bundle import ValidationEvidenceBundle


@dataclass(frozen=True)
class AcceptanceDecision:
    decision: str
    full: bool = False
    partial: bool = False
    status: str | None = None
    category: str | None = None


class AcceptancePolicy(Protocol):
    def evaluate(self, evidence: ValidationEvidenceBundle, *, accept_on_pass=True) -> AcceptanceDecision: ...


class EvidenceLinkedAcceptancePolicy:
    """Full policy, preserving legacy checks and their exact precedence.

    FILE_GATE is called only after the orchestrator's current-file re-audit;
    this policy never substitutes a serialized hash flag for that re-audit.
    """
    version = 'evidence-linked-acceptance-1.0'

    def evaluate(self, evidence, *, accept_on_pass=True):
        if evidence.route == 'DETECTOR_EFFECT':
            # Historical per-instance label is provisional until file re-audit.
            status = evidence.revalidation.get('target_smell_revalidation_status')
            label = {'TARGET_SMELL_REMOVED': 'ACCEPTED',
                     'MNT_PARTIALLY_PROCESSED': 'PARTIALLY_ACCEPTED',
                     'RECHECK_INCONCLUSIVE': 'MANUAL_REVIEW_REQUIRED'}.get(status, 'REJECTED')
            return AcceptanceDecision(label)
        if evidence.route == 'REVALIDATION_CHECKS':
            from pipeline.agents.revalidation_policy import _category
            checks = evidence.policy_checks
            blocked = next((c for c in checks if c['status'] == 'BLOCKED'), None)
            failed = next((c for c in checks if c['status'] == 'FAIL'), None)
            if blocked:
                return AcceptanceDecision('REVALIDATION_BLOCKED', status='REVALIDATION_BLOCKED', category=_category(blocked['check_id']))
            if failed:
                return AcceptanceDecision('REVALIDATION_FAILED', status='REVALIDATION_FAILED', category=_category(failed['check_id']))
            return AcceptanceDecision('REVALIDATION_PASSED', status='REVALIDATION_PASSED')
        if evidence.route == 'INSTANCE_RESUME':
            decision = evidence.revalidation['decision']
            partial = False
            full = False
            if decision == 'REVALIDATION_PASSED' and accept_on_pass:
                numeric = evidence.revalidation.get('comparison', {}).get('numeric_target_effects')
                partial = bool(evidence.instance['smell_type'] == 'Magic_Number_Test' and numeric and not numeric['all_targets_processed'])
                full = not partial
            return AcceptanceDecision(decision, full=full, partial=partial)
        if evidence.route != 'FILE_GATE':
            raise ValueError('Unknown validation evidence route: ' + evidence.route)
        decision = evidence.engineering.get('decision')
        if decision in {'VALIDATION_FAILED', 'VALIDATION_BLOCKED'}:
            return AcceptanceDecision(decision)
        instance, effect = evidence.instance, evidence.revalidation
        target_status = effect.get('target_smell_revalidation_status')
        from pipeline.agents.safety_contracts import selected_numeric_effect_valid
        partial = (instance.get('smell_type') == 'Magic_Number_Test' and target_status == 'MNT_PARTIALLY_PROCESSED'
            and effect.get('final_acceptance_status') == 'PARTIALLY_ACCEPTED'
            and selected_numeric_effect_valid(effect.get('numeric_target_effects'))
            and effect['numeric_target_effects']['all_targets_processed'] is False)
        full = target_status == 'TARGET_SMELL_REMOVED' and effect.get('final_acceptance_status') == 'ACCEPTED'
        if full and instance.get('smell_type') == 'Magic_Number_Test':
            numeric = effect.get('numeric_target_effects') or {}
            full = selected_numeric_effect_valid(numeric) and numeric['all_targets_processed'] and numeric['detector_method_positive'] is False
        decision = ('REVALIDATION_PASSED' if partial or full else
                    'REVALIDATION_FAILED' if target_status in {'TARGET_SMELL_REMAINS', 'NEW_SMELL_INTRODUCED', 'METHOD_COUNT_VIOLATION'}
                    else 'REVALIDATION_BLOCKED')
        return AcceptanceDecision(decision, full=bool(full), partial=bool(partial))


class CompileTestOnlyAcceptancePolicy:
    """Reserved interface only; no compile/test-only acceptance in Full."""
    version = 'compile-test-only-acceptance-reserved'

    def evaluate(self, evidence, *, accept_on_pass=True):
        raise NotImplementedError('Compile/test-only acceptance is reserved for a separately validated ablation')
