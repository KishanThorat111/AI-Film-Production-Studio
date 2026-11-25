// Images, Gemini video, voices, sound effects.
import { h, api, post, act, ask, paid, toast, usd, credits, when, media, cap } from "../core.js";
import { tag, cost, facts, head, empty, table, field, copyBox, items, problems, reviews, checks, attemptsLine, judge, targetTools, approval, fileDrop, picture, player } from "../ui.js";

const withdraw = (a, t) => a.outcome === "pending" && !a.result_asset_id && (!a.rstate || a.rstate === "failed") ? h("p.small.dim", {}, "An attempt was begun but nothing was sent. ", act("Take it back", () => post(`attempt/${a.id}/withdraw`), { cls: "quiet" })) : null;

// ------------------------------------------------------------ images
async function images(m) {
  const [d, c, b] = await Promise.all([api("images"), api("connectors"), api("money")]);
  const left = b.cap_left.image, prov = c.connectors.filter(x => d.providers.includes(x.id));
  m.append(head("Images", `In order: each is built only from approved ones. ${usd(left)} of the approved total is left for all providers together.`));
  if (d.mode !== "api") m.append(h("div.card", {}, tag("info", "by hand"), " Generation is off for this film. Allow it on Home, or import pictures made elsewhere."));
  else if (!d.allowance) m.append(h("div.card", {}, tag("warn", "no allowance"), " This film may not spend yet. ", h("a", { href: "#budget" }, "Open an allowance")));
  if (!d.images.length) return m.append(empty("No image is planned yet.", h("a.btn", { href: "#plan/work" }, "Ask for an image plan")));
  for (const i of d.images) {
    const sel = field("p", "Provider", { options: prov.map(p => [p.id, p.title + (p.enabled ? "" : " (off)")]) }), why = field("reason", "What was wrong, and what changes", { size: 46 }), pr = field("t", "Prompt", { area: true, value: i.prompt, rows: 7 });
    const pend = i.attempts.some(a => a.outcome === "pending" && a.result_asset_id), open = i.target?.state !== "approved" && i.target?.state !== "stopped", retry = i.used > 0;
    const body = () => ({ provider: sel.get(), prompt: pr.get() === i.prompt.trim() ? "" : pr.get(), reason: why.get() });
    m.append(h("section.card", { id: i.id }, h("div.head", {}, h("h3", {}, i.id), h("span.small.dim", {}, [cap(i.kind), i.role, i.aspect].filter(Boolean).join(", "), i.for ? ` · ${i.for}` : ""), h("span.push.row", {}, i.approved ? tag("approved") : attemptsLine(i.target, i.limit, i.used), targetTools(i.target?.state === "approved" ? i.target : null))),
      i.waiting_for.length ? h("p.small", {}, tag("waiting", "waits"), " for ", i.waiting_for.join(", ")) : null,
      h("div.thumbs", {}, i.attempts.filter(a => a.result_asset_id).map(a => h("div.stack", {}, picture(a.result_asset_id, `${i.id}, attempt ${a.attempt_no}`, `${a.surface || "by hand"}${a.actual_amount != null ? " · " + usd(a.actual_amount) : a.est_amount ? " · up to " + usd(a.est_amount) : ""}${a.reason ? " · " + a.reason : ""}`), checks(a.checks, a.result_asset_id), reviews(i.reviews.filter(r => r.target_id === a.result_asset_id), { none: " " }), judge(a)))),
      i.attempts.map(a => a.withdrawn ? null : withdraw(a, i.target)), i.target?.state === "stopped" ? targetTools(i.target) : null,
      open && !i.waiting_for.length && !pend ? h("details", { open: retry || undefined }, h("summary", {}, retry ? "Prepare a corrected attempt" : "Make it"), pr.el, retry ? why.el : null,
        h("div.row.gap", {}, sel.el, paid("Generate one image", async () => (await post(`images/${encodeURIComponent(i.id)}/estimate`, body())).musd, p => post(`images/${encodeURIComponent(i.id)}/generate`, { ...body(), expect: p }), { title: `Generate ${i.id}`, what: `One picture from ${prov.find(p => p.id === sel.get())?.title || "the provider"}. The prompt and ${i.refs.length} reference pictures leave this computer.`, money: usd }, { disabled: d.mode !== "api" || !d.allowance }), cost("paid, inside the approved total")),
        fileDrop("Or import one made elsewhere", "image/*", fd => api(`images/${encodeURIComponent(i.id)}/import`, { form: fd }), retry ? [why] : [])) : null));
  }
}

// ------------------------------------------------------------ Gemini video, by hand
async function video(m) {
  const d = await api("video");
  m.append(head("Gemini video", "You generate in the Gemini app. The studio prepares two exact frames and one prompt, and checks what comes back. Account names are only labels you type."));
  if (!d.shots.length) return m.append(empty("No shot of this film is planned as a generation.", h("a.btn", { href: "#plan/work" }, "Ask for motion")));
  m.append(h("datalist", { id: "labels" }, d.labels.map(l => h("option", { value: l.label }))));
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
        const why = field("reason", "Why another take", { size: 30 }), cr = field("credits", "Credits used", { size: 10 }), vn = field("voice", "Voice used, exact name", { size: 22 }), vk = field("voice_kind", "Kind of voice", { options: [["", "choose…"], ["provider_permanent", "has the ∞ sign"], ["library", "library voice, no ∞"]] }), nt = field("note", "Note", { size: 16 });
        const takes = l.takes.filter(t => t.source !== "temp"), open = p.approved && !["stopped", "approved"].includes(l.target?.state);
        return h("div.card", { id: l.line_id }, h("div.head", {}, h("h3", {}, l.line_id, " · ", l.speaker), h("span.push.row", {}, attemptsLine(l.target, l.limit, takes.length), targetTools(l.target?.state === "approved" ? l.target : null))), h("p.small.dim", {}, "Script: ", l.text), copyBox(l.spoken_text, "Copy the text"), h("p", {}, l.delivery.intent), h("p.small.dim", {}, [l.delivery.pace && `Pace: ${l.delivery.pace}`, l.delivery.distance && `Distance: ${l.delivery.distance}`, l.delivery.avoid && `Avoid: ${l.delivery.avoid}`, l.file_name && `File: ${l.file_name}`, l.shot && `Shot ${l.shot}`, l.target_seconds && `${l.target_seconds[0]} to ${l.target_seconds[1]} s`].filter(Boolean).join(" · ")),
          takes.map(t => h("div.stack", {}, h("div.row", {}, h("b", {}, "Take ", t.take_no), tag("", t.source === "api" ? "API" : "by hand"), h("audio", { src: media(t.asset_id), controls: true, preload: "none" })), checks(t.checks), judge({ id: t.attempt_id, outcome: t.outcome }))), l.target?.state === "stopped" ? targetTools(l.target) : null,
          open ? fileDrop("Import this line", "audio/*", fd => api(`voice/${p.id}/${encodeURIComponent(l.line_id)}/import`, { form: fd }), [...(p.casting ? [vn, vk] : []), cr, ...(takes.length ? [why] : []), nt]) : null,
          open && d.mode === "api" ? paid("Generate this line", async () => (await post(`voice/${p.id}/${encodeURIComponent(l.line_id)}/estimate`, { voice_id: vid.get() || "x" })).credits, n => vid.get() ? post(`voice/${p.id}/${encodeURIComponent(l.line_id)}/generate`, { voice_id: vid.get(), reason: why.get(), expect: n }) : Promise.reject(new Error("Write the voice id above")), { title: "Generate this line", what: "One request to ElevenLabs with the text above.", money: credits, extra: "Included credits only. Nothing is bought." }) : null);
      })));
  }
}

// ------------------------------------------------------------ sound effects
async function sound(m) {
  const d = await api("sfx");
  m.append(head("Sound effects", d.mode === "api" ? "The API is allowed, with included credits you release on Budget. Every sound shows its price first." : "By hand, or allow the API on Home."));
  m.append(h("p.small.dim", {}, "ElevenLabs effects stay in this film. Their terms do not clearly allow a reusable collection, so the library refuses them."));
  if (!d.packages.length) return m.append(empty("No sound is planned yet.", h("a.btn", { href: "#plan/work" }, "Ask for voices and sounds")));
  for (const p of d.packages) {
    const why = field("reason", "What is wrong, and what changes", { size: 36 }), cr = field("credits", "Credits used", { size: 10 });
    const open = p.approved && !["stopped", "approved"].includes(p.target?.state), pend = p.attempts.some(a => a.outcome === "pending" && a.result_asset_id), done = p.attempts.filter(a => a.result_asset_id);
    m.append(h("section.card", { id: p.cue }, packHead(p, "sfx", p.doc.sound), h("p", {}, p.doc.purpose), h("p.small.dim", {}, `Library searched for ${p.doc.library_search.terms.join(", ")}: ${p.doc.library_search.hits} found. ${p.doc.library_search.why_not || ""}`), copyBox(p.doc.prompt, "Copy the prompt"), facts({ Duration: p.doc.duration_seconds + " s", "Prompt influence": p.doc.prompt_influence, Loop: p.doc.loop ? "on" : "off", File: p.doc.file_name }), h("p.small.dim", {}, "Accept if: ", p.doc.acceptance.join("; "), ". Avoid: ", p.doc.avoid),
      h("details", {}, h("summary", {}, "What to do on the website"), h("ol", {}, p.doc.manual_steps.map(x => h("li", {}, x)))),
      done.map(a => h("div.stack", {}, h("div.row", {}, h("b", {}, "Attempt ", a.attempt_no), tag("", a.origin === "api" ? "API" : "by hand"), a.actual_amount != null ? h("span.small.dim", {}, credits(a.actual_amount)) : null, h("audio", { src: media(a.result_asset_id), controls: true, preload: "none" })), checks(a.checks), judge(a))), targetTools(p.target),
      open && !pend ? h("div.gap", {}, done.length ? why.el : null, d.mode === "api" ? h("div.row.gap", {}, paid("Generate this sound", async () => (await api(`sfx/${p.id}/estimate`)).credits, n => post(`sfx/${p.id}/generate`, { reason: why.get(), expect: n }), { title: "Generate this sound", what: `One request to ElevenLabs: ${p.doc.sound}.`, money: credits, extra: "Included credits only. Nothing is bought. The sound stays in this film." }), cost("included credits")) : null, fileDrop("Import a sound made by hand", "audio/*", fd => api(`sfx/${p.id}/import`, { form: fd }), done.length ? [cr, why] : [cr])) : null));
  }
}

export const pages = { images: { title: "Images", draw: images }, video: { title: "Gemini video", draw: video }, voice: { title: "Voices", draw: voice }, sound: { title: "Sound effects", draw: sound } };
