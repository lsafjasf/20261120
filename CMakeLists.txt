cmake_minimum_required(VERSION 3.20)
project(hevc_tile_lab LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_executable(hevc-lab
  src/main.cpp
  src/bit_reader.cpp
  src/hevc_parser.cpp
  src/sample_gen.cpp
  src/metrics.cpp
)

target_compile_options(hevc-lab PRIVATE -Wall -Wextra -Wpedantic)
target_compile_definitions(hevc-lab PRIVATE HEVC_LAB_VERSION="0.1.0")

enable_testing()
add_test(NAME hevc_lab_help COMMAND hevc-lab)
