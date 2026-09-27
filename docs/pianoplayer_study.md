# Study: marcomusy/pianoplayer vs our fingering (2026-09-26)

## How pianoplayer works
Source: `hand.py`.

- **Search**: for each note, a depth-first search through every way of fingering the next 3–9 notes (5^9 at most, with rules that prune some branches). Only the first note of the best candidate is kept, then the window slides on by one note. It is greedy, with a look-ahead of up to 9 notes.
- **Cost**: the average speed the fingers must move at. After each note, all five fingers are placed at relaxed offsets around the finger that just played. The relaxed offsets are asymmetric, in cm: thumb −7, index −2.8, middle 0, ring 2.8, little 5.6.
  - Each finger's speed is |next key − predicted position of that finger| / (dt + 0.1).
  - The speed is divided by a finger-strength weight (1.1, 1.0, 1.1, 0.9, 0.8).
  - On black keys it is also divided by a per-finger factor (0.3, 1, 1.1, 0.8, 0.7).
- **Hard rules**: no crossing of long fingers, no thumb onto a black key going up, chord finger order, and per-pair chord stretch limits.
- **Hand size**: seven presets scale the relaxed offsets.
- **Chords**: turned into short runs, 50 ms apart.
- **Other features**: pre-fingered notes act as anchors. It reads MusicXML, MIDI and the PIG dataset format. It outputs fingering-annotated MusicXML or PIG text, and a per-note cost (difficulty) that can colour the output.
- **What it lacks**: a hand split (a single-track MIDI goes to the right hand only), a held-note model, figure recognition, and global optimisation.
- **Bug**: `keypos_midi` spaces all 12 semitones equally over a 7-white-key octave. Positions are not monotonic (B3 lands to the right of C4), so the MIDI path is badly broken as shipped.

## Head-to-head on Hanon
Setup: our Hanon MIDIs, book fingering, 52 of the 60 exercises (41–48 timed out for pianoplayer). Figures are the unweighted mean disagreement per exercise, RH/LH.

| Version | RH / LH disagreement |
|---|---|
| pianoplayer as shipped (MIDI; only exercises 1, 39 and 40 run) | 52 / 69 % |
| pianoplayer with key positions fixed | 30.4 / 34.1 % |
| ours, cost model only (no figures) | 29.4 / 30.2 % |
| ours, full (current) | 26.7 / 27.6 % |
| ours + their velocity term (weight 0.05, scratch copy only) | 21.9 / 23.5 % |

Note-weighted overall for ours: 21.5 / 22.8 % now, and 19.5 / 20.6 % with the velocity term.

Speed: pianoplayer takes about 30 s per exercise; ours takes under 1 s.

By group (pianoplayer / ours / ours + velocity):

| Exercises | pianoplayer | ours | ours + velocity |
|---|---|---|---|
| 1–20 five-finger | 18 | 21 | 14 |
| 21–38 shifts | 45 | 43 | 38 |
| 39–43 scales | 34 | 2 | 2 |
| 44–50 repeated notes / trills | 46 | 35 | 35 |
| 51–60 double notes | 35 | 14 | 14 |

## Worth adopting
1. **Velocity / posture term** in `_transition`: weight about 0.05. Tested only in the scratch copy `exp/fingering.py`.
   - Largest gains: exercise 7 (from 72/78 to 14/20), 13, 14, 18, 25, 36.
   - Small losses: 12, 17, 30, 44, 46, 47.
   - The relaxed offsets should come from the pianist's anatomy.
2. **PIG dataset** import (and maybe export), to benchmark against human fingerings of real repertoire.
3. **Per-note difficulty / cost** shown in the fingering editor.
4. **MusicXML export** of fingering, for MuseScore.

## Side finding
In 21–38 our figure layer makes things worse: 43/44 % with it against 36/36 % with the cost model alone. Worth investigating.

## PIG benchmark (2026-09-27)
The user downloaded the PIG dataset v1.2 (150 pieces) into the project folder: `FingeringFiles/` and `PianoFingeringDataset_v1.2.zip`.

### Official test split (pieces 001–030), Default pianist, zero-shot
Figures are general / high / soft match rates (%). Published rows are from Ramoneda et al. 2022, Table 2, and are trained on the other 120 pieces.

| Method | RH | LH |
|---|---|---|
| **Ours** | 59.2 / 66.2 / 83.2 | 64.7 / 72.3 / 83.6 |
| HMM1 | 58.3 / 65.1 / 81.1 | – |
| HMM2 | 60.7 / 66.7 / 83.9 | 66.3 / 71.9 / 83.0 |
| HMM3 | 60.5 / 66.9 / 83.4 | – |
| ArGNNThumb-s (best published) | 62.8 / 68.9 / 86.2 | 70.9 / 76.3 / 87.6 |
| Human pianists vs each other | 70.2 / 78.9 / 91.0 | 73.1 / 79.3 / 90.5 |

All 150 pieces, both hands: 63.9 / 65.7 / 71.0 with the Default pianist. The user's own run gave 62.1, probably because a custom pianist was active.

### Ablations
Pieces 031–150 are used as the tuning ("train") set.

| Change | Train, both hands (general) | Test, both hands (general) |
|---|---|---|
| Current settings | 64.3 | 62.0 |
| Economy of motion 0 / 0.1 / 0.2 | 64.0 / 64.7 / 65.6 | 62.5 / 61.6 / 60.9 |
| Figure layer off | 65.2 | 62.9 |

- Economy of motion is noise-level on real repertoire.
- Removing each figure rule in turn: scales (`_stepwise`) cost about 0.7; double notes help (RH +0.7); arpeggios, broken octaves and octaves each cost about 0.3; repeated notes and trills make no difference.
- Lowering `W_SCALE` from 3.0 to 0.75 (arpeggios left at 2.5): train 65.0, test 62.8 (RH 60.4 / 67.4 / 84.6). Hanon overall barely moves (RH 19.5 → 19.5, LH 20.6 → 20.8). This is a candidate change; not applied yet.
- Scripts in the scratchpad: `pig_sweep.py`, `pig_fig.py`, `pig_scale.py`.

## Learned weights (applied 2026-09-27)
- `W_SCALE` changed from 3.0 to 0.75.
- `learn_weights.py` runs a coordinate search (factors ×2/×0.5, then ×1.5/×0.67, then ×1.2/×0.83; a change is kept if it improves by more than 0.03 points). It maximises the general match rate on PIG pieces 031–150, with pieces 001–030 held out for testing.
- The figure weights (`figure`, `figures.W_*`) are frozen by default because a free run weakens textbook figures:
  - free run: test 66.6 %, but Hanon worsens to 29 / 30 %;
  - frozen run: test 66.1 %, Hanon 23.5 / 24.7 %, with scales and double notes close to textbook.
- Result: 21 weights changed. They are stored as `LEARNED_W` in `fingering.py` and in `learned_weights.json`.
  - The biggest moves: `thumb_black` 1.92 → 0.24, `steal` 1.5 → 12, `chord_stretch_adj` 1.2 → 5.76, `same_key_new_finger` 0.8 → 4.8, `cross` 3.2 → 6.4, `shift_speed` 8 → 2, `velocity` 0.05 → 0.1.
- Pianist behaviour "Fingering model": **learned** (the default) or **textbook**. Textbook is the old hand-calibrated `W_TEXTBOOK`. Pianist sliders scale whichever base is chosen.

### Test set (001–030)
Figures are general / high / soft match rates (%).

| Model | RH | LH |
|---|---|---|
| Learned | 62.3 / 70.1 / 86.9 | 69.8 / 77.4 / 89.1 |
| Textbook | 60.1 / 67.2 / 84.4 | 65.0 / 72.5 / 83.9 |
| Best published (ArGNNThumb-s) | 62.8 / 68.9 / 86.2 | 70.9 / 76.3 / 87.6 |
| Humans vs each other | 70.2 / 78.9 / 91.0 | 73.1 / 79.3 / 90.5 |

### Hanon disagreement

| Model | RH / LH (%) |
|---|---|
| Learned | 23.5 / 24.7 |
| Textbook | 19.5 / 20.5 |

With the learned model the biggest losses are in Hanon's weak-finger drills (exercises 44–50). Scales and double notes stay close to textbook.

### Chopin Op. 10 No. 2
The learned model fingers the chromatic line 3-4-5 over the chords, which is Chopin's approach; the textbook model used thumbs there. The "fast crossing" count for this piece rises from 19 to 158 as a result, which is expected.
