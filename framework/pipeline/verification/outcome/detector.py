from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import hashlib
import csv
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

from pipeline.agents.validation_runner import redact
from pipeline.agents.hardened_detector_adapter import HardenedDetectorAdapter, inspect_detector_binary


class Phase1DetectorRunner:
    version = "5.1"

    def __init__(self, *, repo_root: Path | None = None, max_output_chars: int = 20000):
        self.repo_root = (repo_root or Path(__file__).resolve().parents[3]).resolve()
        self.max_output_chars = max_output_chars
        self._binary_identity: dict[str, Any] | None = None
        self._scopes: dict[Path, Path] = {}

    def run(self, *, workspace: Path, run_id: str, project_name: str, detector_jar: Path,
            timeout_seconds: int) -> tuple[dict[str, Any], str, str, Path | None]:
        execution_cwd=None
        binding={"logical_run_id":run_id,"logical_project_name":project_name}
        def finish(*args):
            result=self._result(*args)
            result.update(binding)
            result["cwd"]=str(execution_cwd or self.repo_root)
            return result
        try:
            binary_identity=inspect_detector_binary(detector_jar).to_dict()
            self._binary_identity=binary_identity
        except Exception as exc:
            command=["mvn","-q","exec:java","-Dexec.mainClass=Main","-Dutrefactor.mode=detect"]
            started=datetime.now(timezone.utc).isoformat(); clock=monotonic()
            result=finish(command,started,clock,None,False,"BLOCKED","DETECTOR_IDENTITY_MISMATCH",str(exc))
            result["detector_identity"]={"status":"FAILED","error":str(exc)}
            return result,"",redact(str(exc)),None
        executable = shutil.which("mvn")
        command = [executable or "mvn", "-f", str(self.repo_root/"pom.xml"), "-q", "exec:java", "-Dexec.mainClass=Main", "-Dutrefactor.mode=detect"]
        started = datetime.now(timezone.utc).isoformat(); clock = monotonic()
        if not executable:
            return finish(command, started, clock, None, False, "BLOCKED", "MAVEN_BLOCKED", "Maven executable was not found."), "", "", None
        try:
            execution_cwd=Path(tempfile.mkdtemp(prefix="fd-",dir=os.environ.get("FULL_DETECTOR_TMP_ROOT"))).resolve()
            # The frontend and JAR both use user.dir. Maven -f keeps the compiled
            # frontend/dependencies while user.dir belongs only to this invocation.
            output_dir=execution_cwd/"data"/"r"/project_name
            output_dir.mkdir(parents=True)
            self._scopes[output_dir.resolve()]=execution_cwd
            env = self._environment(Path(workspace).resolve(), "r", project_name, Path(detector_jar).resolve())
            input_hashes={str(p.relative_to(workspace)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in Path(workspace).rglob('*.java')}
            binding['input_hashes']=input_hashes
            # Conservative budget for split suffix and detector timestamp output.
            paths=[output_dir/"split"/str(Path(p).parent)/ (Path(p).stem+"__99999.java") for p in input_hashes]
            if os.name=='nt' and any(len(str(p))>=240 for p in paths):
                return finish(command,started,clock,None,False,"BLOCKED","DETECTOR_PATH_TOO_LONG","Use a shorter isolated detector root."),"","",output_dir
            process = subprocess.Popen(command, cwd=str(execution_cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, encoding="utf-8", errors="replace", shell=False,
                                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
            try:
                stdout, stderr = process.communicate(timeout=timeout_seconds)
            except KeyboardInterrupt:
                from pipeline.verification.engineering.runner import _terminate_process_tree
                _terminate_process_tree(process)
                process.communicate()
                raise
            except subprocess.TimeoutExpired:
                if os.name == "nt": subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, shell=False)
                else: process.kill()
                stdout, stderr = process.communicate()
                return finish(command, started, clock, None, True, "BLOCKED", "TIMEOUT_BLOCKED", "Detector command timed out."), redact(stdout), redact(stderr), output_dir
            stdout, stderr = redact(stdout), redact(stderr)
            if process.returncode != 0:
                category = _classify(stdout + stderr)
                return finish(command, started, clock, process.returncode, False, "BLOCKED", category, "Phase 1 detector execution failed."), stdout, stderr, output_dir
            csvs = [x for x in (output_dir / "detection").glob("Output_TestSmellDetection_*.csv") if not x.name.endswith("_failures.csv")] if (output_dir / "detection").is_dir() else []
            if len(csvs)!=1:
                return finish(command, started, clock, 0, False, "BLOCKED", "DETECTOR_OUTPUT_MISSING", "Detector result CSV is missing or non-unique."), stdout, stderr, output_dir
            try:
                result_csv=csvs[0]
                failure=result_csv.with_name(result_csv.stem+"_failures.csv")
                if not failure.is_file(): raise ValueError('DETECTOR_FAILURE_SIDECAR_MISSING')
                rows=HardenedDetectorAdapter.read_output(result_csv)
                failed_rows=HardenedDetectorAdapter.read_output(failure,allow_any_header=True)
                inputs=list((output_dir/"detection").glob('detector_input_*.csv'))
                if len(inputs)!=1: raise ValueError('DETECTOR_INPUT_MANIFEST_NON_UNIQUE')
                with inputs[0].open(encoding='utf-8-sig',newline='') as handle: input_rows=list(csv.reader(handle))
                expected=[str(Path(r[1]).resolve()).lower() for r in input_rows if r]
                actual=[str(Path(r['TestFilePath']).resolve()).lower() for r in rows]
                if len(set(actual))!=len(actual) or sorted(expected)!=sorted(actual):
                    raise ValueError('DETECTOR_INPUT_OUTPUT_SET_MISMATCH')
                if any(r.get('App')!=project_name for r in rows): raise ValueError('DETECTOR_APP_MISMATCH')
                current={str(p.relative_to(workspace)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in Path(workspace).rglob('*.java')}
                if current!=input_hashes: raise ValueError('DETECTOR_SOURCE_CHANGED')
                binding.update(input_csv_ref=str(inputs[0]),input_csv_sha256=hashlib.sha256(inputs[0].read_bytes()).hexdigest(),
                               output_csv_ref=str(result_csv),failure_sidecar_ref=str(failure),
                               failure_sidecar_sha256=hashlib.sha256(failure.read_bytes()).hexdigest(),input_unchanged=True)
            except Exception as exc:
                return finish(command,started,clock,0,False,"BLOCKED","DETECTOR_RESULT_INVALID",str(exc)),stdout,stderr,output_dir
            if failed_rows:
                return finish(command,started,clock,0,False,"BLOCKED","DETECTOR_ANALYSIS_FAILED",
                                    f"Detector reported {len(failed_rows)} failed input row(s)."),stdout,stderr,output_dir
            result=finish(command, started, clock, 0, False, "PASS", None, "Phase 1 detector completed.")
            result.update({"output_hash":hashlib.sha256(sorted(csvs)[-1].read_bytes()).hexdigest(),"record_count":len(rows)})
            return result, stdout, stderr, output_dir
        except OSError as exc:
            return finish(command, started, clock, None, False, "BLOCKED", "DETECTOR_NOT_AVAILABLE", str(exc)), "", redact(str(exc)), None

    @staticmethod
    def prepare_input(*, workspace: Path, source_relative_path: Path) -> Path:
        """Return the exact Java file the detector will read, without rewriting it."""
        root = Path(workspace).resolve()
        target = (root / source_relative_path).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("Detector input is outside its isolated workspace.") from exc
        if not target.is_file():
            raise FileNotFoundError(f"Detector input is missing: {source_relative_path.as_posix()}")
        return target

    @staticmethod
    def _environment(workspace: Path, run_id: str, project_name: str, detector_jar: Path) -> dict[str, str]:
        blocked = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH")
        env = {k: v for k, v in os.environ.items() if not any(x in k.upper() for x in blocked)}
        env.update({"UTREFACTOR_RUN_ID": run_id, "UTREFACTOR_PROJECT_FILEPATH": str(workspace),
                    "UTREFACTOR_TSDETECT_JAR": str(detector_jar), "UTREFACTOR_MODE": "detect",
                    "UTREFACTOR_PROJECT_NAME": project_name, "UTREFACTOR_TEST_SOURCE_MODE": "all-java"})
        return env

    def _result(self, command, started, clock, exit_code, timed_out, status, category, summary):
        return {"status": status, "sanitized_command": [Path(command[0]).name, *command[1:]], "cwd": str(self.repo_root),
                "started_at": started, "completed_at": datetime.now(timezone.utc).isoformat(),
                "duration_ms": int((monotonic()-clock)*1000), "exit_code": exit_code, "timed_out": timed_out,
                "failure_category": category, "summary": summary,"detector_identity":self._binary_identity}

    def cleanup_successful_output(self, output_dir: Path, *, run_id: str, project_name: str) -> dict[str, Any]:
        """Remove a copied detector workspace after its required artifacts were captured."""
        expected=(self.repo_root/"data"/run_id/project_name).resolve()
        actual=Path(output_dir).resolve()
        scope=self._scopes.get(actual)
        if scope is not None:
            expected=(scope/"data"/"r"/project_name).resolve()
            if actual!=expected or not scope.name.startswith('fd-'):
                raise ValueError("Detector cleanup target is outside its invocation scope.")
            try:
                # Required raw CSV evidence must outlive successful split cleanup.
                archive=scope/"raw"; archive.mkdir()
                raw=[]
                for source in sorted((actual/"detection").glob('*.csv')):
                    target=archive/source.name; shutil.copy2(source,target)
                    digest=hashlib.sha256(source.read_bytes()).hexdigest()
                    if hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
                        raise OSError('DETECTOR_RAW_ARCHIVE_HASH_MISMATCH')
                    raw.append({'path':str(target),'sha256':digest,'original_path':str(source)})
                if len(raw)<3: raise OSError('DETECTOR_REQUIRED_RAW_ARCHIVE_INCOMPLETE')
                data=(scope/'data').resolve()
                if data.parent!=scope: raise ValueError('DETECTOR_CLEANUP_SCOPE_MISMATCH')
                shutil.rmtree(data); self._scopes.pop(actual)
                return {"status":"SPLITS_CLEANED_RAW_RETAINED","retained":True,"error":None,"raw_artifacts":raw}
            except OSError as exc:
                return {"status":"CLEANUP_FAILED","retained":True,"error":redact(str(exc))}
        if actual!=expected or expected.parent!=(self.repo_root/"data"/run_id).resolve():
            raise ValueError("Detector cleanup target is outside the expected run/project scope.")
        try:
            shutil.rmtree(actual)
            run_root=expected.parent
            if run_root.is_dir() and not any(run_root.iterdir()): run_root.rmdir()
            return {"status":"CLEANED","retained":False,"error":None}
        except OSError as exc:
            return {"status":"CLEANUP_FAILED","retained":True,"error":redact(str(exc))}


def _classify(output: str) -> str:
    lower = output.lower()
    if "unknown host" in lower or "name or service not known" in lower or "could not transfer artifact" in lower: return "DEPENDENCY_BLOCKED"
    if "java_home" in lower or "jdk" in lower and "not found" in lower: return "JDK_BLOCKED"
    return "DETECTOR_EXECUTION_BLOCKED"
