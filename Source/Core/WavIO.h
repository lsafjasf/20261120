#pragma once

#include "Core/Types.h"

#include <string>

namespace abx
{
[[nodiscard]] bool readPcm16Wav(const std::string& path, AudioData& audio, std::string& error);
[[nodiscard]] bool writePcm16Wav(const std::string& path, const AudioData& audio, std::string& error);
} // namespace abx
