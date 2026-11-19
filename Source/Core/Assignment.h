#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace abx
{
enum class Source
{
    A = 0,
    B = 1
};

struct AssignmentPlan
{
    std::uint64_t seed = 0;
    std::vector<Source> replayOrder;
};

[[nodiscard]] AssignmentPlan makeSeededPlan(std::uint64_t seed);
[[nodiscard]] AssignmentPlan makeReplayPlan(std::vector<Source> order);

[[nodiscard]] std::optional<std::vector<Source>> parseReplayOrder(const std::string& text);
[[nodiscard]] std::string encodeReplayOrder(const std::vector<Source>& order);

class XSourceAllocator
{
public:
    explicit XSourceAllocator(AssignmentPlan plan);

    [[nodiscard]] Source sourceForTrial(int trialNumber) const;
    [[nodiscard]] std::vector<Source> orderForTrials(int numberOfTrials) const;
    [[nodiscard]] const AssignmentPlan& plan() const noexcept;

private:
    AssignmentPlan plan_;
};
} // namespace abx
