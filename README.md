# Hand-thesia - Animated Hands for Piano

What if Synthesia had nice lively hands to go along with those falling notes? Well look no further!

Hand-thesia reads MIDI files and animates, in real time and seen from above, the piano keys and a pair of
procedurally animated hands playing them - with fingering worked out automatically and
editable by hand.

***NOTE: VIBE-CODED PROJECT! Do not post issues, you'll fix them faster just vibe-coding yourself.***

## Features

- **Falling-notes player** with a full 88-key keyboard, pedal support and sound through your
  system's MIDI synth. What you hear follows what the animated hands physically play (rolled
  chords, early releases), and the pedals are played as on/off switches.
- **Automatic hand split** for single-track MIDI, and **fingering** from a beam-search planner
  with figure recognition (scales, arpeggios, chromatic runs, octaves, double notes, trills,
  repeated notes), the thumb or little finger covering two keys when a chord needs it, and
  weights learned from the PIG fingering dataset.
- **Procedural hands**: inverse kinematics per finger, crossings, thumb-unders, hand
  crossings (the crossing hand always goes over), wrist bounce on repeated chords and forearm
  rotation in tremolos.
- **Fingering editor**: a DAW-style piano roll above the keyboard and hands. Select notes,
  right-click to set hand and finger, re-plan groups, see per-note difficulty, and finger note
  by note in **sequential mode** (number keys, or M K O ; ' / V D W A LShift). Exports a copy of
  the MIDI file with the fingering embedded (or PIG text).
- **Pianists & hands**: create pianists with their own hand anatomy (19 bones), technique and
  fingering preferences, and a skin (cartoon, white gloves, robot or skeleton).

## Requirements

- Python 3.10+ (developed with 3.12)
- `pygame` and `pretty_midi` (see `requirements.txt`)
- For sound: any MIDI output (Windows' built-in Microsoft GS Wavetable Synth works; a
  soundfont synth such as VirtualMIDISynth sounds much better)

## Getting started

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python main.py                   # main menu
python main.py song.mid          # play a file straight away
python main.py song.mid --edit   # open it in the fingering editor
```

Other options: `--no-sound`, `--speed 0.5`, `--screenshot frame.png --at 12.5`.

Keyboard controls are listed at the top of `main.py` (player) and `editor.py` (editor), and in
the app's status lines.

## Fingering in MIDI files

Exported files are byte-for-byte copies of the original with a text event before each note:
`R1`-`R5` / `L1`-`L5` (hand and finger), `F1`-`F5` (finger only) or `R` / `L` (hand only).
Loading such a file restores the hands and fingers exactly. PIG fingering files (`.txt`) can be
opened and exported too.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

The tests run headless (SDL dummy drivers) and use a temporary pianists folder. Design notes
live in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md); [`CLAUDE.md`](CLAUDE.md) is the short
guide for working on the code with Claude Code.

Benchmarks that need data you download yourself (not in the repository):

- `python pig_eval.py path/to/FingeringFiles` - match rates against the
  [PIG dataset](https://beam.kisarazu.ac.jp/research/PianoFingeringDataset/) annotators.
- `python learn_weights.py path/to/FingeringFiles` - re-tune the planner weights on PIG pieces 031-150.

## Data not included

The PIG dataset, score PDFs, skin reference images and third-party MIDI files (for example the
MAESTRO dataset, CC BY-NC-SA 4.0) are not redistributed here; see `.gitignore`. Only three
small test MIDI files made for this project are tracked.
