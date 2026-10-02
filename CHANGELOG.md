# Changelog

Versions are vYY.MAJOR.MINOR from v26.1.0 (before that, the UTC date and time of the commit,
v20YY.MM.DD.HHMM). Newest first.

## v26.1.7 - Speed limits from the command line
- Fixed: `--speed` and `--audio-speed` took any number - 0 froze the song, a negative one ran it
  backwards. They're now kept between 10% and 200%, like the speed keys.

## v26.1.6 - Fingers on their keys
- Fixed: in fast, wide repeated broken chords (Chopin's Ocean Étude) the hand was held still as if
  playing a tremolo, halfway between notes it couldn't reach, so fingers struck beside their keys.
  Only figures the hand can cover from one place are played as tremolos now.
- Fixed: a rolled chord too wide to hold let go of its lower notes only after the top one was
  struck, so the hand never reached it; they're now let go in time.
- Fixed: undoing in sequential fingering mode could crash the editor when the undo went back to a
  note of the other hand.

## v26.1.5 - Glissandos keep to the top speed
- Fixed: in Liszt's Hungarian Rhapsody No. 10 at 4:21 the hand jumped across four keys in one
  frame at the end of a glissando. A glissando key further on than the hand can slide in time is
  now struck a moment late instead.
- Fixed: a chord far away straight after a glissando made the hand fly there at up to three times
  the pianist's top speed; it's now struck a little late, as the hand gets there.
- Fixed: the hand twitched sideways as it left one glissando with another coming.

## v26.1.4 - Bug fixes
- Fixed: a MIDI file without notes did nothing when opened; it now says it has no notes.
- Fixed: resizing the window while the fingering editor asked about unsaved changes crashed the
  app.
- Fixed: dragging a synced recording's waveform to the window's left edge while playing left the
  recording silent.
- Fixed: a pianist named "Settings" could overwrite the app's settings file.
- Removed leftover code that no longer did anything.

## v26.1.3 - pygame-ce
- Now runs on pygame-ce, the community edition of pygame: drawing is about 5% faster and looks
  the same. To switch an existing install: `pip uninstall -y pygame`, then
  `pip install -r requirements.txt`. Plain pygame still works.

## v26.1.2 - Loading progress bar
- Opening a file shows a progress bar, and the window keeps responding while the song loads and the
  hands' fingering is planned (in the background).
- Choosing "Default sound" or syncing a recording no longer stops for a second plan of the hands:
  they're ready from the load.
- Fixed: opening another file from the fingering editor ("Open…") crashed the app.

## v26.1.1 - Faster loading and smoother playback
- Unfingered MIDI files load about a third faster (Chopin's 12-minute Concerto No. 1: 7.5 s to 5 s).
- Smoother playback: the hands' motion is worked out ahead in each frame's spare time, so far
  fewer frames run late, and the stalls around glissandos (up to half a second) are gone.
- Everything looks and plays exactly as before.

## v26.1.0 - New version numbers
- Versions are now numbered year.major.minor, starting at v26.1.0; each update raises the last
  number.

## v2026.10.02.1613 - Sound stops when you leave
- Fixed: leaving the player or the fingering editor while the sustain pedal was down left the
  notes ringing on. The pedals are now lifted and all sound stopped when you leave, open another
  file, mute, or quit.

## v2026.10.02.1605 - Fine-tune fingering (advanced)
- New "Fine Tune Fingering Behavior (ADVANCED)" button at the bottom left of a pianist's behaviour
  page: every weight of the fingering planner as a slider - stretches, crossings, repeated
  fingers, black keys, chords, hand shifts and timing, each finger's strength and ease on black
  keys, and how firmly scales, arpeggios, octaves, trills and other figures keep their standard
  fingering.
- Each weight shows what it does and its default, and has its own Reset button; Reset all is at
  the top. A weight's default is what the pianist's other behaviour settings make it.
- The changes are saved with the pianist and used for their fingering everywhere.
- The behaviour list is a little more compact, so it fits with the new button.

## v2026.10.02.1546 - Synced recordings
- After choosing a MIDI file to play, choose how it should sound: the default sound (the MIDI
  synth), or an audio file synced to the notes and hands.
- For an audio file, first choose the playback speed (fixed from then on), then the file
  (WAV, OGG, MP3 or FLAC). It must be at least as long as the MIDI at that speed.
- The player opens paused with the recording's waveform across the top, a progress bar running
  from the left. Drag the waveform left or right to line the recording up with the notes (hold
  Shift for finer moves), or nudge it with , and . (10 ms; 100 ms with Shift).
- From the command line: --audio recording.wav, with --audio-speed and --audio-offset.

## v2026.10.02.0616 - Faster equal keys
- Equal keys no longer slow the player down: the lanes, the keyboard and the finger numbers are
  drawn once and reused, so equal keys now draw as fast as (or faster than) realistic keys. They
  look exactly the same.

## v2026.10.02.0551 - Resting hands stay by their next notes
- A resting hand is no longer drawn toward the other hand when it plays next where it is (Chopin's
  Op. 25 No. 6 at 0:25: the right hand stays up high between its passages).
- A resting hand moved to the notes it plays next stays there until it plays them, instead of
  sliding back across them (and, in Op. 25 No. 6 near the end, across the other hand).

## v2026.10.02.0534 - Hands start uncrossed, no overshoot when resting
- The hands no longer start a piece crossed: a hand that comes in later waits on its own side of
  the one that plays first (the beginning of the Dante Sonata).
- A resting hand drawn toward the playing one no longer goes past the notes it plays next and
  jerks back (Chopin's Op. 25 No. 6 after the rising chromatic thirds).

## v2026.10.02.0518 - Steady tremolos
- Tremolos and trills are now recognised as a figure: the hand plays them from one place, the
  wrist still and each finger staying over its key, instead of swinging toward every note (the
  left hand's twitching fingers at the start of the Dante Sonata, Hanon 60).
- When a tremolo moves to a new position the hand moves with it.

## v2026.10.02.0455 - Knuckle lines, short thumbs curve
- The cartoon hand shows the knuckles where the fingers meet the hand: a short line across each
  finger and the thumb.
- Fixed: in the Pianists & hands studio, a short thumb was drawn straight in the natural resting
  curve; it now curves like any other.

## v2026.10.02.0446 - Chords between glissandos
- Fixed: chords played between glissandos were missed, the hand still travelling from the
  glissando (Liszt's Hungarian Rhapsody No. 10 at 4:27 and through the glissando section). The
  hand is now always back in place for the next chord, and leaves for a glissando only once its
  last keys are let go; when time is short it sets off as soon as the glissando ends.

## v2026.10.02.0316 - Thumb glissando arm lean
- In a thumb glissando the forearm leans toward the way the hand slides, elbow trailing, as if
  pushing the thumb along the keys.

## v2026.10.02.0309 - Glissando travel at top speed, thumb nail sliver
- Fixed: going to or from a glissando the hand could jump across the keyboard (up to 2.5 times
  its top speed, e.g. Liszt's Hungarian Rhapsody No. 10 at 4:23). It now travels there at no more
  than its top speed, easing in and out; between two glissandos close together it glides straight
  from one to the next.
- Fixed: a glissando starting while the last one was still fading out made the hand snap away.
- Palm-up glissandos show a sliver of the thumb's nail along its edge toward the fingers.

## v2026.10.02.0250 - Glissando hands refined
- Thumb glissandos: the thumb's nail is drawn flush with the thumb's outer edge, on the keys.
- Palm-up glissandos: the fingers lie flush against each other (each finger's outline still
  shows), and the thumb is tucked across the palm.
- Glissandos close together no longer keep the hand in its glissando pose when the next one
  starts more than 5 keys away from where the last ended: the hand may go back to rest between
  them.

## v2026.10.02.0238 - Thumb glissando clear of the black keys
- The fist in a thumb glissando no longer runs into the black keys: the thumb slides a little
  lower on the white keys, so the knuckles stay just in front of the black ones.

## v2026.10.02.0236 - Thumb glissando on the keys
- In a thumb glissando the hand now sits up on the keys like a real one: a fist with its knuckles
  over the keys, the thumb straight alongside it, its nail sliding further up the keys.

## v2026.10.02.0231 - Palm lines, thumb glissandos
- When the hand is turned palm up for a glissando, the palm's lines (heart, head and life lines)
  are drawn, so it's clear it is the palm you see.
- Glissandos toward the thumb (right hand going down, left hand going up) are now played with the
  thumb: fingers 2-5 curled right in, the thumb straight out and lying along the keys, its nail
  sliding on them. Turning round between glissandos goes smoothly from one pose to the other.

## v2026.10.02.0218 - Simpler glissando pose
- The glissando hand is now flat and turned palm up, the fingers straight and together, the thumb
  tucked in beside them, sliding the backs of the fingers along the keys with the fingers trailing.
- It turns over smoothly as it goes into and out of a glissando; only the thumb's nail shows.

## v2026.10.02.0211 - Glissando pose, like a real hand
- The glissando hand now looks like a real one: the back of the hand stays up and leads the way
  it slides, the hand turned so the fingers trail, all the fingertips and the thumb gathered
  together at one point on the keys.
- Only the thumb's nail shows in the glissando pose (the fingers are curled under).

## v2026.10.02.0159 - Glissandos
- The hands now play glissandos: a quick string of white keys (or black keys) going one way is
  slid with the fingers pinched together and straight, the back of the hand facing the way it
  goes, the nails gliding along the keys.
- New pianist settings (Glissandos): whether to spot and slide them, the longest gap between
  notes (50 ms), the fewest notes (6), and how long a break between glissandos the hand keeps
  its glissando pose through (1 s).
- Glissando notes show "g" in the player and the fingering editor. In the editor, select a string
  of notes and choose Glissando from the right-click menu (or press G) to mark one yourself; it is
  saved with the exported file.

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
