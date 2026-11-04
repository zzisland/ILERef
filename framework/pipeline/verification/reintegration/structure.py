from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pipeline.agents.candidate_validation import ANNOTATION_PATTERN, FORBIDDEN_TOP_LEVEL, METHOD_PATTERN, PLACEHOLDER, extract_single_method
from pipeline.agents.risk_evidence import strip_comments_and_literals
from pipeline.agents.refactoring_contract import normalized_source, sha256_text
from pipeline.contracts.adapter_v41 import extract_method_signature

from pipeline.agents.validation_contract import sha256_file


def _check(cid, passed, message, evidence=None, expected=None, actual=None, severity="ERROR"):
    return {"check_id": cid, "status": "PASS" if passed else "FAIL", "severity": severity, "message": message, "evidence": evidence, "expected": expected, "actual": actual}


class ValidationStructureValidator:
    version = "4A.1-inherited-source-regions"

    def validate(self, payload: dict[str, Any], refactoring_result: dict[str, Any]) -> dict[str, Any]:
        source = Path(payload["source_test_file"]); candidate_file = Path(payload["candidate_file_ref"]); candidate_method = Path(payload["candidate_method_ref"])
        checks=[]
        checks.append(_check("PIPELINE_STATE", payload.get("pipeline_state")=="CANDIDATE_GENERATED", "Only CANDIDATE_GENERATED may validate."))
        checks.append(_check("REFACTORING_DECISION", refactoring_result["result"].get("decision")=="CANDIDATE_GENERATED", "Phase 3 decision is CANDIDATE_GENERATED."))
        checks.append(_check("SOURCE_EXISTS", source.is_file(), "Original source exists.")); checks.append(_check("CANDIDATE_FILE_EXISTS", candidate_file.is_file() and candidate_file.stat().st_size>0 if candidate_file.exists() else False, "Candidate file exists and is non-empty."))
        checks.append(_check("CANDIDATE_METHOD_EXISTS", candidate_method.is_file() and candidate_method.stat().st_size>0 if candidate_method.exists() else False, "Candidate method exists and is non-empty."))
        if not all(p.is_file() for p in (source,candidate_file,candidate_method)): return {"passed":False,"checks":checks}
        source_text=source.read_text(encoding="utf-8"); file_text=candidate_file.read_text(encoding="utf-8"); method_text=candidate_method.read_text(encoding="utf-8")
        from .placeholders import file_placeholder_evidence
        placeholder_evidence = file_placeholder_evidence(source_text, file_text)
        checks += [
            _check("SOURCE_HASH", sha256_text(source_text)==payload["source_test_file_hash"], "Original source hash matches Phase 3.", expected=payload["source_test_file_hash"], actual=sha256_text(source_text)),
            _check("CANDIDATE_FILE_HASH", sha256_text(file_text)==payload["candidate_file_hash"], "Candidate file hash matches Phase 3."),
            _check("CANDIDATE_METHOD_HASH", sha256_text(method_text)==payload["candidate_method_hash"], "Candidate method hash matches Phase 3."),
            _check("DISTINCT_PATHS", source.resolve()!=candidate_file.resolve(), "Candidate and original source paths differ."),
            _check("NO_MARKDOWN_OR_PLACEHOLDER", placeholder_evidence['passed'], "Candidate introduces no unexempted Markdown or placeholder residue.", evidence=placeholder_evidence),
        ]
        contract=refactoring_result['result']['risk_contract']
        mnt=contract.get('smell')=='Magic_Number_Test'
        extracted, extraction_checks=extract_single_method(method_text, ast_scope=mnt); checks.extend(extraction_checks)
        method_occurrences=file_text.count(method_text.strip())
        if mnt:
            from pipeline.agents.method_location import verify_location
            from pipeline.agents.refactoring_contract import replace_method_in_copy
            try:
                verify_location(contract,source_text,method_text,file_text)
                location_ok=True
            except (ValueError,KeyError,TypeError):location_ok=False
            checks.append(_check('MNT_METHOD_LOCATION',location_ok,'Owner, signature, instance and range identify one method.'))
        else:
            checks.append(_check("METHOD_FILE_CONSISTENCY", method_occurrences==1, "Candidate method occurs exactly once in candidate file.", expected=1, actual=method_occurrences))
        try: signature=extract_method_signature(method_text)
        except ValueError: signature=None
        checks.append(_check("METHOD_SIGNATURE", signature==payload["method_signature"], "Candidate method signature matches contract.", expected=payload["method_signature"], actual=signature))
        contract=refactoring_result["result"]["risk_contract"]; start,end=contract["method_start_line"],contract["method_end_line"]
        from pipeline.agents.safety_contracts import replay_saved_contract
        replayed, replay_reason = replay_saved_contract(contract, method_text, source_text)
        checks.append(_check('CURRENT_SAFETY_CONTRACT', replayed, replay_reason))
        original_lines=source_text.splitlines(keepends=True); prefix="".join(original_lines[:start-1]); suffix="".join(original_lines[end:])
        from pipeline.agents.refactoring_contract import replace_method_in_copy
        outside_ok=file_text==replace_method_in_copy(source_text,method_text,start,end)
        checks.append(_check("ONLY_TARGET_METHOD_DIFF", outside_ok, "Package, imports, class structure, helpers, and other methods are unchanged."))
        original_method="".join(original_lines[start-1:end])
        checks.append(_check("TEST_ANNOTATIONS", ANNOTATION_PATTERN.findall(method_text)==ANNOTATION_PATTERN.findall(original_method), "Test annotations match the original method.", expected=ANNOTATION_PATTERN.findall(original_method), actual=ANNOTATION_PATTERN.findall(method_text)))
        method_name=re.escape(payload["target_test_method"])
        unique_count=len(re.findall(r"\b"+method_name+r"\s*\(",strip_comments_and_literals(file_text)))
        checks.append(_check("TARGET_METHOD_UNIQUE", location_ok if mnt else unique_count==1, "Target method is uniquely located in its owning class.", expected=1, actual=1 if mnt and location_ok else unique_count))
        masked_file=strip_comments_and_literals(file_text)
        checks.append(_check("JAVA_STRUCTURE_BALANCED", masked_file.count("{")==masked_file.count("}") and masked_file.count("(")==masked_file.count(")"), "Candidate Java delimiters are balanced."))
        method_identity = (
            sha256_text(payload["original_method_source"]) == payload["original_method_hash"]
            and normalized_source(original_method) == normalized_source(payload["original_method_source"])
        )
        checks.append(_check("ORIGINAL_METHOD_HASH", method_identity, "Original method source and Phase 3 hash still match."))
        return {"passed": all(x["status"]=="PASS" for x in checks if x["severity"]=="ERROR"), "checks":checks}
