#pragma once

#include "mxf/rational.hpp"

#include <cstdint>
#include <string>

namespace mxf {

struct Timecode {
    int hours = 0;
    int minutes = 0;
    int seconds = 0;
    int frames = 0;
    bool drop = false;
    int nominal_base = 30;

    std::string str() const;
    int64_t nominal_frame_count() const;
};

class TimecodeCodec {
public:
    TimecodeCodec(Rational edit_rate, int start_frame = 0,
                  bool drop = false, int nominal_base = 0);

    int nominal_base() const { return nominal_base_; }
    bool drop() const { return drop_; }
    Rational edit_rate() const { return edit_rate_; }

    Timecode from_frame(int64_t actual_frame) const;
    int64_t to_frame(const Timecode& tc) const;

    std::string format(int64_t actual_frame) const;
    std::string seconds(int64_t actual_frame, int digits = 9) const;

private:
    Rational edit_rate_;
    int64_t start_frame_ = 0;
    bool drop_ = false;
    int nominal_base_ = 30;
    int drop_frames_ = 0;
};

}  // namespace mxf
