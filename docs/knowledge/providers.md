<!-- made by tools/knowledge.py from the code; do not edit -->
# Providers

Keys live in Windows Credential Manager only. Hosts are fixed, redirects refused. The Gemini app (video) is always used by hand; there is no video-generation capability.

| Connector | Unit | Capabilities |
| --- | --- | --- |
| ElevenLabs (voice and sound effects) | credits | Text to Speech; Text to Dialogue; Sound effects; Read voices; Read usage; Music (blocked: Music terms exclude film on every self-serve plan (checked 25 Nov 2026)) |
| Gemini API (inspection only) | musd | Inspect imported video; Inspect imported audio |
| Google images (Nano Banana) | musd | Generate image; Edit image |
| OpenAI images (GPT Image) | musd | Generate image; Edit image |
| Black Forest Labs (FLUX 3) | musd | Generate image; Edit image |

Free after sending only when the answer proves that no work was done: HTTP 400, 401, 402, 403, 404, 405, 406, 413, 415, 422, 429, 451. Everything else, including 5xx and an accepted job whose result is lost, is *unknown*: it stays reserved, is never sent again, and the user settles it. Only HTTP 429 is repeated by itself, at most three times.
