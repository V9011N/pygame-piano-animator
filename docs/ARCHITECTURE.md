# Pygame Piano Animator - architecture notes

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
  - **Repeats**: a chord struck again within 0.5 s and split differently from the time before costs 4.
  - **Crowding**: a two-note group split one per hand with fewer than 5 semitones between them costs 1.5 per semitone short. A split third is really one hand's double note.
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
  - **Trills** (≥ 6 alternations of neighbouring notes): any strong finger (sets {1,2,3} / {2,3,4}), never 4-5.
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
| antic_hand | `ANTIC_T` / `NEED_T` |
| antic_fingers | `pianist.finger_lead`: -1..1 (2026-09-28: extended below 0). 0..1: head start `PREP_MAX_T` 0.4–1.4 s, travel share 0.9–0.3 (unchanged). Below 0: 0.4 → 0.05 s and 0.9 → 1.0 (just in time); never less than the trip needs at the top speed. C major scale at 8 notes/s: the thumb is tucked under 0.2–0.3 s before its note at 0, 0.055 s at −1. Shown in the studio as the head start in ms |
| cross_height | arc when a finger crosses over the thumb |
| lift_height | `PREP` heights |
| cross_turn | `CROSS_TURN_DEG` |
| smoothness | `HAND_SMOOTH_T` |
| early_release | `EARLY_LIFT_T` |
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
  - Chords wider than the physical reach are rolled bottom-up, and their lower notes are released early.
  - Finger early lifts shorten notes.
  - Keys are let go early, or struck late, to keep to the pianist's top travel speed (see below).
  - Notes a hand drops (a 6th note) aren't played.
  - `common.Performance` feeds `Transport`, which sends note on/off events in time order along with the pedal CCs (64/66/67 from `MidiSong.controls`).
  - The keyboard shows the performed keys.
- The Studio has four pages: browser (search, size chips, sort), overview (RGB sliders, stretched/natural view, Save that stays disabled for a new pianist until Anatomy and Behavior have both been visited), anatomy (clickable bones and table, group sliders, span-over-keys view with the thumb on C4) and behavior.
- Badges: the active pianist appears in the bottom-right of the hand area (player and editor), in the menu line and in the Studio's top bar. A "Ped." indicator sits bottom-left.

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
    Nails (`_nail`) follow the distal phalanx's true pitch (its screen length against the height drop): flat, the nail
    sits short of the tip; as the tip curls down it is foreshortened along the finger (to no less than 0.7 r) and its
    free edge slides out to the end of the finger's outline, so the skin in front of it disappears and the squashed
    nail fills the rounded end, clipped to that circle. Curled tips often tuck back under the last knuckle, so the
    direction comes from the middle phalanx once the distal one is seen nearly end-on, and the outline's end is the
    knuckle's circle when that reaches further than the tip's.
    The forward direction is the sum of the finger's other segments, not the middle phalanx alone: strongly curved,
    that one points straight down and its screen direction is sub-pixel noise (it flipped the nail onto the knuckle).
    Past straight down the nail turns out of sight over the end: its minimum length shrinks between 95° and 115° of
    pitch (`NAIL_HIDE_DEG`), and it isn't drawn beyond that.
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
- **Finger curvature** behaviour `finger_curve` (Motion; default 40%, where 63.6% is the original look):
  - `hands.curl_factor(p) = 1 + 1.1·(0.636 − c)` scales `rest_reach`, i.e. how far in front of the knuckles the fingertips sit. The range runs from 1.7 (flat) to 0.6 (curved).
  - The hand's height offset is `z_off = 0.9·(c − 0.636)` in. It is applied in `world()` and in `base_local`.
  - Flatter settings may extend up to 0.09 more of the finger length than `REACH_COMFORT` allows.
  - `static_skeleton(..., curl)` is passed the same factor for the studio's natural view.
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
    `pose()` uses `_placed_at`: `_hand_at` shifted sideways by `_idle_shift`, weighted by `idle(self)·(1 − idle(other))`.
  - The shift keeps the idle wrist at least 0.8 hand spans outside the other hand's furthest reach over the next 0.5 s
    (`_clear_line`, which then relaxes at 6 in/s so the idle hand drifts back rather than springs), and no more than
    1.5 spans from the other hand's average place over the last 1.2 s, so it loosely follows. It isn't pushed past
    1.5 in inside the keyboard's end. The other hand is sampled every 0.1 s; shifts are solved at 30 Hz and averaged
    over ±0.25 s. The hand's turn follows the forearm's natural yaw at the new place.
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
  - `HandAnimator.pair_key` holds {id(note): (lo, hi)}. `_pk(note)` returns the pair, and `key_target` of a pair aims between the two keys. `_roll_wide_chords` measures reach from the pair's centre.
  - Corpus: rolled chords went from 60 to 44, with 64 doubles.
  - PIG test 66.23% (up from 66.17%), Hanon unchanged.
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
    starting in between - the hand stays in the glissando pose through the break (HR10: 15 episodes).
  - `HandAnimator`: glissando notes are kept out of the fingering and the fingers' timeline (`gliss_ids`; no
    finger, `finger_for` None, `is_gliss`) and added to `performance` at their written times.
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
    the contact point - `GLISS_THUMB_IN` 2.0 in up a white key, so the fist's knuckles are over the keys. Both are one family
    (u = smoothed (d+1)/2 of `_gliss_contact`'s direction d), so turning round inside an episode morphs from one
    to the other. Palm up, `struct["palm_up"]` is set and the cartoon skin draws the heart, head and life lines
    (`skins._palm_lines`, quadratic curves in palm coordinates between the index/little knuckles and the wrist
    sides - the wrist sides swapped when the hand is turned over, as the forearm isn't) instead of the back's web
    crease. (Tried first: the hand rolled 78° onto its side with straight fingers; then the fingertips and thumb
    pinched to one point - the thumb's occlusion couldn't be drawn well.)
  - Player and editor show "g" for a glissando note; the editor marks / unmarks a selection that passes
    `glissando.is_string` (right-click "Glissando" / "Not a glissando", or G).
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
