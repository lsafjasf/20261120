#include "mxf/fixture.hpp"
#include "mxf/mxf.hpp"
#include "mxf/rational.hpp"
#include "mxf/timecode.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

namespace {

int failures = 0;

void check(bool condition, const std::string& message) {
    if (!condition) {
        std::cerr << "FAIL: " << message << "\n";
        ++failures;
    }
}

std::string temp_path(const std::string& name) {
    const auto stamp =
        std::chrono::steady_clock::now().time_since_epoch().count();
    return (std::filesystem::temp_directory_path() /
            ("mxf_trackcheck_" + std::to_string(stamp) + "_" + name))
        .string();
}

void test_timecode() {
    const mxf::Rational rate{30000, 1001};
    const mxf::TimecodeCodec drop(rate, 0, true, 30);
    check(drop.format(1799) == "00:00:59;29", "last frame of first minute");
    check(drop.format(1800) == "00:01:00;02", "first dropped minute start");
    check(drop.format(17982) == "00:10:00;00", "ten minute boundary");
    check(drop.to_frame(drop.from_frame(1800)) == 1800,
          "drop-frame boundary round-trip");

    const mxf::TimecodeCodec ndf(rate, 0, false, 30);
    check(ndf.format(1800) == "00:01:00:00", "non-drop timecode");
    for (int64_t frame = 0; frame < 1000000; ++frame) {
        if (drop.to_frame(drop.from_frame(frame)) != frame ||
            ndf.to_frame(ndf.from_frame(frame)) != frame) {
            check(false, "timecode round-trip drift at frame " +
                             std::to_string(frame));
            break;
        }
    }

    check(mxf::scale_floor(1, rate, mxf::Rational(48000, 1)) == 1601,
          "floor sample boundary");
    check(mxf::scale_round(1, rate, mxf::Rational(48000, 1)) == 1602,
          "nearest sample boundary");
    check(mxf::scale_round(30, rate, mxf::Rational(48000, 1)) == 48048,
          "exact end sample boundary");
}

std::vector<uint8_t> read_bytes(const std::string& path) {
    std::ifstream input(path, std::ios::binary);
    return std::vector<uint8_t>(std::istreambuf_iterator<char>(input),
                                std::istreambuf_iterator<char>());
}

void write_bytes(const std::string& path,
                 const std::vector<uint8_t>& bytes) {
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    output.write(reinterpret_cast<const char*>(bytes.data()),
                 static_cast<std::streamsize>(bytes.size()));
}

bool corrupt_first_index_offset(const std::string& source,
                                const std::string& destination) {
    std::vector<uint8_t> bytes = read_bytes(source);
    const std::array<uint8_t, 16> key = {
        0x06, 0x0e, 0x2b, 0x34, 0x02, 0x53, 0x01, 0x01,
        0x0d, 0x01, 0x02, 0x01, 0x01, 0x10, 0x01, 0x00};
    for (size_t i = 0; i + 16 <= bytes.size(); ++i) {
        if (!std::equal(key.begin(), key.end(), bytes.begin() +
                                                 static_cast<std::ptrdiff_t>(i))) {
            continue;
        }
        size_t cursor = i + 16;
        const uint8_t first = bytes[cursor++];
        size_t length = first;
        if (first >= 0x80) {
            const int count = first & 0x7f;
            length = 0;
            for (int j = 0; j < count; ++j) {
                length = (length << 8) | bytes[cursor++];
            }
        }
        const size_t end = cursor + length;
        while (cursor + 4 <= end) {
            const uint16_t tag =
                static_cast<uint16_t>((bytes[cursor] << 8) |
                                      bytes[cursor + 1]);
            const uint16_t size =
                static_cast<uint16_t>((bytes[cursor + 2] << 8) |
                                      bytes[cursor + 3]);
            if (tag == 0x3f0a && size >= 11) {
                const size_t entry = cursor + 4 + 8;
                bytes[entry + 10] ^= 0x02;
                write_bytes(destination, bytes);
                return true;
            }
            cursor += 4 + size;
        }
    }
    return false;
}

void test_fixture() {
    const std::string path = temp_path("sample.mxf");
    const std::string corrupt = temp_path("corrupt.mxf");
    mxf::FixtureOptions options;
    std::string error;
    check(mxf::write_sample_mxf(path, options, error),
          "write fixture: " + error);

    const mxf::Document document = mxf::parse_mxf_file(path);
    check(document.partitions.size() == 3, "three partitions");
    check(document.descriptors.size() == 2, "two descriptors");
    check(document.essence.size() == 60, "interleaved essence count");
    check(document.index_segments.size() == 2, "two index segments");
    check(document.index_verifications.size() == 2,
          "two index verifications");
    check(!document.timecodes.empty() && document.timecodes.front().drop,
          "drop-frame timecode component");

    int video_tracks = 0;
    int audio_tracks = 0;
    for (const mxf::Track& track : document.tracks) {
        if (track.material) {
            continue;
        }
        if (track.kind == mxf::TrackKind::Video) {
            ++video_tracks;
        } else if (track.kind == mxf::TrackKind::Audio) {
            ++audio_tracks;
            check(track.descriptor_index >= 0, "audio descriptor mapping");
            const mxf::Descriptor& descriptor =
                document.descriptors[static_cast<size_t>(
                    track.descriptor_index)];
            check(descriptor.sample_rate == mxf::Rational(48000, 1),
                  "audio sample rate");
        }
    }
    check(video_tracks == 1 && audio_tracks == 1,
          "one video and one audio source track");
    for (const mxf::IndexVerification& verification :
         document.index_verifications) {
        check(verification.pass, "index verification passes");
    }
    check(document.essence[0].klv_offset < document.essence[1].klv_offset,
          "interleaved essence is ordered by file position");
    check(document.essence[0].track_number !=
              document.essence[1].track_number,
          "adjacent essence KLVs belong to different tracks");

    check(corrupt_first_index_offset(path, corrupt),
          "corrupt index offset");
    const mxf::Document damaged = mxf::parse_mxf_file(corrupt);
    bool detected = false;
    for (const mxf::IndexVerification& verification :
         damaged.index_verifications) {
        detected = detected || !verification.pass;
    }
    check(detected, "corrupt index offset is detected");

    std::filesystem::remove(path);
    std::filesystem::remove(corrupt);
}

}  // namespace

int main() {
    test_timecode();
    test_fixture();
    if (failures != 0) {
        std::cerr << failures << " test(s) failed\n";
        return 1;
    }
    std::cout << "all mxf tests passed\n";
    return 0;
}
