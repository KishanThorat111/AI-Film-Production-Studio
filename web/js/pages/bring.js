// Bring in files: drop anything, the studio looks at it, the user says where it belongs.
import { h, api, post, act, go, when, cap, toast, refresh } from "../core.js";
import { tag, head, empty, table, field, cost, sourceFields, VOICE_KIND, SOUND_KIND } from "../ui.js";

const KIND = { image: "Picture", video: "Clip", audio: "Sound" };
const WHERE = { images: "Images", video: "Gemini video", voice: "Voices", sound: "Sound", library: "Library" };
const size = b => b > 1e6 ? (b / 1e6).toFixed(1) + " MB" : Math.ceil(b / 1e3) + " KB";

// Each file is sent on its own, so that one bad file does not hold up the rest.
export async function bringFiles(files, say) {
  let ok = 0;
  for (const [i, f] of [...files].entries()) {
    say(`${i + 1} of ${files.length}: looking at ${f.name}…`);
    const fd = new FormData(); fd.append("file", f);
    try { await api("bring", { form: fd }); ok++; } catch (e) { toast(`${f.name}: ${e.message}`, true); }
  }
  say("");
  return ok;
}

async function bring(m) {
  const d = await api("bring");
  m.append(head("Bring in files", "Pictures, Gemini clips, ElevenLabs takes, sound effects, ambience, music. Drop them all here, or on the page they belong to. Your own file stays where it is; the studio keeps an exact, read-only copy."));
  const st = h("p.small.dim", { role: "status" }), inp = h("input", { type: "file", multiple: true, accept: "image/*,video/*,audio/*,.mkv,.opus,.flac", "aria-label": "Files" });
  const run = async l => { if (!l.length) return; await bringFiles(l, t => { st.textContent = t; }); inp.value = ""; await refresh(); };
  inp.addEventListener("change", () => run(inp.files));
  m.append(h("div.drop.big", {}, h("div.row", {}, h("b", {}, "Choose files"), inp, h("span.small.faint", {}, "or drop them anywhere in this window")), st, h("p.small.dim", {}, "Looking at a file is local and free. A sound is listened to, a clip is compared with your approved frames, to suggest where it belongs.")));
  if (d.files.length) m.append(h("h2", {}, "Where do these belong?"), d.files.map(f => one(f, d)));
  else m.append(empty("No file is waiting to be placed."));
  m.append(h("details", { open: !d.files.length || undefined }, h("summary", {}, "What goes where"), table(["What you have", "Where it is listed", "What happens"], [
    ["Character, place and reference pictures, still frames, first and last frames", link("images"), "Picture check, face comparison. You accept it. Frames become part of a Gemini package, stills go into the edit."],
    ["Clips from the Gemini app", link("video"), "Every frame is checked and compared with the two frames you approved. You judge it on Review before you see the findings."],
    ["ElevenLabs takes", link("voice"), "Words are compared with the script, level and noise are measured. You accept one take per line; it goes into the edit by itself."],
    ["Sound effects, room tone, ambience, music, recordings", link("sound"), "Each needs its source and licence. You accept one per sound; it goes into the edit by itself."],
    ["Sounds for every film", link("library"), "Kept with their licence; admitted only when you have confirmed the terms."]])));
  m.append(h("h2", {}, "Where everything is kept"), h("div.card", {}, table(null, d.places.map(p => [p.title, h("span.mono.small", {}, p.path), act("Show folder", () => post("reveal", { place: p.id }), { cls: "quiet", stay: true })])), h("p.small.dim", {}, "Look, but do not move or rename anything there. The Backup page copies all of it.")));
}
const link = p => h("a", { href: "#" + p }, WHERE[p]);

function one(f, d) {
  const opt = x => [x.id, `${x.open ? "" : "(not open) "}${x.title}`];
  const best = f.to[0]?.score >= .35 ? f.to[0] : null, sel = field("to", "Where it belongs", { options: [["", "choose…"], ...f.to.map(opt)], value: best?.id });
  const box = h("div"), by = Object.fromEntries(f.to.map(x => [x.id, x]));
  let fields = [];
  const draw = () => {
    const x = by[sel.get()];
    fields = [];
    box.replaceChildren();
    if (!x) return;
    const kind = x.id.split(":")[0];
    if (["clip", "shotclip"].includes(kind)) fields.push(field("label", "Account label (only a note)", { size: 16 }), field("model", "Model shown in the app", { size: 18 }));
    if (kind === "line") { if (x.casting) fields.push(field("voice", "Voice used, exact name", { size: 22 }), field("voice_kind", "Kind of voice", { options: VOICE_KIND })); fields.push(field("credits", "Credits used", { size: 8 })); }
    if (kind === "sfx") fields.push(field("credits", "Credits used", { size: 8 }));
    if (kind === "library") fields.push(field("kind", "Kind of sound", { options: SOUND_KIND }));
    if (["cue", "library"].includes(kind)) fields.push(field("title", "What it is", { size: 24, value: f.name.replace(/\.[^.]+$/, "") }));
    if (kind === "image" || x.licence) fields.push(...sourceFields(d.terms, kind === "image" ? "own" : ""));
    if (x.reason) fields.push(field("reason", x.outside ? "How it was made, and why without a package" : "What was wrong with the previous one, and what changed", { size: 46 }));
    box.append(h("div.row.gap", {}, fields.map(q => q.el)), x.why ? h("p.small.dim", {}, "Suggested because ", x.why, ".") : null, x.open ? null : h("p.small", {}, tag("warn", "not open"), " This is approved or out of attempts. Open its page to ask for another version."));
  };
  sel.input.addEventListener("change", draw); draw();
  const show = f.kind === "image" ? h("img", { src: "/waiting/" + f.id, alt: f.name }) : f.kind === "video" ? h("video", { src: "/waiting/" + f.id, controls: true, preload: "metadata" }) : h("audio", { src: "/waiting/" + f.id, controls: true, preload: "metadata" });
  return h("section.card", { id: f.id }, h("div.head", {}, h("h3", {}, f.name), h("span.small.dim", {}, [KIND[f.kind], f.size, f.kind !== "image" && f.seconds ? f.seconds + " s" : null, size(f.bytes), when(f.at)].filter(Boolean).join(" · "))),
    h("div.two.bring", {}, h("div.waiting", {}, show), h("div", {}, sel.el, box,
      h("div.row.gap", {}, act("Bring it in", async () => {
        if (!sel.get()) throw new Error("Choose where it belongs");
        const r = await post(`bring/${f.id}/put`, { to: sel.get(), ...Object.fromEntries(fields.map(q => [q.name, q.get()])) });
        if (r.better_match) toast(`Look twice: this looks more like the clip for ${r.better_match}`, true);
        return r;
      }, { cls: "primary", done: () => "Stored and checked. It waits for your judgement.", busy: f.kind === "video" ? "Checking every frame…" : "Storing and checking…" }), act("Discard", () => post(`bring/${f.id}/discard`), { cls: "quiet", confirm: "Discard this file?", why: "Only the studio's waiting copy is removed. Your own file stays where it is." }), cost("local, free")),
      best && best.why ? null : h("p.small.faint", {}, "Nothing points to one place. Choose it yourself."))));
}

export const pages = { bring: { title: "Bring in files", draw: bring } };
