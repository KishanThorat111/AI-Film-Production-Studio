-- Studio authoritative store, v2.2 (P0). SQLite >= 3.50, WAL, foreign_keys=ON. Media bytes live outside the DB.
-- Nothing in this file is specific to any story, character, location or provider.
-- Rules are enforced here, not in prompts. actor='user' is writable only by the local UI process.
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ============ 1. productions and sources ============
CREATE TABLE universe(id TEXT PRIMARY KEY, title TEXT NOT NULL);
CREATE TABLE film(
  id TEXT PRIMARY KEY, universe_id TEXT NOT NULL REFERENCES universe(id), title TEXT NOT NULL,
  fps_num INT NOT NULL, fps_den INT NOT NULL DEFAULT 1, width INT NOT NULL, height INT NOT NULL,
  profile_json TEXT NOT NULL DEFAULT '{}',          -- per-film production profile: look, techniques, tolerances
  is_benchmark INT NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK(status IN('development','preproduction','production','post','released','archived')));
CREATE TABLE source_doc(
  id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id),
  kind TEXT NOT NULL CHECK(kind IN('script','story_lock','note')), revision INT NOT NULL, sha256 TEXT NOT NULL,
  relpath TEXT NOT NULL, precedence INT NOT NULL, locked_at TEXT, locked_by TEXT, UNIQUE(film_id,kind,revision));
-- smallest citable unit of a script; every story fact cites spans
CREATE TABLE source_span(
  id TEXT PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES source_doc(id), ordinal INT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN('heading','action','dialogue','narration','sound','transition','note','other')),
  text TEXT NOT NULL, text_hash TEXT NOT NULL, UNIQUE(doc_id,ordinal));
CREATE TABLE story_node(
  id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id),
  kind TEXT NOT NULL CHECK(kind IN('event','clue','reveal','question','belief','motivation','state','open_issue')),
  statement TEXT NOT NULL, story_time TEXT,
  status TEXT NOT NULL CHECK(status IN('proposed','accepted','disputed','retired')));
CREATE TABLE story_cite(
  node_id TEXT NOT NULL REFERENCES story_node(id), span_id TEXT NOT NULL REFERENCES source_span(id), PRIMARY KEY(node_id,span_id));
-- compiled per film from ITS OWN script: generic checkers, film-specific parameters
CREATE TABLE invariant(
  id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id),
  severity TEXT NOT NULL CHECK(severity IN('critical','major','moderate')),
  method TEXT NOT NULL CHECK(method IN('measured','lint','inspected','human')),
  checker TEXT NOT NULL, params_json TEXT NOT NULL, span_id TEXT NOT NULL REFERENCES source_span(id),
  status TEXT NOT NULL CHECK(status IN('proposed','active','retired')), activated_by TEXT,
  CHECK(status<>'active' OR (activated_by IS NOT NULL AND activated_by='user')));

-- ============ 2. assets ============
CREATE TABLE asset(
  id TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN('image','video','audio','model3d','document','sequence')),
  origin TEXT NOT NULL CHECK(origin IN('api_image','api_audio','manual_video','manual_audio','derived','rendered','licensed','recorded','authored')),
  storage_class TEXT NOT NULL CHECK(storage_class IN('original','derived','proxy','cache')),
  relpath TEXT NOT NULL, bytes INT NOT NULL, probe_json TEXT, provider TEXT, provider_model TEXT, provenance_json TEXT,
  created_at TEXT NOT NULL);
CREATE UNIQUE INDEX asset_original_once ON asset(sha256) WHERE storage_class='original';
CREATE TABLE asset_edge(
  child_id TEXT NOT NULL REFERENCES asset(id), parent_id TEXT NOT NULL REFERENCES asset(id),
  op TEXT NOT NULL, op_params_json TEXT NOT NULL, tool_version TEXT NOT NULL, PRIMARY KEY(child_id,parent_id,op));
CREATE TABLE asset_binding(
  asset_id TEXT NOT NULL REFERENCES asset(id), target_type TEXT NOT NULL, target_id TEXT NOT NULL,
  role TEXT NOT NULL, bound_by TEXT NOT NULL, PRIMARY KEY(asset_id,target_type,target_id,role));
CREATE TABLE replica(
  asset_id TEXT NOT NULL REFERENCES asset(id), volume_serial TEXT NOT NULL, relpath TEXT NOT NULL,
  verified_sha256 TEXT NOT NULL, verified_at TEXT NOT NULL, PRIMARY KEY(asset_id,volume_serial));
-- one row per VERSION of a source's terms; every right is yes / no / unclear, and 'unclear' is treated as no
CREATE TABLE licence_terms(
  id TEXT PRIMARY KEY, source TEXT NOT NULL, version TEXT, url TEXT, evidence_relpath TEXT NOT NULL, checked_on TEXT NOT NULL,
  plan_required TEXT, r_store TEXT NOT NULL, r_edit TEXT NOT NULL, r_sync TEXT NOT NULL, r_commercial TEXT NOT NULL, r_youtube TEXT NOT NULL,
  r_reuse_films TEXT NOT NULL, subscription_bound INT NOT NULL CHECK(subscription_bound IN(0,1)), platforms TEXT,
  attribution_required INT NOT NULL CHECK(attribution_required IN(0,1)), ai_training_forbidden INT NOT NULL DEFAULT 0,
  content_id_risk TEXT NOT NULL CHECK(content_id_risk IN('low','possible','unknown')), ambiguity TEXT, verified_by TEXT,
  CHECK(r_store IN('yes','no','unclear') AND r_edit IN('yes','no','unclear') AND r_sync IN('yes','no','unclear')
    AND r_commercial IN('yes','no','unclear') AND r_youtube IN('yes','no','unclear') AND r_reuse_films IN('yes','no','unclear')));
CREATE TABLE asset_licence(
  asset_id TEXT PRIMARY KEY REFERENCES asset(id), terms_id TEXT NOT NULL REFERENCES licence_terms(id),
  obtained_on TEXT NOT NULL, plan_at_creation TEXT, proof_relpath TEXT, attribution_text TEXT);
-- rights follow lineage: an edited or layered sound is only as free as every source it was made from
CREATE VIEW asset_ancestry AS
  WITH RECURSIVE up(asset_id,anc) AS (SELECT id,id FROM asset UNION SELECT up.asset_id,e.parent_id FROM up JOIN asset_edge e ON e.child_id=up.anc)
  SELECT asset_id,anc FROM up;
CREATE VIEW asset_root AS
  SELECT y.asset_id, y.anc AS root_id FROM asset_ancestry y WHERE NOT EXISTS(SELECT 1 FROM asset_edge e WHERE e.child_id=y.anc);
CREATE VIEW root_rights AS
  SELECT r.asset_id, r.root_id, l.* FROM asset_root r LEFT JOIN asset_licence al ON al.asset_id=r.root_id LEFT JOIN licence_terms l ON l.id=al.terms_id;
CREATE TABLE library_item(
  asset_id TEXT PRIMARY KEY REFERENCES asset(id), scope TEXT NOT NULL CHECK(scope IN('studio','universe','character','location','film')), scope_ref TEXT,
  kind TEXT NOT NULL CHECK(kind IN('ambience','room_tone','foley','hard_fx','music','motif','impulse_response')),
  title TEXT NOT NULL, tags_json TEXT NOT NULL, analysis_json TEXT, admitted_by TEXT NOT NULL CHECK(admitted_by='user'), admitted_on TEXT NOT NULL, retired_at TEXT,
  CHECK(scope='studio' OR scope_ref IS NOT NULL));

-- ============ 3. canon ============
CREATE TABLE canon_entity(
  id TEXT PRIMARY KEY, universe_id TEXT NOT NULL REFERENCES universe(id),
  kind TEXT NOT NULL CHECK(kind IN('character','wardrobe','environment','prop','camera','look','voice','sound','rule')), name TEXT NOT NULL);
CREATE TABLE canon_revision(
  id TEXT PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES canon_entity(id), rev INT NOT NULL,
  spec_json TEXT NOT NULL, content_hash TEXT NOT NULL, valid_from TEXT, valid_to TEXT,        -- story time, not wall time
  status TEXT NOT NULL CHECK(status IN('proposed','locked','retired')), locked_by TEXT, UNIQUE(entity_id,rev),
  CHECK(status<>'locked' OR (locked_by IS NOT NULL AND locked_by='user')));
-- cinematic = a viewpoint chosen per shot; diegetic = a physical device that exists in the story world
CREATE TABLE camera(
  revision_id TEXT PRIMARY KEY REFERENCES canon_revision(id),
  mode TEXT NOT NULL CHECK(mode IN('cinematic','diegetic')), environment_id TEXT REFERENCES canon_entity(id),
  pose_json TEXT, lens_json TEXT, capture_json TEXT,
  CHECK(mode<>'diegetic' OR (pose_json IS NOT NULL AND lens_json IS NOT NULL AND environment_id IS NOT NULL)));
-- one image in an identity, environment or prop pack. depth 0 = anchor, 1 = made directly from an anchor. Never deeper.
CREATE TABLE reference_item(
  id TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES canon_revision(id), asset_id TEXT NOT NULL REFERENCES asset(id),
  yaw REAL, pitch REAL, framing TEXT, expression TEXT, light TEXT, lens TEXT, is_anchor INT NOT NULL DEFAULT 0,
  generation_depth INT NOT NULL CHECK(generation_depth BETWEEN 0 AND 1),
  status TEXT NOT NULL CHECK(status IN('candidate','approved','retired')), approved_by TEXT,
  CHECK(status<>'approved' OR (approved_by IS NOT NULL AND approved_by='user')), CHECK(is_anchor=0 OR generation_depth=0));

-- ============ 4. scenes and shots ============
CREATE TABLE scene(
  id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id), number INT NOT NULL, slug TEXT NOT NULL, story_time TEXT, UNIQUE(film_id,number));
CREATE TABLE shot(
  id TEXT PRIMARY KEY, scene_id TEXT NOT NULL REFERENCES scene(id), code TEXT NOT NULL,
  tier TEXT NOT NULL CHECK(tier IN('A','B','C')), state TEXT NOT NULL DEFAULT 'draft', UNIQUE(scene_id,code));
CREATE TABLE shot_span(
  shot_id TEXT NOT NULL REFERENCES shot(id), span_id TEXT NOT NULL REFERENCES source_span(id), PRIMARY KEY(shot_id,span_id));
CREATE TABLE shot_spec(
  id TEXT PRIMARY KEY, shot_id TEXT NOT NULL REFERENCES shot(id), rev INT NOT NULL,
  purpose TEXT NOT NULL CHECK(length(purpose)>0), spec_json TEXT NOT NULL, camera_revision_id TEXT REFERENCES canon_revision(id),
  technique TEXT CHECK(technique IN('reuse','graphics','still','still_move','layered_2p5d','render_3d','plate_comp','generated','hybrid')),
  motion_requirement TEXT, technique_reason TEXT, fallback_json TEXT, content_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN('draft','locked','superseded')), UNIQUE(shot_id,rev),
  CHECK(technique NOT IN('generated','hybrid') OR (motion_requirement IS NOT NULL AND technique_reason IS NOT NULL)));
CREATE TABLE shot_canon(
  shot_spec_id TEXT NOT NULL REFERENCES shot_spec(id), canon_revision_id TEXT NOT NULL REFERENCES canon_revision(id),
  role TEXT NOT NULL, must_hold INT NOT NULL DEFAULT 1, PRIMARY KEY(shot_spec_id,canon_revision_id,role));

-- ============ 5. providers, money, paid API calls ============
-- this installation's link to a provider. secret_ref is the NAME of a Windows Credential Manager entry, never the secret.
CREATE TABLE connector(
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, adapter TEXT NOT NULL, adapter_version TEXT NOT NULL, secret_ref TEXT,
  secret_fingerprint TEXT, secret_hint TEXT, config_json TEXT NOT NULL DEFAULT '{}',
  generation_enabled INT NOT NULL DEFAULT 0 CHECK(generation_enabled IN(0,1)), enabled_by TEXT, enabled_at TEXT, retired_at TEXT,
  CHECK(secret_ref IS NULL OR secret_ref LIKE 'wincred:%'),
  CHECK(generation_enabled=0 OR (secret_ref IS NOT NULL AND enabled_by IS NOT NULL AND enabled_by='user')));
-- dated facts about a surface or API; an expired profile blocks work. connector_id is NULL for manual surfaces.
CREATE TABLE provider_profile(
  id TEXT PRIMARY KEY, surface TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN('image_api','audio_api','video_manual','audio_manual','inspect_api','inspect_manual')),
  capabilities_json TEXT NOT NULL, terms_json TEXT NOT NULL, source_url TEXT, verified_on TEXT NOT NULL, expires_on TEXT NOT NULL,
  connector_id TEXT REFERENCES connector(id), CHECK(kind NOT IN('image_api','audio_api','inspect_api') OR connector_id IS NOT NULL));
-- closed list on purpose: there is no video-generation capability to enable
CREATE TABLE connector_capability(
  connector_id TEXT NOT NULL REFERENCES connector(id), capability TEXT NOT NULL CHECK(capability IN
    ('tts','dialogue','speech_to_speech','stt','forced_alignment','voices_read','usage_read','image_generate','image_edit','inspect_video','inspect_audio','sfx','music')),
  policy TEXT NOT NULL DEFAULT 'blocked' CHECK(policy IN('allowed','blocked')), policy_reason TEXT NOT NULL, set_by TEXT NOT NULL CHECK(set_by='user'),
  PRIMARY KEY(connector_id,capability));
-- append-only connection history. A check that costs money needs the user.
CREATE TABLE connector_check(
  id INTEGER PRIMARY KEY AUTOINCREMENT, connector_id TEXT NOT NULL REFERENCES connector(id),
  kind TEXT NOT NULL CHECK(kind IN('auth','availability','capability','entitlement','usage')), capability TEXT,
  outcome TEXT NOT NULL CHECK(outcome IN('ok','denied','insufficient','unreachable','error')), billable INT NOT NULL DEFAULT 0, approved_by TEXT,
  detail_json TEXT NOT NULL, checked_at TEXT NOT NULL, CHECK(billable=0 OR (approved_by IS NOT NULL AND approved_by='user')));
CREATE TABLE usage_snapshot(
  id INTEGER PRIMARY KEY AUTOINCREMENT, connector_id TEXT NOT NULL REFERENCES connector(id), source TEXT NOT NULL CHECK(source IN('api','user_reported')),
  tier TEXT, unit TEXT NOT NULL, used INT NOT NULL, quota INT NOT NULL, resets_on TEXT, raw_json TEXT NOT NULL, taken_at TEXT NOT NULL);
-- each fact on the Connectors page is separate; none implies the next
CREATE VIEW connector_status AS SELECT c.id, c.provider, c.secret_ref IS NOT NULL AS credentials_configured,
  (SELECT outcome FROM connector_check k WHERE k.connector_id=c.id AND k.kind='auth' ORDER BY k.id DESC LIMIT 1) AS authentication,
  (SELECT outcome FROM connector_check k WHERE k.connector_id=c.id AND k.kind='availability' ORDER BY k.id DESC LIMIT 1) AS availability,
  c.generation_enabled AS integration_enabled,
  EXISTS(SELECT 1 FROM provider_profile p WHERE p.kind LIKE '%manual' AND p.surface LIKE c.provider||'%' AND p.expires_on>=date('now')) AS manual_available FROM connector c;
-- manual or api, per film and service, chosen by the user. No row = manual. Video can only be manual.
CREATE TABLE production_mode(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, film_id TEXT NOT NULL REFERENCES film(id), service TEXT NOT NULL CHECK(service IN('voice','sfx','stills','inspection','video')),
  mode TEXT NOT NULL CHECK(mode IN('manual','api')), set_by TEXT NOT NULL CHECK(set_by='user'), set_at TEXT NOT NULL, CHECK(service<>'video' OR mode='manual'));
CREATE VIEW mode_current AS SELECT m.* FROM production_mode m WHERE m.seq=(SELECT MAX(seq) FROM production_mode x WHERE x.film_id=m.film_id AND x.service=m.service);
-- what the user has released: micro-USD of cash, or credits already included in a subscription. Never mixed.
CREATE TABLE spend_envelope(
  id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id), purpose TEXT NOT NULL,
  limit_amount INT NOT NULL CHECK(limit_amount>0), max_per_request INT NOT NULL CHECK(max_per_request>0),
  opened_by TEXT NOT NULL CHECK(opened_by='user'), expires_on TEXT, closed_at TEXT,
  unit TEXT NOT NULL DEFAULT 'musd' CHECK(unit IN('musd','credits')));
-- every paid call is reserved before it is sent; 'unknown' = crash after send: reconcile, never resend
CREATE TABLE api_request(
  id TEXT PRIMARY KEY, envelope_id TEXT NOT NULL REFERENCES spend_envelope(id), profile_id TEXT NOT NULL REFERENCES provider_profile(id),
  shot_spec_id TEXT REFERENCES shot_spec(id), canon_revision_id TEXT REFERENCES canon_revision(id), purpose TEXT NOT NULL,
  prompt_rev INT NOT NULL, prompt TEXT NOT NULL, request_json TEXT NOT NULL, request_hash TEXT NOT NULL, changed_vs_prior_json TEXT,
  requested_by TEXT NOT NULL, est_amount INT NOT NULL CHECK(est_amount>0), actual_amount INT,
  state TEXT NOT NULL CHECK(state IN('reserved','sent','succeeded','refused','failed','unknown')),
  provider_job_id TEXT, created_at TEXT NOT NULL, idem_key TEXT NOT NULL UNIQUE,
  capability TEXT NOT NULL DEFAULT 'image_generate',
  cost_class TEXT NOT NULL DEFAULT 'first' CHECK(cost_class IN('first','retry','alternative','casting','verification','benchmark')),
  voice_package_id TEXT REFERENCES voice_package(id), sfx_package_id TEXT REFERENCES sfx_package(id), attempt_id TEXT REFERENCES attempt(id));
-- a cash ceiling per category across ALL providers and envelopes. It can never be raised or removed inside the application.
CREATE TABLE hard_cap(
  category TEXT PRIMARY KEY CHECK(category IN('image','inspection')), limit_musd INT NOT NULL CHECK(limit_musd>0), authority TEXT NOT NULL, created_at TEXT NOT NULL);
-- money that is not a request: subscription, top-up, tax. Entered or confirmed by the user from a real invoice.
CREATE TABLE ledger_charge(
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN('subscription','topup','tax','other')), amount_minor INT NOT NULL, currency TEXT NOT NULL,
  period TEXT, evidence_relpath TEXT, confirmed_by TEXT NOT NULL CHECK(confirmed_by='user'), charged_on TEXT NOT NULL);
CREATE TABLE api_request_ref(
  request_id TEXT NOT NULL REFERENCES api_request(id), ordinal INT NOT NULL, asset_id TEXT NOT NULL REFERENCES asset(id),
  role TEXT NOT NULL, PRIMARY KEY(request_id,ordinal));
CREATE TABLE api_result(
  request_id TEXT NOT NULL REFERENCES api_request(id), asset_id TEXT NOT NULL REFERENCES asset(id), PRIMARY KEY(request_id,asset_id));

-- ============ 6. manual video generation (by the user, in the Gemini app) ============
CREATE TABLE frame_pair(
  id TEXT PRIMARY KEY, shot_spec_id TEXT NOT NULL REFERENCES shot_spec(id), rev INT NOT NULL,
  first_asset_id TEXT NOT NULL REFERENCES asset(id), last_asset_id TEXT REFERENCES asset(id),
  last_waived_by TEXT, checks_json TEXT NOT NULL, UNIQUE(shot_spec_id,rev),
  CHECK(last_asset_id IS NOT NULL OR (last_waived_by IS NOT NULL AND last_waived_by='user')));
CREATE TABLE video_package(
  id TEXT PRIMARY KEY, frame_pair_id TEXT NOT NULL REFERENCES frame_pair(id), rev INT NOT NULL,
  profile_id TEXT NOT NULL REFERENCES provider_profile(id), prompt TEXT NOT NULL, settings_json TEXT NOT NULL,
  attachments_json TEXT NOT NULL, acceptance_json TEXT NOT NULL, content_hash TEXT NOT NULL, changed_vs_prior_json TEXT,
  UNIQUE(frame_pair_id,rev), CHECK(rev=1 OR changed_vs_prior_json IS NOT NULL));
-- a name the user types. No login, no quota, no rotation logic.
CREATE TABLE account_label(id TEXT PRIMARY KEY, label TEXT NOT NULL UNIQUE);
CREATE TABLE gen_attempt(
  id TEXT PRIMARY KEY, package_id TEXT NOT NULL REFERENCES video_package(id), attempt_no INT NOT NULL,
  account_label_id TEXT REFERENCES account_label(id), app_model TEXT, app_mode TEXT, usage_note TEXT,
  state TEXT NOT NULL CHECK(state IN('planned','generated','not_used','refused')),
  confirmed_by TEXT, result_asset_id TEXT REFERENCES asset(id),
  outcome TEXT CHECK(outcome IN('approved','recovered','rejected')), failure_class TEXT, performed_on TEXT, created_at TEXT NOT NULL,
  UNIQUE(package_id,attempt_no), CHECK(state='planned' OR (confirmed_by IS NOT NULL AND confirmed_by='user')));

-- ============ 7. agents, tasks, evidence, review ============
CREATE TABLE agent_version(
  hash TEXT PRIMARY KEY, role TEXT NOT NULL, git_path TEXT NOT NULL, model TEXT NOT NULL, effort TEXT NOT NULL,
  adopted_at TEXT, bench_run_id TEXT, retired_at TEXT);
CREATE TABLE task(
  id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id), kind TEXT NOT NULL CHECK(kind IN('make','review')),
  action TEXT NOT NULL, role TEXT NOT NULL, gate TEXT, target_type TEXT, target_id TEXT,
  packet_json TEXT NOT NULL, packet_hash TEXT NOT NULL, blind INT NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'ready' CHECK(state IN('blocked','ready','leased','reported','accepted','rejected','cancelled','expired')),
  lease_token TEXT, lease_until TEXT, attempt INT NOT NULL DEFAULT 0, max_attempts INT NOT NULL DEFAULT 2,
  agent_hash TEXT REFERENCES agent_version(hash), superseded_by TEXT REFERENCES task(id), UNIQUE(role,action,packet_hash,attempt));
CREATE TABLE task_dep(
  task_id TEXT NOT NULL REFERENCES task(id), needs_id TEXT NOT NULL REFERENCES task(id),
  PRIMARY KEY(task_id,needs_id), CHECK(task_id<>needs_id));
-- every view or measurement actually handed to an agent
CREATE TABLE evidence_serve(
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES task(id), asset_id TEXT NOT NULL REFERENCES asset(id),
  view TEXT NOT NULL, frame_from INT, frame_to INT, region_json TEXT, served_at TEXT NOT NULL);
-- modality_json: what was really examined (frames, slit-scan, metrics, full video, audio)
CREATE TABLE report(
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES task(id),
  verdict TEXT NOT NULL CHECK(verdict IN('APPROVED','REJECTED','NEEDS_CORRECTION','NEEDS_INSPECTION','UNCERTAIN','NOT_APPLICABLE')),
  body_json TEXT NOT NULL, schema_version TEXT NOT NULL, modality_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE criterion_result(
  report_id TEXT NOT NULL REFERENCES report(id), criterion_id TEXT NOT NULL, mandatory INT NOT NULL CHECK(mandatory IN(0,1)),
  result TEXT NOT NULL CHECK(result IN('met','not_met','cannot_determine','not_applicable')), PRIMARY KEY(report_id,criterion_id));
CREATE TABLE finding(
  id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES report(id), defect_class TEXT NOT NULL,
  severity TEXT NOT NULL CHECK(severity IN('critical','major','moderate','minor','info')),
  evidence_id TEXT REFERENCES evidence_serve(id), frame_from INT, frame_to INT, region_json TEXT, requirement_ref TEXT,
  statement TEXT NOT NULL, reason TEXT NOT NULL CHECK(length(reason)>0),
  next_action TEXT NOT NULL CHECK(next_action IN
    ('none','re_inspect','correct_image','recover_edit','recover_composite','redesign_shot','regenerate','ask_user')),
  human_verdict TEXT CHECK(human_verdict IN('true','false','unsure')),
  CHECK(severity='info' OR evidence_id IS NOT NULL));
-- deterministic measurement; never written by an agent role
CREATE TABLE check_result(
  id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES asset(id), check_name TEXT NOT NULL, check_version TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK(outcome IN('pass','fail','warn','error')), value_json TEXT NOT NULL,
  basis_hash TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(asset_id,check_name,check_version,basis_hash));
CREATE TABLE human_label(
  id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES asset(id), blind INT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN('watch','listen','identity','look')),
  verdict TEXT NOT NULL CHECK(verdict IN('good','fixable','bad','unsure')), tags_json TEXT, created_at TEXT NOT NULL);

-- ============ 8. approvals with dependency tracking ============
CREATE TABLE current_hash(
  dep_type TEXT NOT NULL, dep_id TEXT NOT NULL, field TEXT NOT NULL DEFAULT '*', hash TEXT NOT NULL, PRIMARY KEY(dep_type,dep_id,field));
-- only the deterministic gate engine or the user can approve; agents only report
CREATE TABLE approval(
  id TEXT PRIMARY KEY, gate TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK(outcome IN('approved','rejected','waived')), actor TEXT NOT NULL CHECK(actor IN('user','gate-engine')),
  evidence_json TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL,
  CHECK(outcome<>'waived' OR (actor='user' AND reason IS NOT NULL AND length(reason)>0)));
CREATE TABLE approval_dep(
  approval_id TEXT NOT NULL REFERENCES approval(id), dep_type TEXT NOT NULL, dep_id TEXT NOT NULL,
  field TEXT NOT NULL DEFAULT '*', hash TEXT NOT NULL, PRIMARY KEY(approval_id,dep_type,dep_id,field));
-- live = approved or waived, with recorded inputs, all of which still have the hash that was approved
CREATE VIEW approval_live AS
  SELECT a.* FROM approval a WHERE a.outcome IN('approved','waived')
  AND EXISTS(SELECT 1 FROM approval_dep d WHERE d.approval_id=a.id)
  AND NOT EXISTS(SELECT 1 FROM approval_dep d
    LEFT JOIN current_hash c ON c.dep_type=d.dep_type AND c.dep_id=d.dep_id AND c.field=d.field
    WHERE d.approval_id=a.id AND c.hash IS NOT d.hash);
CREATE TABLE decision(
  id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id), question TEXT NOT NULL, options_json TEXT NOT NULL,
  chosen TEXT, rationale TEXT, decided_by TEXT, evidence_json TEXT, created_at TEXT NOT NULL, supersedes TEXT REFERENCES decision(id));

-- ============ 9. recovery, timeline, audio, render, jobs ============
CREATE TABLE recovery_proposal(
  id TEXT PRIMARY KEY, source_asset_id TEXT NOT NULL REFERENCES asset(id), shot_id TEXT NOT NULL REFERENCES shot(id),
  rung TEXT NOT NULL CHECK(rung IN('R1_range','R2_edit','R3_repair','R4_composite','R5_hybrid','R6_app_edit')),
  ops_json TEXT NOT NULL, addresses_json TEXT NOT NULL, cannot_fix_json TEXT NOT NULL,
  output_asset_id TEXT REFERENCES asset(id), state TEXT NOT NULL CHECK(state IN('proposed','rendered','accepted','rejected')));
CREATE TABLE timeline(id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id), name TEXT NOT NULL, UNIQUE(film_id,name));
CREATE TABLE timeline_rev(
  id TEXT PRIMARY KEY, timeline_id TEXT NOT NULL REFERENCES timeline(id), rev INT NOT NULL, doc_json TEXT NOT NULL,
  content_hash TEXT NOT NULL, author TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN('draft','locked','superseded')), UNIQUE(timeline_id,rev));
CREATE TABLE script_line(
  id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id), span_id TEXT NOT NULL REFERENCES source_span(id),
  speaker_id TEXT REFERENCES canon_entity(id), text TEXT NOT NULL, direction_json TEXT);
-- a character's voice is canon (a canon_revision of kind 'voice'); provider IDs are replaceable bindings to it
CREATE TABLE voice_binding(
  id TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES canon_revision(id), provider TEXT NOT NULL, provider_voice_id TEXT NOT NULL,
  category TEXT NOT NULL CHECK(category IN('provider_permanent','library','designed','instant_clone','professional_clone','recorded_actor')),
  model_id TEXT, settings_json TEXT NOT NULL, available_until TEXT, notice_days INT,
  consent_asset_id TEXT REFERENCES asset(id), approved_sample_asset_id TEXT REFERENCES asset(id),
  status TEXT NOT NULL CHECK(status IN('candidate','approved','retired')), approved_by TEXT,
  CHECK(status<>'approved' OR (approved_by IS NOT NULL AND approved_by='user' AND approved_sample_asset_id IS NOT NULL)),
  CHECK(category NOT IN('instant_clone','professional_clone','recorded_actor') OR consent_asset_id IS NOT NULL));
CREATE TABLE pronunciation(
  id TEXT PRIMARY KEY, universe_id TEXT NOT NULL REFERENCES universe(id), film_id TEXT REFERENCES film(id), term TEXT NOT NULL,
  say_as TEXT NOT NULL, notation TEXT NOT NULL CHECK(notation IN('respell','ipa','alias')), approved_by TEXT);
-- one package, usable by hand or by API: same text, voice, settings and acceptance either way
CREATE TABLE voice_package(
  id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id), rev INT NOT NULL, series TEXT NOT NULL, doc_json TEXT NOT NULL, content_hash TEXT NOT NULL,
  manual_profile_id TEXT NOT NULL REFERENCES provider_profile(id), api_profile_id TEXT REFERENCES provider_profile(id),
  changed_vs_prior_json TEXT, UNIQUE(film_id,series,rev), CHECK(rev=1 OR changed_vs_prior_json IS NOT NULL));
CREATE TABLE voice_package_line(
  package_id TEXT NOT NULL REFERENCES voice_package(id), line_id TEXT NOT NULL REFERENCES script_line(id), ordinal INT NOT NULL, PRIMARY KEY(package_id,line_id));
CREATE TABLE audio_take(
  id TEXT PRIMARY KEY, line_id TEXT NOT NULL REFERENCES script_line(id), asset_id TEXT NOT NULL REFERENCES asset(id), take_no INT NOT NULL,
  voice_revision_id TEXT REFERENCES canon_revision(id), settings_json TEXT NOT NULL, credits_confirmed INT,
  word_timing_json TEXT, package_id TEXT REFERENCES voice_package(id), binding_id TEXT REFERENCES voice_binding(id),
  source TEXT NOT NULL CHECK(source IN('manual','api','recorded','temp')), request_id TEXT REFERENCES api_request(id), confirmed_by TEXT,
  format_json TEXT NOT NULL, UNIQUE(line_id,take_no), CHECK(source<>'api' OR request_id IS NOT NULL),
  CHECK(source NOT IN('manual','recorded') OR (confirmed_by IS NOT NULL AND confirmed_by='user')));
CREATE TABLE sound_cue(
  id TEXT PRIMARY KEY, scene_id TEXT NOT NULL REFERENCES scene(id),
  kind TEXT NOT NULL CHECK(kind IN('foley','hard_fx','ambience','room_tone','music','motif','designed_silence')),
  purpose TEXT NOT NULL CHECK(length(purpose)>0), spec_json TEXT NOT NULL, asset_id TEXT REFERENCES asset(id),
  sourcing TEXT CHECK(sourcing IN('reuse','reuse_edited','variant','generate','record','none')), search_json TEXT,
  CHECK(sourcing IS NULL OR sourcing NOT IN('generate','variant') OR search_json IS NOT NULL));
CREATE TABLE sfx_package(
  id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id), cue_id TEXT NOT NULL REFERENCES sound_cue(id), rev INT NOT NULL,
  doc_json TEXT NOT NULL, content_hash TEXT NOT NULL, manual_profile_id TEXT NOT NULL REFERENCES provider_profile(id),
  api_profile_id TEXT REFERENCES provider_profile(id), changed_vs_prior_json TEXT, UNIQUE(cue_id,rev), CHECK(rev=1 OR changed_vs_prior_json IS NOT NULL));
-- work that is waiting for the user's hands. Waiting is a normal state, not a failure, and has no timeout.
CREATE TABLE manual_task(
  id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id), kind TEXT NOT NULL CHECK(kind IN('voice','sfx','video')),
  package_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'waiting' CHECK(state IN('waiting','imported','closed')),
  opened_at TEXT NOT NULL, closed_at TEXT, note TEXT, UNIQUE(kind,package_id));
-- one thing the film needs from a generator; at most three attempts unless the user extends it
CREATE TABLE generation_target(
  id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id), kind TEXT NOT NULL CHECK(kind IN('image','voice','sfx','video','inspection')),
  ref_id TEXT NOT NULL, title TEXT NOT NULL, max_attempts INT NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 3),
  state TEXT NOT NULL DEFAULT 'open' CHECK(state IN('open','approved','stopped','cancelled')), UNIQUE(film_id,kind,ref_id));
CREATE TABLE attempt_extension(
  id TEXT PRIMARY KEY, target_id TEXT NOT NULL REFERENCES generation_target(id), extra INT NOT NULL CHECK(extra BETWEEN 1 AND 3),
  granted_by TEXT NOT NULL CHECK(granted_by='user'), reason TEXT NOT NULL CHECK(length(reason)>0), created_at TEXT NOT NULL);
CREATE TABLE attempt(
  id TEXT PRIMARY KEY, target_id TEXT NOT NULL REFERENCES generation_target(id), attempt_no INT NOT NULL, origin TEXT NOT NULL CHECK(origin IN('api','manual')),
  spec_json TEXT NOT NULL, spec_hash TEXT NOT NULL, reason TEXT, prior_attempt_id TEXT REFERENCES attempt(id), findings_json TEXT NOT NULL DEFAULT '[]',
  outcome TEXT NOT NULL DEFAULT 'pending' CHECK(outcome IN('pending','accepted','needs_correction','rejected','refused','no_result')),
  result_asset_id TEXT REFERENCES asset(id), created_at TEXT NOT NULL, UNIQUE(target_id,attempt_no));
-- versions of what fills a slot (a line, a cue, a shot, a reference). Nothing approved is ever deleted; restoring is a new row.
CREATE TABLE slot(id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id), kind TEXT NOT NULL, ref_id TEXT NOT NULL, title TEXT NOT NULL, UNIQUE(film_id,kind,ref_id));
CREATE TABLE slot_version(
  id TEXT PRIMARY KEY, slot_id TEXT NOT NULL REFERENCES slot(id), version_no INT NOT NULL, asset_id TEXT NOT NULL REFERENCES asset(id),
  status TEXT NOT NULL CHECK(status IN('candidate','proposed_replacement','approved','declined')), proposed_by TEXT NOT NULL, approved_by TEXT,
  reason TEXT NOT NULL CHECK(length(reason)>0), evidence_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, decided_at TEXT,
  UNIQUE(slot_id,version_no), CHECK(status<>'approved' OR (approved_by IS NOT NULL AND approved_by='user')));
CREATE VIEW slot_current AS SELECT v.* FROM slot_version v WHERE v.status='approved' AND v.version_no=(
  SELECT MAX(x.version_no) FROM slot_version x WHERE x.slot_id=v.slot_id AND x.status='approved');
-- a suggested change to an agent, skill or rule. Adopting needs a benchmark run and the user.
CREATE TABLE policy_proposal(
  id TEXT PRIMARY KEY, subject TEXT NOT NULL, proposal TEXT NOT NULL, evidence_json TEXT NOT NULL, proposed_by TEXT NOT NULL, created_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN('proposed','tested','adopted','declined')), bench_run_id TEXT REFERENCES bench_run(id), approved_by TEXT,
  CHECK(status<>'adopted' OR (bench_run_id IS NOT NULL AND approved_by IS NOT NULL AND approved_by='user')));
-- every placement of a sound or picture in a film; final use is refused unless every source allows it
CREATE TABLE asset_use(
  id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id), asset_id TEXT NOT NULL REFERENCES asset(id), timeline_rev_id TEXT REFERENCES timeline_rev(id),
  purpose TEXT NOT NULL CHECK(purpose IN('temp','final')), used_as TEXT NOT NULL);
-- readiness: derived from the chosen modes and the plan; nothing is required that the workflow does not use
CREATE TABLE preflight_run(id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id), stage TEXT NOT NULL, basis_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE preflight_item(
  run_id TEXT NOT NULL REFERENCES preflight_run(id), requirement TEXT NOT NULL, service TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN('manual','api','local')),
  connector_id TEXT REFERENCES connector(id), capability TEXT, state TEXT NOT NULL CHECK(state IN('ready','manual_ready','not_required','blocked','unknown')),
  evidence_json TEXT NOT NULL, next_step TEXT, PRIMARY KEY(run_id,requirement), CHECK(state NOT IN('blocked','unknown') OR next_step IS NOT NULL));
CREATE TABLE stage_pause(
  id TEXT PRIMARY KEY, film_id TEXT NOT NULL REFERENCES film(id), service TEXT NOT NULL, reason TEXT NOT NULL, error_json TEXT NOT NULL, options_json TEXT NOT NULL,
  opened_at TEXT NOT NULL, resolution TEXT CHECK(resolution IN('retry','manual','wait','cancel')), resolved_by TEXT, resolved_at TEXT,
  CHECK(resolution IS NULL OR (resolved_by IS NOT NULL AND resolved_by='user')));
CREATE TABLE render(
  id TEXT PRIMARY KEY, timeline_rev_id TEXT NOT NULL REFERENCES timeline_rev(id),
  kind TEXT NOT NULL CHECK(kind IN('preview','review','master','delivery')), engine_version TEXT NOT NULL, gpu_key TEXT NOT NULL,
  settings_json TEXT NOT NULL, asset_id TEXT REFERENCES asset(id), qa_json TEXT);
CREATE TABLE media_job(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, args_json TEXT NOT NULL, idem_key TEXT NOT NULL UNIQUE,
  resource TEXT NOT NULL CHECK(resource IN('cpu','gpu','io','net')), est_ram_mb INT NOT NULL, est_disk_mb INT NOT NULL,
  state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN('queued','running','done','failed','interrupted','cancelled')),
  worker_pid INT, heartbeat TEXT, progress_json TEXT, attempt INT NOT NULL DEFAULT 0, error TEXT);

-- ============ 10. learning, benchmarks, release, audit ============
CREATE TABLE lesson(
  id TEXT PRIMARY KEY, scope_json TEXT NOT NULL, statement TEXT NOT NULL, support_json TEXT NOT NULL,
  contradict_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL CHECK(status IN('candidate','trial','adopted','retired')),
  approved_by TEXT, CHECK(status<>'adopted' OR (approved_by IS NOT NULL AND approved_by='user')));
CREATE TABLE lesson_use(
  lesson_id TEXT NOT NULL REFERENCES lesson(id), target_type TEXT NOT NULL, target_id TEXT NOT NULL,
  arm TEXT NOT NULL CHECK(arm IN('with','held_out')), outcome TEXT, PRIMARY KEY(lesson_id,target_type,target_id));
CREATE VIRTUAL TABLE lesson_fts USING fts5(statement, lesson_id UNINDEXED);
-- the plan and pass marks are hashed before any result exists
CREATE TABLE bench_run(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, plan_json TEXT NOT NULL, plan_hash TEXT NOT NULL, subject TEXT NOT NULL,
  result_json TEXT, created_at TEXT NOT NULL);
CREATE TABLE policy_snapshot(
  id TEXT PRIMARY KEY, topic TEXT NOT NULL, url TEXT NOT NULL, fetched_on TEXT NOT NULL, expires_on TEXT NOT NULL,
  sha256 TEXT NOT NULL, summary TEXT NOT NULL);
CREATE TABLE release_item(
  film_id TEXT NOT NULL REFERENCES film(id), item TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN('open','pass','fail','waived')), evidence_json TEXT, decided_by TEXT,
  PRIMARY KEY(film_id,item), CHECK(state<>'waived' OR (decided_by IS NOT NULL AND decided_by='user')));
CREATE TABLE usage_log(
  id TEXT PRIMARY KEY, task_id TEXT REFERENCES task(id), model TEXT, effort TEXT, input_tokens INT, output_tokens INT,
  source TEXT NOT NULL CHECK(source IN('reported','unavailable')));
CREATE TABLE event(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL, verb TEXT NOT NULL,
  target_type TEXT NOT NULL, target_id TEXT NOT NULL, payload_json TEXT NOT NULL);

-- ============ triggers: immutability ============
CREATE TRIGGER asset_no_delete BEFORE DELETE ON asset BEGIN SELECT RAISE(ABORT,'assets are never deleted'); END;
CREATE TRIGGER asset_immutable BEFORE UPDATE OF sha256,relpath,bytes,origin,storage_class ON asset
  BEGIN SELECT RAISE(ABORT,'asset identity is immutable'); END;
CREATE TRIGGER event_no_update BEFORE UPDATE ON event BEGIN SELECT RAISE(ABORT,'event log is append-only'); END;
CREATE TRIGGER event_no_delete BEFORE DELETE ON event BEGIN SELECT RAISE(ABORT,'event log is append-only'); END;
CREATE TRIGGER approval_no_update BEFORE UPDATE ON approval BEGIN SELECT RAISE(ABORT,'approvals are append-only'); END;
CREATE TRIGGER approval_no_delete BEFORE DELETE ON approval BEGIN SELECT RAISE(ABORT,'approvals are append-only'); END;
CREATE TRIGGER approval_dep_no_update BEFORE UPDATE ON approval_dep BEGIN SELECT RAISE(ABORT,'approval inputs are append-only'); END;
CREATE TRIGGER approval_dep_no_delete BEFORE DELETE ON approval_dep BEGIN SELECT RAISE(ABORT,'approval inputs are append-only'); END;
CREATE TRIGGER canon_locked BEFORE UPDATE OF spec_json,content_hash ON canon_revision WHEN OLD.status='locked'
  BEGIN SELECT RAISE(ABORT,'locked canon: create a new revision'); END;
CREATE TRIGGER spec_locked BEFORE UPDATE OF spec_json,content_hash,technique,purpose ON shot_spec WHEN OLD.status='locked'
  BEGIN SELECT RAISE(ABORT,'locked shot spec: create a new revision'); END;
CREATE TRIGGER script_locked BEFORE UPDATE OF sha256,relpath ON source_doc WHEN OLD.locked_at IS NOT NULL
  BEGIN SELECT RAISE(ABORT,'locked source: import a new revision'); END;
CREATE TRIGGER span_immutable BEFORE UPDATE ON source_span BEGIN SELECT RAISE(ABORT,'script text is immutable: import a new revision'); END;
CREATE TRIGGER span_no_delete BEFORE DELETE ON source_span BEGIN SELECT RAISE(ABORT,'script text is immutable: import a new revision'); END;
CREATE TRIGGER timeline_locked BEFORE UPDATE OF doc_json,content_hash ON timeline_rev WHEN OLD.status='locked'
  BEGIN SELECT RAISE(ABORT,'locked timeline: create a new revision'); END;
CREATE TRIGGER bench_plan_fixed BEFORE UPDATE OF plan_json,plan_hash ON bench_run
  BEGIN SELECT RAISE(ABORT,'benchmark plan is fixed before results'); END;

-- ============ triggers: money ============
CREATE TRIGGER no_overspend BEFORE INSERT ON api_request WHEN
    (SELECT closed_at FROM spend_envelope WHERE id=NEW.envelope_id) IS NOT NULL
    OR COALESCE((SELECT expires_on FROM spend_envelope WHERE id=NEW.envelope_id),'9999') < date('now')
    OR NEW.est_amount > (SELECT max_per_request FROM spend_envelope WHERE id=NEW.envelope_id)
    OR (SELECT limit_amount FROM spend_envelope WHERE id=NEW.envelope_id) < NEW.est_amount + (SELECT COALESCE(SUM(
         CASE WHEN state IN('reserved','sent','unknown') THEN est_amount
              ELSE COALESCE(actual_amount, CASE state WHEN 'succeeded' THEN est_amount ELSE 0 END) END),0)
       FROM api_request WHERE envelope_id=NEW.envelope_id)
  BEGIN SELECT RAISE(ABORT,'spend envelope refused this request'); END;
CREATE TRIGGER api_starts_reserved BEFORE INSERT ON api_request WHEN NEW.state<>'reserved'
  BEGIN SELECT RAISE(ABORT,'a paid request must be reserved before it is sent'); END;
CREATE TRIGGER api_profile_fresh BEFORE INSERT ON api_request
  WHEN (SELECT expires_on FROM provider_profile WHERE id=NEW.profile_id) < date('now')
    OR (SELECT kind FROM provider_profile WHERE id=NEW.profile_id) NOT IN('image_api','audio_api','inspect_api')
  BEGIN SELECT RAISE(ABORT,'profile expired, or not an API the studio may call'); END;
CREATE TRIGGER api_state_machine BEFORE UPDATE OF state ON api_request WHEN NOT (
    OLD.state=NEW.state
    OR (OLD.state='reserved' AND NEW.state IN('sent','failed'))
    OR (OLD.state='sent' AND NEW.state IN('succeeded','refused','failed','unknown'))
    OR (OLD.state='unknown' AND NEW.state IN('succeeded','refused','failed')))
  BEGIN SELECT RAISE(ABORT,'illegal request state change'); END;
CREATE TRIGGER api_no_unchanged_retry BEFORE INSERT ON api_request
  WHEN NEW.purpose<>'bench_repeat' AND EXISTS(
    SELECT 1 FROM api_request p WHERE p.request_hash=NEW.request_hash AND p.state IN('succeeded','refused'))
  BEGIN SELECT RAISE(ABORT,'identical request already answered: change something or reuse the result'); END;

-- included credits must never spill into a paid balance: needs a snapshot read from the provider, and room in it
CREATE TRIGGER api_no_credit_spill BEFORE INSERT ON api_request WHEN (SELECT unit FROM spend_envelope WHERE id=NEW.envelope_id)='credits' AND COALESCE((
    SELECT s.quota - s.used FROM usage_snapshot s WHERE s.source='api' AND s.connector_id=(SELECT connector_id FROM provider_profile WHERE id=NEW.profile_id) ORDER BY s.id DESC LIMIT 1),-1)
    < NEW.est_amount + (SELECT COALESCE(SUM(CASE WHEN r.state IN('reserved','sent','unknown') THEN r.est_amount WHEN r.state='succeeded' THEN COALESCE(r.actual_amount,r.est_amount) ELSE 0 END),0)
       FROM api_request r JOIN spend_envelope e ON e.id=r.envelope_id JOIN provider_profile p ON p.id=r.profile_id
       WHERE e.unit='credits' AND p.connector_id=(SELECT connector_id FROM provider_profile WHERE id=NEW.profile_id)
         AND (r.state IN('reserved','sent','unknown') OR r.created_at > (SELECT s.taken_at FROM usage_snapshot s WHERE s.source='api' AND s.connector_id=p.connector_id ORDER BY s.id DESC LIMIT 1)))
  BEGIN SELECT RAISE(ABORT,'would exceed verified included credits: no spill into paid balance'); END;
CREATE TRIGGER api_connector_gate BEFORE INSERT ON api_request WHEN NOT EXISTS(
    SELECT 1 FROM provider_profile p JOIN connector c ON c.id=p.connector_id JOIN connector_capability k ON k.connector_id=c.id AND k.capability=NEW.capability
    WHERE p.id=NEW.profile_id AND c.generation_enabled=1 AND c.retired_at IS NULL AND k.policy='allowed')
  BEGIN SELECT RAISE(ABORT,'integration is off, or this capability is not allowed'); END;
CREATE TRIGGER api_mode_gate BEFORE INSERT ON api_request WHEN NOT EXISTS(
    SELECT 1 FROM mode_current m JOIN spend_envelope e ON e.film_id=m.film_id JOIN provider_profile p ON p.id=NEW.profile_id
    WHERE e.id=NEW.envelope_id AND m.mode='api' AND m.service=CASE WHEN NEW.capability='sfx' THEN 'sfx' WHEN p.kind='audio_api' THEN 'voice' WHEN p.kind='image_api' THEN 'stills' ELSE 'inspection' END)
  BEGIN SELECT RAISE(ABORT,'this film uses manual mode for that service'); END;
CREATE TRIGGER api_not_while_paused BEFORE INSERT ON api_request WHEN EXISTS(
    SELECT 1 FROM stage_pause s JOIN spend_envelope e ON e.film_id=s.film_id JOIN provider_profile p ON p.id=NEW.profile_id
    WHERE e.id=NEW.envelope_id AND s.resolution IS NULL AND s.service=CASE WHEN NEW.capability='sfx' THEN 'sfx' WHEN p.kind='audio_api' THEN 'voice' WHEN p.kind='image_api' THEN 'stills' ELSE 'inspection' END)
  BEGIN SELECT RAISE(ABORT,'stage is paused: the user chooses retry, manual, wait or cancel'); END;
CREATE TRIGGER api_no_provider_switch BEFORE INSERT ON api_request WHEN NEW.voice_package_id IS NOT NULL AND
    (SELECT api_profile_id FROM voice_package WHERE id=NEW.voice_package_id) IS NOT NEW.profile_id
  BEGIN SELECT RAISE(ABORT,'package is for a different provider: no silent switch'); END;
CREATE TRIGGER api_audio_needs_package BEFORE INSERT ON api_request WHEN NEW.capability IN('tts','dialogue','speech_to_speech') AND NOT EXISTS(
    SELECT 1 FROM approval_live WHERE gate='G9_voice_package' AND target_id=NEW.voice_package_id)
  BEGIN SELECT RAISE(ABORT,'voice generation needs a package with a live approval'); END;
CREATE TRIGGER api_sfx_needs_package BEFORE INSERT ON api_request WHEN NEW.capability='sfx' AND NOT EXISTS(
    SELECT 1 FROM approval_live WHERE gate='G9_sfx_package' AND target_id=NEW.sfx_package_id AND actor='user')
  BEGIN SELECT RAISE(ABORT,'sound-effect generation needs a package approved by the user'); END;
CREATE TRIGGER api_sfx_provider BEFORE INSERT ON api_request WHEN NEW.sfx_package_id IS NOT NULL AND
    (SELECT api_profile_id FROM sfx_package WHERE id=NEW.sfx_package_id) IS NOT NEW.profile_id
  BEGIN SELECT RAISE(ABORT,'package is for a different provider: no silent switch'); END;
-- cash: there must be a cap for the category, and room under it across every provider
CREATE TRIGGER hard_cap_holds BEFORE INSERT ON api_request WHEN (SELECT unit FROM spend_envelope WHERE id=NEW.envelope_id)='musd' AND COALESCE((
    SELECT h.limit_musd FROM hard_cap h WHERE h.category=CASE WHEN NEW.capability IN('image_generate','image_edit') THEN 'image' WHEN NEW.capability LIKE 'inspect%' THEN 'inspection' ELSE '-' END),0)
    < NEW.est_amount + (SELECT COALESCE(SUM(CASE WHEN r.state IN('reserved','sent','unknown') THEN MAX(r.est_amount,COALESCE(r.actual_amount,0))
         ELSE COALESCE(r.actual_amount, CASE r.state WHEN 'succeeded' THEN r.est_amount ELSE 0 END) END),0)
       FROM api_request r JOIN spend_envelope e ON e.id=r.envelope_id WHERE e.unit='musd'
         AND (r.capability IN('image_generate','image_edit'))=(NEW.capability IN('image_generate','image_edit')))
  BEGIN SELECT RAISE(ABORT,'hard cap for this category would be exceeded'); END;
CREATE TRIGGER hard_cap_no_update BEFORE UPDATE ON hard_cap BEGIN SELECT RAISE(ABORT,'a hard cap cannot be changed here'); END;
CREATE TRIGGER hard_cap_no_delete BEFORE DELETE ON hard_cap BEGIN SELECT RAISE(ABORT,'a hard cap cannot be removed here'); END;
-- attempts: bounded, reasoned, never identical
CREATE TRIGGER attempt_rules BEFORE INSERT ON attempt WHEN
    (SELECT state FROM generation_target WHERE id=NEW.target_id)<>'open'
    OR NEW.attempt_no<>1+(SELECT COUNT(*) FROM attempt WHERE target_id=NEW.target_id)
    OR NEW.attempt_no>(SELECT max_attempts FROM generation_target WHERE id=NEW.target_id)+(SELECT COALESCE(SUM(extra),0) FROM attempt_extension WHERE target_id=NEW.target_id)
  BEGIN SELECT RAISE(ABORT,'attempt limit reached or target closed: stop and report'); END;
CREATE TRIGGER attempt_retry_reasoned BEFORE INSERT ON attempt WHEN NEW.attempt_no>1 AND (
    NEW.reason IS NULL OR length(NEW.reason)<10 OR json_array_length(NEW.findings_json)<1
    OR NEW.prior_attempt_id IS NOT (SELECT id FROM attempt WHERE target_id=NEW.target_id AND attempt_no=NEW.attempt_no-1)
    OR (SELECT outcome FROM attempt WHERE id=NEW.prior_attempt_id) NOT IN('needs_correction','rejected','refused'))
  BEGIN SELECT RAISE(ABORT,'a retry needs a reason, a finding and a judged earlier attempt'); END;
CREATE TRIGGER attempt_not_identical BEFORE INSERT ON attempt WHEN EXISTS(SELECT 1 FROM attempt a WHERE a.target_id=NEW.target_id AND a.spec_hash=NEW.spec_hash)
  BEGIN SELECT RAISE(ABORT,'identical specification was already tried'); END;
CREATE TRIGGER attempt_frozen BEFORE UPDATE OF target_id,attempt_no,spec_json,spec_hash,reason,findings_json ON attempt BEGIN SELECT RAISE(ABORT,'attempt history is fixed'); END;
CREATE TRIGGER attempt_no_delete BEFORE DELETE ON attempt BEGIN SELECT RAISE(ABORT,'attempt history is fixed'); END;
CREATE TRIGGER target_limit_fixed BEFORE UPDATE OF max_attempts ON generation_target BEGIN SELECT RAISE(ABORT,'use an attempt extension granted by the user'); END;
-- one delivered answer per attempt; at most two transport retries for replies that never came
CREATE TRIGGER api_needs_attempt BEFORE INSERT ON api_request WHEN
    NEW.attempt_id IS NULL OR (SELECT outcome FROM attempt WHERE id=NEW.attempt_id)<>'pending' OR (SELECT origin FROM attempt WHERE id=NEW.attempt_id)<>'api'
    OR EXISTS(SELECT 1 FROM api_request r WHERE r.attempt_id=NEW.attempt_id AND r.state<>'failed')
    OR (SELECT COUNT(*) FROM api_request r WHERE r.attempt_id=NEW.attempt_id AND r.state='failed')>2
  BEGIN SELECT RAISE(ABORT,'request needs an open attempt with no answer yet'); END;
CREATE TRIGGER slot_version_no_delete BEFORE DELETE ON slot_version BEGIN SELECT RAISE(ABORT,'version history is fixed'); END;
CREATE TRIGGER slot_version_decided BEFORE UPDATE ON slot_version WHEN OLD.status IN('approved','declined') OR NEW.asset_id<>OLD.asset_id OR NEW.version_no<>OLD.version_no
  BEGIN SELECT RAISE(ABORT,'a decided version is fixed: propose a new one'); END;
-- revalidated just before money moves: newest auth and capability checks must be ok and at most 15 minutes old
CREATE TRIGGER api_recheck_before_send BEFORE UPDATE OF state ON api_request WHEN OLD.state='reserved' AND NEW.state='sent' AND (
    COALESCE((SELECT outcome='ok' AND checked_at>=datetime('now','-15 minutes') FROM connector_check WHERE kind='auth'
      AND connector_id=(SELECT connector_id FROM provider_profile WHERE id=NEW.profile_id) ORDER BY id DESC LIMIT 1),0)=0
    OR COALESCE((SELECT outcome='ok' AND checked_at>=datetime('now','-15 minutes') FROM connector_check WHERE kind='capability' AND capability=NEW.capability
      AND connector_id=(SELECT connector_id FROM provider_profile WHERE id=NEW.profile_id) ORDER BY id DESC LIMIT 1),0)=0)
  BEGIN SELECT RAISE(ABORT,'connection or capability not freshly verified'); END;
CREATE TRIGGER connector_check_no_update BEFORE UPDATE ON connector_check BEGIN SELECT RAISE(ABORT,'connection history is append-only'); END;
CREATE TRIGGER connector_check_no_delete BEFORE DELETE ON connector_check BEGIN SELECT RAISE(ABORT,'connection history is append-only'); END;
CREATE TRIGGER mode_no_update BEFORE UPDATE ON production_mode BEGIN SELECT RAISE(ABORT,'mode history is append-only'); END;

-- ============ triggers: rights ============
-- beyond one film: every source must allow store, edit, sync, commercial, YouTube and reuse, without a running subscription, on verified terms
CREATE TRIGGER library_admission BEFORE INSERT ON library_item WHEN NEW.scope<>'film' AND EXISTS(
    SELECT 1 FROM root_rights r WHERE r.asset_id=NEW.asset_id AND (r.source IS NULL OR r.verified_by IS NOT 'user' OR r.subscription_bound<>0
      OR r.r_store<>'yes' OR r.r_edit<>'yes' OR r.r_sync<>'yes' OR r.r_commercial<>'yes' OR r.r_youtube<>'yes' OR r.r_reuse_films<>'yes'))
  BEGIN SELECT RAISE(ABORT,'rights do not allow the shared library'); END;
CREATE TRIGGER library_scope_no_widen BEFORE UPDATE OF scope,scope_ref,asset_id ON library_item
  BEGIN SELECT RAISE(ABORT,'retire the item and admit it again under the new scope'); END;
CREATE TRIGGER final_use_rights BEFORE INSERT ON asset_use WHEN NEW.purpose='final' AND EXISTS(
    SELECT 1 FROM root_rights r WHERE r.asset_id=NEW.asset_id AND (r.source IS NULL OR r.verified_by IS NOT 'user'
      OR r.r_sync<>'yes' OR r.r_commercial<>'yes' OR r.r_youtube<>'yes'
      OR (r.root_id<>NEW.asset_id AND r.r_edit<>'yes')))
  BEGIN SELECT RAISE(ABORT,'a source of this asset is not cleared for this film'); END;
CREATE TRIGGER no_cross_film_reuse BEFORE INSERT ON asset_use WHEN EXISTS(
    SELECT 1 FROM root_rights r WHERE r.asset_id=NEW.asset_id AND COALESCE(r.r_reuse_films,'unclear')<>'yes' AND EXISTS(
      SELECT 1 FROM asset_use u JOIN asset_root ur ON ur.asset_id=u.asset_id WHERE ur.root_id=r.root_id AND u.film_id<>NEW.film_id))
  BEGIN SELECT RAISE(ABORT,'a source of this asset may not be reused in another film'); END;
CREATE TRIGGER terms_immutable BEFORE UPDATE OF source,version,r_store,r_edit,r_sync,r_commercial,r_youtube,r_reuse_films,subscription_bound ON licence_terms WHEN OLD.verified_by IS NOT NULL
  BEGIN SELECT RAISE(ABORT,'verified terms are fixed: record a new version'); END;
CREATE TRIGGER voice_lasts BEFORE INSERT ON voice_binding WHEN NEW.status='approved' AND NEW.available_until IS NOT NULL AND NEW.available_until < date('now','+180 days')
  BEGIN SELECT RAISE(ABORT,'voice will disappear too soon to cast'); END;

-- ============ triggers: workflow and review ============
CREATE TRIGGER video_profile_manual BEFORE INSERT ON video_package
  WHEN (SELECT kind FROM provider_profile WHERE id=NEW.profile_id)<>'video_manual'
    OR (SELECT expires_on FROM provider_profile WHERE id=NEW.profile_id) < date('now')
  BEGIN SELECT RAISE(ABORT,'video packages are for a current manual surface only'); END;
CREATE TRIGGER package_needs_live_frames BEFORE INSERT ON video_package
  WHEN NOT EXISTS(SELECT 1 FROM approval_live WHERE gate='G4_frame_pair' AND target_id=NEW.frame_pair_id)
  BEGIN SELECT RAISE(ABORT,'frame pair has no live approval'); END;
CREATE TRIGGER attempt_needs_live_package BEFORE INSERT ON gen_attempt
  WHEN NOT EXISTS(SELECT 1 FROM approval_live WHERE gate='G5_package' AND target_id=NEW.package_id)
  BEGIN SELECT RAISE(ABORT,'package has no live approval'); END;
CREATE TRIGGER finding_evidence_owned BEFORE INSERT ON finding WHEN NEW.evidence_id IS NOT NULL AND
    (SELECT task_id FROM evidence_serve WHERE id=NEW.evidence_id) IS NOT (SELECT task_id FROM report WHERE id=NEW.report_id)
  BEGIN SELECT RAISE(ABORT,'finding cites evidence never served to this task'); END;
-- the role that made the candidate may not review it
CREATE TRIGGER no_self_review BEFORE INSERT ON task WHEN NEW.kind='review' AND EXISTS(
    SELECT 1 FROM asset_binding b WHERE b.target_type=NEW.target_type AND b.target_id=NEW.target_id AND b.role='candidate' AND b.bound_by=NEW.role)
  BEGIN SELECT RAISE(ABORT,'the maker may not review its own work'); END;
-- any mandatory criterion that is not met, or not determined, blocks approval; nothing averages it away
CREATE TRIGGER mandatory_blocks BEFORE INSERT ON approval WHEN NEW.outcome='approved' AND EXISTS(
    SELECT 1 FROM criterion_result cr JOIN report r ON r.id=cr.report_id JOIN task t ON t.id=r.task_id
    WHERE cr.mandatory=1 AND cr.result IN('not_met','cannot_determine')
      AND t.gate=NEW.gate AND t.target_id=NEW.target_id AND t.superseded_by IS NULL AND t.state IN('reported','accepted'))
  BEGIN SELECT RAISE(ABORT,'a mandatory criterion is not met or not determined'); END;
