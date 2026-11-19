"""Simulated RTSP session with network-outage recovery.

Models a client state machine (INIT -> DESCRIBED -> READY -> PLAYING)
against an in-process server, drops the transport mid-playback, then
verifies that DESCRIBE and PLAY state recover correctly after reconnect
and that playback resumes from the last known stream position.
"""
from dataclasses import dataclass, field


class ConnectionDropped(Exception):
    pass


@dataclass
class Response:
    code: int
    reason: str
    headers: dict = field(default_factory=dict)
    body: str = ""

    @property
    def ok(self):
        return self.code == 200


class Transport:
    """In-order request/response channel with fault injection."""

    def __init__(self, server):
        self.server = server
        self.connected = True

    def send(self, method, url, headers=None, body=""):
        if not self.connected:
            raise ConnectionDropped("tcp connection reset")
        return self.server.handle(method, url, headers or {}, body)

    def drop(self):
        self.connected = False

    def reconnect(self):
        self.connected = True


SDP = ("v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=cc-sample\r\n"
       "t=0 0\r\na=control:*\r\n"
       "m=video 0 RTP/AVP 96\r\na=rtpmap:96 MP2T/90000\r\n")


class RTSPServer:
    def __init__(self):
        self.sessions = {}
        self.next_session = 1
        self.log = []

    def handle(self, method, url, headers, body):
        self.log.append((method, headers.get("CSeq", "?")))
        if method == "DESCRIBE":
            return Response(200, "OK",
                            {"Content-Type": "application/sdp"}, SDP)
        if method == "SETUP":
            sid = str(self.next_session)
            self.next_session += 1
            self.sessions[sid] = {"state": "READY", "npt": 0.0}
            return Response(200, "OK", {"Session": sid,
                                        "Transport": "RTP/AVP/TCP"})
        session = self.sessions.get(headers.get("Session", ""))
        if session is None:
            return Response(454, "Session Not Found")
        if method == "PLAY":
            npt = session["npt"]
            range_hdr = headers.get("Range", "")
            if range_hdr.startswith("npt="):
                npt = float(range_hdr[4:].rstrip("-"))
            session["state"] = "PLAYING"
            session["npt"] = npt
            return Response(200, "OK",
                            {"Session": headers["Session"],
                             "Range": f"npt={npt:.3f}-"})
        if method == "GET_PARAMETER":
            return Response(200, "OK", {"Session": headers["Session"]})
        if method == "TEARDOWN":
            session["state"] = "INIT"
            return Response(200, "OK")
        return Response(501, "Not Implemented")


class RTSPClient:
    def __init__(self, url):
        self.url = url
        self.state = "INIT"
        self.session = None
        self.sdp = None
        self.position = 0.0          # last confirmed npt, seconds
        self.cseq = 0
        self.transport = None
        self.trace = []

    def _request(self, method, headers=None):
        self.cseq += 1
        hdrs = {"CSeq": str(self.cseq)}
        if self.session:
            hdrs["Session"] = self.session
        hdrs.update(headers or {})
        resp = self.transport.send(method, self.url, hdrs)
        self.trace.append(f"C->S {method} CSeq={self.cseq} -> "
                          f"{resp.code} {resp.reason}")
        return resp

    def connect(self, server):
        self.transport = Transport(server)

    def describe(self):
        resp = self._request("DESCRIBE", {"Accept": "application/sdp"})
        if resp.ok:
            self.sdp = resp.body
            self.state = "DESCRIBED"
        return resp

    def setup(self):
        resp = self._request("SETUP", {"Transport": "RTP/AVP/TCP"})
        if resp.ok:
            self.session = resp.headers["Session"]
            self.state = "READY"
        return resp

    def play(self, resume_from=None):
        headers = {}
        if resume_from is not None:
            headers["Range"] = f"npt={resume_from:.3f}-"
        resp = self._request("PLAY", headers)
        if resp.ok:
            self.state = "PLAYING"
            npt = resp.headers.get("Range", "npt=0-")[4:].rstrip("-")
            self.position = float(npt)
        return resp

    def keepalive(self):
        return self._request("GET_PARAMETER")

    def tick(self, seconds):
        """Advance playback; raises ConnectionDropped if the link is down."""
        self.keepalive()
        self.position += seconds

    def recover(self, server):
        """Reconnect after a network outage and restore PLAY state."""
        self.trace.append("!! network outage detected; reconnecting")
        self.transport = Transport(server)       # fresh TCP connection
        self.session = None
        self.state = "INIT"
        saved_position = self.position
        resp = self.describe()
        assert resp.ok and self.sdp == SDP, "DESCRIBE failed after reconnect"
        assert self.setup().ok, "SETUP failed after reconnect"
        resp = self.play(resume_from=saved_position)
        assert resp.ok, "PLAY failed after reconnect"
        self.trace.append(f"recovered: state={self.state} "
                          f"resume npt={self.position:.3f}")
        return self.state == "PLAYING"


def simulate_outage():
    """Run the full outage/reconnect scenario; return (checks, trace)."""
    server = RTSPServer()
    client = RTSPClient("rtsp://127.0.0.1:8554/cc-sample")
    checks = []

    client.connect(server)
    client.describe()
    checks.append(("DESCRIBE ok, SDP received",
                   client.state == "DESCRIBED" and client.sdp == SDP))
    client.setup()
    checks.append(("SETUP ok, session established",
                   client.state == "READY" and client.session is not None))
    client.play()
    checks.append(("PLAY ok, streaming", client.state == "PLAYING"))

    for _ in range(5):                            # 5 s of playback
        client.tick(1.0)
    position_before = client.position

    client.transport.drop()                       # simulate network cut
    try:
        client.tick(1.0)
        checks.append(("outage detected on keepalive", False))
    except ConnectionDropped:
        checks.append(("outage detected on keepalive", True))

    recovered = client.recover(server)
    checks.append(("DESCRIBE state restored after reconnect",
                   "DESCRIBE" in [m for m, _ in server.log[-3:]]))
    checks.append(("PLAY state restored after reconnect",
                   recovered and client.state == "PLAYING"))
    checks.append(("resume position continuous",
                   abs(client.position - position_before) < 1e-6))
    client.tick(1.0)
    checks.append(("streaming continues after recovery",
                   client.position > position_before))
    return checks, client.trace
