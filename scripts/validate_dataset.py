"""Validate the initial repository metadata without claiming omitted evidence passed.
Exit 0: metadata checks passed (full evidence may remain PARTIAL).
Exit 1: metadata/integrity failure. Exit 2: requested full evidence unavailable.
"""
import argparse,collections,csv,json
from common import *
from dataset_schema import FIELDS
from full_evidence_validation import duplicate_keys,acceptance_issues

def run(report="results/validation/repository_validation.json",require_full=False):
 output_path=result_path(report)
 schema=read_json(inside("configurations/validation/dataset_schema.json"))
 tables={name:read_csv(inside("data/normalized/"+name+".csv")) for name in FIELDS}
 errors=[];counts=collections.Counter()
 def check(ok,code,detail=""):
  counts[code+("_PASS" if ok else "_FAIL")]+=1
  if not ok:errors.append({"code":code,"detail":detail})
 for name,rows in tables.items():
  with inside("data/normalized/"+name+".csv").open(encoding="utf-8-sig",newline="") as f:columns=next(csv.reader(f))
  check(columns==FIELDS[name]==schema["tables"][name]["columns"],"SCHEMA",name)
  check(not duplicate_keys(rows,schema["tables"][name]["primary_key"]),"PRIMARY_KEY",name)
 ids={r["instance_id"]:r for r in tables["instances"]}
 executions={r["execution_id"]:r for r in tables["executions"]}
 candidates={r["candidate_id"]:r for r in tables["candidates"]}
 evidence={r["artifact_id"]:r for r in tables["evidence_manifest"]}
 configs=read_json(inside("data/normalized/configurations.json"))
 available={r["artifact_id"]:r for r in read_csv(inside("REPOSITORY_AVAILABILITY.csv"))}
 for name,rows in tables.items():
  for r in rows:
   for key,lookup in [("instance_id",ids),("linked_instance_id",ids),("execution_id",executions),("candidate_id",candidates),("linked_candidate_id",candidates)]:
    if r.get(key):check(r[key] in lookup,"FOREIGN_KEY",name+":"+key)
   for key in ["source_record_id","evidence_reference"]:
    if r.get(key):check(r[key].split("#",1)[0] in evidence,"EVIDENCE_REFERENCE",name+":"+key)
   for key in ["configuration_id","generation_configuration","validation_configuration","decision_configuration"]:
    if r.get(key):check(r[key] in configs,"CONFIGURATION",name+":"+key)
   if r.get("instance_id") and r.get("execution_id") in executions:check(executions[r["execution_id"]]["instance_id"]==r["instance_id"],"EXECUTION_BINDING",name)
   if r.get("instance_id") and r.get("candidate_id") in candidates:check(candidates[r["candidate_id"]]["instance_id"]==r["instance_id"],"CANDIDATE_BINDING",name)
 for r in tables["verification_checks"]:check(r["result"] in schema["check_enum"],"CHECK_ENUM",r["check_record_id"])
 for r in tables["decisions"]:check(r["decision"] in schema["decision_enum"],"DECISION_ENUM",r["instance_id"])
 for r in tables["applicability"]:check(r["applicability_decision"] in schema["applicability_enum"],"APPLICABILITY_ENUM",r["instance_id"])
 groups=collections.defaultdict(list)
 for r in tables["verification_checks"]:groups[(r["candidate_id"],r["execution_id"])].append(r)
 accepted=[r for r in tables["decisions"] if r["decision_configuration"]=="rq1.recorded-native" and r["decision"]=="ACCEPT" and truth(r["included_in_paper"])]
 index=read_csv(inside("experiments/rq1/accepted_evidence_index.csv"))
 indexed={r["instance_id"]:r for r in index}
 check(len(indexed)==len(index),"ACCEPTED_INDEX_UNIQUE")
 check(set(indexed)=={r["instance_id"] for r in accepted},"ACCEPTED_INDEX_BINDING")
 for d in accepted:
  row=indexed.get(d["instance_id"],{})
  check(all(row.get(k)==d[k] for k in ["candidate_id","execution_id","decision","evidence_reference"]),"ACCEPTED_INDEX_DECISION",d["instance_id"])
  candidate=candidates.get(d["candidate_id"],{})
  check(row.get("candidate_sha256")==candidate.get("candidate_hash") and row.get("candidate_artifact")==candidate.get("candidate_artifact"),"ACCEPTED_INDEX_CANDIDATE",d["instance_id"])
  # This tests consistency of recorded flags, not physical contract/candidate availability.
  issues=acceptance_issues(d,groups[(d["candidate_id"],d["execution_id"])],truth(row.get("contract_saved","")))
  check(not issues,"RECORDED_ACCEPTANCE_CONSISTENCY",d["instance_id"])
 members=collections.defaultdict(set)
 for r in tables["cohort_membership"]:members[r["cohort_id"]].add(r["instance_id"])
 check(members["rq1.accepted"]==set(indexed),"ACCEPTED_COHORT")
 for rq in ["rq1","rq2","rq3"]:
  frozen=read_csv(inside("data/cohorts/"+rq+"/membership.csv"))
  # Compare every field, preserving the original per-execution cohort semantics.
  key=lambda r:tuple(r[k] for k in FIELDS["cohort_membership"])
  actual=[r for r in tables["cohort_membership"] if r["cohort_id"].startswith(rq+".")]
  check(collections.Counter(map(key,frozen))==collections.Counter(map(key,actual)),"FROZEN_MEMBERSHIP",rq)
 for r in tables["methods"]:
  check(all(i in ids for i in json.loads(r["target_instance_ids"])),"METHOD_TARGET_FK",r["mapping_id"])
 for r in tables["rq3_pairs"]:
  for arm in ["original","replay"]:
   eid=r[arm+"_execution_id"];cid=r[arm+"_candidate_id"]
   check(eid in executions and cid in candidates,"REPLAY_FK",r["instance_id"])
   if cid in candidates:check(candidates[cid]["instance_id"]==r["instance_id"] and candidates[cid]["execution_id"]==eid,"REPLAY_BINDING",r["instance_id"])
 byexecution=collections.defaultdict(list)
 for r in tables["candidates"]:byexecution[r["execution_id"]].append(r)
 for r in tables["coverage"]:check(truth(r["candidate_available"])==bool(byexecution[r["execution_id"]]),"COVERAGE_METADATA_BINDING",r["instance_id"])
 for r in tables["candidate_artifact_relations"]:check(r["artifact_id"] in evidence,"INTEGRATED_ARTIFACT_FK",r["candidate_id"])
 for d in tables["decisions"]:
  if d["decision"]!="ACCEPT":continue
  cfg=d["decision_configuration"];rows=groups[(d["candidate_id"],d["execution_id"])];selected=[]
  if cfg in {"rq2.a.common","rq2.a.full-strict"}:
   needed={"FINAL_static","FINAL_compile","FINAL_target","FINAL_smell"}
   if cfg=="rq2.a.full-strict":needed.add("FINAL_contract")
   selected=[r for r in rows if r["check_id"] in needed]
   check({r["check_id"] for r in selected}==needed,"ENDPOINT_REQUIRED_CHECKS",d["instance_id"])
  elif cfg=="rq2.b.compile-target-only":
   needed={"PAIRED_COMPILE","PAIRED_EXACT_TARGET"}
   selected=[r for r in rows if r["check_id"] in needed]
   check({r["check_id"] for r in selected}==needed,"ENDPOINT_REQUIRED_CHECKS",d["instance_id"])
  elif cfg=="rq3.common":
   selected=[r for r in rows if r["check_id"].startswith("REPLAY_ENDPOINT_")]
   check(len(selected)==3,"ENDPOINT_REQUIRED_CHECKS",d["instance_id"])
  elif cfg=="rq1.baseline-common":
   for typ in ["COMPILATION","TARGET_TEST","ISSUE_REMOVAL"]:
    cs=[r for r in rows if r["check_type"]==typ]
    check(bool(cs) and all(r["result"]=="PASS" for r in cs),"BASELINE_RECORDED_EVIDENCE",d["instance_id"])
  if selected:check(all(r["result"]=="PASS" for r in selected),"ENDPOINT_CONSISTENCY",d["instance_id"])
 hashes={};omitted=set();present=set()
 for r in tables["evidence_manifest"]:
  a=available.get(r["artifact_id"])
  check(a is not None,"AVAILABILITY_DECLARED",r["artifact_id"])
  path=r["exported_relative_path"]
  if path and inside(path).is_file():
   hashes.setdefault(path,sha_file(inside(path)))
   check(hashes[path]==r["exported_sha256"],"PRESENT_EVIDENCE_HASH",r["artifact_id"]);present.add(path)
   check(a is not None and a["repository_status"]=="INCLUDED","AVAILABILITY_STATUS",r["artifact_id"])
  elif path:
   omitted.add(path)
   check(a is not None and a["repository_status"]=="WITHHELD_BATCH_SCOPE","OMISSION_EXPLICIT",r["artifact_id"])
 source_index=SourceStore()
 for i in ids.values():
  e=source_index.entry("datasets/"+i["project"]+"/"+i["source_file"])
  check(bool(e) and e["original_sha256"]==i["source_hash"],"HISTORICAL_SOURCE_HASH_INDEX",i["instance_id"])
 candidate_bytes=sum(inside(r["candidate_artifact"]).is_file() for r in candidates.values())
 contracts=inside("data/normalized/contracts.jsonl").is_file()
 full_status="BLOCKED"
 output={"status":"FAIL" if errors else "PARTIAL","metadata_validation":"FAIL" if errors else "PASS","full_evidence_validation":full_status,"contract_payloads":"PRESENT_UNVALIDATED" if contracts else "WITHHELD_LICENSE_REVIEW","candidate_bytes_present":candidate_bytes,"candidate_records":len(candidates),"historical_acceptance_index_links":len(indexed),"counts":{k:len(v) for k,v in tables.items()},"cohort_counts":{k:len(v) for k,v in sorted(members.items())},"evidence_files_present":len(present),"evidence_files_explicitly_omitted":len(omitted),"check_counts":dict(counts),"errors":errors,"scope":"Schema, historical metadata, frozen memberships and recorded consistency only. Missing bytes never PASS.","network_disabled":True,"outside_package_reads_denied":True}
 write_json(output_path,output)
 print(json.dumps({k:v for k,v in output.items() if k not in {"check_counts","errors","cohort_counts","counts"}},indent=2))
 if errors:print(json.dumps(errors[:10],indent=2))
 if errors:return 1
 return 2 if require_full else 0
if __name__=="__main__":
 p=argparse.ArgumentParser(description=__doc__);p.add_argument("--report",default="results/validation/repository_validation.json");p.add_argument("--require-full-evidence",action="store_true");a=p.parse_args();offline_guard();raise SystemExit(run(a.report,a.require_full_evidence))
