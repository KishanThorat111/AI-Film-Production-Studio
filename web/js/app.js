// Shell: film chooser, navigation, activity, router.
import { $, h, api, post, act, ask, toast, state, onReload, go, when } from "./core.js";
import { tag, failure } from "./ui.js";
import * as film from "./pages/film.js";
import * as make from "./pages/make.js";
import * as finish from "./pages/finish.js";
import * as studio from "./pages/studio.js";
import * as bring from "./pages/bring.js";

const PAGES = { ...film.pages, ...make.pages, ...finish.pages, ...studio.pages, ...bring.pages };
const GROUPS = [["Film", ["home", "script", "plan", "shots"]], ["Make", ["bring", "images", "video", "voice", "sound"]], ["Finish", ["review", "cut", "release"]], ["Studio", ["agents", "connectors", "budget", "readiness", "backup", "library", "learning", "log"]]];
let token = 0, needs = [];

function nav() {
  const c = {}; for (const n of needs) (c[n.page] ||= []).push(n);
  $("#nav").replaceChildren(...GROUPS.map(([g, l]) => h("div", {}, h("h4", {}, g), l.map(p => h("a", { href: "#" + p, "aria-current": state.page === p ? "page" : null }, PAGES[p].title, c[p] ? h("span.count", { cls: c[p].some(x => x.trouble) ? "hot" : "", title: "waiting for you" }, c[p].length) : null)))));
}

const FILMLESS = ["connectors", "backup", "library", "log"];
function welcome() {
  return h("div", {}, h("h1", {}, "Welcome"), h("p.lead", {}, "There is no film in this studio yet. A film begins with its name and its script."), h("div.card", {}, h("ol", {}, h("li", {}, "Create the film."), h("li", {}, "Import the script (Word, Fountain, Final Draft or plain text). You see how it was read before anything is stored."), h("li", {}, "The Home page then always says what to do next.")), h("div.row.gap", {}, h("button.primary", { type: "button", onclick: async () => newFilm(await api("films")) }, "Create a film"))),
    h("p.small.dim", {}, "Moving from another computer or restoring a copy? Close the studio and follow “If something goes wrong” in the guide."));
}

async function stopStudio() {
  if (!await ask("Stop the studio?", h("p", {}, "Everything is saved. Work that is running goes on from its last finished part when you start again."), { yes: "Stop the studio" })) return;
  try {
    let r = await post("stop");
    if (r.running) { if (!await ask("Something is running", h("p", {}, `Running now: ${r.running.join(", ")}. It will be interrupted and can be continued later.`), { yes: "Stop anyway", danger: true })) return; r = await post("stop", { anyway: true }); }
    stopped = true;
    document.body.replaceChildren(h("div.stopped", {}, h("h1", {}, "The studio has stopped"), h("p", {}, "You can close this page. Double-click Start Studio.bat to start again.")));
  } catch (e) { toast(e.message, true); }
}

async function chooser() {
  const d = await api("films");
  state.film = state.film && d.films.some(f => f.id === state.film) ? state.film : d.current;
  const sel = h("select", { "aria-label": "Film", onchange: async () => { if (sel.value === "+") return newFilm(d); state.film = sel.value; await post(`films/${encodeURIComponent(sel.value)}/choose`); await route(); } }, d.films.map(f => h("option", { value: f.id, selected: f.id === state.film }, f.title + (f.is_benchmark ? " (benchmark)" : ""))), h("option", { value: "+" }, "New film…"));
  $("#filmbox").replaceChildren(d.films.length ? sel : h("span.small.faint", {}, "no film yet"));
  return d;
}

async function newFilm(d) {
  const f = { title: h("input", { size: 44, required: true }), logline: h("textarea", { rows: 2 }), format: h("select", {}, [["16:9", "16:9 widescreen"], ["21:9", "21:9 wide"], ["9:16", "9:16 vertical"]].map(x => h("option", { value: x[0] }, x[1]))), fps: h("select", {}, [24, 25, 30].map(x => h("option", {}, x))), universe: h("select", {}, h("option", { value: "" }, "a world of its own"), d.universes.map(u => h("option", { value: u.id }, "shares the world of " + u.title))) };
  const v = await ask("A new film", h("p.dim", {}, "Every film has its own script, canon, shots, edits and limits. Films that share a world share people and places."), { yes: "Create", fields: [["title", "Title"], ["logline", "One sentence about it"], ["format", "Picture"], ["fps", "Frames per second"], ["universe", "World"]].map(([name, label]) => ({ name, label, el: f[name] })) });
  if (v) { try { const r = await post("films", v); state.film = r.id; await chooser(); go("script"); await route(); } catch (e) { toast(e.message, true); } }
  else await chooser();
}

async function route() {
  const [p, a] = location.hash.slice(1).split("/"); const page = PAGES[p] ? p : "home", mine = ++token;
  const same = state.page === page; state.page = page; state.arg = a ? decodeURIComponent(a) : null;
  nav(); $("#crumb").textContent = PAGES[page].title; document.title = PAGES[page].title + " · Studio";
  const m = $("#main"), y = same ? scrollY : 0, box = h("div");
  if (!same) m.replaceChildren(h("div.skeleton"), h("div.skeleton"));
  try { if (!state.film && !FILMLESS.includes(page)) box.append(welcome()); else await PAGES[page].draw(box, state.arg); }
  catch (e) { box.replaceChildren(failure({ title: "This page could not be shown", what: e.message, safe: "Nothing was changed", you: "Try again; if it stays, look at the Log page", actions: [act("Try again", () => { }, {})] })); }
  if (mine !== token) return;
  m.replaceChildren(box);
  const aim = state.arg && (document.getElementById(state.arg) || m.querySelector(`[data-at="${CSS.escape(state.arg)}"]`));
  if (!same) { m.focus({ preventScroll: true }); scrollTo(0, 0); } else scrollTo(0, y);
  if (aim && (!same || aimed !== location.hash)) { for (let e = aim.parentElement; e; e = e.parentElement) if (e.matches("details")) e.open = true; aim.scrollIntoView({ block: "start" }); aim.classList.add("aim"); }
  aimed = location.hash;
  $("#shell").classList.remove("open"); $("#menu").setAttribute("aria-expanded", "false");
  last = "";
  pulse();
}

// What runs, what waits, what is wrong: small, always visible, never faked.
let last = "", running = false, stopped = false, aimed = "", silent = 0;
async function pulse() {
  if (running || stopped || !state.film) return; running = true;
  try {
    const [s, j] = await Promise.all([api("status"), api("jobs")]);
    needs = s.needs_you; nav();
    const live = j.jobs.filter(x => ["running", "queued"].includes(x.state));
    $("#activity").replaceChildren(...live.slice(0, 3).map(x => h("span", {}, tag(x.state, `${x.kind}${x.progress.total > 1 && x.state === "running" ? ` ${Math.round(100 * x.progress.done / x.progress.total)}%` : ""}`))), h("span.faint", {}, `${s.machine.ram_free_gb} GB free`));
    const b = []; silent = 0;
    for (const u of s.unknown) b.push(h("div.bad", {}, "A paid request has no answer and may have been charged.", h("a", { href: "#home" }, "Settle it")));
    for (const p of s.pauses) b.push(h("div", {}, `Paused: ${p.reason}.`, h("a", { href: "#home" }, "Decide")));
    if (s.backup.level === "bad") b.push(h("div.bad", {}, "There is no recent copy of your work.", h("a", { href: "#backup" }, "Make one")));
    $("#banner").replaceChildren(...b);
    const key = JSON.stringify([s.film?.id, j.jobs.map(x => [x.id, x.state]), s.needs_you.length]);
    if (last && key !== last && !document.querySelector("dialog[open]") && !typing()) { last = key; route(); }
    last = key;
  } catch { if (++silent > 1) $("#banner").replaceChildren(h("div.bad", {}, "The studio does not answer. Is its window still open? Double-click Start Studio.bat; nothing is lost.")); } finally { running = false; }
}
const typing = () => { const e = document.activeElement; return e && e.matches("input,textarea,select") && e.value !== "" || [...document.querySelectorAll("video,audio")].some(v => !v.paused) || [...document.querySelectorAll("main textarea,main input[type=text]")].some(x => x.value !== x.defaultValue) || [...document.querySelectorAll("main input[type=file]")].some(x => x.files.length); };

// A file dropped anywhere: never opened by the browser, always brought in.
addEventListener("dragover", e => { if (e.dataTransfer?.types.includes("Files")) e.preventDefault(); });
addEventListener("drop", async e => {
  if (!e.dataTransfer?.files.length) return;
  e.preventDefault();
  if (!state.film) return toast("Create a film first", true);
  const n = await bring.bringFiles(e.dataTransfer.files, t => { if (t) toast(t); });
  if (n) { if (state.page === "bring") route(); else go("bring"); }
});
$("#stop").addEventListener("click", stopStudio);
onReload(route);
addEventListener("hashchange", route);
$("#menu").addEventListener("click", () => { const o = $("#shell").classList.toggle("open"); $("#menu").setAttribute("aria-expanded", o); if (o) $("#nav a").focus(); });
addEventListener("keydown", e => { if (e.key === "Escape") $("#shell").classList.remove("open"); });
await chooser();
await route();
setInterval(() => { if (!document.hidden) pulse(); }, 4000);
