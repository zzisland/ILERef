"""Narrow exemptions for unchanged, AST-proven legitimate source regions."""
from collections import Counter

from pipeline.agents.candidate_validation import PLACEHOLDER
from pipeline.agents.safety_contracts import Java, walk

VERSION = 'inherited-source-regions-1'
REGIONS = {'line_comment', 'block_comment', 'string_literal', 'character_literal', 'spread_parameter'}


def file_placeholder_evidence(original: str, candidate: str) -> dict:
    """Retain strict rejection except for unchanged comments/literals/varargs.

    The independent identity, contract and outside-target-diff checks remain
    mandatory. No arbitrary code/identifier match receives an exemption.
    """
    def inventory(source):
        java = Java(source)
        regions = [(java.span(n), n.type, java.text(n)) for n in walk(java.root) if n.type in REGIONS]
        result = []
        for match in PLACEHOLDER.finditer(source):
            enclosing = [r for r in regions if r[0][0] <= match.start() and match.end() <= r[0][1]]
            region = min(enclosing, key=lambda r: r[0][1] - r[0][0]) if enclosing else None
            result.append({'line': source.count('\n', 0, match.start()) + 1, 'marker': match.group(),
                           'region': (region[1], region[2]) if region else None})
        return result

    # Explicit output residue is never exempted, even when already in the input.
    if '```' in candidate or '<PLACEHOLDER>' in candidate:
        return {'passed': False, 'version': VERSION, 'reason': 'EXPLICIT_OUTPUT_RESIDUE', 'inherited': [], 'rejected': []}
    if not PLACEHOLDER.search(candidate):
        return {'passed': True, 'version': VERSION, 'reason': 'NO_PLACEHOLDER_MATCH', 'inherited': [], 'rejected': []}
    try:
        baseline = Counter(x['region'] for x in inventory(original) if x['region'])
        observed = inventory(candidate)
    except ValueError as exc:
        return {'passed': False, 'version': VERSION, 'reason': str(exc), 'inherited': [], 'rejected': []}
    inherited, rejected = [], []
    for item in observed:
        region = item['region']
        if region and baseline[region] > 0:
            baseline[region] -= 1
            inherited.append(item)
        else:
            rejected.append(item)
    return {'passed': not rejected, 'version': VERSION, 'reason': 'UNCHANGED_LEGITIMATE_REGIONS_ONLY' if not rejected else 'UNEXEMPTED_PLACEHOLDER',
            'inherited': inherited, 'rejected': rejected}
