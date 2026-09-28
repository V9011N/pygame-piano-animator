# CLAUDE.md

Guide for working on this repository with Claude Code. Detailed design notes (algorithms,
constants, measured results) are in `docs/ARCHITECTURE.md` - read the relevant section before
changing a subsystem, and update it when you change behaviour.

## What this is

A pygame app that plays MIDI files as falling notes above an 88-key keyboard, with
procedurally animated hands (seen from above) whose fingering is planned automatically, plus a
fingering editor and a pianist/hand editor. Flat module layout at the repository root; modules
import each other by name.

## Run

```bash
pip install -r requirements.txt
python main.py [song.mid] [--edit] [--no-sound] [--speed X] [--screenshot out.png --at SECONDS]
```

`--screenshot` renders one frame and exits - useful for checking visual changes without a
window (combine with `SDL_VIDEODRIVER=dummy`).

## Test

```bash
pip install -r requirements-dev.txt
pytest
```

Headless (SDL dummy drivers); `tests/conftest.py` redirects `pianist.FOLDER` to a temp dir.
Add a test for new behaviour where it can be checked numerically (fingering results, detected
gestures, editor state after scripted keys). Keep tests fast and free of local data.

## Modules

| File | Role |
|---|---|
| `main.py` | `App` (window, synth, mode switching, frame clock), `MainMenu`, `Visualizer` (falling notes) |
| `common.py` | Shared UI and playback: colours, `bottom_layout`, `Keyboard`, `MidiOut`, `Performance`, `Transport`, dialogs, buttons, sliders |
| `midi_loader.py` | `MidiSong` / `Note`, MIDI + PIG loading, hand assignment, fingering markers, `save_fingered_midi`, `pedal_switches` |
| `hand_split.py` | Beam search that splits single-track MIDI into hands |
| `fingering.py` | Fingering planner (beam search over chord states, cost weights, `LEARNED_W`, thumb/pinky pairs, `score_fingering`) |
| `figures.py` | Figure recognition (scales, arpeggios, chromatic, octaves, repeated notes, trills, double notes) feeding the planner |
| `hands.py` | `HandGeometry`, `HandAnimator` (per-hand IK, crossings, rolled chords, wrist gestures), hand-crossing layering, skeleton drawing |
| `skins.py` | Skinned hand drawing (cartoon, gloves, robot) from the pose structure |
| `pianist.py` | `Pianist` model (anatomy, behaviour settings, skin), storage in `pianists/` |
| `hand_editor.py` | "Pianists & hands" studio (browser, overview, anatomy, behaviour pages) |
| `editor.py` | Fingering editor (piano roll, context menus, undo, sequential mode, difficulty, export) |
| `pig_eval.py`, `learn_weights.py` | PIG benchmark and weight tuning (need the dataset locally) |

## Conventions and gotchas

- The left hand is solved as a mirrored right hand (`fingering.mirror_pitch`, about D4); the
  keyboard pattern is symmetric about D, so white/black properties survive mirroring.
- Behaviour settings live in `pianist.BEHAVIORS`; the studio's behaviour page lists them
  automatically. Read them with `Pianist.b(key)` (e.g. `p.b("wrist_bounce")`) so older pianist files get defaults.
- Call `hands.pair_hands` on the animators whenever you build them: an idle hand moves out of the playing
  hand's way (`HandAnimator._placed_at`), and `crossing_episodes` assumes it does.
- `HandAnimator` is rebuilt whenever fingering changes; the editor defers that rebuild while
  typing in sequential mode (`_rebuild_due`). `editor.notes` is the song's own list - update
  `editor.index` when replacing a note object.
- Nothing in a hand moves faster than the pianist's top speed (`max_speed`): the hand split, the
  fingering, the timeline (`HandAnimator._speed_schedule`) and the drawn motion all keep to it.
- Audio (and every lit key or note) follows the hands' performance (`HandAnimator.performance`),
  not the raw MIDI note times - keys let go early or struck late to keep to the top speed; pedals are sent as switches (`MidiSong.controls`), raw values kept in `raw_controls`.
- The frame clock is capped (`MAX_FRAME_DT`) so slow loads never jump the song ahead.
- Keep the fingering planner deterministic; check changes against the PIG test split
  (`pig_eval.py`) and Hanon when you have the data - see `docs/ARCHITECTURE.md` for the
  current numbers.
- Don't commit third-party data (MIDI collections, PIG files, PDFs, reference images) or
  personal `pianists/` files; `.gitignore` covers them.
- Windows is the main target (the author's machine); paths go through `os.path`, and file
  dialogs use tkinter.
