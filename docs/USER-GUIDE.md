# Using the studio

## Start and stop

| You want to | Do |
|---|---|
| Start | Double-click **Start Studio.bat**. A black window opens, then the browser. Nothing else is needed |
| Keep working | Leave the black window open. The page works only on this computer, and most of it without internet |
| Get the page back | Closed the browser? Double-click **Start Studio.bat** again. It opens the studio that is already running |
| Stop | *Stop the studio* at the bottom of the menu. Closing the black window also works: a running render goes on from its last finished part next time |
| After a restart, a crash or a power cut | Start as usual. The studio checks its database, says what was interrupted, and offers *Continue* |

If the studio is updated, its database is brought forward at the next start, after a copy. If the database is damaged, the studio does not start and changes nothing; the window says how to restore a copy into a new folder.

## First ten minutes (free, nothing leaves the computer)

1. **Backup** → *Copy now*, then *Test restoring*. Add a second place when you have one.
2. **Home** shows **Next step**: the one thing to do now, why, and whether it is local, by hand, or paid.
3. **Cut**: play the benchmark. Grey figures and a computer voice are stand-ins.
4. **Review** → *Try the inspection on any clip*: your verdict first, findings after.

## Bringing in your files

Two ways, with the same result: drop the file on the card it belongs to, or drop any number of files anywhere in the window (page **Bring in files**). There the studio says what each file is and where it probably belongs, from its name, the words spoken in it, or its first frame. You confirm. Until then nothing is in the film.

Your own file is never changed. The studio keeps an exact, read-only copy. *About this file* on every result shows where it lies, where it came from and where it is in use. *Show in folder* opens it. Never move or rename files in `data\`.

| You have | Page | Before | You fill in | What the studio checks | Then |
|---|---|---|---|---|---|
| Pictures: people, places, references, stills, first and last frames | **Images**, on the card with its name | An image plan, or *Add a picture that is not in the plan* | Where it comes from | Format, sharpness, face against your approved pictures | *Accept*. A still goes into the edit; first and last frames go to Gemini video |
| A Gemini clip | **Gemini video**, on the shot | *Check the pair* → *These are the frames* → *Approve this package*; then the five steps on the card | Account label (only a note) | Every frame, the two approved frames, whether it looks more like another shot | **Review**: watch, verdict, then the findings, *Accept*. It goes into the edit |
| A clip for a shot without a package | **Gemini video** → *A clip for a shot that has no package* | – | How it was made, and why | Every frame | The same |
| An ElevenLabs take | **Voices**, on the line | Cast first (the casting package), then approve the package | Credits used | Words against the script, level, noise, clicks | *Compare the takes*, *Accept* one. It goes into the edit |
| A sound effect from ElevenLabs | **Sound**, under *Effects to generate* | Approve the package | Credits used | Level, noise, clicks, length | *Accept*. It goes into the edit |
| Ambience, room tone, music, your own recordings | **Sound** → the sound → *Fill it* (or *Add a sound that is not planned*) | – | Where it comes from; the credit, if one has to be shown | Level, clicks | *Accept*. If it is new, place it on Cut |
| Sounds for every film | **Library** | – | Kind, where it comes from | Level | Confirm the terms, then *Admit* |

Accepted: png, jpg, webp · mp4, mov, webm, mkv · wav, mp3, flac, ogg, opus, m4a. Up to 600 MB each.

- **The wrong clip or file?** A file that is not what it claims is refused and nothing is stored. A clip that looks like another shot is stored with a warning; *Reject* it with a reason.
- **Another version:** *Ask for another version* on the card. The accepted one stays in the edit until you accept the new one. Learning lists all versions and can bring an old one back.
- **Three attempts** per item, counted only when something was really made. When they are used, the studio stops and asks you.
- **On the timeline:** lines, sounds and shots are named in the edit, and the newest accepted file fills them. To add something new, open *Add a sound or a picture at the playhead* on Cut.
- **Licences:** a finished film refuses material whose source is not recorded or whose terms you have not confirmed. A source that is not listed can be added on Library.

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
| 8 Voices, sounds | Voices, Sound | Cast first. Make lines by hand, or allow the API | sound-designer, story-editor |
| 9 Judge | Review | Blind verdict, then findings; mark findings true or wrong | forensic and continuity inspectors |
| 10 Edit | Cut | Assemble, ask the film-editor, note changes, approve | film-editor, sound-designer, post-artist |
| 11 Release | Release | Text, thumbnail, watch the master, approve; **you** upload | release-auditor |

Documents may be approved against a reviewer with a written reason. Rules cannot be overruled.

## What costs what

- **Local, free**: importing, checking, storyboards, editing, rendering, backup, judging.
- **Agents** use your Claude plan. Each run is started by you, inside the weekly allowance on the Agents page. If Claude's limit is reached, agents pause and nothing else stops.
- **Images** and **Gemini inspection** show the price and ask before every request. Together they cannot pass the approved totals, $10 and $5.
- **ElevenLabs** by API uses only included credits that you release on Budget. Nothing is ever bought.
- **The Gemini app** is always used by hand, with your own plan.

## Keys

Only on **Connectors**: paste, *Save key*, *Test connection* (free), *Enable*. Keys are kept by Windows and cannot be shown again.

## If something goes wrong

Each problem says what happened, whether money may be involved, what is safe and what you can do.

| You see | It means |
|---|---|
| Paused | Nothing more is sent until you choose |
| No answer, may have been charged | Look in the provider's history, then press what you found. It is never sent twice |
| Waiting for memory | Close Chrome or another large program; the job starts by itself |
| Interrupted | *Continue* goes on from the last finished part |
| Evidence number | The same number is in `data\errors.log` |
| The studio does not answer | Its window was closed. Start it again |
| Something else is using port 8765 | An older window is open, or another program has the port. Close it, or restart the computer |
| The studio does not start | The window names the reason and the two commands that restore the newest copy into a new folder |

## Where things are

**Bring in files** lists every place with *Show folder*: originals, what was made from them, Gemini packages (`data\packages\`), YouTube packages (`data\release\`). Copies are kept where the Backup page says.
