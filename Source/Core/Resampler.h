#pragma once

#include "Core/Types.h"

namespace abx
{
struct ResampleOptions
{
    int halfWidth = 16;
    double rolloff = 0.94;
};

[[nodiscard]] AudioData resample(const AudioData& input,
                                 double targetSampleRate,
                                 ResampleOptions options = {});

[[nodiscard]] AudioData mixToStereo(const AudioData& input);
} // namespace abx
