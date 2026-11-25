-- Two things: records that cannot be bent afterwards, and everything a film needs so that no story lives in the program.

-- ============ 1. money tells the truth ============
-- why a request ended the way it did. A request that left this computer is only free when something proves it.
ALTER TABLE api_request ADD COLUMN outcome_basis TEXT;
ALTER TABLE api_request ADD COLUMN sent_at TEXT;
ALTER TABLE api_request ADD COLUMN ended_at TEXT;
CREATE TRIGGER api_free_needs_proof BEFORE UPDATE OF state ON api_request WHEN NEW.state='failed' AND (
    (OLD.state='reserved' AND COALESCE(NEW.outcome_basis,'')<>'not_sent')
    OR (OLD.state='sent' AND COALESCE(NEW.outcome_basis,'') NOT IN('never_connected','refused_before_work'))
    OR (OLD.state='unknown' AND COALESCE(NEW.outcome_basis,'') NOT IN('user_checked_account','provider_confirmed')))
  BEGIN SELECT RAISE(ABORT,'a request that was sent is only free when something proves it'); END;
CREATE TRIGGER api_facts_fixed BEFORE UPDATE OF envelope_id,profile_id,prompt,request_json,request_hash,requested_by,est_amount,created_at,idem_key,capability,attempt_id,voice_package_id,sfx_package_id ON api_request
  BEGIN SELECT RAISE(ABORT,'what was asked for, and its price, cannot be rewritten'); END;
CREATE TRIGGER api_cost_fixed BEFORE UPDATE OF actual_amount ON api_request WHEN OLD.actual_amount IS NOT NULL AND NEW.actual_amount IS NOT OLD.actual_amount
  BEGIN SELECT RAISE(ABORT,'a recorded cost cannot be changed'); END;
CREATE TRIGGER api_cost_sane BEFORE UPDATE OF actual_amount ON api_request WHEN NEW.actual_amount<0 BEGIN SELECT RAISE(ABORT,'a cost cannot be negative'); END;
CREATE TRIGGER api_no_delete BEFORE DELETE ON api_request BEGIN SELECT RAISE(ABORT,'paid requests are never deleted'); END;
CREATE TRIGGER api_result_no_update BEFORE UPDATE ON api_result BEGIN SELECT RAISE(ABORT,'results are fixed'); END;
CREATE TRIGGER api_result_no_delete BEFORE DELETE ON api_result BEGIN SELECT RAISE(ABORT,'results are fixed'); END;
CREATE TRIGGER envelope_fixed BEFORE UPDATE OF film_id,limit_amount,max_per_request,unit,opened_by ON spend_envelope
  BEGIN SELECT RAISE(ABORT,'an allowance cannot be changed: close it and open another'); END;
CREATE TRIGGER envelope_stays_closed BEFORE UPDATE OF closed_at ON spend_envelope WHEN OLD.closed_at IS NOT NULL BEGIN SELECT RAISE(ABORT,'a closed allowance stays closed'); END;
CREATE TRIGGER envelope_no_delete BEFORE DELETE ON spend_envelope BEGIN SELECT RAISE(ABORT,'allowances are never deleted'); END;
CREATE TRIGGER ledger_no_update BEFORE UPDATE ON ledger_charge BEGIN SELECT RAISE(ABORT,'ledger rows are fixed: add a correcting row'); END;
CREATE TRIGGER ledger_no_delete BEFORE DELETE ON ledger_charge BEGIN SELECT RAISE(ABORT,'ledger rows are fixed: add a correcting row'); END;
CREATE TRIGGER connector_enable_needs_test BEFORE UPDATE OF generation_enabled ON connector WHEN NEW.generation_enabled=1 AND OLD.generation_enabled=0 AND (
    NEW.secret_ref IS NULL OR COALESCE((SELECT outcome FROM connector_check WHERE connector_id=NEW.id AND kind='auth' ORDER BY id DESC LIMIT 1),'')<>'ok')
  BEGIN SELECT RAISE(ABORT,'a connector is switched on only after a successful test'); END;

-- ============ 2. evidence stays as it was given ============
CREATE TRIGGER report_no_update BEFORE UPDATE ON report BEGIN SELECT RAISE(ABORT,'reports are fixed'); END;
CREATE TRIGGER report_no_delete BEFORE DELETE ON report BEGIN SELECT RAISE(ABORT,'reports are fixed'); END;
CREATE TRIGGER finding_fixed BEFORE UPDATE OF report_id,defect_class,severity,evidence_id,frame_from,frame_to,region_json,requirement_ref,statement,reason,next_action ON finding
  BEGIN SELECT RAISE(ABORT,'findings are fixed; only your verdict on them may be added'); END;
CREATE TRIGGER finding_verdict_once BEFORE UPDATE OF human_verdict ON finding WHEN OLD.human_verdict IS NOT NULL AND NEW.human_verdict IS NULL BEGIN SELECT RAISE(ABORT,'a verdict can be changed, not erased'); END;
CREATE TRIGGER finding_no_delete BEFORE DELETE ON finding BEGIN SELECT RAISE(ABORT,'findings are fixed'); END;
CREATE TRIGGER criterion_no_update BEFORE UPDATE ON criterion_result BEGIN SELECT RAISE(ABORT,'reports are fixed'); END;
CREATE TRIGGER criterion_no_delete BEFORE DELETE ON criterion_result BEGIN SELECT RAISE(ABORT,'reports are fixed'); END;
CREATE TRIGGER serve_no_update BEFORE UPDATE ON evidence_serve BEGIN SELECT RAISE(ABORT,'evidence records are fixed'); END;
CREATE TRIGGER serve_no_delete BEFORE DELETE ON evidence_serve BEGIN SELECT RAISE(ABORT,'evidence records are fixed'); END;
CREATE TRIGGER label_no_update BEFORE UPDATE ON human_label BEGIN SELECT RAISE(ABORT,'your judgements are fixed: add a new one'); END;
CREATE TRIGGER label_no_delete BEFORE DELETE ON human_label BEGIN SELECT RAISE(ABORT,'your judgements are fixed: add a new one'); END;
CREATE TRIGGER check_no_update BEFORE UPDATE ON check_result BEGIN SELECT RAISE(ABORT,'measurements are fixed'); END;
CREATE TRIGGER check_no_delete BEFORE DELETE ON check_result BEGIN SELECT RAISE(ABORT,'measurements are fixed'); END;
CREATE TRIGGER vpk_fixed BEFORE UPDATE ON voice_package BEGIN SELECT RAISE(ABORT,'a package revision is fixed'); END;
CREATE TRIGGER vpk_no_delete BEFORE DELETE ON voice_package BEGIN SELECT RAISE(ABORT,'a package revision is fixed'); END;
CREATE TRIGGER xpk_fixed BEFORE UPDATE ON sfx_package BEGIN SELECT RAISE(ABORT,'a package revision is fixed'); END;
CREATE TRIGGER xpk_no_delete BEFORE DELETE ON sfx_package BEGIN SELECT RAISE(ABORT,'a package revision is fixed'); END;
CREATE TRIGGER gpk_fixed BEFORE UPDATE ON video_package BEGIN SELECT RAISE(ABORT,'a package revision is fixed'); END;
CREATE TRIGGER gpk_no_delete BEFORE DELETE ON video_package BEGIN SELECT RAISE(ABORT,'a package revision is fixed'); END;
CREATE TRIGGER profile_dates_forward BEFORE UPDATE OF expires_on ON provider_profile WHEN NEW.expires_on>date('now','+120 days') BEGIN SELECT RAISE(ABORT,'provider facts are trusted for at most 120 days'); END;
ALTER TABLE event ADD COLUMN chain TEXT;

-- ============ 3. attempts that never happened, and reopening ============
CREATE TABLE attempt_withdrawal(
  attempt_id TEXT PRIMARY KEY REFERENCES attempt(id), reason TEXT NOT NULL CHECK(length(reason)>=5), withdrawn_by TEXT NOT NULL CHECK(withdrawn_by IN('user','system')), at TEXT NOT NULL);
CREATE TRIGGER withdrawal_rules BEFORE INSERT ON attempt_withdrawal WHEN
    (SELECT outcome FROM attempt WHERE id=NEW.attempt_id)<>'pending' OR (SELECT result_asset_id FROM attempt WHERE id=NEW.attempt_id) IS NOT NULL
    OR EXISTS(SELECT 1 FROM api_request r WHERE r.attempt_id=NEW.attempt_id AND NOT (r.state='failed' AND COALESCE(r.outcome_basis,'') IN('not_sent','never_connected','refused_before_work','user_checked_account','provider_confirmed')))
  BEGIN SELECT RAISE(ABORT,'only an attempt that produced nothing and cost nothing can be withdrawn'); END;
CREATE TRIGGER withdrawal_closes AFTER INSERT ON attempt_withdrawal BEGIN UPDATE attempt SET outcome='no_result' WHERE id=NEW.attempt_id; END;
CREATE TRIGGER withdrawal_no_update BEFORE UPDATE ON attempt_withdrawal BEGIN SELECT RAISE(ABORT,'attempt history is fixed'); END;
CREATE TRIGGER withdrawal_no_delete BEFORE DELETE ON attempt_withdrawal BEGIN SELECT RAISE(ABORT,'attempt history is fixed'); END;
CREATE VIEW attempt_counted AS SELECT * FROM attempt a WHERE NOT EXISTS(SELECT 1 FROM attempt_withdrawal w WHERE w.attempt_id=a.id);
CREATE TABLE target_reopen(
  id TEXT PRIMARY KEY, target_id TEXT NOT NULL REFERENCES generation_target(id), after_attempt_id TEXT NOT NULL REFERENCES attempt(id),
  reason TEXT NOT NULL CHECK(length(reason)>=5), reopened_by TEXT NOT NULL CHECK(reopened_by='user'), at TEXT NOT NULL);
DROP TRIGGER attempt_rules;
CREATE TRIGGER attempt_rules BEFORE INSERT ON attempt WHEN
    (SELECT state FROM generation_target WHERE id=NEW.target_id)<>'open'
    OR NEW.attempt_no<>1+(SELECT COUNT(*) FROM attempt WHERE target_id=NEW.target_id)
    OR 1+(SELECT COUNT(*) FROM attempt_counted WHERE target_id=NEW.target_id)>(SELECT max_attempts FROM generation_target WHERE id=NEW.target_id)+(SELECT COALESCE(SUM(extra),0) FROM attempt_extension WHERE target_id=NEW.target_id)
  BEGIN SELECT RAISE(ABORT,'attempt limit reached or target closed: stop and report'); END;
DROP TRIGGER attempt_retry_reasoned;
CREATE TRIGGER attempt_retry_reasoned BEFORE INSERT ON attempt WHEN (SELECT COUNT(*) FROM attempt_counted WHERE target_id=NEW.target_id)>0 AND (
    NEW.reason IS NULL OR length(NEW.reason)<10 OR json_array_length(NEW.findings_json)<1
    OR NEW.prior_attempt_id IS NOT (SELECT id FROM attempt_counted WHERE target_id=NEW.target_id ORDER BY attempt_no DESC LIMIT 1)
    OR NOT ((SELECT outcome FROM attempt WHERE id=NEW.prior_attempt_id) IN('needs_correction','rejected','refused')
        OR ((SELECT outcome FROM attempt WHERE id=NEW.prior_attempt_id)='accepted' AND EXISTS(SELECT 1 FROM target_reopen o WHERE o.target_id=NEW.target_id AND o.after_attempt_id=NEW.prior_attempt_id))))
  BEGIN SELECT RAISE(ABORT,'a retry needs a reason, a finding and a judged earlier attempt'); END;
DROP TRIGGER attempt_not_identical;
CREATE TRIGGER attempt_not_identical BEFORE INSERT ON attempt WHEN EXISTS(SELECT 1 FROM attempt_counted a WHERE a.target_id=NEW.target_id AND a.spec_hash=NEW.spec_hash)
  BEGIN SELECT RAISE(ABORT,'identical specification was already tried'); END;
CREATE TRIGGER target_state_rules BEFORE UPDATE OF state ON generation_target WHEN OLD.state='approved' AND NEW.state='open' AND NOT EXISTS(SELECT 1 FROM target_reopen WHERE target_id=NEW.id)
  BEGIN SELECT RAISE(ABORT,'an approved item is reopened by the user with a reason'); END;

-- ============ 4. films ============
ALTER TABLE film ADD COLUMN created_at TEXT;
ALTER TABLE film ADD COLUMN logline TEXT;
ALTER TABLE source_doc ADD COLUMN original_name TEXT;
ALTER TABLE source_doc ADD COLUMN format TEXT;
ALTER TABLE scene ADD COLUMN span_from INT;
ALTER TABLE scene ADD COLUMN span_to INT;
ALTER TABLE scene ADD COLUMN state TEXT NOT NULL DEFAULT 'open';
ALTER TABLE shot ADD COLUMN ordinal INT NOT NULL DEFAULT 0;
CREATE TABLE film_setting(film_id TEXT NOT NULL REFERENCES film(id), key TEXT NOT NULL, value_json TEXT NOT NULL, set_by TEXT NOT NULL, set_at TEXT NOT NULL, PRIMARY KEY(film_id,key));
-- canon shared by a fictional universe and used by a film; a benchmark universe never feeds a film
CREATE TABLE film_canon(film_id TEXT NOT NULL REFERENCES film(id), entity_id TEXT NOT NULL REFERENCES canon_entity(id), PRIMARY KEY(film_id,entity_id));

-- every creative document an agent (or an import) proposes. Text is fixed; a correction is a new revision; only the user's approval puts it into effect.
CREATE TABLE work_doc(
  id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id),
  kind TEXT NOT NULL CHECK(kind IN('production_plan','canon','shot_list','image_plan','motion_plan','sound_plan','cut','release','research')),
  scope TEXT NOT NULL, rev INT NOT NULL, doc_json TEXT NOT NULL, content_hash TEXT NOT NULL, based_on_json TEXT NOT NULL DEFAULT '[]',
  made_by TEXT NOT NULL, agent_hash TEXT REFERENCES agent_version(hash), task_id TEXT REFERENCES task(id), changed_vs_prior TEXT, problems_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'proposed' CHECK(status IN('proposed','approved','returned','superseded')), decided_by TEXT, decided_at TEXT, decision_note TEXT, created_at TEXT NOT NULL,
  UNIQUE(film_id,kind,scope,rev), CHECK(rev=1 OR changed_vs_prior IS NOT NULL), CHECK(status<>'approved' OR (decided_by IS NOT NULL AND decided_by='user')));
CREATE TRIGGER work_doc_fixed BEFORE UPDATE OF film_id,kind,scope,rev,doc_json,content_hash,based_on_json,made_by,agent_hash,task_id,changed_vs_prior,problems_json ON work_doc
  BEGIN SELECT RAISE(ABORT,'a proposed document is fixed: make a new revision'); END;
CREATE TRIGGER work_doc_no_delete BEFORE DELETE ON work_doc BEGIN SELECT RAISE(ABORT,'documents are never deleted'); END;

-- a still the film needs: an identity anchor, a reference, or a frame of a shot
CREATE TABLE image_spec(
  id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id), code TEXT NOT NULL, ordinal INT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN('anchor','reference','frame','comparison')), entity_id TEXT REFERENCES canon_entity(id), shot_id TEXT REFERENCES shot(id),
  role TEXT CHECK(role IN('still','first','last','plate','layer')), aspect TEXT NOT NULL, refs_json TEXT NOT NULL, prompt TEXT NOT NULL CHECK(length(prompt)>0),
  base_code TEXT, shows_json TEXT NOT NULL DEFAULT '[]', doc_id TEXT REFERENCES work_doc(id), content_hash TEXT NOT NULL, retired INT NOT NULL DEFAULT 0, UNIQUE(film_id,code));
-- how a shot moves between two frames, before a Gemini package exists
CREATE TABLE motion_plan(
  shot_id TEXT PRIMARY KEY REFERENCES shot(id), first_image TEXT NOT NULL, last_image TEXT NOT NULL, camera TEXT NOT NULL CHECK(camera IN('locked','moving')),
  zones_json TEXT NOT NULL DEFAULT '[]', draft_json TEXT NOT NULL, doc_id TEXT REFERENCES work_doc(id), content_hash TEXT NOT NULL);

-- an edit: ordered decisions with reasons, referring to shots, lines and cues rather than files
CREATE TABLE cut_rev(
  id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id), scope TEXT NOT NULL, rev INT NOT NULL, base_rev INT,
  doc_json TEXT NOT NULL, content_hash TEXT NOT NULL, commands_json TEXT NOT NULL DEFAULT '[]', author TEXT NOT NULL, agent_hash TEXT REFERENCES agent_version(hash), task_id TEXT REFERENCES task(id),
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN('draft','approved','superseded')), approved_by TEXT, note TEXT, created_at TEXT NOT NULL,
  UNIQUE(film_id,scope,rev), CHECK(status<>'approved' OR (approved_by IS NOT NULL AND approved_by='user')));
CREATE TRIGGER cut_rev_fixed BEFORE UPDATE OF film_id,scope,rev,base_rev,doc_json,content_hash,commands_json,author,agent_hash,task_id ON cut_rev BEGIN SELECT RAISE(ABORT,'an edit revision is fixed: make a new one'); END;
CREATE TRIGGER cut_rev_no_delete BEFORE DELETE ON cut_rev BEGIN SELECT RAISE(ABORT,'edit history is fixed'); END;
CREATE TABLE cut_note(
  id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id), scope TEXT NOT NULL, cut_rev_id TEXT REFERENCES cut_rev(id), frame INT, clip_id TEXT,
  text TEXT NOT NULL CHECK(length(text)>0), by TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open' CHECK(state IN('open','answered','dropped')), answer TEXT, created_at TEXT NOT NULL);
ALTER TABLE timeline_rev ADD COLUMN cut_rev_id TEXT REFERENCES cut_rev(id);
ALTER TABLE timeline_rev ADD COLUMN status_json TEXT;
ALTER TABLE timeline_rev ADD COLUMN created_at TEXT;
ALTER TABLE render ADD COLUMN created_at TEXT;
ALTER TABLE render ADD COLUMN seconds REAL;
ALTER TABLE media_job ADD COLUMN film_id TEXT REFERENCES film(id);
ALTER TABLE media_job ADD COLUMN created_at TEXT;
ALTER TABLE media_job ADD COLUMN started_at TEXT;
ALTER TABLE media_job ADD COLUMN ended_at TEXT;
ALTER TABLE media_job ADD COLUMN result_json TEXT;

-- ============ 5. agents are measured ============
ALTER TABLE task ADD COLUMN tier TEXT;
ALTER TABLE task ADD COLUMN priority INT NOT NULL DEFAULT 50;
ALTER TABLE task ADD COLUMN created_at TEXT;
ALTER TABLE task ADD COLUMN closed_at TEXT;
ALTER TABLE task ADD COLUMN why_blocked TEXT;
ALTER TABLE agent_version ADD COLUMN content TEXT;
ALTER TABLE agent_version ADD COLUMN seen_at TEXT;
ALTER TABLE usage_log ADD COLUMN role TEXT;
ALTER TABLE usage_log ADD COLUMN film_id TEXT;
ALTER TABLE usage_log ADD COLUMN cached_tokens INT;
ALTER TABLE usage_log ADD COLUMN cache_write_tokens INT;
ALTER TABLE usage_log ADD COLUMN seconds REAL;
ALTER TABLE usage_log ADD COLUMN turns INT;
ALTER TABLE usage_log ADD COLUMN agent_hash TEXT;
ALTER TABLE usage_log ADD COLUMN outcome TEXT;
ALTER TABLE usage_log ADD COLUMN at TEXT;
-- how long things really take, so that estimates come from history
CREATE TABLE time_log(
  id TEXT PRIMARY KEY, film_id TEXT REFERENCES film(id), activity TEXT NOT NULL CHECK(activity IN('agent','provider_wait','user_wait','user_work','import','inspection','edit','render','check','correction','backup')),
  basis_json TEXT NOT NULL DEFAULT '{}', seconds REAL NOT NULL CHECK(seconds>=0), units REAL, unit TEXT, ref TEXT, at TEXT NOT NULL);

-- ============ 6. release ============
CREATE TABLE release_package(
  id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id), rev INT NOT NULL, kind TEXT NOT NULL CHECK(kind IN('long','short')), render_id TEXT REFERENCES render(id),
  doc_json TEXT NOT NULL, folder TEXT, status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN('draft','ready','approved')), approved_by TEXT, created_at TEXT NOT NULL, UNIQUE(film_id,kind,rev),
  CHECK(status<>'approved' OR (approved_by IS NOT NULL AND approved_by='user')));
