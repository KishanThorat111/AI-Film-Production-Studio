"""Local stand-ins for the providers. They let tests exercise every path, including failures, without a key or a cent."""
import base64
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import soundfile as sf
from PIL import Image

GOOD = "sk_test_good_key_0123456789"


def wav(seconds=2.6):
    t = np.arange(int(48000 * seconds)) / 48000
    b = io.BytesIO()
    sf.write(b, (.2 * np.sin(2 * np.pi * 200 * t) * np.exp(-t)).astype(np.float32), 48000, format="MP3")
    return b.getvalue()


def png(w=2752, h=1536, seed=1):
    r = np.random.default_rng(seed)
    a = np.asarray(Image.fromarray(r.integers(0, 200, (48, 86, 3), np.uint8)).resize((w, h), Image.BICUBIC))
    b = io.BytesIO()
    Image.fromarray(np.clip(a.astype(int) + r.integers(-6, 6, a.shape), 0, 255).astype(np.uint8)).save(b, "PNG")
    return b.getvalue()


class Fake:
    """behaviour: ok | fail500 | drop | refuse | quota | 429once | noperm"""

    def __init__(self):
        self.behaviour, self.calls, self.used, self.quota, self.gen, self.seed = "ok", [], 10000, 30000, 0, 1
        me = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def out(self, code, body, ctype="application/json", headers=None):
                b = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("content-type", ctype)
                self.send_header("content-length", str(len(b)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(b)

            def handle_any(self):
                n = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(n) if n else b""
                parts = self.path.split("?")[0].split("/")
                host, path = parts[1], "/" + "/".join(parts[2:])
                key = self.headers.get("xi-api-key") or self.headers.get("x-goog-api-key") or self.headers.get("x-key") or (self.headers.get("authorization") or "").replace("Bearer ", "")
                me.calls.append((self.command, host, path, body[:200]))
                if key != GOOD:
                    return self.out(400 if "google" in host else 401, {"detail": {"status": "invalid_api_key", "message": f"Invalid API key {key}"}})
                gen = self.command == "POST" and "get_result" not in path
                if gen:
                    me.gen += 1
                    b = me.behaviour
                    if b == "drop":
                        self.connection.close()
                        return
                    if b == "fail500":
                        return self.out(500, {"detail": {"message": "boom"}})
                    if b == "quota":
                        return self.out(402, {"detail": {"code": "insufficient_credits", "message": "not enough"}})
                    if b == "noperm":
                        return self.out(403, {"detail": {"code": "insufficient_permissions", "message": "no sfx"}})
                    if b == "429once":
                        me.behaviour = "ok"
                        return self.out(429, {"detail": {"code": "rate_limit_exceeded", "message": "slow"}})
                if host == "api.elevenlabs.io":
                    if path == "/v1/user/subscription":
                        return self.out(200, {"tier": "starter", "character_count": me.used, "character_limit": me.quota, "next_character_count_reset_unix": 1767000000, "status": "active", "currency": "inr", "can_extend_character_limit": False, "max_credit_limit_extension": 0, "can_use_instant_voice_cloning": True})
                    if path == "/v1/models":
                        return self.out(200, [{"model_id": "eleven_v4", "name": "Eleven v4", "can_do_text_to_speech": True, "requires_alpha_access": False, "maximum_text_length_per_request": 10000, "model_rates": {"character_cost_multiplier": 1}}])
                    if path == "/v2/voices":
                        return self.out(200, {"voices": [], "total_count": 4, "has_more": False})
                    cost = 104
                    if path == "/v1/sound-generation":
                        cost = int(40 * json.loads(body)["duration_seconds"])
                    if path.startswith("/v1/text-to-speech"):
                        cost = len(json.loads(body)["text"])
                    me.used += cost
                    return self.out(200, wav(), "audio/mpeg", {"character-cost": str(cost), "request-id": f"r{me.gen}"})
                if host == "generativelanguage.googleapis.com":
                    if path == "/v1beta/models":
                        return self.out(200, {"models": [{"name": "models/" + x, "supportedGenerationMethods": ["generateContent"]} for x in ("gemini-4-hydrogen", "gemini-nano-banana-2.1")]})
                    if path.endswith("/interactions"):
                        if me.behaviour == "refuse":
                            return self.out(200, {"id": "x", "steps": [{"type": "model_output", "content": [{"type": "text", "text": "I can't"}]}]})
                        me.seed += 1
                        return self.out(200, {"id": "x", "steps": [{"type": "thought", "content": [{"type": "image", "data": "A" * 2000}]}, {"type": "model_output", "content": [{"type": "image", "mime_type": "image/png", "data": base64.b64encode(png(seed=me.seed)).decode()}]}]})
                    if path.endswith(":generateContent"):
                        return self.out(200, {"responseId": "g1", "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps({"shots": 1, "what_happens": "a figure leans", "defects": [{"time": "00:02.1", "kind": "morph", "severity": "major", "description": "face changes"}], "cannot_tell": ["identity"]})}]}}], "usageMetadata": {"promptTokenCount": 30000, "candidatesTokenCount": 400, "thoughtsTokenCount": 600}})
                if host == "api.openai.com":
                    if path == "/v1/models":
                        return self.out(200, {"data": [{"id": "gpt-image-2.5-sunburst"}]})
                    return self.out(200, {"data": [{"b64_json": base64.b64encode(png(1536, 1024)).decode()}], "usage": {"output_tokens": 2000, "input_tokens_details": {"image_tokens": 500, "text_tokens": 200}}})
                if host == "api.bfl.ai" and path == "/v1/credits":
                    return self.out(200, {"credits": 500.0})
                return self.out(404, {"detail": "nope"})
            do_GET = do_POST = handle_any

        self.s = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.s.server_port}"
        threading.Thread(target=self.s.serve_forever, daemon=True).start()

    def stop(self):
        self.s.shutdown()
