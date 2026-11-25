<!-- made by tools/knowledge.py from the code; do not edit -->
# Workflow and gates

| Gate | What is approved |
| --- | --- |
| G0_script | Script fixed |
| G1_plan | Production plan |
| G2_canon | Canon: people, places, things |
| G3_shots | Shot design per scene |
| G3_images | Image plan |
| G4_frame_pair | Frames of a generated shot |
| G5_package | Gemini package |
| G7_clip | Imported clip |
| G9_voice_package | Voice package |
| G9_sfx_package | Sound package |
| G8_scene | Scene edit |
| G10_film | Film edit |
| G11_release | Release |

## Documents

| Document | Made by | Reviewed by | Built on | Gate |
| --- | --- | --- | --- | --- |
| production_plan | story-editor | continuity-inspector | script | G1_plan |
| canon | production-designer | continuity-inspector | plan | G2_canon |
| shot_list | cinematographer | film-editor | plan, canon | G3_shots |
| image_plan | image-maker | cinematographer | canon, shots | G3_images |
| motion_plan | motion-engineer | its packages are reviewed | shots | - |
| sound_plan | sound-designer | its packages are reviewed | shots | - |
| release | release-auditor | its packages are reviewed | - | G11_text |

An approval records the hash of everything it was based on. When one of these changes, the approval no longer holds. A rule's objection cannot be overruled; a reviewer's can, by the user, with a reason.

## Before release

| Item | Meaning |
| --- | --- |
| film_approved | The film edit is approved by you |
| material | No stand-ins are left |
| rights | Every picture and sound may be used |
| clips | Every generated clip passed its gate |
| voices | Voices are cast and lines approved |
| technical | The delivered file passed every check |
| captions | Captions were made and looked at |
| text | Title, description and tags are chosen |
| thumbnail | A thumbnail is chosen |
| disclosure | The AI disclosure is written |
| credits | Required credits are in the description |
| backup | A verified copy exists on another drive |
| watched | You watched the whole master with sound |
