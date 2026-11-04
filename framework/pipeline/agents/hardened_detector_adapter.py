from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Iterable

from pipeline.stage_cache import stable_sha256


# User-approved replacement, 2026-09-05. Keep exact pins: do not learn trust
# from whichever binary happens to be installed at runtime.
EXPECTED_DETECTOR_SHA256 = "7d89be5e7aab461723bd4b7057a9daf0e3e0595862cf684ca667b8f451b0f3df"
EXPECTED_CONTENT_FINGERPRINT = "4746001880d0ac55d267dd09e1ca62c9ceaf5e31a9571f945501d39a3833ac0f"
CONTENT_FINGERPRINT_SOURCE = "RECOMPUTED_ZIP_ENTRY_MANIFEST_SHA256_V1"


class DetectorIdentityError(RuntimeError):
    pass


class DetectorOutputError(RuntimeError):
    pass


def _content_fingerprint(archive: zipfile.ZipFile) -> str:
    """Hash compact UTF-8 JSON of sorted [name, size, SHA256(bytes)] entries.

    Includes every non-directory entry (also META-INF/MANIFEST.MF), excludes
    ZIP timestamps/compression metadata, and rejects duplicate entry names.
    This is our documented content identity, not the old build fingerprint.
    """
    entries = [entry for entry in archive.infolist() if not entry.is_dir()]
    if len({entry.filename for entry in entries}) != len(entries):
        raise DetectorIdentityError("DETECTOR_JAR_DUPLICATE_ENTRIES")
    manifest = sorted((entry.filename, entry.file_size,
                       hashlib.sha256(archive.read(entry)).hexdigest()) for entry in entries)
    payload = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class DetectorBinaryIdentity:
    detector_jar_path: str
    detector_jar_sha256: str
    detector_content_fingerprint: str
    detector_content_fingerprint_source: str
    detector_main_class: str
    identity_status: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def inspect_detector_binary(detector_jar: Path, *, expected_sha256: str = EXPECTED_DETECTOR_SHA256,
                            expected_content_fingerprint: str = EXPECTED_CONTENT_FINGERPRINT) -> DetectorBinaryIdentity:
    path=Path(detector_jar).resolve()
    if not path.is_file():
        raise DetectorIdentityError(f"Configured detector JAR does not exist: {path}")
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise DetectorIdentityError(f"DETECTOR_JAR_HASH_MISMATCH: expected {expected_sha256}, actual {digest}")
    try:
        with zipfile.ZipFile(path) as archive:
            manifest=archive.read("META-INF/MANIFEST.MF").decode("utf-8",errors="replace")
            content_fingerprint = _content_fingerprint(archive)
    except (OSError,KeyError,zipfile.BadZipFile) as exc:
        raise DetectorIdentityError(f"DETECTOR_JAR_INVALID: {exc}") from exc
    attributes={}
    for raw in manifest.replace("\r\n","\n").splitlines():
        if ":" in raw:
            key,value=raw.split(":",1); attributes[key.strip()]=value.strip()
    main=attributes.get("Main-Class") or ""
    if main != "Main":
        raise DetectorIdentityError(f"DETECTOR_MAIN_CLASS_MISMATCH: expected Main, actual {main or '<missing>'}")
    if content_fingerprint != expected_content_fingerprint:
        raise DetectorIdentityError(
            f"DETECTOR_CONTENT_FINGERPRINT_MISMATCH: expected {expected_content_fingerprint}, actual {content_fingerprint}"
        )
    return DetectorBinaryIdentity(
        detector_jar_path=str(path), detector_jar_sha256=digest,
        detector_content_fingerprint=content_fingerprint,
        detector_content_fingerprint_source=CONTENT_FINGERPRINT_SOURCE,
        detector_main_class=main, identity_status="VERIFIED",
    )


class HardenedDetectorAdapter:
    """Direct, identity-pinned adapter used by synthetic tests and runner guards."""

    version = "hardened-detector-adapter-v2"

    def __init__(self, detector_jar: Path, *, java_executable: str | None = None,
                 expected_sha256: str = EXPECTED_DETECTOR_SHA256):
        self.identity=inspect_detector_binary(detector_jar,expected_sha256=expected_sha256)
        self.java_executable=java_executable or shutil.which("java") or "java"

    def write_input(self, path: Path, rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
        path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
        normalized=[]
        with path.open("w",encoding="utf-8",newline="") as handle:
            writer=csv.writer(handle,quoting=csv.QUOTE_MINIMAL,lineterminator="\n")
            for row in rows:
                values=(str(row.get("project") or ""),str(row.get("test_file") or ""),str(row.get("production_file") or ""))
                if not values[0] or not values[1]:
                    raise ValueError("Detector input requires project and test_file.")
                writer.writerow(values); normalized.append(values)
        return {"path":str(path.resolve()),"row_count":len(normalized),
                "input_manifest_hash":stable_sha256(normalized),"input_file_sha256":_file_sha(path)}

    def run_direct(self, *, input_csv: Path, output_dir: Path, timeout_seconds: int = 120) -> dict[str, Any]:
        input_csv=Path(input_csv).resolve(); output_dir=Path(output_dir).resolve(); output_dir.mkdir(parents=True,exist_ok=True)
        before={path.resolve() for path in output_dir.glob("Output_TestSmellDetection_*.csv")}
        command=[self.java_executable,"-jar",self.identity.detector_jar_path,str(input_csv)]
        started=datetime.now(timezone.utc).isoformat(); clock=monotonic()
        try:
            completed=subprocess.run(command,cwd=output_dir,text=True,encoding="utf-8",errors="replace",
                                     capture_output=True,timeout=timeout_seconds,shell=False,check=False,
                                     env=_safe_environment())
        except (OSError,subprocess.TimeoutExpired) as exc:
            return self._result(command,started,clock,None,"DETECTOR_FAILED",str(exc),None,"",str(exc),input_csv)
        fresh=[path.resolve() for path in output_dir.glob("Output_TestSmellDetection_*.csv")
               if path.resolve() not in before and not path.name.endswith("_failures.csv")]
        if completed.returncode != 0:
            return self._result(command,started,clock,completed.returncode,"DETECTOR_FAILED",
                                "Detector process exited non-zero.",None,completed.stdout,completed.stderr,input_csv)
        if len(fresh) != 1:
            return self._result(command,started,clock,completed.returncode,"DETECTOR_RESULT_INVALID",
                                f"Expected one fresh result CSV, found {len(fresh)}.",None,completed.stdout,completed.stderr,input_csv)
        result_csv=fresh[0]
        try:
            rows=self.read_output(result_csv)
        except (OSError,UnicodeError,csv.Error,DetectorOutputError) as exc:
            return self._result(command,started,clock,completed.returncode,"DETECTOR_RESULT_INVALID",str(exc),
                                result_csv,completed.stdout,completed.stderr,input_csv)
        failures=result_csv.with_name(result_csv.stem+"_failures.csv")
        if failures.is_file() and failures.stat().st_size:
            try:
                failure_rows=self.read_output(failures,allow_any_header=True)
            except Exception as exc:
                return self._result(command,started,clock,completed.returncode,"DETECTOR_RESULT_INVALID",str(exc),
                                    result_csv,completed.stdout,completed.stderr,input_csv)
            if failure_rows:
                return self._result(command,started,clock,completed.returncode,"DETECTOR_ANALYSIS_FAILED",
                                    f"Detector reported {len(failure_rows)} failed input row(s).",result_csv,
                                    completed.stdout,completed.stderr,input_csv,rows=rows)
        return self._result(command,started,clock,completed.returncode,"PASS","Detector completed and CSV parsed.",
                            result_csv,completed.stdout,completed.stderr,input_csv,rows=rows)

    @staticmethod
    def read_output(path: Path, *, allow_any_header: bool = False) -> list[dict[str, str]]:
        with Path(path).open("r",encoding="utf-8-sig",newline="") as handle:
            reader=csv.DictReader(handle)
            if not reader.fieldnames or any(name is None or not str(name).strip() for name in reader.fieldnames):
                raise DetectorOutputError("Detector CSV header is missing or malformed.")
            if not allow_any_header and not {"App","TestClass"}.issubset(reader.fieldnames):
                raise DetectorOutputError("Detector CSV lacks required App/TestClass columns.")
            rows=list(reader)
        if any(None in row for row in rows):
            raise DetectorOutputError("Detector CSV contains columns outside its declared header.")
        return rows

    def _result(self,command,started,clock,exit_code,status,summary,result_csv,stdout,stderr,input_csv,rows=None):
        return {**self.identity.to_dict(),"adapter_version":self.version,
                "detector_command":[Path(command[0]).name,*command[1:]],"detector_exit_code":exit_code,
                "status":status,"summary":summary,"started_at":started,
                "completed_at":datetime.now(timezone.utc).isoformat(),"duration_ms":int((monotonic()-clock)*1000),
                "input_manifest_hash":_input_manifest_hash(input_csv),"input_file_sha256":_file_sha(input_csv),
                "output_path":str(result_csv) if result_csv else None,
                "output_hash":_file_sha(result_csv) if result_csv else None,"record_count":len(rows or []),
                "records":rows or [],"stdout":_redact(stdout),"stderr":_redact(stderr)}


def _file_sha(path: Path | None) -> str | None:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path and Path(path).is_file() else None


def _input_manifest_hash(path: Path) -> str | None:
    if not Path(path).is_file(): return None
    with Path(path).open("r",encoding="utf-8-sig",newline="") as handle:
        return stable_sha256([row for row in csv.reader(handle)])


def _safe_environment() -> dict[str,str]:
    blocked=("KEY","TOKEN","SECRET","PASSWORD","AUTH")
    return {key:value for key,value in os.environ.items() if not any(item in key.upper() for item in blocked)}


def _redact(value: str) -> str:
    import re
    return re.sub(r"(?i)(api[_-]?key|authorization|token|password|secret)\s*[:=]\s*\S+",r"\1=<redacted>",value or "")
