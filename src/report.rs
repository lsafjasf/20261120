//! Text + JSON report rendering (JSON is hand-serialized, zero extra deps).

use std::fmt::Write;

use crate::checks::{BoundaryReport, CompareResult, OverflowReport};
use crate::cookie::MagicCookie;
use crate::decoder::DecodeOutput;
use crate::packet::PacketReport;

pub struct FileReport {
    pub name: String,
    pub spec_summary: String,
    pub cookie: MagicCookie,
    pub headers: Vec<PacketReport>,
    pub boundary: BoundaryReport,
    pub compare: CompareResult,
    pub overflow: OverflowReport,
    pub decode: DecodeOutput,
    /// Optional bit-exact-vs-acoustic demonstration (injected 1-LSB error).
    pub perturbation: Option<PerturbationDemo>,
}

#[derive(Debug, Clone)]
pub struct PerturbationDemo {
    pub channel: usize,
    pub frame: usize,
    pub bit_exact_after: bool,
    pub acoustic_after: bool,
    pub max_relative_error: f64,
}

pub fn render_text(reports: &[FileReport]) -> String {
    let mut out = String::new();
    for r in reports {
        let layout = r
            .cookie
            .layout
            .as_ref()
            .map(|l| l.name)
            .unwrap_or_else(|| r.cookie.implicit_layout_name());
        writeln!(out, "============================================================").ok();
        writeln!(out, "FILE: {} ({})", r.name, r.spec_summary).ok();
        writeln!(
            out,
            "  magic cookie [{} bytes]: frameLength={} bitDepth={} channels={} \
             sampleRate={} maxFrameBytes={} avgBitRate={}",
            r.cookie.raw_len,
            r.cookie.frame_length,
            r.cookie.bit_depth,
            r.cookie.num_channels,
            r.cookie.sample_rate,
            r.cookie.max_frame_bytes,
            r.cookie.avg_bit_rate
        )
        .ok();
        writeln!(out, "  channel layout: {layout}").ok();
        writeln!(
            out,
            "  packets: {}  cumulative samples(header): {}  stts samples: {}",
            r.boundary.packet_count, r.boundary.cumulative_samples, r.boundary.timing_sample_count
        )
        .ok();
        writeln!(out, "  per-packet decoded sample counts:").ok();
        for (h, d) in r.headers.iter().zip(r.decode.packets.iter()) {
            let partial = if h.frame_samples == r.cookie.frame_length as usize {
                "full"
            } else {
                "PARTIAL"
            };
            writeln!(
                out,
                "    pkt {:>3}: bytes={:>7} header_samples={:>5} ({}) decoded_frames={:>5} elements={}",
                h.packet_index,
                h.byte_size,
                h.frame_samples,
                partial,
                d.decoded_frames,
                h.elements
                    .iter()
                    .map(|e| format!(
                        "tag=0b{:03b}/ch{}/shift{}/{}",
                        e.tag,
                        if e.tag == 0b001 { 2 } else { 1 },
                        e.shift_bits,
                        if e.uncompressed { "raw" } else { "compressed" }
                    ))
                    .collect::<Vec<_>>()
                    .join(",")
            )
            .ok();
        }
        if r.boundary.anomalies.is_empty() {
            writeln!(out, "  BOUNDARY CHECK: PASS (packet boundaries match magic cookie)").ok();
        } else {
            writeln!(out, "  BOUNDARY CHECK: FAIL").ok();
            for a in &r.boundary.anomalies {
                writeln!(out, "    - pkt {}: {}", a.packet_index, a.detail).ok();
            }
        }
        let cmp = &r.compare;
        writeln!(
            out,
            "  BIT-EXACT CHECK: {} ({} samples, {} mismatches, max |diff|={})",
            if cmp.bit_exact { "PASS" } else { "FAIL" },
            cmp.total_samples,
            cmp.mismatches.len(),
            cmp.max_abs_diff
        )
        .ok();
        writeln!(
            out,
            "  ACOUSTIC LOSSLESS CHECK: {} (max relative error {:.3e})",
            if cmp.acoustic_lossless { "PASS" } else { "FAIL" },
            cmp.max_relative_error
        )
        .ok();
        if !cmp.mismatches.is_empty() {
            for m in cmp.mismatches.iter().take(5) {
                writeln!(
                    out,
                    "    mismatch ch={} frame={} expected={} actual={} |diff|={}",
                    m.channel, m.frame, m.expected, m.actual, m.abs_diff
                )
                .ok();
            }
        }
        let ov = &r.overflow;
        writeln!(
            out,
            "  OVERFLOW CHECK: {} (range seen [{}, {}], allowed [{}, {}], violations={})",
            if ov.violations == 0 { "PASS" } else { "FAIL" },
            ov.min_seen,
            ov.max_seen,
            ov.min_allowed,
            ov.max_allowed,
            ov.violations
        )
        .ok();
        if let Some(p) = &r.perturbation {
            writeln!(
                out,
                "  BIT-EXACT vs ACOUSTIC DEMO: after flipping 1 LSB at ch{} frame{} -> \
                 bit_exact={}, acoustic_lossless={} (rel err {:.3e})",
                p.channel, p.frame, p.bit_exact_after, p.acoustic_after, p.max_relative_error
            )
            .ok();
        }
    }
    out
}

fn json_escape(s: &str) -> String {
    s.replace('\\', "\\\\").replace('"', "\\\"")
}

pub fn render_json(reports: &[FileReport]) -> String {
    let mut out = String::new();
    writeln!(out, "[").ok();
    for (i, r) in reports.iter().enumerate() {
        let c = &r.cookie;
        writeln!(out, "  {{").ok();
        writeln!(out, "    \"file\": \"{}\",", json_escape(&r.name)).ok();
        writeln!(out, "    \"spec\": \"{}\",", json_escape(&r.spec_summary)).ok();
        writeln!(
            out,
            "    \"cookie\": {{\"frame_length\": {}, \"bit_depth\": {}, \"channels\": {}, \
             \"sample_rate\": {}, \"layout\": \"{}\"}},",
            c.frame_length,
            c.bit_depth,
            c.num_channels,
            c.sample_rate,
            json_escape(c.layout.as_ref().map(|l| l.name).unwrap_or_else(|| c.implicit_layout_name()))
        )
        .ok();
        writeln!(
            out,
            "    \"boundary\": {{\"packets\": {}, \"cumulative_samples\": {}, \
             \"stts_samples\": {}, \"anomalies\": {}}},",
            r.boundary.packet_count,
            r.boundary.cumulative_samples,
            r.boundary.timing_sample_count,
            r.boundary.anomalies.len()
        )
        .ok();
        writeln!(
            out,
            "    \"per_packet_samples\": [{}],",
            r.headers
                .iter()
                .map(|h| h.frame_samples.to_string())
                .collect::<Vec<_>>()
                .join(",")
        )
        .ok();
        writeln!(
            out,
            "    \"bit_exact\": {}, \"acoustic_lossless\": {}, \
             \"mismatch_count\": {}, \"max_abs_diff\": {}, \"max_relative_error\": {:.3e},",
            r.compare.bit_exact,
            r.compare.acoustic_lossless,
            r.compare.mismatches.len(),
            r.compare.max_abs_diff,
            r.compare.max_relative_error
        )
        .ok();
        writeln!(
            out,
            "    \"overflow\": {{\"violations\": {}, \"min_seen\": {}, \"max_seen\": {}}}",
            r.overflow.violations, r.overflow.min_seen, r.overflow.max_seen
        )
        .ok();
        if let Some(p) = &r.perturbation {
            writeln!(
                out,
                ",\n    \"lsb_perturbation_demo\": {{\"channel\": {}, \"frame\": {}, \
                 \"bit_exact_after\": {}, \"acoustic_lossless_after\": {}, \
                 \"max_relative_error\": {:.3e}}}",
                p.channel, p.frame, p.bit_exact_after, p.acoustic_after, p.max_relative_error
            )
            .ok();
        }
        writeln!(out, "  }}{}", if i + 1 == reports.len() { "" } else { "," }).ok();
    }
    writeln!(out, "]").ok();
    out
}
