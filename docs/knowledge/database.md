<!-- made by tools/knowledge.py from the code; do not edit -->
# Database

Schema version 3: 1 baseline, 2 backup, 3 truth_and_films. 99 tables.

`account_label`, `agent_version`, `api_request`, `api_request_ref`, `api_result`, `approval`, `approval_dep`, `asset`, `asset_binding`, `asset_edge`, `asset_licence`, `asset_use`, `attempt`, `attempt_extension`, `attempt_withdrawal`, `audio_take`, `backup_run`, `backup_target`, `bench_run`, `camera`, `canon_entity`, `canon_revision`, `check_result`, `connector`, `connector_capability`, `connector_check`, `criterion_result`, `current_hash`, `cut_note`, `cut_rev`, `decision`, `event`, `evidence_serve`, `film`, `film_canon`, `film_setting`, `finding`, `frame_pair`, `gen_attempt`, `generation_target`, `hard_cap`, `human_label`, `image_spec`, `invariant`, `ledger_charge`, `lesson`, `lesson_use`, `library_item`, `licence_terms`, `manual_task`, `media_job`, `meta`, `motion_plan`, `policy_proposal`, `policy_snapshot`, `preflight_item`, `preflight_run`, `production_mode`, `pronunciation`, `provider_profile`, `recovery_proposal`, `reference_item`, `release_item`, `release_package`, `render`, `replica`, `report`, `restore_drill`, `scene`, `schema_step`, `script_line`, `sfx_package`, `shot`, `shot_canon`, `shot_span`, `shot_spec`, `slot`, `slot_version`, `sound_cue`, `source_doc`, `source_span`, `spend_envelope`, `stage_pause`, `story_cite`, `story_node`, `target_reopen`, `task`, `task_dep`, `time_log`, `timeline`, `timeline_rev`, `universe`, `usage_log`, `usage_snapshot`, `video_package`, `voice_binding`, `voice_package`, `voice_package_line`, `work_doc`

## Rules the database itself enforces

| Rule | What it says |
| --- | --- |
| api_audio_needs_package | voice generation needs a package with a live approval |
| api_connector_gate | integration is off, or this capability is not allowed |
| api_cost_fixed | a recorded cost cannot be changed |
| api_cost_sane | a cost cannot be negative |
| api_facts_fixed | what was asked for, and its price, cannot be rewritten |
| api_free_needs_proof | a request that was sent is only free when something proves it |
| api_mode_gate | this film uses manual mode for that service |
| api_needs_attempt | request needs an open attempt with no answer yet |
| api_no_credit_spill | would exceed verified included credits: no spill into paid balance |
| api_no_delete | paid requests are never deleted |
| api_no_provider_switch | package is for a different provider: no silent switch |
| api_no_unchanged_retry | identical request already answered: change something or reuse the result |
| api_not_while_paused | stage is paused: the user chooses retry, manual, wait or cancel |
| api_profile_fresh | profile expired, or not an API the studio may call |
| api_recheck_before_send | connection or capability not freshly verified |
| api_result_no_delete | results are fixed |
| api_result_no_update | results are fixed |
| api_sfx_needs_package | sound-effect generation needs a package approved by the user |
| api_sfx_provider | package is for a different provider: no silent switch |
| api_starts_reserved | a paid request must be reserved before it is sent |
| api_state_machine | illegal request state change |
| approval_dep_no_delete | approval inputs are append-only |
| approval_dep_no_update | approval inputs are append-only |
| approval_no_delete | approvals are append-only |
| approval_no_update | approvals are append-only |
| asset_immutable | asset identity is immutable |
| asset_no_delete | assets are never deleted |
| attempt_frozen | attempt history is fixed |
| attempt_needs_live_package | package has no live approval |
| attempt_no_delete | attempt history is fixed |
| attempt_not_identical | identical specification was already tried |
| attempt_retry_reasoned | a retry needs a reason, a finding and a judged earlier attempt |
| attempt_rules | attempt limit reached or target closed: stop and report |
| backup_run_no_delete | backup history is append-only |
| backup_run_no_update | backup history is append-only |
| bench_plan_fixed | benchmark plan is fixed before results |
| canon_locked | locked canon: create a new revision |
| check_no_delete | measurements are fixed |
| check_no_update | measurements are fixed |
| connector_check_no_delete | connection history is append-only |
| connector_check_no_update | connection history is append-only |
| connector_enable_needs_test | a connector is switched on only after a successful test |
| criterion_no_delete | reports are fixed |
| criterion_no_update | reports are fixed |
| cut_rev_fixed | an edit revision is fixed: make a new one |
| cut_rev_no_delete | edit history is fixed |
| envelope_fixed | an allowance cannot be changed: close it and open another |
| envelope_no_delete | allowances are never deleted |
| envelope_stays_closed | a closed allowance stays closed |
| event_no_delete | event log is append-only |
| event_no_update | event log is append-only |
| final_use_rights | a source of this asset is not cleared for this film |
| finding_evidence_owned | finding cites evidence never served to this task |
| finding_fixed | findings are fixed; only your verdict on them may be added |
| finding_no_delete | findings are fixed |
| finding_verdict_once | a verdict can be changed, not erased |
| gpk_fixed | a package revision is fixed |
| gpk_no_delete | a package revision is fixed |
| hard_cap_holds | hard cap for this category would be exceeded |
| hard_cap_no_delete | a hard cap cannot be removed here |
| hard_cap_no_update | a hard cap cannot be changed here |
| label_no_delete | your judgements are fixed: add a new one |
| label_no_update | your judgements are fixed: add a new one |
| ledger_no_delete | ledger rows are fixed: add a correcting row |
| ledger_no_update | ledger rows are fixed: add a correcting row |
| library_admission | rights do not allow the shared library |
| library_scope_no_widen | retire the item and admit it again under the new scope |
| mandatory_blocks | a mandatory criterion is not met or not determined |
| mode_no_update | mode history is append-only |
| no_cross_film_reuse | a source of this asset may not be reused in another film |
| no_overspend | spend envelope refused this request |
| no_self_review | the maker may not review its own work |
| package_needs_live_frames | frame pair has no live approval |
| profile_dates_forward | provider facts are trusted for at most 120 days |
| report_no_delete | reports are fixed |
| report_no_update | reports are fixed |
| restore_drill_no_delete | restore history is append-only |
| restore_drill_no_update | restore history is append-only |
| script_locked | locked source: import a new revision |
| serve_no_delete | evidence records are fixed |
| serve_no_update | evidence records are fixed |
| slot_version_decided | a decided version is fixed: propose a new one |
| slot_version_no_delete | version history is fixed |
| span_immutable | script text is immutable: import a new revision |
| span_no_delete | script text is immutable: import a new revision |
| spec_locked | locked shot spec: create a new revision |
| target_limit_fixed | use an attempt extension granted by the user |
| target_state_rules | an approved item is reopened by the user with a reason |
| terms_immutable | verified terms are fixed: record a new version |
| timeline_locked | locked timeline: create a new revision |
| video_profile_manual | video packages are for a current manual surface only |
| voice_lasts | voice will disappear too soon to cast |
| vpk_fixed | a package revision is fixed |
| vpk_no_delete | a package revision is fixed |
| withdrawal_closes |  |
| withdrawal_no_delete | attempt history is fixed |
| withdrawal_no_update | attempt history is fixed |
| withdrawal_rules | only an attempt that produced nothing and cost nothing can be withdrawn |
| work_doc_fixed | a proposed document is fixed: make a new revision |
| work_doc_no_delete | documents are never deleted |
| xpk_fixed | a package revision is fixed |
| xpk_no_delete | a package revision is fixed |
