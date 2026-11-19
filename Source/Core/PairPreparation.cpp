#include "Core/PairPreparation.h"

#include <algorithm>
#include <cmath>

namespace abx
{
bool preparePair(const AudioData& inputA,
                 const AudioData& inputB,
                 double targetSampleRate,
                 PreparedPair& prepared,
                 std::string& error,
                 AlignmentOptions alignmentOptions)
{
    if (inputA.empty() || inputB.empty())
    {
        error = "两个输入都必须包含音频。";
        return false;
    }

    if (targetSampleRate <= 0.0)
        targetSampleRate = std::max(inputA.sampleRate, inputB.sampleRate);

    auto stereoA = mixToStereo(inputA);
    auto stereoB = mixToStereo(inputB);

    if (stereoA.empty() || stereoB.empty())
    {
        error = "输入声道转换失败。";
        return false;
    }

    auto resampledA = resample(stereoA, targetSampleRate);
    auto resampledB = resample(stereoB, targetSampleRate);
    if (resampledA.empty() || resampledB.empty())
    {
        error = "采样率统一失败。";
        return false;
    }

    auto alignment = estimateAlignment(resampledA, resampledB, alignmentOptions);
    if (!alignment.ok)
    {
        error = alignment.error;
        return false;
    }

    AudioData trimmedA;
    AudioData trimmedB;
    if (!trimToCommonInterval(resampledA, resampledB, alignment,
                              trimmedA, trimmedB, error))
        return false;

    prepared.reference = std::move(trimmedA);
    prepared.comparison = std::move(trimmedB);
    prepared.alignment = alignment;
    prepared.workingSampleRate = targetSampleRate;
    prepared.inputASampleRate = inputA.sampleRate;
    prepared.inputBSampleRate = inputB.sampleRate;
    error.clear();
    return true;
}
} // namespace abx
