// Components shared by all pages.
import { $, h, api, post, act, ask, toast, refresh, usd, when, media, cap, secs } from "./core.js";

// One vocabulary of states. A colour always has a word.
const STATE = {
  good: ["pass", "ok", "ready", "manual_ready", "accepted", "APPROVED", "approved", "succeeded", "done", "met", "equal", "verified", "good", "locked", "handed_in", "generated clip", "generated still", "approved take", "approved effect", "library sound", "studio sound", "chosen file"],
  warn: ["warn", "unknown", "needs_correction", "NEEDS_CORRECTION", "UNCERTAIN", "NEEDS_INSPECTION", "major", "waived", "interrupted", "draft", "proposed", "insufficient", "different", "fixable", "cannot_determine", "returned", "3D blocking", "storyboard card", "temporary computer voice", "procedural placeholder", "not made yet", "not prepared yet", "missing", "a single frame, no motion yet"],
  bad: ["fail", "failed", "bad", "blocked", "denied", "error", "unreachable", "REJECTED", "rejected", "refused", "critical", "stopped", "not_met", "unavailable", "limit"],
  info: ["waiting", "pending", "running", "queued", "leased", "sent", "reserved", "info", "open", "moderate", "minor", "blind"],
};
const WORD = { manual_ready: "ready, by hand", not_required: "not needed", needs_correction: "needs correction", ok: "working", denied: "refused", insufficient: "not enough", no_result: "withdrawn", APPROVED: "approved", REJECTED: "rejected", NEEDS_CORRECTION: "needs correction", UNCERTAIN: "uncertain", NEEDS_INSPECTION: "needs inspection", NOT_APPLICABLE: "not applicable", leased: "being worked on", cannot_determine: "cannot be determined", not_met: "not met", handed_in: "handed in", nothing_handed_in: "ended without result", queued: "waiting its turn" };
export const tag = (v, text) => h("span.tag", { cls: Object.keys(STATE).find(k => STATE[k].includes(v)) || "" }, text || WORD[v] || String(v ?? "").replaceAll("_", " "));
export const cost = text => h("span.cost", {}, text);

export const facts = o => h("div.facts", {}, Object.entries(o).filter(([, v]) => v != null && v !== "").map(([k, v]) => h("div", {}, k, h("b", {}, v))));
export const head = (title, lead, ...right) => h("div", {}, h("div.head", {}, h("h1", {}, title), right.length ? h("div.row.push", {}, right) : null), lead ? h("p.lead", {}, lead) : null);
export const empty = (text, ...kids) => h("div.empty", {}, h("p", {}, text), kids.length ? h("div.row.gap", {}, kids) : null);
export const table = (cols, rows) => rows.length ? h("div.scroll", {}, h("table", {}, cols ? h("thead", {}, h("tr", {}, cols.map(c => h("th", { scope: "col", cls: c.startsWith("#") ? "num" : "" }, c.replace("#", ""))))) : null, h("tbody", {}, rows.map(r => h("tr", {}, r.map((c, i) => h("td", { cls: cols && cols[i]?.startsWith("#") ? "num" : "" }, c))))))) : null;
export const bar = (a, b, good) => h("div.bar", { cls: good ? "good" : "", role: "img", "aria-label": `${a} of ${b}` }, h("i", { style: `width:${b ? Math.min(100, 100 * a / b) : 0}%` }));
export function field(name, label, o = {}) {
  const el = o.options ? h("select", { name, "aria-label": label }, o.options.map(x => h("option", { value: x[0] ?? x, selected: (x[0] ?? x) === o.value }, x[1] ?? x))) : o.area ? h("textarea", { name, value: o.value || "", placeholder: o.placeholder || "", rows: o.rows || 4, "aria-label": label }) : h("input", { name, type: o.type || "text", size: o.size || 22, value: o.value ?? "", placeholder: o.placeholder || "", "aria-label": label, autocomplete: "off" });
  return { name, label, el: h("label.f", {}, label, el), input: el, get: () => el.value.trim() };
}
export function copyBox(text, label = "Copy") {
  return h("div", {}, h("div.copy", { tabIndex: 0 }, text), h("button.quiet", { type: "button", onclick: async () => { await navigator.clipboard.writeText(text); toast("Copied"); } }, label));
}
export function tabs(list, start, show) {
  const box = h("div"), bar_ = h("div.tabs", { role: "tablist" });
  const pick = k => { [...bar_.children].forEach(b => b.setAttribute("aria-selected", b.dataset.k === k)); box.replaceChildren(); show(k, box); };
  for (const [k, t] of list) bar_.append(h("button", { type: "button", role: "tab", "data-k": k, onclick: () => pick(k) }, t));
  pick(start && list.some(x => x[0] === start) ? start : list[0][0]);
  return [bar_, box];
}

// ------------------------------------------------------------ evidence
export const items = list => table(null, (list || []).map(i => [tag(i[1]), i[0], h("span.dim.small", {}, i[2] || "")]));
export const problems = l => l.length ? h("ul.plain", {}, l.map(p => h("li", {}, h("div.row", {}, tag(p.severity), h("b", {}, p.where), h("span", {}, p.text))))) : h("p.small.dim", {}, "The rules found nothing to object to.");
export function reviews(list, o = {}) {
  if (!list?.length) return h("p.small.dim", {}, o.none || "No independent review yet.");
  return list.map(r => h("details", { open: r.verdict !== "APPROVED" || undefined }, h("summary", {}, tag(r.verdict), " ", r.role, r.created_at ? h("span.faint.small", {}, " ", when(r.created_at)) : null),
    (r.findings || r.body?.findings || []).map(f => h("div.need", {}, tag(f.severity), h("div.grow", {}, h("div", {}, f.statement), h("div.small.dim", {}, f.reason, f.suggested_fix ? " Fix: " + f.suggested_fix : "")),
      f.id ? h("div.row", {}, f.human_verdict ? tag(f.human_verdict === "true" ? "good" : "warn", f.human_verdict === "true" ? "you agreed" : f.human_verdict === "false" ? "you disagreed" : "unsure") : null, act("True", () => post("finding/" + f.id, { verdict: "true" }), { cls: "quiet" }), act("Wrong", () => post("finding/" + f.id, { verdict: "false" }), { cls: "quiet" })) : null)),
    r.body?.limitations?.length ? h("p.small.faint", {}, "Not examined: ", r.body.limitations.join("; ")) : null));
}
export function checks(list, aid) {
  return (list || []).filter((c, i, a) => a.findIndex(x => x.check_name === c.check_name) === i).map(c => {
    const v = c.value;
    return h("details", { open: c.outcome === "fail" || undefined }, h("summary", {}, tag(c.outcome), " ", { voice: "Voice check", sound: "Sound check", image: "Picture check", video: "Clip check, every frame", gemini: "Second opinion by Gemini", render: "Delivery check" }[c.check_name] || c.check_name),
      items(v.items?.length ? v.items : v.technical?.items), v.word_differences?.length ? h("p.small", {}, "Heard differently: ", v.word_differences.map(d => `${d.script.join(" ") || "nothing"} → ${d.heard.join(" ") || "nothing"}`).join("; ")) : null,
      v.adherence ? facts({ "First frame": v.adherence.first?.text || v.adherence.first?.outcome, "Last frame": v.adherence.last?.text || v.adherence.last?.outcome }) : null,
      v.flags?.length ? table(["", "What", "Frames", ""], v.flags.map(f => [tag(f.severity), f.class, `${f.from} to ${f.to}`, h("span.small.dim", {}, f.text || "")])) : v.flags ? h("p.small.dim", {}, "No anomaly measured.") : null,
      v.usable?.length ? h("p.small", {}, "Usable: ", v.usable.map(r => `${r[0]}–${r[1]}`).join(", ")) : null, v.answer ? h("pre.copy", {}, JSON.stringify(v.answer, null, 1)) : null,
      v.views && aid ? h("details", {}, h("summary", {}, "Evidence pictures"), h("div.thumbs", {}, Object.keys(v.views).map(k => h("a", { href: `/view/${aid}/${k}`, target: "_blank" }, h("figure", {}, h("img", { src: `/view/${aid}/${k}`, loading: "lazy", alt: k }), h("figcaption", {}, k)))))) : null,
      v.not_examined?.length ? h("p.small.faint", {}, "Not examined: ", v.not_examined.join("; ")) : null);
  });
}

// ------------------------------------------------------------ attempts
export const attemptsLine = (t, limit, used) => h("span.small", {}, !t ? tag("", "not started") : t.state === "approved" ? tag("approved") : t.state === "stopped" ? tag("stopped", "all attempts used") : h("span.dim", {}, `${used} of ${limit} attempts used`));
export function judge(a, word = "look at it") {
  if (!a?.id || a.outcome !== "pending") return a?.outcome ? h("div.row", {}, tag(a.outcome)) : null;
  const note = field("note", "what is wrong, if anything", { size: 34 });
  const send = v => post(`attempt/${a.id}/judge`, { verdict: v, note: note.get() });
  return h("div.row.gap", {}, note.el, act("Accept", () => send("accepted"), { cls: "primary", done: "Accepted" }), act("Needs correction", () => note.get() ? send("needs_correction") : Promise.reject(new Error("Write what is wrong, so that the next attempt can change it"))), act("Reject", () => note.get() ? send("rejected") : Promise.reject(new Error("Write what is wrong"))));
}
export function targetTools(t) {
  if (!t) return null;
  if (t.state === "stopped") { const w = field("why", "why more attempts, and what will change", { size: 40 }); return h("div.card.hot", {}, h("p", {}, "All attempts are used. Nothing more is made unless you allow it."), h("div.row.gap", {}, w.el, act("Allow one more", () => post(`target/${t.id}/extend`, { extra: 1, reason: w.get() })))); }
  if (t.state === "approved") return act("Ask for another version", async () => { const v = await ask("Another version", h("p", {}, "The approved version stays in place until you accept a new one."), { yes: "Reopen", fields: [{ name: "reason", label: "What should be better", el: h("input", { size: 50 }) }] }); return v ? post(`target/${t.id}/reopen`, v) : null; }, { cls: "quiet" });
  return null;
}
export function approval(kind, id, rv) {
  const ok = rv.length && rv.at(-1).verdict === "APPROVED", why = field("reason", "why you accept it as it is", { size: 44 });
  return h("div.gap", {}, h("div.row", {}, act("Approve this package", () => post(`package/${kind}/${id}/approve`), { cls: "primary", done: "Approved" }), h("span.small.dim", {}, "Refused while a rule or the independent review blocks it.")),
    ok ? null : h("details", {}, h("summary", {}, "Accept it against the review, with a reason"), h("div.row", {}, why.el, act("Accept with this reason", () => post(`package/${kind}/${id}/approve`, { reason: why.get() })))));
}

// ------------------------------------------------------------ files
export function fileDrop(label, accept, send, fields = []) {
  const st = h("span.small.dim", { role: "status" }), inp = h("input", { type: "file", accept, "aria-label": label });
  const run = async f => {
    if (!f) return;
    st.textContent = `Importing and checking ${f.name}…`; box.classList.remove("over");
    const fd = new FormData(); fd.append("file", f); for (const x of fields) fd.append(x.name, x.get());
    try { await send(fd, f); toast("Imported and checked"); await refresh(); } catch (e) { st.textContent = e.message; toast(e.message, true); } finally { inp.value = ""; }
  };
  inp.addEventListener("change", () => run(inp.files[0]));
  const box = h("div.drop", {}, h("div.row", {}, h("b", {}, label), inp, h("span.small.faint", {}, "or drop a file here")), fields.length ? h("div.row.gap", {}, fields.map(f => f.el)) : null, st);
  box.addEventListener("dragover", e => { e.preventDefault(); box.classList.add("over"); });
  box.addEventListener("dragleave", () => box.classList.remove("over"));
  box.addEventListener("drop", e => { e.preventDefault(); run(e.dataTransfer.files[0]); });
  return box;
}
export const picture = (id, alt, capText) => h("figure", {}, h("a", { href: media(id), target: "_blank", title: "Open full size" }, h("img", { src: media(id), loading: "lazy", alt })), capText ? h("figcaption", {}, capText) : null);

// ------------------------------------------------------------ player with frame steps
export function player(src, fps = 24, o = {}) {
  const v = h("video", { src, preload: "metadata", loop: !!o.loop, playsInline: true }), time = h("span.time");
  const f = () => Math.floor(v.currentTime * fps + .01);
  const seek = n => { v.pause(); v.currentTime = Math.max(0, (n + .5) / fps); };
  const show = () => { time.textContent = `${secs(f(), fps)}  frame ${f()}`; o.onframe?.(f()); };
  v.addEventListener("timeupdate", show); v.addEventListener("seeked", show); v.addEventListener("loadedmetadata", show);
  let a = null, b = null;
  const mark = h("span.small.dim");
  const loop = () => { if (a != null && b != null && v.currentTime * fps > b) v.currentTime = a / fps; };
  v.addEventListener("timeupdate", loop);
  const btn = (t, l, fn) => h("button.quiet", { type: "button", title: l, "aria-label": l, onclick: fn }, t);
  const rate = h("select", { "aria-label": "speed", onchange: () => v.playbackRate = +rate.value }, [.25, .5, 1, 2].map(x => h("option", { value: x, selected: x === 1 }, x + "×")));
  const box = h("div.player", { tabIndex: 0, role: "group", "aria-label": "Player. Space plays, arrow keys step one frame, with Shift one second, A and B set a loop." }, v,
    h("div.pbar", {}, btn("▶", "Play or pause", () => v.paused ? v.play() : v.pause()), btn("⟨", "One frame back", () => seek(f() - 1)), btn("⟩", "One frame forward", () => seek(f() + 1)), time, rate, btn("A", "Start of loop", () => { a = f(); mark.textContent = `loop from ${a}`; }), btn("B", "End of loop", () => { b = f(); if (a == null) a = 0; mark.textContent = `loop ${a}–${b}`; }), btn("×", "No loop", () => { a = b = null; mark.textContent = ""; }), mark, h("span.push"), btn("⤢", "Full screen", () => v.requestFullscreen())));
  box.addEventListener("keydown", e => {
    const k = e.key; if (e.target.matches("input,textarea,select")) return;
    if (k === " ") { v.paused ? v.play() : v.pause(); } else if (k === "ArrowLeft") seek(f() - (e.shiftKey ? fps : 1)); else if (k === "ArrowRight") seek(f() + (e.shiftKey ? fps : 1)); else if (k === "a") a = f(); else if (k === "b") { b = f(); a ??= 0; } else return;
    e.preventDefault();
  });
  return { el: box, video: v, seek, frame: f };
}

// ------------------------------------------------------------ a failure answers seven questions
export function failure(o) {
  const rows = [["What", o.what], ["Where", o.where], ["State", o.state], ["Money", { no: "Nothing was charged", unknown: "It may have been charged. Look at your account.", possibly: "It may have been charged" }[o.money] || o.money], ["Safe", o.safe], ["You can", o.you], ["Evidence", o.ref]].filter(r => r[1]);
  return h("div.fail", { role: "group", "aria-label": "Problem" }, h("h3", {}, o.title), h("dl", {}, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])), o.actions ? h("div.row", {}, o.actions) : null);
}
export const jobRow = j => h("div.need", {}, tag(j.state), h("div.grow", {}, h("b", {}, cap(j.kind)), h("div.small.dim", {}, j.error || j.progress?.text || (j.ended_at ? when(j.ended_at) : "")), j.state === "running" && j.progress?.total > 1 ? bar(j.progress.done, j.progress.total) : null),
  h("div.row", {}, ["queued", "running"].includes(j.state) ? act("Cancel", () => post(`jobs/${j.id}/cancel`), { cls: "quiet" }) : null, ["interrupted", "failed", "cancelled"].includes(j.state) ? act(j.state === "interrupted" ? "Continue" : "Try again", () => post(`jobs/${j.id}/resume`), { cls: "quiet" }) : null));
export { $, usd };
