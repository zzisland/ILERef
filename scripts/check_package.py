"""Package-local syntax, import targets, executable resources and binary checks."""
import ast
import importlib.util
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'framework')]
from runtime_support import write_json

def check():
    from pipeline.contract_registry import check_registry
    from pipeline.agents.hardened_detector_adapter import inspect_detector_binary
    errors=[];count=0
    for parent in ['framework','scripts','tests']:
        for p in (ROOT/parent).rglob('*.py'):
            tree=ast.parse(p.read_text(encoding='utf-8-sig'));count+=1
            for node in ast.walk(tree):
                modules=[node.module] if isinstance(node,ast.ImportFrom) and node.level==0 else [a.name for a in node.names] if isinstance(node,ast.Import) else []
                for module in modules:
                    if module and module.split('.')[0] in {'pipeline','refactoring'}:
                        target=ROOT/'framework'/module.replace('.','/')
                        if not target.with_suffix('.py').exists() and not target.is_dir():errors.append({'file':str(p.relative_to(ROOT)),'missing_import':module})
    registry=check_registry()
    binary=inspect_detector_binary(ROOT/'tools/detector/TestSmellDetector.jar').to_dict()
    modules=['pipeline.agents.risk_agent','pipeline.agents.refactoring_agent','pipeline.verification.file_gate','pipeline.ar_module2.detector','pipeline.verification.acceptance.policy']
    for name in modules:
        module=__import__(name,fromlist=['*'])
        if not Path(module.__file__).resolve().is_relative_to(ROOT):errors.append({'outside_import':name})
    packages={name:bool(importlib.util.find_spec(name)) for name in ['yaml','jsonschema','tree_sitter','tree_sitter_java','langchain_openai']}
    report={'status':'PASS' if not errors else 'FAIL','python_files_parsed':count,'runtime_contracts_checked':len(registry['contracts']),
            'detector_identity':binary,'packages_available':packages,'errors':errors,
            'scope':'Package-local syntax, imports, contract metadata, rule references, prompt resources and detector identity; not a semantic proof of all contracts.'}
    write_json(ROOT/'results/checks/package.json',report)
    print(json.dumps(report,indent=2))
    return 0 if not errors else 1
if __name__=='__main__':raise SystemExit(check())
