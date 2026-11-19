#pragma once

namespace abx
{
struct BinomialResult
{
    int trials = 0;
    int hits = 0;
    double hitRate = 0.0;
    double pOneSided = 1.0;
    double pTwoSided = 1.0;
    double alpha = 0.05;
    bool significant = false;
};

[[nodiscard]] BinomialResult exactBinomialTest(int trials, int hits, double alpha = 0.05);
} // namespace abx
