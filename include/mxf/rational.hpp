#pragma once

#include <cstdint>
#include <numeric>
#include <string>

namespace mxf {

struct Rational {
    int64_t num = 0;
    int64_t den = 1;

    Rational() = default;
    Rational(int64_t numerator, int64_t denominator);

    bool valid() const { return num > 0 && den > 0; }
    long double value() const;
    std::string str() const;
};

bool operator==(const Rational& a, const Rational& b);
bool operator!=(const Rational& a, const Rational& b);

struct ExactValue {
    int64_t whole = 0;
    int64_t remainder = 0;
    int64_t denominator = 1;
};

ExactValue divide_exact(int64_t numerator, int64_t denominator);
int64_t scale_floor(int64_t value, const Rational& from, const Rational& to);
int64_t scale_round(int64_t value, const Rational& from, const Rational& to);
std::string decimal_string(int64_t numerator, int64_t denominator, int digits = 9);

}  // namespace mxf
