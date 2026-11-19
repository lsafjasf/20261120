#pragma once

#include "Core/Types.h"

#include <string>

namespace abx
{
struct AlignmentOptions
{
    double maxDelaySeconds = 2.0;
    double minimumOverlapSeconds = 0.5;
    double analysisLimitSeconds = 30.0;
    double correlationRate = 8000.0;
    float audibleThreshold = 1.0e-4f;
};

[[nodiscard]] AlignmentResult estimateAlignment(const AudioData& reference,
                                                const AudioData& comparison,
                                                AlignmentOptions options = {});

[[nodiscard]] bool trimToCommonInterval(const AudioData& reference,
                                        const AudioData& comparison,
                                        const AlignmentResult& alignment,
                                        AudioData& trimmedReference,
                                        AudioData& trimmedComparison,
                                        std::string& error);
} // namespace abx
