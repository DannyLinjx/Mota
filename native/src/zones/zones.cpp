#include "mota/zones.hpp"

#include <algorithm>
#include <limits>
#include <map>
#include <numeric>

#include "mota/rtree.hpp"

namespace mota {
namespace {

using Interval = std::pair<int64_t, int64_t>;

struct UnionFind {
  std::vector<int> parent;
  explicit UnionFind(size_t size) : parent(size) {
    std::iota(parent.begin(), parent.end(), 0);
  }
  int find(int value) {
    while (parent[static_cast<size_t>(value)] != value) {
      parent[static_cast<size_t>(value)] =
          parent[static_cast<size_t>(parent[static_cast<size_t>(value)])];
      value = parent[static_cast<size_t>(value)];
    }
    return value;
  }
  void unite(int a, int b) {
    a = find(a);
    b = find(b);
    if (a != b) parent[static_cast<size_t>(a)] = b;
  }
};

std::vector<Interval> polygon_intervals(const std::vector<Vec2>& polygon,
                                        int64_t y) {
  std::vector<int64_t> crossings;
  for (size_t i = 0; i < polygon.size(); ++i) {
    const Vec2& a = polygon[i];
    const Vec2& b = polygon[(i + 1) % polygon.size()];
    if ((a.y <= y && b.y > y) || (b.y <= y && a.y > y)) {
      crossings.push_back(a.y == b.y ? a.x : (a.x + b.x) / 2);
    }
  }
  std::sort(crossings.begin(), crossings.end());
  std::vector<Interval> out;
  for (size_t i = 0; i + 1 < crossings.size(); i += 2) {
    if (crossings[i + 1] > crossings[i]) {
      out.emplace_back(crossings[i], crossings[i + 1]);
    }
  }
  return out;
}

std::vector<Interval> intersect_intervals(const std::vector<Interval>& a,
                                          const std::vector<Interval>& b) {
  std::vector<Interval> out;
  size_t i = 0;
  size_t j = 0;
  while (i < a.size() && j < b.size()) {
    const int64_t start = std::max(a[i].first, b[j].first);
    const int64_t end = std::min(a[i].second, b[j].second);
    if (end > start) out.emplace_back(start, end);
    if (a[i].second < b[j].second) {
      ++i;
    } else {
      ++j;
    }
  }
  return out;
}

std::vector<Interval> subtract_intervals(std::vector<Interval> base,
                                         std::vector<Interval> cuts) {
  std::sort(cuts.begin(), cuts.end());
  std::vector<Interval> merged;
  for (const Interval& cut : cuts) {
    if (cut.second <= cut.first) continue;
    if (!merged.empty() && cut.first <= merged.back().second) {
      merged.back().second = std::max(merged.back().second, cut.second);
    } else {
      merged.push_back(cut);
    }
  }
  std::vector<Interval> out;
  for (const Interval& piece : base) {
    int64_t cursor = piece.first;
    for (const Interval& cut : merged) {
      if (cut.second <= cursor) continue;
      if (cut.first >= piece.second) break;
      if (cut.first > cursor) out.emplace_back(cursor, std::min(cut.first, piece.second));
      cursor = std::max(cursor, cut.second);
      if (cursor >= piece.second) break;
    }
    if (cursor < piece.second) out.emplace_back(cursor, piece.second);
  }
  return out;
}

Aabb zone_bounds(const std::vector<Vec2>& outline) {
  Aabb bounds{outline[0].x, outline[0].y, outline[0].x, outline[0].y};
  for (const Vec2& point : outline) {
    bounds = bounds.merged(Aabb{point.x, point.y, point.x, point.y});
  }
  return bounds;
}

std::vector<Interval> obstacle_intervals(const ZoneObstacle& obstacle,
                                         int64_t clearance, int64_t y) {
  if (!obstacle.expand_clearance && obstacle.shape.kind == ShapeKind::Polygon) {
    return polygon_intervals(obstacle.shape.points, y);
  }
  Aabb bounds = shape_bounds(obstacle.shape).bounds;
  if (obstacle.expand_clearance) bounds = bounds.expanded(clearance);
  if (y < bounds.min_y || y > bounds.max_y) return {};
  return {Interval{bounds.min_x, bounds.max_x}};
}

std::vector<FillRect> band_fill(const FillRect& base,
                                const std::vector<Vec2>& board_outline,
                                const std::vector<Vec2>& zone_outline,
                                const std::vector<ZoneObstacle>& obstacles,
                                const std::vector<FillRect>& higher,
                                int64_t clearance) {
  std::vector<int64_t> cuts_y{base.y0, base.y1};
  auto add_y = [&](int64_t value) {
    if (value > base.y0 && value < base.y1) cuts_y.push_back(value);
  };
  for (const Vec2& point : board_outline) add_y(point.y);
  for (const Vec2& point : zone_outline) add_y(point.y);
  for (const ZoneObstacle& obstacle : obstacles) {
    const Aabb bounds = shape_bounds(obstacle.shape).bounds;
    const Aabb expanded = obstacle.expand_clearance
                              ? bounds.expanded(clearance)
                              : bounds;
    add_y(expanded.min_y);
    add_y(expanded.max_y);
    if (obstacle.shape.kind == ShapeKind::Polygon) {
      for (const Vec2& point : obstacle.shape.points) add_y(point.y);
    }
  }
  for (const FillRect& rect : higher) {
    add_y(rect.y0);
    add_y(rect.y1);
  }
  std::sort(cuts_y.begin(), cuts_y.end());
  cuts_y.erase(std::unique(cuts_y.begin(), cuts_y.end()), cuts_y.end());

  std::vector<FillRect> out;
  for (size_t i = 0; i + 1 < cuts_y.size(); ++i) {
    const int64_t y0 = cuts_y[i];
    const int64_t y1 = cuts_y[i + 1];
    if (y1 <= y0) continue;
    const int64_t y_mid = y0 + (y1 - y0) / 2;
    std::vector<Interval> pieces =
        intersect_intervals(polygon_intervals(zone_outline, y_mid),
                            polygon_intervals(board_outline, y_mid));
    pieces = intersect_intervals(pieces, {Interval{base.x0, base.x1}});
    if (pieces.empty()) continue;
    std::vector<Interval> cuts;
    for (const ZoneObstacle& obstacle : obstacles) {
      const auto intervals = obstacle_intervals(obstacle, clearance, y_mid);
      cuts.insert(cuts.end(), intervals.begin(), intervals.end());
    }
    for (const FillRect& rect : higher) {
      if (y_mid >= rect.y0 && y_mid <= rect.y1) {
        cuts.emplace_back(rect.x0, rect.x1);
      }
    }
    for (const Interval& piece : subtract_intervals(pieces, cuts)) {
      FillRect rect{piece.first, y0, piece.second, y1, false};
      if (rect.valid()) out.push_back(rect);
    }
  }
  return out;
}

Shape rect_shape(const FillRect& rect) {
  return make_polygon({{rect.x0, rect.y0}, {rect.x1, rect.y0},
                       {rect.x1, rect.y1}, {rect.x0, rect.y1}});
}

}  // namespace

int64_t FillRect::area() const {
  if (!valid()) return 0;
  const __int128 value = static_cast<__int128>(width()) * height();
  return value > std::numeric_limits<int64_t>::max()
             ? std::numeric_limits<int64_t>::max()
             : static_cast<int64_t>(value);
}

bool FillRect::touches(const FillRect& other) const {
  return x0 <= other.x1 && other.x0 <= x1 && y0 <= other.y1 && other.y0 <= y1;
}

bool polygon_is_rectilinear(const std::vector<Vec2>& polygon) {
  if (polygon.size() < 3) return false;
  for (size_t i = 0; i < polygon.size(); ++i) {
    const Vec2& a = polygon[i];
    const Vec2& b = polygon[(i + 1) % polygon.size()];
    if (a.x != b.x && a.y != b.y) return false;
  }
  return true;
}

ZoneFillResult fill_zone(const ZoneSpec& zone,
                         const std::vector<Vec2>& board_outline,
                         const std::vector<ZoneObstacle>& obstacles,
                         const std::vector<ZoneAnchor>& anchors,
                         const std::vector<FillRect>& higher_priority) {
  ZoneFillResult result;
  result.zone_id = zone.id;
  result.net = zone.net;
  result.layer = zone.layer;
  result.priority = zone.priority;
  result.outline = zone.outline;
  if (zone.outline.size() < 3 || board_outline.size() < 3) {
    result.diagnostic = "ZONE_OR_BOARD_OUTLINE_MISSING";
    return result;
  }
  if (!polygon_is_rectilinear(zone.outline) ||
      !polygon_is_rectilinear(board_outline)) {
    result.non_rectilinear = true;
    result.diagnostic = "ZONE_NON_RECTILINEAR";
    return result;
  }
  if (zone.clearance_nm < 0 || zone.thermal_gap_nm < 0 ||
      zone.thermal_spoke_width_nm < 0 || zone.thermal_spokes < 0) {
    throw KernelError(ErrorCode::InvalidInput, "zone dimensions must be non-negative");
  }

  std::vector<ZoneObstacle> effective = obstacles;
  struct ThermalPlan {
    ZoneAnchor anchor;
    Aabb exclusion;
    std::string exclusion_id;
  };
  std::vector<ThermalPlan> thermals;
  if (zone.thermal_spokes > 0) {
    for (const ZoneAnchor& anchor : anchors) {
      if (!anchor.thermal) continue;
      const Aabb bounds = shape_bounds(anchor.shape).bounds;
      const Aabb exclusion = bounds.expanded(zone.thermal_gap_nm);
      const std::string id = "thermal:" + anchor.id;
      ZoneObstacle obstacle;
      obstacle.id = id;
      obstacle.net = zone.net;
      obstacle.expand_clearance = false;
      obstacle.shape = make_polygon({{exclusion.min_x, exclusion.min_y},
                                     {exclusion.max_x, exclusion.min_y},
                                     {exclusion.max_x, exclusion.max_y},
                                     {exclusion.min_x, exclusion.max_y}});
      effective.push_back(obstacle);
      thermals.push_back(ThermalPlan{anchor, exclusion, id});
    }
  }

  const Aabb zone_box = zone_bounds(zone.outline);
  FillRect base{zone_box.min_x, zone_box.min_y, zone_box.max_x, zone_box.max_y};
  for (FillRect rect : band_fill(base, board_outline, zone.outline, effective,
                                 higher_priority, zone.clearance_nm)) {
    result.components.push_back(FillComponent{
        zone.id + ":fill", zone.net, zone.layer, {rect}, {}, false, rect.area()});
  }

  std::vector<FillRect> all_rects;
  for (const FillComponent& component : result.components) {
    all_rects.insert(all_rects.end(), component.rects.begin(), component.rects.end());
  }

  if (zone.thermal_spokes > 0) {
    for (const ThermalPlan& plan : thermals) {
      const Aabb pad = shape_bounds(plan.anchor.shape).bounds;
      const int64_t cx = pad.center_x();
      const int64_t cy = pad.center_y();
      const int64_t half = zone.thermal_spoke_width_nm / 2;
      const std::vector<FillRect> candidates = {
          {plan.exclusion.min_x, cy - half, pad.min_x, cy + half, true},
          {pad.max_x, cy - half, plan.exclusion.max_x, cy + half, true},
          {cx - half, plan.exclusion.min_y, cx + half, pad.min_y, true},
          {cx - half, pad.max_y, cx + half, plan.exclusion.max_y, true},
      };
      std::vector<ZoneObstacle> spoke_obstacles;
      for (const ZoneObstacle& obstacle : effective) {
        if (obstacle.id != plan.exclusion_id) spoke_obstacles.push_back(obstacle);
      }
      for (const FillRect& candidate : candidates) {
        if (!candidate.valid()) continue;
        for (FillRect spoke : band_fill(candidate, board_outline, zone.outline,
                                        spoke_obstacles, higher_priority,
                                        zone.clearance_nm)) {
          spoke.spoke = true;
          all_rects.push_back(spoke);
        }
      }
    }
  }

  if (all_rects.empty()) {
    result.kept_area_nm2 = 0;
    return result;
  }

  UnionFind uf(all_rects.size());
  std::vector<Aabb> rect_bounds;
  rect_bounds.reserve(all_rects.size());
  for (const FillRect& rect : all_rects) {
    rect_bounds.push_back(Aabb{rect.x0, rect.y0, rect.x1, rect.y1});
  }
  RTree index;
  index.build(rect_bounds);
  for (size_t i = 0; i < all_rects.size(); ++i) {
    std::vector<size_t> hits;
    index.query(rect_bounds[i], hits);
    for (size_t j : hits) {
      if (j > i && all_rects[i].touches(all_rects[j])) uf.unite(static_cast<int>(i),
                                                                static_cast<int>(j));
    }
  }

  std::vector<bool> anchored(all_rects.size(), false);
  for (const ZoneAnchor& anchor : anchors) {
    const Aabb bounds = shape_bounds(anchor.shape).bounds.expanded(1);
    std::vector<size_t> hits;
    index.query(bounds, hits);
    for (size_t index_value : hits) {
      if (anchored[index_value]) continue;
      if (shape_distance(rect_shape(all_rects[index_value]), anchor.shape)
              .distance_nm <= 1) {
        anchored[index_value] = true;
      }
    }
  }

  std::map<int, std::vector<size_t>> groups;
  for (size_t i = 0; i < all_rects.size(); ++i) {
    groups[uf.find(static_cast<int>(i))].push_back(i);
  }
  result.components.clear();
  for (const auto& entry : groups) {
    bool group_anchored = false;
    int64_t area = 0;
    FillComponent component;
    component.id = zone.id + ":" + std::to_string(entry.first);
    component.net = zone.net;
    component.layer = zone.layer;
    for (size_t index_value : entry.second) {
      const FillRect& rect = all_rects[index_value];
      area += rect.area();
      group_anchored = group_anchored || anchored[index_value];
      if (rect.spoke) {
        component.spokes.push_back(rect);
      } else {
        component.rects.push_back(rect);
      }
    }
    component.anchored = group_anchored;
    component.area_nm2 = area;
    if (!group_anchored && zone.remove_islands) {
      ++result.islands_removed;
      result.removed_area_nm2 += area;
      continue;
    }
    result.kept_area_nm2 += area;
    result.components.push_back(std::move(component));
  }
  return result;
}

}  // namespace mota
