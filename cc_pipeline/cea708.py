"""CEA-708 DTVCC decoder.

Reassembles DTVCC packets from cc_data triplets (cc_type 2/3), parses
service blocks, tracks windows / pen styles, and emits caption events
with frame-accurate start/end and style-switch records.
"""

from dataclasses import dataclass, field

from .errors import ErrorLog

# C1 command parameter lengths (bytes following the command byte)
C1_PARAM_LEN = {
    0x88: 1,  # CLW  clear windows
    0x89: 1,  # DSW  display windows
    0x8A: 1,  # HDW  hide windows
    0x8B: 1,  # TGW  toggle windows
    0x8C: 1,  # DLW  delete windows
    0x8D: 1,  # DLY  delay
    0x8E: 0,  # DLC  delay cancel
    0x8F: 0,  # RST  reset
    0x90: 2,  # SPA  set pen attributes
    0x91: 3,  # SPC  set pen color
    0x92: 2,  # SPL  set pen location
    0x97: 4,  # SWA  set window attributes
}
C1_NAMES = {
    0x88: "CLW", 0x89: "DSW", 0x8A: "HDW", 0x8B: "TGW", 0x8C: "DLW",
    0x8D: "DLY", 0x8E: "DLC", 0x8F: "RST", 0x90: "SPA", 0x91: "SPC",
    0x92: "SPL", 0x97: "SWA",
}


@dataclass
class PenStyle:
    italics: bool = False
    underline: bool = False
    fg_color: str = "white"

    def describe(self):
        parts = [self.fg_color]
        if self.italics:
            parts.append("italics")
        if self.underline:
            parts.append("underline")
        return "+".join(parts)


@dataclass
class Window:
    wid: int
    visible: bool = False
    defined: bool = False
    anchor_point: int = 0
    anchor_vertical: int = 0
    anchor_horizontal: int = 0
    row_count: int = 0
    col_count: int = 0
    pen: PenStyle = field(default_factory=PenStyle)
    text: str = ""
    start_frame: int = None

    def region(self):
        """Approximate (row0, row1, col0, col1) in the 708 75x210 grid."""
        voff = self.anchor_vertical
        if self.anchor_point in (3, 4, 5):
            voff -= self.row_count // 2
        elif self.anchor_point in (6, 7, 8):
            voff -= self.row_count
        hoff = self.anchor_horizontal
        if self.anchor_point in (1, 4, 7):
            hoff -= self.col_count // 2
        elif self.anchor_point in (2, 5, 8):
            hoff -= self.col_count
        return (max(voff, 0), voff + self.row_count,
                max(hoff, 0), hoff + self.col_count)


@dataclass
class Event708:
    source: str
    service: int
    window: int
    start_frame: int
    end_frame: int = None
    region: tuple = ()
    text: str = ""

    def close(self, frame):
        if self.end_frame is None:
            self.end_frame = frame


class Decoder708:
    def __init__(self, log: ErrorLog):
        self.log = log
        self.windows = {}            # (service, wid) -> Window
        self.current_window = {}     # service -> wid
        self.events = []
        self.style_log = []          # (frame, source, description)
        self.open_events = {}        # (service, wid) -> Event708
        self._pkt = bytearray()
        self._pkt_offsets = []
        self._pkt_expected = None
        self._pkt_frame = 0
        self._pkt_offset = 0

    # -- packet reassembly ----------------------------------------------

    def feed(self, pair):
        if pair.cc_type == 3:                     # DTVCC packet start
            if self._pkt_expected is not None:
                self.log.add(pair.offset, pair.frame, "cea708",
                             "new packet start before previous packet "
                             f"completed ({len(self._pkt)}/"
                             f"{self._pkt_expected} bytes)")
            size_code = pair.data1 & 0x3F
            self._pkt_expected = 128 if size_code == 0 else size_code * 2 - 1
            self._pkt = bytearray([pair.data2])
            self._pkt_offsets = [pair.offset + 2]
            self._pkt_frame = pair.frame
            self._pkt_offset = pair.offset
        elif pair.cc_type == 2:                   # DTVCC packet data
            if self._pkt_expected is None:
                self.log.add(pair.offset, pair.frame, "cea708",
                             "packet data without packet start")
                return
            self._pkt.extend([pair.data1, pair.data2])
            self._pkt_offsets.extend([pair.offset + 1, pair.offset + 2])
            if len(self._pkt) >= self._pkt_expected:
                self._parse_packet(bytes(self._pkt[:self._pkt_expected]),
                                   self._pkt_offsets[:self._pkt_expected],
                                   pair.frame, self._pkt_offset)
                self._pkt = bytearray()
                self._pkt_offsets = []
                self._pkt_expected = None

    def flush(self, last_frame):
        if self._pkt_expected is not None:
            self.log.add(self._pkt_offset, self._pkt_frame, "cea708",
                         f"stream ended mid-packet ({len(self._pkt)}/"
                         f"{self._pkt_expected} bytes)")
            self._pkt_expected = None
        for ev in self.open_events.values():
            ev.close(last_frame)
            self.events.append(ev)
        self.open_events = {}

    # -- packet / service-block parsing ----------------------------------

    def _parse_packet(self, pkt, offsets, frame, pkt_offset):
        pos = 0
        while pos < len(pkt):
            header = pkt[pos]
            pos += 1
            if header == 0:
                break                            # padding to end of packet
            service = (header >> 5) & 0x07
            block_size = header & 0x1F
            if service == 7:
                if pos >= len(pkt):
                    self.log.add(offsets[pos - 1], frame, "cea708",
                                 "truncated extended service header")
                    return
                service = pkt[pos] & 0x3F
                pos += 1
            if service == 0 or block_size == 0:
                continue
            if pos + block_size > len(pkt):
                self.log.add(offsets[pos], frame, "cea708",
                             f"service {service} block overruns packet "
                             f"({block_size} bytes declared)")
                return
            self._parse_block(service, pkt[pos:pos + block_size],
                              frame, offsets[pos:pos + block_size])
            pos += block_size

    def _window(self, service, wid):
        key = (service, wid)
        if key not in self.windows:
            self.windows[key] = Window(wid)
        return self.windows[key]

    # -- block command interpreter ---------------------------------------

    def _open_event(self, service, win, frame):
        key = (service, win.wid)
        if key not in self.open_events and win.text.strip():
            self.open_events[key] = Event708(
                source=f"708-S{service}", service=service, window=win.wid,
                start_frame=win.start_frame or frame,
                region=win.region(), text=win.text)

    def _close_window_event(self, service, wid, frame):
        key = (service, wid)
        win = self.windows.get(key)
        if win is not None:
            self._open_event(service, win, frame)
        ev = self.open_events.pop(key, None)
        if ev is not None:
            ev.close(frame)
            self.events.append(ev)
        if win is not None:
            win.text = ""
            win.start_frame = None

    def _append_text(self, service, frame, text):
        wid = self.current_window.get(service, 0)
        win = self._window(service, wid)
        if win.start_frame is None:
            win.start_frame = frame
        win.text += text

    def _parse_block(self, service, block, frame, offsets):
        pos = 0
        while pos < len(block):
            cmd = block[pos]
            at = offsets[pos]
            pos += 1
            if cmd >= 0x20 and cmd != 0x7F and cmd < 0x80 or cmd > 0x9F:
                self._append_text(service, frame, chr(cmd))
                continue
            if cmd == 0x7F:
                self._append_text(service, frame, "♪")
                continue
            if cmd < 0x20:                          # C0 group
                if cmd == 0x0D:                     # CR
                    self._append_text(service, frame, "\n")
                elif cmd in (0x10, 0x18):           # EXT1 / P16
                    need = 2 if cmd == 0x18 else 1
                    if pos + need > len(block):
                        self.log.add(at, frame, "cea708",
                                     f"truncated {'P16' if cmd == 0x18 else 'EXT1'}"
                                     " escape")
                        return
                    pos += need
                elif cmd in (0x00, 0x03, 0x08, 0x0C, 0x0E):
                    pass                            # NUL/ETX/BS/FF/HCR
                else:
                    self.log.add(at, frame, "cea708",
                                 f"reserved C0 code 0x{cmd:02X}")
                continue
            # C1 group
            if 0x80 <= cmd <= 0x87:                 # CW0-CW7
                self.current_window[service] = cmd & 0x07
                continue
            if 0x98 <= cmd <= 0x9F:                 # DF0-DF7
                if pos + 6 > len(block):
                    self.log.add(at, frame, "cea708",
                                 f"truncated DF{cmd & 7} command")
                    return
                self._define_window(service, cmd & 7, block[pos:pos + 6])
                pos += 6
                continue
            plen = C1_PARAM_LEN.get(cmd)
            if plen is None:
                self.log.add(at, frame, "cea708",
                             f"reserved C1 code 0x{cmd:02X}")
                continue
            if pos + plen > len(block):
                self.log.add(at, frame, "cea708",
                             f"truncated {C1_NAMES[cmd]} command")
                return
            params = block[pos:pos + plen]
            pos += plen
            self._c1_command(service, cmd, params, frame, at)

    def _define_window(self, service, wid, p):
        win = self._window(service, wid)
        win.defined = True
        win.anchor_point = (p[1] >> 4) & 0x0F
        win.anchor_vertical = p[2] & 0x7F
        win.anchor_horizontal = p[3]
        win.row_count = (p[4] & 0x0F) + 1
        win.col_count = (p[5] & 0x3F) + 1

    def _c1_command(self, service, cmd, p, frame, at):
        name = C1_NAMES[cmd]
        src = f"708-S{service}"
        if name in ("DSW", "HDW", "TGW", "CLW", "DLW"):
            mask = p[0]
            for wid in range(8):
                if not (mask >> wid) & 1:
                    continue
                win = self._window(service, wid)
                if name == "DSW":
                    win.visible = True
                elif name == "HDW":
                    win.visible = False
                    self._close_window_event(service, wid, frame)
                elif name == "TGW":
                    win.visible = not win.visible
                    if not win.visible:
                        self._close_window_event(service, wid, frame)
                elif name in ("CLW", "DLW"):
                    self._close_window_event(service, wid, frame)
                    if name == "DLW":
                        win.defined = False
                        win.visible = False
        elif name == "RST":
            for (svc, wid) in list(self.open_events):
                if svc == service:
                    self._close_window_event(svc, wid, frame)
        elif name == "SPA":
            wid = self.current_window.get(service, 0)
            win = self._window(service, wid)
            pen = PenStyle(italics=bool(p[0] & 0x80),
                           underline=bool(p[0] & 0x40),
                           fg_color=win.pen.fg_color)
            if pen != win.pen:
                win.pen = pen
                self.style_log.append((frame, src, pen.describe()))
        elif name == "SPC":
            wid = self.current_window.get(service, 0)
            win = self._window(service, wid)
            colors = ["black", "red", "green", "yellow",
                      "blue", "magenta", "cyan", "white"]
            fg = colors[(p[0] >> 2) & 0x07] if p[0] & 0x03 == 0 else "white"
            pen = PenStyle(win.pen.italics, win.pen.underline, fg)
            if pen != win.pen:
                win.pen = pen
                self.style_log.append((frame, src, pen.describe()))


def decode_708(pairs, log: ErrorLog):
    """Decode all 708 pairs; returns (events, style_log)."""
    dec = Decoder708(log)
    last_frame = 0
    for pair in pairs:
        if pair.cc_type in (2, 3) and pair.valid:
            dec.feed(pair)
            last_frame = max(last_frame, pair.frame)
    dec.flush(last_frame)
    dec.events.sort(key=lambda e: (e.start_frame, e.service))
    return dec.events, dec.style_log
