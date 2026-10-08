# Hand-thesia - Animated Hands for Piano

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/V9011N/pygame-piano-animator)

What if Synthesia had nice lively hands to go along with those falling notes? Well look no further!

Hand-thesia reads MIDI files and animates, in real time and seen from above, the piano keys and a pair of
procedurally animated hands playing them - with fingering worked out automatically and
editable by hand.

### Sample Vids Showing the Different Hand Skins

[![Bach Solfeggietto Example with Skeleton Hands](https://img.youtube.com/vi/gj3QNIqWuMY/0.jpg)](https://www.youtube.com/watch?v=gj3QNIqWuMY)

Bach Solfeggietto with Skeleton Hands

[![Beethoven Waldstein Example with Robot Hands](https://img.youtube.com/vi/h--70K8a0XE/0.jpg)](https://www.youtube.com/watch?v=h--70K8a0XE)

Beethoven Waldstein with Robot Hands

[![Debussy Arabesque Example with Human Hands](https://img.youtube.com/vi/aiAx60huEHE/0.jpg)](https://www.youtube.com/watch?v=aiAx60huEHE)

Debussy Arabesque with Human Hands

[![Cuphead Show Example with White Gloves](https://img.youtube.com/vi/4Zgt4DTOMAo/0.jpg)](https://www.youtube.com/watch?v=4Zgt4DTOMAo)

From The Cuphead Show with White Gloves

### Like what you see and want to help with further development? Join the Discord: https://discord.gg/HDRX89mQ9 

## Features

- **Falling-notes player** with a full 88-key keyboard, pedal support and sound through your
  system's MIDI synth. What you hear follows what the animated hands physically play (rolled
  chords, early releases), and the pedals are played as on/off switches.
- **Synced recordings**: play a rendered audio file (WAV, OGG, MP3, FLAC) instead of the
  synth, in time with the notes and hands. Pick the playback speed first (it stays fixed), then
  line the recording up by dragging its waveform across the top of the player.
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
- **Recent**: the main menu lists the last five setups you opened - a MIDI file with its soundfont or
  synced recording, or a file in the fingering editor - to open again in one click.
- **Settings**: drag the keyboard higher or lower on the screen, choose realistic or equal keys,
  cap the frame rate (24-240 fps, or uncapped), choose a soundfont (.sf2 / .sf3) to play the notes
  with instead of the system's MIDI synth, choose the interface font (any installed font or a font
  file), and show a performance overlay (frame rate and frame
  times). A volume control sits in the player's and the editor's top bars;
  hover over the player's controls to see what each does; the arrow-keys button under them lists the keys.
- **Pianists & hands**: create pianists with their own hand anatomy (19 bones and the wrist width), technique and
  fingering preferences, and a skin (cartoon, white gloves, robot or skeleton).

## Credits

The interface typeface is Source Sans Pro by Adobe, under the SIL Open Font License
(`assets/fonts/OFL.txt`).

## Requirements

- Python 3.10+ (developed with 3.12)
- `pygame-ce` (or plain `pygame`) and `pretty_midi` (see `requirements.txt`)
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

Switching an existing install from `pygame` to `pygame-ce` (both install as `import pygame`, so
remove the old one first): `pip uninstall -y pygame` then `pip install -r requirements.txt`.

Other options: `--no-sound`, `--speed 0.5`, `--screenshot frame.png --at 12.5`, and
`--audio recording.wav --audio-speed 0.75 --audio-offset 1.5` to play a synced recording straight away.

Keyboard controls are listed at the top of `main.py` (player) and `editor.py` (editor), and in
the app's status lines.

## A single .exe (Windows)

Hand-thesia can be built into one self-contained `Hand-thesia.exe` with
[Nuitka](https://nuitka.net) - no Python needed on the computer that runs it:

```bash
pip install -r requirements.txt -r requirements-build.txt
python build.py                  # -> dist\Hand-thesia.exe (no console window)
python build.py --console        # the same with a console window, for testing
```

Nuitka needs a C compiler: it offers to download MinGW64 the first time (accepted automatically),
or uses Visual Studio's if installed. The first build takes several minutes; later ones reuse
the compiled parts in `build\`. Build with the Python you develop with (it has tkinter, needed for
the file dialogs). `build.py` asks that Python's Tcl and Tk where their libraries are and hands
them to Nuitka (it prints `Using --tcl-library-dir=...`); with Tcl/Tk 9, as in the python.org
Python 3.14, the libraries are built into the DLLs and are copied out to `build\tcl-library` first.

Using the .exe:

- Double-click it, drop MIDI files on its window, or open a `.mid` file with it ("Open with").
  The command-line options above work too.
- It unpacks itself once per version to `%LOCALAPPDATA%\Hand-thesia\<version>` and starts
  quickly from then on.
- Your pianists and settings are kept in `%APPDATA%\Hand-thesia\pianists` (that's
  `C:\Users\<you>\AppData\Roaming\...` - not `AppData\Local`, where the program unpacks itself) - or, to
  keep everything beside the .exe (a USB stick, say), make a folder called `pianists` next to it. The folder
  appears once a pianist is saved or a setting changed; "Open pianists folder" in Pianists & hands opens it.
- If something goes wrong it says so in a message box; the details go to `hand-thesia.log` in
  the same data folder.
- To move pianists from a source checkout, copy the checkout's `pianists` folder there.

To change the icon, edit `tools/make_icon.py` and run it (it writes `assets/icon.png` and
`assets/icon.ico`).

## Fingering in MIDI files

Exported files are copies of the original with a text event before each note:
`R1`-`R5` / `L1`-`L5` (hand and finger), `F1`-`F5` (finger only) or `R` / `L` (hand only), and
each hand's notes on its own MIDI channel (the pedals go to both).
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
