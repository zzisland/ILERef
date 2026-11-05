"""Inspect one preserved Method-Issue instance without a model or network."""
import argparse,json
from common import *
EXAMPLE="utrefactor:v1:65e1a979dfd24359bb33eb94e7cb470b2c6c4b7ac21457a83a82f52443f3b6ae"
def collect(iid):
 tables={}
 for name in ["instances","executions","applicability","candidates","decisions","verification_checks","cohort_membership","rq3_pairs","coverage"]:
  tables[name]=[r for r in read_csv(inside("data/normalized/"+name+".csv")) if r.get("instance_id")==iid]
 if len(tables["instances"])!=1:raise ValueError("Unknown or nonunique instance ID")
 contract_path=inside("data/normalized/contracts.jsonl")
 tables["contracts"]=[json.loads(line) for line in contract_path.read_text(encoding="utf-8").splitlines() if line and json.loads(line)["instance_id"]==iid] if contract_path.is_file() else None
 tables["evidence"]=[r for r in read_csv(inside("data/normalized/evidence_manifest.csv")) if r.get("linked_instance_id")==iid]
 refs={r["evidence_reference"].split("#")[0] for name in ["applicability","candidates","decisions","verification_checks","contracts"] for r in (tables[name] or []) if r.get("evidence_reference")}
 refs.update(r["source_record_id"].split("#")[0] for name in ["instances","executions"] for r in tables[name] if r.get("source_record_id"))
 tables["source_index"]=[r for r in SourceStore().rows if r["artifact_id"] in refs or r["artifact_id"] in {e["artifact_id"] for e in tables["evidence"]}]
 availability={r["artifact_id"]:r for r in read_csv(inside("REPOSITORY_AVAILABILITY.csv"))}
 return {"scope":"Historical metadata only; omitted evidence is not a completed check","instance_id":iid,"availability":{"contracts":"INCLUDED" if contract_path.is_file() else "WITHHELD_LICENSE_REVIEW","evidence":[availability[r["artifact_id"]] for r in tables["evidence"] if r["artifact_id"] in availability]},**tables}
def main():
 p=argparse.ArgumentParser(description=__doc__);g=p.add_mutually_exclusive_group(required=True);g.add_argument("--example",action="store_true");g.add_argument("--instance-id");p.add_argument("--output");a=p.parse_args();offline_guard()
 output_path=result_path(a.output) if a.output else None
 result=collect(EXAMPLE if a.example else a.instance_id)
 if output_path:write_json(output_path,result)
 print(json.dumps({"instance_id":result["instance_id"],"instance":result["instances"][0],"counts":{k:len(v) for k,v in result.items() if isinstance(v,list)},"decisions":result["decisions"],"output":a.output,"availability":{"contracts":result["availability"]["contracts"],"evidence_status_counts":dict(__import__("collections").Counter(e["repository_status"] for e in result["availability"]["evidence"]))}},indent=2))
 return 0
if __name__=="__main__":raise SystemExit(main())
