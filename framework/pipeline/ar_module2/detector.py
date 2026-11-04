"""Byte-identical full-target-file detector projection, common before and after."""
import hashlib
import shutil
import tempfile
from pathlib import Path
from pipeline.verification.outcome.detector import Phase1DetectorRunner
from pipeline.resume_store import file_hash


class TargetFileDetector(Phase1DetectorRunner):
    def __init__(self, relative, runtime_root, production=None, repo_root=None):
        self.framework_root = Path(repo_root or Path(__file__).resolve().parents[2])
        self.relative, self.production = Path(relative), production
        shadow = Path(runtime_root).resolve()
        shadow.mkdir(parents=True, exist_ok=True)
        if not (shadow / 'pom.xml').exists():
            shutil.copy2(self.framework_root / 'pom.xml', shadow / 'pom.xml')
            shutil.copytree(self.framework_root / 'target/classes', shadow / 'target/classes', dirs_exist_ok=True)
        super().__init__(repo_root=shadow)

    def run(self, *, workspace, run_id, project_name, detector_jar, timeout_seconds):
        with tempfile.TemporaryDirectory(prefix='ar-module2-det-') as directory:
            projection = Path(directory)
            source, target = Path(workspace) / self.relative, projection / self.relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            if file_hash(source) != file_hash(target):
                raise ValueError('DETECTOR_SOURCE_PROJECTION_DRIFT')
            if self.production and Path(self.production).is_file():
                from pipeline.agents.validation_contract import nearest_maven_root
                production = Path(self.production)
                dest = projection / production.relative_to(nearest_maven_root(production))
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(production.read_bytes())
            result, out, err, output = super().run(workspace=projection,
                run_id=hashlib.sha256(run_id.encode()).hexdigest()[:16], project_name='p',
                detector_jar=detector_jar, timeout_seconds=timeout_seconds)
            result.update(scope='ONE_COMPLETE_TARGET_TEST_FILE',
                          compiled_source_copy_sha256=file_hash(source), actual_detector_source_sha256=file_hash(target))
            return result, out, err, output

    def cleanup_successful_output(self, output_dir, *, run_id, project_name):
        path = Path(output_dir)
        return super().cleanup_successful_output(path, run_id=path.parent.name, project_name=path.name)
