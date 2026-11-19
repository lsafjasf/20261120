#include "Core/Statistics.h"

#include <algorithm>
#include <cmath>
#include <limits>

namespace abx
{
namespace
{
double logProbabilityMass(int trials, int hits)
{
    if (hits < 0 || hits > trials)
        return -std::numeric_limits<double>::infinity();

    return std::lgamma(static_cast<double>(trials) + 1.0)
           - std::lgamma(static_cast<double>(hits) + 1.0)
           - std::lgamma(static_cast<double>(trials - hits) + 1.0)
           - static_cast<double>(trials) * std::log(2.0);
}
} // namespace

BinomialResult exactBinomialTest(int trials, int hits, double alpha)
{
    BinomialResult result;
    result.trials = std::max(0, trials);
    result.hits = std::clamp(hits, 0, result.trials);
    result.alpha = alpha;

    if (result.trials == 0)
        return result;

    result.hitRate = static_cast<double>(result.hits) / static_cast<double>(result.trials);
    const double observedLogMass = logProbabilityMass(result.trials, result.hits);
    const double likelihoodLimit = observedLogMass + 1.0e-10;

    double oneSided = 0.0;
    double twoSided = 0.0;

    for (int candidate = 0; candidate <= result.trials; ++candidate)
    {
        const double logMass = logProbabilityMass(result.trials, candidate);
        const double mass = std::exp(logMass);

        if (candidate >= result.hits)
            oneSided += mass;
        if (logMass <= likelihoodLimit)
            twoSided += mass;
    }

    result.pOneSided = std::clamp(oneSided, 0.0, 1.0);
    result.pTwoSided = std::clamp(twoSided, 0.0, 1.0);
    result.significant = result.pOneSided < alpha;
    return result;
}
} // namespace abx
