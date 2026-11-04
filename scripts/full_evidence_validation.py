"""Schema, referential, hash, frozen membership and decision/evidence validation."""
import argparse,collections,json
from common import *
from dataset_schema import FIELDS
KEYS={"instances":["instance_id"],"executions":["execution_id"],"applicability":["instance_id","execution_id","configuration_id"],"candidates":["candidate_id"],"verification_checks":["check_record_id"],"decisions":["instance_id","execution_id","decision_configuration"],"cohort_membership":["cohort_id","instance_id","execution_id"],"evidence_manifest":["artifact_id","linked_instance_id","linked_candidate_id","evidence_type"],"methods":["mapping_id"],"rq3_pairs":["instance_id"],"exclusions":["instance_id","execution_id"],"coverage":["instance_id","execution_id"],"candidate_artifact_relations":["candidate_id","execution_id","artifact_role"]}
CHECKS={"PASS","FAIL","INCONCLUSIVE","NOT_RUN","NOT_APPLICABLE"}
DECISIONS={"ACCEPT","REVIEW","REJECT","ABSTAIN","NOT_RUN","NO_CANDIDATE","NOT_ACCEPT"}
def duplicate_keys(rows,fields):
 c=collections.Counter(tuple(r.get(f,"") for f in fields) for r in rows)
 return [k for k,n in c.items() if n>1]
def acceptance_issues(decision,checks,contract_present):
 if decision["decision"]!="ACCEPT" or not truth(decision["included_in_paper"]):return []
 if decision["decision_configuration"]!="rq1.recorded-native":return []
 errors=[]
 if not decision["candidate_id"]:errors.append("ACCEPT_WITHOUT_CANDIDATE")
 if not contract_present:errors.append("ACCEPT_WITHOUT_CONTRACT")
 for typ in ["CONTRACT","SOURCE_INTEGRITY","COMPILATION","TARGET_TEST","ISSUE_REMOVAL","DETECTOR_EXECUTION"]:
  relevant=[x for x in checks if x["check_type"]==typ]
  if not relevant or not any(x["result"]=="PASS" for x in relevant):errors.append("NO_PASS_"+typ)
  if any(x["result"] in {"FAIL","INCONCLUSIVE","NOT_RUN"} for x in relevant):errors.append("NONPASS_"+typ)
 return errors
def run(data="data/normalized",pilot=False,report=None):
 t={k:read_csv(inside(data+"/"+k+".csv")) for k in FIELDS};errors=[];counts=collections.Counter()
 def ck(ok,code,detail=""):
  counts[code+("_PASS" if ok else "_FAIL")]+=1
  if not ok:errors.append({"code":code,"detail":detail})
 for name,rs in t.items():
  with inside(data+"/"+name+".csv").open(encoding="utf-8",newline="") as f:fields=next(__import__("csv").reader(f))
  ck(fields==FIELDS[name],"SCHEMA_COLUMNS",name);ck(not duplicate_keys(rs,KEYS[name]),"PRIMARY_KEY",name)
 ids={r["instance_id"] for r in t["instances"]};exe={r["execution_id"]:r for r in t["executions"]};cands={r["candidate_id"]:r for r in t["candidates"]};evidence={r["artifact_id"]:r for r in t["evidence_manifest"]}
 for name,rs in t.items():
  for r in rs:
   for key,allowed in [("instance_id",ids),("linked_instance_id",ids),("execution_id",exe),("candidate_id",cands),("linked_candidate_id",cands)]:
    if r.get(key):ck(r[key] in allowed,"FOREIGN_KEY",name+":"+key+":"+r[key])
   for k in ["source_record_id","evidence_reference"]:
    if r.get(k):ck(r[k].split("#",1)[0] in evidence,"EVIDENCE_FOREIGN_KEY",name+":"+k)
   if r.get("execution_id") and r.get("instance_id"):ck(exe[r["execution_id"]]["instance_id"]==r["instance_id"],"EXECUTION_INSTANCE_BINDING",name)
   if r.get("candidate_id") and r.get("instance_id"):ck(cands[r["candidate_id"]]["instance_id"]==r["instance_id"],"CANDIDATE_INSTANCE_BINDING",name)
 for r in t["verification_checks"]:ck(r["result"] in CHECKS,"CHECK_ENUM",r["check_record_id"])
 for r in t["decisions"]:ck(r["decision"] in DECISIONS,"DECISION_ENUM",r["instance_id"])
 for r in t["applicability"]:ck(r["applicability_decision"] in {"ADMIT","REVIEW","PRESERVE","NOT_APPLICABLE"},"APPLICABILITY_ENUM",r["instance_id"])
 hashed={}
 for r in t["evidence_manifest"]:
  if r["status"]!="EXPORTED":continue
  p=inside(r["exported_relative_path"])
  if r["exported_relative_path"] not in hashed:hashed[r["exported_relative_path"]]=sha_file(p) if p.is_file() else ""
  ck(hashed[r["exported_relative_path"]]==r["exported_sha256"],"EXPORTED_EVIDENCE_HASH",r["artifact_id"])
 for r in t["candidates"]:
  p=inside(r["candidate_artifact"]);h=sha_file(p) if p.is_file() else ""
  ck(h==r["candidate_hash"]==r["exported_hash"],"EXACT_CANDIDATE_BYTES",r["candidate_id"])
 contracts=[json.loads(s) for s in inside(data+"/contracts.jsonl").read_text(encoding="utf-8").splitlines() if s]
 ck(not duplicate_keys(contracts,["contract_id"]),"CONTRACT_PRIMARY_KEY")
 contracted=set()
 for c in contracts:
  ck(c["instance_id"] in ids,"CONTRACT_INSTANCE_FK",c["contract_id"])
  if c["candidate_id"]:ck(c["candidate_id"] in cands,"CONTRACT_CANDIDATE_FK",c["contract_id"]);contracted.add(c["candidate_id"])
  raw=c["contract_payload"]
  if raw.get("instance_id"):ck(raw["instance_id"]==c["instance_id"],"CONTRACT_INSTANCE_BINDING",c["contract_id"])
 checkgroups=collections.defaultdict(list)
 for c in t["verification_checks"]:checkgroups[(c["candidate_id"],c["execution_id"])].append(c)
 accepted=[]
 for d in t["decisions"]:
  errs=acceptance_issues(d,checkgroups[(d["candidate_id"],d["execution_id"])],d["candidate_id"] in contracted)
  for err in errs:ck(False,err,d["instance_id"])
  if d["decision_configuration"]=="rq1.recorded-native" and d["decision"]=="ACCEPT" and truth(d["included_in_paper"]):accepted.append(d["instance_id"]);ck(not errs,"ACCEPTED_COMPLETE_EVIDENCE",d["instance_id"])
 members=collections.defaultdict(set)
 for r in t["cohort_membership"]:members[r["cohort_id"]].add(r["instance_id"])
 s=SourceStore()
 for i in t["instances"]:
  e=s.entry("datasets/"+i["project"]+"/"+i["source_file"])
  ck(bool(e) and e["original_sha256"]==i["source_hash"],"ORIGINAL_SOURCE_FILE_HASH",i["instance_id"])
 for dec in t["decisions"]:
  if dec["decision"]!="ACCEPT":continue
  cfg=dec["decision_configuration"];rows=checkgroups[(dec["candidate_id"],dec["execution_id"])]
  selected=[]
  if cfg in {"rq2.a.common","rq2.a.full-strict"}:
   needed={"FINAL_static","FINAL_compile","FINAL_target","FINAL_smell"}
   if cfg=="rq2.a.full-strict":needed.add("FINAL_contract")
   selected=[r for r in rows if r["check_id"] in needed]
   ck({r["check_id"] for r in selected}==needed,"ENDPOINT_REQUIRED_CHECKS",dec["instance_id"]+":"+cfg)
  elif cfg=="rq2.b.compile-target-only":
   needed={"PAIRED_COMPILE","PAIRED_EXACT_TARGET"}
   selected=[r for r in rows if r["check_id"] in needed]
   ck({r["check_id"] for r in selected}==needed,"ENDPOINT_REQUIRED_CHECKS",dec["instance_id"]+":"+cfg)
  elif cfg=="rq3.common":
   selected=[r for r in rows if r["check_id"].startswith("REPLAY_ENDPOINT_")]
   ck(len(selected)==3,"ENDPOINT_REQUIRED_CHECKS",dec["instance_id"]+":"+cfg)
  if selected:ck(all(r["result"]=="PASS" for r in selected),"POLICY_DECISION_CONSISTENCY",dec["instance_id"]+":"+cfg)
 expected={
 "rq1.detected":{r["instance_id"] for r in s.load(ANCHORS["overall"])},
 "rq1.admitted":{r["instance_id"] for r in s.load(ANCHORS["overall"]) if r["final_admission_state"]=="ADMIT"},
 "rq1.accepted":{r["instance_id"] for r in s.load(ANCHORS["overall"]) if truth(r["file_native_positive_evidence"])},
 "rq1.baseline.Full":{r["instance_id"] for r in s.load(ANCHORS["baseline"]) if r["framework"]=="Full"},
 "rq1.baseline.UTRefactor":{r["instance_id"] for r in s.load(ANCHORS["baseline"]) if r["framework"]=="UTRefactor"},
 "rq2.a.full.input":{r["instance_id"] for r in s.load(ANCHORS["paired"])},
 "rq2.a.rule.input":{r["instance_id"] for r in s.load(ANCHORS["paired"])},
 "rq2.a.paired.full":{r["instance_id"] for r in s.load(ANCHORS["paired"]) if truth(r["paired_eligible"])},
 "rq2.b.parent":{r["instance_id"] for r in s.load(ANCHORS["a3"])},
 "rq2.b.paired":{r["instance_id"] for r in s.load(ANCHORS["a3"]) if r["candidate_pair_available"]},
 "rq3.source120":{r["instance_id"] for r in s.load(ANCHORS["source120"])["rows"]},
 "rq3.original79":{r["instance_id"] for r in s.load(ANCHORS["replay"])["records"]},
 "rq3.replay79":{r["instance_id"] for r in s.load(ANCHORS["replay"])["records"]},
 }
 for cohort,want in expected.items():
  if pilot:want=want&ids
  ck(members[cohort]==want,"FROZEN_MEMBERSHIP",cohort)
 ck(set(accepted)==members["rq1.accepted"],"ACCEPTED_COHORT_DECISION")
 configs=read_json(inside(data+"/configurations.json"))
 for name,rs in t.items():
  for r in rs:
   for key in ["configuration_id","generation_configuration","validation_configuration","decision_configuration"]:
    if r.get(key):ck(r[key] in configs,"CONFIGURATION_FOREIGN_KEY",name+":"+r[key])
 for c in contracts:
  ck(c["execution_id"] in exe,"CONTRACT_EXECUTION_FK",c["contract_id"])
  ck(c["evidence_reference"].split("#",1)[0] in evidence,"CONTRACT_EVIDENCE_FK",c["contract_id"])
  if c["candidate_id"] in cands:ck(cands[c["candidate_id"]]["execution_id"]==c["execution_id"],"CONTRACT_EXECUTION_BINDING",c["contract_id"])
 for r in t["rq3_pairs"]:
  for arm in ["original","replay"]:
   eid=r[arm+"_execution_id"];cid=r[arm+"_candidate_id"]
   ck(eid in exe and cid in cands,"REPLAY_PAIR_FK",r["instance_id"]+":"+arm)
   if cid in cands:ck(cands[cid]["instance_id"]==r["instance_id"] and cands[cid]["execution_id"]==eid,"REPLAY_PAIR_BINDING",r["instance_id"]+":"+arm)
 for r in t["methods"]:
  ck(all(i in ids for i in json.loads(r["target_instance_ids"])),"FROZEN_METHOD_TARGET_FK",r["mapping_id"])
 for r in t["candidate_artifact_relations"]:
  ck(r["artifact_id"] in evidence,"INTEGRATED_ARTIFACT_FK",r["candidate_id"])
 by_execution=collections.defaultdict(list)
 for r in t["candidates"]:by_execution[r["execution_id"]].append(r)
 for r in t["coverage"]:ck(truth(r["candidate_available"])==bool(by_execution[r["execution_id"]]),"BASELINE_COVERAGE_SAVED_CANDIDATE",r["instance_id"]+":"+r["framework"])
 for d in t["decisions"]:
  if d["decision_configuration"]=="rq1.baseline-common" and d["decision"]=="ACCEPT":
   relevant=checkgroups[(d["candidate_id"],d["execution_id"])]
   for typ in ["COMPILATION","TARGET_TEST","ISSUE_REMOVAL"]:
    cs=[r for r in relevant if r["check_type"]==typ]
    ck(bool(cs) and all(r["result"]=="PASS" for r in cs),"BASELINE_SUCCESS_EVIDENCE",d["instance_id"]+":"+typ)
 # Each original paper selection row must be represented, including excluded/inconclusive cases.
 ck(len(ids)==len(expected["rq1.detected"] if not pilot else expected["rq1.detected"]&ids),"POPULATION_NO_SILENT_DROP")
 output={"status":"PASS" if not errors else "FAIL","mode":"PILOT" if pilot else "FULL","counts":{k:len(v) for k,v in t.items()},"accepted_instances_with_complete_evidence":len(accepted)-sum(1 for i in set(accepted) if any(e["detail"]==i for e in errors)),"check_counts":dict(counts),"errors":errors,"withheld_or_missing_sources":dict(collections.Counter(r["status"] for r in s.rows if r["status"]!="EXPORTED")),"cohort_counts":{k:len(v) for k,v in sorted(members.items())},"scope":"Offline validation of preserved data and evidence; no engineering replay","network_disabled":True,"outside_package_reads_denied":True}
 write_json(inside(report or ("results/validation/"+("pilot_validation.json" if pilot else "dataset_validation.json"))),output)
 print(json.dumps({k:v for k,v in output.items() if k not in ["check_counts","errors"]},indent=2))
 if errors:print("ERRORS",json.dumps(errors[:20],indent=2))
 return 0 if not errors else 1
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--data",default="data/normalized");p.add_argument("--pilot",action="store_true");p.add_argument("--report",help="Package-relative output path; keeps previous validation reports unchanged");a=p.parse_args();offline_guard();raise SystemExit(run(a.data,a.pilot,a.report))
