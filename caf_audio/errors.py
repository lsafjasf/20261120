class CafError(Exception):
    """Base class for CAF parsing and decoding failures."""


class CafFormatError(CafError):
    """The container structure is malformed or incomplete."""


class MissingChunkError(CafFormatError):
    """A required CAF chunk is absent."""


class UnsupportedFormatError(CafError):
    """The format is valid enough to identify but unsupported by this tool."""


class InvalidEndianFlagError(CafError):
    """The linear-PCM flags cannot identify a valid byte order."""


class TimecodeError(ValueError):
    """A timecode label or frame index is outside the selected time base."""


class ExtendedFloatError(ValueError):
    """An 80-bit IEEE extended value is malformed."""
