"""AST-owned split identity; CSV smell vectors are never synthesized."""
from pipeline.agents.safety_contracts import Java, children
from pipeline.agents.method_location import signature, single_method
from pipeline.verification.reintegration.method_inventory import TEST_ANNOTATIONS
from pipeline.verification.outcome.results import load_phase1_detection_output as legacy_load

VERSION = 'ar-module2-detector-owned-ast-1'


def correct(records):
    values = []
    for record in records:
        java = Java(record['test_code'])
        owners = [n for n in children(java.root) if n.type == 'class_declaration'
                  and java.text(n.child_by_field_name('name')) == record['class']]
        if not owners:
            # Synthetic offline detector fixtures may emit a method, not a class.
            java, method = single_method(record['test_code'])
            matches = [method]
        elif len(owners) == 1:
            matches = []
            for node in children(owners[0].child_by_field_name('body')):
                if node.type != 'method_declaration':
                    continue
                modifiers = next((n for n in children(node) if n.type == 'modifiers'), None)
                names = [java.text(n.child_by_field_name('name')).rsplit('.', 1)[-1]
                         for n in children(modifiers) if n.type in ('annotation', 'marker_annotation')] if modifiers else []
                if any(name in TEST_ANNOTATIONS for name in names):
                    matches.append(node)
        else:
            matches = []
        if not matches and not record['smells']:
            # Splitter can emit a helper-only / fully commented-out test shell.
            # Keep the raw CSV archived; it supplies no test-method observation.
            continue
        if len(matches) != 1:
            raise ValueError('DETECTOR_OWNED_TEST_IDENTITY_INCONCLUSIVE:' + record['split_filename'])
        sig = signature(java, matches[0])
        identity = {**record['canonical_identity'], 'complete_method_signature': sig}
        values.append({**record, 'method_signature': sig, 'method': sig.split('(', 1)[0],
                       'canonical_identity': identity, 'identity_completion_source': VERSION})
    return values


def load_phase1_detection_output(directory, payload):
    # Do not trust historic split-number => target hints. Derive actual AST identity.
    payload = {k: v for k, v in payload.items() if k != 'expected_methods'}
    records, csv = legacy_load(directory, payload)
    return correct(records), csv
