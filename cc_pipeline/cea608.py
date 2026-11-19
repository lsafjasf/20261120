"""CEA-608 closed-caption decoder.

Handles both 608 fields (cc_type 0 -> field 1 / CC1-CC2, cc_type 1 ->
field 2 / CC3-CC4), all four channels, pop-on / roll-up / paint-on
modes, PAC + mid-row styling, and parity / reserved-code diagnostics.
"""

from dataclasses import dataclass, field

from .errors import ErrorLog

PAC_ROWS = [11, 12, 13, 14, 15, 6, 8, 10, 1, 2, 3, 4, 5, 7, 9, 10]
COLORS = ["white", "green", "blue", "cyan", "red", "yellow", "magenta", "white"]

MISC_CONTROL = {
    0x20: "RCL", 0x21: "BS", 0x24: "DER", 0x25: "RU2", 0x26: "RU3",
    0x27: "RU4", 0x28: "FON", 0x29: "RDC", 0x2A: "TR", 0x2B: "RTD",
    0x2C: "EDM", 0x2D: "CR", 0x2E: "ENM", 0x2F: "EOC",
}

SPECIAL_CHARS = {
    0x30: "®", 0x31: "°", 0x32: "½", 0x33: "¿", 0x34: "™", 0x35: "¢",
    0x36: "£", 0x37: "♪", 0x38: "à", 0x39: " ", 0x3A: "è", 0x3B: "â",
    0x3C: "ê", 0x3D: "î", 0x3E: "ô", 0x3F: "û",
}

EXTENDED_CHARS = {
    0x20: "Á", 0x21: "É", 0x22: "Ó", 0x23: "Ú", 0x24: "Ü", 0x25: "ü",
    0x26: "‘", 0x27: "¡", 0x28: "*", 0x29: "'", 0x2A: "─", 0x2B: "©",
    0x2C: "℠", 0x2D: "•", 0x2E: "“", 0x2F: "”", 0x30: "À", 0x31: "Â",
    0x32: "Ç", 0x33: "È", 0x34: "Ê", 0x35: "Ë", 0x36: "ë", 0x37: "Î",
    0x38: "Ï", 0x39: "ï", 0x3A: "Ô", 0x3B: "Ù", 0x3C: "ù", 0x3D: "Û",
    0x3E: "«", 0x3F: "»",
}


def odd_parity_ok(byte):
    return bin(byte).count("1") % 2 == 1


@dataclass(frozen=True)
class Style:
    color: str = "white"
    italics: bool = False
    underline: bool = False

    def describe(self):
        parts = [self.color]
        if self.italics:
            parts.append("italics")
        if self.underline:
            parts.append("underline")
        return "+".join(parts)


@dataclass
class Segment:
    text: str
    style: Style


@dataclass
class Line:
    row: int
    start_frame: int = None
    segments: list = field(default_factory=list)

    @property
    def text(self):
        return "".join(s.text for s in self.segments)


@dataclass
class CaptionEvent:
    source: str          # e.g. "608-CC1"
    channel: int
    mode: str
    start_frame: int
    end_frame: int = None
    rows: tuple = ()
    text: str = ""
    segments: list = field(default_factory=list)

    def close(self, frame):
        if self.end_frame is None:
            self.end_frame = frame


@dataclass
class ChannelState:
    number: int
    mode: str = "popon"
    rollup_window: int = 0
    displayed: list = field(default_factory=list)      # list[Line]
    nondisplayed: list = field(default_factory=list)   # list[Line]
    cur_row: int = 15
    cur_style: Style = Style()
    open_events: list = field(default_factory=list)    # list[CaptionEvent]


class Decoder608:
    def __init__(self, log: ErrorLog):
        self.log = log
        self.channels = {n: ChannelState(n) for n in (1, 2, 3, 4)}
        self.data_channel = {0: 1, 1: 3}   # field -> channel for plain text
        self.last_control = {0: None, 1: None}
        self.events = []
        self.style_log = []                # (frame, source, description)

    # -- helpers ---------------------------------------------------------

    def _target_lines(self, st):
        return st.nondisplayed if st.mode == "popon" else st.displayed

    def _append_text(self, st, frame, text):
        lines = self._target_lines(st)
        if not lines:
            lines.append(Line(st.cur_row))
        line = lines[-1]
        line.row = st.cur_row
        if line.start_frame is None and text.strip():
            line.start_frame = frame
        if line.segments and line.segments[-1].style == st.cur_style:
            line.segments[-1].text += text
        else:
            line.segments.append(Segment(text, st.cur_style))

    def _finalize_line(self, st, line, frame):
        if line.text.strip():
            self.events.append(CaptionEvent(
                source=f"608-CC{st.number}", channel=st.number,
                mode=st.mode, start_frame=line.start_frame or frame,
                end_frame=frame, rows=(line.row,),
                text=line.text, segments=line.segments))

    def _set_style(self, st, frame, style):
        if style != st.cur_style:
            st.cur_style = style
            self.style_log.append(
                (frame, f"608-CC{st.number}", style.describe()))

    def _close_events(self, st, frame):
        for ev in st.open_events:
            ev.close(frame)
            self.events.append(ev)
        st.open_events = []

    def _emit_displayed(self, st, frame):
        rows = tuple(sorted({ln.row for ln in st.displayed}))
        ev = CaptionEvent(
            source=f"608-CC{st.number}", channel=st.number, mode=st.mode,
            start_frame=frame, rows=rows,
            text=" / ".join(ln.text for ln in st.displayed),
            segments=[s for ln in st.displayed for s in ln.segments])
        st.open_events.append(ev)

    # -- control codes ---------------------------------------------------

    def _misc_control(self, st, frame, code):
        name = MISC_CONTROL[code]
        if name == "RCL":
            st.mode = "popon"
        elif name in ("RU2", "RU3", "RU4"):
            self._close_events(st, frame)
            st.mode = "rollup"
            st.rollup_window = int(name[2])
            st.displayed = []
        elif name == "RDC":
            self._close_events(st, frame)
            st.mode = "painton"
            st.displayed = []
        elif name == "EOC":
            self._close_events(st, frame)
            st.displayed, st.nondisplayed = st.nondisplayed, []
            if st.displayed:
                self._emit_displayed(st, frame)
        elif name == "EDM":
            self._close_events(st, frame)
            if st.mode != "popon":
                for ln in st.displayed:
                    self._finalize_line(st, ln, frame)
            st.displayed = []
        elif name == "ENM":
            st.nondisplayed = []
        elif name == "CR" and st.mode in ("rollup", "painton"):
            window = st.rollup_window if st.mode == "rollup" else 15
            while len(st.displayed) >= max(window, 1):
                self._finalize_line(st, st.displayed.pop(0), frame)
            st.displayed.append(Line(st.cur_row))
        return name

    def _pac(self, st, frame, b1, b2):
        idx = (b1 & 0x07) + (8 if b2 & 0x20 else 0)
        st.cur_row = PAC_ROWS[idx]
        underline = bool(b2 & 0x01)
        attr = (b2 >> 1) & 0x07
        if b2 & 0x10:
            style = Style("white", False, underline)   # indent PAC: white
        elif attr == 0x07:
            style = Style("white", True, underline)
        else:
            style = Style(COLORS[attr], False, underline)
        self._set_style(st, frame, style)
        return f"PAC row={st.cur_row} {style.describe()}"

    def _midrow(self, st, frame, b2):
        underline = bool(b2 & 0x01)
        if b2 in (0x2E, 0x2F):
            style = Style("white", True, underline)
        else:
            style = Style(COLORS[(b2 >> 1) & 0x07], False, underline)
        self._set_style(st, frame, style)
        self._append_text(st, frame, " ")   # mid-row codes render as a space
        return f"MIDROW {style.describe()}"

    # -- pair dispatch ---------------------------------------------------

    def feed(self, pair):
        """Decode one 608 byte pair (CcPair with cc_type 0 or 1)."""
        field = pair.cc_type
        frame = pair.frame
        if not odd_parity_ok(pair.data1):
            self.log.add(pair.offset + 1, frame, "cea608",
                         f"parity error in byte 0x{pair.data1:02X}")
        if not odd_parity_ok(pair.data2):
            self.log.add(pair.offset + 2, frame, "cea608",
                         f"parity error in byte 0x{pair.data2:02X}")
        b1, b2 = pair.data1 & 0x7F, pair.data2 & 0x7F
        if b1 == 0 and b2 == 0:
            return                                  # null padding
        if b1 < 0x10:
            return                                  # XDS packets: ignored

        if 0x10 <= b1 <= 0x1F:
            ch_in_field = 0 if b1 < 0x18 else 1
            channel = field * 2 + ch_in_field + 1
            st = self.channels[channel]
            key = (b1, b2)
            if key == self.last_control[field]:
                self.last_control[field] = None     # repeated control: skip
                return
            self.last_control[field] = key
            self.data_channel[field] = channel

            if b2 >= 0x40:
                self._pac(st, frame, b1, b2)
            elif b1 in (0x14, 0x1C) and b2 in MISC_CONTROL:
                self._misc_control(st, frame, b2)
            elif b1 in (0x11, 0x19) and 0x20 <= b2 <= 0x2F:
                self._midrow(st, frame, b2)
            elif b1 in (0x11, 0x19) and 0x30 <= b2 <= 0x3F:
                self._append_text(st, frame, SPECIAL_CHARS[b2])
            elif b1 in (0x12, 0x1A, 0x13, 0x1B) and 0x20 <= b2 <= 0x3F:
                self._append_text(st, frame, EXTENDED_CHARS.get(b2, "�"))
            elif b1 in (0x17, 0x1F) and 0x21 <= b2 <= 0x23:
                pass                                # tab offset TO1-TO3
            elif b1 in (0x14, 0x1C) and b2 in (0x22, 0x23):
                self.log.add(pair.offset + 1, frame, "cea608",
                             f"reserved control code {b1:02X} {b2:02X}")
            else:
                self.log.add(pair.offset + 1, frame, "cea608",
                             f"invalid control pair {b1:02X} {b2:02X}")
            return

        self.last_control[field] = None
        st = self.channels[self.data_channel[field]]
        self._append_text(st, frame, chr(b1))
        if b2 >= 0x20:
            self._append_text(st, frame, chr(b2))

    def flush(self, last_frame):
        for st in self.channels.values():
            self._close_events(st, last_frame)
            if st.mode != "popon":
                for ln in st.displayed:
                    self._finalize_line(st, ln, last_frame)
            st.displayed = []


def decode_608(pairs, log: ErrorLog):
    """Decode all 608 pairs; returns (events, style_log)."""
    dec = Decoder608(log)
    last_frame = 0
    for pair in pairs:
        if pair.cc_type in (0, 1) and pair.valid:
            dec.feed(pair)
            last_frame = max(last_frame, pair.frame)
    dec.flush(last_frame)
    dec.events.sort(key=lambda e: (e.start_frame, e.channel))
    return dec.events, dec.style_log
