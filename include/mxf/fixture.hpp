#pragma once

#include "mxf/rational.hpp"

#include <string>

namespace mxf {

struct FixtureOptions {
    int frames = 30;
    Rational edit_rate{30000, 1001};
    int sample_rate = 48000;
    bool drop_frame_timecode = true;
};

bool write_sample_mxf(const std::string& path, const FixtureOptions& options,
                      std::string& error);

}  // namespace mxf
