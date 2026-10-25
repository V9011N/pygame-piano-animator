"""Playback: what reaches the synth, in what order, from any starting point."""
from miditool.document import Document, Event, Note
from miditool.playback import Player


class FakeOut:
    def __init__(self):
        self.sent = []
        self.port = True
        self.volume = 1.0
        self.muted = False
        self.error = ""

    def ensure(self):
        return True

    def short(self, status, d1=0, d2=0):
        self.sent.append((status, d1, d2))

    def note_on(self, ch, pitch, vel):
        self.sent.append((0x90 | ch, pitch, vel))

    def note_off(self, ch, pitch):
        self.sent.append((0x80 | ch, pitch, 0))

    def silence(self):
        self.sent.append(("silence",))


def _doc():
    doc = Document.new_empty()
    doc.begin("x")
    for k in range(4):                                  # quarter notes at 120 bpm: one every 0.5 s
        doc.put_note(Note(doc.new_id(), 1, 0, 60 + k, 480 * k, 480 * k + 240, 80))
    doc.put_event(Event(doc.new_id(), 1, 600, 0, bytes([0xB0, 64, 127])))
    doc.put_event(Event(doc.new_id(), 1, 10, 0, bytes([0xC1, 5])))
    doc.commit()
    return doc


def test_plays_notes_and_pedal_in_order():
    doc = _doc()
    out = FakeOut()
    p = Player(out)
    assert p.start(doc, 0)
    for _ in range(300):                                # 2.5 s in 1/120 s frames
        p.update(1 / 120, doc)
    ons = [m[1] for m in out.sent if m[0] == 0x90]
    assert ons == [60, 61, 62, 63]
    i_ped = out.sent.index((0xB0, 64, 127))
    assert out.sent.index((0x90, 61, 80)) < i_ped < out.sent.index((0x90, 62, 80))
    assert not p.playing                                # stopped at the end
    assert not p.sounding


def test_starting_midway_chases_programs_and_pedals():
    doc = _doc()
    out = FakeOut()
    p = Player(out)
    p.start(doc, 1000)                                  # after the pedal went down and the program changed
    assert (0xC1, 5, 0) in out.sent
    assert (0xB0, 64, 127) in out.sent
    p.update(0.6, doc)
    assert [m[1] for m in out.sent if m[0] == 0x90] == [63]


def test_edits_while_playing_dont_leave_notes_hanging():
    doc = _doc()
    out = FakeOut()
    p = Player(out)
    p.start(doc, 0)
    p.update(0.1, doc)                                  # the first note sounds
    doc.remove_note(next(n.id for n in doc.notes.values() if n.pitch == 60))
    p.stop()
    offs = {m[1] for m in out.sent if m[0] == 0x80}
    assert 60 in offs
