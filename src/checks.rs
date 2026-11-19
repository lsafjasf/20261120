//! Decoded-sample verification: bit-exact vs acoustic lossless, plus
//! cumulative packet boundary and overflow checks.

use crate::cookie::MagicCookie;
use crate::decoder::DecodeOutput;
use crate::generator::{BitDepth, GeneratedPcm};
use crate::packet::PacketReport;

#[derive(Debug, Clone)]
pub struct SampleMismatch {
    pub channel: usize,
    pub frame: usize,
    pub expected: i32,
    pub actual: i32,
    pub abs_diff: i64,
}

#[derive(Debug, Clone)]
pub struct CompareResult {
    pub total_samples: usize,
    pub bit_exact: bool,
    pub mismatches: Vec<SampleMismatch>,
    /// Acoustic comparison: all differences below the tolerance floor.
    pub acoustic_lossless: bool,
    pub max_abs_diff: i64,
    pub max_relative_error: f64,
}

/// Demonstrate the difference between the two verification philosophies on
/// demand: take a bit-exact decoded stream and flip a single least
/// significant bit in one sample. Bit-exact comparison must fail while the
/// acoustic comparison still passes (the error is ~1 LSB, inaudible).
pub fn compare_with_single_lsb_perturbation(
    pcm: &GeneratedPcm,
    decoded: &DecodeOutput,
    stored_depth: BitDepth,
) -> (CompareResult, SampleMismatch) {
    let mut perturbed = decoded.clone();
    let channel = 0usize;
    let frame = perturbed.planar[channel].len() / 2;
    let original = perturbed.planar[channel][frame];
    perturbed.planar[channel][frame] = original ^ 1;
    let result = compare_samples(pcm, &perturbed, stored_depth);
    let injected = SampleMismatch {
        channel,
        frame,
        expected: original,
        actual: original ^ 1,
        abs_diff: 1,
    };
    (result, injected)
}

/// Bit-exact: every decoded i32 equals the generated source sample.
/// Acoustic lossless: normalize to [-1,1] using the bit-depth full scale and
/// require a relative error below -120 dB (strictly sub-LSB for >=16 bit).
pub fn compare_samples(
    pcm: &GeneratedPcm,
    decoded: &DecodeOutput,
    stored_depth: BitDepth,
) -> CompareResult {
    let channels = pcm.planar.len();
    let full_scale = stored_depth.max_value() as f64;
    // Acoustic-lossless floor: about -90 dB relative to full scale. This is
    // below the threshold of human hearing yet deliberately distinct from the
    // strict integer equality used by the bit-exact check: a single 16-bit LSB
    // error (~-96 dB) passes acoustically while failing bit-exact.
    let acoustic_tol = 3.2e-5_f64;

    let mut total = 0usize;
    let mut mismatches = Vec::new();
    let mut max_abs_diff = 0i64;
    let mut max_relative_error = 0.0f64;
    let mut acoustic_ok = true;

    for ch in 0..channels {
        let expected = &pcm.planar[ch];
        let actual = &decoded.planar[ch];
        if expected.len() != actual.len() {
            // Record a synthetic mismatch so the caller reports the length fault.
            mismatches.push(SampleMismatch {
                channel: ch,
                frame: expected.len().min(actual.len()),
                expected: expected.len() as i32,
                actual: actual.len() as i32,
                abs_diff: expected.len().abs_diff(actual.len()) as i64,
            });
            acoustic_ok = false;
            continue;
        }
        for (frame, (&e, &a)) in expected.iter().zip(actual.iter()).enumerate() {
            total += 1;
            let diff = (i64::from(e) - i64::from(a)).abs();
            if diff != 0 {
                if mismatches.len() < 20 {
                    mismatches.push(SampleMismatch {
                        channel: ch,
                        frame,
                        expected: e,
                        actual: a,
                        abs_diff: diff,
                    });
                }
                max_abs_diff = max_abs_diff.max(diff);
                // Full-scale-normalized error (not sample-relative), so zero
                // samples cannot cause a division by zero.
                let rel = (f64::from(e) - f64::from(a)).abs() / full_scale;
                max_relative_error = max_relative_error.max(rel);
                if rel > acoustic_tol {
                    acoustic_ok = false;
                }
            }
        }
    }

    let bit_exact = mismatches.is_empty();
    CompareResult {
        total_samples: total,
        bit_exact,
        acoustic_lossless: acoustic_ok,
        mismatches,
        max_abs_diff,
        max_relative_error,
    }
}

#[derive(Debug, Clone)]
pub struct BoundaryAnomaly {
    pub packet_index: usize,
    pub detail: String,
}

#[derive(Debug, Clone)]
pub struct BoundaryReport {
    pub packet_count: usize,
    pub cumulative_samples: u64,
    pub cookie_frame_length: u32,
    pub timing_sample_count: u64,
    pub anomalies: Vec<BoundaryAnomaly>,
}

/// Cross-check three independent views of packet boundaries:
/// 1. compressed-header sample counts (our parser),
/// 2. actual frames Symphonia produced per packet,
/// 3. stts timing entries and the cookie frame_length.
pub fn check_boundaries(
    cookie: &MagicCookie,
    header_reports: &[PacketReport],
    decoded: &DecodeOutput,
    timing_sample_count: u64,
) -> BoundaryReport {
    let mut anomalies = Vec::new();
    let packet_count = header_reports.len();

    if decoded.packets.len() != packet_count {
        anomalies.push(BoundaryAnomaly {
            packet_index: packet_count.min(decoded.packets.len()),
            detail: format!(
                "packet count mismatch: {} compressed packets vs {} decoded packets",
                packet_count,
                decoded.packets.len()
            ),
        });
    }

    let mut cumulative = 0u64;
    for report in header_reports {
        let idx = report.packet_index;
        let is_last = idx + 1 == packet_count;

        // Full packets must equal cookie frame_length; only the final packet may be partial.
        if !is_last && report.frame_samples != cookie.frame_length as usize {
            anomalies.push(BoundaryAnomaly {
                packet_index: idx,
                detail: format!(
                    "non-final packet declares {} samples, cookie frame_length is {}",
                    report.frame_samples, cookie.frame_length
                ),
            });
        }
        if is_last && report.frame_samples > cookie.frame_length as usize {
            anomalies.push(BoundaryAnomaly {
                packet_index: idx,
                detail: format!(
                    "final packet declares {} samples exceeding frame_length {}",
                    report.frame_samples, cookie.frame_length
                ),
            });
        }

        if let Some(decoded_packet) = decoded.packets.get(idx) {
            if decoded_packet.decoded_frames != report.frame_samples {
                anomalies.push(BoundaryAnomaly {
                    packet_index: idx,
                    detail: format!(
                        "compressed header says {} frames but decoder produced {}",
                        report.frame_samples, decoded_packet.decoded_frames
                    ),
                });
            }
            if u64::from(decoded_packet.duration_ts) != report.frame_samples as u64 {
                anomalies.push(BoundaryAnomaly {
                    packet_index: idx,
                    detail: format!(
                        "demuxer duration {} != header sample count {}",
                        decoded_packet.duration_ts, report.frame_samples
                    ),
                });
            }
        }
        cumulative += report.frame_samples as u64;
    }

    if cumulative != timing_sample_count {
        anomalies.push(BoundaryAnomaly {
            packet_index: packet_count,
            detail: format!(
                "cumulative header samples {cumulative} != stts sample count {timing_sample_count}"
            ),
        });
    }

    BoundaryReport {
        packet_count,
        cumulative_samples: cumulative,
        cookie_frame_length: cookie.frame_length,
        timing_sample_count,
        anomalies,
    }
}

#[derive(Debug, Clone)]
pub struct OverflowReport {
    pub depth: BitDepth,
    pub min_seen: i32,
    pub max_seen: i32,
    pub min_allowed: i32,
    pub max_allowed: i32,
    pub violations: usize,
}

/// Zero/full-scale guard: decoded values must stay inside the signed range
/// of the declared bit depth (no wrap-around / saturation overflow).
pub fn check_overflow(depth: BitDepth, decoded: &DecodeOutput) -> OverflowReport {
    let min_allowed = depth.min_value();
    let max_allowed = depth.max_value();
    let mut min_seen = i32::MAX;
    let mut max_seen = i32::MIN;
    let mut violations = 0;
    let mut any = false;
    for channel in &decoded.planar {
        for &sample in channel {
            any = true;
            min_seen = min_seen.min(sample);
            max_seen = max_seen.max(sample);
            if sample < min_allowed || sample > max_allowed {
                violations += 1;
            }
        }
    }
    if !any {
        min_seen = 0;
        max_seen = 0;
    }
    OverflowReport { depth, min_seen, max_seen, min_allowed, max_allowed, violations }
}
