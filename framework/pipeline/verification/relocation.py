"""Offline source-provenance verification for the Stage 5 code move."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT/'analysis/framework_refactor_stage5_20260920/relocation_manifest.json'


def verified_original_hash(path):
    """Validate moved implementations, then return the audited pre-move hash.

    This is for document provenance only, never for runtime cache identities.
    Runtime identities must include the new implementation files themselves.
    """
    key = str(Path(path).resolve().relative_to(ROOT)).replace('\\', '/')
    data = json.loads(MANIFEST.read_text(encoding='utf-8'))
    entry = data['entries'].get(key)
    if entry is None:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    for name, expected in data['dependencies'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != expected:
            raise ValueError('Verification implementation relocation drift: ' + name)
    # Do not let an altered compatibility file hide behind the moved source.
    module = entry['implementation'][:-3].replace('/', '.')
    import ast
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    actual = next((n.value.value for n in tree.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == '_VERIFICATION_MODULE' for t in n.targets)
                   and isinstance(n.value, ast.Constant)), None)
    if actual != module:
        raise ValueError('Verification compatibility mapping drift: ' + key)
    return entry['original_sha256']
