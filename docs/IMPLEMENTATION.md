# Implementation record

For the next session. What exists, what is proven, what is not. Pages about details are made from the code: [knowledge](knowledge/README.md).

## State, 25 November 2026

| Roadmap phase | State |
|---|---|
| 0 Protect | Done: Git, cold copy, verified copies, restore, drill, migrations, start check. **Open: a second place for copies** (user) |
| 1 Backend correctness | Done: D-01 to D-05, D-08 to D-24, D-27, D-28 fixed, with tests |
| 2 Film-neutral core | Done: no film is named in `studio/` or `web/` (test); the first benchmark is data; two films with clashing names tested; foreign script run by real agents |
| 3 Agents | 12 files. Real runs: story-editor (maker, reviewer), continuity-inspector, production-designer, cinematographer (maker), film-editor, image-maker, sound-designer. **Not run yet:** motion-engineer, post-artist, release-auditor, forensic-inspector, blind-viewer. No per-role bench yet |
| 4 Orchestration | Done: tasks from events, leases, dispatch with allowance, showrunner tools and `/studio` |
| 5 Engine and editing | Done: commands, honest schema, long-film mixer, retime, masks, transitions, 29 effects, previs, checks over the full length. Not built, by decision: tracking, stabilising, roto (when a shot needs them) |
| 6 Failures | Done for everything that can be simulated; real provider replies not seen |
| 7 AgentOps, knowledge | Usage, agreement, misses, time records, generated knowledge. Routing experiments wait for data |
| 8 Security, operations | Event hash chain, protected records, restricted project settings, health page, drills |
| 9 Interface | New: 19 pages, design system, browser tests at three widths. **Not yet used by the user** |
| 10 Real films | **Not started: needs keys, recordings, clips and verdicts** |

## Evidence

- Tests: run `python -m pytest`. Database rules of the approved baseline: `python tools/validate_baseline.py` (169).
- Inspection on planted defects ([held-out](evidence/inspection-bench-heldout.json)): 19/45 → 40/45; on footage it was not tuned on 34/36 with 1 false alarm in 4 clean clips. Rendered footage, not generated footage. **Slow melting of shapes is mostly missed**; that is left to eyes.
- Foreign script “Low Water” with real agents in a throw-away studio: plan (accepted first time, 11 real open questions), review (11 usable findings), canon (21 entities), 9 designed shots with 3D sets, edit by the film-editor (11 commands, 2 refusals it corrected itself), render passed every check; image plan accepted first time; sound plan after three refusals (73 thousand tokens; its guide has been corrected since, not yet tried again). Otherwise 12 to 32 thousand output tokens per run; together about 193 thousand.
- The first benchmark, from a restored copy: 1,134 frames, 36.8 frames/s, all checks pass, loudness −17.1 for −17 wanted.

## Decisions taken while building

| Decision | Why |
|---|---|
| One table for creative documents (`work_doc`) with linter, reviewers, gate and effect per kind | Same rules for plan, canon, shots, images; less code than one table each |
| An edit names shots, lines and cues (`cut_rev`); files are resolved at render time | A better take replaces a stand-in without touching the edit |
| Agents send commands; the same commands serve the user's buttons | One validated door |
| Two full reviews and one closing review (was three) | 26 reviews for 7 packages was not affordable |
| image-maker proposes only; the user presses generate | Keeps the spending rule of P0 |
| Music generation stays out; library and silence | Licence |
| Reviews of whole edits are off until the user switches them on | Cost |

## Working rules

- Never act on `data/` in development. Tests and trials use a temporary `STUDIO_DATA`. The live database changes only by the app or a migration, after a copy.
- Schema steps in `studio/schema/` are never edited once released; a change is a new step.
- After changing commands, effects, agents, gates or schema: `python tools/knowledge.py`.
- Claude usage is the scarce resource: no batches of agents; at most three at once; state the estimate first.

## Known loose ends

- A 73-thousand-token run is above the allowance logic's idea of typical; watch the first real sound plan.
- Face comparison has never seen a generated person. Melting shapes are mostly missed. Provider replies are stand-ins until keys exist.

## Next

1. User: use the app, keys, casting, first images (slice A), one Gemini clip, blind verdicts.
2. From that: thresholds of inspection on real clips, per-role benches, first agent-written packages compared with the hand-written ones.
3. Then Slice B with real media, then a complete short film through every gate.
