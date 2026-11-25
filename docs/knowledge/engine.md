<!-- made by tools/knowledge.py from the code; do not edit -->
# Media engine 0.2.0

Everything the timeline accepts is done; anything else is refused. `tests/test_edit.py` checks each entry.

## Edit commands

| Command | Meaning |
| --- | --- |
| place | put a new clip before or after another |
| trim | change length or start in the source |
| slip | show another part of the source, same length |
| move | change the order or the track |
| remove | take out a clip, a sound or a silence |
| set_source | use another shot, part, image, line, cue or sound |
| set_speed | retime: a number, or keyframes |
| set_transform | position, scale, rotation, opacity; keyframes allowed |
| set_transition | how it begins: cut, dip, dissolve, flash, wipe |
| set_blend | add, multiply, normal, screen |
| set_matte | a black and white picture that shows or hides |
| set_hold | keep the last frame when the source ends |
| set_until | let the picture stay until a silence is complete |
| add_effect | append an effect |
| set_effect | change parameters, switch, or limit with a mask |
| remove_effect | take out an effect |
| place_sound | add a line, cue, library sound or asset |
| set_sound | change time, level, place, fades or effects of a sound |
| plan_silence | a scripted silence, enforced in the mix |
| marker | name a moment |
| set_finish | effects on every picture clip |
| set_rooms | acoustic spaces |
| set_ducking | what gives way to what, by how much |
| set_loudness | target level |
| set_parts | scenes of the film in order |

Transitions: cut, dip, dissolve, flash, wipe. Blends: add, multiply, normal, screen. Text: caption, timestamp.

## Picture effects

| Effect | Parameters and defaults |
| --- | --- |
| beam | x=0.5, y=0.5, radius=0.22, soft=0.5, ambient=0.04, gain=1.6, hot=0.35, warm=0.12, squash=1, angle=0, jitter=0.004 |
| bloom | threshold=0.8, gain=0.25, radius=26 |
| blur | radius=4 |
| chromatic | amount=0.002 |
| compression | block=8, amount=0.5, levels=24 |
| corner_pin | x0=0, y0=0, x1=1, y1=0, x2=1, y2=1, x3=0, y3=1 |
| depth_parallax | dx=0, dy=0, zoom=0, focus=0.5 |
| dust | gain=0.5, density=1, size=1.3, drift_x=0.004, drift_y=-0.002, lit=0.25 |
| fade | level=1 |
| film_curve | shoulder=0.8, toe=0.004, white=4 |
| flicker | amount=0.08, speed=1, base=1, seed=0 |
| fog | density=0.25, height=0.55, scale=3, drift=0.004, r=0.5, g=0.55, b=0.6, light=0.25 |
| glow_pulse | x=0.5, y=0.5, radius=0.2, gain=0.4, r=1, g=0.35, b=0.08, pulse=0.15, speed=0.4 |
| grade | exposure=0, contrast=1, saturation=1, temp=0, tint=0, lift=0, gamma=1, pivot=0.18, shadow_r=0, shadow_g=0, shadow_b=0 |
| grain | amount=0.03, size=1.4, chroma=0.25 |
| halation | threshold=0.6, gain=0.35, radius=14 |
| ir_look | gain=1.1, falloff=0.7, noise=0.03 |
| key | r=0.05, g=0.6, b=0.1, tolerance=0.25, soft=0.15, spill=0.5 |
| lens_blur | focus=0.5, radius=10, range=0.08 |
| lens_distort | k1=0, k2=0, zoom=1, chroma=0 |
| letterbox | ratio=2.39 |
| luma_key | low=0.02, high=0.12 |
| rain | gain=0.25, density=1, speed=1, slant=0.06, length=0.05 |
| scanlines | count=480, amount=0.3, roll=0 |
| shaft | x0=0.5, y0=1.1, x1=0.5, y1=0.3, w0=0.02, w1=0.22, gain=0.06, warm=0.1, noise=0.6 |
| sharpen | amount=0.4, radius=1.2 |
| shimmer | x=0.5, y=0.4, rx=0.2, ry=0.25, amount=0.0012, scale=34 |
| tint | r=1, g=1, b=1, amount=0.3 |
| vignette | amount=0.5, power=2.2 |
| wipe | progress=0, angle=0, soft=0.06 |

## Sound

Buses: narration, dialogue, foley, hard_fx, ambience, music, reverb. Effects: highpass, lowpass, phone, room. Default ducking: ambience -3.5 dB under voice; music -7 dB under voice. Keyframes count film frames. Rooms, distance, walls and moving sources are simulated; ambience can loop.
