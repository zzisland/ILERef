from __future__ import annotations

import json
import csv
import io
import hashlib
from pathlib import Path
from typing import Any

from pipeline.contracts.adapter_v41 import normalize_smell_name
from pipeline.agents.detector_identity import DetectorIdentityAmbiguous, canonical_detector_identity
from .method_identity import identify, VERSION


def load_schema_v41_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list) or any(x.get("schemaVersion") != "4.1" for x in payload):
        raise ValueError("Detection output is not a schema 4.1 record array.")
    return payload


def normalized_record(record: dict[str, Any]) -> dict[str, Any]:
    tc = record.get("testContext") or {}
    raw = tc.get("smellList") or []
    smells = sorted({normalize_smell_name(str(x)) for x in raw})
    package=tc.get("testPackageName") or ""
    class_name=Path(tc.get("testFilename") or "").stem
    split_filename=Path(str(tc.get("splitFilename") or "")).stem
    relative_path=str(tc.get("testRelativePath") or "").replace("\\", "/")
    located=identify(tc.get("testCode") or "",class_name,package)
    if located is None:
        raise DetectorIdentityAmbiguous('TARGET_METHOD_ATTRIBUTION_UNCERTAIN:NO_TEST')
    method_signature,method_proof=located
    split_relative_path="/".join(filter(None,("split",package.replace(".","/"),split_filename+".java"))) if split_filename else ""
    identity=canonical_detector_identity(
        project_id=str(record.get("projectName") or record.get("projectId") or ""),
        module_path=str(tc.get("modulePath") or ""), package_name=package,
        source_relative_path=relative_path, class_name=class_name,
        split_relative_path=split_relative_path, split_file_name=split_filename,
        method_signature=method_signature,
    )
    return {
        "package": package, "class": class_name,
        "split_filename": split_filename, "relative_path": relative_path,
        "method_signature": method_signature, "method": method_signature.split("(", 1)[0],
        "smells": smells, "test_code": tc.get("testCode") or "", "start_line": tc.get("testCodeStartLine"),
        "end_line": tc.get("testCodeEndLine"), "source_record": record,
        "canonical_identity": identity, "identity_completion_source": VERSION, "method_identity_proof":method_proof,
    }


def verify_execution_csvs(result_csv: Path, evidence: dict[str, Any]) -> bytes:
    """Consume the exact byte snapshot attested by this detector invocation."""
    if evidence.get('status')!='PASS' or evidence.get('input_unchanged') is not True:
        raise ValueError('DETECTOR_EXECUTION_EVIDENCE_MISSING')
    artifacts=[('output_csv_ref','output_hash',result_csv),
               ('failure_sidecar_ref','failure_sidecar_sha256',result_csv.with_name(result_csv.stem+'_failures.csv')),
               ('input_csv_ref','input_csv_sha256',None)]
    main=None
    for ref,hash_key,expected in artifacts:
        if not evidence.get(ref) or not evidence.get(hash_key) or not evidence.get('cwd'):
            raise ValueError('DETECTOR_EXECUTION_EVIDENCE_MISSING:'+ref)
        p=Path(evidence[ref]).resolve()
        if not p.is_relative_to(Path(evidence['cwd']).resolve()) or p.parent!=result_csv.resolve().parent or (expected is not None and p!=expected.resolve()):
            raise ValueError('DETECTOR_EVIDENCE_SCOPE_CONFLICT:'+ref)
        content=p.read_bytes()
        if hashlib.sha256(content).hexdigest()!=evidence[hash_key]:
            raise ValueError('DETECTOR_EVIDENCE_DRIFT:'+ref)
        if ref=='output_csv_ref':main=content
    return main


def load_phase1_detection_output(output_dir: Path, payload: dict[str, Any], *, execution_evidence=None) -> tuple[list[dict[str, Any]], Path]:
    """Adapt detection-only CSV plus the Phase 1 split sources.

    Detection-only mode intentionally skips schema JSON generation, but uses the
    identical preprocessor and detector. Split sources provide method identity;
    the CSV provides the real smell vector.
    """
    csvs=sorted(x for x in (Path(output_dir)/"detection").glob("Output_TestSmellDetection_*.csv") if not x.name.endswith("_failures.csv"))
    if len(csvs)!=1: raise ValueError("Phase 1 detector CSV is missing or non-unique.")
    snapshot=verify_execution_csvs(csvs[0],execution_evidence) if execution_evidence is not None else csvs[0].read_bytes()
    rows=list(csv.DictReader(io.StringIO(snapshot.decode('utf-8-sig'),newline='')))
    prefix=payload["target_class"]+"__"
    package_path=Path(*filter(None,payload["target_package"].split(".")))
    split_dir=Path(output_dir)/"split"/package_path
    records=[]
    metadata={"App","TestClass","TestFilePath","ProductionFilePath","RelativeTestFilePath","RelativeProductionFilePath","NumberOfMethods"}
    for split in sorted(split_dir.glob(prefix+"*.java")):
        expected_suffix=(Path("split")/package_path/split.name).as_posix().lower()
        short_candidates=[r for r in rows if Path(str(r.get("TestClass") or "")).stem==split.stem]
        exact=[r for r in short_candidates if _normalized_csv_path(r.get("TestFilePath")).lower().endswith(expected_suffix)]
        if len(exact)>1:
            raise DetectorIdentityAmbiguous(f"Duplicate detector rows for {expected_suffix}.")
        if len(exact)==1:
            row=exact[0]; completion_source="CSV_TEST_FILE_PATH"
        elif len(short_candidates)==1 and not _normalized_csv_path(short_candidates[0].get("TestFilePath")):
            row=short_candidates[0]; completion_source="TRUSTED_SPLIT_MANIFEST"
        elif short_candidates:
            raise DetectorIdentityAmbiguous(
                f"Detector split name {split.stem!r} is not unique for package {payload['target_package']!r}."
            )
        else:
            continue
        code=split.read_text(encoding="utf-8-sig")
        expected_methods=payload.get("expected_methods") or {}
        hint=(expected_methods.get(split.stem) or {}).get("method_signature")
        candidate_path=payload.get("candidate_file")
        candidate=Path(candidate_path).read_text(encoding="utf-8-sig") if candidate_path else None
        located=identify(code,payload["target_class"],payload["target_package"],hint,candidate)
        if located is None:
            continue
        signature,method_proof=located
        smells=sorted({normalize_smell_name(name) for name,value in row.items() if name not in metadata and str(value).strip()=="1"})
        split_relative_path=(Path("split")/package_path/split.name).as_posix()
        identity=canonical_detector_identity(
            project_id=str(payload.get("project_id") or row.get("App") or ""),
            module_path=str(payload.get("module_path") or ""),
            package_name=payload["target_package"], source_relative_path=payload["source_relative_path"],
            class_name=payload["target_class"], split_relative_path=split_relative_path,
            split_file_name=split.stem, method_signature=signature,
        )
        records.append({"package":payload["target_package"],"class":payload["target_class"],"split_filename":split.stem,
                         "relative_path":payload["source_relative_path"].replace("\\","/"),"method_signature":signature,
                         "method":signature.split("(",1)[0],"smells":smells,"test_code":code,"start_line":None,"end_line":None,"source_record":row,
                         "canonical_identity":identity,"identity_completion_source":VERSION,"csv_identity_completion_source":completion_source,"method_identity_proof":method_proof})
    if not records: raise ValueError("Detector output did not expose target-class split methods.")
    return records,csvs[-1]


def _signature(code: str) -> str:
    located=identify(code)
    if located is None: raise DetectorIdentityAmbiguous('TARGET_METHOD_ATTRIBUTION_UNCERTAIN:NO_TEST')
    return located[0]


def _normalized_csv_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").lstrip("./")
