#include "mxf/rational.hpp"

#include <cstdlib>
#include <iomanip>
#include <limits>
#include <sstream>

namespace mxf {

Rational::Rational(int64_t numerator, int64_t denominator) : num(numerator), den(denominator) {
    if (den == 0) {
        den = 1;
    }
    if (den < 0) {
        num = -num;
        den = -den;
    }
    const auto divisor = std::gcd(num < 0 ? -num : num, den);
    if (divisor > 1) {
        num /= divisor;
        den /= divisor;
    }
}

long double Rational::value() const {
    return static_cast<long double>(num) / static_cast<long double>(den);
}

std::string Rational::str() const {
    return std::to_string(num) + "/" + std::to_string(den);
}

bool operator==(const Rational& a, const Rational& b) {
    return a.num == b.num && a.den == b.den;
}

bool operator!=(const Rational& a, const Rational& b) {
    return !(a == b);
}

ExactValue divide_exact(int64_t numerator, int64_t denominator) {
    if (denominator <= 0) {
        return {};
    }
    const __int128 n = numerator;
    const __int128 d = denominator;
    __int128 quotient = n / d;
    __int128 remainder = n % d;
    if (remainder < 0) {
        remainder += d;
        --quotient;
    }
    return {static_cast<int64_t>(quotient), static_cast<int64_t>(remainder),
            denominator};
}

int64_t scale_floor(int64_t value, const Rational& from, const Rational& to) {
    if (!from.valid() || !to.valid()) {
        return 0;
    }
    const __int128 numerator =
        static_cast<__int128>(value) * from.den * to.num;
    const __int128 denominator =
        static_cast<__int128>(from.num) * to.den;
    const __int128 result = numerator / denominator;
    return static_cast<int64_t>(result);
}

int64_t scale_round(int64_t value, const Rational& from, const Rational& to) {
    if (!from.valid() || !to.valid()) {
        return 0;
    }
    const __int128 numerator =
        static_cast<__int128>(value) * from.den * to.num;
    const __int128 denominator =
        static_cast<__int128>(from.num) * to.den;
    if (numerator >= 0) {
        return static_cast<int64_t>((numerator + denominator / 2) /
                                    denominator);
    }
    return static_cast<int64_t>((numerator - denominator / 2) /
                                denominator);
}

std::string decimal_string(int64_t numerator, int64_t denominator, int digits) {
    if (denominator == 0) {
        return "n/a";
    }
    if (digits < 0) {
        digits = 0;
    }
    const __int128 scale = [] {
        __int128 result = 1;
        for (int i = 0; i < 9; ++i) {
            result *= 10;
        }
        return result;
    }();
    __int128 scaled = static_cast<__int128>(numerator) * scale;
    if (denominator < 0) {
        scaled = -scaled;
        denominator = -denominator;
    }
    __int128 rounded = scaled / denominator;
    const __int128 remainder = scaled % denominator;
    if (remainder * 2 >= denominator) {
        ++rounded;
    }

    const bool negative = rounded < 0;
    if (negative) {
        rounded = -rounded;
    }
    const __int128 integer_part = rounded / scale;
    __int128 fraction_part = rounded % scale;
    std::ostringstream stream;
    if (negative) {
        stream << '-';
    }
    stream << static_cast<int64_t>(integer_part);
    if (digits > 0) {
        stream << '.' << std::setw(9) << std::setfill('0')
               << static_cast<int64_t>(fraction_part);
        std::string text = stream.str();
        const auto dot = text.find('.');
        if (dot != std::string::npos) {
            text.resize(dot + 1 + static_cast<size_t>(digits));
        }
        return text;
    }
    return stream.str();
}

}  // namespace mxf
