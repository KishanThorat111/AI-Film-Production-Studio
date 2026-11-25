// Home, Script, Plan and canon, Shots.
import { h, api, post, act, ask, toast, refresh, state, go, when, cap, media, usd } from "../core.js";
import { tag, facts, head, empty, table, bar, field, problems, reviews, fileDrop, failure, jobRow, tabs, picture, copyBox } from "../ui.js";

const SERVICE = { voice: "Voices", sfx: "Sound effects", stills: "Images", inspection: "Gemini inspection", video: "Video" };

const to = n => "#" + n.page + (n.at ? "/" + encodeURIComponent(n.at) : "");

// ------------------------------------------------------------ home
async function home(m) {
  const [s, j] = await Promise.all([api("status"), api("jobs")]);
  m.append(head(s.film.title, s.film.logline || (s.film.is_benchmark ? "A benchmark. Nothing made here becomes canon." : "")));
  if (s.migrated?.copy) m.append(h("p.small.dim", {}, `The database was brought forward at this start; the version before is kept at ${s.migrated.copy}.`));
  for (const u of s.unknown) m.append(h("div.card.hot", {}, failure({ title: "A paid request has no answer", what: `${u.capability} at ${u.surface}`, where: s.film.title, state: "Unknown. It was sent and no answer was stored.", money: "unknown", safe: "Everything approved and everything made by hand", you: "Look at the provider's account history, then say what you found. It is never sent again by itself.", ref: u.id + (u.provider_job_id ? ", job " + u.provider_job_id : ""), actions: [act("Look it up at the provider", () => post("unknown/" + u.id, { action: "reconcile" }), { done: r => r.state === "succeeded" ? "Found and stored" : r.message }), act("My account shows a charge", () => post("unknown/" + u.id, { action: "charged" })), act("My account shows no charge", () => post("unknown/" + u.id, { action: "free" }))] })));
  for (const p of s.pauses) m.append(h("div.card.hot", {}, failure({ title: `${SERVICE[p.service]} paused: ${p.reason}`, what: p.error.message, state: "Paused. Nothing is sent until you decide.", money: p.error.charged, safe: (p.error.safe || []).join("; ") || "Finished work", you: "Choose how to go on", ref: p.error.request, actions: p.options.filter(o => o !== "reconcile").map(o => act({ retry: "Allow new requests", manual: "Continue by hand", wait: "Close; I will wait", cancel: "Cancel this step" }[o], () => post("pause/" + p.id, { resolution: o }))) })));
  if (s.next) m.append(h("section.card.next", { "aria-label": "Next step" }, h("div.head", {}, h("div.grow", {}, h("span.small.faint", {}, "Next step"), h("h2", {}, s.next.title), h("p.dim", {}, s.next.why)), h("div.row", {}, s.next.how ? h("span.cost", {}, s.next.how) : null, s.next.kind === "job" && s.next.ref ? act("Continue", () => post(`jobs/${s.next.ref}/resume`), { cls: "primary" }) : s.next.page === "home" ? null : h("a.btn.primary", { href: to(s.next) }, "Go there")))));
  m.append(h("div.chain", {}, s.progress.map(p => h("a", { href: "#" + p.page }, h("span", {}, p.title), h("b", {}, p.of ? `${p.done}/${p.of}` : "–"), p.note ? h("span", {}, p.note) : null, bar(p.done, p.of, p.of > 0 && p.done >= p.of)))));
  const wait = s.needs_you.filter(n => !["unknown", "pause"].includes(n.kind));
  m.append(h("div.two", {}, h("section", {}, h("h2", {}, "Needs you"), wait.length ? h("div.card", {}, wait.slice(0, 14).map(n => h("div.need", {}, tag(n.trouble ? "bad" : n.kind === "manual" ? "waiting" : "info", { judge: "judge", doc: "decide", package: "approve", decision: "story", manual: "by hand", bring: "place", cut: "watch", backup: "safety", job: "job", stopped: "stopped", script: "start" }[n.kind] || n.kind), h("div.grow", {}, h("b", {}, n.title), h("div.small.dim", {}, n.why)), n.kind === "job" ? act("Continue", () => post(`jobs/${n.ref}/resume`)) : h("div.row", {}, n.how ? h("span.cost", {}, n.how) : null, h("a.btn", { href: to(n) }, "Open"))))) : empty("Nothing waits for you."),
    s.agents.length ? h("p.small.dim", {}, "Waiting for agents: ", s.agents.map(a => `${a.role} (${a.n})`).join(", "), ". ", h("a", { href: "#agents" }, "Open Agents")) : null),
    h("section", {}, h("h2", {}, "Running and recent"), h("div.card", {}, j.jobs.length ? j.jobs.slice(0, 6).map(jobRow) : h("p.dim", {}, "No background work yet.")),
      h("h2", {}, "How this film is made"), h("div.card", {}, table(null, [["voice", true], ["sfx", true], ["stills", true], ["inspection", true], ["video", false]].map(([k, can]) => [SERVICE[k], tag(s.modes[k] === "api" ? "good" : "info", s.modes[k] === "api" ? "API allowed" : k === "inspection" ? "local only" : k === "stills" ? "off" : "by hand"), can ? act(s.modes[k] === "api" ? "Switch off API" : "Allow API", () => post("mode", { service: k, mode: s.modes[k] === "api" ? "manual" : "api" }), { cls: "quiet" }) : h("span.small.faint", {}, "always by hand, in the Gemini app")])), h("p.small.dim", {}, "Allowing the API starts nothing. Each request shows its price first.")))));
  if (s.stand_ins.length) m.append(h("details", {}, h("summary", {}, `${s.stand_ins.length} stand-ins in the newest renders`), h("p.small.dim", {}, s.stand_ins.join(", "))));
}

// ------------------------------------------------------------ script
async function scriptPage(m) {
  const d = await api("script");
  m.append(head("Script", "The words are fixed at import and never changed by the studio. A new version is a new revision; whatever was built on changed text is marked as out of date."));
  const lay = field("layout", "Layout", { options: [["", "recognise it"], ["screenplay", "screenplay"], ["labelled", "labelled (Visual:, Narration:)"], ["prose", "prose"]] });
  const res = h("div"); let file = null;
  const look = async fd => {
    file = fd.get("file"); fd.set("layout", lay.get());
    const p = await api("script/preview", { form: fd });
    res.replaceChildren(h("div.card", {}, h("h3", {}, "This is what the studio reads"), facts({ Layout: p.layout, Scenes: p.scenes.length, Pieces: p.spans.length, Words: p.words, Speakers: p.speakers.join(", ") || "none" }), p.unsure.length ? h("ul", {}, p.unsure.map(u => h("li", {}, tag("warn", "check"), " ", u))) : h("p.small.dim", {}, "Nothing looked doubtful."),
      h("details", {}, h("summary", {}, "Read it as pieces"), h("div.scroll", { style: "max-height:420px;overflow:auto" }, table(["Kind", "Text"], p.spans.map(s => [h("span.small.faint", {}, s.kind), s.text])))),
      h("div.row.gap", {}, act("Fix this revision", async () => { const f2 = new FormData(); f2.append("file", file); f2.append("layout", lay.get()); await api("script/import", { form: f2 }); }, { cls: "primary", done: "The script is fixed" }), h("span.small.dim", {}, "If it reads wrongly, choose another layout and look again."))));
    throw Object.assign(new Error(""), { quiet: true });
  };
  const inp = h("input", { type: "file", accept: ".docx,.txt,.md,.fountain,.fdx", "aria-label": "Script file" });
  const run = async () => { if (!inp.files[0]) return; const fd = new FormData(); fd.append("file", inp.files[0]); try { await look(fd); } catch (e) { if (!e.quiet) toast(e.message, true); } };
  inp.addEventListener("change", run);
  m.append(h("div.card", {}, h("div.row", {}, h("b", {}, d.doc ? "Import a new revision" : "Import the script"), inp, lay.el), h("p.small.dim", {}, "Word, Fountain, Final Draft or plain text. You see how it was read before anything is stored."), res));
  if (!d.doc) return;
  m.append(facts({ Revision: d.doc.revision, File: d.doc.original_name || d.doc.relpath, Layout: d.doc.format, Fixed: when(d.doc.locked_at) }));
  const open = d.decisions.filter(x => !x.chosen && x.options.length);
  if (open.length) m.append(h("h2", {}, "Only you can decide"), open.map(x => { const o = field("o", "Your decision", { size: 44, value: "" }); return h("div.card", { id: x.id }, h("h3", {}, x.question), (x.evidence.quotes || []).map(q => h("p.small.dim", {}, "“" + q + "”")), h("div.row.gap", {}, x.options.map(c => h("button", { type: "button", onclick: () => { o.input.value = c; } }, c)), o.el, act("Decide", () => o.get() ? post("decision/" + x.id, { chosen: o.get() }) : Promise.reject(new Error("Choose or write a decision")), { cls: "primary" }))); }));
  const done = d.decisions.filter(x => x.chosen && x.options.length);
  if (done.length) m.append(h("details", {}, h("summary", {}, `${done.length} decision${done.length === 1 ? "" : "s"} made`), table(null, done.map(x => [x.question, x.chosen]))));
  m.append(h("h2", {}, "Scenes"), table(["Scene", "Heading", "Shots", ""], d.scenes.map(s => [s.number, s.slug, s.shots, h("a", { href: "#cut/scene:" + s.id }, "edit")])));
  const inScene = new Map(); for (const s of d.scenes) if (s.span_from != null) for (let i = s.span_from; i < s.span_to; i++) inScene.set(i, s.number);
  m.append(h("h2", {}, "Text and coverage"), h("div.card", { style: "max-height:640px;overflow:auto" }, d.spans.map(s => h("div.need", {}, h("span.small.faint", { style: "width:74px" }, s.kind), h("div.grow", {}, s.kind === "heading" ? h("b", {}, s.text) : s.text), s.shots.length ? tag("good", s.shots.join(", ")) : inScene.has(s.ordinal) && ["action", "dialogue", "narration"].includes(s.kind) ? tag("", "no shot") : null))));
}

// ------------------------------------------------------------ plan and canon
const KIND = { production_plan: "Production plan", canon: "Canon", shot_list: "Shots", image_plan: "Image plan", motion_plan: "Motion", sound_plan: "Sound plan", release: "Release text" };
export function askMaker(n) {
  return act(n.doc ? "Ask for a new revision" : "Ask for it", async () => {
    const v = await ask(`${n.title}: ask the ${n.role}`, h("p.dim", {}, "This creates a task. The agent starts only when you run it on the Agents page; that uses your Claude plan."), { yes: "Create the task", fields: [{ name: "note", label: "What matters to you (optional)", el: h("textarea", { rows: 3 }) }] });
    return v ? post("make", { action: n.action, scope: n.scope, note: v.note }) : null;
  }, { done: "The task is waiting on the Agents page", disabled: !n.ready || !!n.task, title: n.ready ? "" : n.why });
}
async function plan(m, arg) {
  const d = await api("docs");
  m.append(head("Plan and canon", "Agents propose documents. Rules check them, somebody else reviews them, you decide. Only approved documents have an effect."));
  const main = d.next.filter(n => !n.scope.startsWith("scene:") && !n.scope.startsWith("shot:")), perScene = d.next.filter(n => n.scope.startsWith("scene:")), perShot = d.next.filter(n => n.scope.startsWith("shot:"));
  const rows = l => table(["Work", "Who", "State", ""], l.map(n => [n.title, h("span.dim", {}, n.role), n.task ? tag(n.task.state === "leased" ? "leased" : "waiting", n.task.state === "leased" ? "being made" : "waiting for its agent") : n.doc ? tag(n.doc.status, `revision ${n.doc.rev}, ${n.doc.status}`) : n.ready ? tag("", "not made") : h("span.small.faint", {}, n.why), askMaker(n)]));
  const [t, box] = tabs([["docs", "Documents"], ["work", "Ask for work"], ["canon", "Canon"]], ["canon", "work"].includes(arg) ? arg : d.docs.length ? "docs" : "work", (k, b) => {
    if (k === "work") b.append(rows(main), perScene.length ? h("details", { open: true }, h("summary", {}, "Scenes"), rows(perScene)) : null, perShot.length ? h("details", {}, h("summary", {}, "Generated shots"), rows(perShot)) : null);
    else if (k === "canon") b.append(canonView(d.canon));
    else b.append(...(d.docs.length ? d.docs.map(docCard) : [empty("Nothing has been proposed yet.", h("a.btn", { href: "#plan/work" }, "Ask for the production plan"))]));
  });
  m.append(t, box);
}
function docCard(d) {
  const why = field("why", "why you accept it as it is", { size: 40 }), back = field("note", "what should be different", { size: 44 });
  const bad = d.reviews.some(r => r.verdict !== "APPROVED") || !d.reviews.length;
  return h("section.card", { id: d.id }, h("div.head", {}, h("h3", {}, KIND[d.kind], d.scope === "film" ? "" : " · " + d.scope.split(":").slice(1).join(":")), tag(d.status), h("span.small.faint.push", {}, `revision ${d.rev} by ${d.made_by}, ${when(d.created_at)}`)),
    d.stale.length ? h("p", {}, tag("warn", "out of date"), ` The ${d.stale.join(" and ")} changed since this was written.`) : null, d.changed_vs_prior ? h("p.small.dim", {}, "Changed: ", d.changed_vs_prior) : null, d.decision_note ? h("p.small", {}, "Your note: ", d.decision_note) : null,
    d.problems.length ? problems(d.problems) : null, d.gate ? reviews(d.reviews, { none: "Not reviewed yet. The review task is on the Agents page." }) : null,
    h("details", { open: d.status === "proposed" || undefined }, h("summary", {}, "Read it"), docBody(d)),
    d.status === "proposed" && d.gate ? h("div", {}, h("div.row.gap", {}, act("Approve", () => post(`docs/${d.id}/approve`), { cls: "primary", done: "Approved and in effect" }), back.el, act("Send back", () => post(`docs/${d.id}/return`, { note: back.get() }), { done: "Sent back with your note" })), bad ? h("details", {}, h("summary", {}, "Approve without a positive review, with a reason"), h("div.row", {}, why.el, act("Approve with this reason", () => post(`docs/${d.id}/approve`, { reason: why.get() })))) : null) : null);
}
function docBody(d) {
  const x = d.doc;
  if (d.kind === "production_plan") return h("div.stack", {}, x.tone ? h("p", {}, h("i", {}, x.tone.statement)) : null, x.scenes.map(s => h("details", { open: x.scenes.length < 4 || undefined }, h("summary", {}, `Scene ${s.number}: ${s.objective}`), facts({ "Audience before": s.audience_before, "Audience after": s.audience_after, Emotion: s.emotion }), h("ol", {}, s.beats.map(b => h("li", {}, b.what, h("span.small.dim", {}, " ", b.visual_need, b.question_opened ? ` Opens: ${b.question_opened}` : "", b.question_answered ? ` Answers: ${b.question_answered}` : "")))))), x.knowledge.length ? h("div", {}, h("h3", {}, "Who knows what"), table(["Fact", "Known by", "How"], x.knowledge.map(k => [k.fact, k.known_by.join(", "), k.fairness]))) : null, x.invariants.length ? h("div", {}, h("h3", {}, "Must never be broken"), h("ul", {}, x.invariants.map(i => h("li", {}, tag(i.severity), " ", i.rule)))) : null, x.open_issues.length ? h("div", {}, h("h3", {}, "Open in the script"), h("ul", {}, x.open_issues.map(q => h("li", {}, q.question)))) : null);
  if (d.kind === "canon") return canonView(x.entities.map(e => ({ ...e, spec: { ...e.spec, must_hold: e.must_hold, may_vary: e.may_vary } })));
  if (d.kind === "shot_list") return h("div.stack", {}, h("p", {}, h("i", {}, x.intention)), table(["Shot", "Length", "For what", "Camera", "Technique"], x.shots.map(s => [h("b", {}, s.code), `${s.frames} f`, h("div", {}, s.purpose, s.withheld ? h("div.small.dim", {}, "Withheld: ", s.withheld) : null), h("span.small", {}, `${s.camera.size}, ${s.camera.move}`), h("div", {}, cap(s.technique), s.technique_reason ? h("div.small.dim", {}, s.technique_reason) : null)])), x.left_out?.length ? h("p.small.dim", {}, `${x.left_out.length} pieces left out with reasons`) : null);
  if (d.kind === "image_plan") return h("div.stack", {}, x.images.map(i => h("details", {}, h("summary", {}, h("b", {}, i.code), ` ${i.kind}${i.role ? ", " + i.role : ""}`, h("span.small.dim", {}, i.refs.length ? " from " + i.refs.join(", ") : "")), h("p.pre", {}, i.prompt))));
  if (d.kind === "release") return h("div.stack", {}, h("ol", {}, x.title_options.map(t => h("li", {}, t))), h("p.pre", {}, x.description), h("p.small.dim", {}, x.tags.join(", ")));
  return h("pre.copy", {}, JSON.stringify(x, null, 1));
}
const canonView = list => list.length ? h("div.grid.wide", {}, list.map(e => h("div.card", {}, h("div.row", {}, h("h3", {}, e.name), h("span.small.faint", {}, e.kind), e.status ? h("span.push", {}, tag(e.status)) : null), (e.spec.must_hold || e.spec.must_never_change || []).length ? h("ul", {}, (e.spec.must_hold || e.spec.must_never_change).map(x => h("li", {}, x))) : null, h("details", {}, h("summary", {}, "All facts"), h("pre.copy", {}, JSON.stringify(e.spec, null, 1)))))) : empty("No canon yet. It follows the approved plan.");

// ------------------------------------------------------------ shots
async function shots(m) {
  const d = await api("shots");
  const can = d.shots.filter(s => s.can_previs);
  m.append(head("Shots", "What each shot is for, how it is made, and what stands in for it now.", can.length ? act(`Render ${can.length} storyboards in 3D`, () => post("previs", { shots: can.map(s => s.id) }), { done: "The 3D worker has started" }) : null));
  if (!d.shots.length) return m.append(empty("No shots yet. They follow the approved plan and canon.", h("a.btn", { href: "#plan/work" }, "Ask for shots")));
  let scene = null;
  for (const s of d.shots) {
    if (s.scene_id !== scene) { scene = s.scene_id; m.append(h("h2", {}, `Scene ${s.number}`, h("span.dim.small", {}, "  ", s.slug))); }
    const pv = s.previs[0];
    m.append(h("section.card", {}, h("div.two", {}, h("div", {}, h("div.head", {}, h("h3", {}, s.code), tag("", "tier " + s.tier), tag(s.now || "", s.now || "nothing yet"), s.status !== "locked" ? tag("draft", "not approved") : null),
      h("p", {}, s.spec.purpose), facts({ Length: `${s.spec.frames} frames`, Picture: s.spec.camera?.size, Move: s.spec.camera?.move, Lens: s.spec.camera?.lens_mm ? s.spec.camera.lens_mm + " mm" : null, Technique: cap(s.technique), Light: s.spec.light }), s.spec.withheld ? h("p.small", {}, h("b", {}, "Withheld: "), s.spec.withheld) : null, s.spec.motion_requirement ? h("p.small.dim", {}, s.spec.motion_requirement) : null,
      h("p.small.dim", {}, s.images.length ? ["Images: ", s.images.map(i => i.code).join(", ")] : "", s.motion ? " · Gemini shot" : "", s.can_previs ? act("3D storyboard", () => post("previs", { shots: [s.id] }), { cls: "quiet", done: "The 3D worker has started" }) : null)),
      h("div", {}, s.picture ? picture(s.picture, s.code, "approved picture") : pv ? h("figure", {}, h("img", { src: `/previs/${encodeURIComponent(state.film)}/${pv}/0000.png`, loading: "lazy", alt: "3D storyboard of " + s.code }), h("figcaption", {}, "3D storyboard")) : empty("No picture yet")))));
  }
}

export const pages = { home: { title: "Home", draw: home }, script: { title: "Script", draw: scriptPage }, plan: { title: "Plan and canon", draw: plan }, shots: { title: "Shots", draw: shots } };
