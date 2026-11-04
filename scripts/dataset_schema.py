"""Unchanged FIELDS constant extracted from the Artifact normalizer."""
FIELDS={
"instances":["instance_id","project","project_revision","source_file","method_signature","source_hash","source_hash_kind","method_source_hash","issue_type","source_record_id"],
"executions":["execution_id","instance_id","experiment_id","variant","configuration_id","source_record_id"],
"applicability":["instance_id","execution_id","applicability_decision","original_decision","applicability_reason","evidence_reference","configuration_id","applicability_rule_id"],
"candidates":["candidate_id","instance_id","execution_id","candidate_artifact","candidate_hash","exported_hash","artifact_kind","generation_configuration","generation_status","evidence_reference"],
"verification_checks":["check_record_id","candidate_id","instance_id","execution_id","check_id","check_type","result","original_result","evidence_reference","validation_configuration","evaluation_scope"],
"decisions":["instance_id","candidate_id","execution_id","decision","original_decision","decision_configuration","evidence_reference","included_in_paper"],
"cohort_membership":["cohort_id","instance_id","experiment_id","execution_id","membership_role"],
"evidence_manifest":["artifact_id","linked_instance_id","linked_candidate_id","evidence_type","source_reference","exported_relative_path","original_sha256","exported_sha256","status","transformation"],
"methods":["mapping_id","project","project_revision","source_file","method_signature","source_hash","target_instance_ids","target_issue_types","source_record_id"],
"rq3_pairs":["instance_id","original_execution_id","replay_execution_id","original_candidate_id","replay_candidate_id","source_hash_equal","candidate_hash_equal","target_test_identity_equal","post_outcome_equal","final_decision_equal","historical_replay_status","evidence_reference"],
"exclusions":["instance_id","execution_id","original_decision","exclusion_reason","evidence_reference"],
"coverage":["instance_id","execution_id","framework","candidate_available","original_final_status","evidence_reference"],
"candidate_artifact_relations":["candidate_id","execution_id","artifact_role","artifact_id","original_sha256","exported_relative_path","evidence_reference"],
}
