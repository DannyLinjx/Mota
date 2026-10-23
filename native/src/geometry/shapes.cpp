#include "mota/shapes.hpp"

#include <algorithm>
#include <cmath>
#include <limits>

namespace mota {
namespace {

constexpr double kPi = 3.141592653589793238462643383279502884;
constexpr double kTwoPi = 2.0 * kPi;
constexpr double kUradToRad = 1e-6;
constexpr double kRadToUrad = 1e6;
constexpr int64_t kDefaultArcErrorNm = 100;

int64_t abs64(int64_t value) { return value < 0 ? -value : value; }

void require_radius(int64_t value, const char* what) {
  if (value < 0) {
    throw KernelError(ErrorCode::InvalidInput,
                      std::string(what) + " must be non-negative");
  }
  if (value > kMaxCoordinateNm) {
    throw KernelError(ErrorCode::CoordinateOutOfRange,
                      std::string(what) + " exceeds the coordinate range");
  }
}

DistanceResult result_of(int64_t distance, int64_t error, bool exact) {
  DistanceResult out;
  out.distance_nm = std::max<int64_t>(0, distance);
  out.error_nm = std::max<int64_t>(0, error);
  out.exact = exact;
  out.intersects = out.distance_nm == 0;
  return out;
}

DistanceResult subtract_radius(const DistanceResult& base, int64_t radius) {
  const int64_t distance = base.distance_nm - radius;
  return result_of(distance, base.error_nm, base.exact);
}

DistanceResult subtract_width(const DistanceResult& base, int64_t width) {
  if (width <= 0) return base;
  return subtract_radius(base, width / 2);
}

Vec2 arc_point(const Vec2& center, int64_t radius, double angle) {
  const int64_t x = std::llround(static_cast<double>(radius) * std::cos(angle));
  const int64_t y = std::llround(static_cast<double>(radius) * std::sin(angle));
  const Vec2 point{checked_add(center.x, x), checked_add(center.y, y)};
  validate_point(point);
  return point;
}

bool angle_in_arc(double angle, double start, double sweep) {
  if (sweep == 0.0) return false;
  if (sweep < 0.0) {
    angle = -angle;
    start = -start;
    sweep = -sweep;
  }
  double rel = std::fmod(angle - start, kTwoPi);
  if (rel < 0.0) rel += kTwoPi;
  // Angles arrive quantised to micro-radians; allow a 1e-6 rad slack so a
  // nominal endpoint is not treated as outside its own span.
  return rel <= sweep + 1e-6;
}

int64_t point_arc_curve_distance(const Shape& arc, const Vec2& p, bool& exact) {
  exact = false;
  const double dx = static_cast<double>(p.x - arc.a.x);
  const double dy = static_cast<double>(p.y - arc.a.y);
  const double radial = std::hypot(dx, dy);
  const double start = arc.start_urad * kUradToRad;
  const double sweep = arc.sweep_urad * kUradToRad;
  if (radial > 0.0 && angle_in_arc(std::atan2(dy, dx), start, sweep)) {
    return std::llround(std::fabs(radial - static_cast<double>(arc.radius)));
  }
  const Vec2 first = arc_point(arc.a, arc.radius, start);
  const Vec2 last = arc_point(arc.a, arc.radius, start + sweep);
  bool exact_first = false;
  bool exact_last = false;
  const int64_t d1 = point_point_distance(p, first, exact_first);
  const int64_t d2 = point_point_distance(p, last, exact_last);
  exact = exact_first && exact_last;
  return std::min(d1, d2);
}

int64_t point_inner_rect_distance(const Shape& rect, const Vec2& p,
                                  int64_t& error, bool& exact) {
  const int64_t inner_w = std::max<int64_t>(0, rect.half_w - rect.corner_radius);
  const int64_t inner_h = std::max<int64_t>(0, rect.half_h - rect.corner_radius);
  const int64_t dx = std::max<int64_t>(0, abs64(p.x - rect.a.x) - inner_w);
  const int64_t dy = std::max<int64_t>(0, abs64(p.y - rect.a.y) - inner_h);
  const SqrtResult root = isqrt_round(static_cast<__int128>(dx) * dx +
                                      static_cast<__int128>(dy) * dy);
  error = root.exact ? 0 : 1;
  exact = root.exact;
  return root.value;
}

DistanceResult point_shape(const Shape& shape, const Vec2& p) {
  bool exact = false;
  switch (shape.kind) {
    case ShapeKind::Point:
      return result_of(point_point_distance(p, shape.a, exact), exact ? 0 : 1,
                       exact);
    case ShapeKind::Segment:
      return result_of(point_segment_distance(p, shape.a, shape.b, exact),
                       exact ? 0 : 1, exact);
    case ShapeKind::Circle: {
      bool distance_exact = false;
      const int64_t d = point_point_distance(p, shape.a, distance_exact);
      return subtract_radius(result_of(d, distance_exact ? 0 : 1,
                                       distance_exact),
                             shape.radius);
    }
    case ShapeKind::RoundRect: {
      int64_t error = 0;
      const int64_t d = point_inner_rect_distance(shape, p, error, exact);
      return subtract_radius(result_of(d, error, exact), shape.corner_radius);
    }
    case ShapeKind::Slot: {
      bool segment_exact = false;
      const int64_t d = point_segment_distance(p, shape.a, shape.b, segment_exact);
      return subtract_radius(result_of(d, segment_exact ? 0 : 1, segment_exact),
                             shape.radius);
    }
    case ShapeKind::Polygon: {
      if (polygon_contains(shape.points, p)) return result_of(0, 0, true);
      int64_t best = std::numeric_limits<int64_t>::max();
      bool all_exact = true;
      for (size_t i = 0; i < shape.points.size(); ++i) {
        const Vec2& a = shape.points[i];
        const Vec2& b = shape.points[(i + 1) % shape.points.size()];
        bool edge_exact = false;
        best = std::min(best, point_segment_distance(p, a, b, edge_exact));
        all_exact = all_exact && edge_exact;
      }
      return result_of(best, all_exact ? 0 : 1, all_exact);
    }
    case ShapeKind::Arc: {
      const int64_t curve = point_arc_curve_distance(shape, p, exact);
      return subtract_width(result_of(curve, exact ? 0 : 1, exact),
                            shape.width_nm);
    }
  }
  throw KernelError(ErrorCode::UnsupportedShape, "unknown shape kind");
}

DistanceResult segment_shape(const Shape& segment, const Shape& shape,
                             int64_t arc_error_nm);

DistanceResult polyline_shape(const Polyline& line, const Shape& shape,
                              int64_t arc_error_nm);

DistanceResult polygon_shape(const Shape& polygon, const Shape& shape,
                             int64_t arc_error_nm) {
  DistanceResult best = result_of(std::numeric_limits<int64_t>::max(), 0, false);
  const size_t count = polygon.points.size();
  for (size_t i = 0; i < count; ++i) {
    const Shape edge = make_segment(polygon.points[i],
                                    polygon.points[(i + 1) % count]);
    const DistanceResult current = segment_shape(edge, shape, arc_error_nm);
    if (current.distance_nm < best.distance_nm) best = current;
    if (best.distance_nm == 0) return best;
  }
  if (shape_contains(polygon, shape.a)) return result_of(0, 0, true);
  if (shape.kind == ShapeKind::Polygon) {
    for (const Vec2& point : shape.points) {
      if (shape_contains(polygon, point)) return result_of(0, 0, true);
    }
  }
  return best;
}

DistanceResult segment_shape(const Shape& segment, const Shape& shape,
                             int64_t arc_error_nm) {
  switch (shape.kind) {
    case ShapeKind::Point:
      return point_shape(segment, shape.a);
    case ShapeKind::Segment: {
      if (segments_intersect(segment.a, segment.b, shape.a, shape.b)) {
        return result_of(0, 0, true);
      }
      bool e1 = false, e2 = false, e3 = false, e4 = false;
      const int64_t d1 = point_segment_distance(segment.a, shape.a, shape.b, e1);
      const int64_t d2 = point_segment_distance(segment.b, shape.a, shape.b, e2);
      const int64_t d3 = point_segment_distance(shape.a, segment.a, segment.b, e3);
      const int64_t d4 = point_segment_distance(shape.b, segment.a, segment.b, e4);
      const bool all_exact = e1 && e2 && e3 && e4;
      return result_of(std::min({d1, d2, d3, d4}), all_exact ? 0 : 1,
                       all_exact);
    }
    case ShapeKind::Circle: {
      bool exact = false;
      const int64_t d = point_segment_distance(shape.a, segment.a, segment.b,
                                               exact);
      return subtract_radius(result_of(d, exact ? 0 : 1, exact), shape.radius);
    }
    case ShapeKind::RoundRect: {
      std::vector<Vec2> inner{
          {shape.a.x - (shape.half_w - shape.corner_radius),
           shape.a.y - (shape.half_h - shape.corner_radius)},
          {shape.a.x + (shape.half_w - shape.corner_radius),
           shape.a.y - (shape.half_h - shape.corner_radius)},
          {shape.a.x + (shape.half_w - shape.corner_radius),
           shape.a.y + (shape.half_h - shape.corner_radius)},
          {shape.a.x - (shape.half_w - shape.corner_radius),
           shape.a.y + (shape.half_h - shape.corner_radius)},
      };
      const Shape inner_shape = make_polygon(std::move(inner));
      return subtract_radius(segment_shape(segment, inner_shape, arc_error_nm),
                             shape.corner_radius);
    }
    case ShapeKind::Slot: {
      const Shape core = make_segment(shape.a, shape.b);
      return subtract_radius(segment_shape(segment, core, arc_error_nm),
                             shape.radius);
    }
    case ShapeKind::Polygon: {
      if (polygon_contains(shape.points, segment.a) ||
          polygon_contains(shape.points, segment.b)) {
        return result_of(0, 0, true);
      }
      return polygon_shape(shape, segment, arc_error_nm);
    }
    case ShapeKind::Arc: {
      const Polyline line = flatten_arc(shape, arc_error_nm);
      DistanceResult best = polyline_shape(line, segment, arc_error_nm);
      best.error_nm += line.error_nm;
      return subtract_width(best, shape.width_nm);
    }
  }
  throw KernelError(ErrorCode::UnsupportedShape, "unknown shape kind");
}

DistanceResult polyline_shape(const Polyline& line, const Shape& shape,
                              int64_t arc_error_nm) {
  if (line.points.empty()) {
    throw KernelError(ErrorCode::InvalidInput, "empty polyline");
  }
  DistanceResult best = result_of(std::numeric_limits<int64_t>::max(), 0, false);
  const size_t count = line.points.size();
  const size_t edges = line.closed ? count : (count > 1 ? count - 1 : 0);
  if (edges == 0) {
    return point_shape(shape, line.points.front());
  }
  for (size_t i = 0; i < edges; ++i) {
    const Shape edge = make_segment(line.points[i],
                                    line.points[(i + 1) % count]);
    const DistanceResult current = segment_shape(edge, shape, arc_error_nm);
    if (current.distance_nm < best.distance_nm) best = current;
    if (best.distance_nm == 0) break;
  }
  best.error_nm += line.error_nm;
  return best;
}

DistanceResult polygon_polygon(const Shape& a, const Shape& b,
                               int64_t arc_error_nm) {
  const DistanceResult direct = polygon_shape(a, b, arc_error_nm);
  if (direct.distance_nm == 0) return direct;
  const DistanceResult reverse = polygon_shape(b, a, arc_error_nm);
  return reverse.distance_nm < direct.distance_nm ? reverse : direct;
}

}  // namespace

const char* shape_kind_name(ShapeKind kind) {
  switch (kind) {
    case ShapeKind::Point: return "point";
    case ShapeKind::Segment: return "segment";
    case ShapeKind::Circle: return "circle";
    case ShapeKind::RoundRect: return "round_rect";
    case ShapeKind::Slot: return "slot";
    case ShapeKind::Polygon: return "polygon";
    case ShapeKind::Arc: return "arc";
  }
  return "unknown";
}

Shape make_point(const Vec2& p) {
  validate_point(p);
  Shape shape;
  shape.kind = ShapeKind::Point;
  shape.a = p;
  return shape;
}

Shape make_segment(const Vec2& a, const Vec2& b) {
  validate_point(a);
  validate_point(b);
  Shape shape;
  shape.kind = ShapeKind::Segment;
  shape.a = a;
  shape.b = b;
  return shape;
}

Shape make_circle(const Vec2& center, int64_t radius) {
  validate_point(center);
  require_radius(radius, "circle radius");
  if (radius == 0) return make_point(center);
  Shape shape;
  shape.kind = ShapeKind::Circle;
  shape.a = center;
  shape.radius = radius;
  return shape;
}

Shape make_round_rect(const Vec2& center, int64_t half_w, int64_t half_h,
                      int64_t corner_radius) {
  validate_point(center);
  require_radius(half_w, "rounded rectangle half width");
  require_radius(half_h, "rounded rectangle half height");
  require_radius(corner_radius, "rounded rectangle corner radius");
  if (corner_radius > std::min(half_w, half_h)) {
    throw KernelError(ErrorCode::InvalidInput,
                      "corner radius exceeds half extents");
  }
  Shape shape;
  shape.kind = ShapeKind::RoundRect;
  shape.a = center;
  shape.half_w = half_w;
  shape.half_h = half_h;
  shape.corner_radius = corner_radius;
  return shape;
}

Shape make_slot(const Vec2& a, const Vec2& b, int64_t radius) {
  validate_point(a);
  validate_point(b);
  require_radius(radius, "slot radius");
  Shape shape;
  shape.kind = ShapeKind::Slot;
  shape.a = a;
  shape.b = b;
  shape.radius = radius;
  return shape;
}

Shape make_polygon(std::vector<Vec2> points) {
  if (points.size() < 3) {
    throw KernelError(ErrorCode::InvalidInput,
                      "polygon needs at least three vertices");
  }
  for (const Vec2& point : points) validate_point(point);
  Shape shape;
  shape.kind = ShapeKind::Polygon;
  shape.points = std::move(points);
  return shape;
}

Shape make_arc(const Vec2& center, int64_t radius, int32_t start_urad,
               int32_t sweep_urad, int64_t width_nm) {
  validate_point(center);
  require_radius(radius, "arc radius");
  if (radius == 0) {
    throw KernelError(ErrorCode::InvalidInput, "arc radius must be positive");
  }
  if (sweep_urad == 0) {
    throw KernelError(ErrorCode::InvalidInput, "arc sweep must be non-zero");
  }
  require_radius(width_nm, "arc width");
  Shape shape;
  shape.kind = ShapeKind::Arc;
  shape.a = center;
  shape.radius = radius;
  shape.start_urad = start_urad;
  shape.sweep_urad = sweep_urad;
  shape.width_nm = width_nm;
  return shape;
}

int64_t point_point_distance(const Vec2& a, const Vec2& b, bool& exact) {
  validate_point(a);
  validate_point(b);
  const Vec2 delta{checked_sub(a.x, b.x), checked_sub(a.y, b.y)};
  const SqrtResult root = isqrt_round(length_squared(delta));
  exact = root.exact;
  return root.value;
}

int64_t point_segment_distance(const Vec2& p, const Vec2& a, const Vec2& b,
                               bool& exact) {
  validate_point(p);
  const Vec2 ab = checked_sub(b, a);
  const __int128 denominator = dot(ab, ab);
  if (denominator == 0) return point_point_distance(p, a, exact);
  const Vec2 ap = checked_sub(p, a);
  const __int128 projection = dot(ap, ab);
  if (projection <= 0) return point_point_distance(p, a, exact);
  if (projection >= denominator) return point_point_distance(p, b, exact);
  const long double ratio =
      static_cast<long double>(projection) / static_cast<long double>(denominator);
  const Vec2 closest{
      checked_add(a.x, std::llround(static_cast<long double>(ab.x) * ratio)),
      checked_add(a.y, std::llround(static_cast<long double>(ab.y) * ratio))};
  exact = false;
  bool distance_exact = false;
  const int64_t distance = point_point_distance(p, closest, distance_exact);
  exact = distance_exact;
  return distance;
}

bool segments_intersect(const Vec2& a, const Vec2& b, const Vec2& c,
                        const Vec2& d) {
  auto on_segment = [](const Vec2& p, const Vec2& q, const Vec2& r) {
    return std::min(p.x, r.x) <= q.x && q.x <= std::max(p.x, r.x) &&
           std::min(p.y, r.y) <= q.y && q.y <= std::max(p.y, r.y);
  };
  const Vec2 ab = checked_sub(b, a);
  const Vec2 cd = checked_sub(d, c);
  const __int128 o1 = cross(ab, checked_sub(c, a));
  const __int128 o2 = cross(ab, checked_sub(d, a));
  const __int128 o3 = cross(cd, checked_sub(a, c));
  const __int128 o4 = cross(cd, checked_sub(b, c));
  if (o1 == 0 && on_segment(a, c, b)) return true;
  if (o2 == 0 && on_segment(a, d, b)) return true;
  if (o3 == 0 && on_segment(c, a, d)) return true;
  if (o4 == 0 && on_segment(c, b, d)) return true;
  return ((o1 > 0) != (o2 > 0)) && ((o3 > 0) != (o4 > 0));
}

bool polygon_contains(const std::vector<Vec2>& points, const Vec2& p) {
  if (points.size() < 3) return false;
  bool inside = false;
  for (size_t i = 0, j = points.size() - 1; i < points.size(); j = i++) {
    const Vec2& a = points[i];
    const Vec2& b = points[j];
    if ((a.y > p.y) == (b.y > p.y)) continue;
    const __int128 lhs =
        static_cast<__int128>(b.x - a.x) * (p.y - a.y);
    const __int128 rhs =
        static_cast<__int128>(p.x - a.x) * (b.y - a.y);
    const bool crosses = b.y > a.y ? lhs > rhs : lhs < rhs;
    if (crosses) inside = !inside;
  }
  return inside;
}

int64_t point_shape_distance(const Shape& shape, const Vec2& p, bool& exact) {
  const DistanceResult result = point_shape(shape, p);
  exact = result.exact;
  return result.distance_nm;
}

bool shape_contains(const Shape& shape, const Vec2& p) {
  switch (shape.kind) {
    case ShapeKind::Point:
      return shape.a.x == p.x && shape.a.y == p.y;
    case ShapeKind::Segment: {
      bool exact = false;
      return point_segment_distance(p, shape.a, shape.b, exact) == 0;
    }
    case ShapeKind::Circle: {
      bool exact = false;
      return point_point_distance(p, shape.a, exact) <= shape.radius;
    }
    case ShapeKind::RoundRect: {
      int64_t error = 0;
      bool exact = false;
      return point_inner_rect_distance(shape, p, error, exact) <=
             shape.corner_radius;
    }
    case ShapeKind::Slot: {
      bool exact = false;
      return point_segment_distance(p, shape.a, shape.b, exact) <= shape.radius;
    }
    case ShapeKind::Polygon:
      return polygon_contains(shape.points, p);
    case ShapeKind::Arc: {
      bool exact = false;
      return point_arc_curve_distance(shape, p, exact) <= shape.width_nm / 2;
    }
  }
  return false;
}

DistanceResult shape_distance(const Shape& a, const Shape& b,
                              int64_t arc_error_nm) {
  if (arc_error_nm <= 0) arc_error_nm = kDefaultArcErrorNm;
  if (a.kind == ShapeKind::Point) return point_shape(b, a.a);
  if (b.kind == ShapeKind::Point) return point_shape(a, b.a);

  if (a.kind == ShapeKind::Circle) {
    const DistanceResult center = point_shape(b, a.a);
    return subtract_radius(center, a.radius);
  }
  if (b.kind == ShapeKind::Circle) {
    const DistanceResult center = point_shape(a, b.a);
    return subtract_radius(center, b.radius);
  }
  if (a.kind == ShapeKind::Slot) {
    const Shape core = make_segment(a.a, a.b);
    return subtract_radius(shape_distance(core, b, arc_error_nm), a.radius);
  }
  if (b.kind == ShapeKind::Slot) {
    const Shape core = make_segment(b.a, b.b);
    return subtract_radius(shape_distance(a, core, arc_error_nm), b.radius);
  }
  if (a.kind == ShapeKind::RoundRect) {
    std::vector<Vec2> inner{
        {a.a.x - (a.half_w - a.corner_radius),
         a.a.y - (a.half_h - a.corner_radius)},
        {a.a.x + (a.half_w - a.corner_radius),
         a.a.y - (a.half_h - a.corner_radius)},
        {a.a.x + (a.half_w - a.corner_radius),
         a.a.y + (a.half_h - a.corner_radius)},
        {a.a.x - (a.half_w - a.corner_radius),
         a.a.y + (a.half_h - a.corner_radius)},
    };
    const Shape inner_shape = make_polygon(std::move(inner));
    return subtract_radius(shape_distance(inner_shape, b, arc_error_nm),
                           a.corner_radius);
  }
  if (b.kind == ShapeKind::RoundRect) {
    return shape_distance(b, a, arc_error_nm);
  }
  if (a.kind == ShapeKind::Segment) return segment_shape(a, b, arc_error_nm);
  if (b.kind == ShapeKind::Segment) return segment_shape(b, a, arc_error_nm);
  if (a.kind == ShapeKind::Arc) {
    const Polyline line = flatten_arc(a, arc_error_nm);
    DistanceResult result = polyline_shape(line, b, arc_error_nm);
    result.error_nm += line.error_nm;
    return subtract_width(result, a.width_nm);
  }
  if (b.kind == ShapeKind::Arc) {
    return shape_distance(b, a, arc_error_nm);
  }
  if (a.kind == ShapeKind::Polygon && b.kind == ShapeKind::Polygon) {
    return polygon_polygon(a, b, arc_error_nm);
  }
  if (a.kind == ShapeKind::Polygon) {
    return polygon_shape(a, b, arc_error_nm);
  }
  if (b.kind == ShapeKind::Polygon) {
    return polygon_shape(b, a, arc_error_nm);
  }
  throw KernelError(ErrorCode::UnsupportedShape,
                    "distance pair is not supported");
}

std::vector<Vec2> rounded_rect_outline(const Shape& shape, int64_t max_error_nm,
                                       int64_t& error_nm) {
  if (shape.kind != ShapeKind::RoundRect) {
    throw KernelError(ErrorCode::InvalidInput,
                      "rounded_rect_outline needs a round_rect");
  }
  error_nm = 0;
  const int64_t inner_w = shape.half_w - shape.corner_radius;
  const int64_t inner_h = shape.half_h - shape.corner_radius;
  std::vector<Vec2> points;
  if (shape.corner_radius == 0) {
    points = {
        {shape.a.x - shape.half_w, shape.a.y - shape.half_h},
        {shape.a.x + shape.half_w, shape.a.y - shape.half_h},
        {shape.a.x + shape.half_w, shape.a.y + shape.half_h},
        {shape.a.x - shape.half_w, shape.a.y + shape.half_h},
    };
    return points;
  }
  const int64_t r = shape.corner_radius;
  const struct {
    Vec2 center;
    double start;
  } corners[4] = {
      {{shape.a.x + inner_w, shape.a.y + inner_h}, -kPi / 2.0},
      {{shape.a.x - inner_w, shape.a.y + inner_h}, 0.0},
      {{shape.a.x - inner_w, shape.a.y - inner_h}, kPi / 2.0},
      {{shape.a.x + inner_w, shape.a.y - inner_h}, kPi},
  };
  double step = kPi / 2.0;
  if (static_cast<double>(max_error_nm) < static_cast<double>(r)) {
    step = 2.0 * std::acos(1.0 - static_cast<double>(max_error_nm) /
                                     static_cast<double>(r));
  }
  step = std::clamp(step, kPi / 180.0, kPi / 2.0);
  const int steps = static_cast<int>(std::ceil((kPi / 2.0) / step));
  const double actual_step = (kPi / 2.0) / steps;
  const int64_t sagitta =
      static_cast<int64_t>(std::ceil(static_cast<double>(r) *
                                     (1.0 - std::cos(actual_step / 2.0))));
  error_nm = sagitta + 1;
  for (const auto& corner : corners) {
    for (int i = 0; i <= steps; ++i) {
      points.push_back(
          arc_point(corner.center, r, corner.start + actual_step * i));
    }
  }
  return points;
}

Polyline flatten_arc(const Shape& arc, int64_t max_error_nm) {
  if (arc.kind != ShapeKind::Arc) {
    throw KernelError(ErrorCode::InvalidInput, "flatten_arc needs an arc");
  }
  if (max_error_nm <= 0) max_error_nm = kDefaultArcErrorNm;
  const double sweep = std::fabs(arc.sweep_urad * kUradToRad);
  const double start = arc.start_urad * kUradToRad;
  const double signed_sweep = arc.sweep_urad * kUradToRad;
  double step = kPi / 2.0;
  if (static_cast<double>(max_error_nm) < static_cast<double>(arc.radius)) {
    step = 2.0 * std::acos(1.0 - static_cast<double>(max_error_nm) /
                                     static_cast<double>(arc.radius));
  }
  step = std::clamp(step, 1e-5, kPi / 2.0);
  int segments = static_cast<int>(std::ceil(sweep / step));
  segments = std::clamp(segments, 1, 4096);
  const double actual_step = signed_sweep / segments;
  const int64_t sagitta =
      static_cast<int64_t>(std::ceil(static_cast<double>(arc.radius) *
                                     (1.0 - std::cos(std::fabs(actual_step) / 2.0))));
  Polyline line;
  line.error_nm = sagitta + 2;
  for (int i = 0; i <= segments; ++i) {
    line.points.push_back(arc_point(arc.a, arc.radius, start + actual_step * i));
  }
  line.closed = std::fabs(sweep - kTwoPi) <= 1e-6;
  if (line.closed && line.points.size() > 1) line.points.pop_back();
  return line;
}

BoundsResult shape_bounds(const Shape& shape) {
  BoundsResult out;
  switch (shape.kind) {
    case ShapeKind::Point:
      out.bounds = Aabb{shape.a.x, shape.a.y, shape.a.x, shape.a.y};
      break;
    case ShapeKind::Segment:
      out.bounds = Aabb::from_points(shape.a, shape.b);
      break;
    case ShapeKind::Circle:
      out.bounds = Aabb::around(shape.a, shape.radius);
      break;
    case ShapeKind::RoundRect:
      out.bounds = Aabb::around(shape.a, std::max(shape.half_w, shape.half_h));
      break;
    case ShapeKind::Slot:
      out.bounds = Aabb::from_points(shape.a, shape.b).expanded(shape.radius);
      break;
    case ShapeKind::Polygon: {
      Aabb bounds{shape.points[0].x, shape.points[0].y,
                  shape.points[0].x, shape.points[0].y};
      for (const Vec2& point : shape.points) {
        bounds = bounds.merged(Aabb{point.x, point.y, point.x, point.y});
      }
      out.bounds = bounds;
      break;
    }
    case ShapeKind::Arc:
      // Conservative full-circle bounds; never under-estimates the arc.
      out.bounds = Aabb::around(shape.a, checked_add(shape.radius,
                                                      shape.width_nm / 2));
      break;
  }
  out.error_nm = 0;
  return out;
}

ShapeTransformResult transform_shape(const Shape& shape, const Transform& t) {
  ShapeTransformResult out;
  out.error_nm = t.rounding_error_nm();
  switch (shape.kind) {
    case ShapeKind::Point:
      out.shape = make_point(t.apply(shape.a));
      break;
    case ShapeKind::Segment:
      out.shape = make_segment(t.apply(shape.a), t.apply(shape.b));
      break;
    case ShapeKind::Circle:
      out.shape = make_circle(t.apply(shape.a), shape.radius);
      break;
    case ShapeKind::Slot:
      out.shape = make_slot(t.apply(shape.a), t.apply(shape.b), shape.radius);
      break;
    case ShapeKind::Polygon: {
      std::vector<Vec2> points;
      points.reserve(shape.points.size());
      for (const Vec2& point : shape.points) points.push_back(t.apply(point));
      out.shape = make_polygon(std::move(points));
      break;
    }
    case ShapeKind::RoundRect: {
      const int32_t rotation = t.normalized_rotation();
      if (rotation % 90 == 0) {
        const bool swap = rotation == 90 || rotation == 270;
        out.shape = make_round_rect(
            t.apply(shape.a), swap ? shape.half_h : shape.half_w,
            swap ? shape.half_w : shape.half_h, shape.corner_radius);
      } else {
        int64_t outline_error = 0;
        std::vector<Vec2> local = rounded_rect_outline(shape, 100, outline_error);
        std::vector<Vec2> points;
        points.reserve(local.size());
        for (const Vec2& point : local) points.push_back(t.apply(point));
        out.shape = make_polygon(std::move(points));
        out.error_nm += outline_error;
      }
      break;
    }
    case ShapeKind::Arc: {
      const double start = shape.start_urad * kUradToRad;
      const double sweep = shape.sweep_urad * kUradToRad;
      double new_start = start;
      if (t.bottom) {
        new_start = kPi - start - sweep;
      }
      new_start += static_cast<double>(t.normalized_rotation()) * kPi / 180.0;
      out.shape = make_arc(
          t.apply(shape.a), shape.radius,
          static_cast<int32_t>(std::llround(new_start * kRadToUrad)),
          shape.sweep_urad, shape.width_nm);
      // Angles are stored in micro-radians. Re-quantising a transformed arc
      // moves its endpoints by at most radius * 0.5 urad; declare that bound
      // instead of pretending the rotation was exact.
      out.error_nm += 1 + static_cast<int64_t>(std::ceil(
                              static_cast<double>(shape.radius) * 5e-7));
      break;
    }
  }
  return out;
}

}  // namespace mota
