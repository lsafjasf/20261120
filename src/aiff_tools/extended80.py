"""Decode and encode the 80-bit IEEE-style extended float used by AIFF."""

from __future__ import annotations

from fractions import Fraction
import math
import struct

BIAS = 16383
INTEGER_BIT = 1 << 63


def _exact_text(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def decode_extended80(raw: bytes) -> dict:
    """Return every useful field of a 10-byte AIFF sample-rate value."""
    if len(raw) != 10:
        raise ValueError(f"extended80 requires 10 bytes, got {len(raw)}")

    exponent_word = int.from_bytes(raw[:2], "big")
    sign = exponent_word >> 15
    exponent_raw = exponent_word & 0x7FFF
    significand = int.from_bytes(raw[2:], "big")
    sign_factor = -1 if sign else 1

    if exponent_raw == 0x7FFF:
        if significand == INTEGER_BIT:
            kind = "infinity"
        elif significand == 0:
            kind = "infinity"
        else:
            kind = "nan"
        value = math.inf if kind == "infinity" else math.nan
        exact = None
    elif exponent_raw == 0:
        kind = "zero" if significand == 0 else "subnormal"
        exact = sign_factor * Fraction(significand, INTEGER_BIT) * Fraction(1, 1 << (BIAS - 1))
        value = float(exact)
    else:
        kind = "normal"
        exponent_unbiased = exponent_raw - BIAS
        exact = sign_factor * Fraction(significand, INTEGER_BIT) * (2 ** exponent_unbiased)
        value = float(exact)

    if kind in ("infinity", "nan"):
        exponent_unbiased = None
        hex_float = str(value)
    else:
        try:
            exponent_unbiased = exact.numerator.bit_length() - exact.denominator.bit_length()
            if exact and abs(exact) < 2 ** exponent_unbiased:
                exponent_unbiased -= 1
        except (AttributeError, ValueError):
            exponent_unbiased = None
        hex_float = float(value).hex()

    return {
        "raw_hex": raw.hex(),
        "sign": sign,
        "exponent_raw": exponent_raw,
        "exponent_hex": f"0x{exponent_raw:04x}",
        "exponent_unbiased": (
            None if kind in ("infinity", "nan") else (
                exponent_raw - BIAS if exponent_raw else 1 - BIAS
            )
        ),
        "significand": significand,
        "significand_hex": f"0x{significand:016x}",
        "integer_bit": (significand >> 63) & 1,
        "kind": kind,
        "value": value,
        "hex_float": hex_float,
        "exact": None if exact is None else _exact_text(exact),
        "fraction": None if exact is None else f"{exact.numerator}/{exact.denominator}",
    }


def encode_extended80(value: int | float | str | Fraction) -> bytes:
    """Encode a finite numeric value as a 10-byte extended float."""
    fraction = value if isinstance(value, Fraction) else Fraction(str(value))
    if fraction == 0:
        return b"\x00" * 10

    sign = 0x8000 if fraction < 0 else 0
    magnitude = abs(fraction)
    exponent = magnitude.numerator.bit_length() - magnitude.denominator.bit_length()
    if magnitude < 2 ** exponent:
        exponent -= 1

    scaled = magnitude / (2 ** exponent) * INTEGER_BIT
    significand = round(scaled)
    if significand >= (1 << 64):
        significand >>= 1
        exponent += 1

    exponent_raw = exponent + BIAS
    if not 0 < exponent_raw < 0x7FFF:
        raise OverflowError(f"value cannot be represented as finite extended80: {value!r}")
    if significand < INTEGER_BIT:
        raise ValueError(f"normal extended80 significand is not normalized: {value!r}")

    return struct.pack(">H", sign | exponent_raw) + significand.to_bytes(8, "big")
