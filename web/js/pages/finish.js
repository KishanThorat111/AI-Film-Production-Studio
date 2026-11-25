// Review, cut, release.
import { h, api, post, act, ask, paid, toast, refresh, usd, when, media, secs, cap, state, go } from "../core.js";
import { tag, facts, head, empty, table, field, items, problems, reviews, checks, judge, fileDrop, fileInfo, player, picture, tabs, copyBox } from "../ui.js";

// ------------------------------------------------------------ review: your eyes first, then the findings
async function review(m, arg) {
  const [d, s] = await Promise.all([api("review"), api("status")]);
  m.append(head("Review", "Your own verdict first. Measurements and findings appear after it, so that they cannot lead you; disagreements are the measure of the inspection."));
  m.append(h("details", {}, h("summary", {}, "Try the inspection on any clip"), h("p.small.dim", {}, "Only a trial: the clip is not attached to a shot and starts no attempt. Local and free."), fileDrop("A clip of yours", "video/*", fd => api("inspect/trial", { form: fd }))));
  const list = d.items;
  if (!list.length) return m.append(empty("No clip, take or picture has been imported into this film yet."));
  const kinds = [["all", `All ${list.length}`], ["video", "Clips"], ["image", "Pictures"], ["audio", "Sound"]];
  const [t, box] = tabs(kinds, kinds.some(k => k[0] === arg) ? arg : "all", (k, b) => list.filter(x => k === "all" || x.kind === k).forEach(x => b.append(one(x, s.film))));
  m.append(t, box);
}
function one(x, film) {
  const prov = JSON.parse(x.provenance_json || "{}"), blind = x.labels.find(l => l.blind), name = x.attempt?.title || (prov.trial ? "Trial clip" : prov.imported_for || x.kind), vid = x.kind === "video";
  const note = field("n", "What you noticed (optional)", { size: 40 }), why = field("w", "Why you approve it anyway", { size: 36 });
  const p = vid ? player(media(x.id), film.fps_num) : null;
  const verdict = v => post(`review/${x.id}/blind`, { verdict: v, kind: { video: "watch", audio: "listen", image: "look" }[x.kind], note: note.get() });
  const show = blind || (!vid && x.attempt && x.attempt.outcome !== "pending");
  return h("section.card", { id: x.id }, h("div.head", {}, h("h3", {}, name), h("span.small.faint", {}, x.attempt ? `attempt ${x.attempt.attempt_no}` : "", " ", when(x.created_at)), h("span.push", {}, x.attempt ? tag(x.attempt.outcome) : null)),
    vid ? p.el : x.kind === "audio" ? h("audio", { src: media(x.id), controls: true, preload: "none" }) : h("div", { style: "max-width:640px" }, picture(x.id, name)),
    !show ? h("div.gap", {}, h("p", {}, vid ? "Watch it at full speed, then step through what bothered you." : x.kind === "audio" ? "Listen twice." : "Look at it."), h("div.row.gap", {}, note.el, act("Good", () => verdict("good")), act("Fixable", () => verdict("fixable")), act("Bad", () => verdict("bad"))))
      : h("div", {}, blind ? h("p.small", {}, tag("blind", "your blind verdict"), " ", blind.verdict, " ", h("span.dim", {}, JSON.parse(blind.tags_json || "{}").note || "")) : null, checks(x.checks, x.id), x.reports.length ? reviews(x.reports) : h("p.small.dim", {}, "No inspector has looked at it yet; the task is on the Agents page."), judge(x.attempt, "look"), fileInfo(x.id),
        vid && x.attempt?.outcome === "accepted" ? h("details", {}, h("summary", {}, "Pass this clip for the finished film"), h("p.small.dim", {}, "Accepting means you want to use it. Passing means its known defects are no reason against the finished film."), h("div.row.gap", {}, why.el, act("Pass it", () => post(`clip/${x.id}/approve`, { reason: why.get() }), { done: "Passed" }))) : null,
        vid && !x.checks.some(c => c.check_name === "gemini") ? h("div.gap", {}, paid("Second opinion by Gemini", async () => (await post(`inspect/${x.id}/estimate`)).musd, n => post(`inspect/${x.id}/gemini`, { expect: n }), { title: "Second opinion", what: "The clip is sent to the Gemini API for inspection only. It is evidence, never a verdict.", money: usd }, { cls: "quiet" })) : null));
}

// ------------------------------------------------------------ cut
async function cut(m, arg) {
  const d = await api("cut" + (arg ? "?scope=" + encodeURIComponent(arg) : ""));
  const sel = h("select", { "aria-label": "What to edit", onchange: () => go("cut", sel.value) }, d.scopes.map(s => h("option", { value: s.scope, selected: s.scope === d.scope }, `${s.title}${s.rev ? `, revision ${s.rev}` : ""}${s.slug ? ": " + s.slug.slice(0, 40) : ""}`)));
  const scope = d.scope, sc = scope.startsWith("scene:") ? scope.slice(6) : null, me = d.scopes.find(s => s.scope === scope);
  m.append(head("Cut", "The edit names shots, lines and sounds, not files. The best material that exists fills each place; what is still a stand-in is marked.", sel));
  if (d.missing) {
    m.append(empty(d.missing, sc && me?.shots ? act("Assemble the designed shots in script order", () => post("cut/assemble", { scene: sc }), { cls: "primary" }) : null, scope === "film" ? act("Join the scenes", () => post("cut/film"), { cls: "primary" }) : null, sc && !me?.shots ? h("a.btn", { href: "#plan/work" }, "Ask for shots") : null));
    return d.revisions?.length ? m.append(revs(d)) : null;
  }
  const tl = d.timeline, fps = tl.fps[0] / tl.fps[1], last = d.renders[0];
  const kinds = [["preview", "Preview, half size"], ["review", "Review, full size"], ["master", "Master"]], kind = field("k", "Render", { options: kinds, value: "review" });
  m.append(h("div.card", {}, h("div.row", {}, tag(d.status_of_rev, `revision ${d.rev}, ${d.status_of_rev}`), h("span.small.dim", {}, `by ${d.author} · ${secs(tl.frames, fps)} · ${tl.frames} frames`), h("span.push.row", {}, kind.el, act("Render", () => post("cut/render", { scope, kind: kind.get() }), { cls: "primary", done: "Rendering has started" }))),
    last ? null : h("p.small.dim", {}, "Not rendered yet. Stand-ins are made with the first render.")));
  let pl = null;
  if (last?.asset_id) { pl = player(media(last.asset_id), fps, { onframe: f => headLine(f) }); m.append(h("div.two", {}, h("div", {}, pl.el, h("p.small.dim.gap", {}, `${cap(last.kind)} of revision ${last.cut_rev ?? last.rev}, ${when(last.created_at)}`, last.cut_rev && last.cut_rev !== d.rev ? " · older than the edit below" : "")), h("div", {}, h("h3", {}, "Checks"), h("div.row", {}, tag(last.qa.outcome)), items(last.qa.items), last.qa.facts ? h("p.small.faint", {}, `Picture ${last.qa.facts.picture_s ?? "–"} s at ${last.qa.facts.render_fps ?? "–"} frames/s, sound ${last.qa.facts.sound_s ?? "–"} s`) : null))); }
  const detail = h("div"), heads = [];
  const headLine = f => heads.forEach(x => x.style.left = 100 * f / tl.frames + "%");
  const lane = (label, list, cls) => { const hd = h("i.playhead", { style: "left:0" }); heads.push(hd); return h("div", {}, h("label", {}, label), h("div.lane", {}, list.map(c => h("button", { type: "button", cls: cls(c), title: `${c.id}: ${c.reason || ""}`, style: `left:${100 * c.a / tl.frames}%;width:${Math.max(.25, 100 * (c.b - c.a) / tl.frames)}%`, onclick: e => { document.querySelectorAll(".lane button").forEach(b => b.setAttribute("aria-pressed", "false")); e.currentTarget.setAttribute("aria-pressed", "true"); pl?.seek(c.a); pick(c); } }, c.id)), hd)); };
  const good = v => ["generated clip", "generated still", "approved take", "approved effect", "library sound", "studio sound", "chosen file", "black", "title card"].includes(v);
  const spf = 48000 / fps, voice = [], other = [];
  for (const a of tl.audio) (["dialogue", "narration"].includes(a.bus) ? voice : other).push({ ...a, a: Math.round(a.at_sample / spf), b: Math.round((a.at_sample + (a.dur_samples || spf * fps * 2)) / spf), sound: true, st: d.status["voice " + a.id] || d.status["sound " + a.id] });
  for (const p of d.planned || []) voice.push({ ...p, reason: p.text, st: p.status });
  m.append(h("div.lanes", {}, tl.video.slice().reverse().map(t => lane(t.track ? `Picture ${t.track}` : "Picture", t.clips.map(c => ({ ...c, a: c.at, b: c.at + c.dur, st: d.status[c.id] })), c => good(c.st) ? "" : "stand")), lane("Speech", voice, c => good(c.st) ? "voice" : "voice stand"), [["foley", "Foley and effects"], ["ambience", "Ambience"], ["music", "Music"]].map(([b, t]) => { const l = other.filter(x => (x.bus === "hard_fx" ? "foley" : x.bus) === b); return l.length ? lane(t, l, () => "snd") : null; }), tl.silences.length ? lane("Silence", tl.silences.map((s, i) => ({ id: "silence " + (i + 1), a: s.from_frame, b: s.from_frame + s.frames, reason: s.reason })), () => "sil") : null), detail);
  const base = d.rev; let cursor = 0;
  const send = (cmd, dry) => post("cut/commands", { scope, base, commands: [].concat(cmd), dry });
  function pick(c) {
    cursor = c.a; headLine(c.a);
    const note = field("t", "Ask for a change here", { size: 50 }), dur = field("d", "Length in frames", { type: "number", size: 6, value: c.b - c.a }), gain = field("g", "Level in dB", { type: "number", size: 6, value: typeof c.gain_db === "number" ? c.gain_db : 0 }), why = field("w", "Reason", { size: 30 });
    const raw = d.doc.clips.find(x => x.id === c.id), rs = d.doc.audio.find(x => x.id === c.id);
    detail.replaceChildren(h("div.card", {}, h("div.head", {}, h("h3", {}, c.id), c.st ? tag(c.st) : null, h("span.small.dim", {}, `${secs(c.a, fps)} to ${secs(c.b, fps)} · frames ${c.a} to ${c.b}`)), h("p", {}, c.reason || ""), raw?.effects?.length ? h("p.small.dim", {}, "Effects: ", raw.effects.map(e => e.op).join(", ")) : null,
      h("div.row.gap", {}, note.el, act("Note it for the editor", () => post("cut/note", { scope, text: note.get(), frame: pl ? pl.frame() : c.a, clip_id: c.id }), { done: "Noted" })),
      raw ? h("div.row.gap", {}, dur.el, why.el, act("Change length", () => send({ op: "trim", id: c.id, dur: +dur.get(), reason: why.get() }), { done: r => r.changes.join("; ") }), raw && c.id !== d.doc.clips.filter(x => !x.track)[0]?.id ? act("Earlier", () => { const l = d.doc.clips.filter(x => !x.track), i = l.findIndex(x => x.id === c.id); return send({ op: "move", id: c.id, before: l[i - 1].id, reason: why.get() || "order" }); }, { cls: "quiet" }) : null, act("Remove", () => send({ op: "remove", id: c.id, reason: why.get() }), { cls: "danger" })) : null,
      rs ? h("div.row.gap", {}, gain.el, why.el, act("Change level", () => send({ op: "set_sound", id: c.id, fields: { gain_db: +gain.get() }, reason: why.get() })), act("Remove", () => send({ op: "remove", id: c.id, reason: why.get() }), { cls: "danger" })) : null));
  }
  m.append(adder(d, () => pl ? pl.frame() : cursor, send));
  const open = d.notes.filter(n => n.state === "open"), why = field("why", "Why you approve it with open points", { size: 40 }), cur = d.revisions[0];
  m.append(h("div.two", {}, h("section", {}, h("h2", {}, "What the rules say"), problems(d.problems), d.final_blockers.length ? h("details", {}, h("summary", {}, `${d.final_blockers.length} files may not be used in a finished film yet`), h("ul", {}, d.final_blockers.slice(0, 12).map(b => h("li", {}, h("span.small", {}, b.why, " ", h("span.faint", {}, b.what.slice(0, 80))))))) : null),
    h("section", {}, h("h2", {}, "Decide"), h("div.card", {}, cur?.status === "approved" ? h("p", {}, tag("approved"), ` Revision ${cur.rev} is your approved edit.`) : h("div.stack", {}, h("p.small.dim", {}, "Watch the render of this revision first."), why.el, h("div.row", {}, act("Approve this edit", () => post("cut/approve", { id: cur.id, reason: why.get() }), { cls: "primary", done: "Approved" })))),
      open.length ? [h("h2", {}, "Open notes"), h("ul.plain", {}, open.map(n => h("li", {}, n.frame != null ? h("span.mono.small", {}, secs(n.frame, fps), " ") : null, n.text)))] : null)), revs(d), d.notes.some(n => n.by !== "user") ? h("details", {}, h("summary", {}, "Requests from the editor"), h("ul", {}, d.notes.filter(n => n.by !== "user").map(n => h("li", {}, n.clip_id ? h("b", {}, n.clip_id, " ") : null, n.text)))) : null,
    h("p.small.dim", {}, h("a", { href: "/api/subtitles?film=" + encodeURIComponent(state.film) + "&scope=" + encodeURIComponent(scope), target: "_blank" }, "Captions of this edit")));
}
// Put something into the edit at the playhead. What is named here is filled with the approved take, sound or picture, now or later.
function adder(d, at, send) {
  const p = d.placeable, base = d.timeline.video.find(t => !t.track)?.clips || [], why = field("why", "Reason", { size: 30 }), gain = field("g", "Level in dB", { type: "number", size: 5, value: 0 }), len = field("len", "Stop after (frames; empty: play all)", { type: "number", size: 8 });
  const opts = [...p.lines.map(x => ["line|" + x.id, `Line, ${x.ready ? "approved take" : "no take yet"}: ${x.text}`]), ...p.cues.map(x => ["cue|" + x.id, `Sound, ${x.status}: ${x.purpose}`]), ...p.library.map(x => ["asset|" + x.id, `Library, ${x.kind.replace("_", " ")}: ${x.title}`])];
  const what = field("what", "Sound", { options: [["", "choose…"], ...opts] }), bus = field("bus", "Track", { options: [["", "as it is meant"], ["dialogue", "dialogue"], ["narration", "narration"], ["foley", "foley"], ["hard_fx", "effects"], ["ambience", "ambience"], ["music", "music"]] });
  const pic = field("pic", "Picture", { options: [["", "choose…"], ...p.shots.map(x => ["shot|" + x.id, "Shot " + x.code]), ...p.images.map(x => ["image|" + x, "Image " + x])] }), dur = field("dur", "Frames", { type: "number", size: 5, value: 48 });
  const name = s => { let n = s.split(":").at(-1).replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 30), i = 2; const used = new Set([...d.doc.clips, ...d.doc.audio].map(x => x.id)); while (used.has(n)) n = n.replace(/-\d+$/, "") + "-" + i++; return n; };
  const under = f => base.filter(c => c.at <= f).at(-1) || base[0];
  return h("details", {}, h("summary", {}, "Add a sound or a picture at the playhead"), h("div.card", {},
    h("div.row", {}, what.el, bus.el, gain.el, len.el, act("Add the sound", () => {
      if (!what.get() || !why.get()) throw new Error("Choose a sound and write the reason");
      const [k, ...r] = what.get().split("|"), id = r.join("|"), f = at(), c = under(f), x = k === "line" ? p.lines.find(l => l.id === id) : k === "cue" ? p.cues.find(l => l.id === id) : p.library.find(l => l.id === id);
      return send({ op: "place_sound", sound: { id: name(k === "asset" ? x.title.replaceAll(" ", "-") : id), source: { [k]: id }, bus: bus.get() || x.bus, at: { clip: c.id, frame: f - c.at }, ...(len.get() ? { frames: +len.get() } : {}), ...(+gain.get() ? { gain_db: +gain.get() } : {}), reason: why.get() }, reason: why.get() });
    }, { done: r => r.changes.join("; ") })),
    h("div.row.gap", {}, pic.el, dur.el, act("Add the picture after the clip under the playhead", () => {
      if (!pic.get() || !why.get()) throw new Error("Choose a picture and write the reason");
      const [k, ...r] = pic.get().split("|"), id = r.join("|");
      return send({ op: "place", clip: { id: name(id), source: { [k]: id }, dur: +dur.get(), effects: [], reason: why.get() }, after: under(at()).id, reason: why.get() });
    }, { done: r => r.changes.join("; ") })),
    h("div.row.gap", {}, why.el, h("span.cost", {}, "local, free")), h("p.small.dim", {}, "Move the playhead with the player, or click a clip above. Any file you brought in and accepted is offered here by its line, sound, image or shot; it does not have to be placed again when you accept a better version.")));
}
const revs = d => h("details", {}, h("summary", {}, `${d.revisions.length} revision${d.revisions.length === 1 ? "" : "s"}`), table(["Revision", "State", "By", "Commands", "Made", ""], d.revisions.map(r => [r.rev, tag(r.status), r.author, r.commands, when(r.created_at), h("span.small.dim", {}, r.note || "")])));

// ------------------------------------------------------------ release
async function release(m) {
  const d = await api("release");
  m.append(head("Release", "A checked package for YouTube. The studio never uploads; you do.", act("Build the package", () => post("release/build", {}), { cls: "primary", done: "Building has started", disabled: !d.scope })));
  if (!d.scope) return m.append(empty("There is no edit to release yet.", h("a.btn", { href: "#cut" }, "Open Cut")));
  const NEXT = { open: "open", fail: "fail", pass: "pass", waived: "waived" };
  m.append(h("div.two", {}, h("section", {}, h("h2", {}, "Before it may go out"), h("div.card", {}, d.items.map(i => { const why = field("w", "Why it is fine without", { size: 26 }); return h("div.need", {}, tag(NEXT[i.state]), h("div.grow", {}, h("b", {}, i.title), h("div.small.dim", {}, evidence(i))), h("div.row", {}, i.manual && i.state !== "pass" ? act("Done", () => post("release/item", { item: i.item, state: "pass" })) : null, i.state !== "pass" && !["rights", "technical", "watched", "film_approved"].includes(i.item) ? h("details", {}, h("summary", {}, "waive"), why.el, act("Waive", () => post("release/item", { item: i.item, state: "waived", reason: why.get() }))) : null)); }))),
    h("section", {}, h("h2", {}, "Packages"), d.packages.length ? d.packages.map(p => h("div.card", {}, h("div.head", {}, h("h3", {}, p.kind === "long" ? "Film" : "Short", ` ${p.rev}`), tag(p.status), p.doc.final ? null : tag("warn", "draft with stand-ins")), h("p.small.dim.pre", {}, p.folder), items(p.doc.qa.items.filter(i => i[1] !== "pass").slice(0, 6)), p.doc.stand_ins?.length ? h("p.small.dim", {}, `${p.doc.stand_ins.length} stand-ins`) : null, p.status === "ready" ? act("Approve this package", () => post("release/approve", { id: p.id }), { cls: "primary" }) : null)) : h("p.dim", {}, "None built yet. A package can be built at any time; with stand-ins it is marked as a draft."), h("p.small.dim.gap", {}, `${d.captions} captions · made with: `, Object.entries(d.made_with).filter(x => x[1]).map(x => `${x[1]} ${x[0]}`).join(", ") || "nothing generated yet"), d.credits.length ? h("details", {}, h("summary", {}, "Credits the licences require"), h("ul", {}, d.credits.map(c => h("li", {}, c)))) : null)));
  const t = d.text || { schema_version: "1", title_options: [], description: "", tags: [], thumbnail: { concept: "" }, disclosure: { altered_or_synthetic: true, note: "Pictures and voices in this film were made with AI tools." }, chapters: [], shorts: [], credits: [], content_notes: [] };
  const f = { title: field("t", "Title", { size: 70, value: t.chosen_title || t.title_options[0] || "" }), desc: field("d", "Description", { area: true, rows: 9, value: t.description }), tags: field("g", "Tags, separated by commas", { size: 70, value: t.tags.join(", ") }), thumb: field("h", "Thumbnail idea", { size: 70, value: t.thumbnail.concept }), tt: field("x", "Words on the thumbnail (optional)", { size: 30, value: t.thumbnail.text || "" }), dis: field("s", "AI disclosure", { area: true, rows: 2, value: t.disclosure?.note || "" }), notes: field("c", "Content notes, separated by commas", { size: 70, value: (t.content_notes || []).join(", ") }) };
  const doc = () => ({ ...t, chosen_title: f.title.get(), title_options: t.title_options.includes(f.title.get()) ? t.title_options : [f.title.get(), ...t.title_options].filter(Boolean).slice(0, 10), description: f.desc.get(), tags: f.tags.get().split(",").map(x => x.trim()).filter(Boolean), thumbnail: { ...t.thumbnail, concept: f.thumb.get() || "a frame of the film", text: f.tt.get() }, disclosure: { altered_or_synthetic: true, note: f.dis.get() }, content_notes: f.notes.get().split(",").map(x => x.trim()).filter(Boolean), schema_version: "1" });
  m.append(h("h2", {}, "Text"), h("div.card.stack", {}, t.title_options.length > 1 ? h("div.row", {}, t.title_options.map(x => h("button.quiet", { type: "button", onclick: () => { f.title.input.value = x; } }, x))) : null, Object.values(f).map(x => x.el), h("div.row.gap", {}, act("Save and use this text", async () => { const r = await post("release/text", { doc: doc() }); await post(`docs/${r.id}/approve`, { note: "written by you", reason: "written and checked by me" }); }, { cls: "primary", done: "Saved" }), h("span.small.dim", {}, "Chapters and Shorts come with the release auditor's draft, or stay empty."))));
  if (d.text?.shorts?.length) m.append(h("h2", {}, "Shorts"), table(["Title", "Frames", "Hook", ""], d.text.shorts.map((s, i) => [s.title, `${s.from_frame}–${s.to_frame}`, s.hook, act("Build", () => post("release/build", { short: i }), { done: "Building has started" })])));
  const a = field("a", "From frame", { type: "number", size: 7, value: 0 }), b = field("b", "To frame", { type: "number", size: 7, value: Math.min(720, Math.round((d.seconds || 10) * 24)) }), ti = field("ti", "Title", { size: 36 }), st = field("st", "Picture", { options: [["crop", "cropped to 9:16"], ["band", "whole width, black above and below"]] }), x = field("cx", "Crop centre, -1 left to 1 right", { type: "number", size: 5, value: 0 });
  m.append(h("details", {}, h("summary", {}, "Build a Short from any part"), h("div.card", {}, h("div.row", {}, a.el, b.el, ti.el, st.el, x.el, act("Build this Short", () => post("release/build", { short: { title: ti.get() || "Short", from_frame: +a.get(), to_frame: +b.get(), style: st.get(), x: +x.get() } }), { done: "Building has started" })))));
}
function evidence(i) { const e = i.evidence || {}; return { film_approved: e.status ? `${e.scope}, revision ${e.revision}: ${e.status}` : e.why, material: e.stand_ins ? `${e.stand_ins} stand-ins, for example ${(e.first || []).map(x => x[0]).slice(0, 3).join(", ")}` : "", rights: (e.blocked || []).slice(0, 2).join("; "), clips: e.clips ? `${e.clips} clips, ${e.without_gate.length} not passed` : "no generated clips", voices: e.temporary?.length ? `temporary: ${e.temporary.join(", ")}` : "", technical: e.render ? e.outcome : "no master of this edit yet", text: e.title || "no title chosen", disclosure: e.text?.note || "not written", credits: e.missing?.length ? "missing: " + e.missing.join("; ") : e.required?.length ? "all present" : "none required", backup: e.newest_hours == null ? "no copy" : `${Math.round(e.newest_hours)} h old, ${e.drives} other drive(s)` }[i.item] || e.reason || i.why_waived || ""; }

export const pages = { review: { title: "Review", draw: review }, cut: { title: "Cut", draw: cut }, release: { title: "Release", draw: release } };
