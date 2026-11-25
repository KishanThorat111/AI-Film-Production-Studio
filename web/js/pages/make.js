// Images, Gemini video, voices, sound effects.
import { h, api, post, act, ask, paid, toast, usd, credits, when, media, cap } from "../core.js";
import { tag, cost, facts, head, empty, table, field, copyBox, items, problems, reviews, checks, attemptsLine, judge, targetTools, approval, fileDrop, fileInfo, sourceFields, VOICE_KIND, SOUND_KIND, picture, player } from "../ui.js";

const withdraw = (a, t) => a.outcome === "pending" && !a.result_asset_id && (!a.rstate || a.rstate === "failed") ? h("p.small.dim", {}, "An attempt was begun but nothing was sent. ", act("Take it back", () => post(`attempt/${a.id}/withdraw`), { cls: "quiet" })) : null;

// ------------------------------------------------------------ images
async function images(m) {
  const [d, c, b] = await Promise.all([api("images"), api("connectors"), api("money")]);
  const left = b.cap_left.image, prov = c.connectors.filter(x => d.providers.includes(x.id));
  m.append(head("Images", `In order: each is built only from approved ones. ${usd(left)} of the approved total is left for all providers together.`));
  if (d.mode !== "api") m.append(h("div.card", {}, tag("info", "by hand"), " Generation is off for this film. Allow it on Home, or import pictures made elsewhere."));
  else if (!d.allowance) m.append(h("div.card", {}, tag("warn", "no allowance"), " This film may not spend yet. ", h("a", { href: "#budget" }, "Open an allowance")));
  m.append(addImage());
  if (!d.images.length) return m.append(empty("No image is planned yet.", h("a.btn", { href: "#plan/work" }, "Ask for an image plan")));
  for (const i of d.images) {
    const sel = field("p", "Provider", { options: prov.map(p => [p.id, p.title + (p.enabled ? "" : " (off)")]) }), why = field("reason", "What was wrong, and what changes", { size: 46 }), pr = field("t", "Prompt", { area: true, value: i.prompt, rows: 7 }), src = sourceFields(d.terms, "");
    const pend = i.attempts.some(a => a.outcome === "pending" && a.result_asset_id), open = i.target?.state !== "approved" && i.target?.state !== "stopped", retry = i.used > 0;
    const body = () => ({ provider: sel.get(), prompt: pr.get() === i.prompt.trim() ? "" : pr.get(), reason: why.get() });
    m.append(h("section.card", { id: i.id }, h("div.head", {}, h("h3", {}, i.id), h("span.small.dim", {}, [cap(i.kind), i.role, i.aspect].filter(Boolean).join(", "), i.for ? ` · ${i.for}` : ""), h("span.push.row", {}, i.approved ? tag("approved") : attemptsLine(i.target, i.limit, i.used), targetTools(i.target?.state === "approved" ? i.target : null))),
      i.waiting_for.length ? h("p.small", {}, tag("waiting", "waits"), " for ", i.waiting_for.join(", ")) : null,
      h("div.thumbs", {}, i.attempts.filter(a => a.result_asset_id).map(a => h("div.stack", {}, picture(a.result_asset_id, `${i.id}, attempt ${a.attempt_no}`, `${a.surface || "by hand"}${a.actual_amount != null ? " · " + usd(a.actual_amount) : a.est_amount ? " · up to " + usd(a.est_amount) : ""}${a.reason ? " · " + a.reason : ""}`), checks(a.checks, a.result_asset_id), reviews(i.reviews.filter(r => r.target_id === a.result_asset_id), { none: " " }), judge(a), fileInfo(a.result_asset_id)))),
      i.attempts.map(a => a.withdrawn ? null : withdraw(a, i.target)), i.target?.state === "stopped" ? targetTools(i.target) : null,
      open && !i.waiting_for.length && !pend ? h("details", { open: retry || undefined }, h("summary", {}, retry ? "Prepare a corrected attempt" : "Make it"), pr.el, retry ? why.el : null,
        h("div.row.gap", {}, sel.el, paid("Generate one image", async () => (await post(`images/${encodeURIComponent(i.id)}/estimate`, body())).musd, p => post(`images/${encodeURIComponent(i.id)}/generate`, { ...body(), expect: p }), { title: `Generate ${i.id}`, what: `One picture from ${prov.find(p => p.id === sel.get())?.title || "the provider"}. The prompt and ${i.refs.length} reference pictures leave this computer.`, money: usd }, { disabled: d.mode !== "api" || !d.allowance }), cost("paid, inside the approved total")),
        fileDrop("Or import one made elsewhere", "image/*", fd => api(`images/${encodeURIComponent(i.id)}/import`, { form: fd }), retry ? [...src, why] : src), cost("local, free")) : null));
  }
}
// A picture the film needs that no agent has planned: named by you, then brought in or generated like any other.
function addImage() {
  const box = h("div"), d = h("details", {}, h("summary", {}, "Add a picture that is not in the plan"), box);
  d.addEventListener("toggle", async () => {
    if (!d.open || box.firstChild) return;
    const c = await api("images/choices"), code = field("code", "Short name, for example MARA-FACE", { size: 22 }), kind = field("kind", "What it is", { options: [["reference", "reference of a person, place or thing"], ["anchor", "the one picture that fixes how it looks"], ["frame", "a frame of a shot"]] }), ent = field("entity", "Of whom or what", { options: [["", "choose…"], ...c.entities.map(e => [e.id, `${e.name} (${e.kind})`])] }), sh = field("shot", "Shot", { options: [["", "choose…"], ...c.shots.map(s => [s.id, s.code])] }), role = field("role", "Frame", { options: [["still", "still picture"], ["first", "first frame"], ["last", "last frame"], ["plate", "background plate"], ["layer", "layer"]] }), asp = field("aspect", "Format", { options: ["16:9", "9:16", "3:4", "4:3", "1:1", "2:3", "3:2", "21:9"] }), pr = field("prompt", "Description, needed only for generating it", { area: true, rows: 2 });
    const on = () => { const f = kind.get() === "frame"; ent.el.hidden = f; sh.el.hidden = role.el.hidden = !f; };
    kind.input.addEventListener("change", on); on();
    box.append(h("div.card", {}, h("div.row", {}, code.el, kind.el, ent.el, sh.el, role.el, asp.el), pr.el, h("div.row.gap", {}, act("Add it", () => post("images/add", Object.fromEntries([code, kind, ent, sh, role, asp, pr].map(f => [f.name, f.get()]))), { done: "Added. Bring in the file on its card below." }))));
  });
  return d;
}

// ------------------------------------------------------------ Gemini video, by hand
async function video(m) {
  const d = await api("video");
  m.append(head("Gemini video", "You generate in the Gemini app. The studio prepares two exact frames and one prompt, and checks what comes back. Account names are only labels you type."));
  m.append(h("datalist", { id: "labels" }, d.labels.map(l => h("option", { value: l.label }))));
  if (!d.shots.length) m.append(empty("No shot of this film is planned as a generation.", h("a.btn", { href: "#plan/work" }, "Ask for motion")));
  if (d.others.length) m.append(outside(d.others));
  for (const s of d.shots) {
    const lab = field("label", "Account label", { size: 14 }), mod = field("model", "Model shown in the app", { size: 18 }), use = field("usage", "Usage note", { size: 16 }), why = field("reason", "Why another attempt, and what changed", { size: 36 });
    lab.input.setAttribute("list", "labels");
    const clips = s.attempts.filter(a => a.result_asset_id), step = !s.first || !s.last ? 0 : !s.pair_approved ? 1 : !s.approved ? 2 : 3;
    m.append(h("section.card", { id: s.shot }, h("div.head", {}, h("h3", {}, s.code), tag(["", "warn", "warn", "good"][step], ["frames not approved", "pair to check", "package to approve", "ready for the Gemini app"][step]), h("span.push.row", {}, attemptsLine(s.target, s.limit, s.used), targetTools(s.target?.state === "approved" ? s.target : null))),
      h("div.pair", {}, ["first", "last"].map((k, i) => s[k] ? picture(s[k], `${k} frame`, `${k} frame: ${s.frames_needed[i]}`) : empty(`${k} frame: approve ${s.frames_needed[i]} first`))),
      step >= 1 ? h("div.gap", {}, s.pair ? h("div", {}, h("div.row", {}, h("h3", {}, "Do the frames belong together?"), tag(s.pair.checks.outcome)), items(s.pair.checks.items)) : null, h("div.row.gap", {}, act(s.pair ? "Check the pair again" : "Check the pair", () => post(`video/${encodeURIComponent(s.shot)}/pair`)), s.pair && !s.pair_approved && s.pair.checks.outcome !== "fail" ? act("These are the frames", () => post(`video/pair/${s.pair.id}/approve`, { accept_warnings: s.pair.checks.outcome === "warn" }), { cls: "primary", confirm: s.pair.checks.outcome === "warn" ? "Use this pair although the check warns?" : null, why: "A generation costs your hands and quota. The warnings are above.", done: "Frames approved; the package is being reviewed" }) : null)) : null,
      s.doc && s.pair_approved ? h("div", {}, h("h3.gap", {}, "Package"), problems(s.problems), reviews(s.reviews, { none: "Not reviewed yet. The review task is on the Agents page." }), s.approved ? null : approval("video", s.package, s.reviews)) : null,
      s.approved ? h("div", {}, h("ol.gap", {}, h("li", {}, "Open the Gemini app and start a new chat with video."), h("li", {}, "Attach image 1, then image 2: ", s.doc.attachments.map(a => h("a.btn", { href: `/api/video/${s.package}/frame/${a.order}`, download: a.file }, `${a.order}: ${a.role.replace("_", " ")}`))), h("li", {}, "Paste the prompt:", copyBox(s.doc.prompt, "Copy the prompt")), h("li", {}, `Choose ${s.doc.aspect_ratio}, about ${s.doc.duration_s.min} to ${s.doc.duration_s.max} seconds, and generate once.`), h("li", {}, "Download the clip and drop it here.")),
        s.doc.acceptance?.length ? h("p.small.dim", {}, "Accept if: ", s.doc.acceptance.map(x => x.text || x).join("; ")) : null,
        s.target?.state !== "approved" && s.target?.state !== "stopped" ? fileDrop("Import the clip", "video/*", async fd => { const r = await api(`video/${s.package}/import`, { form: fd }); if (r.better_match) toast(`This looks more like the clip for ${r.better_match}`, true); }, clips.length ? [lab, mod, use, why] : [lab, mod, use]) : null,
        h("div.row", {}, act("The app refused", () => post(`video/${s.package}/log`, { state: "refused", label: lab.get(), model: mod.get() }), { cls: "quiet", done: "Logged" }), act("Generated, but not used", () => post(`video/${s.package}/log`, { state: "not_used", label: lab.get(), model: mod.get() }), { cls: "quiet", done: "Logged" })),
        s.ledger.length ? h("details", {}, h("summary", {}, `${s.ledger.length} entries in the log`), table(["Date", "State", "Label", "Model"], s.ledger.map(g => [g.performed_on, tag(g.state), g.label || "", g.app_model || ""]))) : null) : null,
      s.target?.state === "stopped" ? targetTools(s.target) : null,
      clips.length ? h("p.gap", {}, h("a.btn", { href: "#review" }, `${clips.length} clip${clips.length > 1 ? "s" : ""}: watch and judge`)) : null));
  }
}

// A clip that was made without a package: for a shot nobody planned as a generation.
function outside(list) {
  const sel = field("shot", "Shot", { options: [["", "choose…"], ...list.map(s => [s.id, s.code + (s.target?.state === "approved" ? " (has an approved clip)" : s.used ? ` (${s.used} of ${s.limit} attempts used)` : "")])] }), why = field("reason", "How it was made, and why without a package", { size: 46 }), lab = field("label", "Account label", { size: 14 }), mod = field("model", "Model shown in the app", { size: 18 });
  lab.input.setAttribute("list", "labels");
  return h("details", {}, h("summary", {}, "A clip for a shot that has no package"), h("p.small.dim", {}, "It is checked and judged like every other clip, but there are no approved frames to compare it with, and the shot's attempts count."), h("div.row", {}, sel.el), fileDrop("Import the clip", "video/*", fd => sel.get() ? api(`shots/${encodeURIComponent(sel.get())}/clip`, { form: fd }) : Promise.reject(new Error("Choose the shot first.")), [lab, mod, why]));
}

// ------------------------------------------------------------ voices
function packHead(p, kind, title) {
  return [h("div.head", {}, h("h3", {}, title), h("span.small.faint", {}, "revision ", p.rev), h("span.push", {}, p.approved ? tag("approved", p.task?.state === "imported" ? "approved and imported" : "approved") : tag("warn", "not approved"))), problems(p.problems), reviews(p.reviews, { none: "Not reviewed yet. The review task is on the Agents page." }), p.approved ? null : approval(kind, p.id, p.reviews)];
}
async function voice(m) {
  const d = await api("voice");
  m.append(head("Voices", d.mode === "api" ? "The API is allowed for this film. Making lines by hand stays possible at any time." : "By hand on the ElevenLabs website with your included credits. Approve a package, make the lines in any order, drop the files here."));
  if (!d.packages.length) return m.append(empty("No voice package yet.", h("a.btn", { href: "#plan/work" }, "Ask for voices and sounds")));
  for (const p of d.packages) {
    const s = p.doc.settings, vid = field("vid", "ElevenLabs voice id", { size: 26 }), chosen = Object.values(p.cast);
    m.append(h("section.card", { id: p.id }, packHead(p, "voice", `${p.doc.package}: ${p.doc.section}`), p.doc.casting ? h("p.small", {}, p.doc.casting) : null,
      p.casting ? null : h("div.row", {}, chosen.every(Boolean) ? [tag("good", "voice chosen"), chosen.map(v => h("span.row", {}, v.name, h("audio", { src: media(v.sample), controls: true, preload: "none" })))] : [tag("warn", "no voice yet"), "Accept a take in the casting package first."]),
      facts({ Model: d.models[p.doc.model]?.title || p.doc.model, Stability: s.stability, Similarity: s.similarity_boost, Style: s.style, Speed: s.speed, Format: p.doc.output_format.requested, Credits: p.doc.cost ? `${p.doc.cost.credits_low} to ${p.doc.cost.credits_high}` : null }),
      h("details", { open: p.approved && d.mode !== "api" || undefined }, h("summary", {}, "What to do on the website"), h("ol", {}, p.doc.manual_steps.map(x => h("li", {}, x)))), d.mode === "api" && p.approved ? vid.el : null,
      p.lines.map(l => {
        const why = field("reason", "Why another take", { size: 30 }), cr = field("credits", "Credits used", { size: 10 }), vn = field("voice", "Voice used, exact name", { size: 22 }), vk = field("voice_kind", "Kind of voice", { options: VOICE_KIND }), nt = field("note", "Note", { size: 16 });
        const takes = l.takes.filter(t => t.source !== "temp"), open = p.approved && !["stopped", "approved"].includes(l.target?.state);
        return h("div.card", { id: l.line_id }, h("div.head", {}, h("h3", {}, l.line_id, " · ", l.speaker), h("span.push.row", {}, attemptsLine(l.target, l.limit, takes.length), targetTools(l.target?.state === "approved" ? l.target : null))), h("p.small.dim", {}, "Script: ", l.text), copyBox(l.spoken_text, "Copy the text"), h("p", {}, l.delivery.intent), h("p.small.dim", {}, [l.delivery.pace && `Pace: ${l.delivery.pace}`, l.delivery.distance && `Distance: ${l.delivery.distance}`, l.delivery.avoid && `Avoid: ${l.delivery.avoid}`, l.file_name && `File: ${l.file_name}`, l.shot && `Shot ${l.shot}`, l.target_seconds && `${l.target_seconds[0]} to ${l.target_seconds[1]} s`].filter(Boolean).join(" · ")),
          takes.length > 1 ? compare(takes) : null,
          takes.map(t => h("div.stack", {}, h("div.row", {}, h("b", {}, "Take ", t.take_no), tag("", t.source === "api" ? "API" : "by hand"), h("audio", { src: media(t.asset_id), controls: true, preload: "none" })), checks(t.checks), judge({ id: t.attempt_id, outcome: t.outcome }), fileInfo(t.asset_id))), l.target?.state === "stopped" ? targetTools(l.target) : null,
          open ? fileDrop("Import this line", "audio/*", fd => api(`voice/${p.id}/${encodeURIComponent(l.line_id)}/import`, { form: fd }), [...(p.casting ? [vn, vk] : []), cr, ...(takes.length ? [why] : []), nt]) : null,
          open && d.mode === "api" ? paid("Generate this line", async () => (await post(`voice/${p.id}/${encodeURIComponent(l.line_id)}/estimate`, { voice_id: vid.get() || "x" })).credits, n => vid.get() ? post(`voice/${p.id}/${encodeURIComponent(l.line_id)}/generate`, { voice_id: vid.get(), reason: why.get(), expect: n }) : Promise.reject(new Error("Write the voice id above")), { title: "Generate this line", what: "One request to ElevenLabs with the text above.", money: credits, extra: "Included credits only. Nothing is bought." }) : null);
      })));
  }
}

// The same numbers of every take side by side.
function compare(takes) {
  const v = t => t.checks.find(c => c.check_name === "voice")?.value || {}, f = t => v(t).facts || {}, n = (x, d = 1) => x == null ? "–" : typeof x === "number" ? x.toFixed(d) : x;
  return h("details", { open: true }, h("summary", {}, "Compare the takes"), table(["Take", "", "#Seconds", "#Loudness", "#Peak", "Words"], takes.map(t => [t.take_no, t.outcome ? tag(t.outcome) : "", n(f(t).duration ?? f(t).seconds), n(f(t).lufs), n(f(t).true_peak ?? f(t).peak_db), v(t).word_differences?.length ? `${v(t).word_differences.length} differ` : "as written"])));
}

// ------------------------------------------------------------ sound: effects, ambience, music
async function sound(m) {
  const d = await api("sfx");
  m.append(head("Sound", d.mode === "api" ? "The API is allowed, with included credits you release on Budget. Every sound shows its price first." : "By hand, or allow the API on Home."));
  m.append(h("p.small.dim", {}, "ElevenLabs effects stay in this film. Their terms do not clearly allow a reusable collection, so the library refuses them."));
  m.append(sounds(d));
  if (d.packages.length) m.append(h("h2", {}, "Effects to generate"));
  else if (!d.cues.length) m.append(empty("No sound is planned yet.", h("a.btn", { href: "#plan/work" }, "Ask for voices and sounds")));
  for (const p of d.packages) {
    const why = field("reason", "What is wrong, and what changes", { size: 36 }), cr = field("credits", "Credits used", { size: 10 });
    const open = p.approved && !["stopped", "approved"].includes(p.target?.state), pend = p.attempts.some(a => a.outcome === "pending" && a.result_asset_id), done = p.attempts.filter(a => a.result_asset_id);
    m.append(h("section.card", { id: p.cue }, packHead(p, "sfx", p.doc.sound), h("p", {}, p.doc.purpose), h("p.small.dim", {}, `Library searched for ${p.doc.library_search.terms.join(", ")}: ${p.doc.library_search.hits} found. ${p.doc.library_search.why_not || ""}`), copyBox(p.doc.prompt, "Copy the prompt"), facts({ Duration: p.doc.duration_seconds + " s", "Prompt influence": p.doc.prompt_influence, Loop: p.doc.loop ? "on" : "off", File: p.doc.file_name }), h("p.small.dim", {}, "Accept if: ", p.doc.acceptance.join("; "), ". Avoid: ", p.doc.avoid),
      h("details", {}, h("summary", {}, "What to do on the website"), h("ol", {}, p.doc.manual_steps.map(x => h("li", {}, x)))),
      done.map(a => h("div.stack", {}, h("div.row", {}, h("b", {}, "Attempt ", a.attempt_no), tag("", a.origin === "api" ? "API" : "by hand"), a.actual_amount != null ? h("span.small.dim", {}, credits(a.actual_amount)) : null, h("audio", { src: media(a.result_asset_id), controls: true, preload: "none" })), checks(a.checks), judge(a), fileInfo(a.result_asset_id))), targetTools(p.target),
      open && !pend ? h("div.gap", {}, done.length ? why.el : null, d.mode === "api" ? h("div.row.gap", {}, paid("Generate this sound", async () => (await api(`sfx/${p.id}/estimate`)).credits, n => post(`sfx/${p.id}/generate`, { reason: why.get(), expect: n }), { title: "Generate this sound", what: `One request to ElevenLabs: ${p.doc.sound}.`, money: credits, extra: "Included credits only. Nothing is bought. The sound stays in this film." }), cost("included credits")) : null, fileDrop("Import a sound made by hand", "audio/*", fd => api(`sfx/${p.id}/import`, { form: fd }), done.length ? [cr, why] : [cr])) : null));
  }
}

// Every sound of the film on one list: what fills it now, and a way to fill it with your own file or a library sound.
function sounds(d) {
  const KIND = Object.fromEntries(SOUND_KIND), groups = [["foley", "Foley and effects"], ["ambience", "Ambience and room tone"], ["music", "Music"], ["designed_silence", "Silence"]], of = k => ({ hard_fx: "foley", room_tone: "ambience", motif: "music" }[k] || k);
  const row = c => {
    const src = sourceFields(d.terms), ti = field("title", "What it is", { size: 20 }), why = field("reason", "What was wrong with the previous one, and what changed", { size: 40 }), lib = field("asset", "Or a library sound", { options: [["", "choose…"], ...d.library.map(l => [l.id, `${l.title} (${KIND[l.kind] || l.kind})`])] });
    const open = !c.package && c.kind !== "designed_silence" && !["approved", "stopped"].includes(c.target?.state), done = c.package ? [] : c.attempts.filter(a => a.result_asset_id), pend = done.some(a => a.outcome === "pending");
    return h("div.need", { id: c.package ? "cue-" + c.id : c.id }, tag(["approved effect", "library sound"].includes(c.status) ? "good" : c.kind === "designed_silence" ? "" : "warn", c.kind === "designed_silence" ? "silence" : c.status), h("div.grow", {}, h("b", {}, c.purpose), h("div.small.dim", {}, `Scene ${c.scene} · ${KIND[c.kind] || "silence"}${c.package ? " · generated, see below" : ""}`),
      c.heard ? h("audio", { src: media(c.heard), controls: true, preload: "none" }) : null,
      done.map(a => h("div.stack", {}, h("div.row", {}, h("b", {}, "Attempt ", a.attempt_no), a.licence ? h("span.small.dim", {}, d.terms.find(t => t.id === a.licence)?.source || a.licence) : null, h("audio", { src: media(a.result_asset_id), controls: true, preload: "none" })), checks(a.checks), judge(a), fileInfo(a.result_asset_id))), c.package ? null : targetTools(c.target),
      open && !pend ? h("details", {}, h("summary", {}, "Fill it"), fileDrop("Your recording, ambience or music", "audio/*", fd => api(`cues/${encodeURIComponent(c.id)}/import`, { form: fd }), [ti, ...src, ...(done.length ? [why] : [])]), d.library.length ? h("div.row", {}, lib.el, act("Use this library sound", () => lib.get() ? post(`cues/${encodeURIComponent(c.id)}/library`, { asset: lib.get() }) : Promise.reject(new Error("Choose a sound")), { done: "In use" })) : h("p.small.dim", {}, "The library is empty. Sounds are admitted on the Library page.")) : null));
  };
  const sc = field("scene", "Scene", { options: d.scenes.map(s => [s.id, `${s.number}: ${s.slug.slice(0, 40)}`]) }), ki = field("kind", "Kind", { options: SOUND_KIND }), pu = field("purpose", "What it is for", { size: 40 });
  return h("div", {}, groups.map(([k, t]) => { const l = d.cues.filter(c => of(c.kind) === k); return l.length ? h("section", {}, h("h2", {}, t), h("div.card", {}, l.map(row))) : null; }),
    h("details", {}, h("summary", {}, "Add a sound that is not planned"), h("div.card", {}, h("div.row", {}, sc.el, ki.el, pu.el, act("Add it", () => post("cues", { scene: sc.get(), kind: ki.get(), purpose: pu.get() }), { disabled: !d.scenes.length, done: "Added. Fill it above, then place it on the Cut page." })))));
}

export const pages = { images: { title: "Images", draw: images }, video: { title: "Gemini video", draw: video }, voice: { title: "Voices", draw: voice }, sound: { title: "Sound", draw: sound } };
