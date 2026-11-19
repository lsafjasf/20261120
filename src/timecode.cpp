#include "mxf/timecode.hpp"

#include <algorithm>
#include <cstdio>

namespace mxf {

namespace {

std::string format_timecode(int hours, int minutes, int seconds, int frames,
                            bool drop) {
    char buffer[64];
    std::snprintf(buffer, sizeof(buffer), "%02d:%02d:%02d%c%02d", hours,
                  minutes, seconds, drop ? ';' : ':', frames);
    return buffer;
}

}  // namespace

std::string Timecode::str() const {
    return format_timecode(hours, minutes, seconds, frames, drop);
}

int64_t Timecode::nominal_frame_count() const {
    const int64_t nominal = (static_cast<int64_t>(hours) * 3600 +
                             static_cast<int64_t>(minutes) * 60 + seconds) *
                                nominal_base +
                            frames;
    if (!drop || nominal_base % 30 != 0) {
        return nominal;
    }
    const int64_t drop_frames = nominal_base / 15;
    const int64_t total_minutes = static_cast<int64_t>(hours) * 60 + minutes;
    const int64_t dropped =
        drop_frames * (total_minutes - total_minutes / 10);
    return nominal - dropped;
}

TimecodeCodec::TimecodeCodec(Rational edit_rate, int start_frame, bool drop,
                             int nominal_base)
    : edit_rate_(edit_rate), start_frame_(start_frame), drop_(drop) {
    if (nominal_base <= 0) {
        if (edit_rate_.valid()) {
            nominal_base = static_cast<int>(
                (edit_rate_.num + edit_rate_.den / 2) / edit_rate_.den);
        } else {
            nominal_base = 30;
        }
    }
    nominal_base_ = std::max(1, nominal_base);
    if (drop_ && nominal_base_ % 30 != 0) {
        drop_ = false;
    }
    if (drop_) {
        drop_frames_ = nominal_base_ / 15;
    }
}

Timecode TimecodeCodec::from_frame(int64_t actual_frame) const {
    int64_t label_frame = actual_frame + start_frame_;
    if (drop_ && drop_frames_ > 0) {
        const int64_t frames_per_10_minutes =
            static_cast<int64_t>(nominal_base_) * 600 - drop_frames_ * 9;
        const int64_t frames_per_minute =
            static_cast<int64_t>(nominal_base_) * 60 - drop_frames_;
        const int64_t ten_minute_blocks =
            label_frame / frames_per_10_minutes;
        const int64_t within_block = label_frame % frames_per_10_minutes;
        label_frame += drop_frames_ * 9 * ten_minute_blocks;
        if (within_block > drop_frames_) {
            label_frame +=
                drop_frames_ * ((within_block - drop_frames_) /
                                frames_per_minute);
        }
    }

    Timecode result;
    result.drop = drop_;
    result.nominal_base = nominal_base_;
    result.frames = static_cast<int>(label_frame % nominal_base_);
    const int64_t total_seconds = label_frame / nominal_base_;
    result.seconds = static_cast<int>(total_seconds % 60);
    result.minutes = static_cast<int>((total_seconds / 60) % 60);
    result.hours = static_cast<int>(total_seconds / 3600);
    return result;
}

int64_t TimecodeCodec::to_frame(const Timecode& tc) const {
    const int64_t nominal =
        (static_cast<int64_t>(tc.hours) * 3600 +
         static_cast<int64_t>(tc.minutes) * 60 + tc.seconds) *
            nominal_base_ +
        tc.frames;
    int64_t dropped = 0;
    if (drop_ && drop_frames_ > 0) {
        const int64_t total_minutes =
            static_cast<int64_t>(tc.hours) * 60 + tc.minutes;
        dropped = drop_frames_ * (total_minutes - total_minutes / 10);
    }
    return nominal - dropped - start_frame_;
}

std::string TimecodeCodec::format(int64_t actual_frame) const {
    return from_frame(actual_frame).str();
}

std::string TimecodeCodec::seconds(int64_t actual_frame, int digits) const {
    if (!edit_rate_.valid()) {
        return "n/a";
    }
    return decimal_string(actual_frame * edit_rate_.den, edit_rate_.num,
                          digits);
}

}  // namespace mxf
