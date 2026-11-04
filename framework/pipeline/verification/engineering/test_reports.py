from __future__ import annotations

import xml.etree.ElementTree as ET
import hashlib
import re
import time
from pathlib import Path
from typing import Any


TEST_EXECUTION_VERSION = '2'


def _report_files(workspace):
    return sorted(set(workspace.rglob('target/surefire-reports/TEST-*.xml')) |
                  set(workspace.rglob('target/failsafe-reports/TEST-*.xml')))


def _report_identity(path):
    stat=path.stat()
    return {'sha256':hashlib.sha256(path.read_bytes()).hexdigest(), 'mtime_ns':stat.st_mtime_ns,
            'ctime_ns':stat.st_ctime_ns, 'size':stat.st_size}


def snapshot_maven_test_reports(workspace):
    """Capture before EACH command, including fallback; never delete old reports."""
    workspace=Path(workspace)
    files={str(p.relative_to(workspace)).replace('\\','/'):_report_identity(p) for p in _report_files(workspace)}
    return {'started_ns':time.time_ns(),'files':files}


def execution_confirmed(summary, target_class=None, target_method=None):
    """Shared acceptance predicate, also used when consuming persisted evidence."""
    fresh={r.get('path'):r for r in summary.get('fresh_reports') or []}
    recorded=summary.get('reports') or []
    if not fresh or not recorded or any(r.get('path') not in fresh or
            r.get('sha256')!=fresh[r.get('path')].get('sha256') for r in recorded):
        return False
    if any(not isinstance(r.get('command_started_ns'),int) or
           not isinstance(r.get('mtime_ns'),int) or r['mtime_ns']<r['command_started_ns'] for r in fresh.values()):
        return False
    matched=[c for r in recorded for c in r.get('cases',[]) if c.get('class')==summary.get('target_class')
             and _method_pattern(summary.get('target_method') or '').fullmatch(c.get('method') or '')]
    if (not matched or any(c.get('failed') or c.get('error') for c in matched)
            or sum(c.get('skipped') is False for c in matched)!=summary.get('invocations')
            or len({(c['class'],c['method']) for c in matched})!=len(matched)):
        return False
    return (summary.get('execution_evidence_version')==TEST_EXECUTION_VERSION
            and summary.get('execution_status')=='EXECUTED'
            and summary.get('target_method_observed') is True and summary.get('invocations',0)>0
            and not summary.get('parse_errors') and not summary.get('ambiguous_cases')
            and not summary.get('target_failures') and not summary.get('target_errors')
            and not summary.get('failures') and not summary.get('errors')
            and bool(summary.get('fresh_reports'))
            and (target_class is None or summary.get('target_class')==target_class)
            and (target_method is None or summary.get('target_method')==target_method))


def _method_pattern(method):
    java_type=r'[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*(?:\[\])*'
    return re.compile(re.escape(method)+r'(?:\((?:'+java_type+r'(?:,\s*'+java_type+r')*)?\))?(?:\[\d+\])?\Z')


def parse_maven_test_reports(workspace: Path, target_class: str, target_method: str, *, before_reports=None) -> dict[str, Any]:
    workspace=Path(workspace)
    report_files = _report_files(workspace)
    summary = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "elapsed_seconds": 0.0,
               "target_class_observed": False, "target_method_observed": False,
               "invocations": 0, "reports": [], "parse_errors": [],
               'execution_evidence_version':TEST_EXECUTION_VERSION,'target_class':target_class,'target_method':target_method,
               'fresh_reports':[], 'stale_reports':[], 'ambiguous_cases':[], 'target_skipped':0,
               'target_failures':0,'target_errors':0,'matched_cases':0,'execution_status':'REPORT_MISSING'}
    summary["_report_payloads"] = {}
    seen=set()
    # Surefire/Failsafe Java method names, optional signature and numeric invocation
    # suffix. Arbitrary @DisplayName text is deliberately not guessed.
    method_pattern=_method_pattern(target_method)
    for path in report_files:
        try:
            relative=str(path.relative_to(workspace)).replace('\\','/')
            identity=_report_identity(path)
            if (before_reports is None or identity==before_reports.get('files',{}).get(relative)
                    or identity['mtime_ns']<before_reports.get('started_ns',0)):
                summary['stale_reports'].append(relative)
                continue
            report_bytes = path.read_bytes()
            if hashlib.sha256(report_bytes).hexdigest() != identity['sha256']:
                raise ValueError('REPORT_CHANGED_WHILE_READING')
            root = ET.fromstring(report_bytes)
            if root.tag not in ('testsuite','testsuites'):
                raise ValueError('UNSUPPORTED_TEST_REPORT_ROOT')
            cases = []
            for case in root.findall(".//testcase"):
                classname, name = case.attrib.get("classname", ""), case.attrib.get("name", "")
                failure = case.find("failure"); error = case.find("error")
                problem=failure if failure is not None else error
                cases.append({"class": classname, "method": name,
                              'skipped':case.find('skipped') is not None,'failed':failure is not None,'error':error is not None,
                              "failure_type": problem.attrib.get("type") if problem is not None else None,
                              "failure_message": (problem.attrib.get("message") or "")[:500] if problem is not None else None})
            # Root counters are not execution proof, but conflicting counters
            # make the report unusable. Nested testsuites are counted by cases.
            for key,predicate in [('tests',lambda c:True),('skipped',lambda c:c['skipped']),
                                  ('failures',lambda c:c['failed']),('errors',lambda c:c['error'])]:
                count=sum(predicate(c) for c in cases)
                if key in root.attrib and int(root.attrib[key])!=count:
                    raise ValueError('REPORT_COUNTER_MISMATCH:'+key)
            elapsed=float(root.attrib.get('time',0) or 0)
            if _report_identity(path)!=identity:
                raise ValueError('REPORT_CHANGED_WHILE_READING')
            summary['fresh_reports'].append({'path':relative,**identity,'command_started_ns':before_reports['started_ns']})
            for c in cases:
                summary['tests']+=1;summary['skipped']+=int(c['skipped'])
                summary['failures']+=int(c['failed']);summary['errors']+=int(c['error'])
                if c['class']!=target_class:continue
                summary['target_class_observed']=True
                if not method_pattern.fullmatch(c['method']):continue
                key=(c['class'],c['method'])
                if key in seen:summary['ambiguous_cases'].append(list(key))
                seen.add(key);summary['matched_cases']+=1
                summary['target_skipped']+=int(c['skipped'])
                summary['target_failures']+=int(c['failed']);summary['target_errors']+=int(c['error'])
                if not c['skipped']:summary['invocations']+=1
            summary['elapsed_seconds']+=elapsed
            summary["reports"].append({"path": relative, 'sha256':identity['sha256'], "cases": cases})
            summary["_report_payloads"][relative] = report_bytes.decode("utf-8")
        except (OSError, ET.ParseError, ValueError) as exc:
            summary["parse_errors"].append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
    summary["report_count"] = len(report_files)
    summary['execution_status']=('REPORT_INVALID' if summary['parse_errors'] else
        'TARGET_MAPPING_AMBIGUOUS' if summary['ambiguous_cases'] else
        'REPORT_FRESHNESS_UNKNOWN' if before_reports is None else
        'REPORT_MISSING' if not report_files else
        'REPORT_STALE' if not summary['fresh_reports'] else
        'TARGET_ALL_SKIPPED' if summary['matched_cases'] and not summary['invocations'] else
        'TARGET_NOT_FOUND' if not summary['matched_cases'] else
        'TARGET_FAILED' if summary['target_failures'] or summary['target_errors'] else
        'TEST_RUN_FAILED' if summary['failures'] or summary['errors'] else 'EXECUTED')
    summary['target_method_observed']=summary['execution_status'] in ('EXECUTED','TARGET_FAILED','TEST_RUN_FAILED')
    return summary
