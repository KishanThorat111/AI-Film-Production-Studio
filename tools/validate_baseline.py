"""Proves that the v2.1 governance rules hold in the database itself. Run: python validate_specs.py
Standard library only; jsonschema and yaml are used if installed."""
import json
import sqlite3
from pathlib import Path

LIVE = Path(__file__).resolve().parents[1] / "studio" / "schema"      # the files the program really uses
c = sqlite3.connect(":memory:", isolation_level=None)
# the approved baseline; rules added by later steps are tested in tests/test_rules.py
c.executescript((LIVE / "0001_baseline.sql").read_text(encoding="utf-8"))
c.execute("pragma foreign_keys=on")
x = c.execute

SETUP = """
insert into universe values('u','U');
insert into film(id,universe_id,title,fps_num,width,height,status) values('f','u','T',24,1920,1080,'development');
insert into source_doc values('d','f','script',1,'h','s.docx',1,'t','user');
insert into source_span values('sp1','d',1,'action','x','h');
insert into scene values('s','f',1,'x',null);
insert into shot values('sh','s','01-01','A','draft');
insert into shot values('sh2','s','01-02','A','draft');
insert into shot_spec values('ss','sh',1,'p','{}',null,'generated','turns','cheaper rungs fail','{}','h','locked');
insert into shot_spec values('ss2','sh2',1,'p','{}',null,'still',null,null,'{}','h','draft');
insert into canon_entity values('ce','u','character','A'),('env','u','environment','E'),('cam','u','camera','C');
insert into canon_revision values('cr1','ce',1,'{}','hash1',null,null,'locked','user'),('camr','cam',1,'{}','h',null,null,'proposed',null),('camr2','cam',2,'{}','h',null,null,'proposed',null);
insert into asset(id,sha256,kind,origin,storage_class,relpath,bytes,created_at) values('a1','aa','image','api_image','original','a',1,'t'),('a2','bb','image','api_image','original','b',1,'t'),('v1','cc','video','manual_video','original','c',1,'t');
insert into connector(id,provider,adapter,adapter_version,secret_ref,generation_enabled,enabled_by,enabled_at,retired_at) values('cimg','imgco','a','1','wincred:img',1,'user','t',null),('cvo','voiceco','a','1','wincred:vo',0,null,null,null),('cnone','otherco','a','1',null,0,null,null,null);
insert into hard_cap values('image',120000,'test','t'),('inspection',50000,'test','t');
insert into connector_capability values('cimg','image_generate','allowed','x','user'),('cvo','tts','allowed','x','user'),('cvo','music','blocked','film excluded by terms','user'),('cvo','sfx','blocked','library rights unclear','user');
insert into provider_profile values('img','img api','image_api','{}','{}',null,'2026-01-01','2099-01-01','cimg'),('old','old api','image_api','{}','{}',null,'2020-01-01','2020-02-01','cimg'),('gem','gemini app','video_manual','{}','{}',null,'2026-01-01','2099-01-01',null),('voapi','voiceco api','audio_api','{}','{}',null,'2026-01-01','2099-01-01','cvo'),('voman','voiceco website','audio_manual','{}','{}',null,'2026-01-01','2099-01-01',null),('vo2','otherco api','audio_api','{}','{}',null,'2026-01-01','2099-01-01','cnone');
insert into connector_check(connector_id,kind,capability,outcome,detail_json,checked_at) values('cimg','auth',null,'ok','{}',datetime('now')),('cimg','capability','image_generate','ok','{}',datetime('now'));
insert into production_mode(film_id,service,mode,set_by,set_at) values('f','stills','api','user','t');
insert into spend_envelope values('env1','f','bench',100000,60000,'user',null,null,'musd'),('env2','f','closed',100000,60000,'user',null,'2026-01-01','musd'),('envc','f','voice, included credits',20000,5000,'user',null,null,'credits');
insert into film(id,universe_id,title,fps_num,width,height,status) values('f2','u','T2',24,1920,1080,'development');
insert into script_line values('ln1','f','sp1',null,'line',null);
insert into voice_package values('vpk','f',1,'S1','{}','h','voman','voapi',null);
insert into current_hash values('voice_package','vpk','*','V1');
insert into approval values('apV','G9_voice_package','voice_package','vpk','approved','user','{}',null,'t');
insert into approval_dep values('apV','voice_package','vpk','*','V1');
insert into voice_package values('vpk2','f',1,'S2','{}','h','voman','voapi',null);
insert into canon_entity values('vx','u','voice','V');
insert into canon_revision values('vr','vx',1,'{}','h',null,null,'proposed',null);
insert into licence_terms(id,source,evidence_relpath,checked_on,r_store,r_edit,r_sync,r_commercial,r_youtube,r_reuse_films,subscription_bound,attribution_required,content_id_risk,verified_by) values('Lfree','open','e','t','yes','yes','yes','yes','yes','yes',0,0,'low','user'),('Lproj','per film only','e','t','unclear','yes','yes','yes','yes','unclear',0,0,'low','user'),('Lnofilm','no film','e','t','yes','yes','no','yes','yes','no',0,0,'low','user'),('Lsub','subscription','e','t','yes','yes','yes','yes','yes','yes',1,0,'low','user'),('Lunv','unverified','e','t','yes','yes','yes','yes','yes','yes',0,0,'low',null);
insert into asset(id,sha256,kind,origin,storage_class,relpath,bytes,created_at) values('s_free','s1','audio','licensed','original','s1',1,'t'),('s_proj','s2','audio','manual_audio','original','s2',1,'t'),('s_nofilm','s3','audio','manual_audio','original','s3',1,'t'),('s_sub','s4','audio','licensed','original','s4',1,'t'),('s_unv','s5','audio','licensed','original','s5',1,'t'),('s_none','s6','audio','recorded','original','s6',1,'t'),('d_free','s7','audio','derived','derived','s7',1,'t'),('d_mix','s8','audio','derived','derived','s8',1,'t'),('take','s9','audio','manual_audio','original','s9',1,'t'),('consent','s10','document','authored','original','s10',1,'t');
insert into asset_licence values('s_free','Lfree','t',null,null,null),('s_proj','Lproj','t',null,null,null),('s_nofilm','Lnofilm','t',null,null,null),('s_sub','Lsub','t',null,null,null),('s_unv','Lunv','t',null,null,null);
insert into asset_edge values('d_free','s_free','trim','{}','1'),('d_mix','s_free','layer','{}','1'),('d_mix','s_proj','layer','{}','1');
insert into frame_pair values('fp','ss',1,'a1','a2',null,'{}');
insert into asset_binding values('a1','frame_pair','fp','candidate','image-maker');
insert into current_hash values('canon','ce','face','F1'),('canon','ce','shoes','S1'),('asset','a1','*','aa'),('asset','a2','*','bb'),('frame_pair','fp','*','P1');
insert into approval values('apA','G4_frame_pair','frame_pair','fp','approved','gate-engine','{}',null,'t');
insert into approval_dep values('apA','canon','ce','face','F1'),('apA','asset','a1','*','aa'),('apA','asset','a2','*','bb');
insert into approval values('apB','G3_keyframe','shot_spec','ss2','approved','user','{}',null,'t');
insert into approval_dep values('apB','canon','ce','shoes','S1');
insert into video_package values('vp','fp',1,'gem','prompt','{}','[]','[]','h',null);
insert into approval values('apC','G5_package','video_package','vp','approved','user','{}',null,'t');
insert into approval_dep values('apC','frame_pair','fp','*','P1');
insert into task(id,film_id,kind,action,role,gate,target_type,target_id,packet_json,packet_hash,state) values('k1','f','review','inspect','forensic-inspector','G7_clip','asset','v1','{}','1','accepted'),('k2','f','review','inspect','continuity-inspector','G7_clip','asset','v1','{}','2','accepted');
insert into report values('r1','k1','REJECTED','{}','2','["frames"]','t'),('r2','k2','APPROVED','{}','2','["frames"]','t');
insert into evidence_serve values('e1','k1','v1','slit',0,239,null,'t');
insert into criterion_result values('r1','C1',1,'not_met'),('r2','C2',1,'met');
insert into bench_run values('b','image','{}','h','x',null,'t');
insert into event(at,actor,verb,target_type,target_id,payload_json) values('t','u','v','t','1','{}');
"""
c.executescript(SETUP)

FIND = "insert into finding(id,report_id,defect_class,severity,evidence_id,statement,reason,next_action) values"
API = ("insert into api_request(id,envelope_id,profile_id,purpose,prompt_rev,prompt,request_json,request_hash,"
       "requested_by,est_amount,state,created_at,idem_key) values")
VO = ("insert into api_request(id,envelope_id,profile_id,purpose,prompt_rev,prompt,request_json,request_hash,"
      "requested_by,est_amount,state,created_at,idem_key,capability,cost_class,voice_package_id) values")
LIB = "insert into library_item values"
USE = "insert into asset_use(id,film_id,asset_id,purpose,used_as) values"
VB = "insert into voice_binding(id,revision_id,provider,provider_voice_id,category,settings_json,available_until,consent_asset_id,approved_sample_asset_id,status,approved_by) values"
TAKE = "insert into audio_take(id,line_id,asset_id,take_no,settings_json,source,request_id,confirmed_by,format_json) values"
ATT = "insert into attempt(id,target_id,attempt_no,origin,spec_json,spec_hash,reason,prior_attempt_id,findings_json,created_at) values"
CUE = "insert into sound_cue(id,scene_id,kind,purpose,spec_json,sourcing,search_json) values"
APP = "insert into approval values"
GEN = "insert into gen_attempt(id,package_id,attempt_no,state,confirmed_by,created_at) values"
TASK = "insert into task(id,film_id,kind,action,role,gate,target_type,target_id,packet_json,packet_hash) values"
CASES = [
    # evidence
    ("allow", "finding with evidence served to its own task", FIND + "('1','r1','vanish','critical','e1','x','why','recover_edit')"),
    ("block", "finding citing evidence served to another task", FIND + "('2','r2','v','critical','e1','x','why','regenerate')"),
    ("block", "defect with no evidence", FIND + "('3','r1','v','major',null,'x','why','regenerate')"),
    ("block", "finding with no reason", FIND + "('4','r1','v','major','e1','x','','regenerate')"),
    ("block", "finding with an unknown next action", FIND + "('5','r1','v','major','e1','x','why','whatever')"),
    ("block", "report with a status outside the six", "insert into report values('r9','k1','PASS','{}','2','[]','t')"),
    # immutability
    ("block", "delete an asset", "delete from asset"),
    ("block", "change an asset hash", "update asset set sha256='z'"),
    ("block", "import the same original twice", "insert into asset(id,sha256,kind,origin,storage_class,relpath,bytes,created_at) values('dup','cc','video','manual_video','original','z',1,'t')"),
    ("block", "edit script text", "update source_span set text='changed'"),
    ("block", "edit a locked shot spec", "update shot_spec set spec_json='{1}' where id='ss'"),
    ("block", "edit locked canon", "update canon_revision set spec_json='{1}' where id='cr1'"),
    ("block", "rewrite history", "update event set actor='x'"),
    ("block", "change a benchmark plan after it is recorded", "update bench_run set plan_json='{2}'"),
    ("allow", "record benchmark results", "update bench_run set result_json='{}'"),
    # user authority
    ("block", "agent locks canon", "insert into canon_revision values('c2','ce',2,'{}','h',null,null,'locked','production-designer')"),
    ("block", "lock canon with no locker", "insert into canon_revision values('c3','ce',3,'{}','h',null,null,'locked',null)"),
    ("block", "agent approves anything", APP + "('p1','G3','shot','sh','approved','showrunner','{}','fine','t')"),
    ("block", "gate engine waives a gate", APP + "('p2','G3','shot','sh','waived','gate-engine','{}','ok','t')"),
    ("block", "user waives without a reason", APP + "('p3','G3','shot','sh','waived','user','{}',null,'t')"),
    ("allow", "user waives with a reason", APP + "('p4','G3','shot','sh','waived','user','{}','accepted look','t')"),
    ("block", "agent activates a story rule", "insert into invariant values('i','f','critical','lint','order_before','{}','sp1','active','story-editor')"),
    ("block", "agent approves a canon reference image", "insert into reference_item(id,revision_id,asset_id,generation_depth,status,approved_by) values('ri','cr1','a1',0,'approved','image-maker')"),
    ("block", "reference made from a reference of a reference", "insert into reference_item(id,revision_id,asset_id,generation_depth,status) values('ri2','cr1','a1',2,'candidate')"),
    ("block", "agent adopts its own lesson", "insert into lesson values('l','{}','x','[]','[]','adopted','showrunner')"),
    # review independence
    ("block", "maker reviews its own frame pair", TASK + "('t1','f','review','inspect','image-maker','G4_frame_pair','frame_pair','fp','{}','9')"),
    ("allow", "another role reviews that frame pair", TASK + "('t2','f','review','inspect','forensic-inspector','G4_frame_pair','frame_pair','fp','{}','9')"),
    ("block", "approve clip while one mandatory criterion fails (other reviewer approves)", APP + "('p5','G7_clip','asset','v1','approved','gate-engine','{}',null,'t')"),
    ("block", "even the user cannot 'approve' over it (must waive with reason)", APP + "('p6','G7_clip','asset','v1','approved','user','{}',null,'t')"),
    ("allow", "user waives it with a reason", APP + "('p7','G7_clip','asset','v1','waived','user','{}','defect hidden by cut','t')"),
    # cameras
    ("block", "in-story physical camera without a fixed pose", "insert into camera values('camr','diegetic','env',null,'{}',null)"),
    ("allow", "in-story physical camera with pose, lens and place", "insert into camera values('camr','diegetic','env','{}','{}',null)"),
    ("allow", "cinematic viewpoint with no fixed pose", "insert into camera values('camr2','cinematic',null,null,null,null)"),
    # shot logic
    ("block", "generated shot with no motion requirement", "insert into shot_spec values('ssx','sh2',2,'p','{}',null,'generated',null,null,'{}','h','draft')"),
    ("block", "shot with no purpose", "insert into shot_spec values('ssy','sh2',3,'','{}',null,'still',null,null,'{}','h','draft')"),
    # money
    ("allow", "paid image request inside the envelope", API + "('q1','env1','img','explore',1,'p','{}','H1','image-maker',50000,'reserved','t','k1')"),
    ("block", "request above the per-request cap", API + "('q2','env1','img','explore',1,'p','{}','H2','image-maker',60001,'reserved','t','k2')"),
    ("block", "request that would exceed the envelope", API + "('q3','env1','img','explore',1,'p','{}','H3','image-maker',50001,'reserved','t','k3')"),
    ("block", "request on a closed envelope", API + "('q4','env2','img','explore',1,'p','{}','H4','image-maker',1000,'reserved','t','k4')"),
    ("block", "request on an expired provider profile", API + "('q5','env1','old','explore',1,'p','{}','H5','image-maker',1000,'reserved','t','k5')"),
    ("block", "API request to a manual video surface", API + "('q6','env1','gem','video',1,'p','{}','H6','motion-engineer',1000,'reserved','t','k6')"),
    ("block", "request sent without a reservation", API + "('q7','env1','img','explore',1,'p','{}','H7','image-maker',1000,'sent','t','k7')"),
    ("block", "same idempotency key twice", API + "('q8','env1','img','explore',1,'p','{}','H8','image-maker',1000,'reserved','t','k1')"),
    ("allow", "mark request sent", "update api_request set state='sent' where id='q1'"),
    ("allow", "crash after sending -> unknown", "update api_request set state='unknown' where id='q1'"),
    ("block", "resend an unknown request", "update api_request set state='sent' where id='q1'"),
    ("block", "unknown request still holds its money", API + "('q9','env1','img','explore',1,'p','{}','H9','image-maker',50001,'reserved','t','k9')"),
    ("allow", "reconcile unknown -> succeeded, cheaper than estimated", "update api_request set state='succeeded',actual_amount=40000 where id='q1'"),
    ("allow", "the difference is released", API + "('q10','env1','img','explore',1,'p','{}','H10','image-maker',55000,'reserved','t','k10')"),
    ("block", "identical request already answered", API + "('q11','env1','img','explore',1,'p','{}','H1','image-maker',1000,'reserved','t','k11')"),
    ("block", "agent opens a spend envelope", "insert into spend_envelope values('env3','f','x',1,1,'showrunner',null,null,'musd')"),
    # manual video
    ("allow", "attempt on an approved package", GEN + "('g1','vp',1,'planned',null,'t')"),
    ("block", "agent marks a generation as done", GEN + "('g2','vp',2,'generated','showrunner','t')"),
    ("block", "generated with no confirmer", GEN + "('g3','vp',3,'generated',null,'t')"),
    ("allow", "user confirms a generation", GEN + "('g4','vp',4,'generated','user','t')"),
    ("block", "new package revision with nothing changed", "insert into video_package values('vp2','fp',2,'gem','prompt','{}','[]','[]','h',null)"),
    ("block", "video package aimed at an API profile", "insert into video_package values('vp3','fp',3,'img','prompt','{}','[]','[]','h','{}')"),
    ("block", "frame pair with no last frame and no user waiver", "insert into frame_pair values('fp2','ss',2,'a1',null,null,'{}')"),
    ("allow", "frame pair with last frame waived by user", "insert into frame_pair values('fp3','ss',3,'a1',null,'user','{}')"),
    ("block", "package for a frame pair that was never approved", "insert into video_package values('vp4','fp3',1,'gem','prompt','{}','[]','[]','h',null)"),
    # connectors
    ("block", "agent switches API generation on", "update connector set generation_enabled=1,enabled_by='showrunner' where id='cvo'"),
    ("block", "enable a connector that has no credentials", "update connector set generation_enabled=1,enabled_by='user' where id='cnone'"),
    ("block", "store a key itself instead of a reference to it", "update connector set secret_ref='sk_live_abc' where id='cnone'"),
    ("block", "agent allows a capability", "insert into connector_capability values('cvo','stt','allowed','x','sound-designer')"),
    ("block", "declare a video-generation capability", "insert into connector_capability values('cvo','video_generate','allowed','x','user')"),
    ("block", "set video to API mode", "insert into production_mode(film_id,service,mode,set_by,set_at) values('f','video','api','user','t')"),
    ("block", "agent switches a film to API mode", "insert into production_mode(film_id,service,mode,set_by,set_at) values('f','voice','api','showrunner','t')"),
    ("block", "connection test that costs money without the user", "insert into connector_check(connector_id,kind,outcome,billable,detail_json,checked_at) values('cvo','auth','ok',1,'{}','t')"),
    ("allow", "free connection test while generation is off", "insert into connector_check(connector_id,kind,outcome,detail_json,checked_at) values('cvo','auth','ok','{}',datetime('now'))"),
    ("block", "rewrite connection history", "update connector_check set outcome='ok'"),
    # voice: off, then on
    ("allow", "manual take while API is off", TAKE + "('tk0','ln1','take',1,'{}','manual',null,'user','{}')"),
    ("block", "manual take not confirmed by the user", TAKE + "('tk1','ln1','take',2,'{}','manual',null,null,'{}')"),
    ("block", "take claimed from the API with no request", TAKE + "('tk2','ln1','take',3,'{}','api',null,null,'{}')"),
    ("block", "voice request: integration off and film in manual mode", VO + "('w1','envc','voapi','x',1,'p','{}','W1','sound-designer',100,'reserved','t','w1','tts','first','vpk')"),
    ("allow", "user turns the integration on", "update connector set generation_enabled=1,enabled_by='user' where id='cvo'"),
    ("block", "voice request: integration on, but film still in manual mode", VO + "('w2','envc','voapi','x',1,'p','{}','W2','sound-designer',100,'reserved','t','w2','tts','first','vpk')"),
    ("allow", "user sets the film's voice mode to API", "insert into production_mode(film_id,service,mode,set_by,set_at) values('f','voice','api','user','t')"),
    ("block", "voice request with no verified usage figure", VO + "('w3','envc','voapi','x',1,'p','{}','W3','sound-designer',100,'reserved','t','w3','tts','first','vpk')"),
    ("allow", "record a figure the user read from a screenshot", "insert into usage_snapshot(connector_id,source,unit,used,quota,raw_json,taken_at) values('cvo','user_reported','credits',10153,42000,'{}','2026-01-01')"),
    ("block", "a screenshot figure is not enough to spend on", VO + "('w4','envc','voapi','x',1,'p','{}','W4','sound-designer',100,'reserved','t','w4','tts','first','vpk')"),
    ("allow", "record usage read from the account", "insert into usage_snapshot(connector_id,source,unit,used,quota,raw_json,taken_at) values('cvo','api','credits',25000,30000,'{}','2026-01-01')"),
    ("block", "voice request for a package nobody approved", VO + "('w5','envc','voapi','x',1,'p','{}','W5','sound-designer',100,'reserved','t','w5','tts','first','vpk2')"),
    ("block", "voice request with no package", VO + "('w6','envc','voapi','x',1,'p','{}','W6','sound-designer',100,'reserved','t','w6','tts','first',null)"),
    ("block", "package sent to a different provider", VO + "('w7','envc','vo2','x',1,'p','{}','W7','sound-designer',100,'reserved','t','w7','tts','first','vpk')"),
    ("block", "music request (capability blocked by licence)", VO + "('w8','envc','voapi','x',1,'p','{}','W8','sound-designer',100,'reserved','t','w8','music','first','vpk')"),
    ("block", "sound-effect request (capability blocked by licence)", VO + "('w9','envc','voapi','x',1,'p','{}','W9','sound-designer',100,'reserved','t','w9','sfx','first','vpk')"),
    ("allow", "approved voice request inside included credits", VO + "('w10','envc','voapi','x',1,'p','{}','W10','sound-designer',4000,'reserved','2026-02-01','w10','tts','first','vpk')"),
    ("block", "request that would spill past included credits", VO + "('w11','envc','voapi','x',1,'p','{}','W11','sound-designer',1001,'reserved','2026-02-01','w11','tts','alternative','vpk')"),
    ("block", "send without a fresh capability check", "update api_request set state='sent' where id='w10'"),
    ("allow", "capability check passes", "insert into connector_check(connector_id,kind,capability,outcome,detail_json,checked_at) values('cvo','capability','tts','ok','{}',datetime('now'))"),
    ("allow", "send", "update api_request set state='sent' where id='w10'"),
    ("allow", "provider fails", "update api_request set state='failed',actual_amount=0 where id='w10'"),
    ("allow", "stage pauses", "insert into stage_pause(id,film_id,service,reason,error_json,options_json,opened_at) values('pz','f','voice','503','{}','[]','t')"),
    ("block", "request while the stage is paused", VO + "('w12','envc','voapi','x',1,'p','{}','W12','sound-designer',100,'reserved','2026-02-01','w12','tts','retry','vpk')"),
    ("block", "agent resolves the pause to manual", "update stage_pause set resolution='manual',resolved_by='showrunner' where id='pz'"),
    ("allow", "user resolves the pause", "update stage_pause set resolution='retry',resolved_by='user' where id='pz'"),
    ("allow", "controlled retry", VO + "('w13','envc','voapi','x',1,'p','{}','W13','sound-designer',100,'reserved','2026-02-01','w13','tts','retry','vpk')"),
    ("allow", "newer check fails", "insert into connector_check(connector_id,kind,outcome,detail_json,checked_at) values('cvo','auth','denied','{}',datetime('now'))"),
    ("block", "send after the newest check failed", "update api_request set state='sent' where id='w13'"),
    ("allow", "user turns the integration off again", "update connector set generation_enabled=0 where id='cvo'"),
    ("block", "voice request: mode is API, connection tested, but integration off", VO + "('w14','envc','voapi','x',1,'p','{}','W14','sound-designer',100,'reserved','2026-02-01','w14','tts','first','vpk')"),
    ("allow", "stills still work while voice is down", API + "('q20','env1','img','explore',1,'p','{}','H20','image-maker',1000,'reserved','t','k20')"),
    ("block", "agent records a top-up", "insert into ledger_charge values('lc','voiceco','topup',50000,'INR',null,null,'showrunner','t')"),
    ("block", "preflight item blocked with no next step", "insert into preflight_item values('none','x','voice','api',null,null,'blocked','{}',null)"),
    # voices
    ("block", "cloned voice without consent on file", VB + "('vb1','vr','voiceco','x','instant_clone','{}',null,null,null,'candidate',null)"),
    ("allow", "cloned voice with consent on file", VB + "('vb2','vr','voiceco','x','instant_clone','{}',null,'consent',null,'candidate',null)"),
    ("block", "cast a voice that expires soon", VB + "('vb3','vr','voiceco','x','library','{}',date('now','+36 days'),null,'take','approved','user')"),
    ("block", "cast a voice with no approved sample", VB + "('vb4','vr','voiceco','x','provider_permanent','{}',null,null,null,'approved','user')"),
    ("allow", "cast a lasting voice with a sample", VB + "('vb5','vr','voiceco','x','provider_permanent','{}',null,null,'take','approved','user')"),
    # library and rights
    ("block", "generate a sound without searching the library first", CUE + "('c1','s','foley','step','{}','generate',null)"),
    ("allow", "generate after a recorded search", CUE + "('c2','s','foley','step','{}','generate','{\"hits\":0}')"),
    ("allow", "reuse from the library", CUE + "('c3','s','foley','step','{}','reuse',null)"),
    ("allow", "fully cleared sound enters the shared library", LIB + "('s_free','studio',null,'foley','t','[]',null,'user','t',null)"),
    ("allow", "edited version of a cleared sound enters too", LIB + "('d_free','studio',null,'foley','t','[]',null,'user','t',null)"),
    ("block", "sound with unclear reuse rights", LIB + "('s_proj','studio',null,'foley','t','[]',null,'user','t',null)"),
    ("block", "layered sound with one unclear ingredient", LIB + "('d_mix','studio',null,'foley','t','[]',null,'user','t',null)"),
    ("block", "subscription-bound music in the shared library", LIB + "('s_sub','studio',null,'music','t','[]',null,'user','t',null)"),
    ("block", "terms nobody verified", LIB + "('s_unv','studio',null,'foley','t','[]',null,'user','t',null)"),
    ("block", "own recording with no rights record", LIB + "('s_none','studio',null,'foley','t','[]',null,'user','t',null)"),
    ("block", "agent admits an asset", LIB + "('s_none','film','f','foley','t','[]',null,'sound-designer','t',null)"),
    ("allow", "project-only sound kept in its own film", LIB + "('s_proj','film','f','foley','t','[]',null,'user','t',null)"),
    ("block", "widen it to the studio later", "update library_item set scope='studio' where asset_id='s_proj'"),
    ("allow", "use it in its own film", USE + "('u1','f','s_proj','final','foley')"),
    ("block", "use it in a second film", USE + "('u2','f2','s_proj','final','foley')"),
    ("block", "hide it inside a layered sound in a second film", USE + "('u3','f2','d_mix','temp','foley')"),
    ("allow", "use a cleared sound in both films (1)", USE + "('u4','f','s_free','final','foley')"),
    ("allow", "use a cleared sound in both films (2)", USE + "('u5','f2','d_free','final','foley')"),
    ("block", "music whose terms exclude film, in the final mix", USE + "('u6','f','s_nofilm','final','music')"),
    ("block", "asset with no licence record in the final mix", USE + "('u7','f','s_none','final','foley')"),
    ("block", "quietly change verified terms", "update licence_terms set r_reuse_films='yes' where id='Lproj'"),
    # hard caps and attempts
    ("block", "raise a hard cap", "update hard_cap set limit_musd=50000000"),
    ("block", "remove a hard cap", "delete from hard_cap"),
    ("allow", "user opens a second, larger envelope", "insert into spend_envelope values('env9','f','more',9000000,9000000,'user',null,null,'musd')"),
    ("block", "a second envelope does not get round the cap", API + "('q30','env9','img','x',1,'p','{}','H30','image-maker',90000,'reserved','t','k30')"),
    ("block", "noattempt: paid request with no attempt", API + "('q31','env1','img','x',1,'p','{}','H31','image-maker',10,'reserved','t','k31')"),
    ("allow", "target", "insert into generation_target(id,film_id,kind,ref_id,title) values('T','f','sfx','cue','t')"),
    ("block", "target with more than three attempts", "insert into generation_target(id,film_id,kind,ref_id,title,max_attempts) values('T4','f','sfx','cue4','t',4)"),
    ("allow", "first attempt", ATT + "('A1','T',1,'api','{}','h1',null,null,'[]','t')"),
    ("block", "second attempt while the first is unjudged", ATT + "('A2','T',2,'api','{}','h2','hinge too bright','A1','[\"f1\"]','t')"),
    ("allow", "first attempt judged", "update attempt set outcome='needs_correction' where id='A1'"),
    ("block", "retry with no reason", ATT + "('A2','T',2,'api','{}','h2',null,'A1','[\"f1\"]','t')"),
    ("block", "retry with no finding", ATT + "('A2','T',2,'api','{}','h2','hinge too bright','A1','[]','t')"),
    ("block", "identical retry", ATT + "('A2','T',2,'api','{}','h1','hinge too bright','A1','[\"f1\"]','t')"),
    ("allow", "reasoned, changed retry", ATT + "('A2','T',2,'api','{}','h2','hinge too bright','A1','[\"f1\"]','t')"),
    ("allow", "second judged", "update attempt set outcome='rejected' where id='A2'"),
    ("allow", "third attempt", ATT + "('A3','T',3,'manual','{}','h3','too much room tone','A2','[\"f2\"]','t')"),
    ("allow", "third judged", "update attempt set outcome='rejected' where id='A3'"),
    ("block", "fourth attempt", ATT + "('A4','T',4,'api','{}','h4','still not right here','A3','[\"f3\"]','t')"),
    ("block", "raise the limit", "update generation_target set max_attempts=3 where id='T'"),
    ("block", "agent grants more attempts", "insert into attempt_extension values('x','T',1,'showrunner','please','t')"),
    ("allow", "user grants one more", "insert into attempt_extension values('x','T',1,'user','one more idea','t')"),
    ("allow", "fourth attempt after the user's extension", ATT + "('A4','T',4,'api','{}','h4','still not right here','A3','[\"f3\"]','t')"),
    ("block", "rewrite an attempt", "update attempt set spec_json='{2}' where id='A1'"),
    ("block", "erase an attempt", "delete from attempt"),
    ("allow", "slot", "insert into slot values('S','f','cue','c','t')"),
    ("block", "agent approves a version", "insert into slot_version values('V0','S',1,'s_free','approved','sound-designer','sound-designer','x','{}','t',null)"),
    ("allow", "user approves version 1", "insert into slot_version values('V1','S',1,'s_free','approved','sound-designer','user','x','{}','t',null)"),
    ("allow", "agent proposes a better version", "insert into slot_version values('V2','S',2,'d_free','proposed_replacement','sound-designer',null,'cleaner attack','{}','t',null)"),
    ("block", "quietly alter the approved version", "update slot_version set asset_id='d_free' where id='V1'"),
    ("block", "delete a version", "delete from slot_version"),
    ("block", "adopt a policy change without a test run", "insert into policy_proposal values('pp','a','b','{}','showrunner','t','adopted',null,'user')"),
    ("block", "sound-effect request without the user's package approval", VO + "('w30','envc','voapi','x',1,'p','{}','W30','sound-designer',100,'reserved','2026-02-01','w30','sfx','first',null)"),
    # release
    ("block", "agent waives a release item", "insert into release_item values('f','music_rights','waived','{}','release-auditor')"),
]
bad = 0
auto = 0
for want, name, q in CASES:
    if "into api_request(id" in q and "noattempt" not in name:   # every paid request needs its own open attempt; give each test one
        auto += 1
        x(f"insert into generation_target(id,film_id,kind,ref_id,title) values('tg{auto}','f','image','r{auto}','t')")
        x(f"insert into attempt(id,target_id,attempt_no,origin,spec_json,spec_hash,created_at) values('at{auto}','tg{auto}',1,'api','{{}}','s','t')")
        q = q.replace("api_request(id", "api_request(attempt_id,id", 1).replace("values('", f"values('at{auto}','", 1)
    try:
        x(q)
        got, why = "allow", ""
    except sqlite3.Error as e:
        got, why = "block", str(e)
    bad += got != want
    print(("ok  " if got == want else "LEAK"), got, "|", name, "|", why)


def live():
    return sorted(r[0] for r in x("select id from approval_live where id in('apA','apB','apC')"))


steps = [("before any change", None, ["apA", "apB", "apC"]),
         ("character's shoes change -> only the shot that depends on shoes goes stale", "update current_hash set hash='S2' where field='shoes'", ["apA", "apC"]),
         ("character's face changes -> frame pair approval goes stale", "update current_hash set hash='F2' where field='face'", ["apC"]),
         ("frame pair replaced -> its package goes stale", "update current_hash set hash='P2' where dep_type='frame_pair'", []),
         ("face restored to the approved value -> approval is valid again", "update current_hash set hash='F1' where field='face'", ["apA"])]
for name, q, want in steps:
    if q:
        x(q)
    ok = live() == want
    bad += not ok
    print("ok  " if ok else "LEAK", "stale |", name, "|", live())
for name, q in (("stale package cannot be generated", GEN + "('g9','vp',9,'planned',null,'t')"),):
    try:
        x(q)
        bad += 1
        print("LEAK", name)
    except sqlite3.Error as e:
        print("ok   block |", name, "|", e)
n = x("select count(*) from sqlite_master where type='table' and name not like 'lesson_fts%' and name not like 'sqlite%'").fetchone()[0]
print(f"{len(CASES) + len(steps) + 1} rules checked, {bad} wrong; {n} tables (+ 1 FTS index)")
