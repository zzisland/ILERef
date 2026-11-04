"""Source-grounded MNT naming proposals, independent of model self-assessment.

This is deliberately not a general semantic parser. It consumes the existing
Java AST and declaration resolver's evidence. A source-bound preparation trace
permits an attempt, not acceptance. The candidate validator separately checks
names and information gain. Quoted intent and bound fixture cardinalities are
provable subcases; arbitrary paraphrase remains review, never model-certified.
"""
from __future__ import annotations

import copy
import json
import re

GENERIC_WORDS = {'expected','actual','value','values','number','magic','test','check','assert','result',
                 'the','a','an','of','for','in','is','this','that','count','get','reading','fixture','token'}
NAME_GLUE = {'expected','for','of','the','a','an'}
PROSE_FRAMING = {'this','test','case','original','context','describes'}
REVIEW_REASONS = {'MNT_NAME_MEANING_REQUIRES_REVIEW','MNT_NAME_BENEFIT_UNCONFIRMED',
                  'MNT_EXPLANATION_REQUIRES_REVIEW','MNT_CONTEXT_ONLY_BENEFIT_UNCONFIRMED'}
CLAIM_WORDS = {'second','seconds','millisecond','milliseconds','minute','minutes','hour','hours',
               'byte','bytes','kilogram','kilograms','meter','meters','timeout','currency','dollar','dollars',
               'price','cost','business','production','guaranteed'}
JAVA_RESERVED = set(('abstract assert boolean break byte case catch char class const continue default do double '
    'else enum extends final finally float for goto if implements import instanceof int interface long native new '
    'package private protected public return short static strictfp super switch synchronized this throw throws '
    'transient try void volatile while true false null var yield record sealed permits _').split())


def naming_review_reason(proof):
    return next((t['naming_validation']['reason_code'] for t in proof['targets']
                 if t.get('naming_validation', {}).get('status') == 'REVIEW'), None)


def evaluate_name(context, proposal):
    """Bounded source grounding, then a distinct information-gain check.

    Names may reorder descriptive terms. Relations/negation must survive;
    arbitrary paraphrase remains unknown, never certified by model prose.
    Explanations can quote original intent in neutral prose without a template.
    """
    qualifier, name, explanation = proposal['qualifier'], proposal['name'], proposal['explanation']
    if qualifier not in context['qualifiers']:
        raise ValueError('MNT_UNGROUNDED_QUALIFIER')
    if not isinstance(explanation,str) or not explanation.strip():
        raise ValueError('MNT_UNVERIFIED_EXPLANATION')
    cardinality = context['rule'] == 'BOUND_CARDINALITY_WITH_QUOTED_FIXTURE_TOKEN_V1'
    fixture_trace = context.get('fixture_trace') or {}
    quoted_preparation = qualifier in fixture_trace.get('labels', [])
    fixture = cardinality or quoted_preparation
    source_words = ([qualifier,*context['subject_words'],'count'] if cardinality else
                    [qualifier,*fixture_trace['call_words'],'fixture'] if quoted_preparation else words(qualifier))
    name_words = words(name)
    allowed = set(source_words) | NAME_GLUE
    # Explicitly ungrounded claims are rejection, unknown synonyms are review.
    ungrounded = (set(name_words) | set(words(explanation))) & CLAIM_WORDS - set(source_words)
    if ungrounded:
        raise ValueError('MNT_UNGROUNDED_SEMANTIC_CLAIM')
    if not fixture and not context.get('intent_available', bool(qualifier)):
        return 'MNT_CONTEXT_ONLY_BENEFIT_UNCONFIRMED'
    if set(name_words) - allowed:
        return 'MNT_NAME_MEANING_REQUIRES_REVIEW'
    if re.search(r'\d',name):
        # A numeric suffix may encode a new assertion about value/identity.
        # Existing numeric targets are not a license to append arbitrary digits.
        return 'MNT_NAME_MEANING_REQUIRES_REVIEW'
    content = set(source_words) - NAME_GLUE - (set() if fixture else PROSE_FRAMING)
    if not content <= set(name_words):
        return 'MNT_NAME_MEANING_REQUIRES_REVIEW'
    # Never turn "after reset" into "reset after ...", or drop a negation.
    for index, word in enumerate(source_words):
        if word in {'after','before','without','not','no','non','unless','until','except'}:
            if index+1 >= len(source_words): return 'MNT_NAME_MEANING_REQUIRES_REVIEW'
            pairs=list(zip(name_words,name_words[1:]))
            if (word,source_words[index+1]) not in pairs:
                return 'MNT_NAME_MEANING_REQUIRES_REVIEW'
    if fixture:
        # These are roles already proved by the bound cardinality/fixture path,
        # not a fixed phrase order. Do not turn fixture tokens into runtime claims.
        receiver = context['receiver_name'] if cardinality else fixture_trace['receiver_name']
        required = set(source_words) | {'fixture','token'} | set(words(receiver))
        prose = set(words(explanation))
        neutral = {'expected','the','a','an','of','for','in','original','test','context','describes'}
        quoted = re.findall(r'''["']([^"']+)["']''', explanation)
        if (not required <= prose or prose - required - neutral or
                qualifier not in [q.lower() for q in quoted] or re.search(r'\d', explanation)):
            return 'MNT_EXPLANATION_REQUIRES_REVIEW'
    else:
        # A neutral wrapper around an exact source quotation is provable; new
        # relations/synonyms require review rather than arbitrary NL inference.
        from .mnt_comments import information
        phrase = tuple(str(x).lower() for x in information(qualifier))
        prose = tuple(str(x).lower() for x in information(explanation))
        positions = [i for i in range(len(prose)-len(phrase)+1) if prose[i:i+len(phrase)] == phrase]
        neutral = {'original','test','intent','expected','context','describes','the','a','an','name',
                   'exposes','at','assertion',':','.',',','"',"'"}
        if not phrase or not any(set(prose[:i]+prose[i+len(phrase):]) <= neutral for i in positions):
            return 'MNT_EXPLANATION_REQUIRES_REVIEW'
    novel = set(name_words) - set(words(context['actual_expression'])) - GENERIC_WORDS - NAME_GLUE
    if len(set(name_words)-NAME_GLUE) < 2 or not novel:
        return 'MNT_NAME_BENEFIT_UNCONFIRMED'
    return None


def words(text):
    return [w.lower() for w in re.findall(r'[A-Z]+(?=[A-Z][a-z]|[^A-Za-z]|$)|[A-Z]?[a-z]+', text)]


def annotation_purpose(text):
    """Bounded negative evidence, not a general prose/meaning classifier.

    A maintenance instruction cannot supply a numeric name. Explicit uncertainty
    about an expectation is stronger negative evidence ONLY at its target.
    Ordinary prose still needs every existing trace and benefit check.
    """
    text = re.sub(r'\s+', ' ', text).strip()
    subject = re.search(r'(?i)\b(?:expected|expectation|result|answer|value|outcome)\b|预期|结果|数值', text)
    pending = re.search(r'(?i)\b(?:TODO|FIXME|XXX|TBD|placeholder|pending|provisional|unverified|'
                        r'uncertain|unsure|unknown|unconfirmed)\b|not\s+sure|'
                        r'not\s+(?:yet\s+|been\s+)?(?:verified|confirmed|checked|validated|known)\b|'
                        r'待确认|待核实|占位|不确定', text)
    # A normal "check the result after ..." describes the test's purpose.
    # Require an explicit deferred-verification request, not check/verify alone.
    confirmation = re.search(r'(?i)\bplease\s+(?:verify|confirm|recheck)\b|'
                             r'\bneeds?\s+(?:(?:to\s+be\s+)?(?:verified|confirmed|checked)|'
                             r'verification|confirmation|checking)\b|请.*(?:核对|确认|核实)', text)
    question = '?' in text or '？' in text
    if subject and (pending or confirmation or question):
        return 'EXPECTED_UNCONFIRMED'
    if question and re.search(r'(?i)\b(?:this|that|it)\b.*\b(?:correct|right|wrong|expected)\b', text):
        return 'EXPECTED_UNCONFIRMED'
    if pending or question or re.search(r'(?i)\b(?:to\s+be\s+(?:confirmed|determined|checked)|fill\s+(?:this|in)|'
                            r'insert\s+(?:description|explanation)|replace\s+me)\b', text):
        return 'NON_EXPLANATORY'
    return 'EXPLANATORY_CANDIDATE'


def associated_comments(statement):
    """Use immediate comment groups and same-line trailing comments only.

    Do not steal a previous statement's trailing comment or search the method
    for TODOs. Blank lines do not change the established AST association.
    """
    from .safety_contracts import COMMENTS
    previous = statement.prev_named_sibling
    notes = []
    while previous and previous.type in COMMENTS:
        anchor = previous.prev_named_sibling
        if anchor and anchor.type not in COMMENTS and anchor.end_point.row == previous.start_point.row:
            break
        notes.append(previous)
        previous = previous.prev_named_sibling
    notes.reverse()
    following = statement.next_named_sibling
    while following and following.type in COMMENTS and following.start_point.row == statement.end_point.row:
        notes.append(following)
        following = following.next_named_sibling
    return notes


def target_annotations(java, call, statement):
    notes = associated_comments(statement)
    args = java.at(call['start'], call['end']).child_by_field_name('arguments').named_children
    if len(args) == 3 and args[-1].type == 'string_literal':
        notes.append(args[-1])
    return notes


def unconfirmed_target_intent(java, call, statement):
    from .safety_contracts import digest
    evidence = []
    for note in target_annotations(java, call, statement):
        text = java.text(note)
        if annotation_purpose(text) == 'EXPECTED_UNCONFIRMED':
            start,end = java.span(note)
            evidence.append({'source':'ORIGINAL_METHOD', 'start':start, 'end':end,
                             'quote':text, 'sha256':digest(text), 'purpose':'EXPECTED_UNCONFIRMED'})
    return evidence


def naming_context(java, call, actual, statement, target_id, *, resolver=None):
    # Both old fixture labels and the new intent path must pass the same alias,
    # reassignment and branch barriers before contributing semantic evidence.
    legacy = fixture_naming_context(java, call, actual, statement, target_id)
    trace = original_trace(java, statement, call, resolver, initializer_labels_only=bool(legacy)) if resolver else None
    def bound(value):
        from .safety_contracts import digest
        value.update(target_id=target_id, method_sha256=digest(java.source),
                     assertion_id=call['assertion_id'], actual_expression=call['arguments'][1],
                     generation_permission='CONTEXT_ONLY_NOT_NAME_OR_BENEFIT_PROOF')
        return value
    if legacy:
        return bound(legacy)
    if not trace:
        return None
    from .safety_contracts import digest, COMMENTS
    intents = []
    for node in trace:
        intents.extend(associated_comments(node))
    # Original assertion messages are equally admissible intent, never numeric proof.
    node = java.at(call['start'], call['end'])
    args = node.child_by_field_name('arguments').named_children
    if len(args) == 3 and args[-1].type == 'string_literal':
        intents.append(args[-1])
    choices = []
    for intent in intents:
        raw = java.text(intent)
        if annotation_purpose(raw) != 'EXPLANATORY_CANDIDATE':
            continue
        if intent.type == 'string_literal':
            if '\\' in raw: continue
            phrase = raw[1:-1]
        else:
            # Protect machine directives; only ordinary original prose is a naming source.
            from .mnt_comments import PROTECTED
            if PROTECTED.search(raw): continue
            phrase = re.sub(r'^//\s*|^/\*+|\*/$', '', raw).strip()
            phrase = re.sub(r'\s*\n\s*\*?\s*', ' ', phrase)
        # This is only an attempt gate. Do not require a final identifier phrase,
        # a word order, a numeric-free sentence, or a proven information gain.
        if not set(words(phrase)) - GENERIC_WORDS:
            continue
        choices.append((intent,phrase))
    from .safety_contracts import walk, children
    # A variable name or a bare literal assignment alone is not naming context.
    # Keep actual test preparation with arguments/array elements or related calls;
    # original_trace has already stopped aliases, reassignments and ambiguity.
    prepared = any(n != statement and (
        n.type == 'expression_statement' or any(
            x.type in ('object_creation_expression','array_initializer') and
            bool(children(x.child_by_field_name('arguments')) if x.type == 'object_creation_expression' else children(x))
            for x in walk(n))) for n in trace)
    if not choices and not prepared: return None
    # A later candidate may expose an exact test-fixture token at expected, even
    # when the bound getter is not a cardinality. This describes fixture identity
    # and the original call only, never a production value, unit or business rule.
    # The same trace barriers already apply; do not inspect unrelated initializers.
    receiver=actual
    while receiver.get('receiver'): receiver=receiver['receiver']
    fixture_trace={}
    if actual.get('method_name') and receiver.get('source') == 'LOCAL_DECLARATION':
        labels=set()
        for node in trace:
            if node == statement: continue
            for declaration in walk(node):
                if declaration.type != 'variable_declarator' or java.text(declaration.child_by_field_name('name')) != receiver.get('name'):
                    continue
                value=declaration.child_by_field_name('value')
                if value and value.type == 'object_creation_expression':
                    for literal in children(value.child_by_field_name('arguments')):
                        raw=java.text(literal)
                        if literal.type == 'string_literal' and re.fullmatch(r'"[A-Za-z]{3,24}"',raw):
                            labels.add(raw[1:-1].lower())
        labels -= GENERIC_WORDS | {'true','false','null'}
        if labels:
            fixture_trace={'labels':sorted(labels),'receiver_name':receiver['name'],
                           'call_words':words(actual['method_name']),
                           'claim_scope':'QUOTED_TEST_FIXTURE_IDENTITY_AND_ORIGINAL_CALL_ONLY'}
    evidence=[]
    for index, node in enumerate([*trace,*(n for n,_ in choices)]):
        start,end=java.span(node);quote=java.text(node)
        evidence.append({'evidence_id':target_id+':ORIGINAL_TRACE:'+str(index),'source':'ORIGINAL_METHOD',
            'start':start,'end':end,'quote':quote,'sha256':digest(quote)})
    return bound({'rule':'ORIGINAL_INTENT_TRACE_V1','evidence':evidence,
        'qualifiers':list(dict.fromkeys([*(phrase for _,phrase in choices),*fixture_trace.get('labels',[])])) or [''],
        'fixture_trace':fixture_trace,
        'intent_available':bool(choices),
        # Legacy field is an optional quoting example, never a required template.
        'explanation_template':'Original test intent: {qualifier}',
        'benefit_rule':'PENDING_INDEPENDENT_NAME_RELEVANCE_AND_INFORMATION_GAIN',
        'trace_status':'UNALIASED_LINEAR_SOURCE_TRACE','numeric_correctness_inferred':False})


def original_trace(java, statement, call, resolver, *, initializer_labels_only=False):
    """Trace only local/this-field reads and direct same-block preparation.

    All nodes are original. Unknown aliases/escapes/control flow stop naming,
    not type inference. No counting calls or evaluating production code.
    """
    from .safety_contracts import walk, children, PRIMITIVES
    if resolver is None: return None
    expression=java.at(call['start'],call['end']).child_by_field_name('arguments').named_children[1]
    declarations={};fields=set();nodes=[statement]
    def trace(node, depth=0):
        if depth>4: raise ValueError('MNT_CONTEXT_TRACE_DEPTH_UNSUPPORTED')
        if node.type=='identifier':
            name=java.text(node)
            binding=resolver.variable(name,expression)
            if binding['source']=='FIELD_DECLARATION':fields.add(name);return
            matches=[n for n in walk(java.root) if n.type=='variable_declarator' and java.text(n.child_by_field_name('name'))==name]
            if len(matches)!=1:raise ValueError('MNT_CONTEXT_BINDING_UNRESOLVED')
            declaration=matches[0];parent=declaration.parent
            if parent.parent!=statement.parent or parent.start_byte>=statement.start_byte:
                raise ValueError('MNT_CONTEXT_SCOPE_UNSUPPORTED')
            if name in declarations:return
            declarations[name]=(declaration,binding['type']);nodes.append(parent)
            value=declaration.child_by_field_name('value')
            if value is None:raise ValueError('MNT_CONTEXT_INITIALIZER_MISSING')
            if value.type=='identifier' and binding['type'] not in PRIMITIVES:
                raise ValueError('MNT_CONTEXT_ALIAS_UNSUPPORTED')
            if initializer_labels_only:
                # The established path quotes a literal fixture token only; it
                # does not infer the initializer's runtime/production semantics.
                return
            trace(value,depth+1)
        elif node.type=='field_access':
            obj=node.child_by_field_name('object')
            if obj.type=='this':fields.add(java.text(node.child_by_field_name('field')))
            else:trace(obj,depth+1)
        elif node.type=='method_invocation':
            obj=node.child_by_field_name('object')
            if obj is None or obj.type=='this':
                raise ValueError('MNT_CONTEXT_IMPLICIT_RECEIVER_UNSUPPORTED')
            # A return type alone does not prove the initialization call binding.
            resolver.expression(node,expression)
            trace(obj,depth+1)
            if any(n.type=='identifier' for n in children(node.child_by_field_name('arguments'))):
                raise ValueError('MNT_CONTEXT_ARGUMENT_ALIAS_UNSUPPORTED')
        elif node.type=='object_creation_expression':
            if any(n.type=='identifier' for n in children(node.child_by_field_name('arguments'))):
                raise ValueError('MNT_CONTEXT_ARGUMENT_ALIAS_UNSUPPORTED')
        elif node.type not in ('string_literal','character_literal','decimal_integer_literal','hex_integer_literal',
                                'decimal_floating_point_literal','null_literal','array_creation_expression','array_initializer'):
            raise ValueError('MNT_CONTEXT_EXPRESSION_UNSUPPORTED')
    trace(expression)
    names=set(declarations)|fields
    if not names:return None
    start=statement.parent.start_byte if fields else min(n.start_byte for n in nodes)
    for sibling in statement.parent.named_children:
        if sibling.end_byte>statement.start_byte or sibling.end_byte<start:continue
        identifiers={java.text(n) for n in walk(sibling) if n.type=='identifier'}
        if not names.intersection(identifiers):continue
        if sibling.type in ('if_statement','try_statement','while_statement','for_statement','switch_expression','do_statement','enhanced_for_statement'):
            raise ValueError('MNT_CONTEXT_CONTROL_FLOW_AMBIGUOUS')
        for n in walk(sibling):
            if n.type in ('assignment_expression','update_expression'):
                raise ValueError('MNT_CONTEXT_REASSIGNMENT_UNSUPPORTED')
            if n.type=='variable_declarator' and n not in [d[0] for d in declarations.values()]:
                value=n.child_by_field_name('value')
                if value and any(java.text(x) in names for x in walk(value) if x.type=='identifier'):
                    raise ValueError('MNT_CONTEXT_ALIAS_UNSUPPORTED')
            if n.type=='argument_list' and any(java.text(arg) in names for arg in children(n)):
                raise ValueError('MNT_CONTEXT_ESCAPE_UNSUPPORTED')
        if sibling.type=='expression_statement':
            invocation=children(sibling)[0]
            if invocation.type=='method_invocation' and not java.text(invocation.child_by_field_name('name')).startswith('assert'):
                resolver.expression(invocation,expression)
                nodes.append(sibling)
    return sorted({(n.start_byte,n.end_byte):n for n in nodes}.values(),key=lambda n:n.start_byte)


def fixture_naming_context(java, call, actual, statement, target_id):
    from .safety_contracts import walk, children, digest
    if call['name'] != 'assertEquals':
        return None
    cardinality = actual.get('semantic_kind') == 'ARRAY_LENGTH'
    measured = actual.get('receiver', {}) if cardinality else actual
    method = measured.get('method') or {}
    if not cardinality:
        cardinality = bool(re.search(r'(?is)@return\s+(?:the\s+)?(?:number|count)\s+of\b', method.get('javadoc', '')))
    if not cardinality:
        return None
    subject_words = [w for w in words(measured.get('method_name', measured.get('name', '')))
                     if w not in {'get', 'count', 'size', 'length', 'number', 'of'}]
    if not subject_words or any(w in {'value', 'values', 'actual', 'result', 'data', 'item', 'items'} for w in subject_words):
        return None
    receiver = measured
    while receiver.get('receiver'):
        receiver = receiver['receiver']
    if receiver.get('source') != 'LOCAL_DECLARATION' or not receiver.get('initializer'):
        return None
    declarations = [n for n in walk(java.root) if n.type == 'variable_declarator'
                    and java.text(n.child_by_field_name('name')) == receiver.get('name')]
    if len(declarations) != 1:
        return None
    declaration = declarations[0]
    if declaration.parent.parent != statement.parent or declaration.start_byte >= statement.start_byte:
        return None
    initializer = declaration.child_by_field_name('value')
    qualifiers = set()
    for n in walk(initializer):
        # No method-name inference. These tokens are quoted fixture labels only.
        if n.type == 'string_literal':
            token = java.text(n)[1:-1]
        elif n.type == 'field_access':
            token = java.text(n.child_by_field_name('field'))
            if not re.fullmatch(r'[A-Z][A-Z_]+', token):
                continue
        else:
            continue
        if re.fullmatch(r'[A-Za-z]{3,24}', token):
            qualifiers.add(token.lower())
    qualifiers -= {'true', 'false', 'null', 'value', 'expected', 'actual', 'number', 'magic'}
    if not qualifiers:
        return None
    subject = ' '.join(subject_words)
    evidence = []
    for kind, node in [('TEST_RECEIVER_PREPARATION', declaration.parent), ('ORIGINAL_ASSERTION', statement)]:
        start, end = java.span(node)
        quote = java.text(node)
        evidence.append({'evidence_id': target_id + ':' + kind,
                         'source': 'ORIGINAL_METHOD', 'start': start, 'end': end,
                         'sha256': digest(quote), 'quote': quote})
    # Ordinary comments/messages are preserved as context, but cannot themselves
    # create a new semantic claim or an unbound naming qualifier.
    previous = statement.prev_named_sibling
    note = java.text(previous) if previous and previous.type in ('line_comment', 'block_comment') else ''
    return {'rule': 'BOUND_CARDINALITY_WITH_QUOTED_FIXTURE_TOKEN_V1',
            'subject_words': subject_words, 'qualifiers': sorted(qualifiers),
            'receiver_name': receiver['name'], 'evidence': evidence,
            'ordinary_comment': note, 'original_message': call['arguments'][2:] or [],
            'explanation_template': f'Expected {subject} count for fixture token "{{qualifier}}" in {receiver["name"]}.',
            'benefit_rule': 'EXPLICIT_EXPECTED_CARDINALITY_AND_TEST_FIXTURE_LABEL; no inferred business claim'}


def pending_targets(proof):
    return [t for t in (proof or {}).get('targets', []) if t.get('action') == 'PROPOSE_LOCAL_NAME']


def materialize_names(proof, proposals, original, owner, *, _collect_review=False):
    """Return a candidate-specific copy. NEVER alter the original risk proof."""
    from .safety_contracts import Java, walk, digest
    result = copy.deepcopy(proof)
    result, proposals = select_targets(result, proposals)
    # Names are checked on the chosen combination, not separately per target.
    declarations = [t['suggested_name'] for t in result['targets'] if t.get('action') == 'DECLARE_LOCAL']
    if len(declarations) != len(set(declarations)):
        raise ValueError('MNT_DUPLICATE_DECLARATION_NAME')
    targets = pending_targets(result)
    if not targets:
        if proposals:
            raise ValueError('MNT_UNAPPROVED_NAMING_PROPOSAL')
        return result
    if not isinstance(proposals, list) or len(proposals) != len(targets):
        raise ValueError('MNT_NAMING_PROPOSALS_REQUIRED')
    if any(not isinstance(p, dict) for p in proposals):
        raise ValueError('MNT_INVALID_NAMING_PROPOSAL')
    ids = [p.get('target_id') for p in proposals]
    if len(set(ids)) != len(ids) or set(ids) != {t['target_id'] for t in targets}:
        raise ValueError('MNT_NAMING_TARGET_SET_MISMATCH')
    java = Java(owner or original)
    names = {java.text(n) for n in walk(java.root) if n.type in ('identifier', 'type_identifier')}
    names.update(t['suggested_name'] for t in result['targets'] if t.get('suggested_name'))
    for target in targets:
        proposal = next(p for p in proposals if p['target_id'] == target['target_id'])
        if set(proposal) != {'target_id', 'name', 'qualifier', 'explanation', 'evidence_ids'}:
            raise ValueError('MNT_INVALID_NAMING_PROPOSAL_FIELDS')
        context = target['naming_context']
        if context.get('method_sha256') != digest(original) or context.get('target_id') != target['target_id']:
            raise ValueError('MNT_STALE_NAMING_EVIDENCE')
        refs = proposal['evidence_ids']
        expected_refs = [e['evidence_id'] for e in context['evidence']]
        if not isinstance(refs, list) or len(refs) != len(expected_refs) or set(refs) != set(expected_refs):
            raise ValueError('MNT_ORIGINAL_EVIDENCE_REQUIRED')
        for e in context['evidence']:
            if original[e['start']:e['end']] != e['quote'] or digest(e['quote']) != e['sha256']:
                raise ValueError('MNT_STALE_NAMING_EVIDENCE')
        name = proposal['name']
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$]*', name) or name in JAVA_RESERVED:
            raise ValueError('MNT_INVALID_LOCAL_NAME')
        if name in names:
            raise ValueError('MNT_NAME_COLLISION')
        reason = evaluate_name(context, proposal)
        names.add(name)
        target.update(action='DECLARE_LOCAL', suggested_name=name,
                      declaration=f'final {target["literal_type"]} {name} = {target["literal"]};',
                      naming_validation={'status': 'REVIEW' if reason else 'PASS', 'rule': context['rule'],
                                         'reason_code': reason or 'MNT_NAME_SOURCE_AND_GAIN_VERIFIED',
                                         'source_grounding': 'UNKNOWN' if reason and reason != 'MNT_NAME_BENEFIT_UNCONFIRMED' else 'PASS',
                                         'explanatory_gain': 'UNKNOWN' if reason else 'EXPOSES_ORIGINAL_INTENT_AT_EXPECTED',
                                         'explanation': proposal['explanation'],
                                         'evidence_ids': refs, 'model_self_assessment_used': False})
    reason = naming_review_reason(result)
    if reason and not _collect_review:
        raise ValueError(reason)
    return result


def select_targets(proof, selection):
    """Validate a complete, source-bound partition; never trust model skip claims.

    Old list/plain responses remain an ALL-target adapter for fresh proofs only.
    A subset always needs the explicit envelope, including every retained ID.
    """
    universe = {t['target_id']: t for t in proof.get('numeric_targets', [])}
    safe = {t['target_id']: t for t in proof.get('targets', [])}
    if isinstance(selection, dict):
        from pipeline.contracts import validate_contract
        if selection.get('selected_target_ids') == []:
            raise ValueError('MNT_ZERO_EFFECTIVE_TARGETS')
        try:
            validate_contract(selection, 'mnt-selection')
        except ValueError as exc:
            raise ValueError('MNT_INVALID_TARGET_SELECTION_SCHEMA') from exc
        ids = selection['selected_target_ids']
        retained = selection['retained_targets']
        keep = [t['target_id'] for t in retained]
        if len(set(keep)) != len(keep) or set(ids) & set(keep) or set(ids) | set(keep) != set(universe):
            raise ValueError('MNT_SELECTION_PARTITION_MISMATCH')
        proposals = selection['naming_proposals']
    else:
        if set(universe) != set(safe):
            raise ValueError('MNT_EXPLICIT_SELECTION_REQUIRED')
        ids = list(safe); retained = []; proposals = selection
    if not ids:
        raise ValueError('MNT_ZERO_EFFECTIVE_TARGETS')
    if len(set(ids)) != len(ids) or not set(ids) <= set(safe):
        raise ValueError('MNT_SELECTION_OUTSIDE_SAFE_SET')
    proof['targets'] = [t for t in proof['targets'] if t['target_id'] in ids]
    proof['selected_target_ids'] = [t['target_id'] for t in proof['targets']]
    proof['selection_retained_targets'] = retained
    return proof, proposals


def decode_response(raw, proof, saved_proposals=None):
    """Only MNT's new naming path uses a strict JSON envelope."""
    if saved_proposals is not None:
        return raw, saved_proposals
    text = raw.strip()
    if not pending_targets(proof) and not text.startswith(('{', '```json')):
        return raw, []  # strict ALL-target compatibility adapter in select_targets
    fence = re.fullmatch(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    if fence:
        text = fence[1]
    def unique_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('MNT_DUPLICATE_RESPONSE_KEY')
            value[key] = item
        return value
    value = json.loads(text, object_pairs_hook=unique_pairs)
    if not isinstance(value, dict) or not isinstance(value.get('method_code'), str):
        raise ValueError('MNT_STRUCTURED_RESPONSE_REQUIRED')
    if set(value) == {'method_code', 'naming_proposals', 'selected_target_ids', 'retained_targets'}:
        return value['method_code'], {k: v for k, v in value.items() if k != 'method_code'}
    if set(value) != {'method_code', 'naming_proposals'}:
        raise ValueError('MNT_STRUCTURED_RESPONSE_REQUIRED')
    return value['method_code'], value['naming_proposals']


def saved_selection(contract):
    """New persisted selection is separate from the legacy naming-proposal list."""
    selection = contract.get('mnt_selection')
    if selection is not None:
        if not isinstance(selection, dict) or selection.get('naming_proposals') != contract.get('mnt_naming_proposals'):
            raise ValueError('MNT_CONFLICTING_SAVED_SELECTION')
        return selection
    return contract.get('mnt_naming_proposals')
