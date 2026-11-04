from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

from pipeline.agents.base import load_revalidation_agent_config, load_validation_agent_config
from pipeline.agents.detection_result_adapter import load_phase1_detection_output, load_schema_v41_records, normalized_record
from pipeline.agents.detector_runner import Phase1DetectorRunner
from pipeline.agents.refactoring_contract import sha256_text
from pipeline.agents.test_report_parser import parse_maven_test_reports, snapshot_maven_test_reports, execution_confirmed
from pipeline.agents.validation_contract import nearest_maven_root, sha256_file
from pipeline.agents.validation_runner import MavenValidationRunner
from pipeline.agents.validation_types import BLOCKED_STATUSES
from pipeline.agents.validation_workspace import IsolatedValidationWorkspace, project_input_sha256
from pipeline.agents.risk_evidence import strip_comments_and_literals
from pipeline.agents.instance_revalidation import canonicalize_records, revalidate_instances
from pipeline.file_candidate import FileCandidateBundle
from pipeline.hashing import HASH_CHAIN_VERSION, sha256_file_bytes


def structure_checks(bundle,candidate_path,targets):
    current=bundle.source_file.read_text(encoding="utf-8") if bundle.source_file.is_file() else ""
    candidate=candidate_path.read_text(encoding="utf-8") if candidate_path.is_file() else ""
    structural_source=strip_comments_and_literals(candidate)
    target_group_ids={str(x.get("method_group_id") or x.get("instance_id") or "") for x in targets}
    bundle_group_ids={str(x.get("method_group_id") or x.get("instance_id") or "") for x in bundle.methods}
    target_mapping_ok=(bool(targets) and target_group_ids==bundle_group_ids)
    from pipeline.agents.safety_contracts import replay_method_chain
    contract_ok = all(replay_method_chain(method, [t for t in targets if
        str(t.get('method_group_id') or t.get('instance_id')) == str(method.get('method_group_id') or method.get('instance_id'))], current)
        for method in bundle.methods)
    from pipeline.file_candidate import assemble_method_candidates, FileCandidateError
    try:
        exact_file=candidate==assemble_method_candidates(bundle.source_file,list(bundle.methods)).candidate_source
    except (FileCandidateError,ValueError):
        exact_file=False
    values=[
        ('EXACT_COMBINED_CANDIDATE_FILE',exact_file),
        ('CURRENT_SAFETY_CONTRACT_CHAIN', contract_ok),
        ("SOURCE_HASH",sha256_text(current)==bundle.source_hash),
        ("CANDIDATE_HASH",sha256_text(candidate)==bundle.candidate_hash),
        ("SOURCE_CANDIDATE_DISTINCT",bundle.source_file.resolve()!=candidate_path.resolve()),
        # In multi-smell mode several atomic smell instances legitimately
        # point at one final method candidate.  Compare method-group
        # identities instead of forcing instance_count == method_count.
        ("TARGETS_PRESENT",target_mapping_ok),
        ("NO_MARKDOWN_OR_PLACEHOLDER","```" not in candidate and "<PLACEHOLDER>" not in candidate),
        ("JAVA_STRUCTURE_BALANCED",structural_source.count("{")==structural_source.count("}") and structural_source.count("(")==structural_source.count(")")),
    ]
    return [{"check_id":cid,"status":"PASS" if ok else "FAIL","severity":"ERROR","message":cid.replace("_"," ").title()} for cid,ok in values]

