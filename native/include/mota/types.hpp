// Integer geometry primitives for the Mota engine v2 kernel.
//
// Coordinates are int64 nanometres. Intermediate products (cross products,
// squared distances) use __int128 so 1e15-scale coordinates cannot wrap.
// Every arithmetic helper rejects overflow instead of returning a wrapped
// value; the caller surfaces it as a typed job failure.
#pragma once

#include <cstdint>
#include <stdexcept>
#include <string>

namespace mota {

// 1e15 nm = 1000 km. This is far beyond any board and keeps every cross
// product inside __int128 (max |cross| ~ 4e30 << 1.7e38).
inline constexpr int64_t kMaxCoordinateNm = 1'000'000'000'000'000LL;
inline constexpr int64_t kMaxBoardExtentNm = 2'000'000'000'000'000LL;
inline constexpr int64_t kNmPerMm = 1'000'000LL;

enum class ErrorCode {
  None,
  InvalidInput,
  CoordinateOutOfRange,
  Overflow,
  UnsupportedShape,
  ModelInvalid,
  InputTooLarge,
  StaleInput,
  Cancelled,
  BudgetExhausted,
  Internal,
};

const char* error_code_name(ErrorCode code);

class KernelError : public std::runtime_error {
 public:
  KernelError(ErrorCode code, const std::string& message)
      : std::runtime_error(message), code_(code) {}
  ErrorCode code() const { return code_; }

 private:
  ErrorCode code_;
};

struct Vec2 {
  int64_t x = 0;
  int64_t y = 0;
};

struct Aabb {
  int64_t min_x = 0;
  int64_t min_y = 0;
  int64_t max_x = 0;
  int64_t max_y = 0;

  static Aabb from_points(const Vec2& a, const Vec2& b);
  static Aabb around(const Vec2& p, int64_t radius);
  int64_t width() const { return max_x - min_x; }
  int64_t height() const { return max_y - min_y; }
  int64_t center_x() const;
  int64_t center_y() const;
  bool intersects(const Aabb& other) const;
  bool contains(const Vec2& p) const;
  bool contains(const Aabb& other) const;
  Aabb expanded(int64_t margin) const;
  Aabb merged(const Aabb& other) const;
  int64_t area_128_clamped() const;  // saturated to int64 for metrics only
};

int64_t checked_add(int64_t a, int64_t b);
int64_t checked_sub(int64_t a, int64_t b);
int64_t checked_mul(int64_t a, int64_t b);
Vec2 checked_add(const Vec2& a, const Vec2& b);
Vec2 checked_sub(const Vec2& a, const Vec2& b);
Vec2 checked_neg(const Vec2& v);
__int128 dot(const Vec2& a, const Vec2& b);
__int128 cross(const Vec2& a, const Vec2& b);
__int128 length_squared(const Vec2& v);

void validate_point(const Vec2& p);
void validate_extent(int64_t value, const char* what);

// Square root of a non-negative 128-bit integer, rounded to nearest int64.
// Reports whether the input was a perfect square so the caller can state a
// zero error for exact 3-4-5 style results.
struct SqrtResult {
  int64_t value = 0;
  bool exact = false;
};
SqrtResult isqrt_round(__int128 value);

// One shared local->board transform for pads, footprints, 3D, export and DRC.
// p_board = origin + R(rotation) * M(bottom) * p_local
// M(bottom) mirrors x (KiCad bottom-side convention). Rotations that are not
// multiples of 90 degrees round each coordinate by at most 1 nm; the transform
// reports that bound instead of hiding it.
struct Transform {
  Vec2 origin{};
  int32_t rotation_deg = 0;
  bool bottom = false;

  bool is_exact() const;
  int64_t rounding_error_nm() const;
  Vec2 apply(const Vec2& local) const;
  Vec2 apply_direction(const Vec2& local) const;
  int32_t normalized_rotation() const;
};

}  // namespace mota
