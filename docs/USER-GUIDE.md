# Using the studio

## Start
Double-click **Start Studio.bat**. Keep the black window open. The page works only on this computer, and most of it works without internet.

## First ten minutes (free, nothing leaves the computer)
1. **Backup** → *Copy now*, then *Test restoring*. Add a second place when you have one.
2. **Home**: what is finished, what waits for you. The film chooser is at the top left; *New film…* starts another story.
3. **Cut**: play the benchmark. Grey figures and a computer voice are stand-ins.
4. **Review** → *Try the inspection on any clip*: your verdict first, findings after.

## A film, in order

| Step | Page | You do | Others do |
|---|---|---|---|
| 1 Script | Script | Import it, look at how it was read, fix it | – |
| 2 Plan | Plan and canon → Ask for work | Ask, then **Agents → Run** | story-editor writes, continuity-inspector checks |
| 3 Open questions | Script | Decide what the script leaves open | – |
| 4 Canon | Plan and canon | Approve people, places, things | production-designer, continuity-inspector |
| 5 Shots | Plan and canon, Shots | Approve per scene; render 3D storyboards | cinematographer, film-editor |
| 6 Images | Images | Open an allowance (Budget), allow the API (Home), agree to each price, judge | image-maker writes; inspectors look |
| 7 Gemini clips | Gemini video | Check the pair, approve, generate **by hand in the Gemini app**, drop the clip | motion-engineer, cinematographer |
| 8 Voices, sounds | Voices, Sound effects | Cast first. Make lines by hand, or allow the API | sound-designer, story-editor |
| 9 Judge | Review | Blind verdict, then findings; mark findings true or wrong | forensic and continuity inspectors |
| 10 Edit | Cut | Assemble, ask the film-editor, note changes, approve | film-editor, sound-designer, post-artist |
| 11 Release | Release | Text, thumbnail, watch the master, approve; **you** upload | release-auditor |

Documents may be approved against a reviewer with a written reason. Rules cannot be overruled.

## What costs what
- **Agents** use your Claude plan. Each run is started by you, inside the weekly allowance on the Agents page. If Claude's limit is reached, agents pause and nothing else stops.
- **Images** and **Gemini inspection** show the price and ask before every request. Together they cannot pass the approved totals, $10 and $5.
- **ElevenLabs** by API uses only included credits that you release on Budget. Nothing is ever bought.

## Keys
Only on **Connectors**: paste, *Save key*, *Test connection* (free), *Enable*. Keys are kept by Windows and cannot be shown again.

## When something goes wrong
Each problem says what happened, whether money may be involved, what is safe and what you can do.

| You see | It means |
|---|---|
| Paused | Nothing more is sent until you choose |
| No answer, may have been charged | Look in the provider's history, then press what you found. It is never sent twice |
| Waiting for memory | Close Chrome or another large program; the job starts by itself |
| Interrupted | *Continue* goes on from the last finished part |
| Evidence number | The same number is in `data\errors.log` |
| The studio does not start | The window names the reason and the two commands that restore the newest copy into a new folder |

## Where things are
`data\` (database, originals, renders, storyboards) · `data\packages\` (frames and prompt for Gemini) · `data\release\` (packages for YouTube) · copies: `E:\AIFilmStudio-Backup`.
