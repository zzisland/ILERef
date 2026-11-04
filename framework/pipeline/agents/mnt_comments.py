"""Bounded comment-information checks for MNT, not a natural-language solver.

Executable AST equivalence is a prerequisite. Formatting, a small nonsemantic
prose wrapper, and relocation around the SAME bound statement can be proved.
Other prose equivalence remains unknown. Added text never becomes naming proof.
"""
from __future__ import annotations
import re

COMMENT_RULE_VERSION = '1.0'
PROTECTED = re.compile(
    r'(?i)@(?:formatter|test-|utrefactor|SuppressWarnings|Generated)|'
    r'\b(?:CHECKSTYLE|NOSONAR|NOPMD|noinspection|spotless|spotbugs|findbugs|'
    r'cspell|DO\s+NOT\s+EDIT|CONTRACT[-_]|evidence_id)\b|'
    r'\b(?:Code\s+generated|Generated\s+(?:by|code|file)|Generated:)|'
    r'(?m:^\s*(?://|/\*|\*)?\s*#?(?:end)?region\b)|\blanguage\s*=')
CRITICAL = re.compile(r'(?i)\b(?:bug|issue|ticket|regression|specification|spec|boundary|'
                      r'edge|maximum|minimum|inclusive|exclusive)\b|'
                      r'\b[A-Z][A-Z0-9]+-\d+\b|#\d+|https?://')


def information(text):
    """Ordered tokens: no bag-of-words, synonym inference, or number masking."""
    if text.startswith('//'):
        text = text[2:]
    elif text.startswith('/*'):
        text = text[2:-2]
        text = re.sub(r'(?m)^\s*\*(?:\s|$)', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'^(?:Note|NOTE):\s*', '', text)
    # A terminal prose period is not a comparison operator or decimal point.
    text = re.sub(r'(?<=[A-Za-z])\.$', '', text)
    return tuple(re.findall(r'\w+(?:[-.]\w+)*|>=|<=|!=|==|[^\w\s]', text))


def _inventory(source):
    from .safety_contracts import Java, walk, COMMENTS
    java = Java(source)
    leaves = []
    def visit(n):
        if n.type in COMMENTS: return
        if not n.children: leaves.append(n)
        else:
            for child in n.children: visit(child)
    visit(java.root)
    def ordinal(n):
        return (sum(l.end_byte <= n.start_byte for l in leaves),
                sum(l.end_byte <= n.end_byte for l in leaves), n.type)
    def anchor(n):
        siblings = [c for c in n.parent.named_children if c.type not in COMMENTS]
        previous = [c for c in siblings if c.end_byte <= n.start_byte]
        following = [c for c in siblings if c.start_byte >= n.end_byte]
        if previous and previous[-1].end_point.row == n.start_point.row:
            return previous[-1]
        return following[0] if following else previous[-1] if previous else n.parent
    comments = []
    for n in walk(java.root):
        if n.type not in COMMENTS: continue
        text = java.text(n)
        statement = anchor(n)
        # Only original unchanged plain literals at this same statement may
        # establish redundancy. Candidate-added prose is never a source.
        literals = [information(java.text(l)[1:-1]) for l in walk(statement)
                    if l.type == 'string_literal' and re.fullmatch(r'"[^"\\\r\n]*"', java.text(l))]
        comments.append({'text': text, 'tokens': information(text), 'anchor': ordinal(statement),
            'position': sum(l.end_byte <= n.start_byte for l in leaves),
            'start': java.span(n)[0], 'end': java.span(n)[1], 'kind': n.type,
            'protected': bool(PROTECTED.search(text)), 'critical': bool(CRITICAL.search(text)),
            'original_literals': literals})
    return comments


def check_comments(baseline, candidate):
    """baseline is the exact executable contract, carrying ORIGINAL comments."""
    from .safety_contracts import ast_tokens, digest
    result = {'rule_version': COMMENT_RULE_VERSION, 'decision': 'ALLOW', 'reason_code': 'MNT_COMMENT_INFORMATION_PRESERVED',
              'baseline_hash': digest(baseline), 'candidate_hash': digest(candidate),
              'naming_evidence_source': 'ORIGINAL_SOURCE_ONLY', 'changes': []}
    def change(decision, reason, old=None, new=None):
        def ref(item):
            return {k: item[k] for k in ('text','start','end','anchor','protected','critical')} if item else None
        result['changes'].append({'decision': decision, 'reason_code': reason, 'before': ref(old), 'after': ref(new)})
        rank = {'ALLOW': 0, 'REVIEW': 1, 'REJECT': 2}
        if rank[decision] > rank[result['decision']]:
            result.update(decision=decision, reason_code=reason)
    try:
        if ast_tokens(baseline) != ast_tokens(candidate):
            change('REJECT', 'MNT_COMMENT_EXECUTABLE_CONTEXT_CHANGED')
            return result
        before, after = _inventory(baseline), _inventory(candidate)
    except ValueError:
        change('REJECT', 'MNT_COMMENT_AST_UNAVAILABLE')
        return result
    used_old, used_new = set(), set()
    # Tool directives retain their syntax, kind, order and exact token boundary.
    old_protected = [(i, c) for i, c in enumerate(before) if c['protected']]
    new_protected = [(i, c) for i, c in enumerate(after) if c['protected']]
    def protected_key(c):
        return (c['kind'], c['text'].replace('\r\n','\n').rstrip(), c['position'])
    protected_unchanged = [protected_key(c) for _, c in old_protected] == [protected_key(c) for _, c in new_protected]
    result['protected_markers'] = {'before': len(old_protected), 'after': len(new_protected), 'unchanged': protected_unchanged}
    if not protected_unchanged:
        change('REJECT', 'MNT_COMMENT_PROTECTED_MARKER_CHANGED')
    used_old.update(i for i, _ in old_protected); used_new.update(i for i, _ in new_protected)
    for i, old in enumerate(before):
        if i in used_old: continue
        matches = [(j, new) for j, new in enumerate(after) if j not in used_new
                   and old['tokens'] == new['tokens'] and old['anchor'] == new['anchor']]
        if matches:
            j, new = matches[0]; used_old.add(i); used_new.add(j)
            change('ALLOW', 'MNT_COMMENT_EQUIVALENT_AT_SAME_STATEMENT', old, new)
    for i, old in enumerate(before):
        if i in used_old: continue
        # A duplicate is redundant only if another original, retained copy or
        # original literal carries identical ordered information at this anchor.
        retained = any(j in used_old and other['anchor'] == old['anchor'] and other['tokens'] == old['tokens']
                       for j, other in enumerate(before))
        if retained or (old['tokens'] and old['tokens'] in old['original_literals']):
            used_old.add(i); change('ALLOW', 'MNT_COMMENT_REDUNDANT_INFORMATION_RETAINED', old)
            continue
        matches = [(j, new) for j, new in enumerate(after) if j not in used_new and new['tokens'] == old['tokens']]
        if matches:
            j, new = matches[0]; used_old.add(i); used_new.add(j)
            change('REVIEW', 'MNT_COMMENT_MOVED_ASSOCIATION_UNPROVEN', old, new)
            continue
        matches = [(j, new) for j, new in enumerate(after) if j not in used_new and new['anchor'] == old['anchor']]
        new = matches[0][1] if matches else None
        used_old.add(i)
        if matches: used_new.add(matches[0][0])
        def facts(tokens):
            return [t for t in tokens if re.search(r'\d', t) or t.lower() in {'not','no','never', 'inclusive', 'exclusive'}
                    or t in {'<','>','<=','>=','!=','=='}]
        if old['critical'] and (new is None or any(t not in new['tokens'] for t in facts(old['tokens']))):
            change('REJECT', 'MNT_COMMENT_CRITICAL_INFORMATION_LOST', old, new)
        elif new:
            extended_claim = len(new['tokens']) > len(old['tokens']) and new['tokens'][:len(old['tokens'])] == old['tokens']
            if facts(old['tokens']) != facts(new['tokens']) or extended_claim:
                change('REJECT', 'MNT_COMMENT_UNSUPPORTED_FACT_CHANGE', old, new)
            else:
                change('REVIEW', 'MNT_COMMENT_REWRITE_EQUIVALENCE_UNPROVEN', old, new)
        else:
            change('REVIEW', 'MNT_COMMENT_INFORMATION_RETENTION_UNPROVEN', old)
    for j, new in enumerate(after):
        if j in used_new: continue
        original_copy = any(old['tokens'] == new['tokens'] and old['anchor'] == new['anchor']
                            for old in before if not old['protected'])
        if original_copy:
            change('ALLOW', 'MNT_COMMENT_DUPLICATE_ORIGINAL_INFORMATION', None, new)
        else:
            change('REJECT', 'MNT_COMMENT_UNGROUNDED_ADDITION', None, new)
    return result


def request_comment_check(original, candidate, request):
    """Rebuild from current proof; never trust a saved comment verdict."""
    from .safety_contracts import verify_proof, get_proof, expected_mnt
    from .mnt_naming import materialize_names
    payload = request['input']; context = payload.get('context') or {}
    evidence = payload['risk_result']['result']['evidence']
    valid, reason = verify_proof(evidence, payload['instance'], context)
    if not valid:
        return {'decision': 'REJECT', 'reason_code': reason, 'changes': [], 'rule_version': COMMENT_RULE_VERSION}
    try:
        # Only a comment comparison here: an unresolved name is never approval.
        # The mandatory naming and exact-contract checks remain independently false.
        proof = materialize_names(get_proof(evidence), payload.get('mnt_naming_proposals'), original, context.get('test_class_source') or '', _collect_review=True)
        return check_comments(expected_mnt(original, proof), candidate)
    except (ValueError, TypeError, KeyError) as exc:
        return {'decision': 'REJECT', 'reason_code': str(exc), 'changes': [], 'rule_version': COMMENT_RULE_VERSION}
