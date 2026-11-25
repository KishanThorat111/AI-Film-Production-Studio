"""What every studio has, whatever the film: connectors (off), provider facts, spending ceilings, licence terms. No secret, no film, nothing enabled."""
from __future__ import annotations

from .db import DB, now

EXP = "2026-12-25"
AUTH = "Limits approved by the owner of this studio: images at most $10 in total, Gemini inspection at most $5, ElevenLabs included credits only"


def profile(db, pid, surface, kind, caps, url, connector=None):
    db.insert("provider_profile", id=pid, surface=surface, kind=kind, capabilities_json=caps, terms_json={}, source_url=url, verified_on="2026-11-25", expires_on=EXP, connector_id=connector)


TERMS = [
    # id, source, version, url, store, edit, sync, commercial, youtube, reuse, attribution, cid, ambiguity
    ("own", "Made in this studio (procedural sound, engine graphics, own recordings)", "1", None, "yes", "yes", "yes", "yes", "yes", "yes", 0, "low", None),
    ("elevenlabs_speech", "ElevenLabs speech, paid plan", "Terms 9 Oct 2026", "https://elevenlabs.io/terms-of-use", "yes", "yes", "yes", "yes", "yes", "yes", 0, "low", "Not for models labelled beta"),
    ("elevenlabs_sfx", "ElevenLabs sound effects", "Creative terms 9 Oct 2026, Exhibit 4", "https://elevenlabs.io/creative-terms", "unclear", "yes", "yes", "yes", "yes", "unclear", 0, "low", "Only combined with other material; not in any collection of sounds. Kept in this film only"),
    ("sonniss_gdc", "Sonniss GDC bundle", "2.0, 27 Aug 2026", "https://sonniss.com/gdc-bundle-license/", "yes", "yes", "yes", "yes", "yes", "yes", 0, "low", "No AI training; not redistributed as effects"),
    ("cc0", "Creative Commons CC0", "1.0", "https://creativecommons.org/publicdomain/zero/1.0/", "yes", "yes", "yes", "yes", "yes", "yes", 0, "possible", "Check each item's own page"),
    ("cc_by", "Creative Commons Attribution", "4.0", "https://creativecommons.org/licenses/by/4.0/", "yes", "yes", "yes", "yes", "yes", "yes", 1, "possible", "Credit, link and changes must be shown"),
    ("gemini_app", "Gemini app video", "Google terms read 25 Nov 2026", "https://policies.google.com/terms", "yes", "yes", "yes", "yes", "yes", "yes", 0, "low", "SynthID watermark; disclose AI use on YouTube"),
    ("google_images", "Google Gemini API images, paid tier", "API terms 23 Mar 2026", "https://aistudio.google.com/docs/terms", "yes", "yes", "yes", "yes", "yes", "yes", 0, "low", None),
    ("openai_images", "OpenAI API images", "Read 25 Nov 2026", "https://openai.com/policies/usage-policies", "yes", "yes", "yes", "yes", "yes", "yes", 0, "low", "Not for training models"),
    ("bfl_images", "Black Forest Labs API images", "Developer terms 4 Aug 2026", "https://bfl.ai/legal/developer-terms-of-service", "yes", "yes", "yes", "yes", "yes", "yes", 0, "low", "BFL may train on inputs and outputs; Content Credentials must not be removed"),
    ("elevenlabs_music", "ElevenLabs music (any self-serve plan)", "Music terms 9 Oct 2026", "https://elevenlabs.io/music-terms", "yes", "yes", "no", "yes", "unclear", "no", 0, "unknown", "Media rights exclude film"),
    ("windows_tts", "Windows built-in speech (temporary voice)", "system", None, "yes", "yes", "unclear", "unclear", "unclear", "no", 0, "unknown", "Temp voice for the animatic only"),
]


def base(db: DB):
    """Connectors, profiles, caps, terms. No secrets, nothing enabled, no mode chosen."""
    if db.one("select 1 from hard_cap"):
        return
    with db.tx():
        db.insert("hard_cap", category="image", limit_musd=10_000_000, authority=AUTH, created_at=now())
        db.insert("hard_cap", category="inspection", limit_musd=5_000_000, authority=AUTH, created_at=now())
        for cid, prov in (("elevenlabs", "elevenlabs"), ("gemini_inspect", "gemini"), ("google_images", "google"), ("openai_images", "openai"), ("bfl_images", "bfl")):
            db.insert("connector", id=cid, provider=prov, adapter=cid, adapter_version="p0")
        why = AUTH
        for cid, cap, pol, reason in (("elevenlabs", "tts", "allowed", "Approved as a capability; off until you enable the connector and choose API mode"), ("elevenlabs", "dialogue", "allowed", "As above"),
                                      ("elevenlabs", "sfx", "allowed", "Sound effects by API: included credits only, for the film they are made for only"),
                                      ("elevenlabs", "music", "blocked", "Music terms exclude film on every self-serve plan"), ("elevenlabs", "voices_read", "allowed", "Free read"), ("elevenlabs", "usage_read", "allowed", "Free read"),
                                      ("gemini_inspect", "inspect_video", "allowed", "You approved inspection only, at most $5"), ("gemini_inspect", "inspect_audio", "allowed", "You approved inspection only, at most $5"),
                                      *[(c, k, "allowed", "You approved images, at most $10 in total") for c in ("google_images", "openai_images", "bfl_images") for k in ("image_generate", "image_edit")]):
            db.insert("connector_capability", connector_id=cid, capability=cap, policy=pol, policy_reason=reason, set_by="user")
        profile(db, "gemini_app", "Gemini app (by hand)", "video_manual", {"images": 5, "seconds": [3, 10], "aspect": ["16:9", "9:16"], "first_last": "natural language; not listed in app help"}, "https://support.google.com/gemini/answer/16126339")
        profile(db, "voice_manual", "elevenlabs website (by hand)", "audio_manual", {"free_regenerations": "up to two within two hours for some models"}, "https://elevenlabs.io")
        profile(db, "sfx_manual", "elevenlabs website sound effects (by hand)", "audio_manual", {}, "https://elevenlabs.io")
        profile(db, "inspect_manual", "Gemini app second look (by hand)", "inspect_manual", {}, "https://support.google.com/gemini/answer/14903178")
        profile(db, "elevenlabs_api", "ElevenLabs API", "audio_api", {}, "https://elevenlabs.io/docs", "elevenlabs")
        profile(db, "gemini_inspect_api", "Gemini API inspection", "inspect_api", {"fps_max": 24}, "https://aistudio.google.com/docs/video-understanding", "gemini_inspect")
        profile(db, "google_images_api", "Google image API", "image_api", {}, "https://aistudio.google.com/docs/image-generation", "google_images")
        profile(db, "openai_images_api", "OpenAI image API", "image_api", {}, "https://developers.openai.com/api/docs/guides/image-generation", "openai_images")
        profile(db, "bfl_images_api", "BFL image API", "image_api", {}, "https://docs.bfl.ai", "bfl_images")
        for t in TERMS:
            db.insert("licence_terms", id=t[0], source=t[1], version=t[2], url=t[3], evidence_relpath="docs/LICENCES.md", checked_on="2026-11-25", r_store=t[4], r_edit=t[5], r_sync=t[6],
                      r_commercial=t[7], r_youtube=t[8], r_reuse_films=t[9], subscription_bound=0, attribution_required=t[10], content_id_risk=t[11], ambiguity=t[12], verified_by=None)
        db.event("system", "seed.base", "studio", "studio")
