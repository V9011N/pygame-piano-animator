# Changelog

Each version is the UTC date and time of its commit (v20YY.MM.DD.HHMM). Newest first.

## v2026.10.02.0137 - A real hand's spread, thumb on its side
- The hand's span is now measured with the thumb and little finger stretched out the way a real
  hand spreads over the keys, so hands are drawn at a realistic size for their span (about 20%
  smaller than before) and look properly stretched in the hand span view.
- For wide chords like octaves the hand flattens and drops a little, so the thumb and little
  finger reach the full span; fingers in the air keep a relaxed spread.
- The thumb is drawn on its side, as it lies when playing: a narrow nail along its outer edge.

## v2026.10.02.0110 - Steady wrist in scales
- In scale runs (diatonic and chromatic) the wrist now glides steadily along the keyboard
  while the fingers do the crossing, instead of jerking at every thumb crossing.
- The fingers may draw together to let the wrist glide, but never overlap.
- Arpeggios move exactly as before.

## v2026.10.02.0049 - Free hand takes over, textbook scales and arpeggios
- When a file's tracks give one hand more than it can reach at its top speed, while the other
  hand is free, the free hand now takes those notes (the alternating passage at 0:57 of the
  Rachmaninoff 3 ossia cadenza, written all in one track). Everywhere else the tracks' hands are
  kept exactly, hand crossings included.
- Long scales and arpeggios keep their standard fingering: a two-octave scale no longer drifts
  into 3-2-1 crossings (the left hand of Chopin's Concerto No. 1 ending), and arpeggios no longer
  swap patterns on the way down.
- Fixed: B major and B minor scales put the left thumb on F#; the standard 4-3-2-1 (thumb on E
  and B) is used now.
- Harmonic minor scales are recognised across their augmented second, very fast and uneven
  runs are no longer mistaken for chords, and a run that starts from a chord in the same hand
  carries on from it.

## v2026.10.02.0013 - Equal keys
- New "Keys" button on the main menu switches between the realistic keyboard and equal keys,
  after PASHKULI's design: every key's back, black or white, has the same width, so every
  falling note does too, and the white key fronts share the rest evenly.
- With equal keys the lanes above white keys are shaded a little lighter, and white-key notes
  show their finger number in white (black-key notes in black).

## v2026.10.01.2352 - Changelog
- New "What's new" button on the main menu shows this changelog. It glows until you open it,
  and again whenever there is a new version.

## v2026.09.30.1614 - Version number, readable progress bar
- The version is shown in the window title and in the bottom-left corner.
- The song title and time on the progress bar have a drop shadow, so they stay readable over
  the bar's fill (player and fingering editor).

## v2026.09.30.1603 - Exporting fixed
- Fixed: exporting from the fingering editor failed with "_vlq() takes 1 positional argument
  but 2 were given".
- Fixed: fingering stored in MIDI files wasn't loaded back (broken by the previous version).
- Files that only load after being repaired now export too, as a clean file.
- The "notes couldn't be matched" count after an export counts a note doubled on two tracks
  once.

## v2026.09.29.1914 - MIDI files cleaned on load
- Fixed: files with several unlabelled tracks (e.g. Chopin Op. 25 No. 12) failed to load with
  "unsupported operand type(s) for -: 'float' and 'str'".
- Damaged files are repaired when they can't be read: out-of-range data bytes are clipped, a
  cut-off track keeps what it has, and broken meta events and system-exclusive messages are
  dropped.
- In a full score (e.g. a concerto) only the piano part is kept; percussion and empty tracks
  are dropped.
- Notes are tidied: impossible times dropped, velocities and pitches brought into range, a note
  doubled on two tracks played once, and a key struck again while held ends the earlier note.
  What was changed is printed when the file loads.

## v2026.09.28.2340 - Finger thickness
- The finger thickness slider runs from 50% to 120% (was 70-135%); new pianists start at 75%.

## v2026.09.28.2339 - Reaching held inner notes
- Wider middle and ring finger splay, so the hand can hold an inner note under an octave
  (Op. 25 No. 10's middle voice).
- Less smoothing of the hand while its fingers are on keys, so pressed fingertips stay on
  their keys in fast chord passages.

## v2026.09.28.2151 - Octaves around a held note
- Octaves with a held inner note in the same hand are fingered 1-5, leaving room for the
  finger holding it.
- Chords check every pair of fingers against the hand's reach, not only neighbouring ones.

## v2026.09.28.2142 - Held notes stay down
- Held notes are no longer let go when the hand shifts slightly around them; a held key stays
  down through a hand move unless its finger is needed.
- A finger stepping to a neighbouring key lifts only just before it, instead of the full early
  release used for leaps.

## v2026.09.28.2006 - Smoother travelling fingers
- Fingers on their way to a key point at it instead of stretching out fully and then snapping
  back onto their spot.
- The spot a finger aims for on a key changes smoothly as the hand moves.
- The drawn hand is slightly smoothed, rounding off lurches; a finger on its key stays exactly
  on it.

## v2026.09.28.1735 - Playing area on the keys
- Two new pianist settings bound where a fingertip may play on a key, from the front edge up to
  among the black keys.
- Within that area loud notes are played nearer the front of the key and soft ones may be
  played further up.

## v2026.09.28.1719 - White keys among black ones
- In passages mixing black and white keys (e.g. chromatic octaves) white keys are played up
  among the black ones, so the hand no longer moves in and out for every note.
- The lowest finger anticipation is now a 50 ms head start.

## v2026.09.28.1649 - Lower finger anticipation
- The finger anticipation setting goes below its old minimum, down to fingers arriving just in
  time (the thumb no longer tucks under long before its note). Saved pianists are unchanged.

## v2026.09.28.0540 - Top travel speed
- New pianist setting "Top travel speed" (default 3.0 m/s): no part of a hand moves faster.
- The hand split and the fingering avoid moves that would need more than that speed, so hands
  no longer teleport across the keyboard.
- What you hear, and every lit key and note, follows what the hands actually play: keys are let
  go early, or struck slightly late, to keep to the speed.

## v2026.09.28.0431 - Impossible file fingerings
- When a file's fingering for a chord can't be played, the player keeps what fits and plans the
  rest, instead of leaving fingers between the keys (the editor still shows the file's
  fingering).
- Keys still held when a leap starts are let go early, so the hand isn't stretched between
  both places.

## v2026.09.28.0413 - Fingertips square on their keys
- Pressed fingertips stay centred on their keys; fixed a finger twitching on repeated notes.

## v2026.09.28.0349 - Nails on curved fingers
- Fixed nails appearing at the knuckles of strongly curved fingers.

## v2026.09.28.0330 - Idle hand out of the way
- A hand that isn't playing moves out of the playing hand's way and loosely follows it, so
  hands only cross when the music needs it.

## v2026.09.28.0306 - Fingernail perspective
- Nails are foreshortened and slide to the end of the fingertip as a finger curls away.

## v2026.09.27.2345 - README
- README: features, sample videos for each hand skin, requirements and getting started.

## v2026.09.27.2108 - First version
- Falling-notes player with an 88-key keyboard, pedals and sound through the system's MIDI
  synth.
- Automatic hand split for single-track MIDI, and fingering from a planner that recognises
  scales, arpeggios, chromatic runs, octaves, double notes, trills and repeated notes.
- Procedurally animated hands seen from above: finger IK, thumb-unders, hand crossings, wrist
  bounce and forearm rotation.
- Fingering editor: piano roll, set hand and finger per note, re-plan, difficulty view,
  sequential mode, and export to MIDI (fingering embedded) or PIG text.
- Pianists & hands studio: hand anatomy, technique and fingering preferences, and skins
  (cartoon, white gloves, robot, skeleton).
