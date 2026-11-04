"""Source-owned detector identities; split indices are not method identities."""
from pathlib import Path

from pipeline.agents.safety_contracts import Java, children, ast_node_tokens
from pipeline.agents.method_location import signature, single_method
from pipeline.verification.reintegration.method_inventory import TEST_ANNOTATIONS, _method_signature

VERSION = 'detector-owned-ast-2'


def declared_methods(java, owner):
    parts = owner.replace('$', '.').split('.')
    scope = java.root
    for name in parts:
        owners = [n for n in children(scope) if n.type == 'class_declaration'
                  and java.text(n.child_by_field_name('name')) == name]
        if len(owners) != 1:
            raise ValueError('TARGET_METHOD_ATTRIBUTION_UNCERTAIN:OWNER')
        scope = owners[0].child_by_field_name('body')
    return [n for n in children(scope) if n.type == 'method_declaration']


def package(java):
    nodes = java.nodes('package_declaration')
    return java.text(nodes[0])[len('package'):].strip().rstrip(';').strip() if nodes else ''


def method_key(source):
    java, method = single_method('void ' + source + ' {}')
    return _method_signature(java, method)


def identify(code, owner='', expected_package=None, hint=None, candidate=None):
    java = Java(code)
    roots = children(java.root)
    if len(roots) == 1 and roots[0].type == 'method_declaration':
        java, method = single_method(code)
    else:
        if expected_package is not None and package(java) != expected_package:
            raise ValueError('TARGET_METHOD_ATTRIBUTION_UNCERTAIN:PACKAGE')
        methods = declared_methods(java, owner)
        tests = []
        for node in methods:
            modifiers = next((n for n in children(node) if n.type == 'modifiers'), None)
            annotations = [java.text(n.child_by_field_name('name')).rsplit('.', 1)[-1]
                           for n in children(modifiers) if n.type in ('annotation', 'marker_annotation')] if modifiers else []
            if any(name in TEST_ANNOTATIONS for name in annotations):
                tests.append(node)
        if not tests:
            return None  # helper-only shell never supplies a test observation
        if len(tests) != 1:
            raise ValueError('TARGET_METHOD_ATTRIBUTION_UNCERTAIN:MULTIPLE_TESTS')
        method = tests[0]
    key = _method_signature(java, method)
    if hint and method_key(hint) != key:
        raise ValueError('TARGET_METHOD_ATTRIBUTION_UNCERTAIN:HINT_MISMATCH')
    proof = {'version': VERSION, 'owner': owner, 'typed_signature': key}
    if candidate is not None:
        full = Java(candidate)
        if expected_package is not None and package(full) != expected_package:
            raise ValueError('TARGET_METHOD_ATTRIBUTION_UNCERTAIN:SOURCE_PACKAGE')
        matches = [n for n in declared_methods(full, owner) if _method_signature(full, n) == key
                   and ast_node_tokens(full, n) == ast_node_tokens(java, method)]
        if len(matches) != 1:
            raise ValueError('TARGET_METHOD_ATTRIBUTION_UNCERTAIN:SOURCE_BINDING')
        import hashlib
        proof['candidate_text_sha256'] = hashlib.sha256(candidate.encode('utf-8')).hexdigest()
        proof['method_token_sha256'] = hashlib.sha256(repr(ast_node_tokens(java, method)).encode('utf-8')).hexdigest()
    return signature(java, method), proof
