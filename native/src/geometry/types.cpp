#include "mota/types.hpp"

#include <algorithm>
#include <cmath>
#include <limits>

namespace mota {

const char* error_code_name(ErrorCode code) {
  switch (code) {
    case ErrorCode::None: return "NONE";
    case ErrorCode::InvalidInput: return "INVALID_INPUT";
    case ErrorCode::CoordinateOutOfRange: return "COORDINATE_OUT_OF_RANGE";
    case ErrorCode::Overflow: return "OVERFLOW";
    case ErrorCode::UnsupportedShape: return "UNSUPPORTED_SHAPE";
    case ErrorCode::ModelInvalid: return "MODEL_INVALID";
    case ErrorCode::InputTooLarge: return "INPUT_TOO_LARGE";
    case ErrorCode::StaleInput: return "STALE_JOB_INPUT";
    case ErrorCode::Cancelled: return "CANCELLED";
    case ErrorCode::BudgetExhausted: return "BUDGET_EXHAUSTED";
    case ErrorCode::Internal: return "INTERNAL_ERROR";
  }
  return "INTERNAL_ERROR";
}

int64_t checked_add(int64_t a, int64_t b) {
  int64_t result = 0;
  if (__builtin_add_overflow(a, b, &result)) {
    throw KernelError(ErrorCode::Overflow, "int64 addition overflow");
  }
  return result;
}

int64_t checked_sub(int64_t a, int64_t b) {
  int64_t result = 0;
  if (__builtin_sub_overflow(a, b, &result)) {
    throw KernelError(ErrorCode::Overflow, "int64 subtraction overflow");
  }
  return result;
}

int64_t checked_mul(int64_t a, int64_t b) {
  int64_t result = 0;
  if (__builtin_mul_overflow(a, b, &result)) {
    throw KernelError(ErrorCode::Overflow, "int64 multiplication overflow");
  }
  return result;
}

Vec2 checked_add(const Vec2& a, const Vec2& b) {
  return Vec2{checked_add(a.x, b.x), checked_add(a.y, b.y)};
}

Vec2 checked_sub(const Vec2& a, const Vec2& b) {
  return Vec2{checked_sub(a.x, b.x), checked_sub(a.y, b.y)};
}

Vec2 checked_neg(const Vec2& v) {
  if (v.x == std::numeric_limits<int64_t>::min() ||
      v.y == std::numeric_limits<int64_t>::min()) {
    throw KernelError(ErrorCode::Overflow, "negation overflow");
  }
  return Vec2{-v.x, -v.y};
}

__int128 dot(const Vec2& a, const Vec2& b) {
  return static_cast<__int128>(a.x) * b.x + static_cast<__int128>(a.y) * b.y;
}

__int128 cross(const Vec2& a, const Vec2& b) {
  return static_cast<__int128>(a.x) * b.y - static_cast<__int128>(a.y) * b.x;
}

__int128 length_squared(const Vec2& v) {
  return static_cast<__int128>(v.x) * v.x + static_cast<__int128>(v.y) * v.y;
}

void validate_extent(int64_t value, const char* what) {
  if (value < 0 || value > kMaxBoardExtentNm) {
    throw KernelError(
        ErrorCode::CoordinateOutOfRange,
        std::string(what) + " is outside 0.." + std::to_string(kMaxBoardExtentNm));
  }
}

void validate_point(const Vec2& p) {
  if (p.x < -kMaxCoordinateNm || p.x > kMaxCoordinateNm ||
      p.y < -kMaxCoordinateNm || p.y > kMaxCoordinateNm) {
    throw KernelError(ErrorCode::CoordinateOutOfRange,
                      "coordinate (" + std::to_string(p.x) + "," +
                          std::to_string(p.y) + ") exceeds +-" +
                          std::to_string(kMaxCoordinateNm) + " nm");
  }
}

SqrtResult isqrt_round(__int128 value) {
  if (value < 0) {
    throw KernelError(ErrorCode::InvalidInput, "sqrt of a negative value");
  }
  if (value == 0) return SqrtResult{0, true};
  long double root = std::sqrt(static_cast<long double>(value));
  int64_t r = static_cast<int64_t>(root);
  if (r < 0) r = 0;
  while (static_cast<__int128>(r) * r > value) --r;
  while (static_cast<__int128>(r + 1) * (r + 1) <= value) ++r;
  const __int128 low = value - static_cast<__int128>(r) * r;
  const __int128 high = static_cast<__int128>(r + 1) * (r + 1) - value;
  if (high < low) {
    return SqrtResult{r + 1, false};
  }
  return SqrtResult{r, static_cast<__int128>(r) * r == value};
}

Aabb Aabb::from_points(const Vec2& a, const Vec2& b) {
  return Aabb{std::min(a.x, b.x), std::min(a.y, b.y),
              std::max(a.x, b.x), std::max(a.y, b.y)};
}

Aabb Aabb::around(const Vec2& p, int64_t radius) {
  return Aabb{checked_sub(p.x, radius), checked_sub(p.y, radius),
              checked_add(p.x, radius), checked_add(p.y, radius)};
}

int64_t Aabb::center_x() const { return min_x + (max_x - min_x) / 2; }
int64_t Aabb::center_y() const { return min_y + (max_y - min_y) / 2; }

bool Aabb::intersects(const Aabb& other) const {
  return min_x <= other.max_x && other.min_x <= max_x &&
         min_y <= other.max_y && other.min_y <= max_y;
}

bool Aabb::contains(const Vec2& p) const {
  return p.x >= min_x && p.x <= max_x && p.y >= min_y && p.y <= max_y;
}

bool Aabb::contains(const Aabb& other) const {
  return other.min_x >= min_x && other.max_x <= max_x &&
         other.min_y >= min_y && other.max_y <= max_y;
}

Aabb Aabb::expanded(int64_t margin) const {
  if (margin < 0) {
    throw KernelError(ErrorCode::InvalidInput, "negative AABB margin");
  }
  return Aabb{checked_sub(min_x, margin), checked_sub(min_y, margin),
              checked_add(max_x, margin), checked_add(max_y, margin)};
}

Aabb Aabb::merged(const Aabb& other) const {
  return Aabb{std::min(min_x, other.min_x), std::min(min_y, other.min_y),
              std::max(max_x, other.max_x), std::max(max_y, other.max_y)};
}

int64_t Aabb::area_128_clamped() const {
  const __int128 w = static_cast<__int128>(max_x) - min_x;
  const __int128 h = static_cast<__int128>(max_y) - min_y;
  const __int128 area = w * h;
  const __int128 limit = std::numeric_limits<int64_t>::max();
  return area > limit ? std::numeric_limits<int64_t>::max()
                      : static_cast<int64_t>(area);
}

int32_t Transform::normalized_rotation() const {
  int32_t value = rotation_deg % 360;
  if (value < 0) value += 360;
  return value;
}

bool Transform::is_exact() const { return normalized_rotation() % 90 == 0; }

int64_t Transform::rounding_error_nm() const { return is_exact() ? 0 : 1; }

Vec2 Transform::apply_direction(const Vec2& local) const {
  validate_point(local);
  Vec2 p = bottom ? Vec2{-local.x, local.y} : local;
  const int32_t rotation = normalized_rotation();
  switch (rotation) {
    case 0:
      return p;
    case 90:
      return Vec2{-p.y, p.x};
    case 180:
      return Vec2{-p.x, -p.y};
    case 270:
      return Vec2{p.y, -p.x};
    default:
      break;
  }
  constexpr double kPi = 3.141592653589793238462643383279502884;
  const double radians = static_cast<double>(rotation) * kPi / 180.0;
  const double c = std::cos(radians);
  const double s = std::sin(radians);
  const int64_t x = std::llround(static_cast<double>(p.x) * c -
                                 static_cast<double>(p.y) * s);
  const int64_t y = std::llround(static_cast<double>(p.x) * s +
                                 static_cast<double>(p.y) * c);
  return Vec2{x, y};
}

Vec2 Transform::apply(const Vec2& local) const {
  const Vec2 rotated = apply_direction(local);
  const Vec2 result{checked_add(origin.x, rotated.x),
                    checked_add(origin.y, rotated.y)};
  validate_point(result);
  return result;
}

}  // namespace mota
