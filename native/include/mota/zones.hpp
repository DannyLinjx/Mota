// Copper zone fill for rectilinear planes.
//
// The fill is a real rectilinear polygon set (axis-aligned rectangles), not a
// display hatch and not a coarse grid. Arbitrary outlines are rejected with
// ZONE_NON_RECTILINEAR instead of being silently approximated. Curved holes,
// pads and keepouts are subtracted by their conservative AABB; this can remove
// slightly more copper than an exact offset but never creates a false short.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "mota/shapes.hpp"
#include "mota/types.hpp"

namespace mota {

struct FillRect {
  int64_t x0 = 0;
  int64_t y0 = 0;
  int64_t x1 = 0;
  int64_t y1 = 0;
  bool spoke = false;

  int64_t width() const { return x1 - x0; }
  int64_t height() const { return y1 - y0; }
  int64_t area() const;
  bool valid() const { return x1 > x0 && y1 > y0; }
  bool touches(const FillRect& other) const;
};

struct ZoneSpec {
  std::string id;
  std::string net;
  std::string layer;
  int priority = 0;
  std::vector<Vec2> outline;
  int64_t clearance_nm = 200'000;
  int64_t thermal_gap_nm = 250'000;
  int64_t thermal_spoke_width_nm = 250'000;
  int thermal_spokes = 4;  // 0 = solid connection
  bool remove_islands = true;
};

struct ZoneObstacle {
  std::string id;
  Shape shape;
  std::string net;
  // Foreign copper is expanded by the zone clearance; physical holes and
  // keepouts are subtracted as-is.
  bool expand_clearance = true;
};

struct ZoneAnchor {
  std::string id;
  Shape shape;
  bool thermal = false;  // pads get thermal relief; tracks/vias are solid
};

struct FillComponent {
  std::string id;
  std::string net;
  std::string layer;
  std::vector<FillRect> rects;
  std::vector<FillRect> spokes;
  bool anchored = false;
  int64_t area_nm2 = 0;
};

struct ZoneFillResult {
  std::string zone_id;
  std::string net;
  std::string layer;
  int priority = 0;
  std::vector<Vec2> outline;
  std::vector<FillComponent> components;
  int islands_removed = 0;
  int64_t removed_area_nm2 = 0;
  int64_t kept_area_nm2 = 0;
  bool non_rectilinear = false;
  std::string diagnostic;
};

bool polygon_is_rectilinear(const std::vector<Vec2>& polygon);

// Fills one zone. `higher_priority` rects are already committed copper from
// zones with greater priority and are subtracted from this fill.
ZoneFillResult fill_zone(const ZoneSpec& zone,
                         const std::vector<Vec2>& board_outline,
                         const std::vector<ZoneObstacle>& obstacles,
                         const std::vector<ZoneAnchor>& anchors,
                         const std::vector<FillRect>& higher_priority);

}  // namespace mota
