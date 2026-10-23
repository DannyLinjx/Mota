// SHA-256 for frozen worker inputs. Used to reject a changed input artifact
// before any geometry is evaluated.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace mota {

std::string sha256_hex(const std::string& bytes);
std::string sha256_hex(const void* data, size_t size);

}  // namespace mota
