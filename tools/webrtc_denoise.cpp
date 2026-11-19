#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <memory>
#include <string>
#include <vector>

#include "modules/audio_processing/include/audio_processing.h"

namespace {

using Clock = std::chrono::steady_clock;

bool ReadFile(const std::string& path, std::vector<float>* data) {
  std::ifstream input(path, std::ios::binary | std::ios::ate);
  if (!input) return false;
  const auto bytes = input.tellg();
  if (bytes < 0 || bytes % static_cast<std::streamoff>(sizeof(float)) != 0) {
    return false;
  }
  data->resize(static_cast<size_t>(bytes) / sizeof(float));
  input.seekg(0);
  input.read(reinterpret_cast<char*>(data->data()), bytes);
  return input.good() || input.eof();
}

bool WriteFile(const std::string& path, const std::vector<float>& data) {
  std::ofstream output(path, std::ios::binary);
  if (!output) return false;
  output.write(reinterpret_cast<const char*>(data.data()),
               static_cast<std::streamsize>(data.size() * sizeof(float)));
  return static_cast<bool>(output);
}

webrtc::AudioProcessing::Config::NoiseSuppression::Level ParseLevel(
    const std::string& value) {
  if (value == "low") {
    return webrtc::AudioProcessing::Config::NoiseSuppression::kLow;
  }
  if (value == "high") {
    return webrtc::AudioProcessing::Config::NoiseSuppression::kHigh;
  }
  if (value == "very-high") {
    return webrtc::AudioProcessing::Config::NoiseSuppression::kVeryHigh;
  }
  return webrtc::AudioProcessing::Config::NoiseSuppression::kModerate;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 5) {
    std::fprintf(stderr,
                 "usage: %s INPUT.f32 OUTPUT.f32 TIMING.csv LEVEL\n",
                 argv[0]);
    return 2;
  }
  const std::string input_path = argv[1];
  const std::string output_path = argv[2];
  const std::string timing_path = argv[3];
  const auto level = ParseLevel(argv[4]);
  constexpr int kSampleRateHz = 48000;
  constexpr size_t kChannels = 1;
  constexpr size_t kFrameSamples = kSampleRateHz / 100;

  std::vector<float> input;
  if (!ReadFile(input_path, &input) || input.size() % kFrameSamples != 0) {
    std::fprintf(stderr, "invalid float32 input or non-integral 10 ms frame\n");
    return 3;
  }

  std::unique_ptr<webrtc::AudioProcessing> apm(
      webrtc::AudioProcessingBuilder().Create());
  if (!apm) {
    std::fprintf(stderr, "failed to create AudioProcessing\n");
    return 4;
  }

  webrtc::AudioProcessing::Config config;
  config.noise_suppression.enabled = true;
  config.noise_suppression.level = level;
  config.echo_canceller.enabled = false;
  config.high_pass_filter.enabled = false;
  config.gain_controller1.enabled = false;
  config.gain_controller2.enabled = false;
  config.transient_suppression.enabled = false;
  apm->ApplyConfig(config);

  const webrtc::StreamConfig stream_config(kSampleRateHz, kChannels);
  webrtc::ProcessingConfig processing_config;
  processing_config.input_stream() = stream_config;
  processing_config.output_stream() = stream_config;
  if (apm->Initialize(processing_config) != 0) {
    std::fprintf(stderr, "failed to initialize AudioProcessing\n");
    return 5;
  }

  std::vector<float> output(input.size(), 0.0f);
  std::ofstream timing(timing_path);
  if (!timing) return 6;
  timing << "frame,samples,ns\n";

  for (size_t offset = 0, frame = 0; offset < input.size();
       offset += kFrameSamples, ++frame) {
    float* channels[kChannels] = {output.data() + offset};
    const float* const input_channels[kChannels] = {input.data() + offset};
    const auto start = Clock::now();
    const int error = apm->ProcessStream(input_channels, stream_config,
                                         stream_config, channels);
    const auto stop = Clock::now();
    if (error != 0) {
      std::fprintf(stderr, "ProcessStream failed: %d\n", error);
      return 7;
    }
    const auto ns =
        std::chrono::duration_cast<std::chrono::nanoseconds>(stop - start)
            .count();
    timing << frame << ',' << kFrameSamples << ',' << ns << '\n';
  }

  if (!WriteFile(output_path, output)) {
    std::fprintf(stderr, "failed to write output\n");
    return 8;
  }
  return 0;
}
