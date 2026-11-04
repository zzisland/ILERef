"""Check packaged contract resources; refactoring decisions remain in Python."""
import re
from pathlib import Path
import yaml

RULE_FIELDS = (
    'applicability_prerequisites', 'transformation_target',
    'edit_and_preservation_constraints', 'candidate_validation_predicates',
)


def check_documents(item, directory):
    root = Path(directory).resolve()
    files = []
    for key in ('contract_file', 'prompt_file'):
        value = item.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError('Missing document mapping: ' + key)
        path = (root / value).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Contract document escapes registry directory')
        if not path.is_file():
            raise ValueError('Missing contract resource: ' + value)
        files.append(path)
    doc = yaml.safe_load(files[0].read_text(encoding='utf-8'))
    if not isinstance(doc, dict) or doc.get('document_schema_version') != '1.0' or doc.get('execution_authority') != 'python':
        raise ValueError('Invalid contract document: ' + item['contract_id'])
    if any(doc.get(key) != value for key, value in item.items() if key != 'manifest'):
        raise ValueError('Contract/registry metadata mismatch: ' + item['contract_id'])
    rules = doc.get('rules')
    if not isinstance(rules, dict) or any(not isinstance(rules.get(key), str) or not rules[key].strip() for key in RULE_FIELDS):
        raise ValueError('Missing contract rules: ' + item['contract_id'])
    for key in ('allowed_edits', 'forbidden_edits', 'protected_elements', 'required_preservation'):
        reference = rules.get(key)
        if not isinstance(reference, dict) or reference.get('source_field') not in RULE_FIELDS:
            raise ValueError('Invalid rule reference: ' + key)
    prompt = files[1].read_text(encoding='utf-8')
    heading = '# ' + item['contract_id'] + ' / ' + item['version']
    if not prompt.startswith(heading + '\n') or not re.search(r'```text\n\S[\s\S]*?\n```', prompt):
        raise ValueError('Missing or mismatched prompt template: ' + item['contract_id'])
    return files
