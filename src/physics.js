// Physical models: inverse-square distance law, ISO 9613-1 air absorption,
// and distance-dependent reverb predelay. All levels are in dB.

export const SPEED_OF_SOUND = 343.2; // m/s, dry air ~20 C

// ISO 9613-1:1993 pure-tone atmospheric attenuation coefficient.
// frequency in Hz, temperature in K, pressure in kPa, humidity in %RH.
// Returns alpha in dB per metre.
export function airAbsorptionCoefficient(
  frequency,
  { temperature = 293.15, pressure = 101.325, humidity = 50 } = {},
) {
  const T0 = 293.15; // reference temperature (20 C)
  const T01 = 273.16; // triple point isotherm
  const pRef = 101.325; // reference pressure in kPa
  const T = temperature;
  const p = pressure;

  // Saturation vapour pressure ratio (equation 1b).
  const C = -6.8346 * Math.pow(T01 / T, 1.261) + 4.6151;
  const pSatOverPRef = Math.pow(10, C);
  // Molar concentration of water vapour in percent.
  const hMol = humidity * pSatOverPRef * (pRef / p);

  // Oxygen and nitrogen relaxation frequencies (equations 2 and 3).
  const frO =
    (p / pRef) *
    (24.0 + (4.04e4 * hMol * (0.02 + hMol)) / (0.391 + hMol));
  const frN =
    (p / pRef) *
    Math.pow(T / T0, -0.5) *
    (9.0 +
      280.0 *
        hMol *
        Math.exp(-4.17 * (Math.pow(T / T0, -1 / 3) - 1.0)));

  const f = frequency;
  // Attenuation coefficient in dB/m (equation 1).
  const alpha =
    8.686 *
    f *
    f *
    (1.84e-11 * Math.pow(T / T0, 0.5) * (pRef / p) +
      Math.pow(T / T0, -2.5) *
        ((0.01275 * Math.exp(-2239.1 / T)) / (frO + (f * f) / frO) +
          (0.1068 * Math.exp(-3352.0 / T)) / (frN + (f * f) / frN)));
  return alpha;
}

// Inverse-square level change relative to refDistance: -20 log10(d/dRef).
export function distanceGainDb(distance, refDistance = 1) {
  return -20 * Math.log10(distance / refDistance);
}

// Air absorption loss in dB at a given distance.
export function airLossDb(frequency, distance, conditions) {
  return -airAbsorptionCoefficient(frequency, conditions) * distance;
}

// Reverb predelay: base room predelay plus the propagation delay d/c.
export function predelaySeconds(distance, baseSeconds = 0.02) {
  return baseSeconds + distance / SPEED_OF_SOUND;
}

// Direct-path propagation delay in samples.
export function propagationDelaySamples(distance, sampleRate) {
  return Math.round((distance / SPEED_OF_SOUND) * sampleRate);
}
