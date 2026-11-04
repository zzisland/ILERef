"""Two AR metrics; contract compliance never controls independent outcome gates."""
from dataclasses import asdict, replace
from pathlib import Path

from pipeline.ar_independent_static import evaluate as static_outcome, VERSION as STATIC_VERSION
from pipeline.file_candidate import assemble_method_candidates
from pipeline.hashing import sha256_utf8_lf
from pipeline.resume_store import atomic_json, file_hash
from pipeline.verification.file_gate import FileGateExecutor

VERSION = 'ar-rule-only-two-layer-outcome-1'


def range_execution_identity(source_path, instance):
    """Unique source/range/signature/owner identity, including typed overloads.

    A typed overload requires its exact typed fresh XML testcase. Running a
    class, a sibling overload or observing zero tests cannot satisfy it.
    """
    from pipeline.agents.method_location import Java, children, signature
    from pipeline.agents.refactoring_contract import normalized_source
    from pipeline.verification.reintegration.method_inventory import _method_signature
    source = Path(source_path).resolve()
    if source != Path(instance['test_file_path']).resolve():
        raise ValueError('SOURCE_PATH_MISMATCH')
    expected = instance.get('source_byte_sha256') or (instance.get('source_owner_binding') or {}).get('source_file_sha256')
    if not expected or file_hash(source) != expected:
        raise ValueError('SOURCE_HASH_MISMATCH')
    java = Java(source.read_text(encoding='utf-8'))
    span = instance['line_range']
    matches = [n for n in java.nodes('method_declaration')
               if signature(java, n) == instance['method_signature']
               and normalized_source(java.text(n)) == normalized_source(instance['test_code'])
               and (n.start_point.row + 1, n.end_point.row + 1) == (span['start'], span['end'])]
    if len(matches) != 1:
        raise ValueError('RANGE_SIGNATURE_SOURCE_NOT_UNIQUE')
    method = matches[0]
    name = java.text(method.child_by_field_name('name'))
    body = method.parent
    if body.type != 'class_body':
        raise ValueError('LOCAL_METHOD_NOT_SUPPORTED')
    owners, cursor = [], body.parent
    while cursor.type == 'class_declaration':
        owners.append(java.text(cursor.child_by_field_name('name')))
        parent = cursor.parent
        if parent.type == 'program':
            break
        if parent.type != 'class_body' or parent.parent.type != 'class_declaration':
            raise ValueError('LOCAL_CLASS_NOT_SUPPORTED')
        cursor = parent.parent
    if not owners or owners[-1] != Path(instance['test_filename']).stem:
        raise ValueError('OWNER_MISMATCH')
    packages = java.nodes('package_declaration')
    package = java.text(packages[0])[len('package'):].strip().rstrip(';').strip() if packages else ''
    if package != (instance.get('test_package_name') or ''):
        raise ValueError('PACKAGE_MISMATCH')
    owner = '.'.join(filter(None, (package, '$'.join(reversed(owners)))))
    overloads = [n for n in children(body) if n.type == 'method_declaration'
                 and java.text(n.child_by_field_name('name')) == name]
    typed = _method_signature(java, method)
    types = typed[len(name) + 1:-1]
    report_method = (name + '(' + ', '.join(types.split(',')) + ')' if types else name + '()') if len(overloads) > 1 else name
    return owner, report_method


class ARIndependentFileGate(FileGateExecutor):
    """Same production engineering/detector execution; source-only reintegration."""
    version = VERSION

    @staticmethod
    def _execution_identities(source, targets):
        return {t['instance_id']: range_execution_identity(source, t) for t in targets}

    @staticmethod
    def _structure_checks(bundle, candidate_path, targets):
        from pipeline.agents.method_location import Java
        from pipeline.agents.test_method_counter import compare_owned_test_methods
        from pipeline.agents.refactoring_contract import sha256_text
        current = bundle.source_file.read_text(encoding='utf-8')
        candidate = Path(candidate_path).read_text(encoding='utf-8')
        by_id = {t['instance_id']: t for t in targets}
        values = {
            'SOURCE_HASH': sha256_text(current) == bundle.source_hash,
            'CANDIDATE_HASH': sha256_text(candidate) == bundle.candidate_hash,
            'CANDIDATE_BYTE_HASH': file_hash(candidate_path) == bundle.candidate_file_sha256,
            'SOURCE_CANDIDATE_DISTINCT': bundle.source_file.resolve() != Path(candidate_path).resolve(),
            'TARGETS_PRESENT': len(by_id) == len(targets) == len(bundle.methods) == 1
                and set(by_id) == {m['instance_id'] for m in bundle.methods},
        }
        try:
            Java(candidate)
            values['COMPLETE_FILE_AST_PARSE'] = True
            values['EXACT_COMBINED_CANDIDATE_FILE'] = candidate == assemble_method_candidates(bundle.source_file, list(bundle.methods)).candidate_source
            values['SOURCE_ONLY_AR_REPLAY'] = all(
                by_id[m['instance_id']]['smell_type'] == 'Assertion_Roulette'
                and by_id[m['instance_id']]['method_signature'] == m['method_signature']
                and by_id[m['instance_id']]['line_range'] == {'start': m['start_line'], 'end': m['end_line']}
                and static_outcome(by_id[m['instance_id']]['test_code'], m['candidate_method'], current)['static_passed']
                for m in bundle.methods)
            target = targets[0]
            inventory = compare_owned_test_methods(current, candidate, target.get('test_package_name') or '', Path(target['test_filename']).stem)
            values['METHOD_SET_AND_SIGNATURES_PRESERVED'] = inventory['passed'] is True
            range_execution_identity(bundle.source_file, target)
            values['SOURCE_RANGE_OWNER_IDENTITY'] = True
        except (ValueError, KeyError, IndexError):
            values['INDEPENDENT_REINTEGRATION_PROOF'] = False
        return [{'check_id': key, 'status': 'PASS' if ok else 'FAIL', 'severity': 'ERROR', 'message': key}
                for key, ok in values.items()]


def contract_compliance(prepared, raw):
    """Unchanged formal AR validator; evaluated after generation for both arms."""
    import copy
    from pipeline.ablation_runtime import formal_ar_generation_contract
    from pipeline.verification.candidate.validation import CandidateValidator
    try:
        contract = formal_ar_generation_contract(prepared['request'])
        request = copy.deepcopy(prepared['request'])
        request['input']['risk_result'] = {'result': {'evidence': contract['safety_evidence']}}
        validation = CandidateValidator().validate(raw_response=raw, request=request,
                                                  contract=contract, original_file=prepared['original'])
        return {'status': 'PASS' if validation['passed'] else 'FAIL',
                'validator_version': CandidateValidator.validator_version,
                'contract_identity': contract['refactoring_contract'], 'validation': validation}
    except (ValueError, KeyError, TypeError) as exc:
        return {'status': 'INCONCLUSIVE', 'error_type': type(exc).__name__, 'reason': str(exc)}


def summarize_gate(instance, gate):
    """No acceptance from detector labels alone or fabricated absent-stage PASS."""
    from pipeline.verification.engineering.test_reports import execution_confirmed
    from pipeline.verification.acceptance.policy import EvidenceLinkedAcceptancePolicy
    from pipeline.verification.evidence import ValidationEvidenceBundle
    stages = {s['stage']: s['status'] for s in gate.get('stage_results', [])}
    summary = (gate.get('test_summary', {}).get('methods') or {}).get(instance['instance_id'])
    exact = stages.get('TARGET_TEST') == 'PASS' and bool(summary) and execution_confirmed(summary)
    effect = next((x for x in gate.get('instance_revalidation_results', []) if x['instance_id'] == instance['instance_id']), {})
    ar_removed = (effect.get('target_smell_revalidation_status') == 'TARGET_SMELL_REMOVED'
                  and effect.get('final_acceptance_status') == 'ACCEPTED')
    policy = EvidenceLinkedAcceptancePolicy().evaluate(ValidationEvidenceBundle.from_file_gate(instance, gate))
    integrity = (gate.get('hash_chain') or {}).get('status') == 'VERIFIED' and gate.get('source_unchanged') is True and gate.get('candidate_unchanged') is True
    accepted = (stages.get('COMPILE') == 'PASS' and exact and ar_removed and policy.full
                and integrity and gate.get('included_in_statistics') is True)
    return {'compile': stages.get('COMPILE', 'NOT_RUN'),
            'exact_target_test': 'PASS' if exact else stages.get('TARGET_TEST', 'NOT_RUN') if stages.get('TARGET_TEST') != 'PASS' else 'NOT_CONFIRMED',
            'ar_removal': 'PASS' if ar_removed else effect.get('target_smell_revalidation_status', 'NOT_RUN'),
            'integrity_verified': integrity, 'acceptance_evidence': asdict(policy),
            'independent_accepted': bool(accepted)}


def evaluate_generated(prepared, raw, output, project, run_id, gate_executor=None):
    """Layer 1 is informational, never a prerequisite for Layer 2."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    instance = prepared['instance']
    if instance['smell_type'] != 'Assertion_Roulette':
        raise ValueError('AR_ONLY')
    layer1 = contract_compliance(prepared, raw)
    atomic_json(output / 'contract_compliance.json', layer1)
    independent = static_outcome(instance['test_code'], raw, prepared['original'])
    atomic_json(output / 'independent_static.json', independent)
    result = {'version': VERSION, 'static_version': STATIC_VERSION, 'contract_compliance': layer1['status'],
              'structurally_valid': independent['structurally_valid'],
              'assertions_preserved': independent['target_assertions_preserved'],
              'assertion_semantics_preserved': independent['assertion_semantics_preserved'],
              'unrelated_edits_absent': independent['checks'].get('non_assertion_code_unchanged') is True
                  and independent['checks'].get('comments_unchanged') is True,
              'message_added': independent['message_added'],
              'generic_message_quality_pass': independent['generic_message_quality_pass'],
              'independent_static_passed': independent['static_passed'],
              'reintegration': 'NOT_RUN', 'compile': 'NOT_RUN', 'exact_target_test': 'NOT_RUN',
              'ar_removal': 'NOT_RUN', 'independent_accepted': False}
    if not independent['static_passed']:
        result['decision'] = 'INCONCLUSIVE' if independent['inconclusive'] else 'REJECTED'
        atomic_json(output / 'result.json', result)
        return result
    from pipeline.agents.refactoring_contract import sha256_text
    method = independent['candidate_method']
    source = Path(instance['test_file_path'])
    item = dict(instance_id=instance['instance_id'], source_file=str(source),
                source_hash=sha256_text(prepared['original']), start_line=instance['line_range']['start'],
                end_line=instance['line_range']['end'], original_method=instance['test_code'],
                candidate_method=method, candidate_method_sha256_lf=sha256_utf8_lf(method),
                method_signature=instance['method_signature'], smell_type=instance['smell_type'])
    try:
        bundle = assemble_method_candidates(source, [item], project_id=project,
                                           module_path=instance.get('module_path') or '',
                                           source_relative_path=instance['test_relative_path'])
        candidate = output / 'candidate.java'
        candidate.write_text(bundle.candidate_source, encoding='utf-8', newline='\n')
        bundle = replace(bundle, candidate_file_sha256=file_hash(candidate))
        target = {**instance, 'source_byte_sha256': prepared['source_byte_sha256']}
        checks = ARIndependentFileGate._structure_checks(bundle, candidate, [target])
        atomic_json(output / 'reintegration.json', checks)
        result['reintegration'] = 'PASS' if all(c['status'] == 'PASS' for c in checks) else 'FAIL'
        if result['reintegration'] != 'PASS':
            result['decision'] = 'REJECTED'
            atomic_json(output / 'result.json', result)
            return result
        executor = gate_executor or ARIndependentFileGate(repo_root=Path(__file__).resolve().parents[1])
        gate = executor.execute(run_id=run_id, project_name=project, bundle=bundle,
                                targets=[target], before_detection_ref=Path(prepared['context_path']), candidate_path=candidate)
        # Engineering stdout/XML/detector records are preserved in this new namespace.
        for name, text in gate.get('transient_artifacts', {}).items():
            artifact = (output / 'engineering_artifacts' / name).resolve()
            if not artifact.is_relative_to((output / 'engineering_artifacts').resolve()):
                raise ValueError('ARTIFACT_PATH_ESCAPE')
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(text, encoding='utf-8')
        atomic_json(output / 'file_gate.json', gate)
        result.update(summarize_gate(instance, gate))
        result['decision'] = 'ACCEPTED' if result['independent_accepted'] else 'INCONCLUSIVE' if gate.get('validation_decision') == 'VALIDATION_BLOCKED' else 'REJECTED'
    except (ValueError, KeyError, OSError) as exc:
        result.update(decision='INCONCLUSIVE', error_type=type(exc).__name__, reason=str(exc))
    atomic_json(output / 'result.json', result)
    return result
