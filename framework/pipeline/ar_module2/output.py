"""AST output representation adapter and LF source-range composition; no repair."""
import re
from pathlib import Path
from pipeline.agents.safety_contracts import Java, children
from pipeline.agents.method_location import single_method, signature
from pipeline.verification.candidate.validation import java_semantic_tokens
from pipeline.hashing import normalize_lf, sha256_utf8_exact, build_file_id
from pipeline.file_candidate import FileCandidateBundle

VERSION = 'ar-module2-ast-output-lf-1'


def parse(raw, original, owner):
    text = normalize_lf(raw or '').strip()
    result = {'version': VERSION, 'status': 'FAIL', 'candidate_method': None,
              'representation': None, 'reason': None, 'raw_content_discarded': False}
    if text == 'REFUSE_UNSAFE_REFACTORING':
        return {**result, 'reason': 'MODEL_REFUSAL'}
    fence = re.fullmatch(r'```(?:java)?\s*\n?(.*?)\s*```', text, re.DOTALL | re.IGNORECASE)
    if fence:
        if '```' in fence.group(1):
            return {**result, 'status': 'INCONCLUSIVE', 'reason': 'UNSUPPORTED_MIXED_OR_MULTIPLE_BLOCKS'}
        text = fence.group(1).strip()
    elif '```' in text:
        # Preserve ambiguous mixed / multiple output, never concatenate independent patches.
        return {**result, 'status': 'INCONCLUSIVE', 'reason': 'UNSUPPORTED_MIXED_OR_MULTIPLE_BLOCKS'}
    try:
        java, method = single_method(text)
        return {**result, 'status': 'PASS', 'candidate_method': text,
                'representation': 'FENCED_METHOD' if fence else 'METHOD'}
    except ValueError:
        pass
    try:
        java = Java(text)
        before, original_node = single_method(normalize_lf(original))
        expected = signature(before, original_node)
        roots = [n for n in children(java.root) if n.type not in ('line_comment', 'block_comment', 'package_declaration', 'import_declaration')]
        if len(roots) == 1 and roots[0].type == 'class_declaration':
            cls = roots[0]
            owner_java = Java(owner)
            original_classes = [n for n in owner_java.nodes('class_declaration')
                                if owner_java.text(n.child_by_field_name('name')) == java.text(cls.child_by_field_name('name'))]
            if len(original_classes) != 1:
                return {**result, 'reason': 'CLASS_OWNER_MISMATCH'}
            body = cls.child_by_field_name('body')
            members = [n for n in children(body) if n.type not in ('line_comment', 'block_comment')]
            matches = [n for n in members if n.type == 'method_declaration' and signature(java, n) == expected]
            if len(matches) != 1:
                return {**result, 'reason': 'TARGET_METHOD_MISSING_OR_AMBIGUOUS'}
            target = matches[0]
            candidate = java.text(target)
            if len(members) == 1:
                # A one-method class is a representation envelope, NOT a whole-file replacement.
                return {**result, 'status': 'PASS', 'candidate_method': candidate,
                        'representation': 'SINGLE_TARGET_CLASS_ENVELOPE'}
            old_targets = [n for n in owner_java.nodes('method_declaration') if signature(owner_java, n) == expected
                           and java_semantic_tokens(owner_java.text(n)) == java_semantic_tokens(original)]
            if len(old_targets) != 1:
                return {**result, 'status': 'INCONCLUSIVE', 'reason': 'WRAPPER_SOURCE_TARGET_AMBIGUOUS'}
            old_start, old_end = owner_java.span(old_targets[0]); start, end = java.span(target)
            old_masked = owner[:old_start] + '__TARGET__' + owner[old_end:]
            new_masked = text[:start] + '__TARGET__' + text[end:]
            if java_semantic_tokens(old_masked) != java_semantic_tokens(new_masked):
                old_members = [signature(owner_java, n) for n in owner_java.nodes('method_declaration')]
                new_members = [signature(java, n) for n in java.nodes('method_declaration')]
                lost = sorted(set(old_members) - set(new_members))
                return {**result, 'status': 'FAIL' if lost else 'INCONCLUSIVE',
                        'candidate_method': candidate, 'reason': 'WRAPPER_LOST_METHODS' if lost else 'UNSUPPORTED_WRAPPER_OUTSIDE_EDITS',
                        'lost_methods': lost}
            def outside_comments(obj, lo, hi):
                return [obj.text(n) for n in sorted(obj.nodes('line_comment') + obj.nodes('block_comment'), key=lambda n: n.start_byte)
                        if obj.span(n)[1] <= lo or obj.span(n)[0] >= hi]
            if outside_comments(owner_java, old_start, old_end) != outside_comments(java, start, end):
                return {**result, 'reason': 'WRAPPER_COMMENT_INFORMATION_CHANGED'}
            return {**result, 'status': 'PASS', 'candidate_method': candidate, 'representation': 'COMPLETE_UNCHANGED_CLASS_WRAPPER'}
        if any(n.type == 'method_declaration' for n in roots):
            return {**result, 'status': 'INCONCLUSIVE', 'reason': 'UNSUPPORTED_ADDITIONAL_MEMBERS'}
        return {**result, 'reason': 'INVALID_OUTPUT_SHAPE'}
    except ValueError:
        return {**result, 'reason': 'JAVA_AST_PARSE_FAILED'}


def compose(source_file, instance, candidate_method, project):
    source_file = Path(source_file).resolve()
    original = normalize_lf(source_file.read_bytes().decode('utf-8'))
    java = Java(original)
    matches = [n for n in java.nodes('method_declaration')
               if signature(java, n) == instance['method_signature']
               and (n.start_point.row + 1, n.end_point.row + 1) == (instance['line_range']['start'], instance['line_range']['end'])
               and java_semantic_tokens(java.text(n)) == java_semantic_tokens(instance['test_code'])]
    if len(matches) != 1:
        raise ValueError('SOURCE_RANGE_NOT_UNIQUE')
    start, end = java.span(matches[0])
    method = normalize_lf(candidate_method)
    # Exact AST range, with outside text preserved after LF transport canonicalization.
    candidate = original[:start] + method + original[end:]
    item = {'instance_id': instance['instance_id'], 'method_signature': instance['method_signature'],
            'start_line': instance['line_range']['start'], 'end_line': instance['line_range']['end'],
            'candidate_method': method, 'source_start': start, 'source_end': end}
    return FileCandidateBundle(source_file=source_file, source_hash=sha256_utf8_exact(original),
        candidate_source=candidate, candidate_hash=sha256_utf8_exact(candidate),
        file_id=build_file_id(project_id=project, module_path=instance.get('module_path') or '',
                              source_relative_path=instance['test_relative_path']), methods=(item,),
        source_relative_path=instance['test_relative_path'])
