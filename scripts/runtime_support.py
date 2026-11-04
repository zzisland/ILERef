import csv, hashlib, json, os, shutil, tarfile
from pathlib import Path, PurePosixPath
ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/ar_cli"

def sha(data):
    return hashlib.sha256(data).hexdigest()

def file_sha(path):
    return sha(Path(path).read_bytes())

def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")

def require(condition, message):
    if not condition:
        raise ValueError(message)

def contained(path, base=ROOT):
    path = Path(path).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError("Path escapes its permitted root")
    return path

def tool_paths():
    java_home = os.environ.get("JAVA_HOME")
    maven_home = os.environ.get("MAVEN_HOME")
    java = str(Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")) if java_home else shutil.which("java")
    maven = str(Path(maven_home) / "bin" / ("mvn.cmd" if os.name == "nt" else "mvn")) if maven_home else shutil.which("mvn")
    return {k: str(Path(v).resolve()) if v and Path(v).is_file() else None
            for k, v in {"java": java, "maven": maven}.items()}

def isolated_environment(base, tools):
    keep = {k: v for k, v in os.environ.items()
            if k.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
                             "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS"}}
    paths = [str(Path(v).parent) for v in tools.values() if v]
    paths += [str(Path(keep.get("SYSTEMROOT", "/usr")) / "System32")] if os.name == "nt" else ["/usr/bin", "/bin"]
    keep.update(PATH=os.pathsep.join(paths), HOME=str(base / "home"),
                USERPROFILE=str(base / "home"), APPDATA=str(base / "home/AppData"),
                LOCALAPPDATA=str(base / "home/AppData/Local"),
                TEMP=str(base / "temp"), TMP=str(base / "temp"),
                TMPDIR=str(base / "temp"), USERNAME="artifact", USER="artifact",
                COMPUTERNAME="artifact", PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    if tools["java"]:
        keep["JAVA_HOME"] = str(Path(tools["java"]).parent.parent)
    if tools["maven"]:
        keep["MAVEN_HOME"] = str(Path(tools["maven"]).parent.parent)
    keep["MAVEN_OPTS"] = ' '.join([
        f'-Duser.home="{base / "home"}"', "-Duser.name=artifact",
        f'-Djava.io.tmpdir="{base / "temp"}"', "-Dfile.encoding=UTF-8"])
    return keep

def extract_project(sample, destination):
    delivery = sample["delivery"]
    archive = contained(EXAMPLE / delivery["project_archive"], EXAMPLE)
    manifest = contained(EXAMPLE / delivery["project_manifest"], EXAMPLE)
    require(file_sha(archive) == delivery["project_archive_sha256"], "Project archive hash mismatch")
    require(file_sha(manifest) == delivery["project_manifest_sha256"], "Project manifest hash mismatch")
    with manifest.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    expected = {r["path"]: r for r in rows}
    require(len(expected) == len(rows) == delivery["archive_files"], "Duplicate or missing project entries")
    with tarfile.open(archive, "r:gz") as source:
        entries = source.getmembers()
        require(len(entries) == len(expected) and {x.name for x in entries} == set(expected), "Archive inventory mismatch")
        for member in entries:
            path = PurePosixPath(member.name)
            require(member.isfile() and not path.is_absolute() and ".." not in path.parts
                    and "\\" not in member.name and ":" not in member.name, "Unsafe archive member")
            target = contained(destination / member.name, destination)
            row = expected[member.name]
            require(member.size == int(row["bytes"]), "Archive size mismatch")
            data = source.extractfile(member).read()
            require(sha(data) == row["sha256"], "Project file hash mismatch: " + member.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return rows
