from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from pipeline.contracts.adapter_v41 import extract_method_signature

from pipeline.agents.refactoring_contract import normalized_source, replace_method_in_copy, sha256_text
from pipeline.agents.java_assertions import (
    JavaAssertionParseError,
    extract_ect_trigger,
    find_assertion_calls,
    mask_nested_assertions,
    root_assertions,
)
from pipeline.agents.risk_evidence import matching_delimiter, strip_comments_and_literals


METHOD_PATTERN = re.compile(
    r"(?m)^\s*(?:@\s*[\w$.]+(?:\s*\([^\n]*\))?\s*)*"
    r"(?:(?:public|protected|private|static|final|synchronized|abstract|native|strictfp)\s+)*"
    r"(?:<[^>{}]+>\s+)?[\w$][\w.$<>\[\], ?@]*\s+[\w$]+\s*\([^;{}]*\)\s*(?:throws\s+[^{}]+)?\{"
)
ANNOTATION_PATTERN = re.compile(r"@\s*(?:[A-Za-z_$]\w*\.)*(?P<name>[A-Za-z_$]\w*)")
FORBIDDEN_TOP_LEVEL = re.compile(r"(?m)^\s*(?:package|import|class|interface|enum|record)\b")
PLACEHOLDER = re.compile(r"(?i)\b(?:todo|fixme|tbd|placeholder|your code here)\b|\.\.\.")
MEANINGLESS_MESSAGES = {"assertion failed", "test failed", "check value"}


def check(check_id: str, passed: bool, message: str, *, evidence: Any = None, expected: Any = None, actual: Any = None, severity: str = "ERROR") -> dict[str, Any]:
    return {"check_id": check_id, "status": "PASS" if passed else "FAIL", "severity": severity, "message": message, "evidence": evidence, "expected": expected, "actual": actual}


def extract_single_method(raw: str, *, ast_scope: bool = False) -> tuple[str | None, list[dict[str, Any]]]:
    checks = []
    text = (raw or "").strip()
    fenced = re.fullmatch(r"```(?:java)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
        checks.append(check("MARKDOWN_CLEANED", True, "A single Java fence was safely removed.", severity="INFO"))
    elif "```" in text:
        return None, [check("NO_MARKDOWN_RESIDUE", False, "Malformed or mixed Markdown output was rejected.")]
    if ast_scope:
        from pipeline.agents.method_location import single_method
        try:
            single_method(text)
            return text, checks + [check('SINGLE_METHOD', True, 'One input-level method; nested declarations retain their owner.')]
        except ValueError as exc:
            return None, checks + [check('SINGLE_METHOD', False, str(exc))]
    matches = list(METHOD_PATTERN.finditer(strip_comments_and_literals(text)))
    if len(matches) != 1:
        return None, checks + [check("SINGLE_METHOD", False, "Exactly one method declaration is required.", expected=1, actual=len(matches))]
    start = matches[0].start()
    opening = strip_comments_and_literals(text).find("{", matches[0].start(), matches[0].end())
    closing = matching_delimiter(strip_comments_and_literals(text), opening, "{", "}")
    if closing is None or text[:start].strip() or text[closing + 1:].strip():
        return None, checks + [check("COMPLETE_METHOD_ONLY", False, "Output contains incomplete or extra class-level content.")]
    method = text[start:closing + 1].strip()
    return method, checks + [check("SINGLE_METHOD", True, "Exactly one complete method was extracted.")]


class CandidateValidator:
    validator_version = "4.2"

    def validate(self, *, raw_response: str, request: dict[str, Any], contract: dict[str, Any], original_file: str) -> dict[str, Any]:
        original = request["input"]["instance"]["test_code"]
        proposals = None
        if contract['smell'] == 'Magic_Number_Test':
            import copy
            from pipeline.agents.safety_contracts import get_proof
            from pipeline.agents.mnt_naming import decode_response, saved_selection
            proof = get_proof(request['input']['risk_result']['result']['evidence'])
            try:
                raw_response, proposals = decode_response(raw_response, proof, saved_selection(contract))
            except (ValueError, TypeError):
                return {'passed': False, 'candidate_method': None, 'candidate_file': None,
                        'checks': [check('MNT_NAMING_RESPONSE', False, 'MNT_STRUCTURED_RESPONSE_REQUIRED')]}
            request = copy.deepcopy(request)
            request['input']['mnt_naming_proposals'] = proposals
        candidate, checks = extract_single_method(raw_response, ast_scope=contract['smell']=='Magic_Number_Test')
        if candidate is None:
            return {"passed": False, "candidate_method": None, "candidate_file": None, "checks": checks}
        checks.extend(self._generic(candidate, original, contract, original_file))
        if contract['smell']=='Magic_Number_Test':
            from pipeline.agents.method_location import verify_location
            try:
                if contract.get('safety_instance') != request['input']['instance']:
                    raise ValueError('MNT_METHOD_INSTANCE_MISMATCH')
                verify_location(contract, original_file, candidate)
                checks.append(check('MNT_METHOD_LOCATION', True, 'Owner, signature, instance and original range agree.'))
            except (ValueError,KeyError,TypeError) as exc:
                checks.append(check('MNT_METHOD_LOCATION', False, str(exc)))
        if all(item["status"] == "PASS" for item in checks if item["severity"] == "ERROR"):
            checks.extend(self._smell(candidate, original, request, contract))
        from pipeline.verification.reintegration.preview import compose_preview
        candidate, candidate_file = compose_preview(original_file, candidate, contract, checks)
        passed = all(item["status"] == "PASS" for item in checks if item["severity"] == "ERROR")
        mnt_comments = next((c['evidence'] for c in checks if c['check_id'] == 'MNT_COMMENT_INFORMATION'), None)
        review_required = bool(mnt_comments and mnt_comments['decision'] == 'REVIEW' and all(
            c['status'] == 'PASS' or c['severity'] != 'ERROR' or
            (c['check_id'] in {'MNT_COMMENT_INFORMATION', 'MNT_BINDING_AND_REVERSE_INLINE'} and
             c['message'] == mnt_comments['reason_code']) for c in checks))
        if contract['smell'] == 'Magic_Number_Test':
            from pipeline.agents.mnt_naming import REVIEW_REASONS
            failures = [c for c in checks if c['status'] != 'PASS' and c['severity'] == 'ERROR']
            # A naming unknown routes to manual review only when all technical
            # checks finished and no hard failure (including comments) remains.
            review_required = review_required or bool(failures and all(
                c['check_id'] in {'MNT_NAME_GROUNDING','MNT_BINDING_AND_REVERSE_INLINE'} and
                c['message'] in REVIEW_REASONS for c in failures))
        return {"passed": passed, "candidate_method": candidate, "candidate_file": candidate_file if passed else None, "checks": checks,
                **({'mnt_comment_check': mnt_comments, 'review_required': review_required} if mnt_comments else {}),
                **({'mnt_naming_proposals': proposals['naming_proposals'] if isinstance(proposals,dict) else proposals,
                    'mnt_selection': proposals if isinstance(proposals,dict) else None} if proposals is not None else {})}

    def _generic(self, candidate: str, original: str, contract: dict[str, Any], original_file: str) -> list[dict[str, Any]]:
        current_hash = sha256_text(original_file)
        original_annotations = ANNOTATION_PATTERN.findall(original)
        candidate_annotations = ANNOTATION_PATTERN.findall(candidate)
        placeholder_original, placeholder_candidate = original, candidate
        if contract.get('smell') == 'Magic_Number_Test':
            # MNT prose is assessed by the independent comment contract; regex
            # matches inside comments are not Java annotations/placeholders.
            from pipeline.agents.safety_contracts import Java, walk, COMMENTS, apply_edits
            def executable_view(source):
                java = Java(source)
                annotations = [java.text(n.child_by_field_name('name')) for n in walk(java.root)
                               if n.type in ('annotation', 'marker_annotation')]
                masked = apply_edits(source, [(*java.span(n), ' ' * (java.span(n)[1]-java.span(n)[0]))
                                             for n in walk(java.root) if n.type in COMMENTS])
                return annotations, masked
            try:
                original_annotations, placeholder_original = executable_view(original)
                candidate_annotations, placeholder_candidate = executable_view(candidate)
            except ValueError:
                return [check('MNT_JAVA_AST', False, 'MNT_COMMENT_AST_UNAVAILABLE')]
        try: signature = extract_method_signature(candidate)
        except ValueError: signature = None
        return [
            check("NON_EMPTY", bool(candidate.strip()), "Candidate is non-empty."),
            check("NO_CLASS_LEVEL_CODE", contract.get('smell')=='Magic_Number_Test' or not FORBIDDEN_TOP_LEVEL.search(candidate), "No extra input-level declaration is present; MNT uses AST ownership."),
            check("METHOD_SIGNATURE_UNCHANGED", signature == contract["method_signature"], "Method name and signature are unchanged.", expected=contract["method_signature"], actual=signature),
            check("TEST_ANNOTATIONS_UNCHANGED", candidate_annotations == original_annotations, "Test annotations are unchanged.", expected=original_annotations, actual=candidate_annotations),
            check("SOURCE_HASH_MATCH", current_hash == contract["source_hash"], "Original file hash still matches the contract.", expected=contract["source_hash"], actual=current_hash),
            check("METHOD_NOT_DELETED", bool(METHOD_PATTERN.search(candidate)), "Target test method remains present."),
            _placeholder_check(placeholder_original, placeholder_candidate),
            check("HAS_CHANGE", normalized_source(candidate) != normalized_source(original), "Candidate must differ from the original method."),
        ]

    def _smell(self, candidate: str, original: str, request: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, Any]]:
        smell = contract["smell"]
        if smell == "Assertion_Roulette": return validate_assertion_roulette(original, candidate, request)
        if smell == "Redundant_Assertion": return validate_redundant_assertion(original, candidate, request)
        if smell == 'Exception_Catching_Throwing': return validate_exception_refactoring(original, candidate, request)
        if smell == 'Magic_Number_Test': return validate_magic_number(original, candidate, request)
        if smell == 'Conditional_Test_Logic':
            from pipeline.agents.safety_contracts import validate_exact
            ok, reason = validate_exact(smell, original, candidate, request)
            return [check('CTL_ORDERED_EXPANSION_CONTRACT', ok, reason)]
        return [check('SUPPORTED_SMELL', False, 'Unknown smell has no candidate contract.')]


def validate_assertion_roulette(original: str, candidate: str, request: dict[str, Any]) -> list[dict[str, Any]]:
    from pipeline.agents.safety_contracts import verify_proof, plain_string
    payload = request.get('input') or {}
    instance = payload.get('instance') or {}
    if original != instance.get('test_code'):
        return [check('CURRENT_RISK_CONTRACT', False, 'ORIGINAL_SOURCE_MISMATCH')]
    evidence = ((payload.get('risk_result') or {}).get('result') or {}).get('evidence') or []
    proof_ok, proof_reason = verify_proof(evidence, instance, payload.get('context') or {})
    if not proof_ok:
        return [check('CURRENT_RISK_CONTRACT', False, proof_reason)]
    try:
        old, new = find_assertion_calls(original), find_assertion_calls(candidate)
    except JavaAssertionParseError as exc:
        return [check("ASSERTION_AST_PARSE", False, "Both methods must have a valid Java AST.", evidence=str(exc))]
    risk_evidence = request["input"]["risk_result"]["result"]["evidence"]
    evidence_ids = {
        item.get("assertion_identity")
        for item in risk_evidence
        if item.get("conclusion") == "MISSING_MESSAGE" and item.get("assertion_identity")
    }
    evidence_codes = {
        normalized_source(item.get("matched_code") or "")
        for item in risk_evidence
        if item.get("conclusion") == "MISSING_MESSAGE"
    }
    old_keys = [_assertion_structure_key(item) for item in old]
    new_keys = [_assertion_structure_key(item) for item in new]
    checks = [
        check("ASSERTION_COUNT_UNCHANGED", len(old) == len(new), "Assertion count is unchanged.", expected=len(old), actual=len(new)),
        check("ASSERTION_ORDER_UNCHANGED", [x["name"] for x in old] == [x["name"] for x in new], "Assertion APIs and order are unchanged."),
        check("ASSERTION_AST_IDENTITY_UNCHANGED", old_keys == new_keys, "Assertion AST paths and control-flow identities are unchanged.", expected=old_keys, actual=new_keys),
        check(
            "ASSERTION_PARENT_RELATION_UNCHANGED",
            [_parent_structure_key(item, old) for item in old] == [_parent_structure_key(item, new) for item in new],
            "Nested assertion parent relationships are unchanged.",
        ),
    ]
    valid = len(old) == len(new) and old_keys == new_keys
    messages = []
    comparisons = []
    missing_targets: list[dict[str, Any]] = []
    if valid:
        for before, after in zip(old, new):
            before_args, after_args = before.get("arguments"), after.get("arguments")
            targeted = (
                before.get("assertion_id") in evidence_ids
                if evidence_ids
                else normalized_source(before.get("code") or "") in evidence_codes
            )
            if targeted:
                ok = (before_args is not None and after_args is not None
                      and len(after_args) == len(before_args) + 1
                      and plain_string(after_args[-1])
                      and before['code'].split('(', 1)[0] == after['code'].split('(', 1)[0]
                      and all(
                          java_semantic_tokens(_argument_without_nested(original, before, index, old))
                          == java_semantic_tokens(_argument_without_nested(candidate, after, index, new))
                          for index in range(len(before_args))
                      ))
                comparisons.append({"assertion":before["name"],"assertion_identity":before.get("assertion_id"),
                                    "parent_assertion_id":before.get("parent_assertion_id"),"nesting_depth":before.get("nesting_depth"),
                                    "targeted":True,"semantic_arguments_equal":ok,
                                    "layout_only_difference":bool(ok and before_args != after_args[:-1])})
                valid = valid and ok
                if ok:
                    messages.append((before, after_args[-1]))
                else:
                    missing_targets.append({"assertion_identity":before.get("assertion_id"),"api":before["name"],
                                            "nesting_depth":before.get("nesting_depth"),"ast_path":before.get("ast_path")})
            else:
                same = java_semantic_tokens(
                    mask_nested_assertions(original,start=before["start"],end=before["end"],calls=[x for x in old if x is not before])
                ) == java_semantic_tokens(
                    mask_nested_assertions(candidate,start=after["start"],end=after["end"],calls=[x for x in new if x is not after])
                )
                comparisons.append({"assertion":before["name"],"targeted":False,"semantic_arguments_equal":same,
                                    "layout_only_difference":bool(same and before.get("code") != after.get("code"))})
                valid = valid and same
    non_assertion_equal = java_semantic_tokens(_replace_assertions_with_markers(original, old)) == java_semantic_tokens(_replace_assertions_with_markers(candidate, new))
    checks.append(check("NON_ASSERTION_CODE_UNCHANGED", non_assertion_equal, "Code outside assertion calls is semantically unchanged."))
    checks.append(check("ONLY_TARGET_MESSAGES_ADDED", valid and bool(evidence_codes), "Only evidence-targeted assertions gained a final JUnit 5 message argument.", evidence=comparisons))
    checks.append(check("TARGET_ASSERTION_MESSAGE_MISSING", not missing_targets, "Every evidence-targeted assertion received a message.", evidence=missing_targets))
    nested_missing = [item for item in missing_targets if int(item.get("nesting_depth") or 0) > 0]
    checks.append(check("NESTED_ASSERTION_NOT_REFACTORED", not nested_missing, "Every nested target assertion received a message.", evidence=nested_missing))
    message_diagnostics = []
    if valid:
        for before, message in messages:
            message_diagnostics.append(assertion_message_diagnostic(before, message))
    meaningful = bool(message_diagnostics) and all(not item["reasons"] for item in message_diagnostics)
    checks.append(check("MESSAGES_MEANINGFUL", meaningful, "New assertion messages are specific rather than generic.",
                        evidence=message_diagnostics, actual=[message for _, message in messages]))
    return checks


def assertion_message_diagnostic(assertion: dict[str, Any], message: str) -> dict[str, Any]:
    """Apply the established Assertion Roulette message-quality policy."""
    from pipeline.agents.safety_contracts import grounded_message
    # A deterministic quotation of the precise source assertion is a checkable
    # diagnostic benefit. Unverified domain claims are never approved by length.
    from json import loads
    try:
        if loads(message) == loads(grounded_message(assertion)):
            return {'message': message, 'reasons': [], 'quality_rule': 'EXACT_SOURCE_ASSERTION_QUOTATION'}
    except (ValueError, TypeError):
        pass
    normalized = str(message).strip().strip('"').lower()
    argument_words = _argument_identifier_words(assertion.get("arguments") or [])
    identifier_restatement = bool(re.fullmatch(r"[a-z_$][\w$]*", normalized)) and normalized.replace("_", "") in argument_words
    reasons = ['UNVERIFIED_DIAGNOSTIC_CLAIM']
    if normalized in MEANINGLESS_MESSAGES: reasons.append("GENERIC_MESSAGE")
    if len(normalized) < 6: reasons.append("TOO_SHORT")
    if identifier_restatement: reasons.append("IDENTIFIER_RESTATEMENT")
    return {"message": normalized, "reasons": reasons}


def _replace_assertions_with_markers(source: str, assertions: list[dict[str, Any]]) -> str:
    pieces, cursor = [], 0
    for index, assertion in enumerate(root_assertions(assertions)):
        pieces.extend((source[cursor:assertion["start"]], f" __ASSERTION_{index}__ "))
        cursor = assertion["end"]
    pieces.append(source[cursor:])
    return "".join(pieces)


def _assertion_structure_key(assertion: dict[str, Any]) -> tuple[Any, ...]:
    return (
        assertion.get("name"), assertion.get("ast_path"),
        tuple(assertion.get("control_flow_path") or []),
        assertion.get("nesting_depth"), assertion.get("statement_ordinal"),
    )


def _parent_structure_key(assertion: dict[str, Any], assertions: list[dict[str, Any]]) -> tuple[Any, ...] | None:
    parent_id = assertion.get("parent_assertion_id")
    if not parent_id:
        return None
    parent = next((item for item in assertions if item.get("assertion_id") == parent_id), None)
    return _assertion_structure_key(parent) if parent else ("MISSING_PARENT",)


def _argument_without_nested(source: str, assertion: dict[str, Any], index: int, assertions: list[dict[str, Any]]) -> str:
    ranges = assertion.get("argument_ranges") or []
    if index >= len(ranges):
        return ""
    span = ranges[index]
    return mask_nested_assertions(
        source,
        start=int(span["start"]),
        end=int(span["end"]),
        calls=[item for item in assertions if item is not assertion],
    )


def java_semantic_tokens(value: str) -> tuple[str, ...]:
    """Tokenize Java while ignoring layout only outside literals/comments."""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    tokens: list[str] = []
    index = 0
    operators = (">>>=", "<<=", ">>=", "...", "::", "->", "==", "!=", "<=", ">=", "&&", "||",
                 "++", "--", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<", ">>>", ">>")
    while index < len(text):
        if text[index].isspace():
            index += 1; continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            end = len(text) if end < 0 else end
            tokens.append("COMMENT:" + text[index:end].rstrip()); index = end; continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = len(text) - 2 if end < 0 else end
            tokens.append("COMMENT:" + text[index:end + 2]); index = end + 2; continue
        if text.startswith('"""', index):
            end = text.find('"""', index + 3)
            end = len(text) - 3 if end < 0 else end
            tokens.append("TEXT_BLOCK:" + text[index:end + 3]); index = end + 3; continue
        if text[index] in ('"', "'"):
            quote, end = text[index], index + 1
            while end < len(text):
                if text[end] == "\\": end += 2; continue
                if text[end] == quote: end += 1; break
                end += 1
            raw_literal = text[index:end]
            if quote == '"':
                decoded = _decode_java_string_literal(raw_literal)
                # Invalid or structurally ambiguous literals stay source-exact:
                # decoding failure must never make two candidates equivalent.
                tokens.append(("STRING_VALUE:" + decoded) if decoded is not None else ("STRING_RAW:" + raw_literal))
            else:
                tokens.append("CHAR:" + raw_literal)
            index = end; continue
        match = re.match(r"[A-Za-z_$][\w$]*|(?:0[xX][0-9a-fA-F_]+|\d[\w._]*)", text[index:])
        if match:
            tokens.append(match.group(0)); index += len(match.group(0)); continue
        operator = next((item for item in operators if text.startswith(item, index)), None)
        if operator:
            tokens.append(operator); index += len(operator); continue
        tokens.append(text[index]); index += 1
    return tuple(tokens)


def _decode_java_string_literal(raw_literal: str) -> str | None:
    """Return the runtime value of one ordinary Java string literal.

    This is deliberately not used for character literals or text blocks.  It
    implements Java escape sequences (including octal) and a conservative
    subset of the pre-lexical Unicode translation.  Unicode escapes which
    would create a quote, backslash, or line terminator are rejected because
    they can change token boundaries or participate in a later escape; their
    raw token therefore remains the comparison identity.
    """
    if len(raw_literal) < 2 or raw_literal[0] != '"' or raw_literal[-1] != '"' or raw_literal.startswith('"""'):
        return None
    content = raw_literal[1:-1]
    translated: list[str] = []
    index = 0
    while index < len(content):
        if content[index] == "\\":
            preceding = 0
            for item in reversed(translated):
                if item != "\\": break
                preceding += 1
            match = re.match(r"\\u+([0-9a-fA-F]{4})", content[index:]) if preceding % 2 == 0 else None
            if match:
                character = chr(int(match.group(1), 16))
                if character in {'"', "\\", "\n", "\r"}:
                    return None
                translated.append(character)
                index += len(match.group(0))
                continue
        translated.append(content[index])
        index += 1

    source = "".join(translated)
    decoded: list[str] = []
    escapes = {"b":"\b", "t":"\t", "n":"\n", "f":"\f", "r":"\r", '"':'"', "'":"'", "\\":"\\", "s":" "}
    index = 0
    while index < len(source):
        character = source[index]
        if character in "\n\r":
            return None
        if character != "\\":
            decoded.append(character); index += 1; continue
        if index + 1 >= len(source):
            return None
        escaped = source[index + 1]
        if escaped in escapes:
            decoded.append(escapes[escaped]); index += 2; continue
        if escaped in "01234567":
            limit = 3 if escaped in "0123" else 2
            end = index + 2
            while end < len(source) and end < index + 1 + limit and source[end] in "01234567":
                end += 1
            decoded.append(chr(int(source[index + 1:end], 8))); index = end; continue
        return None
    return "".join(decoded)


def _argument_identifier_words(arguments: list[str]) -> set[str]:
    words: set[str] = set()
    for argument in arguments:
        for token in java_semantic_tokens(argument):
            if not re.fullmatch(r"[A-Za-z_$][\w$]*", token): continue
            parts = re.findall(r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+|\d+", token)
            lowered = [part.lower() for part in parts]
            words.update(lowered); words.add("".join(lowered)); words.add(token.lower().replace("_", ""))
            if lowered and lowered[0] in {"get", "is", "has", "set", "with"} and len(lowered) > 1:
                words.add("".join(lowered[1:]))
    return words


def _placeholder_check(original: str, candidate: str) -> dict[str, Any]:
    baseline = _placeholder_occurrences(original)
    current = _placeholder_occurrences(candidate)
    remaining = Counter(item["identity"] for item in baseline)
    inherited, added = [], []
    for item in current:
        if remaining[item["identity"]] > 0:
            remaining[item["identity"]] -= 1; inherited.append(item)
        else:
            added.append(item)
    evidence = {"baseline": [_safe_placeholder(x) for x in baseline], "candidate": [_safe_placeholder(x) for x in current],
                "inherited": [_safe_placeholder(x) for x in inherited], "added": [_safe_placeholder(x) for x in added]}
    return check("NO_PLACEHOLDER", not added, "Candidate introduces no suspicious placeholder.", evidence=evidence,
                 expected=0, actual=len(added))


def _placeholder_occurrences(value: str) -> list[dict[str, Any]]:
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    regions: list[tuple[str, int, int, str]] = []
    code_tokens: list[tuple[int, str]] = []
    index = 0
    while index < len(text):
        if text.startswith("//", index):
            end=text.find("\n",index); end=len(text) if end < 0 else end
            regions.append(("COMMENT",index,end,text[index:end].rstrip())); index=end; continue
        if text.startswith("/*", index):
            end=text.find("*/",index+2); end=len(text)-2 if end < 0 else end
            regions.append(("COMMENT",index,end+2,text[index:end+2])); index=end+2; continue
        if text.startswith('"""', index):
            end=text.find('"""',index+3); index=len(text) if end < 0 else end+3; continue
        if text[index] in ('"', "'"):
            quote=text[index]; index+=1
            while index < len(text):
                if text[index]=="\\": index+=2; continue
                if text[index]==quote: index+=1; break
                index+=1
            continue
        match=re.match(r"[A-Za-z_$][\w$]*|(?:0[xX][0-9a-fA-F_]+|\d[\w._]*)|\S",text[index:])
        if match:
            token=match.group(0); code_tokens.append((index,token)); regions.append(("CODE",index,index+len(token),token)); index+=len(token)
        else: index+=1
    found=[]
    for kind,start,end,content in regions:
        for match in PLACEHOLDER.finditer(content):
            absolute=start+match.start(); prev=next((t for pos,t in reversed(code_tokens) if pos < start),None)
            nxt=next((t for pos,t in code_tokens if pos >= end),None)
            marker=match.group(0).lower()
            identity=(kind,marker,content,prev,nxt)
            found.append({"kind":kind,"marker":marker,"offset":absolute,"line":text.count("\n",0,absolute)+1,"identity":identity})
    return found


def _safe_placeholder(item: dict[str, Any]) -> dict[str, Any]:
    return {"kind":item["kind"],"marker":item["marker"],"line":item["line"]}


def validate_redundant_assertion(original: str, candidate: str, request: dict[str, Any]) -> list[dict[str, Any]]:
    from pipeline.agents.safety_contracts import get_proof, validate_exact
    passed, reason = validate_exact('Redundant_Assertion', original, candidate, request)
    proof = get_proof(request.get('input', {}).get('risk_result', {}).get('result', {}).get('evidence', [])) or {}
    if proof.get('ra_transform') == 'CATCH_CONSTANT_DELETION':
        return [check(name, passed, reason) for name in (
            'RA_V2_TARGET_CONSTANT_DELETED', 'RA_V2_OTHER_CATCH_STATEMENTS_PRESERVED',
            'RA_V2_TRY_CATCH_CONTROL_FLOW_PRESERVED', 'RA_V2_OTHER_ASSERTIONS_PRESERVED',
            'RA_V2_NO_UNRELATED_TEXT_CHANGES', 'RA_V2_NON_TARGET_RA_PRESERVED')]
    return [check('ONLY_APPROVED_ASSERTION_REMOVED', passed, reason)]


def validate_exception_refactoring(original: str, candidate: str, request: dict[str, Any]) -> list[dict[str, Any]]:
    from pipeline.agents.safety_contracts import get_proof, validate_exact
    passed, reason = validate_exact('Exception_Catching_Throwing', original, candidate, request)
    proof = get_proof((((request.get('input') or {}).get('risk_result') or {}).get('result') or {}).get('evidence') or []) or {}
    if proof.get('ect_path') == 'NO_THROW_ASSERT_DOES_NOT_THROW':
        target = (proof.get('targets') or [{}])[0]
        evidence = {
            'target_id': target.get('target_id'),
            'throws_type': target.get('throws_type'),
            'throws_binding': target.get('throws_binding'),
            'body_hash': target.get('body_hash'),
            'body_ast_fingerprint': target.get('body_ast_fingerprint'),
            'statement_count': target.get('statement_count'),
            'helper_closure': proof.get('helper_closure'),
            'diagnostic_policy': proof.get('diagnostic_policy'),
        }
        return [check(name, passed, reason, evidence=evidence) for name in (
            'ECT_NOTHROW_SOURCE_BINDING_CURRENT',
            'ECT_NOTHROW_THROWS_CLAUSE_REMOVED',
            'ECT_NOTHROW_SINGLE_BOUND_API',
            'ECT_NOTHROW_COMPLETE_BODY_WRAPPED',
            'ECT_NOTHROW_STATEMENT_BIJECTION',
            'ECT_NOTHROW_BODY_AST_TOKENS_PRESERVED',
            'ECT_NOTHROW_CALLS_AND_METHOD_REFS_PRESERVED',
            'ECT_NOTHROW_ASSERTION_ASSUMPTION_SET_PRESERVED',
            'ECT_NOTHROW_CONTROL_FLOW_UNCHANGED',
            'ECT_NOTHROW_COMMENTS_PRESERVED',
            'ECT_NOTHROW_IMPORT_PLAN_EXACT',
            'ECT_NOTHROW_NO_NON_TARGET_CHANGE',
            'ECT_NOTHROW_EXPECTED_MATERIALIZATION',
        )]
    if ((proof.get('ect_path') == 'MUST_THROW_ASSERT_THROWS'
         and proof.get('ect_path_contract_version') == '2.0')
            or proof.get('ect_contract_version') == '2.0'):
        target = (proof.get('targets') or [{}])[0]
        evidence = {
            'target_id': target.get('target_id'),
            'exception_type': target.get('exception_type'),
            'protected_statement': target.get('protected_statement'),
            'exception_observations': target.get('exception_observations'),
            'flag': target.get('flag'),
        }
        # Exact source-bound replay is intentionally the shared predicate for
        # every facet: weakening one comparison must never let another green
        # check mask a changed exception type, target boundary, assertion, or
        # unrelated statement.
        return [
            check('ECT_V2_ASSERT_THROWS_EXCEPTION_TYPE', passed, reason, evidence=evidence),
            check('ECT_V2_PROTECTED_REGION_EXACT', passed, reason, evidence=evidence),
            check('ECT_V2_EXCEPTION_OBSERVATIONS_PRESERVED', passed, reason, evidence=evidence),
            check('ECT_V2_NON_TARGET_ASSERTIONS_PRESERVED', passed, reason, evidence=evidence),
            check('ECT_V2_PURE_FLAG_REMOVAL_ONLY', passed, reason, evidence=evidence),
            check('ECT_V2_NO_EXCEPTION_CONTROL_FLOW_DRIFT', passed, reason, evidence=evidence),
            check('EXACT_EXCEPTION_REGION', passed, reason, evidence=evidence),
        ]
    # Keep the historical check key, but strengthen it to the full region contract.
    trigger = extract_ect_trigger(candidate, from_assert_throws=True)
    return [check('TRIGGER_BOUNDARY_UNCHANGED', passed, reason, actual=trigger.get('expression')),
            check('EXACT_EXCEPTION_REGION', passed, reason)]


def validate_magic_number(original, candidate, request):
    from pipeline.agents.safety_contracts import validate_exact, get_proof, verify_proof
    from pipeline.agents.mnt_naming import pending_targets, materialize_names, naming_review_reason
    payload = request['input']
    evidence = payload['risk_result']['result']['evidence']
    proof = get_proof(evidence)
    checks = []
    if proof:
        ok, reason = verify_proof(evidence, payload['instance'], payload.get('context') or {})
        details = []
        name_review = None
        if ok:
            try:
                resolved = materialize_names(proof, payload.get('mnt_naming_proposals'), original,
                    (payload.get('context') or {}).get('test_class_source') or '', _collect_review=True)
                name_review = naming_review_reason(resolved)
                details = [{'target_id': t['target_id'], **t['naming_validation']}
                           for t in resolved['targets'] if t.get('naming_validation')]
                reason = 'MNT_ORIGINAL_CONTEXT_GROUNDING_VERIFIED'
            except (ValueError, TypeError, KeyError) as exc:
                ok, reason = False, str(exc)
        checks.append(check('MNT_TARGET_SELECTION', ok, reason,
            evidence={'selected_target_ids': resolved.get('selected_target_ids', []) if ok else [],
                      'retention_suggestions_are_proof': False}))
        if pending_targets(proof):
            checks.append(check('MNT_NAME_GROUNDING', ok and not name_review, name_review or reason, evidence=details))
    passed, reason = validate_exact('Magic_Number_Test', original, candidate, request)
    from pipeline.agents.mnt_comments import request_comment_check
    comment_result = request_comment_check(original, candidate, request)
    return checks + [check('MNT_COMMENT_INFORMATION', comment_result['decision'] == 'ALLOW', comment_result['reason_code'], evidence=comment_result),
                     check('MNT_BINDING_AND_REVERSE_INLINE', passed, reason)]
