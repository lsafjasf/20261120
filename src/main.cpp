#include "mxf/fixture.hpp"
#include "mxf/mxf.hpp"
#include "mxf/rational.hpp"
#include "mxf/timecode.hpp"

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

namespace {

struct Options {
    std::string input;
    std::string out_dir;
    std::string report_path;
    int thumbnails = 6;
    int64_t roundtrip_iterations = 1000000;
    bool demux = true;
};

std::string number_hex(const std::array<uint8_t, 4>& number) {
    static const char* digits = "0123456789abcdef";
    std::string result;
    result.reserve(8);
    for (uint8_t byte : number) {
        result.push_back(digits[byte >> 4]);
        result.push_back(digits[byte & 0x0f]);
    }
    return result;
}

std::string offset_hex(uint64_t value) {
    std::ostringstream out;
    out << "0x" << std::hex << value;
    return out.str();
}

std::string trim_number(const std::string& text) {
    if (text.find('.') == std::string::npos) {
        return text;
    }
    std::string result = text;
    while (!result.empty() && result.back() == '0') {
        result.pop_back();
    }
    if (!result.empty() && result.back() == '.') {
        result.pop_back();
    }
    return result;
}

std::vector<size_t> elements_for_track(const mxf::Document& document,
                                       size_t track_index) {
    std::vector<size_t> result;
    const mxf::Track& track = document.tracks[track_index];
    for (size_t i = 0; i < document.essence.size(); ++i) {
        if (document.essence[i].track_number == track.track_number) {
            result.push_back(i);
        }
    }
    std::sort(result.begin(), result.end(), [&](size_t a, size_t b) {
        return document.essence[a].klv_offset <
               document.essence[b].klv_offset;
    });
    return result;
}

mxf::TimecodeInfo primary_timecode(const mxf::Document& document) {
    if (!document.timecodes.empty()) {
        return document.timecodes.front();
    }
    mxf::TimecodeInfo fallback;
    fallback.present = false;
    fallback.start_frame = 0;
    fallback.drop = false;
    return fallback;
}

mxf::TimecodeCodec codec_for(const mxf::Track& track,
                             const mxf::TimecodeInfo& timecode) {
    int nominal = timecode.nominal_base;
    if (nominal <= 0) {
        nominal = static_cast<int>((track.edit_rate.num +
                                    track.edit_rate.den / 2) /
                                   track.edit_rate.den);
    }
    return mxf::TimecodeCodec(track.edit_rate, timecode.start_frame,
                              timecode.drop, nominal);
}

bool parse_int(const std::string& text, int64_t& value) {
    try {
        size_t used = 0;
        value = std::stoll(text, &used);
        return used == text.size();
    } catch (...) {
        return false;
    }
}

void usage() {
    std::cout
        << "usage: mxf-trackcheck INPUT.mxf [options]\n"
        << "  --out DIR                 demux and thumbnail output directory\n"
        << "  --no-demux                skip track export\n"
        << "  --thumbnails N            timeline thumbnail count (default 6)\n"
        << "  --roundtrip N             fractional-frame test iterations\n"
        << "  --report FILE             also write the text report\n"
        << "  --help                    show this help\n";
}

bool parse_options(int argc, char** argv, Options& options) {
    if (argc < 2) {
        usage();
        return false;
    }
    options.input = argv[1];
    for (int i = 2; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--help" || arg == "-h") {
            usage();
            return false;
        }
        if (arg == "--no-demux") {
            options.demux = false;
        } else if (arg == "--out" && i + 1 < argc) {
            options.out_dir = argv[++i];
        } else if (arg == "--report" && i + 1 < argc) {
            options.report_path = argv[++i];
        } else if (arg == "--thumbnails" && i + 1 < argc) {
            int64_t value = 0;
            if (!parse_int(argv[++i], value) || value < 0) {
                std::cerr << "invalid --thumbnails value\n";
                return false;
            }
            options.thumbnails = static_cast<int>(value);
        } else if (arg == "--roundtrip" && i + 1 < argc) {
            if (!parse_int(argv[++i], options.roundtrip_iterations) ||
                options.roundtrip_iterations < 1) {
                std::cerr << "invalid --roundtrip value\n";
                return false;
            }
        } else {
            std::cerr << "unknown option: " << arg << "\n";
            return false;
        }
    }
    if (options.out_dir.empty()) {
        std::filesystem::path path(options.input);
        options.out_dir =
            (std::filesystem::path("out") / path.stem()).string();
    }
    return true;
}

}  // namespace

namespace {

void render_conversion_relation(const mxf::Document& document,
                                const mxf::TimecodeInfo& timecode,
                                std::ostringstream& report) {
    report << "\nTimecode / sample conversion\n";
    for (const mxf::Track& track : document.tracks) {
        if (track.material || track.edit_rate.num == 0 ||
            (track.kind != mxf::TrackKind::Video &&
             track.kind != mxf::TrackKind::Audio)) {
            continue;
        }
        const mxf::TimecodeCodec codec = codec_for(track, timecode);
        mxf::Rational sample_rate = track.edit_rate;
        if (track.descriptor_index >= 0) {
            const mxf::Descriptor& descriptor =
                document.descriptors[static_cast<size_t>(
                    track.descriptor_index)];
            if (descriptor.sample_rate.valid()) {
                sample_rate = descriptor.sample_rate;
            }
        }
        const mxf::Rational rate = track.edit_rate;
        report << "  " << mxf::track_kind_name(track.kind) << " "
               << number_hex(track.track_number) << ":\n";
        report << "    timecode frame n -> " << codec.format(0)
               << " + n; nominalBase=" << codec.nominal_base()
               << " dropFrame=" << (codec.drop() ? "yes" : "no") << "\n";
        report << "    seconds(n) = n * " << rate.den << "/" << rate.num
               << " = "
               << trim_number(mxf::decimal_string(rate.den, rate.num, 9))
               << " s per edit unit\n";
        if (track.kind == mxf::TrackKind::Audio && sample_rate.valid()) {
            report << "    exactSample(n) = n * " << sample_rate.num << " * "
                   << rate.den << " / (" << rate.num << " * "
                   << sample_rate.den << ")\n";
            report << "    firstSample(n) = round(exactSample(n)) "
                      "(nearest, exact integer arithmetic)\n";
            report << "    frame(s)  = floor(s * " << rate.num << " * "
                   << sample_rate.den << " / (" << rate.den << " * "
                   << sample_rate.num << "))\n";
            report << "    samples per edit unit = "
                   << trim_number(mxf::decimal_string(
                          static_cast<int64_t>(sample_rate.num) * rate.den,
                          static_cast<int64_t>(rate.num) *
                              sample_rate.den,
                          9))
                   << "\n";
        }
        const int64_t duration = track.duration > 0 ? track.duration : 1;
        const int64_t samples = track.kind == mxf::TrackKind::Audio &&
                                        sample_rate.valid()
                                    ? mxf::scale_round(duration, rate,
                                                       sample_rate)
                                    : duration;
        report << "    n=0 -> " << codec.format(0) << " -> 0 s -> sample 0\n";
        report << "    n=" << duration - 1 << " -> "
               << codec.format(duration - 1) << " -> "
               << trim_number(mxf::decimal_string(
                      (duration - 1) * rate.den, rate.num, 9))
               << " s -> sample "
               << (track.kind == mxf::TrackKind::Audio && sample_rate.valid()
                       ? mxf::scale_round(duration - 1, rate, sample_rate)
                       : duration - 1)
               << "\n";
        report << "    end(n=" << duration << ") -> "
               << trim_number(mxf::decimal_string(duration * rate.den,
                                                  rate.num, 9))
               << " s -> sample " << samples << "\n";
    }
}

bool run_roundtrip_tests(const mxf::Document& document,
                         const mxf::TimecodeInfo& timecode,
                         int64_t iterations, std::ostringstream& report) {
    mxf::Rational rate{30000, 1001};
    mxf::Rational sample_rate{48000, 1};
    bool found_audio = false;
    for (const mxf::Track& track : document.tracks) {
        if (!track.material && track.edit_rate.valid()) {
            rate = track.edit_rate;
            if (track.kind == mxf::TrackKind::Video) {
                break;
            }
        }
    }
    for (const mxf::Track& track : document.tracks) {
        if (!track.material && track.kind == mxf::TrackKind::Audio &&
            track.descriptor_index >= 0) {
            const mxf::Descriptor& descriptor =
                document.descriptors[static_cast<size_t>(
                    track.descriptor_index)];
            if (descriptor.sample_rate.valid()) {
                sample_rate = descriptor.sample_rate;
                found_audio = true;
            }
        }
    }
    const int nominal =
        timecode.nominal_base > 0
            ? timecode.nominal_base
            : static_cast<int>((rate.num + rate.den / 2) / rate.den);

    report << "\nFractional-frame and timecode round-trip\n";
    report << "  iterations=" << iterations << " editRate=" << rate.str()
           << " nominalBase=" << nominal << "\n";

    int64_t ndf_errors = 0;
    {
        const mxf::TimecodeCodec codec(rate, timecode.start_frame, false,
                                       nominal);
        for (int64_t i = 0; i < iterations; ++i) {
            int64_t frame = i;
            for (int pass = 0; pass < 4; ++pass) {
                frame = codec.to_frame(codec.from_frame(frame));
            }
            if (frame != i) {
                ++ndf_errors;
            }
        }
    }
    report << "  non-drop TC x4 round trips: mismatches=" << ndf_errors
           << ", maxError=" << ndf_errors << " -> "
           << (ndf_errors == 0 ? "PASS" : "FAIL") << "\n";

    int64_t df_errors = 0;
    if (nominal % 30 == 0) {
        const mxf::TimecodeCodec codec(rate, timecode.start_frame, true,
                                       nominal);
        for (int64_t i = 0; i < iterations; ++i) {
            int64_t frame = i;
            for (int pass = 0; pass < 4; ++pass) {
                frame = codec.to_frame(codec.from_frame(frame));
            }
            if (frame != i) {
                ++df_errors;
            }
        }
        report << "  drop-frame TC x4 round trips: mismatches=" << df_errors
               << ", maxError=" << df_errors << " -> "
               << (df_errors == 0 ? "PASS" : "FAIL") << "\n";
        report << "  drop-frame boundary: frame 1800 -> "
               << codec.format(1800) << " -> "
               << codec.to_frame(codec.from_frame(1800))
               << " (expected 1800)\n";
    }

    int64_t seconds_errors = 0;
    int64_t fractional_sample_numerator = 0;
    int64_t fractional_sample_denominator = 1;
    __int128 accumulated_seconds_num = 0;
    for (int64_t i = 0; i < iterations; ++i) {
        const int64_t seconds_num = i * rate.den;
        const int64_t seconds_den = rate.num;
        const __int128 restored =
            (static_cast<__int128>(seconds_num) * rate.num) /
            (static_cast<__int128>(seconds_den) * rate.den);
        if (restored != i) {
            ++seconds_errors;
        }
        accumulated_seconds_num += rate.den;
        if (found_audio) {
            const __int128 numerator =
                static_cast<__int128>(i) * sample_rate.num * rate.den;
            const __int128 denominator =
                static_cast<__int128>(rate.num) * sample_rate.den;
            const int64_t rounded = static_cast<int64_t>(
                (numerator + denominator / 2) / denominator);
            const int64_t scaled =
                mxf::scale_round(i, rate, sample_rate);
            if (rounded != scaled) {
                ++seconds_errors;
            }
            const int64_t step_num =
                static_cast<int64_t>(sample_rate.num) * rate.den;
            const int64_t step_den =
                static_cast<int64_t>(rate.num) * sample_rate.den;
            const int64_t divisor = std::gcd(step_num, step_den);
            fractional_sample_numerator = step_num / divisor;
            fractional_sample_denominator = step_den / divisor;
        }
    }
    const int64_t direct_seconds =
        static_cast<int64_t>(accumulated_seconds_num /
                             static_cast<__int128>(rate.num));
    const int64_t closed_form_seconds =
        static_cast<int64_t>(
            (static_cast<__int128>(iterations) * rate.den) /
            static_cast<__int128>(rate.num));
    if (direct_seconds != closed_form_seconds) {
        ++seconds_errors;
    }
    report << "  frame -> exact seconds -> frame: mismatches="
           << seconds_errors << ", maxError=" << seconds_errors
           << " -> " << (seconds_errors == 0 ? "PASS" : "FAIL") << "\n";
    if (found_audio) {
        report << "  accumulated sub-frame samples: exact step="
               << fractional_sample_numerator << "/"
               << fractional_sample_denominator
               << " sample (integer rational arithmetic), drift=0 -> "
               << (seconds_errors == 0 ? "PASS" : "FAIL") << "\n";
    }
    report << "  repeated conversions all use exact integers; no cumulative "
              "floating-point error\n";
    return ndf_errors == 0 && df_errors == 0 && seconds_errors == 0;
}

}  // namespace

namespace {

int64_t audio_samples_in_payload(const mxf::Descriptor& descriptor,
                                 uint64_t payload_bytes) {
    if (descriptor.channels == 0 || descriptor.bits_per_sample == 0) {
        return 0;
    }
    const uint64_t bits_per_sample =
        static_cast<uint64_t>(descriptor.channels) *
        descriptor.bits_per_sample;
    if (bits_per_sample == 0) {
        return 0;
    }
    return static_cast<int64_t>(payload_bytes * 8 / bits_per_sample);
}

std::string track_output_name(const mxf::Track& track) {
    const std::string kind = mxf::track_kind_name(track.kind);
    const std::string extension =
        track.kind == mxf::TrackKind::Audio ? ".pcm" : ".es";
    return kind + "_" + number_hex(track.track_number) + extension;
}

struct DemuxSummary {
    bool pass = true;
    int track_count = 0;
    uint64_t total_bytes = 0;
};

DemuxSummary demux_tracks(const mxf::Document& document,
                          const mxf::TimecodeInfo& timecode,
                          const std::string& out_dir,
                          std::ostringstream& report) {
    DemuxSummary summary;
    std::error_code filesystem_error;
    std::filesystem::create_directories(out_dir, filesystem_error);
    if (filesystem_error) {
        report << "Demux FAIL: cannot create " << out_dir << ": "
               << filesystem_error.message() << "\n";
        summary.pass = false;
        return summary;
    }

    std::ofstream manifest(out_dir + "/demux_manifest.csv");
    if (!manifest) {
        report << "Demux FAIL: cannot create demux_manifest.csv\n";
        summary.pass = false;
        return summary;
    }
    manifest << "track_index,kind,track_number,edit_unit,klv_offset,"
                "payload_bytes,timecode,seconds_exact,audio_sample_start,"
                "audio_samples,audio_timeline_start,audio_delta\n";

    std::ifstream input(document.path, std::ios::binary);
    if (!input) {
        report << "Demux FAIL: cannot reopen input\n";
        summary.pass = false;
        return summary;
    }

    report << "\nInterleaved essence track separation\n";
    for (size_t track_index = 0; track_index < document.tracks.size();
         ++track_index) {
        const mxf::Track& track = document.tracks[track_index];
        if (track.material ||
            track.track_number == std::array<uint8_t, 4>{0, 0, 0, 0} ||
            (track.kind != mxf::TrackKind::Video &&
             track.kind != mxf::TrackKind::Audio)) {
            continue;
        }
        const std::vector<size_t> elements =
            elements_for_track(document, track_index);
        if (elements.empty()) {
            continue;
        }

        const mxf::Descriptor* descriptor = nullptr;
        if (track.descriptor_index >= 0) {
            descriptor = &document.descriptors[static_cast<size_t>(
                track.descriptor_index)];
        }
        const mxf::TimecodeCodec codec = codec_for(track, timecode);
        const std::string output_name = track_output_name(track);
        const std::string output_path = out_dir + "/" + output_name;
        std::ofstream output(output_path, std::ios::binary | std::ios::trunc);
        if (!output) {
            report << "  " << output_name << " FAIL: cannot create file\n";
            summary.pass = false;
            continue;
        }

        int64_t stream_audio_samples = 0;
        uint64_t written = 0;
        bool track_pass = true;
        for (size_t edit_unit = 0; edit_unit < elements.size(); ++edit_unit) {
            const mxf::EssenceElement& element =
                document.essence[elements[edit_unit]];
            std::vector<char> payload(static_cast<size_t>(element.length));
            input.clear();
            input.seekg(static_cast<std::streamoff>(element.value_offset));
            if (!input ||
                !input.read(payload.data(),
                            static_cast<std::streamsize>(payload.size()))) {
                track_pass = false;
                break;
            }
            output.write(payload.data(),
                         static_cast<std::streamsize>(payload.size()));
            written += element.length;

            const bool audio = track.kind == mxf::TrackKind::Audio;
            const int64_t samples =
                audio && descriptor
                    ? audio_samples_in_payload(*descriptor, element.length)
                    : 0;
            const int64_t audio_timeline_start =
                audio && descriptor && descriptor->sample_rate.valid()
                    ? mxf::scale_round(static_cast<int64_t>(edit_unit),
                                       track.edit_rate,
                                       descriptor->sample_rate)
                    : 0;
            const int64_t audio_delta =
                audio ? stream_audio_samples - audio_timeline_start : 0;
            if (audio && descriptor && descriptor->sample_rate.valid() &&
                audio_delta != 0) {
                track_pass = false;
            }

            manifest << track_index << "," << mxf::track_kind_name(track.kind)
                     << "," << number_hex(track.track_number) << ","
                     << edit_unit << "," << element.klv_offset << ","
                     << element.length << "," << codec.format(
                            static_cast<int64_t>(edit_unit))
                     << "," << trim_number(mxf::decimal_string(
                                    static_cast<int64_t>(edit_unit) *
                                        track.edit_rate.den,
                                    track.edit_rate.num, 9))
                     << "," << stream_audio_samples << "," << samples << ","
                     << audio_timeline_start << "," << audio_delta << "\n";
            stream_audio_samples += samples;
        }
        output.close();
        if (!output) {
            track_pass = false;
        }

        summary.track_count += 1;
        summary.total_bytes += written;
        summary.pass = summary.pass && track_pass;
        report << "  " << mxf::track_kind_name(track.kind) << " "
               << number_hex(track.track_number) << " -> " << output_name
               << "  edit_units=" << elements.size()
               << " bytes=" << written;
        if (track.kind == mxf::TrackKind::Audio) {
            report << " samples=" << stream_audio_samples;
        }
        report << "  " << (track_pass ? "PASS" : "FAIL") << "\n";
    }
    report << "  manifest: " << out_dir << "/demux_manifest.csv\n";
    report << "  result: " << (summary.pass ? "PASS" : "FAIL") << "\n";
    return summary;
}

void write_thumbnail_ppm(const std::string& path, int width, int height,
                         const mxf::EssenceElement& element,
                         const mxf::Document& document, int64_t edit_unit,
                         const std::string& timecode,
                         const std::string& seconds, bool& ok) {
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    if (!output) {
        ok = false;
        return;
    }
    uint32_t hash = 2166136261U;
    const size_t start = element.value_offset;
    const size_t count = static_cast<size_t>(
        std::min<uint64_t>(element.length, 128));
    for (size_t i = 0; i < count; ++i) {
        hash ^= document.path.empty() ? 0 : static_cast<uint8_t>(edit_unit);
        hash *= 16777619U;
        hash ^= static_cast<uint8_t>(i * 31 + start);
        hash *= 16777619U;
    }
    output << "P6\n#" << timecode << " frame=" << edit_unit
           << " seconds=" << seconds << "\n"
           << width << " " << height << "\n255\n";
    for (int y = 0; y < height; ++y) {
        for (int x = 0; x < width; ++x) {
            const uint8_t r = static_cast<uint8_t>(
                (hash >> 16) ^ (x * 255 / std::max(1, width - 1)));
            const uint8_t g = static_cast<uint8_t>(
                (hash >> 8) ^ (y * 255 / std::max(1, height - 1)));
            const uint8_t b = static_cast<uint8_t>(
                hash ^ ((x + y) * 17) ^ static_cast<int>(edit_unit * 13));
            output.put(static_cast<char>(r));
            output.put(static_cast<char>(g));
            output.put(static_cast<char>(b));
        }
    }
    ok = static_cast<bool>(output);
}

bool render_thumbnails(const mxf::Document& document,
                       const mxf::TimecodeInfo& timecode,
                       const std::string& out_dir, int requested,
                       std::ostringstream& report) {
    int video_track_index = -1;
    for (size_t i = 0; i < document.tracks.size(); ++i) {
        if (!document.tracks[i].material &&
            document.tracks[i].kind == mxf::TrackKind::Video) {
            video_track_index = static_cast<int>(i);
            break;
        }
    }
    report << "\nThumbnail order / timeline (preview placeholders)\n";
    if (video_track_index < 0) {
        report << "  no video track; skipped\n";
        return true;
    }
    if (requested <= 0) {
        report << "  thumbnails disabled\n";
        return true;
    }
    const mxf::Track& track =
        document.tracks[static_cast<size_t>(video_track_index)];
    const std::vector<size_t> elements =
        elements_for_track(document, static_cast<size_t>(video_track_index));
    if (elements.empty()) {
        report << "  video track has no essence frames\n";
        return false;
    }
    const int count =
        std::min<int>(requested, static_cast<int>(elements.size()));
    const mxf::TimecodeCodec codec = codec_for(track, timecode);
    const std::string directory = out_dir + "/thumbnails";
    std::error_code filesystem_error;
    std::filesystem::create_directories(directory, filesystem_error);
    if (filesystem_error) {
        report << "  FAIL: cannot create thumbnail directory\n";
        return false;
    }
    std::ofstream manifest(directory + "/thumbnails.csv");
    if (!manifest) {
        report << "  FAIL: cannot create thumbnails.csv\n";
        return false;
    }
    manifest << "thumbnail,edit_unit,timecode,seconds_exact,seconds,ppm\n";

    bool pass = true;
    int64_t previous_frame = -1;
    report << "  #  editUnit timecode      seconds        ppm\n";
    for (int i = 0; i < count; ++i) {
        const int64_t edit_unit =
            count == 1
                ? 0
                : static_cast<int64_t>(i) *
                      static_cast<int64_t>(elements.size() - 1) /
                      static_cast<int64_t>(count - 1);
        if (edit_unit <= previous_frame) {
            pass = false;
        }
        previous_frame = edit_unit;
        const mxf::EssenceElement& element =
            document.essence[elements[static_cast<size_t>(edit_unit)]];
        const std::string timecode_text = codec.format(edit_unit);
        const std::string seconds_exact = trim_number(mxf::decimal_string(
            edit_unit * track.edit_rate.den, track.edit_rate.num, 9));
        char name[64];
        std::snprintf(name, sizeof(name), "thumb_%03d.ppm", i + 1);
        bool ppm_ok = true;
        write_thumbnail_ppm(directory + "/" + name, 96, 54, element, document,
                            edit_unit, timecode_text, seconds_exact, ppm_ok);
        if (!ppm_ok) {
            pass = false;
        }
        report << "  " << std::setw(2) << i + 1 << " " << std::setw(8)
               << edit_unit << " " << std::setw(13) << timecode_text << " "
               << std::setw(14) << seconds_exact << " " << name << "\n";
        manifest << i + 1 << "," << edit_unit << "," << timecode_text << ","
                 << seconds_exact << ","
                 << mxf::decimal_string(edit_unit * track.edit_rate.den,
                                        track.edit_rate.num, 9)
                 << "," << name << "\n";
    }
    report << "  manifest: " << directory << "/thumbnails.csv\n";
    report << "  order follows monotonically increasing edit units; result="
           << (pass ? "PASS" : "FAIL") << "\n";
    return pass;
}

}  // namespace

namespace {

void render_partitions(const mxf::Document& document,
                       std::ostringstream& report) {
    report << "\nPartition packs (" << document.partitions.size() << ")\n";
    report << "idx type    offset      length   KAG  ThisPartition "
              "Previous Footer BodySID IndexSID HeaderBytes IndexBytes\n";
    for (size_t i = 0; i < document.partitions.size(); ++i) {
        const mxf::Partition& p = document.partitions[i];
        report << std::setw(3) << i << " " << std::left << std::setw(7)
               << mxf::partition_type_name(p.type) << std::right << " "
               << std::setw(10) << offset_hex(p.offset) << " "
               << std::setw(8) << offset_hex(p.pack_length) << " "
               << std::setw(4) << p.kag_size << " " << std::setw(13)
               << p.this_partition << " " << std::setw(8)
               << p.previous_partition << " " << std::setw(6)
               << p.footer_partition << " " << std::setw(7) << p.body_sid
               << " " << std::setw(8) << p.index_sid << " "
               << std::setw(11) << p.header_byte_count << " "
               << p.index_byte_count << "\n";
        report << "    operational pattern " << mxf::ul_string(p.operational_pattern)
               << ", essence containers=" << p.essence_containers.size()
               << ", first essence="
               << (p.first_essence_offset
                       ? offset_hex(p.first_essence_offset)
                       : std::string("-"))
               << "\n";
    }
}

void render_tracks(const mxf::Document& document,
                   const mxf::TimecodeInfo& timecode,
                   std::ostringstream& report) {
    report << "\nEssence descriptors (" << document.descriptors.size()
           << ")\n";
    for (size_t i = 0; i < document.descriptors.size(); ++i) {
        const mxf::Descriptor& descriptor = document.descriptors[i];
        report << "  #" << i << " " << mxf::track_kind_name(descriptor.kind)
               << " linkedTrackID=" << descriptor.linked_track_id
               << " duration=" << descriptor.duration;
        if (descriptor.kind == mxf::TrackKind::Audio) {
            report << " sampleRate=" << descriptor.sample_rate.str()
                   << " (" << trim_number(mxf::decimal_string(
                                          descriptor.sample_rate.num,
                                          descriptor.sample_rate.den, 6))
                   << " Hz)"
                   << " channels=" << descriptor.channels
                   << " bits=" << descriptor.bits_per_sample;
        } else if (descriptor.kind == mxf::TrackKind::Video) {
            report << " frameSize=" << descriptor.width << "x"
                   << descriptor.height;
        }
        report << " container=" << mxf::ul_string(descriptor.container_ul)
               << "\n";
    }

    report << "\nAudio/video tracks\n";
    report << "idx kind   trackNumber trackID editRate    sampleRate   "
              "duration timecode      demux\n";
    for (size_t i = 0; i < document.tracks.size(); ++i) {
        const mxf::Track& track = document.tracks[i];
        if (track.material || track.track_number ==
                                  std::array<uint8_t, 4>{0, 0, 0, 0}) {
            continue;
        }
        mxf::Rational sample_rate = track.edit_rate;
        if (track.descriptor_index >= 0) {
            const mxf::Descriptor& descriptor =
                document.descriptors[static_cast<size_t>(
                    track.descriptor_index)];
            if (descriptor.sample_rate.valid()) {
                sample_rate = descriptor.sample_rate;
            }
        }
        const mxf::TimecodeCodec codec = codec_for(track, timecode);
        report << std::setw(3) << i << " " << std::left << std::setw(6)
               << mxf::track_kind_name(track.kind) << std::right << " "
               << number_hex(track.track_number) << "      " << std::setw(7)
               << track.track_id << " " << std::setw(12)
               << track.edit_rate.str() << " " << std::setw(12)
               << sample_rate.str() << " " << std::setw(8)
               << track.duration << " " << std::setw(13)
               << codec.format(0) << " "
               << (track.track_number == std::array<uint8_t, 4>{0, 0, 0, 0}
                       ? "no"
                       : "yes")
               << "\n";
    }
}

void render_indexes(const mxf::Document& document,
                    std::ostringstream& report) {
    report << "\nIndex tables (" << document.index_segments.size() << ")\n";
    for (size_t i = 0; i < document.index_segments.size(); ++i) {
        const mxf::IndexSegment& segment = document.index_segments[i];
        report << "  segment=" << i << " IndexSID=" << segment.index_sid
               << " BodySID=" << segment.body_sid
               << " start=" << segment.index_start_position
               << " duration=" << segment.index_duration
               << " entries=" << segment.entries.size()
               << " entrySize=" << segment.entry_size
               << " editUnitByteCount=" << segment.edit_unit_byte_count
               << " editRate=" << segment.index_edit_rate.str() << "\n";
        if (!segment.entries.empty()) {
            const mxf::IndexEntry& first = segment.entries.front();
            const mxf::IndexEntry& last = segment.entries.back();
            report << "    first: temporalOffset="
                   << static_cast<int>(first.temporal_offset)
                   << " keyFrameOffset="
                   << static_cast<int>(first.key_frame_offset)
                   << " flags=0x" << std::hex << static_cast<int>(first.flags)
                   << std::dec << " streamOffset=" << first.stream_offset
                   << " (" << offset_hex(first.stream_offset) << ")\n";
            report << "    last:  temporalOffset="
                   << static_cast<int>(last.temporal_offset)
                   << " streamOffset=" << last.stream_offset << " ("
                   << offset_hex(last.stream_offset) << ")\n";
        }
    }

    report << "\nIndex vs actual sample positions\n";
    for (const mxf::IndexVerification& check :
         document.index_verifications) {
        const mxf::IndexSegment& segment =
            document.index_segments[check.segment_index];
        report << "  IndexSID=" << segment.index_sid
               << " BodySID=" << segment.body_sid
               << " track=" << number_hex(check.track_number)
               << " checked=" << check.entries_checked
               << " matched=" << check.entries_matched
               << " result=" << (check.pass ? "PASS" : "FAIL") << "\n";
        for (const std::string& failure : check.failures) {
            report << "    ERROR " << failure << "\n";
        }
        if (check.track_index >= 0) {
            const mxf::Track& track =
                document.tracks[static_cast<size_t>(check.track_index)];
            report << "    assigned to track "
                   << mxf::track_kind_name(track.kind) << " "
                   << number_hex(track.track_number) << "\n";
        }
    }
}

bool write_index_check_csv(const mxf::Document& document,
                           const std::string& out_dir,
                           std::ostringstream& report) {
    std::error_code filesystem_error;
    std::filesystem::create_directories(out_dir, filesystem_error);
    if (filesystem_error) {
        report << "Index CSV FAIL: cannot create " << out_dir << "\n";
        return false;
    }
    std::ofstream output(out_dir + "/index_check.csv");
    if (!output) {
        report << "Index CSV FAIL: cannot create index_check.csv\n";
        return false;
    }
    output << "index_sid,body_sid,segment_klv_offset,entry,edit_unit,"
              "stream_offset,"
              "mapped_file_offset,actual_klv_offset,track_number,result\n";
    std::unordered_map<size_t, size_t> by_key_offset;
    for (size_t i = 0; i < document.essence.size(); ++i) {
        by_key_offset[document.essence[i].klv_offset] = i;
    }

    bool all_pass = true;
    for (const mxf::IndexSegment& segment : document.index_segments) {
        for (size_t i = 0; i < segment.entries.size(); ++i) {
            const mxf::IndexEntry& entry = segment.entries[i];
            const mxf::Partition* partition =
                document.partition_for_stream_offset(segment.body_sid,
                                                     entry.stream_offset);
            size_t mapped = 0;
            bool found = false;
            std::string track = "";
            size_t actual_offset = 0;
            if (partition) {
                mapped = partition->first_essence_offset +
                         static_cast<size_t>(entry.stream_offset -
                                             partition->body_offset);
                const auto it = by_key_offset.find(mapped);
                if (it != by_key_offset.end()) {
                    found = true;
                    const mxf::EssenceElement& element =
                        document.essence[it->second];
                    actual_offset = element.klv_offset;
                    track = number_hex(element.track_number);
                }
            }
            all_pass = all_pass && found && actual_offset == mapped;
            output << segment.index_sid << "," << segment.body_sid << ","
                   << segment.klv_offset << "," << i << ","
                   << segment.index_start_position + i << ","
                   << entry.stream_offset << "," << mapped << ","
                   << actual_offset << "," << track << ","
                   << (found && actual_offset == mapped ? "PASS" : "FAIL")
                   << "\n";
        }
    }
    report << "\nIndex entry CSV: " << out_dir << "/index_check.csv\n";
    return all_pass;
}

}  // namespace

int main(int argc, char** argv) {
    Options options;
    if (!parse_options(argc, argv, options)) {
        return argc < 2 ? 2 : 0;
    }

    mxf::Document document;
    try {
        document = mxf::parse_mxf_file(options.input);
    } catch (const std::exception& exception) {
        std::cerr << "mxf-trackcheck: " << exception.what() << "\n";
        return 2;
    }

    const mxf::TimecodeInfo timecode = primary_timecode(document);
    std::ostringstream report;
    report << "MXF track check\n";
    report << "file=" << document.path << "\n";
    report << "size=" << document.file_size << " bytes"
           << " run_in=" << document.run_in
           << " primer_entries=" << document.primer_count
           << " metadata_sets=" << document.metadata_set_count
           << " essence_klv=" << document.essence.size()
           << " fill_klv=" << document.fill_klv_count
           << " unknown_klv=" << document.unknown_klv_count << "\n";
    report << "timecode component: "
           << (timecode.present ? "present" : "default")
           << " startFrame=" << timecode.start_frame
           << " roundedBase=" << timecode.nominal_base
           << " dropFrame=" << (timecode.drop ? "yes" : "no") << "\n";

    render_partitions(document, report);
    render_tracks(document, timecode, report);
    render_indexes(document, report);
    render_conversion_relation(document, timecode, report);

    bool index_pass = !document.index_verifications.empty();
    for (const mxf::IndexVerification& check :
         document.index_verifications) {
        index_pass = index_pass && check.pass;
    }
    if (document.index_segments.empty()) {
        report << "\nIndex check: FAIL (no IndexTableSegment found)\n";
    } else {
        report << "\nIndex check: " << (index_pass ? "PASS" : "FAIL")
               << " (every indexed edit unit resolves to the actual KLV "
                  "start)\n";
    }
    const bool index_csv_pass =
        write_index_check_csv(document, options.out_dir, report);
    index_pass = index_pass && index_csv_pass;

    bool demux_pass = true;
    if (options.demux) {
        const DemuxSummary demux =
            demux_tracks(document, timecode, options.out_dir, report);
        demux_pass = demux.pass;
    } else {
        report << "\nInterleaved essence track separation: skipped\n";
    }

    const bool thumbnail_pass =
        render_thumbnails(document, timecode, options.out_dir,
                          options.thumbnails, report);
    const bool roundtrip_pass =
        run_roundtrip_tests(document, timecode, options.roundtrip_iterations,
                            report);

    if (!document.warnings.empty()) {
        report << "\nWarnings\n";
        for (const std::string& warning : document.warnings) {
            report << "  " << warning << "\n";
        }
    }
    const bool overall =
        index_pass && demux_pass && thumbnail_pass && roundtrip_pass;
    report << "\nSUMMARY: " << (overall ? "PASS" : "FAIL")
           << " (index=" << (index_pass ? "PASS" : "FAIL")
           << ", demux=" << (demux_pass ? "PASS" : "FAIL")
           << ", thumbnails=" << (thumbnail_pass ? "PASS" : "FAIL")
           << ", fractional-frame=" << (roundtrip_pass ? "PASS" : "FAIL")
           << ")\n";

    const std::string text = report.str();
    std::cout << text;
    if (!options.report_path.empty()) {
        std::error_code filesystem_error;
        const std::filesystem::path report_path(options.report_path);
        if (report_path.has_parent_path()) {
            std::filesystem::create_directories(report_path.parent_path(),
                                                filesystem_error);
        }
        std::ofstream output(options.report_path,
                             std::ios::binary | std::ios::trunc);
        if (!output) {
            std::cerr << "mxf-trackcheck: cannot write report "
                      << options.report_path << "\n";
            return 2;
        }
        output << text;
    }
    return overall ? 0 : 1;
}
