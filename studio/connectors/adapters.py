"""Provider adapters. Each knows: how to test for free, what a request costs at most, how to send it, how to read the reply.

There is deliberately no method that generates video. Costs are upper bounds used for reservation; the real cost is read
from the provider's reply. If an upper bound cannot be stated, estimate() raises CostUnknown and nothing is sent.
Money is in micro-USD (1 USD = 1,000,000); ElevenLabs is in credits.
"""
from __future__ import annotations

import base64
import json
import math
import time
from dataclasses import dataclass, field

from .http import NetError, request


class CostUnknown(Exception):
    pass


@dataclass
class Result:
    data: bytes | None = None
    mime: str | None = None
    text: str | None = None
    actual: int | None = None          # in the connector's unit
    job_id: str | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class Check:
    kind: str
    outcome: str                       # ok denied insufficient unreachable error
    capability: str | None = None
    detail: dict = field(default_factory=dict)


def outcome_of(e: NetError) -> str:
    if e.kind != "http":
        return "unreachable"
    if e.status in (401, 403):
        return "denied"
    if e.status == 402 or e.code in ("insufficient_credits", "quota_exceeded"):
        return "insufficient"
    return "error"


class Adapter:
    id = ""
    provider = ""
    title = ""
    unit = "musd"
    key_help = ""
    config_fields: list[dict] = []
    capabilities: dict[str, dict] = {}

    def __init__(self, key: str, config: dict | None = None):
        self.key, self.config = key, config or {}

    def checks(self) -> tuple[list[Check], dict | None]:
        raise NotImplementedError

    def estimate(self, capability: str, spec: dict) -> int:
        raise NotImplementedError

    on_job = staticmethod(lambda job_id: None)      # the broker records a job number the moment it is known

    def send(self, capability: str, spec: dict, refs: list[tuple[bytes, str]]) -> Result:
        raise NotImplementedError

    def reconcile(self, request_row: dict) -> Result | None:
        """After a lost reply: look for the finished work. None = cannot tell."""
        return None


# ------------------------------------------------------------------ ElevenLabs
class ElevenLabs(Adapter):
    id, provider, title, unit = "elevenlabs", "elevenlabs", "ElevenLabs (voice and sound effects)", "credits"
    key_help = ("Create a restricted key: Text to Speech, Sound Effects, Models (read), Voices (read), User (read), History (read). "
                "Leave Music, voice cloning, sharing and workspace permissions off. Set a monthly credit limit on the key.")
    capabilities = {
        "tts": {"title": "Text to Speech", "generates": True},
        "dialogue": {"title": "Text to Dialogue", "generates": True},
        "sfx": {"title": "Sound effects", "generates": True},
        "voices_read": {"title": "Read voices", "generates": False},
        "usage_read": {"title": "Read usage", "generates": False},
        "music": {"title": "Music", "generates": True, "forced_block": "Music terms exclude film on every self-serve plan (checked 25 Nov 2026)"},
    }
    B = "https://api.elevenlabs.io"

    def _get(self, path, **kw):
        return request("GET", self.B + path, key_header=("xi-api-key", self.key), timeout=30, **kw).json()

    def checks(self):
        out, usage = [], None
        try:
            s = self._get("/v1/user/subscription")
            out += [Check("availability", "ok"), Check("auth", "ok")]
            usage = {"tier": s.get("tier"), "unit": "credits", "used": int(s["character_count"]), "quota": int(s["character_limit"]),
                     "resets_on": s.get("next_character_count_reset_unix"),
                     "raw": {k: s.get(k) for k in ("tier", "status", "character_count", "character_limit", "next_character_count_reset_unix", "currency", "billing_period",
                                                   "can_extend_character_limit", "max_credit_limit_extension", "can_use_instant_voice_cloning", "voice_limit", "voice_slots_used")}}
            paid = s.get("tier") not in (None, "free")
            over = s.get("max_credit_limit_extension")
            out.append(Check("entitlement", "ok" if paid else "insufficient", None, {
                "tier": s.get("tier"), "commercial_plan": paid, "overage_possible": bool(s.get("can_extend_character_limit")) and over not in (0, None),
                "instant_cloning": s.get("can_use_instant_voice_cloning")}))
            out.append(Check("capability", "ok", "usage_read"))
        except NetError as e:
            o = outcome_of(e)
            out += [Check("availability", "unreachable" if o == "unreachable" else "ok", None, {"message": str(e)}), Check("auth", o, None, {"message": str(e), "code": e.code})]
            return out, None
        models = []
        try:
            models = self._get("/v1/models")
            tts = [{"id": m["model_id"], "name": m.get("name"), "max_chars": m.get("maximum_text_length_per_request"), "alpha": bool(m.get("requires_alpha_access")),
                    "multiplier": (m.get("model_rates") or {}).get("character_cost_multiplier"), "discount": (m.get("model_rates") or {}).get("cost_discount_multiplier")}
                   for m in models if m.get("can_do_text_to_speech")]
            usable = [m for m in tts if not m["alpha"]]
            out.append(Check("capability", "ok" if usable else "denied", "tts", {"models": tts, "proven": "model list only; output formats are proven at first use"}))
            out.append(Check("capability", "ok" if any(m["id"] in ("eleven_v4", "eleven_v3") for m in usable) else "denied", "dialogue", {"proven": "model list only"}))
        except NetError as e:
            out.append(Check("capability", outcome_of(e), "tts", {"message": str(e)}))
        try:
            v = self._get("/v2/voices", params={"page_size": 1})
            out.append(Check("capability", "ok", "voices_read", {"total": v.get("total_count")}))
        except NetError as e:
            out.append(Check("capability", outcome_of(e), "voices_read", {"message": str(e)}))
        # The service offers no free way to prove sound-effect permission; a paid plan plus a valid key is the best free evidence.
        out.append(Check("capability", "ok" if usage and usage["tier"] not in (None, "free") else "denied", "sfx", {"proven": "plan and key only; permission is proven at first use"}))
        return out, usage

    def rate(self) -> float:
        """Credits per character. 1.0 until the account's own model list says otherwise (see the owner's notes 06, section 4)."""
        return 1.0

    def estimate(self, capability, spec):
        if capability == "sfx":
            d = spec.get("duration_seconds")
            if not d:
                raise CostUnknown("set the duration: without it the provider chooses and the cost is not known in advance")
            return max(200, math.ceil(40 * float(d)))      # documented 40 credits per second; 200 is the documented undirected price
        if capability == "tts":
            return max(1, math.ceil(len(spec["text"]) * self.rate()))
        if capability == "dialogue":
            return max(1, math.ceil(sum(len(i["text"]) for i in spec["inputs"]) * self.rate()))
        raise CostUnknown(capability)

    def send(self, capability, spec, refs):
        fmt = spec.get("output_format", "mp3_44100_128")
        if capability == "sfx":
            r = request("POST", self.B + "/v1/sound-generation", key_header=("xi-api-key", self.key), params={"output_format": fmt}, timeout=120,
                        json={"text": spec["text"], "duration_seconds": spec["duration_seconds"], "prompt_influence": spec.get("prompt_influence", 0.3),
                              "loop": bool(spec.get("loop", False)), "model_id": spec.get("model_id", "eleven_text_to_sound_v3")})
        elif capability == "tts":
            body = {"text": spec["text"], "model_id": spec["model_id"], "voice_settings": spec.get("voice_settings", {})}
            for k in ("seed", "previous_text", "next_text", "language_code"):
                if spec.get(k) is not None:
                    body[k] = spec[k]
            r = request("POST", f"{self.B}/v1/text-to-speech/{spec['voice_id']}", key_header=("xi-api-key", self.key), params={"output_format": fmt}, json=body, timeout=180)
        elif capability == "dialogue":
            r = request("POST", self.B + "/v1/text-to-dialogue", key_header=("xi-api-key", self.key), params={"output_format": fmt}, timeout=180,
                        json={"inputs": spec["inputs"], "model_id": spec["model_id"], "settings": spec.get("settings", {}), "seed": spec.get("seed")})
        else:
            raise NetError("before_send", "unsupported")
        cost = r.headers.get("character-cost")
        return Result(data=r.content, mime=r.headers.get("content-type", "audio/mpeg"), actual=int(cost) if cost and cost.isdigit() else None,
                      job_id=r.headers.get("request-id"), meta={"output_format": fmt, "cost_header": cost})


# ------------------------------------------------------------------ Gemini, inspection only
INSPECT_MODELS = {   # USD per 1M tokens (input, output incl. thinking); standard tier, read 25 Nov 2026
    "gemini-4-hydrogen": (1.50, 7.50),
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
}


class GeminiInspect(Adapter):
    id, provider, title, unit = "gemini_inspect", "gemini", "Gemini API (inspection only)", "musd"
    key_help = ("Use a key from a Google Cloud project with billing switched on. On the free tier Google may use your clips to improve its products. "
                "This connector can only read clips and report; it has no way to generate video.")
    config_fields = [{"name": "model", "label": "Inspection model", "options": list(INSPECT_MODELS), "default": "gemini-4-hydrogen"},
                     {"name": "billing_confirmed", "label": "I confirm this key's project has billing enabled (paid tier)", "type": "bool", "default": False}]
    capabilities = {"inspect_video": {"title": "Inspect imported video", "generates": True}, "inspect_audio": {"title": "Inspect imported audio", "generates": True}}
    B = "https://generativelanguage.googleapis.com/v1beta"
    MAX_OUT = 4096

    @property
    def model(self):
        return self.config.get("model") or "gemini-4-hydrogen"

    def checks(self):
        out = []
        try:
            j = request("GET", self.B + "/models", key_header=("x-goog-api-key", self.key), params={"pageSize": 1000}, timeout=30).json()
        except NetError as e:
            o = outcome_of(e)
            if e.status == 400:      # Google answers 400 for a malformed or invalid key
                o = "denied"
            return [Check("availability", "unreachable" if o == "unreachable" else "ok", None, {"message": str(e)}), Check("auth", o, None, {"message": str(e)})], None
        names = {m["name"].split("/")[-1]: m for m in j.get("models", [])}
        out += [Check("availability", "ok"), Check("auth", "ok")]
        m = names.get(self.model)
        can = bool(m) and "generateContent" in (m.get("supportedGenerationMethods") or [])
        paid = bool(self.config.get("billing_confirmed"))
        out.append(Check("entitlement", "ok" if paid else "insufficient", None, {"billing_confirmed_by_user": paid}))
        for cap in self.capabilities:
            out.append(Check("capability", "ok" if can and paid else ("insufficient" if can else "denied"), cap, {
                "model": self.model, "listed": bool(m), "available": sorted(n for n in names if n in INSPECT_MODELS),
                "why": None if can and paid else ("confirm paid billing in the settings" if can else "this key cannot use the chosen model")}))
        return out, None

    def _tokens(self, spec):
        s = float(spec["seconds"])
        if spec["kind"] == "video":
            t = math.ceil(s * float(spec["fps"])) * 263 + s * 64
        else:
            t = s * 64
        return t + 1000 * len(spec.get("images", [])) * 3 + len(spec["prompt"]) / 2 + 800

    def estimate(self, capability, spec):
        if self.model not in INSPECT_MODELS:
            raise CostUnknown(f"no verified price for {self.model}")
        pin, pout = INSPECT_MODELS[self.model]
        return math.ceil(self._tokens(spec) * 1.25 * pin + 3 * self.MAX_OUT * pout)     # deliberately above what should happen

    def send(self, capability, spec, refs):
        parts = []
        for i, (b, mime) in enumerate(refs):
            p = {"inlineData": {"mimeType": mime, "data": base64.b64encode(b).decode()}}
            if mime.startswith("video/"):
                p["videoMetadata"] = {"fps": float(spec["fps"])}
            parts.append(p)
        parts.append({"text": spec["prompt"]})
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"maxOutputTokens": self.MAX_OUT, "responseMimeType": "application/json"}}
        r = request("POST", f"{self.B}/models/{self.model}:generateContent", key_header=("x-goog-api-key", self.key), json=body, timeout=240).json()
        u = r.get("usageMetadata", {})
        pin, pout = INSPECT_MODELS[self.model]
        out_tok = (u.get("candidatesTokenCount") or 0) + (u.get("thoughtsTokenCount") or 0)
        actual = math.ceil((u.get("promptTokenCount") or 0) * pin + out_tok * pout) if u.get("promptTokenCount") is not None else None
        c = (r.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in (c.get("content") or {}).get("parts", []) if not p.get("thought"))
        return Result(text=text, mime="application/json", actual=actual, job_id=r.get("responseId"), meta={"usage": u, "finish": c.get("finishReason"), "model": self.model})


# ------------------------------------------------------------------ images
class GoogleImages(Adapter):
    id, provider, title = "google_images", "google", "Google images (Nano Banana)"
    key_help = "A Gemini API key from a project with billing. It may be the same key as the inspection connector, entered again."
    capabilities = {"image_generate": {"title": "Generate image", "generates": True}, "image_edit": {"title": "Edit image", "generates": True}}
    B = "https://generativelanguage.googleapis.com/v1beta"
    MODEL = "gemini-nano-banana-2.1"
    PRICE = {"1K": 33_600, "2K": 50_400}

    def checks(self):
        try:
            j = request("GET", self.B + "/models", key_header=("x-goog-api-key", self.key), params={"pageSize": 1000}, timeout=30).json()
        except NetError as e:
            o = "denied" if e.status == 400 else outcome_of(e)
            return [Check("availability", "unreachable" if o == "unreachable" else "ok", None, {"message": str(e)}), Check("auth", o, None, {"message": str(e)})], None
        have = any(m["name"].endswith("/" + self.MODEL) for m in j.get("models", []))
        return [Check("availability", "ok"), Check("auth", "ok")] + [Check("capability", "ok" if have else "denied", c, {"model": self.MODEL, "listed": have}) for c in self.capabilities], None

    def estimate(self, capability, spec):
        size = spec.get("size", "2K")
        if size not in self.PRICE:
            raise CostUnknown(size)
        return self.PRICE[size] + 4_000 * len(spec.get("reference_ids", [])) + 25_000     # references as tiles, plus thinking tokens

    def send(self, capability, spec, refs):
        inp = [{"type": "image", "mime_type": m, "data": base64.b64encode(b).decode()} for b, m in refs] + [{"type": "text", "text": spec["prompt"]}]
        body = {"model": self.MODEL, "input": inp, "response_format": {"type": "image", "mime_type": "image/jpeg", "aspect_ratio": spec.get("aspect", "16:9"), "image_size": spec.get("size", "2K")}}
        r = request("POST", self.B + "/interactions", key_header=("x-goog-api-key", self.key), headers={"Api-Revision": "2026-05-20"}, json=body, timeout=240).json()
        img = _find_image(r)
        u = r.get("usage") or {}
        if not img:
            raise NetError("http", "the provider answered without an image (possibly a content refusal)", 200, "refused")
        return Result(data=base64.b64decode(img[0]), mime=img[1], actual=None, job_id=r.get("id"), meta={"usage": u})


def _find_image(o):
    """The reply's exact nesting is not documented; find the last non-thought image block."""
    found = None
    if isinstance(o, dict):
        if isinstance(o.get("data"), str) and len(o["data"]) > 1000 and str(o.get("mime_type") or o.get("mimeType") or "image/").startswith("image/"):
            return o["data"], o.get("mime_type") or o.get("mimeType") or "image/jpeg"
        vals = [v for k, v in o.items() if k not in ("input", "thought")]
    elif isinstance(o, list):
        vals = [v for v in o if not (isinstance(v, dict) and v.get("type") == "thought")]
    else:
        return None
    for v in vals:
        found = _find_image(v) or found
    return found


class OpenAIImages(Adapter):
    id, provider, title = "openai_images", "openai", "OpenAI images (GPT Image)"
    key_help = "A project key restricted to Images. Set a project budget. The organisation may need verification for image models."
    capabilities = {"image_generate": {"title": "Generate image", "generates": True}, "image_edit": {"title": "Edit image", "generates": True}}
    config_fields = [{"name": "model", "label": "Model", "options": ["gpt-image-2.5-sunburst", "gpt-image-2.6-flare", "gpt-image-2"], "default": "gpt-image-2.5-sunburst"}]
    B = "https://api.openai.com/v1"
    # No official per-image price for 2.5/2.6 was found. Upper bound: above the highest published figure for this size at 'max' ($0.2107, third party).
    BOUND = {"low": 60_000, "medium": 150_000, "high": 250_000}

    @property
    def model(self):
        return self.config.get("model") or "gpt-image-2.5-sunburst"

    def checks(self):
        try:
            j = request("GET", self.B + "/models", key_header=("Authorization", "Bearer " + self.key), timeout=30).json()
        except NetError as e:
            o = outcome_of(e)
            return [Check("availability", "unreachable" if o == "unreachable" else "ok", None, {"message": str(e)}), Check("auth", o, None, {"message": str(e)})], None
        have = any(m["id"] == self.model for m in j.get("data", []))
        return [Check("availability", "ok"), Check("auth", "ok")] + [Check("capability", "ok" if have else "denied", c, {"model": self.model, "listed": have}) for c in self.capabilities], None

    def estimate(self, capability, spec):
        q = spec.get("quality", "medium")
        if q not in self.BOUND:
            raise CostUnknown(q)
        return self.BOUND[q] + 12_000 * len(spec.get("reference_ids", []))

    def send(self, capability, spec, refs):
        h = ("Authorization", "Bearer " + self.key)
        size = spec.get("pixels", "1536x1024")
        if refs:
            files = [("image[]", (f"ref{i}.png", b, m)) for i, (b, m) in enumerate(refs)]
            r = request("POST", self.B + "/images/edits", key_header=h, files=files, data={"model": self.model, "prompt": spec["prompt"], "size": size, "quality": spec.get("quality", "medium"), "n": "1"}, timeout=300).json()
        else:
            r = request("POST", self.B + "/images/generations", key_header=h, json={"model": self.model, "prompt": spec["prompt"], "size": size, "quality": spec.get("quality", "medium"), "n": 1}, timeout=300).json()
        u = r.get("usage") or {}
        d = u.get("input_tokens_details") or {}
        actual = math.ceil((u.get("output_tokens") or 0) * 30 + (d.get("image_tokens") or 0) * 8 + (d.get("text_tokens") or 0) * 5) if u.get("output_tokens") else None
        return Result(data=base64.b64decode(r["data"][0]["b64_json"]), mime="image/png", actual=actual, meta={"usage": u, "model": self.model})


class BFLImages(Adapter):
    id, provider, title = "bfl_images", "bfl", "Black Forest Labs (FLUX 3)"
    key_help = "Prepaid credits. Note: BFL's API terms allow it to train on what you send and receive."
    capabilities = {"image_generate": {"title": "Generate image", "generates": True}, "image_edit": {"title": "Edit image", "generates": True}}
    config_fields = [{"name": "model", "label": "Model", "options": ["flux-3-image-klein", "flux-3-image"], "default": "flux-3-image-klein"}]
    B = "https://api.bfl.ai/v1"
    PRICE = {"flux-3-image": {"1k": 48_000, "1.5k": 70_000, "2k": 100_000}, "flux-3-image-klein": {"1k": 9_000, "1.5k": 11_000, "2k": 14_000}}

    @property
    def model(self):
        return self.config.get("model") or "flux-3-image-klein"

    def checks(self):
        try:
            j = request("GET", self.B + "/credits", key_header=("x-key", self.key), timeout=30).json()
        except NetError as e:
            o = "denied" if e.status == 422 else outcome_of(e)        # BFL answers 422 for a malformed key
            return [Check("availability", "unreachable" if o == "unreachable" else "ok", None, {"message": str(e)}), Check("auth", o, None, {"message": str(e)})], None
        c = float(j.get("credits") or 0)
        o = "ok" if c >= 10 else "insufficient"
        return [Check("availability", "ok"), Check("auth", "ok"), Check("entitlement", o, None, {"credits": c, "usd": c / 100})] + \
               [Check("capability", o, k, {"credits": c}) for k in self.capabilities], {"tier": "prepaid", "unit": "bfl_credits", "used": 0, "quota": int(c), "resets_on": None, "raw": j}

    def estimate(self, capability, spec):
        try:
            return self.PRICE[self.model][spec.get("size", "2k")]
        except KeyError:
            raise CostUnknown(f"{self.model} {spec.get('size')}") from None

    def send(self, capability, spec, refs):
        body = {"prompt": spec["prompt"], "resolution": spec.get("size", "2k"), "aspect_ratio": spec.get("aspect", "16:9")}
        if refs:
            body["images"] = [base64.b64encode(b).decode() for b, _ in refs]
        if self.model == "flux-3-image":
            body["grounding"] = False
        else:
            body["output_format"] = "png"
            if spec.get("seed") is not None:
                body["seed"] = spec["seed"]
        j = request("POST", f"{self.B}/{self.model}", key_header=("x-key", self.key), json=body, timeout=60).json()
        self.on_job(str(j["id"]))
        try:
            return self._poll(j["id"], j.get("polling_url"), j.get("cost"))
        except NetError as e:                        # the job was accepted: from here nothing is known to be free
            cost = math.ceil(float(j["cost"]) * 10_000) if j.get("cost") is not None else None
            if e.code == "refused":
                e.actual = cost if e.actual is None else e.actual
                raise
            raise NetError("after_send", f"the job was accepted (number {j['id']}), but its result is not known: {e}") from None

    def _poll(self, job, url, cost):
        url = url or f"{self.B}/get_result?id={job}"
        end = time.time() + 180
        while time.time() < end:
            try:
                r = request("GET", url, key_header=("x-key", self.key), timeout=30).json()
            except NetError as e:
                if e.kind == "http" and e.status not in (429, 500, 502, 503):
                    raise
                time.sleep(2)
                continue
            st = r.get("status")
            if st == "Ready":
                img = request("GET", r["result"]["sample"], timeout=60, download=True)
                c = r.get("cost", cost)
                return Result(data=img.content, mime=img.headers.get("content-type", "image/png"), actual=math.ceil(float(c) * 10_000) if c is not None else None, job_id=job, meta={"cost_credits": c})
            if st in ("Request Moderated", "Content Moderated"):
                raise NetError("http", f"refused by the provider: {st}", 200, "refused")
            if st in ("Error", "Failed", "Task not found"):
                raise NetError("http", f"provider could not finish: {st}", 200, "failed")
            time.sleep(1)
        raise NetError("after_send", "no result within three minutes")

    def reconcile(self, row):
        if row.get("provider_job_id"):
            return self._poll(row["provider_job_id"], None, None)
        return None


REGISTRY: dict[str, type[Adapter]] = {a.id: a for a in (ElevenLabs, GeminiInspect, GoogleImages, OpenAIImages, BFLImages)}


def parse_json(text):
    try:
        return json.loads(text)
    except Exception:
        s, e = text.find("{"), text.rfind("}")
        try:
            return json.loads(text[s:e + 1])
        except Exception:
            return None
