cmake_minimum_required(VERSION 3.16)
project(nse_ci_engine C)

set(CMAKE_C_STANDARD 17)
set(CMAKE_C_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

set(NSE_SOURCES
  src/util.c
  src/json_output.c
  src/change_detector.c
  src/dependency_graph.c
  src/test_selector.c
  src/fingerprint.c
  src/cache_metadata.c
  src/ast_metadata.c
  src/selftest.c
)

add_library(nse_engine_core STATIC ${NSE_SOURCES})
target_include_directories(nse_engine_core PUBLIC include)

# Portable release optimization; never assume AVX2 or any ISA extension.
target_compile_options(nse_engine_core PRIVATE
  $<$<C_COMPILER_ID:GNU,Clang>:-O2 -Wall -Wextra -Wpedantic -Wshadow
   -Wconversion -Werror=implicit-function-declaration>
)

add_executable(nse-ci-engine src/main.c)
target_link_libraries(nse-ci-engine PRIVATE nse_engine_core)

add_executable(engine-tests tests/test_engine.c)
target_link_libraries(engine-tests PRIVATE nse_engine_core)

# Sanitizer build (address+undefined) — used by `build.sh --sanitize` and CI.
option(NSE_SANITIZE "Build with ASan+UBSan" OFF)
if(NSE_SANITIZE)
  add_library(nse_engine_core_san STATIC ${NSE_SOURCES})
  target_include_directories(nse_engine_core_san PUBLIC include)
  target_compile_options(nse_engine_core_san PRIVATE
    -g -O1 -fno-omit-frame-pointer -fsanitize=address,undefined
    -fno-sanitize-recover=all -Wall -Wextra -Wpedantic)
  target_compile_definitions(nse_engine_core_san PUBLIC NSE_SANITIZE_BUILD)
  add_executable(nse-ci-engine-san src/main.c)
  target_link_libraries(nse-ci-engine-san PRIVATE nse_engine_core_san
    -fsanitize=address,undefined)
  add_executable(engine-tests-san tests/test_engine.c)
  target_link_libraries(engine-tests-san PRIVATE nse_engine_core_san
    -fsanitize=address,undefined)
endif()
