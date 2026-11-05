"""Check local resources and required dependencies without model requests.
Exit 0: checks pass. Exit 1: resource failure. Exit 2: dependencies unavailable.
"""
import ast
import importlib
import importlib.metadata
import importlib.util
import json
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'framework')]
from runtime_support import contained, file_sha, read_json, require, tool_paths, write_json

REQUIRED_PACKAGES = {'PyYAML': 'yaml', 'jsonschema': 'jsonschema',
                     'tree-sitter': 'tree_sitter', 'tree-sitter-java': 'tree_sitter_java',
                     'langchain-openai': 'langchain_openai'}


def inspect_dependencies():
    pins = dict(line.strip().split('==', 1) for line in
                (ROOT / 'requirements.txt').read_text(encoding='utf-8').splitlines()
                if line.strip() and not line.lstrip().startswith('#'))
    require(set(pins) == set(REQUIRED_PACKAGES), 'Dependency check must cover requirements.txt')
    result = {}
    for distribution, module in REQUIRED_PACKAGES.items():
        row = {'distribution': distribution, 'required_version': pins[distribution],
               'installed_version': None, 'available': False, 'status': 'MISSING'}
        try:
            if importlib.util.find_spec(module) is not None:
                importlib.import_module(module)
                row['available'] = True
                row['installed_version'] = importlib.metadata.version(distribution)
                row['status'] = 'PASS' if row['installed_version'] == pins[distribution] else 'VERSION_MISMATCH'
        except Exception as exc:
            row.update(status='IMPORT_ERROR', error_type=type(exc).__name__)
        result[module] = row
    return result


def check():
    errors = []; skipped = []; count = 0; registry = {}; binary = {}
    dependencies = inspect_dependencies()
    tools = tool_paths()
    blocked = [{'dependency': name, 'status': row['status']} for name, row in dependencies.items()
               if row['status'] != 'PASS']
    blocked += [{'tool': name, 'status': 'MISSING'} for name, path in tools.items() if not path]
    if tools['java'] and not (Path(tools['java']).parent / ('javac.exe' if sys.platform == 'win32' else 'javac')).is_file():
        blocked.append({'tool': 'javac', 'status': 'MISSING_FROM_SELECTED_JAVA_HOME'})
    for parent in ['framework', 'scripts', 'tests']:
        for p in (ROOT / parent).rglob('*.py'):
            try:
                tree = ast.parse(p.read_text(encoding='utf-8-sig')); count += 1
                for node in ast.walk(tree):
                    modules = [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 else [a.name for a in node.names] if isinstance(node, ast.Import) else []
                    for module in modules:
                        if module and module.split('.')[0] in {'pipeline', 'refactoring'}:
                            target = ROOT / 'framework' / module.replace('.', '/')
                            if not target.with_suffix('.py').exists() and not target.is_dir():
                                errors.append({'file': str(p.relative_to(ROOT)), 'missing_import': module})
            except (SyntaxError, UnicodeError) as exc:
                errors.append({'file': str(p.relative_to(ROOT)), 'error_type': type(exc).__name__})
    try:
        config = read_json(ROOT / 'config/run_ar.json')
        for key in ['detection', 'provenance', 'model_config', 'detector_jar', 'agent_config']:
            require(contained(ROOT / config[key]).is_file(), 'Missing configured resource: ' + key)
        provenance = read_json(contained(ROOT / config['provenance']))
        delivery = provenance['delivery']
        for key in ['project_archive', 'project_manifest']:
            p = contained(ROOT / 'examples/ar_cli' / delivery[key], ROOT / 'examples/ar_cli')
            require(file_sha(p) == delivery[key + '_sha256'], 'Example hash mismatch: ' + key)
        ET.parse(ROOT / 'framework/pom.xml')
        require(any((ROOT / 'framework/src/main/java').rglob('*.java')), 'Java frontend sources missing')
    except Exception as exc:
        errors.append({'check': 'example_resources', 'error_type': type(exc).__name__, 'reason': str(exc)})
    for name in ['pipeline.contract_registry', 'pipeline.agents.hardened_detector_adapter',
                 'pipeline.agents.risk_agent', 'pipeline.agents.refactoring_agent',
                 'pipeline.verification.file_gate', 'pipeline.ar_module2.detector',
                 'pipeline.verification.acceptance.policy']:
        try:
            module = importlib.import_module(name)
            require(Path(module.__file__).resolve().is_relative_to(ROOT), 'Outside-package import: ' + name)
            if name == 'pipeline.contract_registry':
                registry = module.check_registry()
            elif name == 'pipeline.agents.hardened_detector_adapter':
                binary = module.inspect_detector_binary(ROOT / 'tools/detector/TestSmellDetector.jar').to_dict()
                require(binary['identity_status'] == 'VERIFIED', 'Detector identity not verified')
        except ImportError as exc:
            skipped.append({'check': name, 'missing_dependency': getattr(exc, 'name', None)})
        except Exception as exc:
            errors.append({'check': name, 'error_type': type(exc).__name__, 'reason': str(exc)})
    status = 'FAIL' if errors else 'BLOCKED' if blocked or skipped else 'PASS'
    report = {'status': status, 'resource_validation': 'FAIL' if errors else 'PARTIAL' if skipped else 'PASS',
              'dependency_validation': 'BLOCKED' if blocked or skipped else 'PASS',
              'python_files_parsed': count, 'runtime_contracts_checked': len(registry.get('contracts', [])),
              'detector_identity': binary, 'tools_available': {k: bool(v) for k, v in tools.items()},
              'packages_available': {k: v['available'] for k, v in dependencies.items()},
              'dependencies': dependencies, 'blocked': blocked, 'skipped_checks': skipped, 'errors': errors,
              'scope': 'Local syntax, imports, example resources, contract metadata, detector identity and pinned dependencies. No model request, Maven dependency resolution, compilation or target-test execution.'}
    write_json(ROOT / 'results/checks/package.json', report)
    print(json.dumps(report, indent=2))
    return {'PASS': 0, 'FAIL': 1, 'BLOCKED': 2}[status]


if __name__ == '__main__':
    raise SystemExit(check())
