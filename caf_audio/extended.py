"""IEEE-754 80-bit extended-precision helpers.

AIFF stores its sample rate in this representation.  The implementation keeps
an exact :class:`fractions.Fraction` for validation, so the common sample-rate
bit patterns can be compared byte-for-byte with published references.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
import struct

from .errors import ExtendedFloatError

EXPONENT_BIAS = 16383
EXPONENT_MAX = 0x7FFF
MANTISSA_BITS = 64
MANTISSA_SCALE = 1 << (MANTISSA_BITS - 1)


@dataclass(frozen=True)
class ExtendedParts:
    sign: int
    exponent: int
    mantissa: int

    @property
    def classification(self) -> str:
        if self.exponent == EXPONENT_MAX:
            return "infinity" if self.mantissa == 1 << 63 else "nan"
        if self.exponent == 0:
            return "zero" if self.mantissa == 0 else "subnormal"
        return "normal"

    @property
    def exact(self) -> Fraction | None:
        if self.classification in {"infinity", "nan"}:
            return None
        if self.exponent == 0:
            if self.mantissa == 0:
                return Fraction(0)
            power = 1 - EXPONENT_BIAS
            value = Fraction(self.mantissa, MANTISSA_SCALE)
            value *= _power_of_two(power)
        else:
            power = self.exponent - EXPONENT_BIAS
            value = Fraction(self.mantissa, MANTISSA_SCALE)
            value *= _power_of_two(power)
        return -value if self.sign else value


def _power_of_two(exponent: int) -> Fraction:
    if exponent >= 0:
        return Fraction(1 << exponent)
    return Fraction(1, 1 << -exponent)


def _round_half_even(value: Fraction) -> int:
    quotient, remainder = divmod(value.numerator, value.denominator)
    twice = remainder * 2
    if twice > value.denominator or (
        twice == value.denominator and quotient & 1
    ):
        quotient += 1
    return quotient


def decode_parts(raw: bytes) -> ExtendedParts:
    if len(raw) != 10:
        raise ExtendedFloatError(
            f"80-bit extended value must be 10 bytes, got {len(raw)}"
        )
    high, mantissa = struct.unpack(">HQ", raw)
    return ExtendedParts(high >> 15, high & EXPONENT_MAX, mantissa)


def decode_extended(raw: bytes) -> float:
    parts = decode_parts(raw)
    if parts.classification == "nan":
        return math.nan
    if parts.classification == "infinity":
        return -math.inf if parts.sign else math.inf
    exact = parts.exact
    assert exact is not None
    try:
        return float(exact)
    except OverflowError as exc:
        raise ExtendedFloatError("extended value exceeds binary64 range") from exc


def encode_extended(value: float | int) -> bytes:
    value = float(value)
    if math.isnan(value):
        return struct.pack(">HQ", 0x7FFF, 0xC000000000000000)
    if math.isinf(value):
        high = EXPONENT_MAX | (0x8000 if value < 0 else 0)
        return struct.pack(">HQ", high, 1 << 63)

    negative = math.copysign(1.0, value) < 0
    magnitude = Fraction.from_float(abs(value))
    if magnitude == 0:
        return struct.pack(">HQ", 0x8000 if negative else 0, 0)

    exponent = magnitude.numerator.bit_length() - magnitude.denominator.bit_length()
    if magnitude < _power_of_two(exponent):
        exponent -= 1

    if exponent > EXPONENT_MAX - EXPONENT_BIAS:
        high = EXPONENT_MAX | (0x8000 if negative else 0)
        return struct.pack(">HQ", high, 1 << 63)

    if exponent >= 1 - EXPONENT_BIAS:
        scaled = magnitude * _power_of_two(MANTISSA_BITS - 1 - exponent)
        mantissa = _round_half_even(scaled)
        if mantissa == MANTISSA_SCALE * 2:
            mantissa >>= 1
            exponent += 1
        stored_exponent = exponent + EXPONENT_BIAS
    else:
        scaled = magnitude * _power_of_two(
            MANTISSA_BITS - 1 + EXPONENT_BIAS - 1
        )
        mantissa = _round_half_even(scaled)
        stored_exponent = 0

    high = stored_exponent | (0x8000 if negative else 0)
    return struct.pack(">HQ", high, mantissa)


def decode_extended_hex(value: str) -> float:
    compact = "".join(value.split())
    if compact.lower().startswith("0x"):
        compact = compact[2:]
    try:
        raw = bytes.fromhex(compact)
    except ValueError as exc:
        raise ExtendedFloatError(f"invalid extended-float hex: {value!r}") from exc
    return decode_extended(raw)
