//! Negative tests: mutate a valid ALAC file and confirm the independent
//! cookie/header/boundary validators reject the corruption.

use std::path::PathBuf;

use alac_harness::{cookie, mp4, packet};

fn load(path: &std::path::Path) -> (mp4::Mp4Info, cookie::MagicCookie, Vec<u8>, Vec<(u64, u32)>) {
    let mut f = std::fs::File::open(path).unwrap();
    let info = mp4::parse_mp4(&mut f).unwrap();
    let ck = cookie::MagicCookie::parse(&info.cookie).unwrap();
    let bytes = std::fs::read(path).unwrap();
    let ranges = info.packet_ranges().unwrap();
    (info, ck, bytes, ranges)
}

fn expect_err(label: &str, result: alac_harness::Result<()>) {
    match result {
        Ok(()) => {
            eprintln!("NEGATIVE TEST FAIL: {label} was accepted");
            std::process::exit(1);
        }
        Err(e) => println!("  [reject] {label}: {e}"),
    }
}

fn main() {
    let path = PathBuf::from(std::env::args().nth(1).expect("m4a path"));
    let (info, ck, bytes, ranges) = load(&path);
    println!("Negative tests on {} ({} packets)", path.display(), ranges.len());

    // 1. Corrupt the magic cookie's frame_length field.
    {
        let mut bad = info.cookie.clone();
        bad[0..4].copy_from_slice(&1234u32.to_be_bytes());
        expect_err("cookie frame_length tamper", cookie::MagicCookie::parse(&bad).map(|_| ()));
    }

    // 2. Corrupt the cookie bit depth to an illegal value.
    {
        let mut bad = info.cookie.clone();
        bad[5] = 33;
        expect_err("cookie illegal bitDepth", cookie::MagicCookie::parse(&bad).map(|_| ()));
    }

    // 3. Corrupt the cookie channel count / layout mismatch.
    {
        let mut bad = info.cookie.clone();
        bad[9] = 7;
        let bad_ck = cookie::MagicCookie::parse(&bad).unwrap();
        let decoded = alac_harness::decoder::decode_file(&path, bad_ck.bit_depth).unwrap();
        let result = if bad_ck.num_channels as usize == decoded.channels {
            Ok(())
        } else {
            Err(alac_harness::HarnessError(format!(
                "cookie claims {} channels but decoded stream has {}",
                bad_ck.num_channels, decoded.channels
            )))
        };
        expect_err("cookie channel tamper vs decoded channels", result);
    }

    // 4. Truncate the first packet so its header overruns the slice.
    {
        let (off, size) = ranges[0];
        let cut = (size / 2).max(4) as usize;
        let slice = &bytes[off as usize..off as usize + cut];
        expect_err("truncated packet", packet::parse_packet(0, slice, &ck).map(|_| ()));
    }

    // 5. Point a packet outside all mdat ranges.
    {
        expect_err(
            "packet outside mdat",
            packet::check_packet_in_mdat(0, 1, 10, &info.mdat_ranges),
        );
    }

    // 6. Garbage the first packet's leading bits so element parsing fails.
    {
        let (off, _size) = ranges[0];
        let mut garbled = bytes.clone();
        garbled[off as usize] = 0xFF;
        let slice = &garbled[off as usize..off as usize + ranges[0].1 as usize];
        expect_err("garbled element header", packet::parse_packet(0, slice, &ck).map(|_| ()));
    }

    println!("All negative tests rejected as expected.");
}
