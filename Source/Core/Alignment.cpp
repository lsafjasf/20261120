#include "Core/Alignment.h"

#include <algorithm>
#include <cmath>
#include <complex>
#include <limits>
#include <numeric>
#include <vector>

namespace abx
{
namespace
{
constexpr double pi = 3.14159265358979323846264338327950288;
using Complex = std::complex<double>;

int nextPowerOfTwo(int value)
{
    int result = 1;
    while (result < value)
        result <<= 1;

    return result;
}

void fft(std::vector<Complex>& values, bool inverse)
{
    const auto size = values.size();
    if (size < 2)
        return;

    for (std::size_t i = 1, j = 0; i < size; ++i)
    {
        auto bit = size >> 1;
        for (; (j & bit) != 0; bit >>= 1)
            j ^= bit;
        j ^= bit;

        if (i < j)
            std::swap(values[i], values[j]);
    }

    for (std::size_t length = 2; length <= size; length <<= 1)
    {
        const double angle = 2.0 * pi / static_cast<double>(length) * (inverse ? 1.0 : -1.0);
        const Complex step(std::cos(angle), std::sin(angle));

        for (std::size_t offset = 0; offset < size; offset += length)
        {
            Complex factor(1.0, 0.0);
            const auto half = length >> 1;

            for (std::size_t index = 0; index < half; ++index)
            {
                const auto even = values[offset + index];
                const auto odd = values[offset + index + half] * factor;
                values[offset + index] = even + odd;
                values[offset + index + half] = even - odd;
                factor *= step;
            }
        }
    }

    if (inverse)
    {
        const auto scale = 1.0 / static_cast<double>(size);
        for (auto& value : values)
            value *= scale;
    }
}

std::vector<double> downmixToMono(const AudioData& audio)
{
    std::vector<double> mono(static_cast<std::size_t>(audio.numFrames()), 0.0);
    if (audio.numChannels() == 0)
        return mono;

    const double gain = 1.0 / static_cast<double>(audio.numChannels());
    for (int channel = 0; channel < audio.numChannels(); ++channel)
    {
        const auto& samples = audio.channels[static_cast<std::size_t>(channel)];
        for (std::size_t frame = 0; frame < samples.size(); ++frame)
            mono[frame] += static_cast<double>(samples[frame]) * gain;
    }

    return mono;
}

Frame firstAudibleFrame(const std::vector<double>& samples, float threshold)
{
    const auto limit = static_cast<double>(threshold);
    for (Frame frame = 0; frame < static_cast<Frame>(samples.size()); ++frame)
    {
        if (std::abs(samples[static_cast<std::size_t>(frame)]) >= limit)
            return frame;
    }

    return 0;
}

std::vector<double> takeDecimated(const std::vector<double>& input,
                                  Frame start,
                                  Frame length,
                                  int factor)
{
    const auto decimatedLength = static_cast<std::size_t>(std::max<Frame>(0, length / factor));
    std::vector<double> output(decimatedLength, 0.0);

    for (std::size_t out = 0; out < decimatedLength; ++out)
    {
        double sum = 0.0;
        const auto first = start + static_cast<Frame>(out) * factor;

        for (int tap = 0; tap < factor; ++tap)
        {
            const auto index = first + tap;
            if (index >= 0 && index < static_cast<Frame>(input.size()))
                sum += input[static_cast<std::size_t>(index)];
        }

        output[out] = sum / static_cast<double>(factor);
    }

    return output;
}

void removeMean(std::vector<double>& values)
{
    if (values.empty())
        return;

    const double mean = std::accumulate(values.begin(), values.end(), 0.0)
                        / static_cast<double>(values.size());
    for (auto& value : values)
        value -= mean;
}

struct CorrelationCandidate
{
    Frame lag = 0;
    double score = -1.0;
};

CorrelationCandidate findCoarseLag(const std::vector<double>& reference,
                                   const std::vector<double>& comparison,
                                   Frame maximumLag,
                                   Frame minimumOverlap)
{
    CorrelationCandidate best;
    if (reference.empty() || comparison.empty())
        return best;

    const auto required = static_cast<int>(
        reference.size() + comparison.size() + 1);
    const int fftSize = nextPowerOfTwo(required);

    std::vector<Complex> referenceSpectrum(static_cast<std::size_t>(fftSize));
    std::vector<Complex> comparisonSpectrum(static_cast<std::size_t>(fftSize));

    for (std::size_t index = 0; index < reference.size(); ++index)
        referenceSpectrum[index] = reference[index];
    for (std::size_t index = 0; index < comparison.size(); ++index)
        comparisonSpectrum[index] = comparison[index];

    fft(referenceSpectrum, false);
    fft(comparisonSpectrum, false);

    for (int index = 0; index < fftSize; ++index)
        referenceSpectrum[static_cast<std::size_t>(index)] *=
            std::conj(comparisonSpectrum[static_cast<std::size_t>(index)]);

    fft(referenceSpectrum, true);

    std::vector<double> prefixA(reference.size() + 1, 0.0);
    std::vector<double> prefixB(comparison.size() + 1, 0.0);

    for (std::size_t index = 0; index < reference.size(); ++index)
        prefixA[index + 1] = prefixA[index] + reference[index] * reference[index];
    for (std::size_t index = 0; index < comparison.size(); ++index)
        prefixB[index + 1] = prefixB[index] + comparison[index] * comparison[index];

    const auto referenceLength = static_cast<Frame>(reference.size());
    const auto comparisonLength = static_cast<Frame>(comparison.size());

    for (Frame lag = -maximumLag; lag <= maximumLag; ++lag)
    {
        const Frame startA = std::max<Frame>(0, -lag);
        const Frame endA = std::min(referenceLength, comparisonLength - lag);
        if (endA - startA < minimumOverlap)
            continue;

        const auto startB = startA + lag;
        const auto endB = endA + lag;
        const double energyA = prefixA[static_cast<std::size_t>(endA)]
                               - prefixA[static_cast<std::size_t>(startA)];
        const double energyB = prefixB[static_cast<std::size_t>(endB)]
                               - prefixB[static_cast<std::size_t>(startB)];

        if (energyA <= 1.0e-20 || energyB <= 1.0e-20)
            continue;

        const auto correlationIndex = static_cast<std::size_t>(
            (static_cast<Frame>(fftSize) - lag) % static_cast<Frame>(fftSize));
        const double score = std::abs(referenceSpectrum[correlationIndex].real())
                             / std::sqrt(energyA * energyB);

        if (score > best.score)
            best = {lag, score};
    }

    return best;
}

CorrelationCandidate refineLag(const std::vector<double>& reference,
                               const std::vector<double>& comparison,
                               Frame coarseLag,
                               int searchRadius,
                               Frame analysisStart,
                               Frame analysisFrames)
{
    CorrelationCandidate best;
    const auto referenceLength = static_cast<Frame>(reference.size());
    const auto comparisonLength = static_cast<Frame>(comparison.size());

    for (Frame lag = coarseLag - searchRadius; lag <= coarseLag + searchRadius; ++lag)
    {
        const Frame startA = std::max<Frame>({0, -lag, analysisStart});
        const Frame endA = std::min<Frame>(
            {referenceLength, comparisonLength - lag, analysisStart + analysisFrames});
        if (endA - startA < 1)
            continue;

        double correlation = 0.0;
        double energyA = 0.0;
        double energyB = 0.0;

        for (Frame frame = startA; frame < endA; ++frame)
        {
            const double a = reference[static_cast<std::size_t>(frame)];
            const double b = comparison[static_cast<std::size_t>(frame + lag)];
            correlation += a * b;
            energyA += a * a;
            energyB += b * b;
        }

        if (energyA <= 1.0e-20 || energyB <= 1.0e-20)
            continue;

        const double score = std::abs(correlation) / std::sqrt(energyA * energyB);
        if (score > best.score)
            best = {lag, score};
    }

    return best;
}
} // namespace

AlignmentResult estimateAlignment(const AudioData& reference,
                                  const AudioData& comparison,
                                  AlignmentOptions options)
{
    AlignmentResult result;
    result.sampleRate = reference.sampleRate;

    if (reference.empty() || comparison.empty())
    {
        result.error = "至少一个输入为空。";
        return result;
    }

    if (std::abs(reference.sampleRate - comparison.sampleRate) > 1.0e-6)
    {
        result.error = "延时估计要求输入已经统一采样率。";
        return result;
    }

    const auto referenceLength = reference.numFrames();
    const auto comparisonLength = comparison.numFrames();
    const auto sampleRate = reference.sampleRate;
    const auto maximumLag = static_cast<Frame>(
        std::min({static_cast<double>(referenceLength),
                  static_cast<double>(comparisonLength),
                  options.maxDelaySeconds * sampleRate}));
    const auto minimumOverlap = static_cast<Frame>(
        std::max(1.0, options.minimumOverlapSeconds * sampleRate));

    if (maximumLag < 1 || referenceLength < minimumOverlap || comparisonLength < minimumOverlap)
    {
        result.error = "输入太短，无法估计延时。";
        return result;
    }

    auto referenceMono = downmixToMono(reference);
    auto comparisonMono = downmixToMono(comparison);
    const auto onsetA = firstAudibleFrame(referenceMono, options.audibleThreshold);
    const auto onsetB = firstAudibleFrame(comparisonMono, options.audibleThreshold);
    const auto margin = static_cast<Frame>(0.25 * sampleRate);
    const auto analysisStart = std::max<Frame>(0, std::min(onsetA, onsetB) - margin);
    const auto availableFrames = std::max(referenceLength, comparisonLength) - analysisStart;
    const auto analysisFrames = std::max<Frame>(
        1, std::min(availableFrames,
                    static_cast<Frame>(options.analysisLimitSeconds * sampleRate)));

    const auto decimation = std::max(
        1, static_cast<int>(std::floor(sampleRate / std::max(100.0, options.correlationRate))));
    auto decimatedA = takeDecimated(referenceMono, analysisStart, analysisFrames, decimation);
    auto decimatedB = takeDecimated(comparisonMono, analysisStart, analysisFrames, decimation);
    removeMean(decimatedA);
    removeMean(decimatedB);

    const auto coarseMaximumLag = static_cast<Frame>(
        std::ceil(static_cast<double>(maximumLag) / static_cast<double>(decimation))) + 1;
    const auto coarseMinimumOverlap = std::max<Frame>(
        1, minimumOverlap / decimation);
    auto coarse = findCoarseLag(decimatedA, decimatedB,
                                coarseMaximumLag, coarseMinimumOverlap);

    if (coarse.score < 0.0)
    {
        result.error = "无法找到具有足够重叠能量的公共区间。";
        return result;
    }

    auto best = CorrelationCandidate{
        coarse.lag * decimation,
        coarse.score};

    if (decimation > 1)
    {
        const auto refined = refineLag(referenceMono, comparisonMono, best.lag,
                                       decimation + 2, analysisStart, analysisFrames);
        if (refined.score >= 0.0)
            best = refined;
    }

    best.lag = std::clamp(best.lag, -maximumLag, maximumLag);

    const Frame commonStartA = std::max<Frame>(0, -best.lag);
    const Frame commonEndA = std::min(referenceLength, comparisonLength - best.lag);
    if (commonEndA <= commonStartA)
    {
        result.error = "延时超过可重叠范围。";
        return result;
    }

    result.ok = true;
    result.lagSamples = best.lag;
    result.commonStartA = commonStartA;
    result.commonEndA = commonEndA;
    result.commonStartB = commonStartA + best.lag;
    result.commonEndB = commonEndA + best.lag;
    result.confidence = std::clamp(best.score, 0.0, 1.0);
    return result;
}

bool trimToCommonInterval(const AudioData& reference,
                          const AudioData& comparison,
                          const AlignmentResult& alignment,
                          AudioData& trimmedReference,
                          AudioData& trimmedComparison,
                          std::string& error)
{
    if (!alignment.ok || alignment.commonFrames() <= 0)
    {
        error = "对齐结果无效。";
        return false;
    }

    if (alignment.commonEndA > reference.numFrames()
        || alignment.commonEndB > comparison.numFrames())
    {
        error = "公共区间超出输入长度。";
        return false;
    }

    const auto length = alignment.commonFrames();

    const auto trimOne = [length](const AudioData& source,
                                  Frame start,
                                  AudioData& destination)
    {
        destination.sampleRate = source.sampleRate;
        destination.channels.resize(source.channels.size());

        for (std::size_t channel = 0; channel < source.channels.size(); ++channel)
        {
            const auto begin = source.channels[channel].begin() + start;
            destination.channels[channel] = std::vector<float>(
                begin, begin + length);
        }
    };

    trimOne(reference, alignment.commonStartA, trimmedReference);
    trimOne(comparison, alignment.commonStartB, trimmedComparison);
    error.clear();
    return true;
}
} // namespace abx
