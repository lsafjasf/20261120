use std::fs;
use std::path::{Path, PathBuf};

use generator::{BitDepth, GeneratedPcm, PcmSpec, SignalKind};
use alac_harness::{checks, cookie, decoder, generator, mp4, packet, report, HarnessError};
use alac_harness::Result;

fn ffmpeg_path() -> PathBuf {
    PathBuf::from(std::env::var("FFMPEG").unwrap_or_else(|_| "ffmpeg".to_string()))
}

struct TestCase {
    name: String,
    spec: PcmSpec,
}

fn build_matrix() -> Vec<TestCase> {
    let sample_rate = 48_000u32;
    let frame_length = 4096usize;
    let mut cases = Vec::new();

    // Interleaving/bit-depth matrix: 1/2/4/6 channels x 16/24/32 bit.
    for &depth in &[BitDepth::B16, BitDepth::B24, BitDepth::B32] {
        for &channels in &[1u8, 2, 4, 6] {
            // Two full packets plus a deliberately partial final packet so the
            // last-frame boundary path is exercised on every file.
            let total = frame_length * 2 + frame_length / 3;
            let name = format!("tones_{}ch_{}", channels, depth.label());
            cases.push(TestCase {
                name,
                spec: PcmSpec { sample_rate, channels, depth, total_samples: total, kind: SignalKind::Tones },
            });
        }
    }

    // Edge signals across depths: zero and full-scale rails.
    for &depth in &[BitDepth::B16, BitDepth::B24, BitDepth::B32] {
        for &kind in &[SignalKind::Zero, SignalKind::FullScale] {
            let total = frame_length + 137;
            cases.push(TestCase {
                name: format!("{}_2ch_{}", kind.label(), depth.label()),
                spec: PcmSpec {
                    sample_rate,
                    channels: 2,
                    depth,
                    total_samples: total,
                    kind,
                },
            });
        }
    }

    // Bit-exact vs acoustic-lossless demonstration: a single-LSB perturbation.
    cases.push(TestCase {
        name: "perturbed_2ch_16bit".to_string(),
        spec: PcmSpec {
            sample_rate,
            channels: 2,
            depth: BitDepth::B16,
            total_samples: frame_length + 256,
            kind: SignalKind::Perturbed,
        },
    });

    cases
}

fn run_case(ffmpeg: &Path, sample_dir: &Path, case: &TestCase) -> Result<report::FileReport> {
    let pcm: GeneratedPcm = generator::generate(case.spec.clone());
    let m4a_path = sample_dir.join(format!("{}.m4a", case.name));
    generator::encode_alac(ffmpeg, &pcm, &m4a_path)?;

    // Stage 1: independent MP4 walk -> cookie + packet table.
    let mut file = fs::File::open(&m4a_path)?;
    let mp4_info = mp4::parse_mp4(&mut file)?;
    let cookie = cookie::MagicCookie::parse(&mp4_info.cookie)?;

    if cookie.frame_length != 4096 {
        return Err(HarnessError(format!(
            "{}: unexpected frameLength {} (encoder contract is 4096)",
            case.name, cookie.frame_length
        )));
    }
    // The ALAC payload carries at most 24 usable bits: a 32-bit PCM source is
    // stored with cookie bitDepth 24. Everything else must match exactly.
    let expected_depth = match case.spec.depth {
        BitDepth::B32 => 24u8,
        other => other as u8,
    };
    if cookie.bit_depth != expected_depth {
        return Err(HarnessError(format!(
            "{}: cookie bitDepth {} != expected storage depth {}",
            case.name, cookie.bit_depth, expected_depth
        )));
    }
    if cookie.num_channels != case.spec.channels {
        return Err(HarnessError(format!(
            "{}: cookie channels {} != requested {}",
            case.name, cookie.num_channels, case.spec.channels
        )));
    }

    // Stage 2: slice each packet out of mdat and validate its compressed header.
    let ranges = mp4_info.packet_ranges()?;
    let file_bytes = fs::read(&m4a_path)?;
    let mut header_reports = Vec::with_capacity(ranges.len());
    for (index, (offset, size)) in ranges.iter().enumerate() {
        packet::check_packet_in_mdat(index, *offset, *size, &mp4_info.mdat_ranges)?;
        let start = *offset as usize;
        let end = start + *size as usize;
        if end > file_bytes.len() {
            return Err(HarnessError(format!("packet {index}: offset past end of file")));
        }
        let report = packet::parse_packet(index, &file_bytes[start..end], &cookie)?;
        header_reports.push(report);
    }

    // Stage 3: decode with Symphonia, packet by packet.
    let decoded = decoder::decode_file(&m4a_path, cookie.bit_depth)?;

    // Stage 4: boundary, sample-value and overflow verification.
    let boundary = checks::check_boundaries(
        &cookie,
        &header_reports,
        &decoded,
        mp4_info.timing_sample_count(),
    );
    // Compare/overflow against the actual stored bit depth (24 for B32 input).
    let stored_depth = match cookie.bit_depth {
        16 => BitDepth::B16,
        24 => BitDepth::B24,
        32 => BitDepth::B32,
        other => {
            return Err(HarnessError(format!("{}: unsupported stored bit depth {other}", case.name)));
        }
    };
    let compare = checks::compare_samples(&pcm, &decoded, stored_depth);
    let overflow = checks::check_overflow(stored_depth, &decoded);
    let perturbation = if case.spec.kind == SignalKind::Perturbed {
        let (demo_cmp, injected) =
            checks::compare_with_single_lsb_perturbation(&pcm, &decoded, stored_depth);
        Some(report::PerturbationDemo {
            channel: injected.channel,
            frame: injected.frame,
            bit_exact_after: demo_cmp.bit_exact,
            acoustic_after: demo_cmp.acoustic_lossless,
            max_relative_error: demo_cmp.max_relative_error,
        })
    } else {
        None
    };

    let spec_summary = format!(
        "{} ch, {}, {} Hz, {} source frames, signal={}",
        case.spec.channels,
        case.spec.depth.label(),
        case.spec.sample_rate,
        case.spec.total_samples,
        case.spec.kind.label()
    );

    Ok(report::FileReport {
        name: format!("{}.m4a", case.name),
        spec_summary,
        cookie,
        headers: header_reports,
        boundary,
        compare,
        overflow,
        decode: decoded,
        perturbation,
    })
}

fn main() -> Result<()> {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let sample_dir = root.join("artifacts/samples");
    let report_dir = root.join("artifacts/reports");
    fs::create_dir_all(&sample_dir)?;
    fs::create_dir_all(&report_dir)?;

    let ffmpeg = ffmpeg_path();
    assert!(ffmpeg.exists() || which(&ffmpeg), "ffmpeg binary not found; set FFMPEG env var");

    let cases = build_matrix();
    println!("Running {} ALAC sample cases...", cases.len());
    let mut reports = Vec::new();
    for case in &cases {
        print!("  {:<28} ... ", case.name);
        match run_case(&ffmpeg, &sample_dir, case) {
            Ok(r) => {
                let boundary = if r.boundary.anomalies.is_empty() { "boundary-ok" } else { "BOUNDARY-FAIL" };
                let values = if r.compare.bit_exact {
                    "bit-exact".to_string()
                } else if r.compare.acoustic_lossless {
                    "acoustic-only".to_string()
                } else {
                    "VALUE-FAIL".to_string()
                };
                let overflow = if r.overflow.violations == 0 { "overflow-ok" } else { "OVERFLOW" };
                println!("{boundary} / {values} / {overflow}");
                reports.push(r);
            }
            Err(e) => {
                println!("ERROR: {e}");
                return Err(e);
            }
        }
    }

    let text = report::render_text(&reports);
    let json = report::render_json(&reports);
    fs::write(report_dir.join("report.txt"), &text)?;
    fs::write(report_dir.join("report.json"), json)?;
    println!("\nWrote artifacts/reports/report.txt and report.json");

    let total_anomalies: usize = reports.iter().map(|r| r.boundary.anomalies.len()).sum();
    let total_overflow: usize = reports.iter().map(|r| r.overflow.violations).sum();
    let value_fail = reports
        .iter()
        .filter(|r| !r.compare.bit_exact && !r.compare.acoustic_lossless)
        .count();
    println!(
        "Summary: {} files, boundary anomalies={}, overflow violations={}, value failures={}",
        reports.len(),
        total_anomalies,
        total_overflow,
        value_fail
    );
    Ok(())
}

fn which(program: &Path) -> bool {
    std::env::var_os("PATH")
        .map(|paths| {
            std::env::split_paths(&paths).any(|dir| dir.join(program).is_file())
        })
        .unwrap_or(false)
}
