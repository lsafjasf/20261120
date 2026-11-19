"""Air absorption coefficient vs frequency, ISO 9613-1 model."""
import numpy as np


def air_absorption(f, temp_c=20.0, pressure_kpa=101.325, rel_humidity=70.0):
    """Attenuation coefficient alpha in dB/m (ISO 9613-1:1993).

    f: frequency (Hz, array-like). Defaults: 20 C, 1 atm, 70 % RH.
    """
    f = np.asarray(f, dtype=float)
    T = temp_c + 273.15
    T0, T01 = 293.15, 273.16
    p, p0 = pressure_kpa, 101.325
    # Saturation vapour pressure and molar concentration of water vapour (%).
    psat = p0 * 10.0 ** (-6.8346 * (T01 / T) ** 1.261 + 4.6151)
    h = rel_humidity * psat / p
    # Relaxation frequencies of oxygen and nitrogen (Hz).
    frO = (p / p0) * (24.0 + 4.04e4 * h * (0.02 + h) / (0.391 + h))
    frN = (p / p0) * (T / T0) ** -0.5 * (
        9.0 + 280.0 * h * np.exp(-4.17 * ((T / T0) ** (1.0 / 3.0) - 1.0))
    )
    alpha = 8.686 * f**2 * (
        1.84e-11 * (p / p0) ** -1 * (T / T0) ** 0.5
        + (T / T0) ** -2.5
        * (
            0.01275 * np.exp(-2239.1 / T) / (frO + f**2 / frO)
            + 0.1068 * np.exp(-3352.0 / T) / (frN + f**2 / frN)
        )
    )
    return alpha


def absorption_table(freqs=None, **kwargs):
    if freqs is None:
        freqs = np.array([125, 250, 500, 1000, 2000, 4000, 6300, 8000,
                          10000, 12500, 16000], dtype=float)
    alpha_dbm = air_absorption(freqs, **kwargs)
    return freqs, alpha_dbm, alpha_dbm * 1000.0  # dB/m and dB/km


def physical_model_checks(freqs, alpha_dbm):
    """Verify the attenuation follows the expected physical behaviour."""
    mono = bool(np.all(np.diff(alpha_dbm) > 0))
    # Classical + relaxation model: alpha ~ f^2 well above the highest
    # relaxation frequency, so alpha/f^2 should asymptote to a constant.
    hf = freqs >= 8000
    ratio = alpha_dbm[hf] / freqs[hf] ** 2
    asymptote_var = float(np.ptp(ratio) / np.mean(ratio))
    # Sanity ranges at 20 C / 70 % RH (published ISO 9613-1 magnitudes).
    a1k = float(np.interp(1000.0, freqs, alpha_dbm)) * 1000.0
    a10k = float(np.interp(10000.0, freqs, alpha_dbm)) * 1000.0
    range_ok = (3.0 < a1k < 8.0) and (80.0 < a10k < 200.0)
    return {
        "monotonic_increase": mono,
        "hf_alpha_over_f2_rel_spread": asymptote_var,
        "alpha_1khz_db_per_km": a1k,
        "alpha_10khz_db_per_km": a10k,
        "range_ok": range_ok,
    }
