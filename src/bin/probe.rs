use std::env;
use std::path::PathBuf;
use alac_harness::generator::{generate, BitDepth, PcmSpec, SignalKind};
fn main() {
    let path = PathBuf::from(env::args().nth(1).unwrap());
    let mut f = std::fs::File::open(&path).unwrap();
    let info = alac_harness::mp4::parse_mp4(&mut f).unwrap();
    let cookie = alac_harness::cookie::MagicCookie::parse(&info.cookie).unwrap();
    let dec = alac_harness::decoder::decode_file(&path, cookie.bit_depth).unwrap();
    let spec = PcmSpec { sample_rate: 48000, channels: cookie.num_channels,
        depth: BitDepth::B32, total_samples: dec.planar[0].len(), kind: SignalKind::Tones };
    let pcm = generate(spec);
    let cmp = alac_harness::checks::compare_samples(&pcm, &dec, BitDepth::B24);
    println!("stored depth {} bit_exact {} acoustic {} maxdiff {} maxrel {:.3e}",
        cookie.bit_depth, cmp.bit_exact, cmp.acoustic_lossless, cmp.max_abs_diff, cmp.max_relative_error);
    for m in cmp.mismatches.iter().take(5) { println!("{:?}", m); }
    println!("src first5 {:?}", &pcm.planar[0][..5]);
    println!("dec first5 {:?}", &dec.planar[0][..5]);
}
