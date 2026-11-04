"""Standard-library-only, local artifact I/O and stable identifiers."""
from pathlib import Path
import csv, json, hashlib, sys, os
ROOT = Path(__file__).resolve().parents[1]
def sha_bytes(b): return hashlib.sha256(b).hexdigest()
def sha_file(p): return sha_bytes(Path(p).read_bytes())
def stable(prefix,*parts): return prefix+":"+sha_bytes(json.dumps(parts,ensure_ascii=False,separators=(",",":")).encode())[:32]
def inside(rel):
 p=(ROOT/rel).resolve()
 if not p.is_relative_to(ROOT): raise ValueError("Path escapes package")
 return p
def read_json(p): return json.loads(Path(p).read_text(encoding="utf-8-sig"))
def read_csv(p):
 with Path(p).open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def write_json(p,obj):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
def write_csv(p,rows,fields=None):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);rows=list(rows)
 fields=fields or (list(rows[0]) if rows else [])
 with p.open("w",encoding="utf-8",newline="") as f:
  w=csv.DictWriter(f,fieldnames=fields,lineterminator="\n",extrasaction="raise");w.writeheader();w.writerows(rows)
def write_jsonl(p,rows):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open("w",encoding="utf-8",newline="\n") as f:
  for r in rows:f.write(json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n")
def truth(v):return v is True or str(v).lower() in {"true","1"}
def logical(v):
 if not v:return ""
 s=str(v).replace("\\","/")
 if s.startswith("legacy://"):return s
 if s.startswith(("analysis/","data/","datasets/","config/","pipeline/","refactoring/","src/")):return "legacy://"+s
 return s
def offline_guard():
 """Deny network and reads/writes outside package or Python installation."""
 allowed=[ROOT,Path(sys.base_prefix).resolve(),Path(sys.prefix).resolve(),Path(sys.executable).resolve().parent]
 def hook(event,args):
  if event.startswith("socket.") and event not in {"socket.__new__"}:raise RuntimeError("Network disabled in artifact P0")
  if event=="open" and args and isinstance(args[0],(str,bytes,os.PathLike)):
   p=Path(os.fsdecode(args[0])).resolve()
   if not any(p.is_relative_to(a) for a in allowed):raise RuntimeError("Outside-package file access denied")
 sys.addaudithook(hook)
class SourceStore:
 def __init__(self):
  self.rows=read_csv(inside("data/source_records/index.csv"))
  self.index={r["source_reference"]:r for r in self.rows}
  self.cache={}
 def entry(self,ref):
  key=logical(ref).split("#",1)[0]
  return self.index.get(key)
 def path(self,ref,required=True):
  e=self.entry(ref)
  if not e or e["status"]!="EXPORTED":
   if required:raise FileNotFoundError("Unavailable source reference: "+str(ref))
   return None
  return inside(e["exported_relative_path"])
 def load(self,ref):
  key=logical(ref).split("#",1)[0]
  if key not in self.cache:
   p=self.path(key)
   if p.suffix==".csv":v=read_csv(p)
   elif p.suffix==".jsonl":v=[json.loads(s) for s in p.read_text(encoding="utf-8").splitlines() if s]
   elif p.suffix==".json":v=read_json(p)
   else:v=p.read_text(encoding="utf-8-sig")
   self.cache[key]=v
  return self.cache[key]
ANCHORS={
"overall":"analysis/final_experiment_consolidation_20261016/instance_reconciliation.csv",
"accepted":"analysis/admission_repair_plan_20261014/accepted_reuse_1812.csv",
"baseline":"analysis/rq1_baseline_final_consolidation_20261018/final_323_target_results.csv",
"methods":"analysis/controlled_baseline_frozen_input_20260920/baseline_sample_manifest.json",
"paired":"analysis/rq2_paired_validation_completion_20261016/paired_instance_results.csv",
"rq2_checks":"analysis/rq2/_acceptance/_definition/_audit/_20261022/raw_check_ledger_212.csv",
"a3":"analysis/ar_ablation_a3_structured/instances.jsonl",
"source120":"analysis/module2_original_rule_ablation/manifest_120.json",
"trace":"analysis/ar_a1_unified_instance_results.json",
"replay":"analysis/ar_rq3_instance_replay_results.json",
"replay_detail":"analysis/rq3_instance_case_audit/audit_data.json",
}
def batch_ref(n):
 return "analysis/rq1_supplement_protocol_repair_20261015/"+("recovery_batch5_20261016/runs/batch_5" if n==5 else f"batch_automation_20261015/runs/batch_{n}")+"/batch_summary.json"
