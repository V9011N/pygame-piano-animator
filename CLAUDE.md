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
| `main.py` | `App` (window, synth, mode switching, frame clock, performance overlay), `MainMenu`, `Visualizer` (falling notes) |
| `app_settings.py` | `SettingsScreen`: keyboard position (dragged; `common.set_keyboard_place`), keyboard type, frame rate cap (`App.set_fps_cap`), performance overlay, soundfont (`App.set_soundfont`) |
| `sf_synth.py` | A chosen soundfont: `SoundfontOut` (MidiOut's interface over `sf2.Synth`, the pedals played here), `MixerStream` (its sound through pygame's mixer), `make_synth` (falls back to the system synth) |
| `sf2.py` | The built-in SoundFont player (.sf2 / .sf3) in numpy: zones, loops, volume envelope; no compiled package |
| `audio_sync.py` | Synced recordings: `SyncAudio` (decode, waveform peaks, play from any point, `offset`), `PlaybackSetup` (default sound or sync; speed, then the audio file, length-checked) |
| `common.py` | Shared UI and playback: colours, `bottom_layout` (keyboard placement), `Keyboard` (realistic or equal keys, `key_style`), `MidiOut`, `Performance`, `Transport`, dialogs, buttons, sliders, `VolumeSlider`, `draw_tooltip`, `run_busy` (a job in a worker thread behind a progress bar) |
| `progress.py` | How far a long job has got: `report(frac)` from deep loops, nested `stage(lo, hi)` |
| `midi_loader.py` | `MidiSong` / `Note`, MIDI + PIG loading, hand assignment, fingering markers, `save_fingered_midi`, `pedal_switches` |
| `hand_split.py` | Beam search that splits single-track MIDI into hands |
| `fingering.py` | Fingering planner (beam search over chord states, cost weights, `LEARNED_W`, thumb/pinky pairs, `score_fingering`) |
| `glissando.py` | Glissando detection (strings of next-door white or black keys), marked glissandos (`Note.gliss`), episodes |
| `figures.py` | Figure recognition (scales, arpeggios, chromatic, octaves, repeated notes, trills, double notes) feeding the planner |
| `hands.py` | `HandGeometry`, `HandAnimator` (per-hand IK, crossings, rolled chords, wrist gestures), hand-crossing layering, skeleton drawing, `build_hands` / `load_with_hands` |
| `skins.py` | Skinned hand drawing (cartoon, gloves, robot) from the pose structure |
| `pianist.py` | `Pianist` model (anatomy, behaviour settings, skin), storage in `pianists/` |
| `hand_editor.py` | "Pianists & hands" studio (browser, overview, anatomy, behaviour pages) |
| `editor.py` | Fingering editor (piano roll, context menus, undo, sequential mode, difficulty, export) |
| `paths.py` | Where bundled files are (`resource()`) and where the user's data goes (`DATA_DIR`: beside the code from source; `%APPDATA%\Hand-thesia` or a portable `pianists` folder beside the .exe when compiled) |
| `build.py` | Nuitka single-file build (`dist/Hand-thesia.exe`); `requirements-build.txt`, `assets/` (icon, from `tools/make_icon.py`) |
| `version.py` | `VERSION` (`vYY.MAJOR.MINOR`, e.g. `v26.1.0`), shown in the window title and bottom-left corner |
| `pig_eval.py`, `learn_weights.py` | PIG benchmark and weight tuning (need the dataset locally) |

## Conventions and gotchas

- The left hand is solved as a mirrored right hand (`fingering.mirror_pitch`, about D4); the
  keyboard pattern is symmetric about D, so white/black properties survive mirroring.
- Behaviour settings live in `pianist.BEHAVIORS`; the studio's behaviour page lists them
  automatically. Read them with `Pianist.b(key)` (e.g. `p.b("wrist_bounce")`) so older pianist files get defaults.
- Load songs through `App._load` (or `run_busy` + `hands.load_with_hands`): it plans the hands in a worker
  thread behind a progress bar. Never touch the display from the job.
- Call `hands.pair_hands` on the animators whenever you build them: an idle hand moves out of the playing
  hand's way (`HandAnimator._placed_at`), and `crossing_episodes` assumes it does.
- `HandAnimator` is rebuilt whenever fingering changes; the editor defers that rebuild while
  typing in sequential mode (`_rebuild_due`). `editor.notes` is the song's own list - update
  `editor.index` when replacing a note object.
- Nothing in a hand moves faster than the pianist's top speed (`max_speed`): the hand split, the
  fingering, the timeline (`HandAnimator._speed_schedule`) and the drawn motion all keep to it.
- Audio (and every lit key or note) follows the hands' performance (`HandAnimator.performance`),
  not the raw MIDI note times - keys let go early or struck late to keep to the top speed; pedals are sent as switches (`MidiSong.controls`), raw values kept in `raw_controls`.
- The frame clock is capped (`MAX_FRAME_DT`) so slow loads never jump the song ahead. With a synced
  recording the player's clock follows the wall clock instead (audio heard at song time t:
  `offset + t / speed`), and the synth is muted.
- Every fingering weight is user-tunable per pianist (`fingering.FINE_TUNE`, `Pianist.weights`, applied last by
  `apply_pianist`): add any new weight to `FINE_TUNE` (a test checks).
- Keep the fingering planner deterministic; check changes against the PIG test split
  (`pig_eval.py`) and Hanon when you have the data - see `docs/ARCHITECTURE.md` for the
  current numbers.
- Versions and the changelog are the author's call (from `v26.1.18.SNAPSHOT-01`): don't change
  `version.py` or `CHANGELOG.md` unless the prompt says what to set. **Before committing, if the prompt
  didn't give the version (and whether to add a changelog entry), ask for it - don't guess.** Formats:
  `vYY.MAJOR.MINOR` or `vYY.MAJOR.MINOR.SNAPSHOT-NN` (older: `vYYYY.MM.DD.HHMM` commit times); changelog
  entries `## <version> - title` with user-facing bullets, newest first (the main menu's "What's new";
  it glows when the top entry changes, not the version). Keep entries as general as possible: never name
  the pieces, files, recordings, datasets or other media used to find or develop a fix or feature (no
  titles, timestamps or per-piece numbers - those belong in `docs/ARCHITECTURE.md`).
- The app is also shipped as one compiled .exe (`build.py`): read bundled files through `paths.resource()` and
  add any new one to `build.py`'s `--include-data-files`; keep the user's files under `paths.DATA_DIR`
  (`pianist.FOLDER`), never beside the modules (compiled, that's a cache folder). No console then: `print`
  goes to `paths.log_path()`.
- Don't commit third-party data (MIDI collections, PIG files, PDFs, reference images) or
  personal `pianists/` files; `.gitignore` covers them.
- Windows is the main target (the author's machine); paths go through `os.path`, and file
  dialogs use tkinter.
