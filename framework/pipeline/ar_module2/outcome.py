"""Common two-layer outcome path, with engineering for unresolved API bindings."""
from dataclasses import replace
from pathlib import Path
from pipeline.ar_independent_outcome import range_execution_identity, summarize_gate
from pipeline.verification.file_gate import FileGateExecutor
from pipeline.resume_store import atomic_json, file_hash
from pipeline.hashing import normalize_lf, sha256_utf8_exact
from .output import compose
from .static import evaluate

VERSION = 'ar-module2-two-layer-outcome-1'


def compliance(prepared, raw):
    """Full supplied contract only; Original never invokes applicability/contract.

    Original contract compliance is NOT_APPLICABLE, not a fabricated FAIL.
    This informational metric never controls independent outcome.
    """
    contract = prepared.get('generation_contract')
    if contract is None:
        return {'status': 'NOT_APPLICABLE', 'reason': 'NO_EXECUTABLE_CONTRACT_GENERATION'}
    try:
        from pipeline.verification.candidate.validation import CandidateValidator
        request = {**prepared['request'], 'input': {**prepared['request']['input'],
                   'risk_result': {'result': {'evidence': contract['safety_evidence']}}}}
        value = CandidateValidator().validate(raw_response=raw, request=request,
                                              contract=contract, original_file=prepared['original'])
        return {'status': 'PASS' if value['passed'] else 'FAIL', 'validation': value,
                'contract_identity': contract['refactoring_contract']}
    except Exception as exc:
        return {'status': 'INCONCLUSIVE', 'reason': type(exc).__name__ + ':' + str(exc)}


class Module2FileGate(FileGateExecutor):
    version = VERSION
    from .gate_bridge import execute

    @staticmethod
    def _execution_identities(source, targets):
        return {t['instance_id']: range_execution_identity(source, t) for t in targets}

    @staticmethod
    def _structure_checks(bundle, candidate_path, targets):
        from pipeline.agents.safety_contracts import Java
        from pipeline.agents.test_method_counter import compare_owned_test_methods
        source = normalize_lf(bundle.source_file.read_bytes().decode('utf-8'))
        candidate = Path(candidate_path).read_bytes().decode('utf-8')
        values = {'SOURCE_LOGICAL_HASH': sha256_utf8_exact(source) == bundle.source_hash,
                  'CANDIDATE_HASH': sha256_utf8_exact(candidate) == bundle.candidate_hash,
                  'CANDIDATE_BYTES': file_hash(candidate_path) == bundle.candidate_file_sha256,
                  'DISTINCT_ARTIFACT': bundle.source_file.resolve() != Path(candidate_path).resolve(),
                  'ONE_BOUND_TARGET': len(targets) == len(bundle.methods) == 1
                      and targets[0]['instance_id'] == bundle.methods[0]['instance_id']}
        try:
            Java(candidate)
            values['JAVA_FILE_AST'] = True
            target, method = targets[0], bundle.methods[0]
            replay = compose(bundle.source_file, target, method['candidate_method'], 'replay')
            values['EXACT_SOURCE_RANGE_ASSEMBLY'] = candidate == replay.candidate_source
            values['INDEPENDENT_SOURCE_REPLAY'] = evaluate(target['test_code'], method['candidate_method'], source)['engineering_eligible']
            values['METHOD_SET_AND_SIGNATURES'] = compare_owned_test_methods(
                source, candidate, target.get('test_package_name') or '', Path(target['test_filename']).stem)['passed'] is True
            range_execution_identity(bundle.source_file, target)
            values['SOURCE_OWNER_AND_RANGE'] = True
        except (ValueError, KeyError, IndexError):
            values['REINTEGRATION_PROOF'] = False
        return [{'check_id': name, 'status': 'PASS' if passed else 'FAIL', 'severity': 'ERROR', 'message': name}
                for name, passed in values.items()]


def evaluate_generated(prepared, raw, output, project, run_id, gate_executor=None, baseline_context=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    instance = prepared['instance']
    layer1 = compliance(prepared, raw)
    atomic_json(output / 'contract_compliance.json', layer1)
    static = evaluate(instance['test_code'], raw, prepared['original'])
    atomic_json(output / 'independent_static.json', static)
    result = {k: static[k] for k in ('structurally_valid', 'assertions_preserved', 'assertion_semantics_preserved',
                                   'unrelated_edits_absent', 'message_added', 'all_messages_added',
                                   'generic_message_quality_pass', 'independent_static_passed')}
    result.update(version=VERSION, contract_compliance=layer1['status'], static_status=static['status'],
                  unsupported=static['unsupported'], reintegration='NOT_RUN', compile='NOT_RUN',
                  exact_target_test='NOT_RUN', ar_removal='NOT_RUN', independent_accepted=False,
                  engineering_eligible=static['engineering_eligible'], before_ar=None)
    if not static['engineering_eligible']:
        result['decision'] = 'INCONCLUSIVE' if static['status'] == 'INCONCLUSIVE' else 'REJECTED'
        atomic_json(output / 'result.json', result)
        return result
    try:
        bundle = compose(instance['test_file_path'], instance, static['candidate_method'], project)
        candidate = output / 'candidate.java'
        # UTF-8 LF is the only transport canonicalization, no content repair.
        candidate.write_bytes(bundle.candidate_source.encode('utf-8'))
        bundle = replace(bundle, candidate_file_sha256=file_hash(candidate))
        target = {**instance, 'source_byte_sha256': prepared['source_byte_sha256']}
        checks = Module2FileGate._structure_checks(bundle, candidate, [target])
        atomic_json(output / 'reintegration.json', checks)
        result['reintegration'] = 'PASS' if all(c['status'] == 'PASS' for c in checks) else 'FAIL'
        if result['reintegration'] == 'FAIL':
            result['decision'] = 'REJECTED'
            atomic_json(output / 'result.json', result)
            return result
        if gate_executor is None:
            from .detector import TargetFileDetector
            from pipeline.agents.validation_contract import nearest_maven_root
            source = Path(instance['test_file_path'])
            import hashlib
            runtime_root = Path(__file__).resolve().parents[2] / 'analysis/module2_original_rule_ablation/rt' / hashlib.sha256(str(output.resolve()).encode()).hexdigest()[:12]
            detector = TargetFileDetector(source.relative_to(nearest_maven_root(source)), runtime_root,
                (instance.get('production_context') or {}).get('productionClassPath'))
            executor = Module2FileGate(repo_root=Path(__file__).resolve().parents[2], detector_runner=detector)
        else:
            executor = gate_executor
        gate = executor.execute(run_id=run_id, project_name=project, bundle=bundle, targets=[target],
                                before_detection_ref=Path(baseline_context or prepared['context_path']), candidate_path=candidate)
        for name, text in gate.get('transient_artifacts', {}).items():
            artifact = (output / 'engineering_artifacts' / name).resolve()
            if not artifact.is_relative_to((output / 'engineering_artifacts').resolve()):
                raise ValueError('ENGINEERING_ARTIFACT_ESCAPE')
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(text, encoding='utf-8', newline='\n')
        atomic_json(output / 'file_gate.json', gate)
        result.update(summarize_gate(instance, gate))
        effect = next((e for e in gate.get('instance_revalidation_results', []) if e['instance_id'] == instance['instance_id']), {})
        result['before_ar'] = (None if effect.get('before_record') is None else
                              'Assertion_Roulette' in effect['before_record']['smells'])
        if result['before_ar'] is False:
            result.update(independent_accepted=False, ar_removal='NOT_APPLICABLE_BEFORE_NEGATIVE')
        if static['status'] == 'INCONCLUSIVE':
            result.update(independent_accepted=False, decision='INCONCLUSIVE')
        else:
            result['decision'] = ('ACCEPTED' if result['independent_accepted'] else
                                  'INCONCLUSIVE' if result['before_ar'] is False or gate.get('validation_decision') == 'VALIDATION_BLOCKED'
                                  or effect.get('target_smell_revalidation_status') in ('RECHECK_INCONCLUSIVE', 'RECHECK_BLOCKED') else 'REJECTED')
    except (ValueError, KeyError, OSError) as exc:
        result.update(decision='INCONCLUSIVE', reason=type(exc).__name__ + ':' + str(exc))
    atomic_json(output / 'result.json', result)
    return result
