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
- A beam search (24 candidates) over onset groups (35 ms tolerance). The lowest k notes go to the LH.
- Initial hand centres come from the upper/lower quartile of the opening notes.
- Costs:
  - **Span** of notes struck together: free to an octave, then 2.5 per semitone. Past 16 it costs `SPAN_OVER` 12 plus 3 per semitone. That is priced as a rolled chord: it is cheaper than a hand leaping two octaves and back in a sixteenth.
  - **Held keys**: a soft 0.8 per semitone beyond the chord's own span, plus 20 past 19.
  - **Load**: more than 5 notes per hand.
  - **Speed**:
    - A strain beyond `5 + 55·dt` semitones: 1.0·ex + `LEAP_IMPOSSIBLE` 0.15·ex². Quadratic, so impossible round trips are ruled out.
    - Distance is measured to the farthest new note, so a chord reaching back into the other hand's range counts in full.
    - `SPEED_COST` 0.25 per 40 semitones/s of travel. Relative speed: of two hands, the one that needn't hurry takes the note.
    - A small shift cost.
  - **Chords at speed**: `CHORD_COST·size·(n−1)^1.5·min(3, 0.4/dt)`. `size` is 0 for shapes of ≤ 5 semitones, 0.5 for ≤ 9 and 1 above.
  - **Crowding**: a two-note group split one per hand with fewer than 5 semitones between them costs 1.5 per semitone short. A split third is really one hand's double note.
  - **Order** (RH above the LH's centre) and a weak **range** preference.
  - **Voices**: applies to multi-track files without hand names. Moving a track to the other hand within 1.5 s of its last note costs `TRACK_SWITCH` 12. Beam entries carry each track's last hand, and that is part of the merge key.
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
  - **Scales** (single-note stepwise runs whose longest one-way stretch is ≥ 6 notes, so 5-finger patterns are left alone):
    - The key comes from `find_key`: keys whose scale contains the run, scored by Krumhansl-Kessler correlation with the notes ±2 s around, plus a tonic bonus at the start/end. Melodic minor is an option.
    - Standard thumb notes per key and hand (`THUMBS`, `MEL_UP/DOWN`), fingered with `scale_fingering`: count scale steps to the thumb; the RH opening counts 2-3-4.
    - A run that stops at its top (RH) or bottom (LH) and leaps ends on the next finger, not the thumb.
  - **Chromatic**: ≥ 4 semitone steps in one direction. RH 1/3 with 2 on C,F; LH 2 on E,B; RH top C gets 5.
  - **Arpeggios**: close-position runs (thirds/fourths, plus the step 7th→root) over more than 13 semitones, whose pitch classes form a triad or seventh.
    - Root-position triads use Hanon's ARP table (24 keys).
    - Sevenths starting on the root: 1-2-3-4 (LH 1-4-3-2). Dim7/aug take the starting note as root.
    - Otherwise the best cyclic map (`arpeggio_map`: thumb on a white key, widest gap across the crossing).
    - RH top / LH bottom gets 5.
  - **Repeated notes**: 3-2-1 (4-3-2-1 for groups of 4).
  - **Trills** (≥ 6 alternations of neighbouring notes): any strong finger (sets {1,2,3} / {2,3,4}), never 4-5.
  - **Octaves**: in octave passages, 1-5 with 4 on black keys (LH mirrored). A lone octave allows 4 or 5. Broken octaves are handled too.
  - **Chromatic thirds** (monotonic semitone steps): Hanon 50's 12-step table by lower-note pitch class.
  - **Scales in thirds** (monotonic stepwise): Hanon 52's four-third + three-third groups per key (`THIRDS_4GROUP`); weight 5.1, because the crossings they need look awkward note by note.
  - **Trills in thirds**: RH lower 1-3, upper 2-4; LH lower 4-2, upper 3-1.
  - **Thirds inside a hand position**: by rank 1-3, 2-4, 3-5 (LH 5-3, 4-2, 3-1).
  - **Trills in sixths/fourths**: (1,4)/(2,5).
  - **Other sixths**: 1-5 (4 on black).

## Fingering planner (fingering.py)
- Right-hand frame (LH mirrored about D4). A beam search (32 candidates) over chords, keeping full history.
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
| antic_fingers | `TRAVEL_SHARE` / `PREP_MAX_T` |
| cross_height | arc when a finger crosses over the thumb |
| lift_height | `PREP` heights |
| cross_turn | `CROSS_TURN_DEG` |
| smoothness | `HAND_SMOOTH_T` |
| early_release | `EARLY_LIFT_T` |
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
  - `gloves`: white glove with a black outline and a light halo so it reads on the dark floor, rim shading, three stitches on the back, a puffy cuff and a thin arm.
  - `robot`: white shell plates with gaps, metal joint cylinders with a chrome highlight, dark fingertip caps, a dark thumb housing, a palm plate with a seam and screws, and a white wrist shell above a black cylinder.
  - `skeleton`: the original bone drawing, through `hands.draw_skeletons`.
- **Pianist skin data**: `Pianist.skin` = `{style, colors{style: {slot: rgb}}, finger_width 0.7–1.35, outline 0–2.5, details, sleeve, shadow}`.
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
- Joint limits (hand frame, + toward the pinky): thumb −64/+34, index −24/+10, middle ±12, ring −12/+14, pinky −8/+24. Press slack 6° (pinky 3°). Wrist deviation 26° CW / 8° CCW.
- Crossing turn: 14° toward the pinky for thumb-under, the other way for finger-over, decided by which note starts later.
- Pose per moment:
  - `_items_at` → (pitch, finger, pull, need, note_start, released).
  - `_solve_hand`: a Procrustes fit + 8 Gauss-Newton steps with saturating penalties.
  - `_hand_at`: a ±0.07 s triangular window on a 1/120 s grid.
- Early lifts for jumps, crossed held keys and wide thumb crossings.
- Fingertips: a free finger leaves at once and arrives early; idle fingers fan out; tips are clamped before IK.
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
