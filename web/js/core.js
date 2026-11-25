// Elements, requests, messages. Text is always inserted as text, never as HTML.
// append and replaceChildren take lists and skip what is not there, as h() does
for (const n of ["append", "replaceChildren"]) { const raw = Element.prototype[n]; Element.prototype[n] = function (...k) { return raw.apply(this, k.flat(Infinity).filter(x => x != null && x !== false)); }; }
export const $ = (s, r = document) => r.querySelector(s);
export function h(tag, props = {}, ...kids) {
  const [t, ...cls] = tag.split(".");
  const e = document.createElement(t || "div");
  if (cls.length) e.className = cls.join(" ");
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "cls") e.className += " " + v;
    else if (k.startsWith("aria") || k.startsWith("data") || k === "for" || k === "list" || k === "role" || k === "style") e.setAttribute(k, v === true ? "true" : v);
    else if (k in e) e[k] = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(Infinity)) if (c != null && c !== false) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}

export const state = { film: null, page: null, arg: null };
const withFilm = p => state.film ? p + (p.includes("?") ? "&" : "?") + "film=" + encodeURIComponent(state.film) : p;

export async function api(path, opt = {}) {
  const o = { method: opt.method || (opt.body !== undefined || opt.form ? "POST" : "GET"), headers: { "x-studio": "1" } };
  if (opt.body !== undefined) { o.body = JSON.stringify(opt.body); o.headers["content-type"] = "application/json"; }
  if (opt.form) o.body = opt.form;
  let r;
  try { r = await fetch("/api/" + withFilm(path), o); } catch { throw new Error("The studio does not answer. Is its window still open? Start it again with Start Studio.bat; nothing is lost."); }
  if (r.status === 401) throw new Error("This page belongs to an earlier start. Open the studio again with Start Studio.bat.");
  const j = (r.headers.get("content-type") || "").includes("json") ? await r.json().catch(() => ({})) : { text: await r.text() };
  if (!r.ok) throw new Error(j.error || "Something went wrong (" + r.status + ")");
  return j;
}
export const post = (p, b = {}) => api(p, { method: "POST", body: b });

export function toast(text, bad = false) {
  const t = h("div.toast", { cls: bad ? "bad" : "" }, text);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), bad ? 11000 : 4500);
}

// A question that names what will happen and what it costs. Resolves to the form's values, or null.
export function ask(title, body, { yes = "Continue", no = "Cancel", danger = false, fields = [] } = {}) {
  return new Promise(done => {
    const d = h("dialog", { "aria-labelledby": "dlg" }, h("h2", { id: "dlg" }, title), body,
      fields.length ? h("div.stack.gap", {}, fields.map(f => h("label.f", {}, f.label, f.el))) : null,
      h("div.row", {}, h("button.primary", { type: "button", cls: danger ? "danger" : "", onclick: () => { const v = Object.fromEntries(fields.map(f => [f.name, f.el.value])); d.close(); done(v); } }, yes), h("button", { type: "button", onclick: () => { d.close(); done(null); } }, no)));
    d.addEventListener("cancel", () => done(null));
    d.addEventListener("close", () => setTimeout(() => d.remove(), 50));
    document.body.append(d);
    d.showModal();
  });
}

let reload = () => { };
export const onReload = f => { reload = f; };
export const refresh = () => reload();

// A button that does something on the server. It is busy while it runs and says what happened.
export function act(label, fn, o = {}) {
  const b = h("button", { type: "button", disabled: o.disabled, title: o.title, cls: o.cls || "", "aria-label": o.aria }, label);
  b.addEventListener("click", async () => {
    if (o.confirm && !await ask(o.confirm, o.why ? h("p", {}, o.why) : null, { yes: label, danger: o.cls === "danger" })) return;
    b.disabled = true; b.classList.add("busy");
    try {
      const r = await fn(b);
      if (r === null) return;
      if (o.done) toast(typeof o.done === "function" ? o.done(r) : o.done);
      if (!o.stay) await refresh();
    } catch (e) { toast(e.message, true); if (o.always) await refresh(); }
    finally { b.disabled = !!o.disabled; b.classList.remove("busy"); }
  });
  return b;
}

// Anything that costs: look at the price, agree to it, send the agreed price along.
export function paid(label, priceOf, send, words, o = {}) {
  return act(label, async () => {
    const p = await priceOf();
    const ok = await ask(words.title, h("div.stack", {}, h("p", {}, words.what), h("p", {}, "At most ", h("b", {}, words.money(p)), ". One request, sent once. If the answer is lost it is not repeated."), words.extra ? h("p.dim.small", {}, words.extra) : null), { yes: `Agree to ${words.money(p)} and send` });
    if (!ok) return null;
    const r = await send(p);
    if (r.state && r.state !== "succeeded") throw new Error(r.state === "unknown" ? "The answer was lost. It may have been charged; see Home. Nothing is sent again by itself." : r.state === "refused" ? "The provider refused this request: " + (r.message || "") : "It did not work: " + (r.message || r.state));
    if (r.warning) toast(r.warning, true);
    return r;
  }, { cls: "primary", done: words.done || "Arrived and checked", always: true, ...o });
}

export const usd = m => "$" + (m / 1e6).toFixed(m < 100000 ? 3 : 2);
export const credits = n => Number(n).toLocaleString("en") + " credits";
export const when = t => { if (!t) return "never"; const d = new Date(t.replace(" ", "T") + "Z"); return isNaN(d) ? t : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }); };
export const secs = (f, fps = 24) => { const s = f / fps; return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`; };
export const media = id => "/media/" + id;
export const cap = s => s ? s[0].toUpperCase() + s.slice(1).replaceAll("_", " ") : "";
export const go = (page, arg) => { location.hash = page + (arg ? "/" + encodeURIComponent(arg) : ""); };
