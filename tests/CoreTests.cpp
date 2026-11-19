#include "Core/Alignment.h"
#include "Core/Assignment.h"
#include "Core/PairPreparation.h"
#include "Core/Resampler.h"
#include "Core/Session.h"
#include "Core/Statistics.h"
#include "Core/WavIO.h"

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <iostream>
#include <string>
#include <vector>

namespace
{
int failures = 0;

#define CHECK(condition)                                                                 \
    do                                                                                   \
    {                                                                                    \
        if (!(condition))                                                                \
        {                                                                                \
            ++failures;                                                                  \
            std::cerr << "FAIL " << __FILE__ << ':' << __LINE__ << "  " #condition "\n"; \
        }                                                                                \
    } while (false)

std::filesystem::path fixture(const char* name)
{
    return std::filesystem::path(ABX_FIXTURE_DIR) / name;
}

abx::AudioData load(const char* name)
{
    abx::AudioData audio;
    std::string error;
    CHECK(abx::readPcm16Wav(fixture(name).string(), audio, error));
    if (!error.empty())
        std::cerr << error << '\n';
    return audio;
}

float maximumDifference(const abx::AudioData& a, const abx::AudioData& b)
{
    float difference = 0.0f;
    const auto channels = std::min(a.numChannels(), b.numChannels());
    const auto frames = std::min(a.numFrames(), b.numFrames());

    for (int channel = 0; channel < channels; ++channel)
    {
        for (abx::Frame frame = 0; frame < frames; ++frame)
        {
            difference = std::max(
                difference,
                std::abs(a.channels[static_cast<std::size_t>(channel)]
                              [static_cast<std::size_t>(frame)]
                         - b.channels[static_cast<std::size_t>(channel)]
                                [static_cast<std::size_t>(frame)]));
        }
    }

    return difference;
}

void testKnownDelay()
{
    const auto master = load("case_delay_master.wav");
    const auto encoded = load("case_delay_encoded.wav");
    abx::PreparedPair pair;
    std::string error;
    CHECK(abx::preparePair(master, encoded, 48000.0, pair, error));
    CHECK(error.empty());
    CHECK(pair.alignment.ok);
    CHECK(pair.alignment.lagSamples == 733);
    CHECK(pair.alignment.commonStartA == 0);
    CHECK(pair.alignment.commonStartB == 733);
    CHECK(pair.alignment.commonFrames() == master.numFrames());
    CHECK(pair.reference.numFrames() == pair.comparison.numFrames());
    CHECK(maximumDifference(pair.reference, pair.comparison) < 1.0e-6f);
}

void testIdenticalContent()
{
    const auto master = load("case_same_master.wav");
    const auto encoded = load("case_same_encoded.wav");
    abx::PreparedPair pair;
    std::string error;
    CHECK(abx::preparePair(master, encoded, 48000.0, pair, error));
    CHECK(pair.alignment.ok);
    CHECK(pair.alignment.lagSamples == 0);
    CHECK(pair.alignment.commonFrames() == master.numFrames());
    CHECK(pair.alignment.confidence > 0.999);
    CHECK(maximumDifference(pair.reference, pair.comparison) < 1.0e-6f);
}

void testLocalDistortion()
{
    const auto master = load("case_distortion_master.wav");
    const auto encoded = load("case_distortion_encoded.wav");
    abx::PreparedPair pair;
    std::string error;
    CHECK(abx::preparePair(master, encoded, 48000.0, pair, error));
    CHECK(pair.alignment.ok);
    CHECK(pair.alignment.lagSamples == 0);
    CHECK(pair.alignment.commonFrames() == master.numFrames());
    CHECK(pair.alignment.confidence > 0.90);

    const abx::Frame distortionStart = static_cast<abx::Frame>(1.20 * pair.workingSampleRate);
    const abx::Frame distortionEnd = static_cast<abx::Frame>(1.40 * pair.workingSampleRate);
    float insideDifference = 0.0f;
    float outsideDifference = 0.0f;

    for (abx::Frame frame = 0; frame < pair.reference.numFrames(); ++frame)
    {
        const float difference = std::abs(
            pair.reference.channels[0][static_cast<std::size_t>(frame)]
            - pair.comparison.channels[0][static_cast<std::size_t>(frame)]);

        if (frame >= distortionStart && frame < distortionEnd)
            insideDifference = std::max(insideDifference, difference);
        else
            outsideDifference = std::max(outsideDifference, difference);
    }

    CHECK(insideDifference > 0.05f);
    CHECK(outsideDifference < 1.0e-5f);
}

void testSampleRateNormalisation()
{
    constexpr double sourceRate = 44100.0;
    constexpr double targetRate = 48000.0;
    abx::AudioData source;
    source.sampleRate = sourceRate;
    source.channels.assign(1, std::vector<float>(
        static_cast<std::size_t>(sourceRate * 1.0), 0.0f));

    for (std::size_t frame = 0; frame < source.channels[0].size(); ++frame)
    {
        const double time = static_cast<double>(frame) / sourceRate;
        source.channels[0][frame] = static_cast<float>(0.5 * std::sin(
            2.0 * 3.14159265358979323846 * 997.0 * time));
    }

    const auto converted = abx::resample(source, targetRate);
    CHECK(std::abs(converted.sampleRate - targetRate) < 1.0e-9);
    CHECK(std::abs(converted.numFrames() - static_cast<abx::Frame>(targetRate)) <= 1);

    double errorEnergy = 0.0;
    double signalEnergy = 0.0;
    for (abx::Frame frame = 1000; frame < converted.numFrames() - 1000; ++frame)
    {
        const double time = static_cast<double>(frame) / targetRate;
        const double expected = 0.5 * std::sin(2.0 * 3.14159265358979323846 * 997.0 * time);
        const double actual = converted.channels[0][static_cast<std::size_t>(frame)];
        errorEnergy += (actual - expected) * (actual - expected);
        signalEnergy += expected * expected;
    }

    CHECK(std::sqrt(errorEnergy / std::max(1.0, signalEnergy)) < 0.01);

    const auto master = load("case_same_master.wav");
    const auto at44100 = abx::resample(master, 44100.0);
    abx::PreparedPair pair;
    std::string error;
    CHECK(abx::preparePair(master, at44100, 48000.0, pair, error));
    CHECK(pair.alignment.ok);
    CHECK(std::abs(pair.alignment.lagSamples) <= 2);
    CHECK(pair.alignment.confidence > 0.98);
    CHECK(std::abs(pair.alignment.commonDurationSeconds() - 3.0) < 0.002);
}

void testAssignmentReplay()
{
    const auto seed = 0x123456789abcdef0ULL;
    const abx::XSourceAllocator first(abx::makeSeededPlan(seed));
    const abx::XSourceAllocator second(abx::makeSeededPlan(seed));
    CHECK(first.orderForTrials(64) == second.orderForTrials(64));

    const auto encoded = abx::encodeReplayOrder(first.orderForTrials(12));
    const auto parsed = abx::parseReplayOrder(encoded);
    CHECK(parsed.has_value());
    CHECK(parsed.has_value() && *parsed == first.orderForTrials(12));

    const auto replay = abx::parseReplayOrder("A,B,B,0,1,A");
    CHECK(replay.has_value());
    if (replay.has_value())
    {
        const abx::XSourceAllocator allocator(abx::makeReplayPlan(*replay));
        CHECK(allocator.orderForTrials(6) == *replay);
    }

    CHECK(abx::parseReplayOrder("ABX").has_value() == false);
}

void testStatistics()
{
    const auto perfect = abx::exactBinomialTest(10, 10);
    CHECK(std::abs(perfect.pOneSided - 0.0009765625) < 1.0e-12);
    CHECK(std::abs(perfect.pTwoSided - 0.001953125) < 1.0e-12);
    CHECK(perfect.significant);

    const auto chance = abx::exactBinomialTest(10, 5);
    CHECK(std::abs(chance.pOneSided - 0.623046875) < 1.0e-12);
    CHECK(chance.pTwoSided > 0.999);
    CHECK(!chance.significant);

    const auto eightOfEight = abx::exactBinomialTest(8, 8);
    CHECK(std::abs(eightOfEight.pOneSided - 0.00390625) < 1.0e-12);
    CHECK(eightOfEight.significant);
}

abx::Selection makeSelection(const abx::PreparedPair& pair,
                             abx::Frame alignedStart,
                             abx::Frame alignedEnd)
{
    abx::Selection selection;
    selection.alignedStart = alignedStart;
    selection.alignedEnd = alignedEnd;
    selection.sourceAStart = pair.alignment.commonStartA
                             + static_cast<abx::Frame>(
                                 static_cast<double>(alignedStart)
                                 / pair.workingSampleRate * pair.inputASampleRate);
    selection.sourceAEnd = pair.alignment.commonStartA
                           + static_cast<abx::Frame>(
                               static_cast<double>(alignedEnd)
                               / pair.workingSampleRate * pair.inputASampleRate);
    selection.sourceBStart = pair.alignment.commonStartB
                             + static_cast<abx::Frame>(
                                 static_cast<double>(alignedStart)
                                 / pair.workingSampleRate * pair.inputBSampleRate);
    selection.sourceBEnd = pair.alignment.commonStartB
                           + static_cast<abx::Frame>(
                               static_cast<double>(alignedEnd)
                               / pair.workingSampleRate * pair.inputBSampleRate);
    selection.sampleRate = pair.workingSampleRate;
    return selection;
}

void testSessionAndLeakBoundary()
{
    const auto order = abx::parseReplayOrder("AAB");
    CHECK(order.has_value());
    if (!order.has_value())
        return;

    abx::AbxSession session(abx::makeReplayPlan(*order));
    abx::SessionMetadata metadata;
    metadata.sessionId = "test-session";
    metadata.createdAtUtc = "2026-11-20T00:00:00Z";
    metadata.testMode = true;
    metadata.replayOrder = "AAB";
    metadata.workingSampleRate = 48000.0;
    metadata.commonFrames = 144000;
    metadata.alignmentLagSamples = 0;

    abx::Selection selection;
    selection.alignedStart = 0;
    selection.alignedEnd = 48000;
    selection.sourceAStart = 0;
    selection.sourceAEnd = 48000;
    selection.sourceBStart = 0;
    selection.sourceBEnd = 48000;
    selection.sampleRate = 48000.0;

    CHECK(session.secretSourceForCurrentTrial() == abx::Source::A);
    CHECK(session.submit(abx::Answer::A, selection, "2026-11-20T00:00:01Z"));
    CHECK(session.secretSourceForCurrentTrial() == abx::Source::A);
    CHECK(session.submit(abx::Answer::A, selection, "2026-11-20T00:00:02Z"));
    CHECK(session.secretSourceForCurrentTrial() == abx::Source::B);
    CHECK(session.submit(abx::Answer::B, selection, "2026-11-20T00:00:03Z"));

    const auto interim = session.interimJson(metadata);
    CHECK(interim.find("\"trialNumber\":1") != std::string::npos);
    CHECK(interim.find("\"answer\":\"A\"") != std::string::npos);
    CHECK(interim.find("\"selection\"") != std::string::npos);
    CHECK(interim.find("xSource") == std::string::npos);
    CHECK(interim.find("\"correct\"") == std::string::npos);
    CHECK(interim.find("\"summary\"") == std::string::npos);
    CHECK(!session.finalSummary().has_value());

    const auto summary = session.endSession();
    CHECK(summary.has_value());
    CHECK(summary.has_value() && summary->trials == 3 && summary->hits == 3);
    CHECK(!session.submit(abx::Answer::A, selection, "later"));

    const auto finalJson = session.finalJson(metadata);
    CHECK(finalJson.find("\"xSource\"") != std::string::npos);
    CHECK(finalJson.find("\"summary\"") != std::string::npos);
    CHECK(finalJson.find("\"hits\":3") != std::string::npos);
    CHECK(finalJson.find("\"assignmentOrder\": \"AAB\"") != std::string::npos);
}
} // namespace

int main()
{
    if (!std::filesystem::exists(fixture("case_delay_master.wav")))
    {
        std::cerr << "Fixtures missing. Run: python3 tests/generate_fixtures.py\n";
        return 2;
    }

    testKnownDelay();
    testIdenticalContent();
    testLocalDistortion();
    testSampleRateNormalisation();
    testAssignmentReplay();
    testStatistics();
    testSessionAndLeakBoundary();

    if (failures != 0)
    {
        std::cerr << failures << " check(s) failed.\n";
        return 1;
    }

    std::cout << "All ABX core checks passed.\n";
    return 0;
}
