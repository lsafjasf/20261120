#pragma once

#include "Core/Assignment.h"
#include "Core/Statistics.h"
#include "Core/Types.h"

#include <optional>
#include <string>
#include <vector>

namespace abx
{
enum class Answer
{
    A = 0,
    B = 1
};

struct SessionMetadata
{
    std::string sessionId;
    std::string createdAtUtc;
    bool testMode = false;
    std::string replayOrder;
    double workingSampleRate = 0.0;
    Frame commonFrames = 0;
    Frame alignmentLagSamples = 0;
};

struct TrialRecord
{
    int trialNumber = 0;
    Source xSource = Source::A;
    Answer answer = Answer::A;
    Selection selection;
    std::string submittedAtUtc;

    [[nodiscard]] bool isCorrect() const noexcept
    {
        return (answer == Answer::A && xSource == Source::A)
               || (answer == Answer::B && xSource == Source::B);
    }
};

class AbxSession
{
public:
    explicit AbxSession(AssignmentPlan plan);

    [[nodiscard]] int currentTrialNumber() const noexcept;
    [[nodiscard]] int completedTrials() const noexcept;
    [[nodiscard]] bool hasEnded() const noexcept;

    // Audio routing only. Never include this value in normal listening UI state.
    [[nodiscard]] Source secretSourceForCurrentTrial() const;

    bool submit(Answer answer, Selection selection, std::string submittedAtUtc);
    [[nodiscard]] std::optional<BinomialResult> endSession();

    [[nodiscard]] const std::vector<TrialRecord>& finalRecords() const noexcept;
    [[nodiscard]] std::optional<BinomialResult> finalSummary() const noexcept;

    [[nodiscard]] std::string interimJson(const SessionMetadata& metadata) const;
    [[nodiscard]] std::string finalJson(const SessionMetadata& metadata) const;

private:
    [[nodiscard]] std::string makeJson(const SessionMetadata& metadata, bool final) const;

    AssignmentPlan plan_;
    XSourceAllocator allocator_;
    std::vector<TrialRecord> records_;
    int currentTrial_ = 1;
    bool ended_ = false;
    std::optional<BinomialResult> summary_;
};
} // namespace abx
