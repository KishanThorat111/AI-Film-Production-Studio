# AI Film Production Studio

A local-first studio for making original horror films with AI assistance, on one Windows computer. It takes a screenplay through planning, canon, shot design, 3D storyboards, images, hand-made video clips, voices, sound, editing, checking and a package for YouTube.

It is a working tool under construction. What is proven and what is not is written down in [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md).

## What it does

- **Understands any script** (Word, Fountain, Final Draft, plain text). The words are fixed at import; agents and people may not change them.
- **Agents propose, rules check, others review, the user decides.** Twelve Claude Code agents with a handful of tools each. They cannot approve, spend, change settings or see keys.
- **Its own media engine:** GPU compositor, sound mixer, edit commands with reasons, segment-wise crash-safe rendering, checks over the whole length of the delivered file. What it accepts, it does; anything else is refused.
- **Inspects what comes back:** every frame of a clip, frame adherence, words against the script, with the user's blind verdict first.
- **Money tells the truth:** prices are agreed before every request; an uncertain request is never sent twice; totals cannot be raised from inside.
- **Everything stays local:** database, media, storyboards and edits. Providers are contacted only for an agreed request.

Video is generated **by hand** in the Gemini app. There is no video-generation API, no browser automation and no upload.

## Requirements

Windows 11, Python 3.13, ffmpeg with NVENC on the PATH, an NVIDIA GPU with Vulkan, about 1.5 GB of free memory while rendering. Claude Code for the agents (optional; everything else works without it).

## Install once

```powershell
git clone https://github.com/KishanThorat111/AI-Film-Production-Studio
cd AI-Film-Production-Studio
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock.txt
```

## Use

Double-click **Start Studio.bat**. Nothing else is needed day to day. See the [guide](docs/USER-GUIDE.md).

## Develop

```powershell
.venv\Scripts\python -m pytest            # everything; needs the GPU and ffmpeg
.venv\Scripts\python tools\knowledge.py   # rewrite the knowledge pages from the code
.venv\Scripts\python tools\publish_check.py   # what must not be published?
git config core.hooksPath .githooks       # runs that check before every push
```

[Knowledge pages](docs/knowledge/README.md) are made from the code and tested against it. Films, scripts, media, databases and keys never belong in this repository; `private/` and `data/` are ignored.

## Licence

No licence has been chosen. All rights are reserved by the author until one is.
