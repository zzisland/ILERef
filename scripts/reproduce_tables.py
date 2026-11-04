"""Recompute paper tables solely from normalized, per-instance records."""
import argparse,json,collections
from common import *
def canonical(v):
 s=str(v).replace("\\%","%")
 return str(float(s[:-1]))+"%" if s.endswith("%") else s
def rate(a,b,d=1):
 if not b:raise ValueError("Zero denominator")
 return f"{100*a/b:.{d}f}%"
def calculate(data):
 tables={name:read_csv(inside(data+"/"+name+".csv")) for name in ["instances","applicability","decisions","cohort_membership","coverage","methods","executions","rq3_pairs"]}
 inst={r["instance_id"]:r for r in tables["instances"]};ex={r["execution_id"]:r for r in tables["executions"]}
 def cohort(name):return {r["instance_id"] for r in tables["cohort_membership"] if r["cohort_id"]==name}
 def accepted(config,variant=None):
  return {r["instance_id"] for r in tables["decisions"] if r["decision_configuration"]==config and r["decision"]=="ACCEPT" and (variant is None or ex[r["execution_id"]]["variant"]==variant)}
 projects=["cli","jsoup","jfreechart","compress"];smells=["AR","RA","ECT"];numeric={};output={}
 rows=[]
 for p in projects:
  v=[sum(r["project"]==p and r["issue_type"]==s for r in inst.values()) for s in smells];rows.append([p]+v+[sum(v)])
 rows.append(["Total"]+[sum(r[j] for r in rows) for j in range(1,5)])
 output["I"]={"columns":["Project","AR","RA","ECT","Total"],"rows":rows};numeric["I"]=[r[1:] for r in rows]
 rows=[]
 for p in projects:
  ms=[r for r in tables["methods"] if r["project"]==p];target={i for r in ms for i in json.loads(r["target_instance_ids"])}
  v=[sum(inst[i]["issue_type"]==s for i in target) for s in smells];rows.append([p,len(ms),len(target)]+v)
 rows.append(["Total"]+[sum(r[j] for r in rows) for j in range(1,6)])
 output["II"]={"columns":["Project","Methods","Targets","AR","RA","ECT"],"rows":rows};numeric["II"]=[r[1:] for r in rows]
 arms={}
 for arm in ["Full","UTRefactor"]:
  rs=[r for r in tables["coverage"] if r["framework"]==arm]
  arms[arm]={"targets":len(rs),"coverage":sum(truth(r["candidate_available"]) for r in rs),"success":sum(r["original_final_status"]=="COMMON_PASS" for r in rs),"failure":sum(r["original_final_status"]=="COMMON_FAIL" for r in rs),"inconclusive":sum(r["original_final_status"]=="INCONCLUSIVE" for r in rs),"no_candidate":sum(r["original_final_status"]=="NO_CANDIDATE" for r in rs)}
 rows=[[label,arms["Full"][k],arms["UTRefactor"][k]] for label,k in [("Target Instances","targets"),("Candidate Coverage","coverage"),("Confirmed Success","success"),("Confirmed Failure","failure")]]
 output["III"]={"columns":["Metric","Our Framework","UTRefactor"],"rows":rows,"notes":arms};numeric["III"]=[r[1:] for r in rows]
 detected=cohort("rq1.detected");admitted=cohort("rq1.admitted");selected=cohort("rq1.accepted")
 strict_published={r["instance_id"] for r in tables["decisions"] if r["decision_configuration"]=="rq1.recorded-native" and r["decision"]=="ACCEPT" and truth(r["included_in_paper"])}
 if selected!=strict_published:raise ValueError("RQ1 accepted cohort/decision mismatch")
 rows=[]
 for smell in smells+[None]:
  ids={i for i in detected if smell is None or inst[i]["issue_type"]==smell};d=len(ids);a=len(ids&admitted);c=len(ids&selected)
  rows.append([smell or "Total",d,a,c,rate(a,d),rate(c,a),rate(c,d)])
 output["IV"]={"columns":["Issue","Detected","Admitted","Accepted","Admission","Conditional Acceptance","End-to-End"],"rows":rows};numeric["IV"]=[r[1:] for r in rows]
 inputs=cohort("rq2.a.full.input");paired=cohort("rq2.a.paired.full");f=accepted("rq2.a.full-strict");r=accepted("rq2.a.common","RULE")
 cf=accepted("rq2.a.common","FULL")
 rows=[["Full",len(inputs),len(paired),len(inputs-paired),len(f)],["Rule-Guided",len(cohort("rq2.a.rule.input")),len(cohort("rq2.a.rule.input")),0,len(r)]]
 pairedrows=[["Full",f"{len(f&paired)}/{len(paired)}",rate(len(f&paired),len(paired),2)],["Rule-Guided",f"{len(r&paired)}/{len(paired)}",rate(len(r&paired),len(paired),2)]]
 pair_counts=lambda x,y:{"both":len(x&y&paired),"full_only":len((x-y)&paired),"rule_only":len((y-x)&paired),"neither":len(paired-(x|y))}
 output["V"]={"columns":["Variant","Inputs","Attempted","Abstained","Accepted"],"rows":rows,"paired_columns":["Variant","Accepted","Accepted Rate"],"paired_rows":pairedrows,"strict_pairs":pair_counts(f,r),"common_pairs":pair_counts(cf,r),"full_common_accepted":len(cf)}
 numeric["V"]=[r[1:] for r in rows+pairedrows]
 c=cohort("rq2.b.paired");fa=accepted("rq2.b.evidence-driven")&c;wa=accepted("rq2.b.compile-target-only")&c
 rows=[["Evidence-Driven",f"{len(fa)}/{len(c)}",rate(len(fa),len(c)),0],["Compile/Test-Only",f"{len(wa)}/{len(c)}",rate(len(wa),len(c)),f"{len(wa-fa)} ({rate(len(wa-fa),len(c))})"]]
 output["VI"]={"columns":["Variant","Accepted","Rate","Difference from Full"],"rows":rows,"difference_instance_ids":sorted(wa-fa)}
 numeric["VI"]=[[f"{len(fa)}/{len(c)}",rate(len(fa),len(c)),0],[f"{len(wa)}/{len(c)}",rate(len(wa),len(c)),len(wa-fa),rate(len(wa-fa),len(c))]]
 shared={r["instance_id"] for r in tables["coverage"] if r["framework"]=="Full" and truth(r["candidate_available"])} & {r["instance_id"] for r in tables["coverage"] if r["framework"]=="UTRefactor" and truth(r["candidate_available"])}
 extras={"shared_baseline_targets":len(shared),"rq3_source_instances":len(cohort("rq3.source120")),"rq3_replay_instances":len(tables["rq3_pairs"]),"rq3_all_five_comparisons_equal":sum(all(truth(r[k]) for k in ["source_hash_equal","candidate_hash_equal","target_test_identity_equal","post_outcome_equal","final_decision_equal"]) for r in tables["rq3_pairs"]),"strict_pairs":output["V"]["strict_pairs"],"common_pairs":output["V"]["common_pairs"]}
 return output,numeric,extras
def run(data="data/normalized"):
 output,numeric,extras=calculate(data);frozen=read_json(inside("configurations/validation/paper_cells.json"));checks=[]
 roman=["I","II","III","IV","V","VI"]
 for row in frozen["checks"]:
  name=roman[int(row["table"])-1];actual=numeric[name][row["row"]-1];expected=row["paper_numeric_cells"]
  if len(actual)!=len(expected):raise ValueError("Table shape differs")
  for j,(a,e) in enumerate(zip(actual,expected),1):checks.append({"table":name,"row":row["row"],"numeric_column":j,"computed":a,"frozen_paper":e,"match":canonical(a)==canonical(e)})
 for name,tab in output.items():
  write_csv(inside(f"results/tables/table_{name}.csv"),[dict(zip(tab["columns"],r)) for r in tab["rows"]],tab["columns"])
  if name=="V":write_csv(inside("results/tables/table_V_paired.csv"),[dict(zip(tab["paired_columns"],r)) for r in tab["paired_rows"]],tab["paired_columns"])
  write_json(inside(f"results/tables/table_{name}.json"),tab)
 matches=sum(r["match"] for r in checks)
 report={"status":"PASS" if matches==len(checks) else "FAIL","matched_numeric_cells":matches,"total_numeric_cells":len(checks),"tables":{n:{"matched":sum(c["match"] for c in checks if c["table"]==n),"cells":sum(c["table"]==n for c in checks)} for n in roman},"checks":checks,"supplementary":extras,"input":"Package-local normalized per-instance data; frozen numbers used only after computation for comparison","network_disabled":True,"source_project_access":"DENIED_BY_RUNTIME_GUARD"}
 write_json(inside("results/validation/table_reproduction.json"),report)
 lines=["# Table reproduction report","",f"Status: **{report['status']}**. Numeric cells matched: **{matches}/{len(checks)}**.","","Computed from normalized instance/cohort/decision records. Frozen paper cells are comparison targets only.","","| Table | Matched | Cells |","|---|---:|---:|"]
 for n,v in report["tables"].items():lines.append(f"| {n} | {v['matched']} | {v['cells']} |")
 lines+=["","RQ2-A strict and common endpoints remain separate. RQ3 79/79 describes historical replay comparisons, not a replay performed by this build.","","Supplementary counts:","",json.dumps(extras,indent=2),"","Detailed mismatches, if any, are in results/validation/table_reproduction.json."]
 inside("TABLE_REPRODUCTION_REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
 print(json.dumps({k:v for k,v in report.items() if k not in ["checks"]},indent=2))
 return 0 if report["status"]=="PASS" else 1
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--all",action="store_true");p.add_argument("--data",default="data/normalized");a=p.parse_args();offline_guard();raise SystemExit(run(a.data))
