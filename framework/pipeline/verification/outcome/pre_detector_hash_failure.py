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


def pre_detector_hash_failure(expected,validation_input,injected,validated,current):
    from pipeline.hashing import file_hash_chain_failure
    return file_hash_chain_failure(expected,validation_input,injected,validated,current)

