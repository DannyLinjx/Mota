// mota-kernel: one JSON-RPC 2.0 message per line, JSON on stdout, logs on
// stderr. The worker never writes project state; it returns a frozen job result.
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

#include "mota/json.hpp"
#include "mota/types.hpp"
#include "mota/worker.hpp"

namespace {

std::string read_stream(std::istream& stream) {
  std::ostringstream buffer;
  buffer << stream.rdbuf();
  return buffer.str();
}

mota::Json error_response(const std::string& message, int code,
                          const std::string& data_code) {
  mota::Json response = mota::Json::object();
  response["jsonrpc"] = mota::Json("2.0");
  response["id"] = mota::Json(nullptr);
  mota::Json error = mota::Json::object();
  error["code"] = mota::Json(static_cast<int64_t>(code));
  error["message"] = mota::Json(message);
  mota::Json data = mota::Json::object();
  data["code"] = mota::Json(data_code);
  error["data"] = data;
  response["error"] = error;
  return response;
}

void respond(const std::string& line) {
  if (line.empty()) return;
  if (line.size() > mota::kMaxFrameBytes) {
    std::cout << error_response("frame exceeds 4 MiB", -32600,
                                "INPUT_TOO_LARGE").dump()
              << std::endl;
    return;
  }
  try {
    const mota::Json request = mota::Json::parse(line);
    std::cout << mota::handle_rpc(request).dump() << std::endl;
  } catch (const mota::KernelError& error) {
    std::cout << error_response(error.what(), -32700, "INVALID_JSON").dump()
              << std::endl;
  } catch (const std::exception& error) {
    std::cout << error_response(error.what(), -32603, "INTERNAL_ERROR").dump()
              << std::endl;
  }
}

}  // namespace

int main(int argc, char** argv) {
  std::string request_path;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--version") {
      std::cout << mota::kernel_info().dump() << std::endl;
      return 0;
    }
    if (arg == "--request" && i + 1 < argc) {
      request_path = argv[++i];
      continue;
    }
    if (arg == "--stdio") {
      continue;
    }
    std::cerr << "mota-kernel: unknown argument " << arg << std::endl;
    return 2;
  }

  if (!request_path.empty()) {
    std::ifstream stream(request_path, std::ios::binary);
    if (!stream) {
      std::cerr << "mota-kernel: cannot open request " << request_path
                << std::endl;
      return 2;
    }
    respond(read_stream(stream));
    return 0;
  }

  std::string line;
  while (std::getline(std::cin, line)) {
    respond(line);
  }
  return 0;
}
