#include "Core/Assignment.h"

#include <algorithm>
#include <cctype>

namespace abx
{
namespace
{
std::uint64_t splitMix64(std::uint64_t value)
{
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30U)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27U)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31U);
}

Source hashedSource(std::uint64_t seed, int trialNumber)
{
    const auto mixed = splitMix64(seed
                                  ^ splitMix64(static_cast<std::uint64_t>(trialNumber)
                                               + 0xd1b54a32d192ed03ULL));
    return (mixed & 1ULL) == 0 ? Source::A : Source::B;
}
} // namespace

AssignmentPlan makeSeededPlan(std::uint64_t seed)
{
    return {seed, {}};
}

AssignmentPlan makeReplayPlan(std::vector<Source> order)
{
    return {0, std::move(order)};
}

std::optional<std::vector<Source>> parseReplayOrder(const std::string& text)
{
    std::vector<Source> order;
    order.reserve(text.size());

    for (const auto character : text)
    {
        const auto upper = static_cast<char>(std::toupper(static_cast<unsigned char>(character)));
        if (upper == 'A' || upper == '0')
            order.push_back(Source::A);
        else if (upper == 'B' || upper == '1')
            order.push_back(Source::B);
        else if (std::isspace(static_cast<unsigned char>(character)) || character == ',')
            continue;
        else
            return std::nullopt;
    }

    if (order.empty())
        return std::nullopt;

    return order;
}

std::string encodeReplayOrder(const std::vector<Source>& order)
{
    std::string text;
    text.reserve(order.size());
    for (const auto source : order)
        text.push_back(source == Source::A ? 'A' : 'B');

    return text;
}

XSourceAllocator::XSourceAllocator(AssignmentPlan plan)
    : plan_(std::move(plan))
{
}

Source XSourceAllocator::sourceForTrial(int trialNumber) const
{
    const auto index = static_cast<std::size_t>(std::max(1, trialNumber) - 1);
    if (index < plan_.replayOrder.size())
        return plan_.replayOrder[index];

    return hashedSource(plan_.seed, std::max(1, trialNumber));
}

std::vector<Source> XSourceAllocator::orderForTrials(int numberOfTrials) const
{
    std::vector<Source> order;
    order.reserve(static_cast<std::size_t>(std::max(0, numberOfTrials)));

    for (int trial = 1; trial <= numberOfTrials; ++trial)
        order.push_back(sourceForTrial(trial));

    return order;
}

const AssignmentPlan& XSourceAllocator::plan() const noexcept
{
    return plan_;
}
} // namespace abx
