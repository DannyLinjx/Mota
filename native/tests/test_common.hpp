// Tiny assertion harness: no external test framework, deterministic output.
#pragma once

#include <cstdint>
#include <iostream>
#include <string>

#include "mota/types.hpp"

namespace motatest {

inline int checks = 0;
inline int failures = 0;

inline void check(bool condition, const std::string& what) {
  ++checks;
  if (!condition) {
    ++failures;
    std::cerr << "FAIL: " << what << "\n";
  }
}

inline void check_near(int64_t actual, int64_t expected, int64_t tolerance,
                       const std::string& what) {
  const int64_t delta = actual > expected ? actual - expected : expected - actual;
  ++checks;
  if (delta > tolerance) {
    ++failures;
    std::cerr << "FAIL: " << what << " actual=" << actual
              << " expected=" << expected << " tolerance=" << tolerance
              << "\n";
  }
}

template <typename Function>
void check_throws(Function&& function, mota::ErrorCode code,
                  const std::string& what) {
  ++checks;
  try {
    function();
  } catch (const mota::KernelError& error) {
    if (error.code() == code) return;
    ++failures;
    std::cerr << "FAIL: " << what << " threw " << mota::error_code_name(error.code())
              << " instead of " << mota::error_code_name(code) << "\n";
    return;
  }
  ++failures;
  std::cerr << "FAIL: " << what << " did not throw\n";
}

}  // namespace motatest
