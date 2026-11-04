from __future__ import annotations

import re
from typing import Any

from .java_assertions import (
    JavaAssertionParseError,
    SUPPORTED_ASSERTIONS,
    find_assertion_calls,
)


RULE_VERSION = "4.1"
ASSERTION_NAME_PATTERN = re.compile(r"\b(assert[A-Z]\w*)\s*\(")


def extract_rule_evidence(instance: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    from .safety_contracts import proof_evidence
    complete = proof_evidence(instance, context)
    smell = instance["smell_type"]
    try:
        if smell == "Assertion_Roulette":
            evidence = _assertion_evidence(instance, context)
        elif smell == "Redundant_Assertion":
            proof = complete['contract']
            evidence = [_call_evidence(instance, call, f'RA-{index:03d}', 'RA-PROVEN-STATEMENT', call.get('conclusion', 'CONFIRMED_REDUNDANT'))
                        for index, call in enumerate(proof['targets'], 1)]
            evidence.append(_evidence(instance, 'RA-ORACLE-GUARD', 'ORACLE_COUNT',
                'remaining_proven_oracle_ids=' + repr(proof.get('remaining_oracle_ids', [])), 1.0))
            evidence.append(_evidence(instance, 'RA-SEMANTIC-CATEGORY', 'RA_SEMANTIC_CLASSIFICATION',
                proof.get('ra_semantic_category', 'BINDING_UNPROVEN'), 1.0))
            from .safety_contracts import current_rule_version
            for item in evidence:
                item['rule_version'] = current_rule_version(smell)
        elif smell == "Exception_Catching_Throwing":
            evidence = []  # Complete target-region proof replaces method-wide regex flags.
        else:
            evidence = []
    except (JavaAssertionParseError, ValueError):
        evidence = []
    return evidence + [complete]


def _assertion_evidence(instance: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        calls = find_assertion_calls(instance["test_code"])
    except JavaAssertionParseError:
        calls = []
    from .safety_contracts import Java, call_node, overload, resolve_api
    java = Java(instance['test_code'])
    owner = context.get('test_class_source') or ''
    junit5 = bool(calls) and all(resolve_api(java, call_node(java, c), owner) for c in calls)
    evidence = [_context_evidence(instance, "JUNIT5_ASSERTIONS_AVAILABLE", junit5)]
    if not calls:
        evidence.append(_evidence(instance, "AR-NO-ASSERTION", "NO_ASSERTION", "No assertion call was parsed.", 0.95))
        return evidence
    for index, call in enumerate(calls, 1):
        name = call["name"]
        args = call["arguments"]
        if not resolve_api(java, call_node(java, call), owner):
            conclusion = 'ASSERTION_API_BINDING_UNKNOWN'
        elif call.get("unsupported_reasons"):
            conclusion = (
                "UNSUPPORTED_ASSERTION_API"
                if "UNSUPPORTED_ASSERTION_API" in call["unsupported_reasons"]
                else "UNSUPPORTED_ASSERTION_CONTEXT"
            )
        elif name not in SUPPORTED_ASSERTIONS:
            conclusion = "UNSUPPORTED_ASSERTION_API"
        elif args is None:
            conclusion = "UNPARSEABLE_ASSERTION"
        else:
            conclusion = overload(java, call)
        evidence.append(_call_evidence(instance, call, f"AR-{index:03d}", "AR-JUNIT5-MESSAGE", conclusion))
    return evidence


def _redundant_evidence(instance: dict[str, Any]) -> list[dict[str, Any]]:
    calls = find_assertion_calls(instance["test_code"])
    evidence: list[dict[str, Any]] = []
    redundant_count = 0
    other_oracles = 0
    for index, call in enumerate(calls, 1):
        conclusion = classify_redundant_call(call)
        if conclusion == "CONFIRMED_REDUNDANT":
            redundant_count += 1
        elif conclusion == "VALID_ORACLE":
            other_oracles += 1
        evidence.append(_call_evidence(instance, call, f"RA-{index:03d}", "RA-SIDE-EFFECT-FREE", conclusion))
    evidence.append(_evidence(
        instance,
        "RA-ORACLE-GUARD",
        "ORACLE_COUNT",
        f"redundant={redundant_count}; remaining_valid_oracles={other_oracles}",
        1.0,
    ))
    return evidence


def classify_redundant_call(call: dict[str, Any]) -> str:
    name, args = call["name"], call["arguments"]
    if args is None:
        return "UNPARSEABLE_ASSERTION"
    if (name == "assertTrue" and args == ["true"]) or (name == "assertFalse" and args == ["false"]) or (name == "assertNull" and args == ["null"]):
        return "CONFIRMED_REDUNDANT"
    if name in {"assertEquals", "assertSame"} and len(args) == 2 and args[0] == args[1]:
        return "POSSIBLE_SIDE_EFFECT"
    if name == "assertNotNull" and len(args) == 1 and re.fullmatch(r"new\s+[A-Za-z_$][\w.$<>]*\s*\(\s*\)", args[0]):
        return "POSSIBLE_SIDE_EFFECT"
    return "VALID_ORACLE" if name in SUPPORTED_ASSERTIONS else "UNSUPPORTED_ASSERTION_API"


def _exception_evidence(instance: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    source = instance["test_code"]
    masked = strip_comments_and_literals(source)
    facts = {
        "HAS_ASSERT_THROWS": bool(re.search(r"\bassertThrows\s*\(", masked)),
        "HAS_TRY": bool(re.search(r"\btry\s*\{", masked)),
        "HAS_FAIL": bool(re.search(r"\bfail\s*\(", masked)),
        "HAS_FINALLY": bool(re.search(r"\bfinally\s*\{", masked)),
        "HAS_EXPLICIT_THROW": bool(re.search(r"\bthrow\s+", masked)),
        "HAS_COMPLEX_CONTROL_FLOW": bool(re.search(r"\b(?:return|break|continue)\b", masked)),
        "ASSERT_THROWS_AVAILABLE": confirms_assert_throws(context.get("test_class_source", ""), source),
    }
    header = masked.split("{", 1)[0]
    facts["HAS_THROWS_DECLARATION"] = bool(re.search(r"\bthrows\b", header))
    facts["TRY_COUNT"] = len(re.findall(r"\btry\s*\{", masked))

    catch = re.search(r"\bcatch\s*\(\s*([^)]*)\)\s*\{", masked)
    if catch:
        catch_end = matching_delimiter(masked, catch.end() - 1, "{", "}")
        catch_body = masked[catch.end():catch_end] if catch_end is not None else ""
        facts["EXCEPTION_TYPE_KNOWN"] = bool(re.match(r"(?:final\s+)?[A-Za-z_$][\w.$<>| ]*\s+[A-Za-z_$]\w*$", catch.group(1).strip()))
        facts["CATCH_HAS_ADDITIONAL_ASSERTION"] = bool(ASSERTION_NAME_PATTERN.search(catch_body))
        facts["CATCH_HAS_SIDE_EFFECT"] = bool(catch_body.strip())
    else:
        facts["EXCEPTION_TYPE_KNOWN"] = False
        facts["CATCH_HAS_ADDITIONAL_ASSERTION"] = False
        facts["CATCH_HAS_SIDE_EFFECT"] = False

    try_match = re.search(r"\btry\s*\{", masked)
    if try_match:
        try_end = matching_delimiter(masked, try_match.end() - 1, "{", "}")
        try_body = masked[try_match.end():try_end] if try_end is not None else ""
        without_fail = re.sub(r"\bfail\s*\([^;]*\)\s*;", "", try_body)
        statements = [item.strip() for item in without_fail.split(";") if item.strip()]
        facts["TRIGGER_STATEMENT_COUNT"] = len(statements)
        facts["TRIGGER_BOUNDARY_CLEAR"] = len(statements) == 1
    else:
        facts["TRIGGER_STATEMENT_COUNT"] = 0
        facts["TRIGGER_BOUNDARY_CLEAR"] = False

    located_keys = {"HAS_TRY", "HAS_FAIL", "TRIGGER_BOUNDARY_CLEAR"}
    method_lines = instance.get("line_range") or {}
    return [
        _evidence(
            instance, f"ECT-{index:03d}", "SOURCE_FACT",
            f"{key}:{str(value).lower()}", 1.0, rule_id=f"ECT-{key}",
            start_line=method_lines.get("start") if key in located_keys and value else None,
            end_line=method_lines.get("end") if key in located_keys and value else None,
            matched_code=source if key in located_keys and value else None,
        )
        for index, (key, value) in enumerate(sorted(facts.items()), 1)
    ]


def confirms_junit5(class_source: str, method_source: str) -> bool:
    return bool(
        re.search(r"import\s+static\s+org\.junit\.jupiter\.api\.Assertions\.(?:\*|\w+)\s*;", class_source)
        or re.search(r"\borg\.junit\.jupiter\.api\.Assertions\.assert", method_source)
    )


def confirms_assert_throws(class_source: str, method_source: str) -> bool:
    return bool(
        re.search(r"import\s+static\s+org\.junit\.jupiter\.api\.Assertions\.(?:\*|assertThrows)\s*;", class_source)
        or "org.junit.jupiter.api.Assertions.assertThrows" in method_source
        or re.search(r"\bassertThrows\s*\(", class_source)
    )


def _context_evidence(instance: dict[str, Any], conclusion: str, available: bool) -> dict[str, Any]:
    return _evidence(instance, "CTX-JUNIT-001", "CONTEXT", conclusion if available else "JUNIT_CONTEXT_MISSING", 1.0, source_available=available)


def _call_evidence(instance: dict[str, Any], call: dict[str, Any], evidence_id: str, rule_id: str, conclusion: str) -> dict[str, Any]:
    start, end = evidence_lines(instance, call.get("start"), call.get("end"))
    return _evidence(
        instance,
        evidence_id,
        "SOURCE_PATTERN",
        conclusion,
        1.0,
        rule_id=rule_id,
        start_line=start,
        end_line=end,
        matched_code=call.get("code"),
        assertion_identity=call.get("assertion_id"),
        parent_assertion_id=call.get("parent_assertion_id"),
        nesting_depth=call.get("nesting_depth"),
        ast_path=call.get("ast_path"),
        control_flow_path=call.get("control_flow_path") or [],
        assertion_api=call.get("name"),
    )


def _evidence(
    instance: dict[str, Any], evidence_id: str, evidence_type: str, conclusion: str,
    confidence: float, *, rule_id: str = "RISK-COMMON", start_line: int | None = None,
    end_line: int | None = None, matched_code: str | None = None,
    source_available: bool = True, assertion_identity: str | None = None,
    parent_assertion_id: str | None = None, nesting_depth: int | None = None,
    ast_path: str | None = None, control_flow_path: list[str] | None = None,
    assertion_api: str | None = None,
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "evidence_type": evidence_type,
        "source_file": instance.get("test_relative_path") or None,
        "method_signature": instance.get("method_signature") or None,
        "start_line": start_line,
        "end_line": end_line,
        "matched_code": matched_code,
        "rule_id": rule_id,
        "rule_version": RULE_VERSION,
        "conclusion": conclusion,
        "confidence": confidence,
        "source_available": source_available,
        "assertion_identity": assertion_identity,
        "parent_assertion_id": parent_assertion_id,
        "nesting_depth": nesting_depth,
        "ast_path": ast_path,
        "control_flow_path": list(control_flow_path or []),
        "assertion_api": assertion_api,
    }


def evidence_lines(instance: dict[str, Any], start: int | None, end: int | None) -> tuple[int | None, int | None]:
    base = (instance.get("line_range") or {}).get("start")
    if not isinstance(base, int) or base <= 0 or start is None:
        return None, None
    source = instance["test_code"]
    start_line = base + source[:start].count("\n")
    end_line = base + source[:end].count("\n") if end is not None else start_line
    return start_line, end_line


def is_simple_reference(value: str) -> bool:
    return bool(re.fullmatch(r"(?:this\.)?[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*", value))


def is_message_argument(value: str) -> bool:
    stripped = value.strip()
    return bool(
        re.fullmatch(r'"(?:\\.|[^"\\])*"', stripped, re.DOTALL)
        or "->" in stripped
    )


def matching_delimiter(source: str, opening: int, open_char: str, close_char: str) -> int | None:
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == open_char:
            depth += 1
        elif source[index] == close_char:
            depth -= 1
            if depth == 0:
                return index
    return None


def split_arguments(source: str, masked: str, start: int, end: int) -> list[str]:
    if not masked[start:end].strip():
        return []
    result, argument_start = [], start
    round_depth = square_depth = brace_depth = 0
    for index in range(start, end):
        char = masked[index]
        if char == "(": round_depth += 1
        elif char == ")": round_depth -= 1
        elif char == "[": square_depth += 1
        elif char == "]": square_depth -= 1
        elif char == "{": brace_depth += 1
        elif char == "}": brace_depth -= 1
        elif char == "," and round_depth == square_depth == brace_depth == 0:
            result.append(source[argument_start:index].strip())
            argument_start = index + 1
    result.append(source[argument_start:end].strip())
    return result


def strip_comments_and_literals(source: str) -> str:
    result, index, state = [], 0, "code"
    while index < len(source):
        current = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "code":
            if current == "/" and following == "/": state = "line"; result.extend("  "); index += 2; continue
            if current == "/" and following == "*": state = "block"; result.extend("  "); index += 2; continue
            if current == '"': state = "string"; result.append(" ")
            elif current == "'": state = "char"; result.append(" ")
            else: result.append(current)
        elif state == "line":
            result.append("\n" if current == "\n" else " ")
            if current == "\n": state = "code"
        elif state == "block":
            if current == "*" and following == "/": state = "code"; result.extend("  "); index += 2; continue
            result.append("\n" if current == "\n" else " ")
        else:
            quote = '"' if state == "string" else "'"
            if current == "\\" and following: result.extend("  "); index += 2; continue
            if current == quote: state = "code"
            result.append("\n" if current == "\n" else " ")
        index += 1
    return "".join(result)
