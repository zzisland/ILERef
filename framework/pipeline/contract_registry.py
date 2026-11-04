"""Traceability registry, not a replacement for executable Python rules."""
import ast
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / 'refactoring/contracts/registry.yaml'
REQUIRED = ('contract_id', 'issue_type', 'version', 'status', 'applicability_entry',
            'contract_builder_entry', 'transformation_entry', 'prompt_entry', 'validator_entry', 'test_entry')
ENTRIES = ('applicability_entry', 'contract_builder_entry', 'transformation_entry', 'prompt_entry', 'validator_entry', 'test_entry')


def load_registry(path=REGISTRY):
    value = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('registry_version') != '1.0' or not isinstance(value.get('contracts'), list):
        raise ValueError('Invalid contract registry')
    return value


def resolve_entry(entry, _visited=None):
    """Inspect entry existence without importing/executing model or RAG modules."""
    module, separator, symbol = entry.partition(':')
    if not separator or not symbol or any(not p.isidentifier() for p in module.split('.')):
        raise ValueError('Invalid implementation entry: ' + entry)
    path = ROOT.joinpath(*module.split('.')).with_suffix('.py')
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    # Resolve the explicit legacy import alias without importing any agents.
    target = next((n.value.value for n in tree.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == '_VERIFICATION_MODULE' for t in n.targets)
                   and isinstance(n.value, ast.Constant)), None)
    if target is not None:
        visited = set() if _visited is None else _visited
        if entry in visited or not isinstance(target, str) or not target.startswith('pipeline.verification.'):
            raise ValueError('Invalid or cyclic verification entry alias: ' + entry)
        return resolve_entry(target + ':' + symbol, visited | {entry})
    node = tree
    for part in symbol.split('.'):
        found = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == part]
        if not found:
            raise ValueError('Missing implementation entry: ' + entry)
        node = found[0]
    return path, node


def check_registry(path=REGISTRY):
    registry = load_registry(path)
    ids = set()
    manifests = set()
    documents = set()
    for item in registry['contracts']:
        if any(not item.get(key) for key in REQUIRED):
            raise ValueError('Missing required registry field: ' + str(item.get('contract_id')))
        if item['contract_id'] in ids:
            raise ValueError('Duplicate contract_id: ' + item['contract_id'])
        ids.add(item['contract_id'])
        if not isinstance(item['version'], str) or not item['version'].strip():
            raise ValueError('Contract version must be a nonempty string')
        if item['status'] != 'active' or item.get('scope') not in {'general', 'project_specific'}:
            raise ValueError('Invalid contract status/scope')
        manifest = (Path(path).parent / item['manifest']).resolve()
        if not manifest.is_relative_to(Path(path).parent.resolve()):
            raise ValueError('Manifest escapes registry directory')
        if manifest in manifests:
            raise ValueError('Manifest registered more than once')
        manifests.add(manifest)
        content = yaml.safe_load(manifest.read_text(encoding='utf-8'))
        if content != {k:v for k,v in item.items() if k != 'manifest'}:
            raise ValueError('Registry/manifest mismatch: ' + item['contract_id'])
        for key in ENTRIES:
            values = item[key] if isinstance(item[key], list) else [item[key]]
            for entry in values:
                _, node = resolve_entry(entry)
                if key == 'test_entry' and not (getattr(node, 'name', '').startswith('test_') or
                       isinstance(node, ast.ClassDef) and any(isinstance(n, ast.FunctionDef) and n.name.startswith('test_') for n in node.body)):
                    raise ValueError('test_entry must identify actual tests')
        if item.get('scope') == 'project_specific' and not item.get('project'):
            raise ValueError('Project-specific contract needs project provenance')
        from .contract_documents import check_documents
        for document in check_documents(item, Path(path).parent):
            if document in documents or document in manifests:
                raise ValueError('Contract document registered more than once')
            documents.add(document)
    discovered = {p.resolve() for p in Path(path).parent.rglob('*.yaml') if p.resolve() != Path(path).resolve()}
    if discovered != manifests | {p for p in documents if p.suffix == '.yaml'}:
        raise ValueError('Unregistered or missing manifest')
    return registry


def contract_for(smell, proof):
    """Describe the already-approved path; never use registry to grant admission."""
    if smell == 'Assertion_Roulette':
        key = 'ar.grounded_message'
    elif smell == 'Redundant_Assertion':
        key = 'ra.jfreechart.catch_constant_deletion' if proof.get('ra_transform') == 'CATCH_CONSTANT_DELETION' else 'ra.ordinary_redundant_assertion'
    elif smell == 'Exception_Catching_Throwing':
        if proof.get('ect_path') == 'NO_THROW_ASSERT_DOES_NOT_THROW':
            key = 'ect.no_throw'
        elif proof.get('ect_path') == 'MUST_THROW_ASSERT_THROWS' and proof.get('ect_path_contract_version') in {'1.0', '2.0'}:
            key = 'ect.must_throw.flag' if proof.get('ect_path_contract_version') == '2.0' else 'ect.must_throw.fail'
        else:
            raise ValueError('Unregistered ECT path contract')
    else:
        return None
    matches = [item for item in load_registry()['contracts'] if item['contract_id'] == key]
    if len(matches) != 1:
        raise ValueError('Missing or ambiguous contract: ' + key)
    return {'contract_id': key, 'version': matches[0]['version']}


if __name__ == '__main__':
    value = check_registry()
    print('Registry OK:', len(value['contracts']), 'contracts; no executable rules changed')
