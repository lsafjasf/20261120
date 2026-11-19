#pragma once

#include "Core/Alignment.h"
#include "Core/Resampler.h"

#include <string>

namespace abx
{
struct PreparedPair
{
    AudioData reference;
    AudioData comparison;
    AlignmentResult alignment;
    double workingSampleRate = 0.0;
    double inputASampleRate = 0.0;
    double inputBSampleRate = 0.0;
};

[[nodiscard]] bool preparePair(const AudioData& inputA,
                               const AudioData& inputB,
                               double targetSampleRate,
                               PreparedPair& prepared,
                               std::string& error,
                               AlignmentOptions alignmentOptions = {});
} // namespace abx
