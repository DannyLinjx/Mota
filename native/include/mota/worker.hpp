// Worker entry points: versioned job execution over the integer geometry core.
#pragma once

#include <cstdint>
#include <string>

#include "mota/json.hpp"

namespace mota {

inline constexpr const char* kKernelName = "mota-kernel";
inline constexpr const char* kKernelVersion = "0.1.0-c6";
inline constexpr size_t kMaxFrameBytes = 4u * 1024u * 1024u;
inline constexpr size_t kMaxGeometryObjects = 200'000;

struct WorkerBudget {
  double wall_seconds = 5.0;
  int64_t max_operations = 100'000;
  double max_memory_mb = 0.0;  // 0 = unlimited; index memory is checked after build
  std::string cancel_file;
};

// Executes one frozen geometry job. `expected_sha256` may be empty for direct
// tests; the protocol always supplies it.
Json run_job(const std::string& input_bytes, const std::string& expected_sha256,
             const WorkerBudget& budget);

// Handles one JSON-RPC 2.0 request line and returns one response line.
Json handle_rpc(const Json& request);

Json kernel_info();

}  // namespace mota
