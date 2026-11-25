<!-- made by tools/knowledge.py from the code; do not edit -->
# Agents

Each has a file in `.claude/agents/` and a role-scoped tool server. None can approve, spend, change a setting, read a file or see a key.

| Role | Does | Model | Effort | Kind |
| --- | --- | --- | --- | --- |
| blind-viewer | Watches an edit without knowing anything about it and says what came across. | sonnet | medium | checker |
| cinematographer | Designs the shots of a scene; reviews image plans and Gemini packages as director of the shot. | opus | medium | maker, checker |
| continuity-inspector | Compares pictures, clips, edits, plans and canon with what was fixed before. | opus | medium | checker |
| film-editor | Edits scenes through commands; reviews shot lists for whether they will cut. | opus | medium | maker, checker |
| forensic-inspector | Finds physical, anatomical and temporal defects in one picture or clip. | opus | medium | checker |
| image-maker | Writes the image plan: identity anchors, references and frames, with prompts for image models. | opus | medium | maker |
| motion-engineer | Plans how one shot moves between two frames and writes the Gemini package for use by hand. | opus | medium | maker |
| post-artist | Finishes and repairs pictures of an edit through commands: matching, integration, texture. | opus | medium | maker |
| production-designer | Writes canon: people, clothes, places, props, looks and voices as measurable facts. | opus | medium | maker |
| release-auditor | Writes the YouTube package and audits rights, disclosure, honesty and delivery. | sonnet | medium | maker, checker |
| sound-designer | Prepares voice packages, cue sheets and effect packages; shapes the sound of an edit through commands. | opus | medium | maker |
| story-editor | Reads a script as a story and writes the production plan; independently reviews voice and sound packages. | opus | medium | maker, checker |

## What makers are asked for

| Task | Role | Hands in | Sees |
| --- | --- | --- | --- |
| make_plan | story-editor | production_plan | guide, script, facts, previous, lessons |
| make_canon | production-designer | canon | guide, script, plan, canon, previous, lessons |
| make_shots | cinematographer | shot_list | guide, script, plan, canon, shots, facts, previous, lessons |
| make_images | image-maker | image_plan | guide, canon, shots, images, facts, previous, lessons |
| make_motion | motion-engineer | motion_plan | guide, shots, canon, images, frame_first, frame_last, facts, previous, lessons |
| make_sound | sound-designer | sound_plan | guide, script, shots, canon, lines, library, facts, previous, lessons |
| make_cut | film-editor | cut | guide, engine, script, plan, shots, material, edit, notes, lessons |
| make_mix | sound-designer | cut | guide, engine, script, material, edit, notes, lessons |
| make_post | post-artist | cut | guide, engine, shots, material, edit, notes, lessons |
| make_release | release-auditor | release | guide, plan, facts, edit, previous, lessons |

## What checkers are asked for

**voice**

- WORDS: The spoken text keeps every script word, in order, and adds none.
- MODEL: Tags, pauses, punctuation and settings are right for the named model.
- PLAYABLE: Each line's intent is a playable action that fits this moment of the scene and what the character knows now.
- SAME: Directions would keep one recognisable person across lines and packages of that voice.
- USABLE: A person could make every line from this page alone, without guessing.
- FILM (advice): Pace, distance and length fit the planned shot and edit.

**sfx**

- SOURCE: The prompt describes a physical source and action that this moment of the scene needs.
- SINGLE: It is one sound, placeable in the edit, dry enough to be given the room later.
- ABSENT: It excludes speech, music and anything the script says is not heard.
- SEARCH: The library search and the reason for generating are credible.
- TONE (advice): The sound serves dread through truth, not cliche.

**video**

- ROLES: It is unmistakable which image is first and which is last.
- ONE: One achievable main action; no contradiction between blocks and prompt.
- CAMERA: The camera instruction matches the two frames and the shot design.
- REACH: The motion between the two frames is physically and visually reachable in the time.
- PURPOSE: The result would serve the shot's purpose.

**clip**

- ANATOMY: Bodies, hands and faces stay plausible in all frames shown.
- OBJECTS: Nothing appears, vanishes, merges or changes shape without cause.
- PHYSICS: Weight, contact, light and shadow behave plausibly.
- CAMERA: The camera does what its measurements say and nothing else.
- USABLE: At least one continuous range is usable; its frames are named.

**clip_match**

- IDENTITY: The people are the persons of the reference pictures, as far as pictures can show.
- WARDROBE: Clothes, hair and carried objects match canon and the neighbouring shot.
- PLACE: Place, props, light direction and screen direction match canon and the neighbouring shot.
- ACTION: The clip shows the planned action from first frame to last frame.

**image**

- ANATOMY: Bodies, hands, faces, eyes and teeth are plausible.
- PHYSICS: Perspective, contact, shadows and reflections agree with one light situation.
- ARTEFACT: No melted detail, false text, duplicated object or impossible join in the parts the eye goes to.
- ASKED: What the picture is for is in the picture.

**image_match**

- IDENTITY: The person is the person of the reference pictures, as far as pictures can show.
- CANON: Every 'must hold' fact of canon that can be seen is right.
- LIGHT: Light, time of day and geography agree with the shot and its neighbours.

**plan**

- CITED: Claims say no more than the cited script text supports.
- COMPLETE: No event, clue, spoken fact or time statement of the script is missing from the plan.
- CONFLICT: Contradictions inside the script are named with both quotes, and none is silently resolved.
- KNOWLEDGE: Who knows what, when, and what the audience knows, is right in every scene.
- DREAD (advice): The plan protects suspense: what is withheld and when it is revealed follows the script.

**canon**

- SOURCE: Stated facts are in the script; invented ones are marked as invented and contradict nothing.
- MEASURABLE: Two people could check a picture against each 'must hold' fact and agree.
- ENOUGH: A designer who never read the script could keep this person or place the same in every picture.
- OPEN: Nothing is fixed that the story needs to change later.

**shot_list**

- CUTS: Neighbouring shots cut together: size, angle, direction and light differ or agree on purpose.
- CHEAPER: No shot asks for a generation that an edit, a still with depth or 3D would do as well.
- REVEAL: Nothing is shown before the story wants it known.
- SPEECH: Lines have pictures that can carry them, with room for silence.
- RHYTHM (advice): Lengths and holds make a rhythm that serves dread rather than average coverage.
- POSSIBLE: Each action fits into its time and one shot.

**image_plan**

- CANON: Each prompt contains every 'must hold' fact that the picture can show, and contradicts none.
- SHOT: Framing, lens, height, light and what is withheld are those of the shot design.
- PAIR: First and last frames of a shot can be joined by one action in its time.
- REFS: References are the right ones and few enough to be obeyed.
- CLEAR: The prompt describes a photograph, not a mood.

**cut**

- STORY: What happens, to whom and why it matters can be followed from the pictures and the sound map alone, as far as the scene wants it followed.
- GEOGRAPHY: Where people are in relation to each other and the place stays clear unless confusion is intended.
- CONTINUITY: People, clothes, props, light and direction continue across cuts.
- RHYTHM (advice): Holds, cuts and silences are purposeful; no stretch is dead or rushed.
- REVEAL (advice): Reveals land where the film wants them and not earlier.

**release**

- RIGHTS: Every used asset has terms that allow this film on YouTube, and required credits are present.
- DISCLOSE: The AI disclosure is correct for what is shown and heard.
- HONEST: Title, thumbnail and description promise only what the film delivers.
- FORMAT: The delivered file passes every technical check.
- SAFE (advice): Content notes fit YouTube's rules for violent or frightening material.

At most 2 full reviews of a package, then one closing review, then the user decides.
