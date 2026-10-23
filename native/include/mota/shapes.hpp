// Shape model and typed distances.
//
// Filled copper shapes: point, segment, circle, rounded rectangle, slot
// (stadium), polygon. Arcs are curve strokes with an optional width. Distances
// are returned with an explicit error bound in nanometres; predicates use
// exact __int128 arithmetic and arcs are flattened with a requested sagitta
// bound. Callers performing clearance checks must compare
// (distance - error) against the requirement.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "mota/types.hpp"

namespace mota {

enum class ShapeKind {
  Point,
  Segment,
  Circle,
  RoundRect,
  Slot,
  Polygon,
  Arc,
};

const char* shape_kind_name(ShapeKind kind);

struct Shape {
  ShapeKind kind = ShapeKind::Point;

  // point / circle center / segment a / slot a / arc center
  Vec2 a{};
  // segment b / slot b
  Vec2 b{};
  // circle radius / slot radius / arc radius
  int64_t radius = 0;
  // rounded rectangle half extents and corner radius
  int64_t half_w = 0;
  int64_t half_h = 0;
  int64_t corner_radius = 0;
  // arc angles in micro-radians
  int32_t start_urad = 0;
  int32_t sweep_urad = 0;
  // arc stroke width (0 = zero-width curve)
  int64_t width_nm = 0;
  // polygon vertices (filled, even-odd)
  std::vector<Vec2> points;
};

Shape make_point(const Vec2& p);
Shape make_segment(const Vec2& a, const Vec2& b);
Shape make_circle(const Vec2& center, int64_t radius);
Shape make_round_rect(const Vec2& center, int64_t half_w, int64_t half_h,
                      int64_t corner_radius);
Shape make_slot(const Vec2& a, const Vec2& b, int64_t radius);
Shape make_polygon(std::vector<Vec2> points);
Shape make_arc(const Vec2& center, int64_t radius, int32_t start_urad,
               int32_t sweep_urad, int64_t width_nm);

struct DistanceResult {
  int64_t distance_nm = 0;
  int64_t error_nm = 0;
  bool exact = true;
  bool intersects = false;
};

struct BoundsResult {
  Aabb bounds{};
  int64_t error_nm = 0;
};

struct Polyline {
  std::vector<Vec2> points;
  bool closed = false;
  int64_t error_nm = 0;
};

struct ShapeTransformResult {
  Shape shape;
  int64_t error_nm = 0;
};

int64_t point_point_distance(const Vec2& a, const Vec2& b, bool& exact);
int64_t point_segment_distance(const Vec2& p, const Vec2& a, const Vec2& b,
                               bool& exact);
bool segments_intersect(const Vec2& a, const Vec2& b, const Vec2& c,
                        const Vec2& d);
bool polygon_contains(const std::vector<Vec2>& points, const Vec2& p);

int64_t point_shape_distance(const Shape& shape, const Vec2& p, bool& exact);
bool shape_contains(const Shape& shape, const Vec2& p);
DistanceResult shape_distance(const Shape& a, const Shape& b,
                              int64_t arc_error_nm = 100);

BoundsResult shape_bounds(const Shape& shape);
ShapeTransformResult transform_shape(const Shape& shape, const Transform& t);
Polyline flatten_arc(const Shape& arc, int64_t max_error_nm);

std::vector<Vec2> rounded_rect_outline(const Shape& shape, int64_t max_error_nm,
                                       int64_t& error_nm);

}  // namespace mota
