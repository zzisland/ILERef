from __future__ import annotations

import shutil
import tempfile
import hashlib
import json
import os
from pathlib import Path
from typing import Any


EXCLUDED_INPUT_NAMES = frozenset({'.git','target','.idea','.gradle','build'})


def artifact_input_exclusion(source_root, candidate_file):
    root=Path(source_root).resolve();candidate=Path(candidate_file).resolve()
    if not candidate.is_relative_to(root):return None
    relative=candidate.relative_to(root)
    # Fixture/output roots inside a project are not build inputs. The same
    # exclusion is applied to the copy and the identity, never to source dirs.
    if 'refactoring' in relative.parts and len(relative.parts)>1:
        if relative.parts[0] in ('src','test','tests','main','resources'):
            raise ValueError('CANDIDATE_ARTIFACT_INSIDE_SOURCE_TREE')
        return root/relative.parts[0]
    return candidate


def project_input_sha256(source_root, candidate_file):
    """Identity of the inputs copied for engineering, without running any tool."""
    root=Path(source_root).resolve();excluded=artifact_input_exclusion(root,candidate_file)
    rows=[]
    def unreadable(error):
        raise error
    for directory,dirs,files in os.walk(root,onerror=unreadable):
        parent=Path(directory)
        dirs[:]=sorted(n for n in dirs if n not in EXCLUDED_INPUT_NAMES and parent/n!=excluded)
        for name in dirs+files:
            path=parent/name
            if path==excluded or name in EXCLUDED_INPUT_NAMES:continue
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError('PROJECT_INPUT_LINK_UNSUPPORTED')
        for name in sorted(files):
            path=parent/name
            if path==excluded or name in EXCLUDED_INPUT_NAMES:continue
            rows.append((path.relative_to(root).as_posix(),hashlib.sha256(path.read_bytes()).hexdigest()))
    return hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest()


class IsolatedValidationWorkspace:
    def __init__(self, source_root: Path, relative_test_path: Path, candidate_file: Path, *, preserve: bool = False):
        self.source_root = source_root.resolve(); self.relative_test_path = relative_test_path
        self.candidate_file = candidate_file.resolve(); self.preserve = preserve
        self._temp_root: Path | None = None; self.root: Path | None = None
        self.cleanup_error: str | None = None

    def __enter__(self):
        self._temp_root = Path(tempfile.mkdtemp(prefix="utrefactor-validation-")).resolve()
        self.root = self._temp_root / "project"
        excluded=artifact_input_exclusion(self.source_root,self.candidate_file)
        def ignore(directory,names):
            return [n for n in names if n in EXCLUDED_INPUT_NAMES or Path(directory)/n==excluded]
        shutil.copytree(self.source_root, self.root, ignore=ignore)
        target = (self.root / self.relative_test_path).resolve()
        if not target.is_relative_to(self.root.resolve()): raise ValueError("Candidate injection target escapes isolated workspace.")
        if not target.is_file(): raise FileNotFoundError(f"Isolated target test file is missing: {self.relative_test_path}")
        target.write_bytes(self.candidate_file.read_bytes())
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.preserve: return False
        try:
            if self._temp_root:
                temp_parent=Path(tempfile.gettempdir()).resolve()
                if self._temp_root.parent!=temp_parent or not self._temp_root.name.startswith("utrefactor-validation-"):
                    raise ValueError("Validation cleanup target is outside the controlled temporary scope.")
                shutil.rmtree(self._temp_root)
        except Exception as cleanup_exc:
            self.cleanup_error = f"{type(cleanup_exc).__name__}: {cleanup_exc}"
        return False

    def manifest(self) -> dict[str, Any]:
        return {"isolation_mode": "controlled_copy", "workspace_scope": "temporary", "source_project_root": str(self.source_root), "injected_relative_path": str(self.relative_test_path).replace("\\", "/"), "excluded_paths": [".git", "target", ".idea", ".gradle", "build"], "preserved": self.preserve, "cleanup_error": self.cleanup_error}
