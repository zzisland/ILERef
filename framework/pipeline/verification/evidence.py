"""Read-only evidence snapshots; collection never grants final acceptance.

Absent stages stay empty: collecting a bundle does not manufacture a PASS or
replace the existing source/file-chain verification performed by the caller.
"""
from dataclasses import dataclass, field, fields
from pipeline.contracts.unified_instance import freeze


@dataclass(frozen=True)
class ValidationEvidenceBundle:
    route: str
    instance: dict = field(default_factory=dict)
    candidate_validation: dict = field(default_factory=dict)
    file_composition: dict = field(default_factory=dict)
    engineering: dict = field(default_factory=dict)
    detector: dict = field(default_factory=dict)
    revalidation: dict = field(default_factory=dict)
    integrity: dict = field(default_factory=dict)
    policy_checks: list = field(default_factory=list)
    version: str = field(default='1.0', init=False)

    def __post_init__(self):
        for entry in fields(self):
            if entry.name not in {'route', 'version'}:
                object.__setattr__(self, entry.name, freeze(getattr(self, entry.name)))

    @classmethod
    def from_file_gate(cls, instance, gate, *, candidate_validation=None):
        effects = {item.get('instance_id'): item for item in gate.get('instance_revalidation_results') or []}
        effect = effects.get(instance['instance_id'])
        if effect is None:
            effect = {'target_smell_revalidation_status': 'RECHECK_INCONCLUSIVE',
                      'final_acceptance_status': 'MANUAL_REVIEW_REQUIRED', 'reason': 'INSTANCE_RESULT_MISSING'}
        return cls(route='FILE_GATE', instance=instance,
                   candidate_validation=candidate_validation or {},
                   file_composition={key: gate.get(key) for key in
                                     ('file_id', 'manifest_ref', 'candidate_file_ref', 'structure_checks', 'instance_ids')},
                   engineering={key: gate.get(key) for key in
                                ('decision', 'status', 'validation_decision', 'stage_results', 'test_summary')},
                   detector=gate.get('detector_execution') or {}, revalidation=effect,
                   integrity={'hash_chain': gate.get('hash_chain'),
                              'source_unchanged': gate.get('source_unchanged'),
                              'candidate_unchanged': gate.get('candidate_unchanged')})

    @classmethod
    def from_revalidation_checks(cls, *, payload, detector, before, after, comparison,
                                 source_integrity, checks):
        return cls(route='REVALIDATION_CHECKS', instance={'smell_type': payload.get('target_smell')},
                   candidate_validation=payload.get('refactoring_result') or {},
                   engineering=payload.get('validation_result') or {}, detector=detector,
                   revalidation={'before': before, 'after': after, 'comparison': comparison},
                   integrity=source_integrity, policy_checks=checks)

    @classmethod
    def from_instance_results(cls, instance, refactoring, validation, revalidation):
        details = revalidation['result']
        return cls(route='INSTANCE_RESUME', instance=instance, candidate_validation=refactoring,
                   engineering=validation, detector=details.get('detector_execution') or {},
                   revalidation=details, integrity=details.get('source_integrity') or {},
                   policy_checks=details.get('policy_checks') or [])
