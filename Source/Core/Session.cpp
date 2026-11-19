#include "Core/Session.h"

#include <iomanip>
#include <sstream>

namespace abx
{
namespace
{
std::string jsonString(const std::string& text)
{
    std::ostringstream output;
    output << '"';

    for (const auto character : text)
    {
        switch (character)
        {
            case '"': output << "\\\""; break;
            case '\\': output << "\\\\"; break;
            case '\b': output << "\\b"; break;
            case '\f': output << "\\f"; break;
            case '\n': output << "\\n"; break;
            case '\r': output << "\\r"; break;
            case '\t': output << "\\t"; break;
            default:
                if (static_cast<unsigned char>(character) < 0x20)
                    output << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                           << static_cast<int>(static_cast<unsigned char>(character)) << std::dec;
                else
                    output << character;
                break;
        }
    }

    output << '"';
    return output.str();
}

const char* sourceName(Source source)
{
    return source == Source::A ? "A" : "B";
}

const char* answerName(Answer answer)
{
    return answer == Answer::A ? "A" : "B";
}

void appendSelection(std::ostringstream& output, const Selection& selection)
{
    output << "{\"alignedStart\":" << selection.alignedStart
           << ",\"alignedEnd\":" << selection.alignedEnd
           << ",\"sourceAStart\":" << selection.sourceAStart
           << ",\"sourceAEnd\":" << selection.sourceAEnd
           << ",\"sourceBStart\":" << selection.sourceBStart
           << ",\"sourceBEnd\":" << selection.sourceBEnd
           << ",\"sampleRate\":" << selection.sampleRate
           << ",\"startSeconds\":"
           << (selection.sampleRate > 0.0
                   ? static_cast<double>(selection.alignedStart) / selection.sampleRate
                   : 0.0)
           << ",\"endSeconds\":"
           << (selection.sampleRate > 0.0
                   ? static_cast<double>(selection.alignedEnd) / selection.sampleRate
                   : 0.0)
           << '}';
}
} // namespace

AbxSession::AbxSession(AssignmentPlan plan)
    : plan_(std::move(plan)),
      allocator_(plan_)
{
}

int AbxSession::currentTrialNumber() const noexcept
{
    return currentTrial_;
}

int AbxSession::completedTrials() const noexcept
{
    return static_cast<int>(records_.size());
}

bool AbxSession::hasEnded() const noexcept
{
    return ended_;
}

Source AbxSession::secretSourceForCurrentTrial() const
{
    return allocator_.sourceForTrial(currentTrial_);
}

bool AbxSession::submit(Answer answer, Selection selection, std::string submittedAtUtc)
{
    if (ended_ || !selection.valid())
        return false;

    records_.push_back({currentTrial_,
                        allocator_.sourceForTrial(currentTrial_),
                        answer,
                        selection,
                        std::move(submittedAtUtc)});
    ++currentTrial_;
    return true;
}

std::optional<BinomialResult> AbxSession::endSession()
{
    if (ended_ || records_.empty())
        return std::nullopt;

    int hits = 0;
    for (const auto& record : records_)
        hits += record.isCorrect() ? 1 : 0;

    summary_ = exactBinomialTest(static_cast<int>(records_.size()), hits);
    ended_ = true;
    return summary_;
}

const std::vector<TrialRecord>& AbxSession::finalRecords() const noexcept
{
    return records_;
}

std::optional<BinomialResult> AbxSession::finalSummary() const noexcept
{
    return summary_;
}

std::string AbxSession::interimJson(const SessionMetadata& metadata) const
{
    return makeJson(metadata, false);
}

std::string AbxSession::finalJson(const SessionMetadata& metadata) const
{
    return makeJson(metadata, true);
}

std::string AbxSession::makeJson(const SessionMetadata& metadata, bool final) const
{
    const bool includeSecrets = final && ended_ && summary_.has_value();
    std::ostringstream output;
    output << std::setprecision(15);
    output << "{\n"
           << "  \"version\": 1,\n"
           << "  \"sessionId\": " << jsonString(metadata.sessionId) << ",\n"
           << "  \"createdAtUtc\": " << jsonString(metadata.createdAtUtc) << ",\n"
           << "  \"status\": " << (includeSecrets ? "\"complete\"" : "\"running\"") << ",\n"
           << "  \"testMode\": " << (metadata.testMode ? "true" : "false") << ",\n"
           << "  \"assignmentSeed\": " << plan_.seed << ",\n"
           << "  \"alignment\": {\"workingSampleRate\": " << metadata.workingSampleRate
           << ", \"commonFrames\": " << metadata.commonFrames
           << ", \"lagSamples\": " << metadata.alignmentLagSamples << "},\n";

    if (metadata.testMode && !metadata.replayOrder.empty())
        output << "  \"replayOrder\": " << jsonString(metadata.replayOrder) << ",\n";

    output << "  \"trials\": [\n";

    for (std::size_t index = 0; index < records_.size(); ++index)
    {
        const auto& record = records_[index];
        output << "    {\"trialNumber\":" << record.trialNumber
               << ",\"answer\":" << jsonString(answerName(record.answer))
               << ",\"submittedAtUtc\":" << jsonString(record.submittedAtUtc)
               << ",\"selection\":";
        appendSelection(output, record.selection);

        if (includeSecrets)
        {
            output << ",\"xSource\":" << jsonString(sourceName(record.xSource))
                   << ",\"correct\":" << (record.isCorrect() ? "true" : "false");
        }

        output << '}' << (index + 1 == records_.size() ? "\n" : ",\n");
    }

    output << "  ]";

    if (includeSecrets)
    {
        const auto& summary = *summary_;
        output << ",\n  \"assignmentOrder\": "
               << jsonString(encodeReplayOrder(allocator_.orderForTrials(completedTrials())))
               << ",\n  \"summary\": {\"trials\":" << summary.trials
               << ",\"hits\":" << summary.hits
               << ",\"hitRate\":" << summary.hitRate
               << ",\"pOneSided\":" << summary.pOneSided
               << ",\"pTwoSided\":" << summary.pTwoSided
               << ",\"alpha\":" << summary.alpha
               << ",\"significant\":" << (summary.significant ? "true" : "false")
               << "}";
    }

    output << "\n}\n";
    return output.str();
}
} // namespace abx
