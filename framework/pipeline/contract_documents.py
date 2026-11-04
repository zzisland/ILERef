"""Offline documentation integrity checks; never used to execute refactoring."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT_RELATIVE = 'analysis/framework_refactor_stage3_20260920'
AUDIT = ROOT / AUDIT_RELATIVE
RULE_LABELS = {
    'applicability_prerequisites': 'Applicability prerequisites',
    'transformation_target': 'Transformation target',
    'edit_and_preservation_constraints': 'Allowed / forbidden edits；protected elements / required preservation',
    'candidate_validation_predicates': 'Candidate validation predicates',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_material(item):
    """Exact Stage 3 prose/templates, with current implementation byte bindings."""
    cid = item['contract_id']
    evidence_dir = AUDIT / 'evidence_after' / cid
    evidence = json.loads((evidence_dir / 'evidence.json').read_text(encoding='utf-8'))
    original = evidence['registry']
    if {k: item.get(k) for k in original} != original:
        raise ValueError('Registry/Stage3 metadata drift: ' + cid)
    report = (AUDIT / 'contract_rules.md').read_text(encoding='utf-8')
    heading = '### ' + cid + ' / ' + item['version'] + '\n'
    if report.count(heading) != 1:
        raise ValueError('Stage3 contract section missing or ambiguous: ' + cid)
    section = report.split(heading, 1)[1].split('\n### ', 1)[0].split('\n## ', 1)[0]
    rules = {}
    for key, label in RULE_LABELS.items():
        match = re.search(r'\*\*' + re.escape(label) + r'\*\*：(.*?)(?:\n\n|$)', section, re.S)
        if not match:
            raise ValueError('Stage3 rule missing: ' + key)
        rules[key] = match.group(1)
    template = (evidence_dir / 'template.txt').read_text(encoding='utf-8')
    if template not in (AUDIT / 'prompt_templates.md').read_text(encoding='utf-8'):
        raise ValueError('Stage3 prompt report/template drift: ' + cid)
    # Authorized prompt-only correction; preserve the original audit as provenance.
    amendment_dir = ROOT / 'analysis/ect_prompt_fix_20260920'
    amendment = json.loads((amendment_dir/'amendment.json').read_text(encoding='utf-8'))
    if amendment.get('revision') != 'ect-fail-prompt-fix-1' or amendment.get('contract_id') != 'ect.must_throw.fail':
        raise ValueError('Unknown prompt audit amendment')
    if sha(amendment_dir/'fail_template.txt') != amendment['template_sha256']:
        raise ValueError('Amended ECT prompt template drift')
    if cid == 'ect.must_throw.fail':
        template = (amendment_dir/'fail_template.txt').read_text(encoding='utf-8')
        rules['candidate_validation_predicates'] = rules['candidate_validation_predicates'].replace(
            '实际 Prompt 错配 flag 段，见 alignment 报告。',
            'Prompt 的 flag 段错配已由 ect-fail-prompt-fix-1 修正；validator 不变。')
    # Preserve the combined approved wording; do not invent finer-grained rules.
    rules['allowed_edits'] = {'source_field': 'transformation_target'}
    for key in ('forbidden_edits', 'protected_elements', 'required_preservation'):
        rules[key] = {'source_field': 'edit_and_preservation_constraints'}
    common = report.split('## 公共链路与版本\n', 1)[1].split('\n## 逐条规则', 1)[0].strip()
    ect = report.split('## ECT 公共边界\n', 1)[1].split('\n## 源码索引', 1)[0].strip() if cid.startswith('ect.') else None
    metadata = {k:v for k,v in item.items() if k != 'manifest'}
    bindings = json.loads((AUDIT/'evidence_after/source_hashes.json').read_text(encoding='utf-8'))
    bindings['pipeline/agents/prompt_service.py'] = amendment['implementation_sha256']
    paths = {
        'pipeline/agents/safety_contracts.py', 'pipeline/agents/ra_contract.py',
        'pipeline/agents/prompt_service.py', 'pipeline/agents/candidate_validation.py',
        'pipeline/agents/refactoring_contract.py', 'pipeline/contract_builders.py',
    }
    for key in ('applicability_entry','transformation_entry','prompt_entry','validator_entry','test_entry'):
        entries = item[key] if isinstance(item[key], list) else [item[key]]
        paths.update(e.split(':')[0].replace('.', '/') + '.py' for e in entries)
    source_hashes = {p:bindings[p] for p in sorted(paths)}
    for p, expected in source_hashes.items():
        actual = sha(ROOT/p)
        if p == 'pipeline/agents/candidate_validation.py':
            from .verification.relocation import verified_original_hash
            actual = verified_original_hash(ROOT/p)
        if actual != expected:
            raise ValueError('Implementation/Stage3 drift requires renewed audit: ' + p)
    doc = {
        'document_schema_version': '1.0', 'execution_authority': 'python',
        **metadata, 'rules': rules, 'common_pipeline_requirements': common,
        'ect_common_restrictions': ect,
        'audit_provenance': {
            'directory': AUDIT_RELATIVE,
            'files_sha256': {name:sha(AUDIT/name) for name in ('contract_rules.md','prompt_templates.md','prompt_validator_alignment.md')},
            'implementation_sha256': source_hashes,
            'prompt_template_sha256': sha(evidence_dir/'template.txt'),
            'prompt_amendment': amendment,
        },
    }
    notes = (AUDIT/'prompt_templates.md').read_text(encoding='utf-8')
    slots = notes.split('## 槽位来源与序列化\n', 1)[1].split('\n## ar.', 1)[0].strip()
    slots = slots.replace('fail 路径亦使用该投影，多个字段为 null。',
                          'fail 路径已独立投影 target_id、exception_type、trigger、message、replacement；无 fail 消息时 message 为 null。')
    suffix = notes.split('## 可选 admission guidance 后缀\n', 1)[1].split('\n## 可选 AR repair', 1)[0].strip()
    prompt = (f'# {cid} / {item["version"]}\n\n'
              '此文件为当前 Python Prompt 的规范快照；runtime 不读取本文件。'
              '`{{expression}}` 是原 Python 表达式占位符，不是新增模板引擎。\n\n'
              '## 最终基础模板\n\n```text\n' + template + '```\n\n'
              '## 槽位与序列化\n\n' + slots + '\n\n'
              '## 可选后缀\n\n' + suffix + '\n\n'
              '## 已知对齐差异\n\n'
              '沿用 Stage 3 prompt_validator_alignment.md，未修正语义：单 Java fence 可清理；'
              'RA catch 允许布局差异；普通 RA/ECT fail 的 AST 检查忽略注释；'
              'ECT fail 的 flag 提示段错配已由 ect-fail-prompt-fix-1 修复，其他行为不变。'
              '具体适用范围以对应 Python validator 为准。\n')
    return doc, prompt


def check_documents(item, directory):
    import yaml
    root = Path(directory).resolve()
    files = []
    for key in ('contract_file', 'prompt_file'):
        value = item.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError('Missing registry document mapping: ' + key)
        path = (root/value).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Contract document escapes registry directory')
        files.append(path)
    expected, prompt = audit_material(item)
    if yaml.safe_load(files[0].read_text(encoding='utf-8')) != expected:
        raise ValueError('Contract/registry/Stage3 audit drift: ' + item['contract_id'])
    if files[1].read_bytes() != prompt.encode('utf-8'):
        raise ValueError('Prompt/Stage3 audit drift: ' + item['contract_id'])
    return files
