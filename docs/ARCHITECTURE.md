# Hand-thesia - architecture notes

Detailed design notes, kept up to date as features were added. Start with `CLAUDE.md` / `README.md` for the overview.

## Files
- `midi_loader.py` – wraps **pretty_midi**.
  - `load_midi(path)` returns a `MidiSong` with:
    - `notes`: each Note has pitch, start and end (seconds), velocity, track, hand and **finger**.
    - `notes_between(t0, t1)` and `active_notes(t)`.
    - `bar_times` and `duration`.
  - `read_fingering(path)` reads the fingering embedded in the file.
  - Hands, in order of precedence:
    1. Track names.
    2. Two tracks with mean pitches ≥ 7 semitones apart: the higher one is the RH.
    3. Otherwise `hand_split.split_hands()`.
- `hand_split.py` – works out which hand plays each note in unlabeled MIDI.
- `figures.py` – recognises standard figures and gives them their standard fingering (see below).
- `fingering.py` – fingering planner.
  - `plan_fingering(groups, vpitch, hand=, context=)`.
  - `finger_hand(notes, left, context)` is the convenience entry point.
- `main.py` – the app.
  - `App` owns the window, the synth and the current mode.
  - `MainMenu` has two options: Play (browse → falling notes) and Fingering editor (browse → editor).
  - `Visualizer` is the falling-notes player. Esc returns to the menu.
  - Command line: `main.py file.mid` plays the file, `--edit` opens it in the editor, `--screenshot` saves one frame.
- `common.py` – shared pieces:
  - colours, `Keyboard`, `MidiOut`;
  - `Transport` (clock, seek, speed, synth on/off);
  - tkinter `pick_file` / `save_file_dialog`;
  - `Button`, `Dialog`, `bottom_layout`.
- `editor.py` – `FingeringEditor`, a horizontal piano roll (DAW style) with the keyboard and animated hands below. See the Fingering editor section.
- (old) main.py visualizer notes:
  - `Visualizer.hands` is {RIGHT/LEFT: HandAnimator}, drawn with `draw_hands`.
  - Keys: H toggles hands, F toggles finger numbers.
- `hands.py` – hand skeletons for both hands. Fingering comes from fingering.py; HandAnimator passes hand + song notes as context.
- Test MIDIs:
  - `demo_song.mid`, `right_hand_test.mid`, `right_hand_octaves_test.mid`.
  - `ChopinEtudes_op10_chetn04.mid` (single-track performance).
  - Added by the user: `Etude op25 n06.mid` (thirds; 4 non-hand tracks), `chopin-etude-no-2…op-10.mid`, `Chopin Ballade 1 Full.mid`, `Chopin Prelude 24…`, `chpn-p16.mid`, `Liszt Mazeppa Intro.mid`, `Ravel Ondine Sample…`, `ORIG-MIDI_02_7_10_13…midi` (a performance).
- `Hanon MIDI/` – the 60 exercises with the book's fingering embedded (tracks "Piano, upper/lower", each played twice). Also `hanon_midi_links.csv` and `fingering_report.csv`.
- Score PDFs: Hanon 1–20 / 21–38 as MuseScore vector engravings; the IMSLP scan for 39–60.

## Performance (v26.1.1)
Measured headless (SDL dummy video, 1600x900) on the development container; a desktop is faster, but the
proportions hold. Frame = `update` + `render`, playing (not seeking), 600 frames per section.
- Load (read + hand split + fingering + animators + first frame), unfingered files: Concerto No. 1 (MAESTRO,
  12 min, 6316 notes) 7.5 -> 5.1 s; HR10 5.3 -> 3.4 s; Op. 25 No. 6 3.7 -> 2.5 s. Files that carry their
  fingering load in < 0.6 s (no planning). What changed, all with identical results (hands and fingers of
  all 19 local files, 128,444 notes; 324 poses and frames):
  - `fingering.key_pos` is a table (it was called 6.3 M times per Concerto load);
  - `fingering.hand_range` is cached per (keys, fingers), cleared by `apply_pianist`;
  - `plan_fingering` caches `_transition` per (last fingers, fingers) within a step;
  - `hand_split`: what a candidate split's notes cost regardless of the hand (`_Part`: pitches, span,
    chord and range terms) is worked out once per split, not once per beam entry.
- Frames: the busy parts are the hand solve (`_solve_hand`'s Gauss-Newton, 8 iterations, finite
  differences - it doesn't converge before 8, so it isn't cut short) and the fingertips (`_key_spot`), then
  skin drawing. Exact speed-ups: `_clamp_tip` split into `_clamp_prep` (once per finger and hand) and
  `_clamp_apply` (per point: `_key_spot` tries 22 depths); the Gram matrix summed once per symmetric
  entry; the nail outline's ring precomputed; the keyboard's overlap map precomputed.
- Spikes: a glissando's way back to the finger pose (`_gliss_follow`) simulates up to 3 s of future poses,
  and a far-future pose restarts the speed limit's chain (`LIMIT_RESTART_T`) - 150-500 ms in one frame.
  Now `HandAnimator.prepare` (from `App.run`, in each frame's spare time: what `clock.tick` would have
  slept, less `IDLE_MARGIN_T`; `hands.prepare_hands` shares it between the hands; player and editor
  `idle()`) carries the speed-limited grid on to `WARM_AHEAD_T` 3.5 s ahead, one step at a time (each
  needs the one before - the same order playback would compute it in, so identical poses), and works
  the glissando ways (`_gliss_follow_steps`, resumable) once the warm grid reaches them. `_gliss_neighbours`
  only asks for a way within `GLISS_TRAVEL_MAX_T` of where it applies. Frames over 16.7 ms, per 600:
  HR10 28 -> 1, Concerto 62 -> 0, Op. 25 No. 6 46 -> 4, Winter Wind 37 -> 0, Dante 119 -> 17; mean
  13-15 -> 8-9 ms; worst 300 -> 36 ms.
- Loading in the background (v26.1.2): `common.run_busy(surf, fonts, text, job)` runs the job in a worker
  thread while the main thread draws a progress bar every `BUSY_FRAME_T` (pygame stays on the main thread;
  QUIT and VIDEORESIZE are kept and posted again afterwards, other input dropped; the job's exception is
  raised in the caller). Progress comes from `progress.py`: deep loops call `progress.report(frac)` (the hand
  split's and the planner's per-group loops), and `progress.stage(lo, hi)` maps a part of the job onto its
  share (`hands.load_with_hands`: read 0-0.5 - of which `read_midi` 0-0.15 -, then `hands.build_hands`
  split between the hands by note count). Outside a tracked job `report` does nothing. The app loads the song
  and plans its hands in one job (`App._load`), so the playback setup and the editor get them ready-made
  (`Visualizer(hands=)`, `FingeringEditor(app, song, hands)`); the synced recording decodes in one too.
  Costs 3-5% of the load time (Concerto 5.1 -> 5.25 s), for a window that keeps responding.
- pygame-ce (v26.1.3, `requirements.txt`; plain pygame still works and the tests pass on both): frames
  ~5% faster (mean HR10 8.2 -> 7.7 ms, Concerto 7.9 -> 7.6, Op. 25 No. 6 8.7 -> 8.0); a frame differs only
  in text anti-aliasing and rounded-corner edge pixels (newer SDL_ttf), the hands and notes identical.
  `pygame.midi` is there too. Cython/Numba were not used: the hot spot (`_solve_hand`) is many small
  Python-level steps over dicts of poses, so it would need rewriting, not compiling.

## Equal keys (common.Keyboard, 2026-10-02)
A second key style after PASHKULI's suggestion on PianoClack, toggled by "Keys: ..." on the main
menu (kept as `"keys"` in `pianists/settings.json`, read with `pianist.app_setting`):
- 88 lanes of one width L = keyboard width / (87 + 0.5 + 5/3), with a gap of max(1, L/15) px.
  Every black key and every white key's back (`Keyboard.tails`) fills its lane, so every
  falling note has the same width. The white fronts share their group's lanes evenly:
  C-E = 5 lanes / 3, F-B = 7 lanes / 4 (as in DAW piano rolls and the Osmose). A0's and C8's
  backs reach the keyboard's edges.
- The average white key keeps the realistic width (`white_w`), so the hands keep their scale;
  `key_rects` of a white key is its front, and a finger playing up among the black keys
  (`white_up`) moves over to the key's back (`HandAnimator.key_target`).
- The lanes above white keys are a shade lighter (`LANE_WHITE`); a white-key note's finger
  number is white with a dark outline, a black-key note's stays dark.
- Not done: the "fancy" version's shadows and reflections on the keys.
- Drawing cost (2026-10-02): equal keys cost ~2-5 ms more a frame than realistic (1600x900, headless; more on a
  bigger window): 52 full-height lane rects (1.7 ms), the keyboard's extra shapes (+0.7 ms), and every white-key
  finger number rendered 5 times for its outline. Now, for both styles: the falling-notes background (fill, lane
  lines, lighter lanes) is drawn once into `Visualizer._lanes` and blitted; finger numbers are rendered once per
  (text, colour) (`_finger_img`); the keyboard at rest is cached (`Keyboard._base`, keyed by rect, style and the
  background under it, which shows through the rounded corners) and each frame only the pressed keys are drawn
  again - over the background colour (gap colour for equal keys), with a realistic white key's black neighbours
  and the white keys under those (`draw`'s closure, within one C-E / F-B group) so overlaps come out the same,
  and the felt's shadow re-applied once per redrawn column. Pixel-identical to before (26 player and editor
  frames, both styles); a drawing surface with a clip set falls back to drawing every key. Without hands:
  equal 5.0-5.5 → 2.0 ms, realistic 2.8-3.5 → 2.8-3.2 ms; keyboard alone 0.9 / 1.6 → 0.1 ms.

## Synced recordings (audio_sync.py, 2026-10-02)
- Choosing a MIDI file to play (menu, drop, O in the player) opens `PlaybackSetup`: "Default sound" (the
  MIDI synth, as before) or "Sync an audio file": a speed slider (25-200%, fixed once playing), then the
  audio file (tkinter dialog). The file must last at least `duration / speed`; a shorter one is refused
  with its length and the length needed. The command line skips the screen (`--audio`, `--audio-speed`,
  `--audio-offset`).
- `SyncAudio` decodes the whole file with `pygame.mixer.Sound` (WAV / OGG / MP3 / FLAC, to the mixer's
  format) and keeps `peaks`: the loudest sample in each `PEAK_T` 5 ms slice (numpy, which pretty_midi
  already needs). `play_from(pos)` plays a `Sound` made over a memoryview of the raw samples from that
  point (no seeking API needed, any format). That copies the rest of the samples: 3.5 ms near the end of a
  9-minute 48 kHz recording, 70 ms from its start (380 ms the first time).
- Decoded at its own rate (v26.1.9). `pygame.mixer.Sound` converts to the mixer's rate, and SDL's conversion
  loses time: a 48 kHz MP3 (Chopin's Ballade No. 1, 522.46 s per its LAME header) decoded at the default
  44.1 kHz came out 521.79 s, 0.13% short, so the recording ran steadily ahead of the notes - its final chord
  0.58 s early (pygame 2.6.1 / SDL 2.28 and pygame-ce 2.5.8 / SDL 2.32 alike). `native_rate(path)` reads the
  rate from the header (WAV fmt chunk, MP3 frame headers past any ID3 tag, FLAC STREAMINFO, Ogg Vorbis; Opus
  48 kHz) and `ensure_mixer(rate)` reopens the mixer at it (`allowedchanges=0`: the device converts as it
  plays, in step), on the main thread before the decoding job. Nothing else uses the mixer. `_peaks` takes
  each slice's max and min straight from the samples (identical result; the Ballade loads in 1.3 s, was 3.6).
- Timing: song time t runs at `speed`; the audio heard at t is at `offset + t / speed`. While playing,
  `Visualizer.update` sets t from the wall clock since the last (re)start (`_anchor`, taken once
  `play_from` has returned - before v26.1.9 it was taken first, so the notes ran ahead of the recording by
  the copy's time at every start), not from the capped frame clock, so the two never drift; the audio
  restarts on play, seek, a nudge and the end of a drag. Scrubbing the top bar while playing holds the song
  and restarts the recording once, on release (not a copy per mouse move).
- Left in the files themselves: rendered from the same MIDI, the Ballade's recording (native rate) is 0 ms off
  at the start, ~30 ms at 3 min, ~145 ms at 5:30, ~255 ms at 6:40-8:00 and ~90 ms at the final chord
  (onsets of loud isolated chords). Not a steady rate, so not a clock: the renderer's playback of the tempo
  changes differs from the exported tempo map (which the loader reads exactly - checked against mido). A lead-in before the recording starts (`offset + t / speed < 0`) waits (`_audio_pending`). The
  synth is muted while a recording plays (restored on leaving); M mutes the recording instead; the speed
  keys do nothing.
- The end (v26.1.17): playback stops at `Transport.end_time()` - the last note plus `END_PAD_T` 0.5 s, or with a
  recording `Visualizer.end_time()`, no earlier than where the recording ends, `(length - offset) * speed`
  (+ `END_PAD_T`): a recording that rings on past the MIDI (a final chord's decay, applause) is heard to its end
  instead of being cut off with the last note. The top bar counts to that end; play from there starts over.
- The waveform strip (`WAVE_H` 56 px, under the top bar, the falling notes below it) is on the same
  timeline as the top bar (song times 0..duration across the width): bright where the MIDI is, dimmed
  outside; a translucent progress fill from the left and the playhead. Dragging it moves the recording
  against the notes (`_drag_wave`, incremental; Shift `WAVE_FINE` 10x finer), `,` / `.` nudge 10 ms (Shift
  100 ms); the offset is kept so the recording still overlaps the MIDI. The strip is cached per offset.
- The player starts paused with a recording, so it can be lined up first.

## Sanitizing MIDI files (midi_loader.py, 2026-09-29)
- `load_midi` runs every file through three steps; what they changed is in `song.cleanup` (and printed).
- `read_midi`: the strict pretty_midi parse; if it fails, `repair_smf` rewrites the file's bytes and it is parsed
  again. The repair walks each track event by event (running status included): data bytes over 127 are clipped,
  a truncated or garbled track keeps what came before, meta events are kept only when valid (tempo, time and key
  signatures, text, end of track), system-exclusive messages go, and dropped events' delta times are carried into
  the next one. Repairing an undamaged file changes nothing. pretty_midi's "tempo on non-zero tracks" warnings are
  silenced (harmless).
- `sanitize_instruments`: tracks without notes and percussion go; when a file has piano parts (GM programs 0-7 with
  no other instrument's name, or a piano-ish name: piano, solo, klavier, RH/LH...) AND other instruments, only the
  piano parts stay. Instruments are recognised by program or name, Italian / German score names included
  (Violini, Fagotti, Corni, Timpani...). A file with nothing recognisably piano keeps everything.
- `sanitize_notes`: notes with non-finite or reversed times go; times start at 0; velocities 1-127; pitches
  folded onto the keyboard; the same key struck twice within 5 ms (doubled on two tracks) is one note; a key
  struck again while still down ends the earlier note there.
- Chopin Concerto No. 1 (full score, 19 tracks): the 18 orchestra tracks are dropped, 6316 piano notes kept.
  Ocean (Op. 25 No. 12) failed to load because of a hand_split bug, not the file: the repeated-chord rule and
  the voice-track memory shared a variable (`prev`), so files with several unlabelled tracks crashed with
  "unsupported operand type(s) for -: 'float' and 'str'".
- Export and the fingering markers read the file the way the loader did (`_smf_tracks`): a file that needed
  `repair_smf` is exported from its repaired bytes, so a damaged, cut-off or RIFF-wrapped source exports to a
  clean file with every note marked. `save_fingered_midi` counts marked notes, not marker events, so a note
  doubled on two tracks counts once and the editor's "couldn't be matched" number stays right.

## Fingering stored in MIDI files
- A text meta event just before each note-on, on the note's own track:
  - `F1`..`F5`: finger only (Hanon);
  - `R1`..`R5` / `L1`..`L5`: hand + finger (the editor writes these; a hand marker overrides track names and the split);
  - `R` / `L`: hand only.
- `midi_loader.read_markers` returns (hand, finger).
- `save_fingered_midi(src, dst, notes, fingers)`:
  - rewrites the source file byte for byte, only removing old markers and inserting new ones;
  - matches notes by (pitch, start ±3 ms) using pretty_midi's tempo rules (tempos from track 0 only; a tick-0 tempo replaces 120 bpm).
  - Round trip: all 9 corpus files reload with identical hands and fingers.
- Old format note (Hanon): `F1`..`F5` markers. The loader keys it by (start time, pitch) onto `Note.finger`, and the planner keeps it.
- All 60 Hanon exercises are annotated: 94.5% of notes, both hands.
  - 1–38 were extracted from the vector PDFs.
  - 39–60 were read from the scan and encoded as rules (scratchpad `hanon/rules.py`, `hanon/scales.py`). figures.py reuses these rules.

## Hand separation (hand_split.py)
- A beam search (32 candidates; 24 lost the good split in the Dante Sonata's chord alternations) over onset groups (35 ms tolerance). The lowest k notes go to the LH.
- Initial hand centres come from the upper/lower quartile of the opening notes.
- Costs:
  - **Span** of notes struck together: free to an octave, then 2.5 per semitone. Past 16 it costs `SPAN_OVER` 12 plus 3 per semitone. That is priced as a rolled chord: it is cheaper than a hand leaping two octaves and back in a sixteenth.
  - **Held keys**: a soft 0.8 per semitone beyond the chord's own span, plus 20 past 19.
  - **Load**: more than 5 notes per hand.
  - **Speed** (2026-09-28: the pianist's top speed, see "Top travel speed" below):
    - A hand covering its last notes reaches anything within `HAND_WK` 7 white keys. The least it must travel is the
      gap between where it can be for its last notes and for the new ones; `fingering.travel_time(gap)` must fit in
      `MOVE_SHARE` 0.75 of the time since it last played, else `TOO_FAST` 60·(r + r²), r = the share over.
      This replaced the strain beyond `5 + 55·dt` semitones, which let the LH take the bottom of the RH's chords and
      leap 2½ octaves in 0.12 s.
    - Distance is measured to the farthest new note, so a chord reaching back into the other hand's range counts in full.
    - `SPEED_COST` 0.25 per 40 semitones/s of travel. Relative speed: of two hands, the one that needn't hurry takes the note.
    - A small shift cost.
  - **Chords at speed**: `CHORD_COST·size·(n−1)^1.5·min(3, 0.4/dt)`, `CHORD_COST` 0.5 (was 1.5, which split fast
    repeated chords between the hands). `size` is 0 for shapes of ≤ 5 semitones, 0.5 for ≤ 9 and 1 above.
    Keys the hand's last group was on (`_Hand.last_ps`) count only `REPEAT_SHAPE` 0.3 (v26.1.15): accompaniment
    repeating its shape is little work. Before, Ballade No. 1's LH A#3-C#4 + F#4 at 7:59.7 went 2 + 1, the F#4 to
    the RH thumb in the middle of its chromatic scale. Chords struck late to keep to the top speed: Ballade
    95 → 89, Dante Sonata 50 → 32; the files with hand tracks split as before.
  - **Repeats**: a chord struck again within 0.5 s and split differently from the time before costs 4.
  - **Crowding**: a two-note group split one per hand with fewer than 5 semitones between them costs 1.5 per semitone short. A split third is really one hand's double note.
  - **Trills stay in one hand** (v26.1.18.SNAPSHOT-02): `figures.find_trills` finds them in all the notes before
    the split (each pair of neighbouring keys on its own, so the other hand's notes in between don't break one).
    Every beam path remembers the hand each trill in progress went to (`th`, part of the merge key), and splits
    giving any of its notes to the other hand are dropped - the costs decide which hand, then the other may not
    pitch in. Trills shared between the hands before: Ballade 2 of 5 (306.2 s: one A#3 of ten to the LH), Dante
    1 of 7, Winter Wind 1 of 7; now 0. Late chords unchanged (Ballade, Winter Wind) or moved between hands
    (Dante 32 -> 32); split time unchanged.
    Chord tones aren't trill notes (v26.1.18.SNAPSHOT-03): a note struck with two others within `TRILL_NEAR` 7
    semitones belongs to a chord (one: a double note - trills in thirds stay trills). Chords alternating quickly
    share neighbouring keys (Scarbo 0:35: D#6/E6, G6/G#6) and were pinned to one hand as "trills". Where a
    longer trill takes notes, what's left of a shorter one must still alternate (it left "E6/E6" runs).
  - **Tracks a hand can't follow, alternating**: `_track_weights` also softens the track prior where a track's
    groups come less than `WIDE_T` 0.15 s apart spanning more than `SPAN_MAX` together, `WIDE_STREAK` 4 times
    in a row - both hands in turn, though the file gives them to one (Scarbo 35.4-40.9 s, all in the right
    track: G#5-D#6-G6 / D6-E6-G#6-D7-E7 every 70 ms). The split now gives 127 of its 336 notes to the left hand
    (each hand a small rotation tremolo: LH G#5 / D6-E6, RH D#6-G6 / G#6-D7-E7 - cheaper than a 5-note chord
    in one hand every 140 ms). A single wide leap is left to the speed check. Other two-track files unchanged.
  - **Order** (RH above the LH's centre) and a weak **range** preference.
  - **Voices**: applies to multi-track files without hand names. Moving a track to the other hand within 1.5 s of its last note costs `TRACK_SWITCH` 12. Beam entries carry each track's last hand, and that is part of the merge key.
  - **Track hands** (2026-10-02): every note goes through the split now, not only notes whose track doesn't say.
    A hand the track gives (by name, or two tracks in different registers) is a preference (`prefer`): leaving it
    costs `TRACK_PRIOR` 200 per note - in effect fixed - except within `TRACK_FREE_T` 0.5 s of a move that hand
    would need to make more than `TOO_FAST_TRACK` 50% over the top speed (`_too_fast`, `_track_weights`); there it
    costs only `TRACK_PRIOR_SOFT` 1, so the free hand takes the notes. Each group also tries the tracks' split
    exactly (k = -1), so crossings written into the tracks survive. Only hands from fingering markers are fixed.
    - Why: the ossia cadenza of Rachmaninoff 3 puts both hands' notes of the alternating passage at 57.5-62.6 s in
      the LH track (chord low, octave two octaves up, every 0.08 s). Moves > 25% over the top speed: 47 -> 8 (the
      rest are 30-50% over and were there before); notes struck > 40 ms late in the passage 98 -> 5, in the piece
      146 -> 53. Nothing else in it changes. Op. 25 No. 10 changes one note (44.67 s, where both hands leap at once);
      Winter Wind, Dante, Ocean, Concerto No. 1: unchanged.
    - A lower prior (6, 15, 40 per note) also moved notes where long held basses (pedal notes baked into the MIDI)
      trip the span/load costs - not the free hand's business; a 25% threshold opened windows around mild excesses
      where the soft split made things worse (109.5 s: 0.2 s late).
- Merged-Hanon accuracy: 99.8% (the main error is Exercise 40).
- Op. 10 No. 4: excursions toward the other hand dropped from 13 to 4. The remaining 4 follow the score (broken-octave drops).
- Diagnostics:
  - `v5/excursion.py file 8`;
  - `v5/jumps.py <dir> [semitones]` (fast leaps);
  - `v5/dump.py file t0 t1` (groups with track/hand).
- Op. 25 No. 6 has three layers around 54–56 s and 88–96 s. The tracks are voices: upper thirds, middle dyads and bass. The middle layer goes to whichever hand is less impossible; as written, the passage isn't fully playable by two hands.

## Figure recognition (figures.py)
- `detect(groups, hand, context, vpitch)` returns {id(note): (finger | frozenset of acceptable fingers, weight)}.
- The planner pays `W["figure"]·weight` for leaving a suggestion, so physical limits and context still win when necessary.
- Figures:
  - **Scales** (single-note stepwise runs whose longest one-way stretch is ≥ 6 notes, so 5-finger patterns are left alone;
    a harmonic minor's augmented second counts as a step between steps going the same way, `_is_step` - not a run
    of minor thirds):
    - The key comes from `find_key`: keys whose scale contains the run, scored by Krumhansl-Kessler correlation with the notes ±2 s around, plus a tonic bonus at the start/end. Melodic minor is an option.
    - Standard thumb notes per key and hand (`THUMBS`, `MEL_UP/DOWN`), fingered with `scale_fingering`: count scale steps to the thumb; the RH opening counts 2-3-4.
    - A run that stops at its top (RH) or bottom (LH) and leaps ends on the next finger, not the thumb.
    - A run that carries on a step from a chord just before it in the same hand isn't an opening (no 2-3-4 / LH
      5 start): the thumb on an octave's top note leads straight into it.
    - Weight `W_SCALE` 0.75 for short runs (the PIG pianists finger them freely), `W_SCALE_LONG` 3.0 for a run of
      `SCALE_LONG` 9+ notes one way: a real scale. The learned weights price 4 over the thumb at 9.6 (3 over: 6.4),
      so at 0.75 a fast two-octave scale drifted into 3-2-1 crossings (Concerto No. 1's closing E major, LH).
    - `THUMBS` corrected (2026-10-02): B major and B minor LH 4-3-2-1 with the thumb on E and B (was B and F#, a
      black key); G# melodic minor descending LH thumbs B and E (was F#).
  - **Chromatic**: ≥ 4 semitone steps in one direction. RH 1/3 with 2 on C,F; LH 2 on E,B; RH top C gets 5.
  - **Arpeggios**: close-position runs (thirds/fourths, plus the step 7th→root) over more than 13 semitones, whose pitch classes form a triad or seventh.
    - Root-position triads use Hanon's ARP table (24 keys).
    - Sevenths starting on the root: 1-2-3-4 (LH 1-4-3-2). Dim7/aug take the starting note as root.
    - Otherwise the best cyclic map (`arpeggio_map`: thumb on a white key, widest gap across the crossing).
    - RH top / LH bottom gets 5.
    - Weight `W_ARP` 5.0 (was 2.5: the planner took 3 over the thumb instead of the book's 4, or turned the pattern
      round on the way down).
    - Not detected: open-position arpeggios (fifths, sixths, an octave+ per hand position - Op. 10 No. 1, Op. 25
      No. 12); they're left to the planner.
  - **Repeated notes**: 3-2-1 (4-3-2-1 for groups of 4).
  - **Trills** (`TRILL_MIN_NOTES` 6+ strikes alternating between neighbouring keys, 1-2 semitones, each within
    `TRILL_GAP_T` 0.2 s and after more than `TRILL_CHORD_T` 0.035 s - not seconds struck together; the same
    definition `find_trills` gives the hand split): any strong finger (sets {1,2,3} / {2,3,4}), never 4-5.
  - **Octaves**: in octave passages, 1-5 with 4 on black keys (LH mirrored). A lone octave allows 4 or 5. Broken octaves are handled too.
  - **Chromatic thirds** (monotonic semitone steps): Hanon 50's 12-step table by lower-note pitch class.
  - **Scales in thirds** (monotonic stepwise): Hanon 52's four-third + three-third groups per key (`THIRDS_4GROUP`); weight 5.1, because the crossings they need look awkward note by note.
  - **Trills in thirds**: RH lower 1-3, upper 2-4; LH lower 4-2, upper 3-1.
  - **Thirds inside a hand position**: by rank 1-3, 2-4, 3-5 (LH 5-3, 4-2, 3-1).
  - **Trills in sixths/fourths**: (1,4)/(2,5).
  - **Other sixths**: 1-5 (4 on black).

- **Textbook benchmark** (2026-10-02; tests/test_figures.py, and a fuller script in the session scratchpad): scales
  in all 24 keys, both hands, 2-4 octaves, up and down, from the 2nd degree, with timing jitter, against fingerings
  written out independently (ABRSM / Hanon); root-position arpeggios against the book's table.
  - Scales: suggestions 88.4% -> 100%, planned 86.5% -> 99.4% (the rest: a run's opening 2-3-4, its turns and ends,
    where books differ).
  - Fast (24 notes/s) uneven, after a chord or over a held bass: suggestions 59.7% -> 93.5%, planned 89.3% -> 99.6%.
  - Arpeggios: planned 85.7% -> 93.6% (the rest: LH 3 or 4 over the thumb; the RH ending on its starting finger).
  - Melodic minors and dominant / diminished sevenths: covered by suggestions, no same finger on neighbours.
  - Real pieces: Winter Wind and the demo unchanged; Dante 24 notes, Ocean 36, Concerto No. 1 208 (its scales).

## Fingering planner (fingering.py)
- Right-hand frame (LH mirrored about D4). A beam search (32 candidates) over chords, keeping full history.
- `group_notes`: onsets within `CHORD_TOL` 30 ms form a chord - except a note a step (1-2 semitones) from a lone
  note struck `RUN_SPLIT_T` 8+ ms before it, which is the next note of a fast run (24 notes/s played unevenly put
  neighbours closer than 30 ms, and the "chord" broke the scale). The animator's groups come from here too.
- Not re-measured on PIG / Hanon for the 2026-10-02 figure changes (the data isn't in the cloud sessions): run
  `pig_eval.py` locally. The textbook benchmark above covers Hanon 39 and 41's figures.
- Costs (`W`):
  - Stretch/cramp.
  - `chord_stretch_adj` 1.2: neighbouring long fingers held more than 1.2 keys apart (thirds want 1-3/2-4/3-5).
  - Crossings (`cross_max` 4 keys) and awkward crossings.
  - Same finger on a new key.
  - `same_shape` 1.5: the same fingers on a small shape (under 7 semitones) moving by step, e.g. 4-2, 4-2. Parallel shapes otherwise cost only travel + 0.3 per changed finger.
  - Weak fingers: `finger_4` 0.12, `finger_5` 0.05. Kept light because stronger biases broke natural 5-finger positions; 4-5 trills are handled by figures.
  - Thumb/pinky on black keys, octave 4 on a white key.
  - Leaps beyond `MAX_SPAN` cost a flat `leap` + `leap_dist`.
- `MAX_SPAN` (white keys): 1-2 5.5, 1-3 6.5, 1-4 7.2, 1-5 8.3, 2-3 2.7, 2-4 4.6, 2-5 6.0, 3-4 2.4, 3-5 4.3, 4-5 2.6. Widened so that e.g. A#-C#-G#-A# with 1-2-4-5 fits.
- Physical limits:
  - Chord + held keys must fit; a held key's shape cost is counted separately from the chord's own.
  - `steal`, same finger too fast, and the anchor shift limit through crossings.
- **Hanon book agreement** (file fingering ignored):
  - RH 21.5%, LH 22.8% disagreement (previous version ~30%).
  - 39 scales 1–2%, 40 chromatic 1–3%, 41 arpeggios <1%, 51/53/57 octaves 0%, 52 thirds scales 1%, 56 8%.
  - What remains is mostly deliberate weak-finger training (27, 32, 36, 45–47 trills with 4-5), which is avoided by design.
- **Synthetic check** (`v5/synth.py`): D and Bb major RH, F major LH, E harmonic minor, chromatic RH/LH, E minor / C first-inversion / Ab arpeggios, chromatic thirds, D major thirds, E major octaves, trills, repeated notes, Alberti bass. All come out with textbook fingering.
- **Corpus check** (`v5/corpus_check.py`): all 9 user MIDIs run in 0.1–3 s each. The remaining "impossible" counts are mostly metric artefacts (octave passages, double-third crossings, leaps).

### Fine-tuning the weights (2026-10-02)
- Studio, behaviour page, bottom left: "Fine Tune Fingering Behavior (ADVANCED)" opens a page with every weight
  of the model as a slider (`fingering.FINE_TUNE`: 63 - all of `W`, the figure weights `figures.W_*` as
  `fig:NAME`, and the per-finger `FINGER_STRENGTH` / `BLACK_EASE` as `strength:f` / `black_ease:f`), each
  with its description, its default and its own Reset; "Reset all" at the top of the side panel. `IMPOSSIBLE`
  is left out: it is a threshold the code tests against, not a preference.
- Per pianist: `Pianist.weights` holds only the changed values (saved as `"weights"`, unknown ids dropped,
  negatives clamped to 0). `apply_pianist` applies them last, over the fingering model and the behaviour
  settings, and includes them in its cache key; before that it records each weight's value as the pianist's
  default (`TUNE_DEFAULTS`, `tuned_defaults(p)`), so a reset returns exactly to what the other settings give.
  `apply_pianist` now also restores the figure weights and finger tables to their base values each time.
- Slider ranges (`tune_range`): 0 to about three times the larger of the two models' defaults (at least 1),
  200 steps. `test_finetune` checks every weight is listed - a new weight must be added to `FINE_TUNE`.
- With no fine-tuning the fingering is unchanged (Op. 25 No. 6, HR10, Dante, Winter Wind: 0 of 13,218 notes).

## Fingering editor (editor.py)
- Layout, top to bottom:
  - top bar (Follow pitch / Open / Export / Menu buttons);
  - info line;
  - bar ruler;
  - roll (a vertical key gutter on the left, playhead fixed at 30% of the width, 6 s view, 12 px rows);
  - felt, keyboard and hands.
- Rows fit the song's range when that fits; otherwise the view follows the pitch.
- Editing:
  - Click or drag-box to select.
  - Right-click one note → hand → finger 1-5. The edit is kept exactly; neighbours are unchanged.
  - Right-click several notes → hand. Those notes are freed and re-planned (`_resolve`: `plan_fingering` over the hand's notes ±3 s, with every other finger fixed).
  - Keys: 1-5 set the finger of a single selected note; R/L re-hand the selection (and re-finger it); Ctrl+Z/Y undo/redo; Ctrl+S export.
  - Unsaved changes prompt Export / Discard / Cancel.
- State is kept per note index: `notes`, `finger`, `user_set`.
  - `_rebuild()` makes a new MidiSong (stable order) and `HandAnimator(song, hand, fingering=...)`, which skips the planner.
- `fingering._states`: fingers given by the file or the user are always honoured, even when they aren't monotonic or repeat a finger. Free notes take the fingers that are left.

## Pianists (pianist.py, hand_editor.py) - added 2026-09-26
- `Pianist` fields: name, color, anatomy (19 bone ids: mc1-5, pp1-5, mp2-5, dp1-5; in model units, about 1.088 cm each) and behavior.
- Pianists are saved as JSON in `pianists/` next to the code. The active one is recorded in `pianists/settings.json`, and `pianist.active()` returns it.
- The built-in "Default" pianist can't be edited; duplicate it to change it.
- Bone limits are 70-135% of the default length.
- Hand span is measured in white keys, key centre to key centre: 8 = a 9th, which is the default. Size classes are Small below 7.6, Medium from 7.6 to 8.6, Large above that.
- `hands.HandGeometry(anatomy)` builds the model:
  - each knuckle moves along its metacarpal to match its length; phalanges come straight from `bones`;
  - `rest_reach` scales with the middle finger's length;
  - `reach_scale()` gives each finger pair's reach relative to the default hand, which scales `fingering.MAX_SPAN`;
  - `hand_split` scales its span limits by the hand-span ratio.
- Behaviours and what they control:

| Behaviour | Controls |
|---|---|
| retraction | idle fingers pull back/up; lowers the minimum curl reach |
| antic_hand | `HandAnimator.antic_t` (0.15–0.65 s, 0.4 at the default) / `need_t` (0.06–0.24 s) |
| antic_fingers | `pianist.finger_lead`: -1..1 (2026-09-28: extended below 0). 0..1: head start `prep_max_t` 0.4–1.4 s, travel share 0.9–0.3 (unchanged). Below 0: 0.4 → 0.05 s and 0.9 → 1.0 (just in time); never less than the trip needs at the top speed. C major scale at 8 notes/s: the thumb is tucked under 0.2–0.3 s before its note at 0, 0.055 s at −1. Shown in the studio as the head start in ms |
| tendon_link | linked tendons of 3, 4, 5: how far a follower goes down with its leader (`_tendon_pull`), default 0.6 |
| cross_height | arc when a finger crosses over the thumb |
| lift_height | `PREP` heights |
| cross_turn | `CROSS_TURN_DEG` |
| smoothness | `HandAnimator.smooth_t` (the hand's motion averaged over ± this) |
| early_release | `HandAnimator.early_lift` |
| key_area_near, key_area_far | where on a key fingertips may play; loudness sets the aim within it |
| max_speed | top travel speed of any part of the hand (m/s): hand split, fingering, schedule and animation limit |
| roll_speed | time between rolled-chord notes |
| weak_bias | `finger_4` / `finger_5` weights |
| stretch_bias | stretch vs cross/leap/shift weights; share of the anatomical reach used |
| black_avoid | thumb / little finger on black keys |
| chromatic, repeated, trill, octaves | `figures.PREFS` |

- `fingering.apply_pianist(p)` is called at the start of `plan_fingering` (the default is the active pianist). With the default pianist, results are identical to before: Hanon RH 21.5% / LH 22.8%.
- Chromatic fingerings are pitch-class maps in the RH frame (the LH is mirrored), except "1234", which greedily forms groups of up to four with thumbs on white keys.
- Performance: `HandAnimator.performance` is `[(press, release, note)]`.
  - Chords wider than the physical reach are rolled bottom-up, and their lower notes are released early: in time
    for the hand to stretch on to the top note at the top speed (`travel_time` of how far it is beyond the pair's
    reach), the top note waiting for that, never past the hand's next chord (v26.1.6; they used to be let go 20 ms
    after the top was struck, so for that moment the hand had to span the impossible).
  - Finger early lifts shorten notes.
  - Keys are let go early, or struck late, to keep to the pianist's top travel speed (see below).
  - Notes a hand drops (a 6th note) aren't played.
  - `common.Performance` feeds `Transport`, which sends note on/off events in time order along with the pedal CCs (64/66/67 from `MidiSong.controls`).
  - The keyboard shows the performed keys.
- The Studio has four pages: browser (search, size chips, sort), overview (RGB sliders, stretched/natural view, Save that stays disabled for a new pianist until Anatomy and Behavior have both been visited), anatomy (clickable bones and table, group sliders, span-over-keys view with the thumb on C4) and behavior.
- Badges: the active pianist appears in the bottom-right of the hand area (player and editor), in the menu line and in the Studio's top bar. The pedals sit bottom-left
  (v26.1.16, `common.draw_pedals`): a pedal box with soft, sostenuto and sustain pedals (CC 67, 66, 64, from
  `MidiSong.control_state`), each lit red while down (64+) and dipped a little - drawn 3x and smoothscaled (hard
  edges onto a see-through surface, so no see-through rings), cached per state and size, scaled with the window
  (0.9-1.5 at 800 px high), its bottom above the version text in the window's corner.

## pianoplayer-inspired additions (2026-09-26)
- **Economy of motion** (`fingering.W["velocity"]` = 0.05):
  - Added to `_transition`, on the non-parallel-shape path.
  - After each chord, the fingers are assumed to sit in a relaxed spread around the first note's finger. `RELAXED` holds the offsets in white keys (−2.97, −1.19, 0, 1.19, 2.38), scaled by the pianist's hand span.
  - Each finger's cost is |key − predicted| / (dt + 0.1), divided by `FINGER_STRENGTH` and, on black keys, by `BLACK_EASE`.
  - The pianist behaviour "economy" (default 0.5) scales the weight.
  - Hanon went from 21.5 / 22.8 % to 19.5 / 20.6 %. On a single real PIG piece it went from 63.1 % to 61.6 %, which is inconclusive.
- **Per-note costs**:
  - `plan_fingering(..., fixed=, costs=)`: `fixed` pins fingers by `id(note)`; `costs` returns each chord's cost increment along the chosen path.
  - `score_fingering()` runs the planner with every finger fixed, figures off and beam 4.
- **PIG format**:
  - `midi_loader.read_pig` / `save_pig` / `load_song` (dispatch by .txt).
  - Channel 0 is the RH, 1 the LH; fingers are negative for the LH; substitutions like "3_1" take the first finger.
  - The app opens .txt files. The editor exports PIG when the source is PIG, or when the chosen name ends in .txt.
  - The ruler shows seconds when the file has no bar lines.
- **pig_eval.py**:
  - Reports general / high / soft match rates against every annotator, averaged over pieces.
  - The dataset host (beam.kisarazu.ac.jp) is blocked from the sandbox. The user has to download the dataset and run the script locally.
  - Test files for the sandbox: scratchpad `pigtest/`, which holds `pig_example` from PianoFingering.jl and synthetic fixtures.
- **Editor difficulty mode**:
  - Toggled with the Difficulty button or the D key.
  - Notes are heat-coloured by the fingering's per-note cost, with the hand shown as a stripe. The scale is set so the 90th-percentile cost reads as red (1.0).
  - A band on the ruler marks hard spots, and the hover text shows the difficulty.
  - Recomputed lazily after each edit.

## Skins and loading fix (2026-09-27)
- **Loading fix**:
  - `App.run` limits each frame's clock step to `MAX_FRAME_DT` (1/15 s), and the first frame after a mode switch advances by 0.
  - Before this, a slow load became one huge first frame that started the song ahead of time and sent a burst of late note-ons.
  - `common.show_loading()` shows a "Loading…" notice for file opens in the app, the player's O key and the editor.
- **Hand structure in poses**: `HandAnimator.pose()` and `static_skeleton()` now also return `struct`:
  - `chains` — per finger, from base to tip; fingers 2–5 start at the metacarpal base, the thumb at the CMC;
  - `wrist` (radial and ulnar sides);
  - `arm_end`;
  - the struct is mirrored for the left hand.
- **skins.py** draws flesh around the skeleton, seen from above, in screen space:
  - Fingers are capsules sized by width in inches per finger (`FINGER_W_IN`), taper per segment and a depth scale.
  - The palm is the convex hull of wrist, CMC, knuckles and metacarpal bases, pushed outward and Chaikin-smoothed.
  - Cartoon and gloves draw all outlines first, then all fills, so each hand has a single silhouette.
  - Anti-aliasing uses `pygame.gfxdraw` when it is available.
  - A shadow pass is drawn on a cached SRCALPHA surface, offset by each part's height.
- **The four styles**:
  - `cartoon`: skin with a warm outline, nails, creases, and a white cuff with a button above a coloured sleeve.
    The MCP knuckles (metacarpal to first phalanx) get a short line across the finger, bowed toward the tip
    (`_knuckle`, `KNUCKLE_ARC`; not when the palm is up, nor when the phalanx is seen end-on).
    Nails (`_nail`) follow the distal phalanx's true pitch (its screen length against the height drop): flat, the nail
    sits short of the tip; as the tip curls down it is foreshortened along the finger (to no less than 0.7 r) and its
    free edge slides out to the end of the finger's outline, so the skin in front of it disappears and the squashed
    nail fills the rounded end, clipped to that circle. Curled tips often tuck back under the last knuckle, so the
    direction comes from the middle phalanx once the distal one is seen nearly end-on, and the outline's end is the
    knuckle's circle when that reaches further than the tip's.
    The forward direction is the sum of the finger's other segments, not the middle phalanx alone: strongly curved,
    that one points straight down and its screen direction is sub-pixel noise (it flipped the nail onto the knuckle).
    When it goes out of sight was measured on the author's hand (v26.1.10, a 4K video curling the fingers in and
    out over the keys, frames every 0.1 s): fingers 2-5 show the whole nail while the finger looks ~0.89 of its
    flat length (last phalanx ~45° down), a short dim cap at the very end at ~0.81 (~60°), and none from ~0.69
    (~75°, in a uniform curl). So `NAIL_SHOW_DEG` (45°, 75°): over it the nail shortens toward the end (smoothstep)
    and narrows to 0.55 of its width, and it isn't drawn beyond. The thumb, played on its side, keeps its nail in
    view to 95°-115° (`NAIL_HIDE_DEG`), as in the video.
  - `gloves`: white glove with a black outline and a light halo so it reads on the dark floor, rim shading, three stitches on the back, a puffy cuff and a thin arm.
  - `robot`: white shell plates with gaps, metal joint cylinders with a chrome highlight, dark fingertip caps, a dark thumb housing, a palm plate with a seam and screws, and a white wrist shell above a black cylinder.
  - `skeleton`: the original bone drawing, through `hands.draw_skeletons`.
- **Pianist skin data**: `Pianist.skin` = `{style, colors{style: {slot: rgb}}, finger_width 0.5–1.2 (default 0.75; was 0.7–1.35, 1.0), outline 0–2.5, details, sleeve, shadow}`.
  - The Default pianist uses the cartoon skin.
  - Pianists saved before skins existed load as skeleton, keeping their bone colour.
  - `badge_color` is the primary colour of the current skin.
- **Studio Overview → Appearance**:
  - 2×2 chips to choose the skin;
  - colour-slot chips with RGB sliders;
  - finger thickness and outline sliders;
  - Details / Cuff-sleeve / Shadow toggles.
  - The browser preview, the overview and the span view draw with the skin. The anatomy page stays skeleton so bones remain clickable.

- **Palm width** (2026-09-27): the palm hull includes each knuckle widened by ±(its finger's proximal radius) across its metacarpal. For the index and little fingers it uses ×1.12, and those two edges also carry down the metacarpal toward the wrist. The final inflate is 0.08 in.
- **Finger curvature** behaviour `finger_curve` (Motion; default 40%):
  - Re-based on the author's hand (v26.1.10, video of scales from above, measured against the keys: ~128 px per
    inch at the key surface, the black keys' fronts 2.05 in from the white front edge). Playing, the fingers are
    long and gently arched - first phalanx ~25° down, middle ~45°, last ~55° (the nails show as caps) - the
    fingertips 2.5-3.4 in into the keys for 2-4, the thumb and little finger ~1-1.6 in nearer the front, the
    knuckles over the keys' front edge. The old model hooked them: first phalanx level or rising, middle 60-70°,
    last 65-86°, tips curled back under the last knuckle, the hand hanging in front of the keys.
  - `hands.curl_factor(p) = 1 + 1.16·(1 − c)` scales `rest_reach`, i.e. how far in front of the knuckles the
    fingertips sit: 1.7 at 40% (2.4 in for the middle finger), 2.16 at 0%, 1.0 (the original curved model) at 100%.
  - The hand's height offset is `z_off = 0.6·(c − 0.4) − 0.11` in (knuckles ~1.8 in above a pressed key at 40%).
    It is applied in `world()` and in `base_local`.
  - `REACH_COMFORT` 0.98 for fingers 2-5 (was 0.9: it forbade the nearly straight, tilted playing finger); flatter
    settings may extend up to 0.09 more. `FINGER_COUPLING` 0.6 (was 0.75; ~0.5 measured).
  - `WHITE_DEPTH_IN` {1: 0.35, 2: 1.70, 3: 1.95, 4: 1.80, 5: 0.90} (was 0.40 / 1.20 / 1.45 / 1.30 / 0.90): the
    middle fingers deeper; the thumb and little finger where their resting spots stay within their comfortable
    reach (0.96 / 0.98 of their length) - further in, they pulled the whole hand forward and hooked the others.
  - Result, pressed middle finger at 40%: alone 22° / 48° / 63°, tip 2.3 in ahead and 1.8 in below the knuckle;
    with the thumb down 23° / 46° / 59°. The demo song's pressed fingers: last phalanx 62-70° (was 80-86°), looking
    0.73-0.76 of their length (0.64-0.69). Octave-wide figures (Hanon 60) still curl more: the thumb and little
    finger spend their reach sideways, so the hand comes forward - as a real one does.
  - `static_skeleton(..., curl)` is passed the same factor for the studio's natural view. Its resting targets
    are pulled in to `REACH_COMFORT` (fingers) and `NATURAL_THUMB_REACH` 0.93 (thumb, the default thumb's own
    curve) of each chain's length: a short thumb couldn't reach its resting spot and was drawn straight.
  - Measured mean PIP+DIP bend on Prelude 24: 81° at 0 %, 107° at 50 %, 114° at 100 % (the bend limit).
  - Fingertip accuracy on keys is unchanged or better.
- **Thumb web** (2026-09-27): `skins._Hand._make_web()` builds the first web space (thenar web) as a polygon from the thumb CMC and MCP to the index metacarpal.
  - Its free edge is a quadratic curve from 35% up the thumb's proximal phalanx to the thumb side of the index knuckle.
  - The curve sags toward the wrist by `span·(0.32 − 0.2·spread)`: deep when the thumb is tucked in, nearly straight when it is spread.
  - `fill_palm(..., web_color=)` draws it together with the palm, so it joins the outline and the shadow.
  - Per style: cartoon adds a crease line; gloves add white shading on the inner web; robot uses a dark membrane with a sheen line, and the palm plate is redrawn over its root.
  - Skeleton: `skins.draw_webs()` draws a faint translucent membrane with a stronger free edge before the bones, in the player (`draw_skeletons`) and in the studio (`draw_skeleton`). Dark bone colours get a pale membrane.
- `fingering.chord_pair_cost` handles out-of-order given chord fingers (one finger on two keys, or a crossing inside a chord), which used to raise a KeyError when a group was re-planned in the editor.
- **Hands crossing each other** (2026-09-27): the hand that crosses always goes OVER. That means the LH crossing the RH going up, or the RH crossing the LH going down.
  - `hands.crossing_episodes(song)` (cached as `song._crossings`) is event-driven over onset moments (35 ms groups). Each hand's position is its latest chord, or its next one after 1.5 s of silence.
    - An episode starts when the hands become crossed (LH high > RH low). The crosser is the hand whose NEW notes caused it: the LH playing above the RH, or the RH playing below the LH.
    - When both hands move at the same moment, the crosser is the one further from its ±4 s mean.
    - The episode lasts until the hands are back on their own sides. The other hand playing underneath doesn't switch who is on top.
    - This replaced a sampled-window version that merged the Toccatina's final LH-up and RH-down crossings into one LH-over episode.
    - Episodes are `(t0, t1, top, strength, lead)`. The hand rises from its previous onset (`lead` 0.25–0.6 s) and settles over 0.4 s.
    - `_crossing_lifts` gives each hand its own smoothed lift, so a handover between two crossings is smooth, and the larger lift is on top.
  - `draw_hands` calls `arrange_crossing`: the crossing hand is drawn on layer 1, and its wrist, palm and forearm are raised by up to 0.8 in (`_lift_pose`). The lift fades to 0 at the fingertips and ramps in and out over 0.4 s.
  - `skins.draw_skinned` and `draw_skeletons` draw layer by layer, and the top hand's shadow falls on the lower hand.
- **Idle hand keeps out of the way** (2026-09-28): hands cross only when the notes make them.
  - `idle_weight` (from each hand's merged busy spans): 0 while a key is held; ramps to 1 from 0.35 to 0.85 s after the
    last release; back to 0 between 1.1 and 0.35 s before the next note.
  - `pair_hands(animators)` links the two `HandAnimator`s (called by the player and the editor whenever they are built).
    `pose()` uses `_placed_at`: `_hand_at` shifted sideways by `_idle_shift_parts`, weighted by `idle(self)·(1 − idle(other))`.
  - The shift keeps the idle wrist at least 0.8 hand spans outside the other hand's furthest reach over the next 0.5 s
    (`_clear_line`, which then relaxes at 6 in/s so the idle hand drifts back rather than springs), and no more than
    1.5 spans from the other hand's average place over the last 1.2 s, so it loosely follows. It isn't pushed past
    1.5 in inside the keyboard's end. The other hand is sampled every 0.1 s; shifts are solved at 30 Hz and averaged
    over ±0.25 s. The hand's turn follows the forearm's natural yaw at the new place.
  - Before a hand's first note, when the other hand plays first, the other hand counts as playing from t = 0
    (`_first_t`): the waiting hand is kept on its own side from the start (Dante Sonata's RH used to sit at its first
    notes, below the LH's, crossed until the LH began and then jump aside).
  - Never across the notes it plays next (`_idle_shift_parts`, `_short_of_next`): the pull toward the other hand (the
    1.5-span follow) may only bring the hand toward where it plays next - a hand resting where it plays next stays
    put (Op. 25 No. 6 at 0:25 the RH, resting high up, was drawn ~8 in down and back) - and never past it (16.0-16.8 s
    it overshot its next chord by ~1.5 in and jerked back). Moved as far as its next notes, either way, the hand
    stays there until it plays them: that part is a share of the way there (smoothed as a share in `_placed_at`, so
    the average of shifts taken from different places can't wobble it), weighted by how far into its rest the hand
    is (`_idle_rise`) - not by the other hand's rests, nor faded ahead of the notes. Beyond its next notes it may
    only be pushed out of the other hand's way (that part fades as usual). Before, when both hands rested at once,
    the push faded and the hand fell back past its next chord - crossing the other hand at Op. 25 No. 6 164.9 s.
    Idle-hand reversals (> 3 in/s, idle > 0.1) Op. 25 No. 6 R 15 → 3, L 2 → 2; Dante R 4 → 3, L 2 → 0; crossed
    samples Op. 25 No. 6 1 → 0, Dante 5 → 0.
  - `crossing_episodes` treats a hand with idle weight above 0.5 as out of the way, so a leap by the playing hand past
    the idle one is no longer an episode; a hand playing on the other side still is.
  - Cost: a cold seek into a long idle stretch ~60 ms (memory capped at 8 s, the time to drift across the keyboard);
    playback unchanged (~1–2 ms per frame for both hands).
  - Poses now also carry `t` and `song`.
- **Repeated notes** (2026-09-27; La Campanella 1:36):
  - Fast re-strikes in the planner: below `repeat_t` 0.2 s, the same finger costs up to `repeat_very_fast` 8.0 extra, and a finger change has its `same_key_new_finger` cost waived pro rata. These are physical constants and aren't learned.
  - Figures: a quick repeated pair (under 0.2 s) followed within 0.3 s by an outward leap of 5 or more semitones (RH up, LH down) is played 2-1 and then 5 (`W_REP_LEAP` 25). At 12 it failed for the user's big-handed pianist (stretch_bias 0.8, economy 0.8 raise the leap and velocity costs). A sweep of 32 pianist settings (model × stretch × economy × hand size × weak bias) now gives 22/22 in all of them. Other pairs under 0.15 s are 2-1 at `W_REP`.
  - Runs of 3 or more repeats use `W_REP_RUN` 6, so the learned dislike of changing finger on one key no longer overrides them. Hanon 44 went from 69% to 16% disagreement, 47 from 70% to 10%, and overall RH 23.5 → 21.7, LH 24.7 → 23.1.
  - Broken octaves now skip notes that are part of a quick repeat. The outer note next to a repeated partner takes 5.
  - PIG test is unchanged (both-hands general 66.1%).
  - La Campanella 95–125 s: 25/25 pairs before an octave leap come out 2-1-5; the three followed by a fifth come out 2-1-4.
- **One finger on two keys** (2026-09-27): `fingering.thumb_pair` covers two neighbouring white keys or two neighbouring black keys, including D#-F# and A#-C#. `pinky_pair` covers two neighbouring white keys. `double_ok(p1, p2, f)` checks either.
  - Both are symmetric about D, so they hold in the LH's mirrored frame.
  - `_states(given, ps)` adds (1,1,…) when the two lowest keys in the hand's frame are a thumb pair, (…,5,5) when the two highest are a pinky pair, and both for chords of 5 or more.
  - `group_notes` keeps up to 5 + thumb pair + pinky pair notes, so 6- and 7-note chords are played rather than dropped.
  - Costs: `chord_pair_cost` charges `thumb_double` 7 / `pinky_double` 8 for the pair. `shape_cost` and the held-key steal (`held_map` is now finger → set of pitches) accept pairs.
  - The costs are tuned so plain chords stay normal (C-D-F-A: 1-2-3-5; C-E-G-B-C: 1-2-3-4-5). Doubles appear when a normal fingering would overstretch neighbouring fingers or exceed MAX_SPAN, e.g. C#-D#-G#-C#: 1-1-3-5.
  - `HandAnimator.pair_key` holds {id(note): (lo, hi)}. `_pk(note)` returns the pair, and `key_target` of a pair aims between the two keys. `_roll_wide_chords` measures reach from the pair's centre; between two notes on one finger (the pair's own two keys) there is no reach to keep to (v26.1.16: it asked for `(1, 1)` and loading failed - Scriabin's Fantasy Op. 28, MAESTRO, LH thumb on two keys in chords wider than the hand).
  - Corpus: rolled chords went from 60 to 44, with 64 doubles.
  - PIG test 66.23% (up from 66.17%), Hanon unchanged.
  - **The thumb bridging two black keys** (v26.1.19.SNAPSHOT-01; Brahms Sonata Op. 5, MAESTRO 2006, 3:20, RH
    D#4-F#4 by the thumb): aimed between the keys, the thumb pointed up the white keys between them (E-F). A real
    thumb lies straight across: its tip on the far key (away from the other fingers), its side on the near one.
    - `fingering.thumb_bridge` (a thumb pair of black keys); `bridge_from` makes the stretch from the bridge to the
      next finger count from its far key, in `chord_pair_cost` (the planner's chords) and `shape_cost` (held
      shapes) - the planner's every-pair reach check already did. `HandAnimator._bridge_pair`: `key_target` aims
      a bridge at its far key, `THUMB_BRIDGE_DEPTH_IN` 1.4 in up it (the playing area runs to 1.6), and
      `_roll_wide_chords` measures reach from there.
    - `_key_fix` adds a constraint for a bridge: the straight thumb's MCP joint - on the line from the tip over
      the near key (`THUMB_BRIDGE_NEAR_IN` 0.3 in from its front), the thumb's two outer bones from the tip - must
      be within the metacarpal's reach (across, for the base's height) of the thumb's base: the hand comes in
      over the keys, as a real one does to lay its thumb along them.
    - `_thumb_bridge` poses it (blended in by `_key_weight`): the tip on the far key, straight to the MCP joint (no
      bend at the IP joint - bent there it looked painful), the MCP where that line meets the metacarpal's reach,
      the place of the two nearer along the keyboard.
    - Results: the near key under the thumb's line 0.34 / 0.38 in in from its front (A#-C#, C#-D#), or under its
      MCP joint (D#-F#, LH; 0.19 in short of its centre, the joint 0.8 in wide). Measured from the far key, some
      chords drop the bridge for a plain fingering: the 3:20 chord D#4-F#4-A#4-D#5 is now 1-2-3-5 (29 bridges in
      the sonata, now 19). Top speed kept; the only new off-key notes are the bridges' near keys (the tip is on the
      far one by design).
- **Aspect ratio** (2026-09-27): `common.bottom_layout` draws everything at the bottom to one scale, pixels per white key.
  - The keys are `KEY_LEN_WW` 5.6 widths long and the hand area `HAND_LEN_WW` 7.0 widths tall. Before this, the key height was capped at 20% of the window height, which squashed the keys in wide windows and left the hands mismatched.
  - If keys + hands would take more than `BOTTOM_MAX_SHARE` 0.5 of the height (windows wider than about 16:9), the keyboard gets narrower and is centred.
  - The menu's decorative keyboard and the studio's span view (`_draw_span`: narrower, centred keys when the view is short) follow the same rule.
  - Checked the player, editor, menu and studio at 2560×1080, 1600×900, 1024×768 and 800×1000.
- **Wrist gestures** (2026-09-27): `HandAnimator._find_gestures` finds two kinds of passage.
  - **Bounce runs:** chord groups (2+ keys) identical to their neighbour within `GESTURE_REPEAT_T` 0.45 s, at least 3 in a run. Runs that follow on closely are joined.
  - **Roll runs:** tremolos, i.e. two disjoint key sets alternating A B A B, 4+ strikes, IOI under 0.3 s, centres 3+ semitones apart, also joined across harmony changes. The side is +1 when the little-finger side (in the vp frame) strikes.
  - `_gesture_at(t)` returns (lift px, roll rad, weight), adding up neighbouring runs so there are no jumps.
  - **Bounce:** lift = `bounce_in`·ppi·min(1, dt/0.3)·sin(π·u^0.75), a quick rebound and a slower drop, 0 at each strike.
  - **Roll:** cosine-interpolated between ±R at the strikes; R = `roll_max`·(0.55 + 0.45·min(1, sep/12)).
  - **Rigid hand (revised 2026-09-27):** in a gesture, the hand takes the keys down itself.
    - Hand offset `hb` = (1 − Pw)·lift + Pw·avg_f(−travel − droll(lx_f)), weighted by each finger's `_press_amount` (PRESS_T in, `GESTURE_KEY_UP_T` 0.04 s out). The hand therefore drops by the key depth while a chord is held, and in a roll pivots on whichever side is holding.
    - `world()` adds gw·(hb + droll(lx)). Free tips = gw·(hb + droll + act·own + spare) with a floor at the key surface. `spare` = travel for fingers with no note within 0.45 s.
    - Result: fingertip-to-knuckle height stays constant (probed on Waldstein: 1.71 in pressed and lifted).
    - Bounce chords are released at `bounce_release` = 0.85 − 0.4·wrist_bounce of the way to the next chord (performance rebuilt). The lift happens only in that gap: sin(π·v^0.7), amplitude `bounce_in`·min(1, 0.25 + gap/0.12).
    - Roll profile c = ½(1 + cos(π·u^1.8)): the hand stays rolled onto the side that just struck, then swings over.
  - New behaviours (Motion): `wrist_bounce` (default 0.5 × `BOUNCE_MAX_IN` 1.2 in), `tremolo_rotation` (default 0.5 × `ROLL_MAX_DEG` 40°) and `gesture_finger_action` (default 0.25).
  - Waldstein: 11 bounce runs per hand (the LH opening has 79 chords); Hanon 60 is one long roll run per section.
- **Pedals as switches** (2026-09-27): MAESTRO/Disklavier files send continuous CC64 values, and a resting foot sits around 44–62. Half-pedalling synths then kept the dampers partly lifted while the indicator (threshold 64) read "up".
  - `MidiSong.controls` is now `pedal_switches(raw)`: 0/127, emitted only on change at the 64 threshold. The file's values are kept in `raw_controls`, which the editor passes on when it rebuilds.
  - Waldstein MAESTRO take 1 goes from 9318 events to 814; the sustain pedal first goes down at 21.2 s.
  - Leaving a performance (player or editor: back to the menu, another file, sequential mode, mute, quitting) calls
    `MidiOut.silence()`: pedals up, all notes off (CC123), all sound off (CC120). "All notes off" alone left the
    synth's sustained notes ringing when the sustain pedal was down (2026-10-02).
- **Sequential fingering mode** (editor, 2026-09-27): right-click a single note → "Sequential fingering from here".
  - Steps through only that note's hand: onset groups within `CHORD_TOL`, each chord from its highest note to its lowest.
  - Keys:
    - 1-5 (top row or numpad): finger, keeping the note's hand.
    - M K O ; ' → R1–R5; V D W A LShift → L1–L5.
    - Tab / Shift+Tab skip; Backspace goes back; Ctrl+Z / Y undo or redo and jump to that note.
    - Clicking a note in the sequence continues from it. Esc or Enter exits.
  - Edits use `set_note(..., rebuild=False)`. The hands are rebuilt `SEQ_REBUILD_T` 0.35 s after typing stops (`_rebuild_due` in `update`), and export and leaving flush first.
  - The new note object is added to `self.index`, because `self.notes` is the song's own list.
  - Drawing: an arrow and double outline on the current note, a thin outline on the rest of its chord, and a SEQUENTIAL status line.
  - Test: scratchpad `e2e_seq.py`. The pgpil shim gained K_QUOTE, K_SEMICOLON and K_KP0–9.

## hands.py design (animation)
- The LH is a mirrored RH (axis_x/_mx). Shoulders at E3 (LH) and C5 (RH).
- Joint limits (hand frame, + toward the pinky; see "A real hand's spread" below): full stretch for keys held or struck thumb −85/+34, index −36/+14, middle ±26, ring −22/+24, pinky −12/+55; in the air thumb −64, index −24/+10, middle ±18, ring −15/+16, pinky +24. Press slack 6° (pinky 3°). Wrist deviation 26° CW / 8° CCW.
- Crossing turn: 14° toward the pinky for thumb-under, the other way for finger-over, decided by which note starts later.
- Pose per moment:
  - `_items_at` → (pitch, finger, pull, need, note_start, released).
  - `_solve_hand`: a Procrustes fit + 8 Gauss-Newton steps with saturating penalties.
  - `_hand_at`: a ±0.07 s triangular window on a 1/120 s grid.
- Early lifts for jumps, crossed held keys and wide thumb crossings.
- Fingertips: a free finger leaves at once and arrives early; idle fingers fan out; tips are clamped before IK.
- **Fingers square on their keys** (2026-09-28): the hand solver's limits are soft and its smoothing blends poses
  from moments when other keys were down, so a held key could end up outside its finger's splay/reach and the clamp
  pulled the tip off it (LH 5–2–1 on Bb–F–Ab with 2 repeating: up to 0.6 key off, 0.2 key of twitch per strike).
  - Keys have a playable depth range, not one spot (`_key_depths`): white 0.3 in from the front to 0.45 in past the
    black keys' front; black 0.25–1.6 in from their front. `_key_spot` keeps the tip centred across the key and at
    its usual depth, sliding along the key only as far as the joint limits (with a 1° / 2 % margin) need. The same
    spot is used for the approach, strike, press and release, so a repeated key doesn't wobble.
  - `_key_fix` then nudges the smoothed hand (damped Gauss-Newton, downhill steps only) as little as it takes for
    every held key, any depth along it, to be reachable. Keys count from 0.12 s before the strike to 0.05 s after
    release (eased in), so a finger that has just let go doesn't drag the hand off the keys still held.
  - Demo song: pressed-tip frames more than 0.1 key off went from 228 to 34 (the rest are one-frame handovers into
    leaps). A pinky–index 6th (G#2–F3 in the LH) is just past the default hand's reach and still compromises.
- **Impossible given fingerings and leaps with keys still down** (2026-09-28; Liszt, Dante Sonata fragment, 0:52):
  - The file fingered RH octaves 4–5. Given fingers were always kept, so no hand pose could reach both keys and both
    fingers ended up between them. `plan_fingering(repair=True)` (the player's `HandAnimator` default) keeps a
    chord's given fingers unless together they are impossible (a pair beyond `MAX_SPAN`, i.e. an `IMPOSSIBLE` chord
    cost); then it keeps as many of them as still leave a playable chord and plans the rest. The changed notes are
    in `HandAnimator.repaired` (3 of 3022 in that file). The editor loads with `repair=False` and shows the file's
    fingering as it is; `score_fingering` and the editor's re-planning never repair.
  - A key still held when a new chord starts out of its finger's reach (distance > `MAX_SPAN` + 0.5 white keys, in
    either order) is let go `early_lift` before it, like a crossed held key. Performance MIDI often overlaps leaps
    by a few tens of ms. Dante fragment: pressed-tip frames > 0.3 key off 82 → 29, worst 5.6 → 1.8 keys (the rest
    are the last frames of leaps). Demo song: > 0.1 key 34 → 8, worst 1.86 → 0.28.
- **White keys among black ones** (2026-09-28; Chopin Op. 25 No. 10, chromatic octaves): white keys were always
  aimed at their finger's usual depth near the front, so the hand moved in for every black-key octave and back out
  for every white one (wrist 7.9 in/s in and out). `HandAnimator.white_up` ({id(note): 0..1}) raises a white-key
  note's target (`key_target(pitch, f, note)`) to `WHITE_UP_IN` 0.2 in past the black keys' front: fully within
  `WHITE_UP_T` 0.3 s of a chord of this hand with a black key, fading out by 0.6 s. It is a preference - `_key_spot`
  still slides a finger along the key if its joints need it. The hand-solver items carry their note so the hand is
  placed for the same spot. Op. 25 No. 10, first 10 s: wrist in/out 7.9 → 2.5 in/s (RH), 8.0 → 2.4 (LH); pressed
  tips > 0.1 key off (first 30 s) 61 → 54. 0.3 / 0.4 in further up didn't help.
- **Playing area and loudness** (2026-09-28): the white-up rule above was too much in forte octaves.
  - New behaviours `key_area_near` (0–50%, default 0) and `key_area_far` (30–100%, default 100%) bound where on a
    key a fingertip may play, as shares of its playable length (`WHITE_SPAN_IN` 0.3 in from the front to 0.45 in
    past the black keys' front; `BLACK_SPAN_IN` 0.25–1.6 in from a black key's front): `_key_depths(pk)`.
  - Within that area loudness decides where a finger aims (`_key_depths(pk, note, f)`, used by `key_target`):
    `loudness(v)` is 0 at velocity ≤ `VEL_SOFT` 50 and 1 at ≥ `VEL_LOUD` 110. Soft notes may aim anywhere in the
    area (white keys up among the black ones); loud ones no further up than their finger's usual spot plus
    `LOUD_MARGIN_IN` (0.25 in on white keys, 0.8 in on black ones - long fingers on black keys next to white ones
    need the room), nearer the front, where the key has leverage.
  - The loudness only sets the aim: `_key_spot` and `_key_fix` may still slide a finger anywhere in the pianist's
    area when it can't reach its aim. Capping the slide too put fingertips off their keys (Op. 25 No. 10, first
    30 s: pressed tips > 0.1 key off 54 → 217-285); as an aim it stays at 55.
  - Op. 25 No. 10 (RH, velocity 92-127 after the opening): wrist in/out 4.6 (0-10 s) and 6.0 in/s (20-30 s) - between
    all-front (7.9) and all-up (2.5).
- **Held notes through hand moves** (2026-09-28; Op. 25 No. 10, 0:06 - the held middle voice, RH D5 / LH D3 with 2
  under octaves moving by step): both were let go 0.11 s after the strike (0.83 s written).
  - `_speed_schedule`'s hand-trip rule released every key still held whenever the hand's range moved at all -
    here by 0.08 white keys (0.001 s of travel), because an octave pinned the range to a point. Now a shift under
    `HAND_MOVE_TOL_WK` 0.25 isn't a trip, and on a real trip a held key stays down unless its finger is needed or
    it doesn't fit with the new chord and the keys already kept (`fingering.shape_cost`).
  - `fingering.hand_range`: a stretch the hand can make (`shape_cost` below `IMPOSSIBLE`) spans the positions
    between its conflicting bounds instead of collapsing to the midpoint.
  - The early release for a finger moving to a new key (`_jump_lift`) scales with the move: `LEGATO_LIFT_T` 0.03 s
    up to `LEGATO_STEP_WK` 2.5 white keys, the pianist's full early release from `JUMP_WK` 6. The D5 -> B4 step at
    8.3 s was let go 0.22 s early.
  - Op. 25 No. 10: middle notes held 0.83 / 0.77 s (were 0.11 and 0.51 / 0.24); notes >= 0.4 s held < 80% of their
    length R 135 -> 8, L 159 -> 48. Fingering: bundled MIDIs unchanged, Dante 3 notes.
  - Limit: with a black-key octave on 1-4 (A#4-A#5) the held 2 on D5 is just beyond the animated hand (best pose
    0.2 in short; the index's splay toward the thumb, the 4's and the thumb's limits all bind), so there the index
    sits 0.2-0.3 key off D5. With 1-5 it's exact. Op. 25 No. 10 (0-30 s) pressed tips > 0.1 key off 69 -> 332
    (most of it the held 2s), > 0.3 key 14 -> 80.
  - Fingering for that (2026-09-28): `figures.detect` suggests 1-5 for an octave with an inner note down in the same
    hand (struck with it, or held - `_inner_note_down`, up to 4 s back) instead of the "4 on black keys" rule, and
    `fingering.inner_room_cost` prices it in the planner (the chord itself, and with the keys still held):
    `inner_room` 12 for 1-4 around an inner note, `inner_finger` 4 when the inner note isn't on the finger lying
    over it in the spread hand (2 up to half the octave's width from the thumb, 3 to `INNER_3_TOP` 0.72, 4 above).
    Chords also check every pair of fingers against its reach, not only neighbours (an octave was taken 3-1 with 2
    between them). Static reach with 2 on D5: 1-5 octaves 0.00-0.07 in short, 1-4 up to 0.20.
  - Op. 25 No. 10: all octaves around the held middle notes are now 1-5 (inner notes on 2, or 3 where 2 must step
    on); the middle notes are held 0.83 s. Pressed tips > 0.1 key off (0-30 s) 332 -> 430, > 0.3 key 80 -> 69:
    404 of the 430 frames are with a held inner note, where the static poses exist but the hand solver doesn't
    reach them while it moves between octaves (the little finger is now the one off). Fingering changes: bundled
    MIDIs and Winter Wind none, Dante 106 notes (mostly 4 -> 5 at the top of an octave-wide hand with a key held).
  - Hand solver for these (2026-09-28). Of the frames with a pressed key > 0.1 in out of reach (Op. 25 No. 10,
    0-30 s: R 224, L 228), about 2/3 were shapes no hand pose reached - the octave pins the thumb and the little
    finger at their limits, and the middle finger's ±12° splay couldn't take a D5 between A4 and A5 (1-3-5, 0.12 in
    short) - and 1/3 were reached by `_key_fix` but lost in the ±50 ms smoothing, which mixes in the next chord's
    pose when chords come every 0.14 s.
    - `SPLAY_LIMIT_DEG`: middle finger ±18° (was ±12), ring −15..+16 (was −12..+14).
    - `_smooth_hand_grid` blends ±`LIMIT_SMOOTH_HAND` 3 steps (±50 ms, the hand travelling) with
      ±`LIMIT_SMOOTH_HAND_ON` 1 step (±17 ms) by how much the keys count (`_key_weight`, max over fingers).
    - `_key_fix` gives a finger already down on its key its pressing slack (`PRESS_SLACK_DEG`, as `_limit_tip`
      does) and samples 13 depths along a key (was 7).
    - Tried and dropped (no measurable gain): a pattern search after Gauss-Newton, warm-starting from the last
      step, letting chord-to-chord keys go 35 ms early, a shorter release fade.
    - Frames > 0.1 in out of reach: R 224 -> 61, L 228 -> 84. Pressed tips > 0.1 key off: Op. 25 No. 10 (0-30 s)
      430 -> 182 (> 0.3 key 69 -> 86: LH black-key octaves on 1-5 with 2 held 3 semitones from the thumb, e.g.
      A#1-G2-A#2, still 0.34 in beyond any pose), Winter Wind (20-40 s) 29 -> 29 (> 0.3: 14 -> 1), Dante
      (140-160 s) 33 -> 1, demo 23 -> 25. Winter Wind fingertip jerks 134 -> 275 (the narrower smoothing while
      keys are down; 846 before any smoothing). ~2.4-2.6 ms per hand per frame.
- **Fingers aiming where they go** (2026-09-28; Winter Wind, Op. 25 No. 11, 0:26 - RH 1-5-2-4 with the thumb
  passing under): fingers 2 and 4 on their way to a key 7 keys over stretched out fully (reach 0.99) with their
  splay pinned at the limit and their tip far up the key (2.8 in), then snapped back (0.97 in in one frame).
  - `_clamp_tip`: a target past the splay limit is brought to the nearest point on the limit's line
    (distance × cos of the excess angle), not swung round at full length. The finger points toward its key and
    only stretches as far as that brings it closer.
  - `_key_spot`: the spot along a key is a soft minimum over 21 spots (distance from the aim + a weight × how far
    out of reach), continuous in the hand's pose - firm (`KEY_SPOT_FIRM` 20, 0.03 in) on the key, softer
    (`KEY_SPOT_SOFT` 4, 0.12 in) while travelling (blended by the trip still to go); a key far out of reach fades
    back to its usual spot. The old "nearest reachable spot" jumped up to 1.5 in for a small hand move.
  - The drawn hand is averaged over ±3 limit steps (±50 ms, triangular, no delay) after the speed limit, and
    fingertips in the air over ±3 (a finger on its key stays exactly on it): the limit's grid corners and the
    key fit's corrections made the whole hand lurch.
  - Winter Wind RH 20-40 s: fingertip jerks (second difference > 0.15 in/frame² at 120 fps) 846 → 134, worst
    1.15 → 0.67; frames with a finger > 95% stretched 450 → 166. Pressed tips > 0.1 key off: Op. 25 No. 10 (0-30 s)
    55 → 69, Winter Wind 27 → 29, demo 9 → 23 (worst 0.35). ~2.0-2.4 ms per hand per frame.
- **Top travel speed** (2026-09-28; pianist `max_speed`, default 3.0 m/s, 0.5–5): no part of a hand - wrist or
  fingertip - travels faster, and what is heard is what the hands then play.
  - Model (`fingering.travel_time`): moves ease in and out like the animation's smootherstep, whose peak is 1.875× the
    average speed, so d white keys take 1.875·d·0.0236 m / top speed. `hand_range(ps, fingers)` is where the hand (its
    thumb's natural spot) can be for a chord: each key minus its finger's natural offset, ±1.5 white keys;
    `range_gap` is how far it must move between two chords.
  - Hand split: see "Hand separation" (`TOO_FAST`). Fingering: `speed_cost` in `_transition` - the hand's range gap,
    and any finger moving to a new key, must fit in 0.75 of the time between the chords, else `too_fast` 40 per 100%
    over (a leap costs 4-8), so a fingering that makes the hand teleport loses to almost anything.
  - Schedule (`HandAnimator._speed_schedule`, after the early-lift rules): chord by chord, a finger moving to a new key
    lets go of its last one travel time + `STRIKE_MIN_T` 0.03 s before; when the hand's range has to move, every key
    it still holds does the same for the hand's trip. Keys are held at least `MIN_HOLD_T` 0.04 s (or half the time to
    the new chord); if that isn't enough the chord is struck late (by at most `MAX_DELAY_T` 0.25 s, never past the
    hand's next chord). `self.lead` holds each note's travel time: `_prep_window` starts long trips early and cuts the
    final drop, and `_travel` never spreads a trip over less than it.
  - Animation: `_limited_at` holds the drawn hand to the top speed (wrist movement plus its turn at 7 in out, per
    `LIMIT_GRID_T` 1/60 s step), and `_limited_tip_grid` does the same for each fingertip across the keys (height is
    left alone, so strikes are unchanged). The chains are cached step to step and restart 0.5 s back after a seek.
    This mostly trims `_key_fix`, which snapped a lagging hand into place just before a strike (8.8 m/s).
  - Audio and display: `HandAnimator.performance` (and so `common.Performance`, the synth, the lit keys, the glow and
    now the lit note bars in the player and the editor) carries the late strikes and early releases.
  - Dante fragment (single track, 3022 notes): hand moves over the limit (planned) R 6 → 0, L 33 → 2 (both at 1.0×);
    finger moves R 7 → 1, L 54 → 0. Animated over the whole piece: wrist frames over 3 m/s 0 (was up to 56 m/s in
    140-160 s), fingertips 28 of 48 000 frames (max 4.1, the joint clamp re-applied after the limit). 35 R / 49 L of
    1522 chords struck late (median 15 ms, max 84 ms). Pressed-tip frames > 0.3 key off 101 → 80, worst 1.80 → 0.78.
    156 notes changed hands and 337 fingers; the bundled MIDIs are unchanged. Loading: split 0.8 → 1.2 s (beam 32),
    plan unchanged; ~1.4 ms per hand per frame (demo), cold seek ~60-100 ms.
- **Scale runs: the wrist glides** (2026-10-02): at every thumb crossing the solved hand stepped to the next
  position and the crossing turn swung in and out, so in a scale the wrist went back and forth (C major RH, 3
  octaves up and down: 18-21 lateral reversals; chromatic: 25-26).
  - `_find_runs`: `RUN_MIN_NOTES` 7+ single notes in a row, each a step (1-2 semitones, or 3 right after a step - a
    harmonic minor's augmented second), at most `RUN_GAP_T` 0.3 s apart. Arpeggios (thirds and wider) never qualify,
    so their motion is untouched. `_run_w(t)` eases in and out over `RUN_RAMP_T` 0.15 s.
  - `_hand_at`: in a run the solved hand (position and turn) is averaged over +-`RUN_GLIDE_T` 0.25 s, from poses
    within the run only (`_hand_avg`), blended in by `_run_w`: a steady glide, the turn evened out.
  - Fingers compress to let it: the splay limits widen toward the hand's middle by `RUN_COMPRESS_DEG` (2: +10° toward
    3, 3: ±6°, 4 and 5: 8° / 10° toward the thumb) in the solver, the key fit and the tip clamps (`_splay_at`), and
    `_separate` keeps fingertips 2-5 at least `TIP_GAP_WK` 0.6 white keys apart across the hand (a finger on its key
    stays put; fully applied from a third of the ease-in): compressed, never overlapping.
  - Results (wrist reversals / RMS jerk / RMS turn rate / frames with fingertips 2-5 < 0.3 key apart):
    C major RH 0.065 s 21 → 4 / 126 → 53 / 109 → 51 deg/s / 139 → 0; E major LH 29 → 2 / 134 → 49 / 114 → 44 / 174 → 0;
    chromatic RH 26 → 2 / 96 → 9 / 81 → 23 / 228 → 0. Concerto No. 1, 690-713 s: reversals R 85 → 49, L 96 → 38;
    pressed tips > 0.1 key off R 86 → 69, L 55 → 52. Arpeggios, Winter Wind (20-30 s) and Ocean: identical.
    Frame cost unchanged (5.7 → 5.8 ms for both hands).
- **Tremolos: the hand holds still** (2026-10-02; Dante Sonata's opening LH tremolo Eb-A-Eb, 5-3-1, 11.4-16.9 s;
  Hanon 60): the solved hand followed each strike - the wrist swung ~1.4 in toward every note, 6 times a second, and
  the idle fingers, keeping their places, flicked ~1 in against it (LH wrist path 24.6 in in 5.3 s, each finger
  ~28 in more relative to the wrist).
  - `_find_tremolos` (tremolos and trills, by strike groups): `TREM_MIN_NOTES` 6+ strikes at most `TREM_GAP_T` 0.3 s
    apart, repeating with one period p of 2-`TREM_PERIOD` 4 strikes - every key or chord is struck again p strikes
    later or was p before (so a note that moves on, the middle note going up a semitone, keeps it going), at least
    two different ones. A plain repeated note (p = 1), a scale, an arpeggio, Winter Wind's alternating line and
    Ocean's figures don't qualify. Pieces split by a stray chord (two notes landing together) within a period are
    joined; a tremolo whose lowest or highest key jumps by more than `TREM_JUMP` 4 semitones (Hanon 60 moving to a new
    position) starts again there, so the hold never straddles a move. Each cycle (any `TREM_PERIOD` strikes in a row)
    must fit in the hand: no wider than the thumb-little finger stretch plus `TREM_REACH_EXTRA` 1.25 white keys
    (v26.1.6: Ocean's repeated broken chords, 11-12 keys a cycle, were held still halfway between their notes -
    pressed tips up to 2.4 keys off; Hanon 60's tenths, 9 keys, are still tremolos).
  - `_hand_at`: averaged over +-`TREM_HOLD_T` 0.5 s from poses within the tremolo (`_hand_avg`), eased in and out
    inside it (`_trem_w`, so the hand is free by the next figure).
  - `_key_fix`: inside a tremolo every finger playing in its current cycle (`_trem_note`: its key from the hand's
    last / next `TREM_PERIOD` strikes, the nearer when they differ) counts as on its key throughout, so the fit
    doesn't pull the hand toward whichever finger is down at the moment.
  - Results: Dante LH 11.5-16.8 s wrist path 24.6 → 7.5 in, fingers' lateral path relative to the wrist ~28 → ~11 in;
    pressed tips > 0.3 key off in the tremolos of Dante, Hanon 60 and Winter Wind: 0 → 0 (max 0.3). Scales,
    arpeggios, Concerto No. 1, Winter Wind 20-30 s and Ocean: identical.
- **Fingertips on their keys, whole pieces** (v26.1.6): pressed fingertips more than a quarter key off their key at
  each strike (+10 ms), old -> new. Op. 25 No. 6 RH 4 -> 0, Dante LH 10 -> 0, Winter Wind LH 1 -> 0, HR10 LH 4 -> 0,
  Op. 25 No. 10 RH 4 -> 4 (worst 2.35 -> 0.63 key), Ocean RH 177 -> 80, LH 14 -> 13; Hanon 60 0 / 1, Ossia 4 / 0 unchanged. Left: Ocean's
  fastest arpeggios (fingerings that don't fit at the file's pace), HR10 RH's 14 fingered notes struck between
  back-to-back glissandos (the hand is in the glissando pose), and a thumb on two keys at once (0.63 off by this
  measure, which takes one of the two keys). A rule in `_solve_hand` letting the less important of two targets
  further apart than those fingers can span give way was tried and dropped: one note better.
- **A real hand's spread** (2026-10-02; from a photo of the author's hand stretched over the keys):
  - The span (`HandGeometry.span_units`, which sets `INCHES_PER_UNIT`) was measured with the thumb 50° and the
    little finger 22° out, so the hand was 27% bigger than its span says and never looked stretched. Now thumb 72°,
    little finger 45° (`_THUMB_MAX_ABD`, `_PINKY_MAX_ABD`), and the span view's index −22°, ring +8° - the middle
    three close together, as in the photo. The default hand's middle finger is 3.1 in (was 3.9).
  - Playing: `SPLAY_LIMIT_DEG` is the full stretch, for keys a finger holds or is about to strike; a finger in the air
    keeps `SPLAY_COMFORT_DEG` (the old limits), blended by `_key_weight` (`_splay_at(f, comp, stretch)`) - otherwise
    a little finger heading for a far key stuck straight out sideways.
  - Flattening (`_low(t)`): the smaller hand couldn't reach octaves from its usual height (knuckles above the keys,
    so a finger reaches ~3/4 of its length across). Held / struck keys spread `FLAT_SPAN_WK` 4.5-6.5 white keys
    (outermost) or more lower the knuckles by up to `FLAT_DROP` 0.6, as a pianist flattens the hand for an octave:
    in the reach ranges (solver, key fit, tip clamps) and in the drawn pose.
  - Pressed tips > 0.1 key off (old -> new): Op. 25 No. 10 0-20 s R 131 -> 127, L 160 -> 170; Dante 140-155 s
    R 28 -> 24, L 58 -> 53; Winter Wind 20-35 s 25 -> 23; demo R 1 -> 1, L 47 -> 37. Without flattening and the
    wider stretch: Op. 25 No. 10 R 942, Dante R 819. Arpeggios: the smaller hand turns a little more (C major RH
    turn rate 101 -> 121 deg/s); scale runs keep their glide (C major RH 3 octaves: 2 reversals, no crowded tips).
  - The thumb plays on its side: its nail is drawn narrow and along its outer edge, its knuckle creases on that side
    only (`skins.THUMB_SIDE`, `_thumb_out`).
- **Glissandos** (2026-10-02; glissando.py; Liszt, Hungarian Rhapsody No. 10, 259-289 s):
  - Behaviours (group "Glissandos"): `glissando` on/off (default on), `gliss_gap` 25-100 ms (50), `gliss_min` 3-16
    notes (6), `gliss_merge` 0.25-3 s (1.0).
  - Detection (`glissando.detect`): one hand's notes in playing order, all one colour, each the next key of that
    colour (`colour_index`), one direction, each at most `gliss_gap` after the one before, `gliss_min` or more.
    Performance MIDI is untidy in a real glissando, so a run may skip one key of its colour per step
    (`MAX_SKIP`), notes struck within `SAME_T` 12 ms are ordered the way the run goes, and loose ends (a slower
    note leading in, the last keys flicked past up to `TAIL_SKIP` 3 skipped ones, within `TAIL_GAP` 2x the gap)
    join the run. HR10: 27 glissandos, 479 notes; no other test MIDI has any.
  - Marked glissandos: `Note.gliss`, written as "Rg" / "Lg" markers by the editor's export; always slid, even with
    detection off (`glissando.find`).
  - Episodes (`glissando.episodes`): glissandos less than `gliss_merge` apart with no other note of the hand
    starting in between - the hand stays in the glissando pose through the break - unless the next one starts more
    than `MERGE_MAX_KEYS` 5 keys of its colour from where the last ended (`keys_between`), when the hand may go
    back to its rest position on the way (HR10: 17 episodes).
  - Travel to and from an episode keeps to the top speed. The finger pose takes no notice of glissandos, so it
    may be far from where one ends or begins; blending straight into it moved the hand up to 7.7 m/s (HR10 at
    263.8 s). Now `_gliss_travel` shifts the finger pose onto a follower path (`_gliss_follow`, cached per
    episode end): leaving, from where the glissando pose left the hand once its blend is done, chasing the finger
    pose at no more than `max_speed` with `GLISS_TRAVEL_ACC` 40 m/s² acceleration until back on it; arriving,
    the same worked backwards from where the next episode's blend begins. When the way back from one episode
    ends after the way into the next begins, `_gliss_across` moves the whole (blended) pose straight from one to
    the other over the gap (smootherstep, or a speed-capped trapezoid when that would be too fast).
    The chase has deadlines: back on the finger pose `KEY_FIX_T` before the hand's next fingered strike, and
    leaving it only once its last key before the glissando is let go (`_next_strike`, `_last_release`). When
    the chase can't make it (the follower lagged 2.5-6.7 in off the chords at HR10 267.25-271.5 s, "the hand
    misses its chords"), `_gliss_rush` moves the whole blended pose instead over all the time there is
    (`_gliss_rush_window`: from the glissando's end to `GLISS_ARRIVE_T` 0.03 s before the strike, or from the
    last release to the glissando's start), speed-capped where possible (`_travel_u`); when even that window is
    shorter than the blend, the plain blend is left alone. Straight across (`_gliss_across`) is only for gaps
    with nothing to play.
    `_gliss_w` also takes the stronger of two overlapping blends (a glissando starting while the last one is
    still fading out used to drop the last one: 20 m/s at 270.9 s). HR10 RH, 257-290 s: frames over 3 m/s 102 →
    13 (two at 3.1 / 3.8 m/s between glissandos with a chord in a tight gap; the rest inside glissandos where the recorded notes jump several keys in ~20 ms (the run's end flicks), which
    the contact follows exactly); pressed frames off the finger pose: 0.
  - Top speed inside and around glissandos (v26.1.5; found by scanning every joint at 60 fps through whole pieces,
    which the earlier checks skipped while fully in the glissando pose). `_gliss_schedule` times the glissando notes
    (`gliss_start`): as written, but a step further than the slide can go in its time - at `GLISS_SPEED_SHARE` 0.9
    of the top speed, `GLISS_EASE_PEAK` 1.5x for the eased steps where `_gliss_contact` turns round or pauses - is
    struck late, the run's later keys with it (HR10 261.32 s: a loose end 4 keys on in 18 ms, 1.7x -> 0.9x).
    `_gliss_paths`, `_gliss_end`, `_gliss_starts` and `performance` (`_gliss_performance`) use those times.
    `_speed_schedule` now reaches the first chord after a glissando from where it ended (`_gliss_exits`: the last
    key with finger 2 palm up, else the thumb), striking it late by up to `MAX_DELAY_T` (a chord 0.25-0.4 s after
    a glissando, far away: joints 1.3-3.15x -> 1.03x, the chord 0.08-0.23 s late), but never so late the hand
    can't let go `GLISS_RAMP_T` before the next glissando. `_gliss_rush` keeps off the neighbouring episode's way
    in or out (an arriving rush starting during the last one's blend-out snapped the wrist 34 px in a frame).
    HR10 changed in 58 notes, by at most 21 ms; no other test file changed. All joints over 105% of the top speed,
    whole pieces: none in Op. 25 No. 6, Dante, Winter Wind, Ocean, Ossia, Concerto, the Hanon; HR10 RH 4 frames
    at 270.0 s (knuckles 1.16x, the wrist at 0.99x: the hand turns back from the palm-up pose as it travels, and
    the palm-up wrist sits ~5 keys beyond where the key-range estimate puts it - a known limit).
  - `HandAnimator`: glissando notes are kept out of the fingering and the fingers' timeline (`gliss_ids`; no
    finger, `finger_for` None, `is_gliss`) and added to `performance` at their scheduled times (`gliss_start`).
  - Pose (`_gliss_pose`, blended with the finger pose by `_blend_pose` over `GLISS_RAMP_T` 0.15 s), after a photo
    of the author's hand: the hand flat and turned over, palm up (`GLISS_ROLL_DEG` 180°, turning over as it blends
    in so the point-by-point blend never folds the hand flat), the fingers straight and side by side (knuckles
    drawn in by `GLISS_SQUEEZE`, `GLISS_FINGER_DIR`), the thumb tucked in along the index (`GLISS_THUMB_TIP`);
    tipped down `GLISS_PITCH_DEG` 8° and turned `GLISS_YAW_DEG` 70° so the fingers trail the way it slides (the
    forearm turns `GLISS_ARM_SHARE` 0.6 of that). The backs of the index and middle fingertips - the nails - rest
    on the keys at the contact point (no part of a finger below them) - `GLISS_WHITE_IN` 0.8 in up a white key,
    `GLISS_BLACK_IN` 0.5 in into a black one - which follows the notes (`_gliss_contact`; through a break it travels
    and turns round, eased). Only the thumb's nail is drawn (`struct["nail_hide"]` = fingers 2-5, kept once a blend
    is a third of the way in; `skins._draw_cartoon`). The bones are in the same order as `_finger_pose`'s, so the
    two blend point by point. That is for sliding toward the little finger (RH up, LH down). Toward the thumb
    (RH down, LH up) it is the thumb method, after a photo: palm down, fingers 2-5 curled into a fist with the
    middle joints down on the keys (`GLISS_CURL`), the thumb straight along the fist (`GLISS_THUMB_DIR`), the
    hand turned so the thumb lies along the keys, tipped down `GLISS_THUMB_PITCH_DEG` 4°, the thumb's nail on
    the contact point - up to `GLISS_THUMB_IN` 2.0 in up a white key (no further than the black keys' front), so
    the fist's knuckles are over the keys, but pulled back so the fist's furthest knuckle stays `GLISS_FIST_CLEAR`
    0.45 in short of the black keys' front (it collided with them otherwise). The thumb's nail is drawn flush with the thumb's
    outer edge (`struct["thumb_edge"]`, `skins.THUMB_EDGE`) to show it on the keys. The forearm
    leans `GLISS_THUMB_ARM_DEG` 18° from straight up the keys toward the way the hand slides (the elbow trailing,
    as if pushing the thumb along; replacing the shoulder's natural lean, which put the right arm the other way
    at the low end).
    Palm up, the skin packs fingers 2-5 flush side by side, joint by joint, one outline width apart
    (`struct["flush"]`, `skins._flush`, using the skin's finger widths), and the thumb is tucked across the palm
    below the knuckles (`GLISS_THUMB_TIP`, bending on the palm's side), drawn with its own outline over the palm;
    its nail, underneath, shows as a sliver along its edge toward the fingers (`skins._thumb_sliver`,
    `THUMB_SLIVER`). Both are one family
    (u = smoothed (d+1)/2 of `_gliss_contact`'s direction d), so turning round inside an episode morphs from one
    to the other. Palm up, `struct["palm_up"]` is set and the cartoon skin draws the heart, head and life lines
    (`skins._palm_lines`, quadratic curves in palm coordinates between the index/little knuckles and the wrist
    sides - the wrist sides swapped when the hand is turned over, as the forearm isn't) instead of the back's web
    crease. (Tried first: the hand rolled 78° onto its side with straight fingers; then the fingertips and thumb
    pinched to one point - the thumb's occlusion couldn't be drawn well.)
  - Player and editor show "g" for a glissando note; the editor marks / unmarks a selection that passes
    `glissando.is_string` (right-click "Glissando" / "Not a glissando", or G).
- **Chromatic runs without twitching** (v26.1.14; Chopin Ballade No. 1, RH upward chromatic scale at 7:58,
  1-3 with 1-2-3 at E-F-F# and B-C-C#): the index (and middle) finger stopped and hopped on, or stepped back,
  several times. Its smoothed path (`_smooth_tip_grid` looks ahead) ran into the middle finger, still holding the
  key before, and `_separate` held it back until that key let go - then let it jump in one frame.
  - `_blocked_until(f, i)` (cached in `_prep_cache`): when a finger 2-5 leaves note i, the latest end of the keys
    held, in that time, by the fingers on the side it is heading for, short of its next key. `_prep_window` starts
    the trip no earlier (and still at least `STRIKE_MIN_T` before the note): the finger waits beside its neighbour,
    then goes.
  - `_separate` pushes by mobility: a pressing finger or one fixed on its key (`_key_weight`) doesn't give way, a
    free one does, in proportion, instead of half each.
  - `_shaped_rests` blends its exceptions (the thumb past the index, a finger the thumb is passing) in over half a
    white key instead of switching them on and off.
  - Results (direction reversals > 3 px per frame, at 60 fps): the 7:58 scale index/middle 4/4 → 0/0; a synthetic
    1-3 chromatic scale at 60 ms a note 5/6 → 0/0 (test); the whole Ballade RH 2: 58 → 49, 3: 30 → 25, LH unchanged.
- **Chromatic scales, octaves and runs that join** (v26.1.15; Ballade No. 1: RH chromatic scales at 5:25, 5:29
  and 7:58, chromatic octaves in both hands at 8:30):
  - The index still stepped back each time it let go of a key next to the middle finger's (C next to C#): `_separate`
    wanted `TIP_GAP_WK` between them, more than the keys are apart, so it pushed the freed finger back, and let
    it spring forward when the middle finger lifted. Next to a finger on its key, a free one now keeps no further off
    than its own last or next key does (`_near_keys_x`; keys on the far side ignored - played before the hand moved
    on), never nearer than `TIP_GAP_MIN_WK` 0.4.
  - Fingers' first keys too: `_blocked_until` covers a finger's first note (neighbours' keys up to
    `PREP_LOOKBACK_T` 2 s back, any neighbour on the target's far side in the way).
  - A held key that a later chord's finger has to cross (`(g > f) != (vm > vn)`, or out of reach) is let go early
    for every chord struck while it is held, not only the next one: in a 1-3 scale the middle finger's legato G#
    overlapped the index's A# after the thumb's A (2 over 3); with no time left it hovered, then jumped 45 px.
  - `_find_runs` also takes octaves and double notes: groups of the same size (up to two notes) whose notes all
    move together by the same step. Chromatic octaves 1-4 / 1-5 turned the hand ~11° every octave (one fit per
    shape) and stepped the wrist back.
  - In a run `_key_fix` weighs a turn `RUN_TURN_STIFF` 3 times as much: it reaches the keys by moving the hand.
  - Results (fingertip direction changes over 2-3 px per frame, still frames between ignored; total turning; v26.1.14
    → now): 7:58-8:01 26 → 0, 108° → 49°; 5:25 4 → 3, 25° → 17°; 5:29 13 → 8, 49° → 38°; RH octaves 162° → 99°
    (wrist path / distance 1.32 → 1.15); LH octaves 116° → 64°. Op. 25 No. 6 RH turning -24%, Op. 25 No. 10 LH
    -9%. Synthetic 1-3 chromatic scale: index 6 → 0 (test). Top speed and fingertips on keys unchanged (Concerto,
    Dante, Ballade).
- **Linked tendons of fingers 3, 4 and 5** (v26.1.18.SNAPSHOT-01; the author's rules): 3 curving down takes 4
  with it, 4 takes 3, and 5 takes 3 and 4 - unless the follower is reaching for a key of its own. In
  `_tips_at`, after the targets and before `_limit_tip`, `_tendon_pull` lowers a follower's tip height by
  `tendon_link` (pianist behaviour, default 0.6) x how far down its leader is (0 at its hover height,
  `_hover`, 1 at the key tops; the deepest leader counts) x how free it is (1 - `_reaching`: 1 while pressing,
  rising with its trip to the next key, `_travel`, 1 from the strike on) - toward `TENDON_FLOOR_IN` 0.15 in
  above the key tops, never onto them. Only heights change. A finger repeating one key at the default: its
  follower's tip from 0.54 in down to 0.31 in (0.38 on average); 2, and 5 when 3 or 4 leads, untouched (tests).
- **The thumb under the hand stays hidden** (v26.1.14): drawn lower than the palm, the thumb passing under was
  still seen between the fingers (the palm covers up to the knuckles only). `skins._tuck_zone`: palm down, the
  thumb below the palm and some of it beyond the knuckle line between the index's outer edge and the little
  finger's (a quad reaching 6 in toward the tips). There `_draw_tucked` shows the hand drawn again without its
  thumb (`_Hand.hide_thumb`) over a copy of what was under the hand (`_Pen` takes an origin; soft edges drawn onto
  a see-through layer left see-through rings, so the copy is opaque), masked to the zone; outside it the whole
  hand. The thumb's shadow is left out too (it is in the hand's own). Cost: ~2.6 ms on such frames (1920x1080).
- Checks: Hanon off-key ≈ 0.1%. Presto Chopin RH ≈ 10% off-centre frames: an animation speed limit, not fingering.

## Tests
- `pytest` from the repository root (`pip install -r requirements-dev.txt` first).
- `tests/conftest.py` makes pygame headless (SDL dummy video/audio drivers) and points `pianist.FOLDER` at a temporary folder, so tests never touch your saved pianists.
- `tests/test_core.py` covers loading, pedal switches, scale fingering, thumb/pinky pairs, given fingers, hand poses, crossings, bounce and tremolo detection.
- `tests/test_ui.py` drives the menu, player, studio and the editor's sequential fingering mode with scripted key events.
- Only the three small MIDIs in `MIDIs/` are tracked; the rest of your MIDI collection, the PIG dataset, score PDFs and skin reference images stay local (see `.gitignore`).
- Benchmarks that need local data:
  - `python pig_eval.py <FingeringFiles folder>` - match rates against the PIG annotators.
  - `python learn_weights.py <FingeringFiles folder>` - re-tunes the planner weights on PIG pieces 031-150.
- Earlier development ran in a cloud sandbox with stub versions of pygame and pretty_midi; the scripts named in older notes below (`evalh.py`, `corpus_check.py`, `e2e_*.py`, ...) were not carried into the repository.

## Next
- Fingering:
  - Melody over accompaniment in one hand (outer fingers for the top voice, e.g. Op. 10 No. 2's chromatic line with 3-4-5) when the chord notes aren't held in the MIDI.
  - More figure types: broken-chord patterns, tremolos, double-sixth scales, octave scales with legato fingering.
  - Rolled chords wider than the hand (fingering/animation side).
- Animation: faster reshaping in presto passages.

## The single-file build (build.py, paths.py, v26.1.11)
- `python build.py` runs Nuitka `--onefile` on `main.py` -> `dist/Hand-thesia.exe` (`.bin` elsewhere). Options:
  the tk-inter plugin (file dialogs; skipped with a warning when the building Python has no tkinter),
  `CHANGELOG.md` and `assets/icon.png` as data files, `pig_eval` / `learn_weights` / tests not followed, pytest
  and setuptools left out, pretty_midi's unused soundfont (`*.sf2`, 6 MB) left out, Windows console disabled
  (`--console`: forced), the icon from `assets/icon.ico`, product and file versions from `VERSION`
  (`v26.1.11` -> `26.1.11.0`). `--onefile-tempdir-spec={CACHE_DIR}/Hand-thesia/{VERSION}`: unpacked once per
  version and reused (a ~1 s start), not to a fresh temp folder each launch.
- Tcl/Tk (v26.1.12): Nuitka's tk-inter plugin only looks for the script libraries in the usual folders
  (`<prefix>\tcl\tcl<version>\init.tcl` or a zip beside it). The python.org Python 3.14.8 for Windows failed
  with "Could not find Tcl": Tcl/Tk 9 keep the library inside the DLL (`info library` is `//zipfs:/...`).
  `build.tcl_tk_options()` runs `build.py --probe-tcl-tk` in a subprocess of the building Python: Tcl's
  `info library` and Tk's `tk_library` (Tk opens a hidden window; with no display, the folder beside Tcl's,
  `tcl8.6` -> `tk8.6`), a `//zipfs:` one copied out with Tcl's `file copy` to `build/tcl-library/{tcl,tk}`,
  each passed as `--tcl-library-dir` / `--tk-library-dir` once it holds `init.tcl` / `tk.tcl`. Checked on
  Linux with Tcl/Tk 8.6 (the plugin bundles 227 + 88 files; the compiled program opens its "Open MIDI file"
  dialog under Xvfb); the copy-out branch is tested with a stand-in interpreter (no Tcl 9 here).
- `paths.py`: `COMPILED` (Nuitka's `__compiled__`), `RESOURCE_DIR` (beside the modules: bundled, read-only),
  `DATA_DIR` (from source the same folder, so `pianists/` is where it always was; compiled, a `pianists` folder
  beside the .exe if one exists - portable - else `%APPDATA%\Hand-thesia`, `$XDG_DATA_HOME/hand-thesia`
  elsewhere). `pianist.FOLDER` and the changelog-seen marker live under `DATA_DIR`. A folder from before the
  rename to Hand-thesia (v26.1.16; `Piano Animator`, `piano-animator`) is moved to the new name the first time,
  unless the new one exists; if the move fails, the old folder is used.
- `main.py`: compiled and with no console (stdout missing or not a terminal), output goes to
  `DATA_DIR/hand-thesia.log` (started afresh past 1 MB, a header per launch); an uncaught error is logged with
  its traceback and shown in a tkinter message box naming the log, exit code 1. The window icon is
  `assets/icon.png`; `PYGAME_HIDE_SUPPORT_PROMPT` hides pygame's banner; `--version`.
- Checked by building on Linux (Nuitka 4.2.2, gcc; ~4 min, 27 MB): run from another folder it renders the player
  (`--screenshot`), unpacks to the cache folder, logs to the data folder, honours a portable `pianists` folder, and
  logs a forced crash. The Windows build uses the same options plus the icon and console flags.
