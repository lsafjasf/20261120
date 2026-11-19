"""DRM key status -> player behaviour mapping."""
from __future__ import annotations

KEY_STATUS_ROWS = [
    {
        "status": "usable",
        "playback": "continue",
        "playerState": "playing",
        "pause": False,
        "error": False,
        "action": "keep-playing",
        "message": "Playback continues; the key is usable.",
    },
    {
        "status": "expired",
        "playback": "pause",
        "playerState": "paused-license-renewal",
        "pause": True,
        "error": False,
        "action": "renew-license",
        "message": "Playback pauses and the player requests a fresh license.",
    },
    {
        "status": "output-restricted",
        "playback": "pause",
        "playerState": "error-output-restricted",
        "pause": True,
        "error": True,
        "action": "show-hdcp-error",
        "message": "Playback pauses and an output-protection error is shown.",
    },
]
