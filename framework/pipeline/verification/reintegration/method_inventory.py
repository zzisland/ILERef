from __future__ import annotations

from typing import Any

METHOD_INVENTORY_VERSION = '2'
TEST_ANNOTATIONS = {'Test', 'ParameterizedTest', 'RepeatedTest', 'TestFactory', 'TestTemplate'}


def _method_signature(java, method):
    """Source-stable overload identity: name and AST parameter types, not names/layout."""
    from pipeline.agents.safety_contracts import children

    def tokens(node):
        if node is None or node.type in ('line_comment', 'block_comment'):
            return ''
        if not node.children:
            return java.text(node)
        return ''.join(tokens(child) for child in node.children)

    types = []
    for param in children(method.child_by_field_name('parameters')):
        if param.type == 'receiver_parameter':
            continue
        if param.type == 'formal_parameter':
            typ = param.child_by_field_name('type')
            if typ is None:
                raise ValueError('METHOD_INVENTORY_PARAMETER_UNKNOWN')
            types.append(tokens(typ) + tokens(param.child_by_field_name('dimensions')))
        elif param.type == 'spread_parameter':
            typ = next((n for n in children(param) if n.type not in ('modifiers', 'variable_declarator')), None)
            if typ is None:
                raise ValueError('METHOD_INVENTORY_PARAMETER_UNKNOWN')
            types.append(tokens(typ) + '[]')
        else:
            raise ValueError('METHOD_INVENTORY_PARAMETER_UNKNOWN')
    return java.text(method.child_by_field_name('name')) + '(' + ','.join(types) + ')'


def owned_method_inventory(source: str, package: str, class_name: str) -> dict[str, Any]:
    """Inventory every lexical owner in the full file with the shared Java AST.

    Known JUnit annotations identify tests; all other methods are retained in the
    inventory as helpers, so unrecognised test annotations cannot hide a deletion.
    Nested/local/anonymous methods never contribute to the outer test count.
    """
    from pipeline.agents.method_location import Java, children
    java = Java(source)
    packages = java.nodes('package_declaration')
    actual_package = java.text(packages[0])[len('package'):].strip().rstrip(';').strip() if packages else ''
    owners = [n for n in children(java.root) if n.type == 'class_declaration'
              and java.text(n.child_by_field_name('name')) == class_name]
    if package != actual_package or len(owners) != 1:
        raise ValueError('METHOD_INVENTORY_OWNER_MISMATCH')
    counts = dict.fromkeys(('target_class_tests', 'nested_class_tests',
                           'anonymous_class_methods', 'local_class_methods', 'helper_methods', 'other_class_tests'), 0)
    methods = []

    def visit(node, owner, scope):
        for child in children(node):
            if child.type == 'method_declaration':
                modifiers = next((n for n in children(child) if n.type == 'modifiers'), None)
                annotations = sorted(java.text(n.child_by_field_name('name'))
                                     for n in children(modifiers) if n.type in ('annotation', 'marker_annotation')) if modifiers else []
                is_test = any(a.rsplit('.', 1)[-1] in TEST_ANNOTATIONS for a in annotations)
                category = ('anonymous_class_methods' if scope == 'anonymous' else
                            'local_class_methods' if scope == 'local' else
                            'target_class_tests' if is_test and scope == 'target' else
                            'other_class_tests' if is_test and scope == 'other' else
                            'nested_class_tests' if is_test else 'helper_methods')
                counts[category] += 1
                sig = _method_signature(java, child)
                methods.append({'owner': owner, 'scope': scope, 'category': category,
                                'signature': sig, 'annotations': annotations, 'is_test': is_test,
                                'start_line': child.start_point.row + 1, 'end_line': child.end_point.row + 1})
                visit(child, owner + '::' + sig, scope)
            elif child.type in ('class_declaration', 'interface_declaration', 'enum_declaration', 'record_declaration'):
                member = node.type == 'class_body'
                kind = scope if scope in ('local', 'anonymous') else ('nested' if member else 'local')
                visit(child, owner + '$' + java.text(child.child_by_field_name('name')), kind)
            elif child.type == 'object_creation_expression' and any(n.type == 'class_body' for n in children(child)):
                visit(child, owner + '$anonymous', 'anonymous')
            else:
                visit(child, owner, scope)

    # Include sibling top-level owners too: the guard covers the complete file.
    for owner in children(java.root):
        if owner.type in ('class_declaration', 'interface_declaration', 'enum_declaration', 'record_declaration'):
            name = java.text(owner.child_by_field_name('name'))
            visit(owner.child_by_field_name('body'), name, 'target' if owner == owners[0] else 'other')
    return {'version': METHOD_INVENTORY_VERSION, 'package': package, 'class': class_name,
            'source_kind': 'COMPLETE_JAVA_SOURCE_AST',
            'test_method_count': sum(counts[k] for k in ('target_class_tests', 'nested_class_tests', 'other_class_tests')),
            'counts': counts, 'methods': methods}


def compare_owned_test_methods(before: str, after: str, package: str, class_name: str) -> dict[str, Any]:
    from collections import Counter
    result = {'version': METHOD_INVENTORY_VERSION, 'status': 'UNKNOWN', 'passed': False}
    try:
        left = owned_method_inventory(before, package, class_name)
        right = owned_method_inventory(after, package, class_name)
    except ValueError as exc:
        return {**result, 'reason_code': str(exc)}
    def identities(inv, tests_only=False):
        return Counter((inv['package'], m['owner'], m['scope'], m['signature'], tuple(m['annotations']))
                       for m in inv['methods'] if not tests_only or
                       (m['is_test'] and m['scope'] not in ('anonymous', 'local')))
    left_tests, right_tests = identities(left, True), identities(right, True)
    removed, added = left_tests - right_tests, right_tests - left_tests
    tests_same = left_tests == right_tests
    same = identities(left) == identities(right)
    delta = right['test_method_count'] - left['test_method_count']
    reason = ('TEST_METHOD_IDENTITIES_PRESERVED' if same else
              'NON_TEST_METHOD_IDENTITIES_CHANGED' if tests_same else
              'TEST_METHOD_COUNT_DECREASED' if delta < 0 else
              'TEST_METHOD_COUNT_INCREASED' if delta > 0 else
              'METHOD_OWNER_OR_IDENTITY_CHANGED')
    return {**result, 'status': 'PASS' if same else 'FAIL', 'passed': same,
            'reason_code': reason, 'test_method_set_changed': not tests_same,
            'removed_test_identities': [list(item) for item in sorted(removed.elements())],
            'added_test_identities': [list(item) for item in sorted(added.elements())],
            'before': left, 'after': right}


def compare_owned_test_files(source, candidate, package, class_name, *, source_sha256, candidate_sha256):
    from pathlib import Path
    from pipeline.hashing import sha256_file_bytes
    result = {'version': METHOD_INVENTORY_VERSION, 'status': 'UNKNOWN', 'passed': False}
    try:
        if not source_sha256 or not candidate_sha256:
            return {**result, 'reason_code': 'METHOD_INVENTORY_FILE_HASH_MISSING'}
        if sha256_file_bytes(source) != source_sha256 or sha256_file_bytes(candidate) != candidate_sha256:
            return {**result, 'reason_code': 'METHOD_INVENTORY_FILE_HASH_MISMATCH'}
        comparison = compare_owned_test_methods(Path(source).read_text(encoding='utf8'),
                                               Path(candidate).read_text(encoding='utf8'), package, class_name)
        if sha256_file_bytes(source) != source_sha256 or sha256_file_bytes(candidate) != candidate_sha256:
            return {**result, 'reason_code': 'METHOD_INVENTORY_FILE_HASH_MISMATCH'}
        return {**comparison, 'source_sha256': source_sha256, 'candidate_sha256': candidate_sha256}
    except (OSError, UnicodeError) as exc:
        return {**result, 'reason_code': 'METHOD_INVENTORY_SOURCE_UNAVAILABLE', 'error_type': type(exc).__name__}


def count_schema_test_methods(records: list[dict[str, Any]], package: str, class_name: str) -> int:
    """Historical record-count utility only; never use this for acceptance guards."""
    matches = {(r["package"], r["class"], r["method_signature"]) for r in records if r["package"] == package and r["class"] == class_name and r["method_signature"]}
    if not matches:
        raise ValueError("Detector did not expose a reliable test-method count for the target class.")
    return len(matches)
