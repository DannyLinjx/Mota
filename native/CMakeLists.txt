cmake_minimum_required(VERSION 3.20)
project(mota_kernel VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

add_library(mota-kernel-lib STATIC
  src/geometry/types.cpp
  src/geometry/shapes.cpp
  src/board/board.cpp
  src/zones/zones.cpp
  src/connectivity/connectivity.cpp
  src/verify/verify.cpp
  src/placement/placement.cpp
  src/routing/global/corridor.cpp
  src/routing/detailed/router.cpp
  src/routing/fanout/fanout.cpp
  src/routing/highspeed/channel.cpp
  src/routing/highspeed/differential.cpp
  src/routing/highspeed/delay.cpp
  src/routing/highspeed/highspeed.cpp
  src/routing/highspeed/highspeed_json.cpp
  src/worker/json.cpp
  src/worker/sha256.cpp
  src/worker/worker.cpp
)
target_include_directories(mota-kernel-lib PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_include_directories(mota-kernel-lib PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)
target_compile_options(mota-kernel-lib PRIVATE -Wall -Wextra -Wpedantic)

add_executable(mota-kernel src/main.cpp)
target_link_libraries(mota-kernel PRIVATE mota-kernel-lib)

add_executable(mota-kernel-tests
  tests/geometry_tests.cpp
  tests/worker_tests.cpp
)
target_link_libraries(mota-kernel-tests PRIVATE mota-kernel-lib)
target_include_directories(mota-kernel-tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/tests)

add_executable(mota-connectivity-tests tests/connectivity_tests.cpp)
target_link_libraries(mota-connectivity-tests PRIVATE mota-kernel-lib)
target_include_directories(mota-connectivity-tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/tests)

add_executable(mota-drc-tests tests/drc_tests.cpp)
target_link_libraries(mota-drc-tests PRIVATE mota-kernel-lib)
target_include_directories(mota-drc-tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/tests)

add_executable(mota-placement-tests tests/placement_tests.cpp)
target_link_libraries(mota-placement-tests PRIVATE mota-kernel-lib)
target_include_directories(mota-placement-tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/tests)

add_executable(mota-routing-tests tests/routing_tests.cpp)
target_link_libraries(mota-routing-tests PRIVATE mota-kernel-lib)
target_include_directories(mota-routing-tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/tests)

add_executable(mota-fanout-tests tests/fanout_tests.cpp)
target_link_libraries(mota-fanout-tests PRIVATE mota-kernel-lib)
target_include_directories(mota-fanout-tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/tests)
target_compile_definitions(mota-fanout-tests PRIVATE
  MOTA_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/../platform_packs/bga-fixtures")

add_executable(mota-highspeed-tests tests/highspeed_tests.cpp)
target_link_libraries(mota-highspeed-tests PRIVATE mota-kernel-lib)
target_include_directories(mota-highspeed-tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/tests)
target_compile_definitions(mota-highspeed-tests PRIVATE
  MOTA_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/../platform_packs/highspeed-fixtures")

enable_testing()
add_test(NAME geometry COMMAND mota-kernel-tests)
add_test(NAME connectivity COMMAND mota-connectivity-tests)
add_test(NAME drc COMMAND mota-drc-tests)
add_test(NAME placement COMMAND mota-placement-tests)
add_test(NAME routing COMMAND mota-routing-tests)
add_test(NAME fanout COMMAND mota-fanout-tests)
add_test(NAME highspeed COMMAND mota-highspeed-tests)
set_tests_properties(geometry connectivity drc placement routing fanout highspeed PROPERTIES TIMEOUT 180)
