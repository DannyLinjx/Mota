#include "mota/worker.hpp"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <map>
#include <string>
#include <vector>

#include "mota/board.hpp"
#include "mota/connectivity.hpp"
#include "mota/highspeed.hpp"
#include "mota/highspeed_json.hpp"
#include "mota/placement.hpp"
#include "mota/routing.hpp"
#include "mota/sha256.hpp"
#include "mota/shapes.hpp"
#include "mota/types.hpp"
#include "mota/verify.hpp"
#include "mota/zones.hpp"

namespace mota {
namespace {

using Clock = std::chrono::steady_clock;

[[noreturn]] void invalid(const std::string& message) {
  throw KernelError(ErrorCode::InvalidInput, message);
}

int64_t int_field(const Json& object, const std::string& key,
                  int64_t fallback, bool required) {
  if (!object.has(key)) {
    if (required) invalid("missing field " + key);
    return fallback;
  }
  const Json& value = object.at(key);
  if (!value.is_int()) invalid("field " + key + " must be an integer");
  return value.as_int();
}

double number_field(const Json& object, const std::string& key,
                    double fallback) {
  if (!object.has(key)) return fallback;
  return object.at(key).as_double();
}

std::string string_field(const Json& object, const std::string& key,
                         const std::string& fallback, bool required) {
  if (!object.has(key)) {
    if (required) invalid("missing field " + key);
    return fallback;
  }
  const Json& value = object.at(key);
  if (!value.is_string()) invalid("field " + key + " must be a string");
  return value.as_string();
}

bool bool_field(const Json& object, const std::string& key, bool fallback) {
  if (!object.has(key)) return fallback;
  return object.at(key).as_bool();
}

Vec2 parse_point(const Json& value) {
  if (value.is_array()) {
    if (value.items().size() != 2) invalid("point array must have two values");
    return Vec2{value.items()[0].as_int(), value.items()[1].as_int()};
  }
  if (!value.is_object()) invalid("point must be an object or array");
  return Vec2{int_field(value, "x", 0, true), int_field(value, "y", 0, true)};
}

Json point_json(const Vec2& point) {
  Json out = Json::object();
  out["x"] = Json(point.x);
  out["y"] = Json(point.y);
  return out;
}

Shape parse_shape(const Json& value) {
  if (!value.is_object()) invalid("shape must be an object");
  const std::string kind = string_field(value, "kind", "", true);
  if (kind == "point") {
    return make_point(parse_point(value.has("p") ? value.at("p") : value));
  }
  if (kind == "segment") {
    return make_segment(parse_point(value.at("a")), parse_point(value.at("b")));
  }
  if (kind == "circle") {
    return make_circle(parse_point(value.at("center")),
                       int_field(value, "radius", 0, true));
  }
  if (kind == "round_rect") {
    return make_round_rect(parse_point(value.at("center")),
                           int_field(value, "half_w", 0, true),
                           int_field(value, "half_h", 0, true),
                           int_field(value, "corner_radius", 0, true));
  }
  if (kind == "slot") {
    return make_slot(parse_point(value.at("a")), parse_point(value.at("b")),
                     int_field(value, "radius", 0, true));
  }
  if (kind == "polygon") {
    if (!value.has("points") || !value.at("points").is_array()) {
      invalid("polygon needs a points array");
    }
    std::vector<Vec2> points;
    for (const Json& item : value.at("points").items()) {
      points.push_back(parse_point(item));
    }
    return make_polygon(std::move(points));
  }
  if (kind == "arc") {
    return make_arc(parse_point(value.at("center")),
                    int_field(value, "radius", 0, true),
                    static_cast<int32_t>(int_field(value, "start_urad", 0, true)),
                    static_cast<int32_t>(int_field(value, "sweep_urad", 0, true)),
                    int_field(value, "width_nm", 0, false));
  }
  invalid("unknown shape kind " + kind);
}

Json shape_json(const Shape& shape) {
  Json out = Json::object();
  out["kind"] = Json(shape_kind_name(shape.kind));
  switch (shape.kind) {
    case ShapeKind::Point:
      out["p"] = point_json(shape.a);
      break;
    case ShapeKind::Segment:
      out["a"] = point_json(shape.a);
      out["b"] = point_json(shape.b);
      break;
    case ShapeKind::Circle:
      out["center"] = point_json(shape.a);
      out["radius"] = Json(shape.radius);
      break;
    case ShapeKind::RoundRect:
      out["center"] = point_json(shape.a);
      out["half_w"] = Json(shape.half_w);
      out["half_h"] = Json(shape.half_h);
      out["corner_radius"] = Json(shape.corner_radius);
      break;
    case ShapeKind::Slot:
      out["a"] = point_json(shape.a);
      out["b"] = point_json(shape.b);
      out["radius"] = Json(shape.radius);
      break;
    case ShapeKind::Polygon: {
      Json points = Json::array();
      for (const Vec2& point : shape.points) points.push_back(point_json(point));
      out["points"] = points;
      break;
    }
    case ShapeKind::Arc:
      out["center"] = point_json(shape.a);
      out["radius"] = Json(shape.radius);
      out["start_urad"] = Json(static_cast<int64_t>(shape.start_urad));
      out["sweep_urad"] = Json(static_cast<int64_t>(shape.sweep_urad));
      out["width_nm"] = Json(shape.width_nm);
      break;
  }
  return out;
}

Json bounds_json(const Aabb& bounds, int64_t error_nm) {
  Json out = Json::object();
  out["min_x"] = Json(bounds.min_x);
  out["min_y"] = Json(bounds.min_y);
  out["max_x"] = Json(bounds.max_x);
  out["max_y"] = Json(bounds.max_y);
  out["error_nm"] = Json(error_nm);
  return out;
}

Aabb parse_aabb(const Json& value) {
  if (!value.is_object()) invalid("area must be an object");
  return Aabb{int_field(value, "min_x", 0, true),
              int_field(value, "min_y", 0, true),
              int_field(value, "max_x", 0, true),
              int_field(value, "max_y", 0, true)};
}

Transform parse_transform(const Json& value) {
  if (!value.is_object()) invalid("transform must be an object");
  Transform transform;
  if (value.has("origin")) transform.origin = parse_point(value.at("origin"));
  transform.rotation_deg =
      static_cast<int32_t>(int_field(value, "rotation_deg", 0, false));
  transform.bottom = bool_field(value, "bottom", false);
  return transform;
}

std::vector<Vec2> parse_polygon_points(const Json& value,
                                       const std::string& what) {
  if (!value.is_array() || value.items().size() < 3) {
    invalid(what + " must be an array of at least three points");
  }
  std::vector<Vec2> points;
  points.reserve(value.items().size());
  for (const Json& item : value.items()) points.push_back(parse_point(item));
  return points;
}

int layer_index_of(const Stackup& stackup, const std::string& layer,
                   const std::string& what) {
  const int index = stackup.index_of(layer);
  if (index < 0) invalid(what + " references unknown layer " + layer);
  return index;
}

std::vector<DrcRule> parse_rules(const Json& value) {
  if (!value.is_array()) invalid("rules must be an array");
  std::vector<DrcRule> rules;
  for (const Json& item : value.items()) {
    DrcRule rule;
    rule.id = string_field(item, "id", "", true);
    rule.kind = string_field(item, "kind", "", true);
    rule.required = bool_field(item, "required", true);
    rule.value_nm = int_field(item, "value_nm", 0, false);
    rule.net_class = string_field(item, "net_class", "", false);
    rule.layer = static_cast<int>(int_field(item, "layer", -1, false));
    if (rule.value_nm < 0) invalid("rule " + rule.id + " has a negative value");
    rules.push_back(std::move(rule));
  }
  return rules;
}

PlacementInput parse_placement(const Json& value, std::vector<Vec2> outline,
                               int64_t width_nm, int64_t height_nm) {
  if (!value.is_object()) invalid("placement must be an object");
  PlacementInput out;
  out.outline = std::move(outline);
  out.board_width_nm = static_cast<double>(width_nm);
  out.board_height_nm = static_cast<double>(height_nm);
  if (value.has("rules")) {
    const Json& raw = value.at("rules");
    out.rules.grid_nm = int_field(raw, "grid_nm", out.rules.grid_nm, false);
    out.rules.edge_clearance_nm =
        int_field(raw, "edge_clearance_nm", out.rules.edge_clearance_nm, false);
    out.rules.courtyard_gap_nm =
        int_field(raw, "courtyard_gap_nm", out.rules.courtyard_gap_nm, false);
    out.rules.decoupling_max_nm =
        int_field(raw, "decoupling_max_nm", out.rules.decoupling_max_nm, false);
    out.rules.congestion_cell_nm =
        int_field(raw, "congestion_cell_nm", out.rules.congestion_cell_nm, false);
    out.rules.wirelength_weight =
        number_field(raw, "wirelength_weight", out.rules.wirelength_weight);
    out.rules.congestion_weight =
        number_field(raw, "congestion_weight", out.rules.congestion_weight);
    out.rules.escape_weight =
        number_field(raw, "escape_weight", out.rules.escape_weight);
    out.rules.module_weight =
        number_field(raw, "module_weight", out.rules.module_weight);
    out.rules.move_weight =
        number_field(raw, "move_weight", out.rules.move_weight);
    out.rules.local_rounds =
        static_cast<int>(int_field(raw, "local_rounds", out.rules.local_rounds, false));
    out.rules.seed = int_field(raw, "seed", out.rules.seed, false);
    out.rules.max_search_nodes =
        int_field(raw, "max_search_nodes", out.rules.max_search_nodes, false);
    out.rules.max_candidates_per_component =
        static_cast<int>(int_field(raw, "max_candidates_per_component",
                                   out.rules.max_candidates_per_component, false));
  }
  out.rules.seed = int_field(value, "seed", out.rules.seed, false);
  if (value.has("components")) {
    if (!value.at("components").is_array()) invalid("placement.components must be an array");
    for (const Json& item : value.at("components").items()) {
      PlacementComponent component;
      component.id = string_field(item, "id", "", true);
      component.reference = string_field(item, "reference", component.id, false);
      component.module = string_field(item, "module", "", false);
      component.x_nm = int_field(item, "x_nm", 0, false);
      component.y_nm = int_field(item, "y_nm", 0, false);
      component.w_nm = int_field(item, "w_nm", 0, true);
      component.h_nm = int_field(item, "h_nm", 0, true);
      component.rotation_deg =
          static_cast<int>(int_field(item, "rotation_deg", 0, false));
      component.fixed = bool_field(item, "fixed", false);
      component.critical = bool_field(item, "critical", false);
      component.pins = static_cast<int>(int_field(item, "pins", 1, false));
      component.pin_pitch_nm = int_field(item, "pin_pitch_nm", 0, false);
      if (component.w_nm <= 0 || component.h_nm <= 0) {
        invalid("component " + component.id + " has no courtyard size");
      }
      if (item.has("decoupling_for")) {
        for (const Json& target : item.at("decoupling_for").items()) {
          component.decoupling_for.push_back(target.as_string());
        }
      }
      out.components.push_back(std::move(component));
    }
  }
  if (value.has("nets")) {
    for (const Json& item : value.at("nets").items()) {
      PlacementNet net;
      net.net = string_field(item, "net", "", true);
      for (const Json& id : item.at("components").items()) {
        net.components.push_back(id.as_string());
      }
      out.nets.push_back(std::move(net));
    }
  }
  if (value.has("keepouts")) {
    for (const Json& item : value.at("keepouts").items()) {
      PlacementKeepout keepout;
      keepout.id = string_field(item, "id", "", true);
      keepout.area = Aabb{int_field(item, "x0", 0, true),
                          int_field(item, "y0", 0, true),
                          int_field(item, "x1", 0, true),
                          int_field(item, "y1", 0, true)};
      if (keepout.area.min_x >= keepout.area.max_x ||
          keepout.area.min_y >= keepout.area.max_y) {
        invalid("keepout " + keepout.id + " has an empty area");
      }
      out.keepouts.push_back(std::move(keepout));
    }
  }
  if (value.has("module_regions")) {
    for (const Json& item : value.at("module_regions").items()) {
      ModuleRegion region;
      region.module = string_field(item, "module", "", true);
      region.area = Aabb{int_field(item, "x0", 0, true),
                         int_field(item, "y0", 0, true),
                         int_field(item, "x1", 0, true),
                         int_field(item, "y1", 0, true)};
      out.module_regions.push_back(std::move(region));
    }
  }
  return out;
}

class Checkpoint {
 public:
  Checkpoint(double wall_seconds, std::string cancel_file)
      : cancel_file_(std::move(cancel_file)) {
    if (wall_seconds < 0.0) invalid("wall_seconds must be non-negative");
    deadline_ = Clock::now() + std::chrono::duration_cast<Clock::duration>(
                                   std::chrono::duration<double>(wall_seconds));
  }

  bool cancel_requested() const {
    if (cancel_file_.empty()) return false;
    std::error_code error;
    return std::filesystem::exists(cancel_file_, error) && !error;
  }

  void check() const {
    if (cancel_requested()) {
      throw KernelError(ErrorCode::Cancelled, "cancel file is present");
    }
    if (Clock::now() > deadline_) {
      throw KernelError(ErrorCode::BudgetExhausted, "wall time budget exhausted");
    }
  }

 private:
  std::string cancel_file_;
  Clock::time_point deadline_;
};

struct BoardData {
  Stackup stackup;
  BoardIndex index;
  std::map<std::string, PadStack> pads;
  std::map<std::string, ViaDefinition> vias;
  std::vector<TrackSpec> tracks;
  std::vector<ZoneSpec> zones;
  std::vector<HoleSpec> holes;
  std::vector<KeepoutSpec> keepouts;
  std::vector<NetTieSpec> net_ties;
  std::vector<std::string> required_endpoints;
  std::vector<NetRequirement> net_requirements;
  std::map<std::string, std::string> net_classes;
  std::vector<DrcRule> rules;
  std::vector<ZoneFillResult> fills;
  PlacementInput placement;
  bool has_placement = false;
  RoutingInput routing;
  bool has_routing = false;
  HighspeedInput highspeed;
  bool has_highspeed = false;
  std::vector<Vec2> outline;
  int64_t width_nm = 0;
  int64_t height_nm = 0;
  int64_t thickness_nm = 0;
};

RoutingInput parse_routing(const Json& value, const BoardData& board,
                           int64_t wall_budget_ms) {
  if (!value.is_object()) invalid("routing must be an object");
  RoutingInput out;
  out.outline = board.outline;
  out.width_nm = board.width_nm;
  out.height_nm = board.height_nm;
  for (size_t index = 0; index < board.stackup.layers().size(); ++index) {
    const LayerSpec& layer = board.stackup.layers()[index];
    if (layer.kind != "copper") continue;
    out.copper_layers.push_back(static_cast<int>(index));
    out.copper_layer_ids.push_back(layer.id);
  }
  for (const auto& entry : board.pads) {
    const PadStack& pad = entry.second;
    RoutingPad routed;
    routed.id = pad.id;
    routed.net = pad.net;
    routed.component_id = pad.component_id;
    routed.pin = pad.pin;
    routed.plated = pad.plated;
    for (const auto& copper : pad.copper_by_layer) {
      const int stackup_layer = board.stackup.index_of(copper.first);
      if (stackup_layer < 0) continue;
      routed.layer_shapes.emplace_back(stackup_layer, copper.second);
    }
    out.pads.push_back(std::move(routed));
  }
  for (const TrackSpec& track : board.tracks) {
    RoutingTrack routed;
    routed.id = track.id;
    routed.net = track.net;
    routed.stackup_layer = track.layer;
    routed.width_nm = track.width_nm;
    routed.points = track.points;
    routed.locked = track.locked;
    out.tracks.push_back(std::move(routed));
  }
  for (const auto& entry : board.vias) {
    const ViaDefinition& via = entry.second;
    const Aabb bounds = shape_bounds(via.pad).bounds;
    RoutingVia routed;
    routed.id = via.id;
    routed.net = via.net;
    routed.center = {bounds.center_x(), bounds.center_y()};
    routed.drill_nm = via.drill_nm;
    routed.pad_outer_nm = std::max(bounds.width(), via.drill_nm);
    routed.start_layer = via.start_index;
    routed.end_layer = via.end_index;
    routed.plated = via.plated;
    routed.locked = via.locked;
    out.vias.push_back(std::move(routed));
  }
  for (const HoleSpec& hole : board.holes) {
    out.holes.push_back(RoutingHole{hole.id, hole.shape});
  }
  for (const KeepoutSpec& keepout : board.keepouts) {
    out.keepouts.push_back(
        RoutingKeepout{keepout.id, keepout.layer, keepout.outline});
  }
  if (value.has("nets")) {
    for (const Json& item : value.at("nets").items()) {
      RoutingNet net;
      net.net = string_field(item, "net", "", true);
      net.locked = bool_field(item, "locked", false);
      for (const Json& terminal : item.at("terminals").items()) {
        net.terminals.push_back(terminal.as_string());
      }
      out.nets.push_back(std::move(net));
    }
  } else {
    std::map<std::string, std::vector<std::string>> by_net;
    for (const RoutingPad& pad : out.pads) {
      if (!pad.net.empty()) by_net[pad.net].push_back(pad.id);
    }
    for (const auto& entry : by_net) {
      out.nets.push_back(RoutingNet{entry.first, entry.second, false});
    }
  }
  if (value.has("rules")) {
    const Json& rules = value.at("rules");
    out.rules.grid_nm = int_field(rules, "grid_nm", out.rules.grid_nm, false);
    out.rules.track_width_nm =
        int_field(rules, "track_width_nm", out.rules.track_width_nm, false);
    out.rules.clearance_nm =
        int_field(rules, "clearance_nm", out.rules.clearance_nm, false);
    out.rules.edge_clearance_nm =
        int_field(rules, "edge_clearance_nm", out.rules.edge_clearance_nm, false);
    out.rules.via_drill_nm =
        int_field(rules, "via_drill_nm", out.rules.via_drill_nm, false);
    out.rules.via_pad_nm =
        int_field(rules, "via_pad_nm", out.rules.via_pad_nm, false);
    out.rules.via_cost_factor =
        number_field(rules, "via_cost_factor", out.rules.via_cost_factor);
    out.rules.corridor_penalty =
        number_field(rules, "corridor_penalty", out.rules.corridor_penalty);
    out.rules.max_rounds =
        static_cast<int>(int_field(rules, "max_rounds", out.rules.max_rounds, false));
    out.rules.max_expansions =
        int_field(rules, "max_expansions", out.rules.max_expansions, false);
    out.rules.max_grid_cells =
        int_field(rules, "max_grid_cells", out.rules.max_grid_cells, false);
    out.rules.max_millis =
        int_field(rules, "max_millis", wall_budget_ms, false);
    out.rules.seed = int_field(rules, "seed", out.rules.seed, false);
  } else {
    out.rules.max_millis = wall_budget_ms;
  }
  out.rules.seed = int_field(value, "seed", out.rules.seed, false);
  out.rules.max_millis = std::max<int64_t>(1, out.rules.max_millis);
  return out;
}

BoardData parse_board(const Json& board, const Checkpoint& checkpoint) {
  if (!board.is_object()) invalid("board must be an object");
  BoardData data;
  data.width_nm = int_field(board, "width_nm", 0, false);
  data.height_nm = int_field(board, "height_nm", 0, false);
  data.thickness_nm = int_field(board, "thickness_nm", 0, false);
  if (data.width_nm < 0 || data.height_nm < 0 || data.thickness_nm < 0) {
    invalid("board extents must be non-negative");
  }
  if (!board.has("stackup")) invalid("board.stackup is required");
  const Json& raw_stackup = board.at("stackup");
  const Json& raw_layers =
      raw_stackup.is_object() ? raw_stackup.at("layers") : raw_stackup;
  if (!raw_layers.is_array() || raw_layers.items().empty()) {
    invalid("stackup.layers must be a non-empty array");
  }
  std::vector<LayerSpec> layers;
  for (const Json& item : raw_layers.items()) {
    LayerSpec layer;
    layer.id = string_field(item, "id", "", true);
    layer.order = static_cast<int>(int_field(item, "order", 0, true));
    layer.kind = string_field(item, "kind", "", true);
    layer.thickness_nm = int_field(item, "thickness_nm", 0, true);
    layer.material_ref = string_field(item, "material_ref", "", false);
    layers.push_back(std::move(layer));
  }
  data.stackup = Stackup(std::move(layers));

  if (board.has("objects")) {
    if (!board.at("objects").is_array()) invalid("board.objects must be an array");
    for (const Json& item : board.at("objects").items()) {
      checkpoint.check();
      const std::string id = string_field(item, "id", "", true);
      const std::string layer = string_field(item, "layer", "", true);
      data.index.add(layer, id, parse_shape(item.at("shape")));
      if (data.index.object_count() > kMaxGeometryObjects) {
        throw KernelError(ErrorCode::InputTooLarge,
                          "geometry object limit exceeded");
      }
    }
  }

  if (board.has("pads")) {
    if (!board.at("pads").is_array()) invalid("board.pads must be an array");
    for (const Json& item : board.at("pads").items()) {
      PadStack pad;
      pad.id = string_field(item, "id", "", true);
      pad.net = string_field(item, "net", "", false);
      pad.component_id = string_field(item, "component_id", "", false);
      pad.pin = string_field(item, "pin", "", false);
      pad.plated = bool_field(item, "plated", true);
      if (item.has("copper_by_layer")) {
        for (const auto& entry : item.at("copper_by_layer").fields()) {
          pad.copper_by_layer.emplace(entry.first, parse_shape(entry.second));
        }
      }
      if (item.has("drill") && !item.at("drill").is_null()) {
        pad.drill = parse_shape(item.at("drill"));
      }
      pad.validate(data.stackup);
      data.pads.emplace(pad.id, std::move(pad));
    }
  }

  if (board.has("vias")) {
    if (!board.at("vias").is_array()) invalid("board.vias must be an array");
    for (const Json& item : board.at("vias").items()) {
      ViaDefinition via;
      via.id = string_field(item, "id", "", true);
      via.net = string_field(item, "net", "", false);
      via.kind = parse_via_kind(string_field(item, "kind", "through", false));
      via.start_layer = string_field(item, "start_layer", "", true);
      via.end_layer = string_field(item, "end_layer", "", true);
      via.drill_nm = int_field(item, "drill_nm", 0, true);
      via.pad = parse_shape(item.at("pad"));
      via.plated = bool_field(item, "plated", true);
      via.locked = bool_field(item, "locked", false);
      via.resolve(data.stackup);
      data.vias.emplace(via.id, std::move(via));
    }
  }

  if (board.has("tracks")) {
    if (!board.at("tracks").is_array()) invalid("board.tracks must be an array");
    for (const Json& item : board.at("tracks").items()) {
      TrackSpec track;
      track.id = string_field(item, "id", "", true);
      track.net = string_field(item, "net", "", false);
      const std::string layer = string_field(item, "layer", "", true);
      track.layer = layer_index_of(data.stackup, layer, "track " + track.id);
      if (!item.has("points") || !item.at("points").is_array() ||
          item.at("points").items().size() < 2) {
        invalid("track " + track.id + " needs at least two points");
      }
      for (const Json& point : item.at("points").items()) {
        track.points.push_back(parse_point(point));
      }
      track.width_nm = int_field(item, "width_nm", 0, true);
      if (track.width_nm <= 0) invalid("track " + track.id + " has no width");
      track.locked = bool_field(item, "locked", false);
      data.tracks.push_back(std::move(track));
    }
  }

  if (board.has("outline")) {
    data.outline = parse_polygon_points(board.at("outline"), "board outline");
  } else if (data.width_nm > 0 && data.height_nm > 0) {
    data.outline = {{0, 0}, {data.width_nm, 0},
                    {data.width_nm, data.height_nm},
                    {0, data.height_nm}};
  }

  if (board.has("zones")) {
    if (!board.at("zones").is_array()) invalid("board.zones must be an array");
    for (const Json& item : board.at("zones").items()) {
      ZoneSpec zone;
      zone.id = string_field(item, "id", "", true);
      zone.net = string_field(item, "net", "", true);
      zone.layer = string_field(item, "layer", "", true);
      layer_index_of(data.stackup, zone.layer, "zone " + zone.id);
      zone.priority = static_cast<int>(int_field(item, "priority", 0, false));
      zone.outline = parse_polygon_points(item.at("outline"), "zone outline");
      zone.clearance_nm = int_field(item, "clearance_nm", 200'000, false);
      zone.thermal_gap_nm = int_field(item, "thermal_gap_nm", 250'000, false);
      zone.thermal_spoke_width_nm =
          int_field(item, "thermal_spoke_width_nm", 250'000, false);
      zone.thermal_spokes =
          static_cast<int>(int_field(item, "thermal_spokes", 4, false));
      zone.remove_islands = bool_field(item, "remove_islands", true);
      data.zones.push_back(std::move(zone));
    }
  }

  if (board.has("holes")) {
    if (!board.at("holes").is_array()) invalid("board.holes must be an array");
    for (const Json& item : board.at("holes").items()) {
      HoleSpec hole;
      hole.id = string_field(item, "id", "", true);
      hole.plated = bool_field(item, "plated", false);
      if (item.has("shape")) {
        hole.shape = parse_shape(item.at("shape"));
      } else {
        const Vec2 center{int_field(item, "x", 0, true),
                          int_field(item, "y", 0, true)};
        hole.shape = make_circle(center, int_field(item, "drill_nm", 0, true) / 2);
      }
      data.holes.push_back(std::move(hole));
    }
  }

  if (board.has("keepouts")) {
    if (!board.at("keepouts").is_array()) invalid("board.keepouts must be an array");
    for (const Json& item : board.at("keepouts").items()) {
      KeepoutSpec keepout;
      keepout.id = string_field(item, "id", "", true);
      const std::string layer = string_field(item, "layer", "", true);
      keepout.layer = layer_index_of(data.stackup, layer,
                                     "keepout " + keepout.id);
      keepout.outline = parse_polygon_points(item.at("outline"), "keepout outline");
      data.keepouts.push_back(std::move(keepout));
    }
  }

  if (board.has("net_ties")) {
    for (const Json& item : board.at("net_ties").items()) {
      NetTieSpec tie;
      tie.id = string_field(item, "id", "", true);
      tie.a = string_field(item, "a", "", true);
      tie.b = string_field(item, "b", "", true);
      tie.allowed = bool_field(item, "allowed", true);
      data.net_ties.push_back(std::move(tie));
    }
  }

  if (board.has("required_endpoints")) {
    for (const Json& item : board.at("required_endpoints").items()) {
      data.required_endpoints.push_back(item.as_string());
    }
  }

  if (board.has("net_requirements")) {
    const Json& requirements = board.at("net_requirements");
    if (requirements.is_array()) {
      for (const Json& item : requirements.items()) {
        NetRequirement requirement;
        requirement.net = string_field(item, "net", "", true);
        requirement.required_groups =
            static_cast<int>(int_field(item, "required_groups", 1, false));
        data.net_requirements.push_back(std::move(requirement));
      }
    } else {
      for (const auto& entry : requirements.fields()) {
        data.net_requirements.push_back(NetRequirement{
            entry.first,
            static_cast<int>(int_field(entry.second, "required_groups", 1, false))});
      }
    }
  }

  if (board.has("net_classes")) {
    for (const auto& entry : board.at("net_classes").fields()) {
      data.net_classes[entry.first] = entry.second.as_string();
    }
  }

  if (board.has("rules")) {
    data.rules = parse_rules(board.at("rules"));
  }

  data.index.build(data.stackup);
  return data;
}

Json query_result_ok(const std::string& id) {
  Json out = Json::object();
  out["id"] = Json(id);
  out["status"] = Json("OK");
  return out;
}

Json query_failure(const std::string& id, ErrorCode code,
                   const std::string& message) {
  Json out = Json::object();
  out["id"] = Json(id);
  out["status"] = Json("FAILED");
  Json error = Json::object();
  error["code"] = Json(error_code_name(code));
  error["message"] = Json(message);
  out["error"] = error;
  return out;
}

struct CommittedFill {
  int layer = -1;
  std::string net;
  FillRect rect;
};

Json rect_json(const FillRect& rect) {
  Json out = Json::object();
  out["x0"] = Json(rect.x0);
  out["y0"] = Json(rect.y0);
  out["x1"] = Json(rect.x1);
  out["y1"] = Json(rect.y1);
  out["area_nm2"] = Json(rect.area());
  return out;
}

Json rects_json(const std::vector<FillRect>& rects) {
  Json out = Json::array();
  for (const FillRect& rect : rects) out.push_back(rect_json(rect));
  return out;
}

Json fill_json(const std::vector<ZoneFillResult>& fills) {
  Json zones = Json::array();
  for (const ZoneFillResult& fill : fills) {
    Json zone = Json::object();
    zone["zone_id"] = Json(fill.zone_id);
    zone["net"] = Json(fill.net);
    zone["layer"] = Json(fill.layer);
    zone["priority"] = Json(static_cast<int64_t>(fill.priority));
    zone["non_rectilinear"] = Json(fill.non_rectilinear);
    zone["diagnostic"] = Json(fill.diagnostic);
    zone["islands_removed"] = Json(static_cast<int64_t>(fill.islands_removed));
    zone["removed_area_nm2"] = Json(fill.removed_area_nm2);
    zone["kept_area_nm2"] = Json(fill.kept_area_nm2);
    Json components = Json::array();
    for (const FillComponent& component : fill.components) {
      Json item = Json::object();
      item["id"] = Json(component.id);
      item["anchored"] = Json(component.anchored);
      item["area_nm2"] = Json(component.area_nm2);
      item["rects"] = rects_json(component.rects);
      item["spokes"] = rects_json(component.spokes);
      components.push_back(item);
    }
    zone["components"] = components;
    zones.push_back(zone);
  }
  Json out = Json::object();
  out["zones"] = zones;
  return out;
}

Json connectivity_json(const ConnectivityReport& report) {
  Json out = Json::object();
  out["u_total"] = Json(static_cast<int64_t>(report.u_total));
  out["short_count"] = Json(static_cast<int64_t>(report.short_count));
  out["open_count"] = Json(static_cast<int64_t>(report.open_count));
  out["missing_endpoint_count"] =
      Json(static_cast<int64_t>(report.missing_endpoint_count));
  Json nets = Json::array();
  for (const NetConnectivityReport& net : report.nets) {
    Json item = Json::object();
    item["net"] = Json(net.net);
    item["pads"] = Json(static_cast<int64_t>(net.pads));
    item["groups"] = Json(static_cast<int64_t>(net.groups));
    item["required_groups"] = Json(static_cast<int64_t>(net.required_groups));
    item["u"] = Json(static_cast<int64_t>(net.u));
    item["connected"] = Json(net.connected);
    Json unconnected = Json::array();
    for (const std::string& endpoint : net.unconnected_endpoints) {
      unconnected.push_back(Json(endpoint));
    }
    item["unconnected_endpoints"] = unconnected;
    nets.push_back(item);
  }
  out["nets"] = nets;
  Json issues = Json::array();
  for (const ConnectivityIssue& issue : report.issues) {
    Json item = Json::object();
    item["code"] = Json(issue.code);
    item["severity"] = Json(issue.severity);
    item["message"] = Json(issue.message);
    item["net"] = Json(issue.net);
    item["count"] = Json(issue.count);
    item["value_nm"] = Json(issue.value_nm);
    Json refs = Json::array();
    for (const std::string& ref : issue.object_refs) refs.push_back(Json(ref));
    item["object_refs"] = refs;
    issues.push_back(item);
  }
  out["issues"] = issues;
  Json ties = Json::array();
  for (const std::string& tie : report.net_ties_used) ties.push_back(Json(tie));
  out["net_ties_used"] = ties;
  return out;
}

Json drc_json(const DrcReport& report) {
  Json out = Json::object();
  out["status"] = Json(report.status);
  out["review_ready"] = Json(report.review_ready);
  out["required_checks"] = Json(static_cast<int64_t>(report.required_checks));
  out["passed_checks"] = Json(static_cast<int64_t>(report.passed_checks));
  out["failed_checks"] = Json(static_cast<int64_t>(report.failed_checks));
  out["not_checked_checks"] =
      Json(static_cast<int64_t>(report.not_checked_checks));
  out["violation_count"] = Json(static_cast<int64_t>(report.violation_count));
  Json diagnostics = Json::array();
  for (const std::string& code : report.diagnostics) diagnostics.push_back(Json(code));
  out["diagnostics"] = diagnostics;
  Json checks = Json::array();
  for (const DrcCheck& check : report.checks) {
    Json item = Json::object();
    item["id"] = Json(check.id);
    item["kind"] = Json(check.kind);
    item["required"] = Json(check.required);
    item["status"] = Json(check.status);
    item["checked_objects"] = Json(check.checked_objects);
    item["message"] = Json(check.message);
    item["coverage"] = Json(check.coverage);
    Json violations = Json::array();
    for (const DrcViolation& violation : check.violations) {
      Json entry = Json::object();
      entry["code"] = Json(violation.code);
      entry["message"] = Json(violation.message);
      entry["net"] = Json(violation.net);
      entry["actual_nm"] = Json(violation.actual_nm);
      entry["required_nm"] = Json(violation.required_nm);
      Json refs = Json::array();
      for (const std::string& ref : violation.object_refs) refs.push_back(Json(ref));
      entry["object_refs"] = refs;
      violations.push_back(entry);
    }
    item["violations"] = violations;
    checks.push_back(item);
  }
  out["checks"] = checks;
  return out;
}

Json run_verify(BoardData& board, const Json& query) {
  const std::string id = string_field(query, "id", "verify", true);
  Json out = query_result_ok(id);
  const auto started = Clock::now();
  auto elapsed_ms = [&](const Clock::time_point& point) {
    return std::chrono::duration<double, std::milli>(Clock::now() - point).count();
  };

  board.fills.clear();
  if (board.outline.size() < 3) {
    out["fill"] = fill_json({});
    out["connectivity"] = connectivity_json({});
    out["drc"] = drc_json(DrcReport{"NOT_CHECKED", false, 0, 0, 0, 0, 0,
                                    {}, {"BOARD_OUTLINE_MISSING"}});
    out["report"] = Json::object();
    out["report"]["status"] = Json("NOT_CHECKED");
    out["report"]["review_ready"] = Json(false);
    out["report"]["diagnostic"] = Json("BOARD_OUTLINE_MISSING");
    return out;
  }

  std::vector<ZoneSpec> zones = board.zones;
  std::sort(zones.begin(), zones.end(), [](const ZoneSpec& a, const ZoneSpec& b) {
    if (a.priority != b.priority) return a.priority > b.priority;
    return a.id < b.id;
  });
  std::vector<CommittedFill> committed;
  const Clock::time_point fill_start = Clock::now();
  for (const ZoneSpec& zone : zones) {
    const int layer_index = board.stackup.index_of(zone.layer);
    std::vector<ZoneObstacle> obstacles;
    std::vector<ZoneAnchor> anchors;
    for (const TrackSpec& track : board.tracks) {
      if (track.layer != layer_index) continue;
      for (size_t i = 0; i + 1 < track.points.size(); ++i) {
        const Shape shape = make_slot(track.points[i], track.points[i + 1],
                                      track.width_nm / 2);
        if (track.net == zone.net) {
          anchors.push_back(ZoneAnchor{"track:" + track.id, shape, false});
        } else {
          obstacles.push_back(ZoneObstacle{"track:" + track.id, shape,
                                           track.net, true});
        }
      }
    }
    for (const auto& entry : board.pads) {
      const PadStack& pad = entry.second;
      const Shape* copper = pad.copper_on(zone.layer);
      if (copper == nullptr) continue;
      if (pad.net == zone.net && pad.plated) {
        anchors.push_back(ZoneAnchor{"pad:" + pad.id, *copper, true});
      } else {
        obstacles.push_back(ZoneObstacle{"pad:" + pad.id, *copper, pad.net, true});
      }
    }
    for (const auto& entry : board.vias) {
      const ViaDefinition& via = entry.second;
      if (!via.spans(layer_index)) continue;
      if (via.net == zone.net && via.plated) {
        anchors.push_back(ZoneAnchor{"via:" + via.id, via.pad, false});
      } else {
        obstacles.push_back(ZoneObstacle{"via:" + via.id, via.pad, via.net, true});
      }
    }
    for (const HoleSpec& hole : board.holes) {
      obstacles.push_back(ZoneObstacle{"hole:" + hole.id, hole.shape, "", false});
    }
    for (const KeepoutSpec& keepout : board.keepouts) {
      if (keepout.layer != layer_index) continue;
      obstacles.push_back(ZoneObstacle{
          "keepout:" + keepout.id, make_polygon(keepout.outline), "", false});
    }
    std::vector<FillRect> higher;
    for (const CommittedFill& fill : committed) {
      if (fill.layer != layer_index) continue;
      higher.push_back(fill.rect);
      if (fill.net != zone.net) {
        const Shape shape = make_polygon({{fill.rect.x0, fill.rect.y0},
                                          {fill.rect.x1, fill.rect.y0},
                                          {fill.rect.x1, fill.rect.y1},
                                          {fill.rect.x0, fill.rect.y1}});
        obstacles.push_back(ZoneObstacle{"higher:" + zone.id, shape,
                                         fill.net, true});
      }
    }
    ZoneFillResult result =
        fill_zone(zone, board.outline, obstacles, anchors, higher);
    for (const FillComponent& component : result.components) {
      for (const FillRect& rect : component.rects) {
        committed.push_back(CommittedFill{layer_index, zone.net, rect});
      }
      for (const FillRect& rect : component.spokes) {
        committed.push_back(CommittedFill{layer_index, zone.net, rect});
      }
    }
    board.fills.push_back(std::move(result));
  }
  const double fill_ms = elapsed_ms(fill_start);

  std::vector<CopperNode> nodes;
  for (const auto& entry : board.pads) {
    const PadStack& pad = entry.second;
    if (!pad.plated) continue;
    CopperNode node;
    node.id = "pad:" + pad.id;
    node.net = pad.net;
    node.kind = "pad";
    node.component_id = pad.component_id;
    node.pin = pad.pin;
    node.plated = pad.plated;
    for (const auto& copper : pad.copper_by_layer) {
      const int layer_index = board.stackup.index_of(copper.first);
      if (layer_index < 0) continue;
      node.layer_shapes.emplace_back(layer_index, copper.second);
    }
    if (!node.layer_shapes.empty()) nodes.push_back(std::move(node));
  }
  for (const TrackSpec& track : board.tracks) {
    CopperNode node;
    node.id = "track:" + track.id;
    node.net = track.net;
    node.kind = "track";
    node.width_nm = track.width_nm;
    for (size_t i = 0; i + 1 < track.points.size(); ++i) {
      node.layer_shapes.emplace_back(
          track.layer, make_slot(track.points[i], track.points[i + 1],
                                 track.width_nm / 2));
    }
    nodes.push_back(std::move(node));
  }
  for (const auto& entry : board.vias) {
    const ViaDefinition& via = entry.second;
    const Aabb bounds = shape_bounds(via.pad).bounds;
    const int64_t pad_outer = std::max(bounds.width(), via.drill_nm);
    CopperNode node;
    node.id = "via:" + via.id;
    node.net = via.net;
    node.kind = "via";
    node.plated = via.plated;
    node.drill_nm = via.drill_nm;
    node.pad_outer_nm = pad_outer;
    node.via_center = {bounds.center_x(), bounds.center_y()};
    node.via_start = via.start_index;
    node.via_end = via.end_index;
    if (via.plated) {
      for (int layer = via.start_index; layer <= via.end_index; ++layer) {
        node.layer_shapes.emplace_back(
            layer, make_circle(node.via_center, std::max<int64_t>(1, pad_outer / 2)));
      }
    }
    nodes.push_back(std::move(node));
  }
  for (const ZoneFillResult& fill : board.fills) {
    const int layer_index = board.stackup.index_of(fill.layer);
    for (const FillComponent& component : fill.components) {
      CopperNode node;
      node.id = "fill:" + component.id;
      node.net = fill.net;
      node.kind = "fill";
      node.is_fill = true;
      node.anchored = component.anchored;
      node.zone_id = fill.zone_id;
      for (const FillRect& rect : component.rects) {
        node.layer_shapes.emplace_back(
            layer_index, make_polygon({{rect.x0, rect.y0}, {rect.x1, rect.y0},
                                       {rect.x1, rect.y1}, {rect.x0, rect.y1}}));
      }
      for (const FillRect& rect : component.spokes) {
        node.layer_shapes.emplace_back(
            layer_index, make_polygon({{rect.x0, rect.y0}, {rect.x1, rect.y0},
                                       {rect.x1, rect.y1}, {rect.x0, rect.y1}}));
      }
      if (!node.layer_shapes.empty()) nodes.push_back(std::move(node));
    }
  }

  ConnectivityInput connectivity_input;
  connectivity_input.nodes = nodes;
  connectivity_input.net_ties = board.net_ties;
  connectivity_input.required_endpoints = board.required_endpoints;
  connectivity_input.net_requirements = board.net_requirements;
  for (const ZoneFillResult& fill : board.fills) {
    connectivity_input.zones.push_back(ZoneConnectivityInfo{
        fill.zone_id, fill.net, board.stackup.index_of(fill.layer), fill.outline,
        fill.islands_removed, fill.removed_area_nm2});
  }
  const Clock::time_point connectivity_start = Clock::now();
  const ConnectivityReport connectivity =
      analyze_connectivity(connectivity_input);
  const double connectivity_ms = elapsed_ms(connectivity_start);

  DrcInput drc_input;
  drc_input.nodes = nodes;
  drc_input.holes = board.holes;
  drc_input.board_outline = board.outline;
  drc_input.net_classes = board.net_classes;
  drc_input.rules = board.rules;
  const Clock::time_point drc_start = Clock::now();
  const DrcReport drc = run_drc(drc_input);
  const double drc_ms = elapsed_ms(drc_start);

  Json fill = fill_json(board.fills);
  const std::string fill_hash = sha256_hex(fill.dump());
  fill["fill_hash"] = Json(fill_hash);

  bool fill_incomplete = false;
  for (const ZoneFillResult& result : board.fills) {
    fill_incomplete = fill_incomplete || result.non_rectilinear;
  }
  const bool failed = drc.status == "FAIL" || connectivity.u_total > 0 ||
                      connectivity.short_count > 0 ||
                      connectivity.missing_endpoint_count > 0;
  const bool not_checked = drc.status == "NOT_CHECKED" || fill_incomplete;
  const std::string overall = failed ? "FAIL"
                                     : (not_checked ? "NOT_CHECKED" : "PASS");
  Json report = Json::object();
  report["status"] = Json(overall);
  report["review_ready"] = Json(overall == "PASS" && drc.review_ready);
  report["u_total"] = Json(static_cast<int64_t>(connectivity.u_total));
  report["short_count"] = Json(static_cast<int64_t>(connectivity.short_count));
  report["missing_endpoint_count"] =
      Json(static_cast<int64_t>(connectivity.missing_endpoint_count));
  report["drc_status"] = Json(drc.status);
  report["fill_incomplete"] = Json(fill_incomplete);
  if (fill_incomplete) {
    Json diagnostics = Json::array();
    diagnostics.push_back(Json("ZONE_NON_RECTILINEAR"));
    report["diagnostics"] = diagnostics;
  }

  out["fill"] = fill;
  out["connectivity"] = connectivity_json(connectivity);
  out["drc"] = drc_json(drc);
  out["report"] = report;
  Json metrics = Json::object();
  metrics["fill_ms"] = Json(fill_ms);
  metrics["connectivity_ms"] = Json(connectivity_ms);
  metrics["drc_ms"] = Json(drc_ms);
  metrics["total_ms"] = Json(elapsed_ms(started));
  out["metrics"] = metrics;
  return out;
}

Json run_place(BoardData& board, const Json& query) {
  const std::string id = string_field(query, "id", "place", true);
  Json out = query_result_ok(id);
  if (!board.has_placement) invalid("job input has no placement block");
  const auto started = Clock::now();
  const PlacementResult result = place_components(board.placement);
  Json placement = Json::object();
  placement["status"] = Json(result.status);
  placement["seed"] = Json(result.seed);
  placement["moves"] = Json(result.moves);
  placement["search_nodes"] = Json(result.search_nodes);
  placement["search_exhausted"] = Json(result.search_exhausted);
  placement["semantic_hash"] = Json(result.semantic_hash);
  placement["wirelength_nm"] = Json(result.wirelength_nm);
  placement["escape_pressure"] = Json(result.escape_pressure);
  Json patch = Json::array();
  for (const PlacementMove& move : result.patch) {
    Json item = Json::object();
    item["component_id"] = Json(move.component_id);
    item["x_nm"] = Json(move.x_nm);
    item["y_nm"] = Json(move.y_nm);
    item["rotation_deg"] = Json(move.rotation_deg);
    item["fixed"] = Json(move.fixed);
    patch.push_back(item);
  }
  placement["patch"] = patch;
  Json unplaced = Json::array();
  for (const std::string& component : result.unplaced_components) {
    unplaced.push_back(Json(component));
  }
  placement["unplaced_components"] = unplaced;
  Json violations = Json::array();
  for (const std::string& violation : result.hard_violations) {
    violations.push_back(Json(violation));
  }
  placement["hard_violations"] = violations;
  Json diagnostics = Json::array();
  for (const std::string& diagnostic : result.diagnostics) {
    diagnostics.push_back(Json(diagnostic));
  }
  placement["diagnostics"] = diagnostics;
  Json congestion = Json::object();
  congestion["columns"] = Json(static_cast<int64_t>(result.congestion.columns));
  congestion["rows"] = Json(static_cast<int64_t>(result.congestion.rows));
  congestion["cell_nm"] = Json(result.congestion.cell_nm);
  congestion["max_load"] = Json(result.congestion.max_load);
  congestion["mean_load"] = Json(result.congestion.mean_load);
  Json hot = Json::array();
  for (const CongestionCell& cell : result.congestion.hot_cells) {
    Json item = Json::object();
    item["column"] = Json(static_cast<int64_t>(cell.column));
    item["row"] = Json(static_cast<int64_t>(cell.row));
    item["x_nm"] = Json(cell.x_nm);
    item["y_nm"] = Json(cell.y_nm);
    item["load"] = Json(cell.load);
    hot.push_back(item);
  }
  congestion["hot_cells"] = hot;
  placement["congestion"] = congestion;
  out["placement"] = placement;
  Json metrics = Json::object();
  metrics["elapsed_ms"] = Json(std::chrono::duration<double, std::milli>(
                                   Clock::now() - started).count());
  out["metrics"] = metrics;
  return out;
}

Json run_route(BoardData& board, const Json& query) {
  const std::string id = string_field(query, "id", "route", true);
  Json out = query_result_ok(id);
  if (!board.has_routing) invalid("job input has no routing block");
  const auto started = Clock::now();
  const RoutingResult result = route_board(board.routing);
  Json routing = Json::object();
  routing["status"] = Json(result.status);
  routing["seed"] = Json(result.seed);
  routing["semantic_hash"] = Json(result.semantic_hash);
  routing["u_total"] = Json(static_cast<int64_t>(result.u_total));
  routing["short_count"] = Json(static_cast<int64_t>(result.short_count));
  routing["wirelength_nm"] = Json(result.wirelength_nm);
  routing["via_count"] = Json(static_cast<int64_t>(result.via_count));
  routing["expansions"] = Json(static_cast<int64_t>(result.expansions));
  routing["rounds"] = Json(static_cast<int64_t>(result.rounds));
  routing["grid_columns"] = Json(static_cast<int64_t>(result.grid_columns));
  routing["grid_rows"] = Json(static_cast<int64_t>(result.grid_rows));
  routing["grid_layers"] = Json(static_cast<int64_t>(result.grid_layers));
  Json tracks = Json::array();
  for (const RoutedTrack& track : result.tracks) {
    Json item = Json::object();
    item["id"] = Json(track.id);
    item["net"] = Json(track.net);
    item["layer_id"] = Json(track.layer_id);
    item["stackup_layer"] = Json(static_cast<int64_t>(track.stackup_layer));
    item["width_nm"] = Json(track.width_nm);
    item["locked"] = Json(track.locked);
    Json points = Json::array();
    for (const Vec2& point : track.points) {
      Json entry = Json::object();
      entry["x"] = Json(point.x);
      entry["y"] = Json(point.y);
      points.push_back(entry);
    }
    item["points"] = points;
    tracks.push_back(item);
  }
  routing["tracks"] = tracks;
  Json vias = Json::array();
  for (const RoutedVia& via : result.vias) {
    Json item = Json::object();
    item["id"] = Json(via.id);
    item["net"] = Json(via.net);
    item["x_nm"] = Json(via.center.x);
    item["y_nm"] = Json(via.center.y);
    item["drill_nm"] = Json(via.drill_nm);
    item["pad_outer_nm"] = Json(via.pad_outer_nm);
    item["start_layer"] = Json(via.start_layer);
    item["end_layer"] = Json(via.end_layer);
    item["start_layer_id"] = Json(via.start_layer_id);
    item["end_layer_id"] = Json(via.end_layer_id);
    item["locked"] = Json(via.locked);
    vias.push_back(item);
  }
  routing["vias"] = vias;
  Json reports = Json::array();
  for (const NetRouteReport& report : result.net_reports) {
    Json item = Json::object();
    item["net"] = Json(report.net);
    item["terminals"] = Json(static_cast<int64_t>(report.terminals));
    item["routed"] = Json(report.routed);
    item["u"] = Json(static_cast<int64_t>(report.u));
    item["reason"] = Json(report.reason);
    Json unconnected = Json::array();
    for (const std::string& endpoint : report.unconnected_endpoints) {
      unconnected.push_back(Json(endpoint));
    }
    item["unconnected_endpoints"] = unconnected;
    reports.push_back(item);
  }
  routing["net_reports"] = reports;
  Json unrouted = Json::array();
  for (const std::string& net : result.unrouted_nets) {
    unrouted.push_back(Json(net));
  }
  routing["unrouted_nets"] = unrouted;
  Json requested = Json::array();
  for (const RoutingNet& net : board.routing.nets) {
    requested.push_back(Json(net.net));
  }
  routing["requested_nets"] = requested;
  Json completed = Json::array();
  for (const RoutingNet& net : board.routing.nets) {
    if (std::find(result.unrouted_nets.begin(), result.unrouted_nets.end(),
                  net.net) == result.unrouted_nets.end()) {
      completed.push_back(Json(net.net));
    }
  }
  routing["completed_nets"] = completed;
  Json diagnostics = Json::array();
  for (const std::string& diagnostic : result.diagnostics) {
    diagnostics.push_back(Json(diagnostic));
  }
  routing["diagnostics"] = diagnostics;
  out["routing"] = routing;
  Json metrics = Json::object();
  metrics["elapsed_ms"] = Json(std::chrono::duration<double, std::milli>(
                                   Clock::now() - started).count());
  out["metrics"] = metrics;
  return out;
}

struct QueryContext {
  BoardData& board;
  int64_t default_arc_error_nm = 100;
};

Json run_highspeed(BoardData& board, const Json& query) {
  const std::string id = string_field(query, "id", "highspeed", true);
  Json out = query_result_ok(id);
  if (!board.has_highspeed) invalid("job input has no highspeed block");
  const auto started = Clock::now();
  const HighspeedResult result = analyze_highspeed(board.highspeed);

  Json highspeed = Json::object();
  highspeed["status"] = Json(result.status);
  highspeed["si_status"] = Json(result.si_status);
  highspeed["pi_status"] = Json(result.pi_status);
  highspeed["seed"] = Json(result.seed);
  highspeed["semantic_hash"] = Json(result.semantic_hash);
  highspeed["pass_count"] = Json(static_cast<int64_t>(result.pass_count));
  highspeed["fail_count"] = Json(static_cast<int64_t>(result.fail_count));
  highspeed["not_checked_count"] =
      Json(static_cast<int64_t>(result.not_checked_count));
  highspeed["skipped_count"] = Json(static_cast<int64_t>(result.skipped_count));
  highspeed["budget_hit"] = Json(result.budget_hit);
  highspeed["search_nodes"] = Json(result.search_nodes);
  highspeed["channels"] =
      Json(static_cast<int64_t>(board.highspeed.channels.size()));
  highspeed["pairs"] = Json(static_cast<int64_t>(board.highspeed.pairs.size()));

  Json checks = Json::array();
  for (const CheckResult& check : result.checks) {
    Json item = Json::object();
    item["check_id"] = Json(check.check_id);
    item["kind"] = Json(check.kind);
    item["subject"] = Json(check.subject);
    item["status"] = Json(check.status);
    item["reason"] = Json(check.reason);
    item["message"] = Json(check.message);
    item["measured_nm"] = Json(check.measured_nm);
    item["limit_nm"] = Json(check.limit_nm);
    item["measured_ps"] = Json(check.measured_ps);
    item["limit_ps"] = Json(check.limit_ps);
    Json refs = Json::array();
    for (const std::string& ref : check.object_refs) refs.push_back(Json(ref));
    item["object_refs"] = refs;
    checks.push_back(item);
  }
  highspeed["checks"] = checks;

  Json bundles = Json::array();
  for (const BundleRoute& bundle : result.bundles) {
    Json item = Json::object();
    item["pair_id"] = Json(bundle.pair_id);
    item["status"] = Json(bundle.status);
    item["coupled_nm"] = Json(bundle.coupled_nm);
    item["uncoupled_nm"] = Json(bundle.uncoupled_nm);
    item["skew_nm"] = Json(bundle.skew_nm);
    item["turn_delta_nm"] = Json(bundle.turn_delta_nm);
    item["unmatched_corners"] =
        Json(static_cast<int64_t>(bundle.unmatched_corners));
    item["turn_compensated"] = Json(bundle.turn_compensated);
    item["shared_vias"] = Json(static_cast<int64_t>(bundle.shared_vias));
    item["p_vias"] = Json(static_cast<int64_t>(bundle.p_vias));
    item["n_vias"] = Json(static_cast<int64_t>(bundle.n_vias));
    item["paired_layers"] = Json(bundle.paired_layers);
    item["sample_step_nm"] = Json(bundle.sample_step_nm);
    Json nets = Json::array();
    for (const std::string& net : bundle.nets) nets.push_back(Json(net));
    item["nets"] = nets;
    bundles.push_back(item);
  }
  highspeed["bundles"] = bundles;

  Json tuning = Json::array();
  for (const TuningPatch& patch : result.tuning) {
    Json item = Json::object();
    item["id"] = Json(patch.id);
    item["pair_id"] = Json(patch.pair_id);
    item["net"] = Json(patch.net);
    item["layer_id"] = Json(patch.layer_id);
    item["stackup_layer"] = Json(patch.stackup_layer);
    item["start_x"] = Json(patch.start.x);
    item["start_y"] = Json(patch.start.y);
    item["end_x"] = Json(patch.end.x);
    item["end_y"] = Json(patch.end.y);
    item["required_extra_nm"] = Json(patch.required_extra_nm);
    item["added_length_nm"] = Json(patch.added_length_nm);
    item["added_delay_ps"] = Json(patch.added_delay_ps);
    item["amplitude_nm"] = Json(patch.amplitude_nm);
    item["pitch_nm"] = Json(patch.pitch_nm);
    item["teeth"] = Json(static_cast<int64_t>(patch.teeth));
    item["controlled"] = Json(patch.controlled);
    item["note"] = Json(patch.note);
    Json points = Json::array();
    for (const Vec2& point : patch.points) {
      Json entry = Json::object();
      entry["x"] = Json(point.x);
      entry["y"] = Json(point.y);
      points.push_back(entry);
    }
    item["points"] = points;
    tuning.push_back(item);
  }
  highspeed["tuning"] = tuning;

  Json violations = Json::array();
  for (const std::string& value : result.violations) {
    violations.push_back(Json(value));
  }
  highspeed["violations"] = violations;
  Json not_checked = Json::array();
  for (const std::string& value : result.not_checked) {
    not_checked.push_back(Json(value));
  }
  highspeed["not_checked"] = not_checked;
  Json diagnostics = Json::array();
  for (const std::string& value : result.diagnostics) {
    diagnostics.push_back(Json(value));
  }
  highspeed["diagnostics"] = diagnostics;

  out["highspeed"] = highspeed;
  Json metrics = Json::object();
  metrics["elapsed_ms"] = Json(std::chrono::duration<double, std::milli>(
                                   Clock::now() - started).count());
  out["metrics"] = metrics;
  return out;
}

Json run_query(const Json& query, QueryContext& context) {
  const std::string id = string_field(query, "id", "", true);
  const std::string kind = string_field(query, "kind", "", true);
  const int64_t arc_error = int_field(query, "arc_error_nm",
                                      context.default_arc_error_nm, false);

  if (kind == "verify") return run_verify(context.board, query);
  if (kind == "place") return run_place(context.board, query);
  if (kind == "route") return run_route(context.board, query);
  if (kind == "highspeed") return run_highspeed(context.board, query);

  if (kind == "distance") {
    Shape a = parse_shape(query.at("a"));
    Shape b = parse_shape(query.at("b"));
    int64_t transform_error = 0;
    if (query.has("transform_a")) {
      const auto result = transform_shape(a, parse_transform(query.at("transform_a")));
      a = result.shape;
      transform_error += result.error_nm;
    }
    if (query.has("transform_b")) {
      const auto result = transform_shape(b, parse_transform(query.at("transform_b")));
      b = result.shape;
      transform_error += result.error_nm;
    }
    const DistanceResult distance = shape_distance(a, b, arc_error);
    Json out = query_result_ok(id);
    out["distance_nm"] = Json(distance.distance_nm);
    out["error_nm"] = Json(distance.error_nm + transform_error);
    out["exact"] = Json(distance.exact && transform_error == 0);
    out["intersects"] = Json(distance.intersects);
    out["conservative_distance_nm"] =
        Json(std::max<int64_t>(0, distance.distance_nm - distance.error_nm -
                                     transform_error));
    if (query.has("required_nm")) {
      const int64_t required = int_field(query, "required_nm", 0, true);
      out["meets_required"] = Json(
          distance.distance_nm - distance.error_nm - transform_error >= required);
    }
    return out;
  }

  if (kind == "shape_transform") {
    const auto result =
        transform_shape(parse_shape(query.at("shape")),
                        parse_transform(query.at("transform")));
    const BoundsResult bounds = shape_bounds(result.shape);
    Json out = query_result_ok(id);
    out["shape"] = shape_json(result.shape);
    out["bounds"] = bounds_json(bounds.bounds, bounds.error_nm + result.error_nm);
    out["error_nm"] = Json(result.error_nm);
    return out;
  }

  if (kind == "board_query") {
    const std::string layer = string_field(query, "layer", "", true);
    const Aabb area = parse_aabb(query.at("area"));
    const std::vector<std::string> ids = context.board.index.query(layer, area);
    Json out = query_result_ok(id);
    Json array = Json::array();
    for (const std::string& object_id : ids) array.push_back(Json(object_id));
    out["object_ids"] = array;
    out["count"] = Json(static_cast<int64_t>(ids.size()));
    const int layer_index = context.board.stackup.index_of(layer);
    out["layer_index"] = Json(static_cast<int64_t>(layer_index));
    for (const LayerIndexStats& stats : context.board.index.layer_stats()) {
      if (stats.layer == layer) {
        out["rtree_nodes"] = Json(static_cast<int64_t>(stats.nodes));
        out["rtree_max_depth"] = Json(static_cast<int64_t>(stats.max_depth));
        out["layer_memory_bytes"] = Json(static_cast<int64_t>(stats.memory_bytes));
      }
    }
    return out;
  }

  if (kind == "index_stats") {
    Json out = query_result_ok(id);
    out["objects"] = Json(static_cast<int64_t>(context.board.index.object_count()));
    out["memory_bytes"] =
        Json(static_cast<int64_t>(context.board.index.memory_bytes()));
    Json layers = Json::array();
    for (const LayerIndexStats& stats : context.board.index.layer_stats()) {
      Json entry = Json::object();
      entry["layer"] = Json(stats.layer);
      entry["objects"] = Json(static_cast<int64_t>(stats.objects));
      entry["nodes"] = Json(static_cast<int64_t>(stats.nodes));
      entry["max_depth"] = Json(static_cast<int64_t>(stats.max_depth));
      entry["memory_bytes"] = Json(static_cast<int64_t>(stats.memory_bytes));
      layers.push_back(entry);
    }
    out["layers"] = layers;
    return out;
  }

  if (kind == "stackup") {
    Json out = query_result_ok(id);
    out["copper_count"] = Json(context.board.stackup.copper_count());
    out["total_thickness_nm"] =
        Json(context.board.stackup.total_thickness_nm());
    out["declared_thickness_nm"] = Json(context.board.thickness_nm);
    out["thickness_match"] =
        Json(context.board.thickness_nm == 0 ||
             context.board.stackup.total_thickness_nm() ==
                 context.board.thickness_nm);
    Json layers = Json::array();
    for (const LayerSpec& layer : context.board.stackup.layers()) {
      Json entry = Json::object();
      entry["id"] = Json(layer.id);
      entry["order"] = Json(layer.order);
      entry["kind"] = Json(layer.kind);
      entry["thickness_nm"] = Json(layer.thickness_nm);
      entry["material_ref"] = Json(layer.material_ref);
      layers.push_back(entry);
    }
    out["layers"] = layers;
    return out;
  }

  if (kind == "via_span") {
    const std::string via_id = string_field(query, "id_ref", "", false);
    const std::string lookup = via_id.empty() ? id : via_id;
    const auto found = context.board.vias.find(lookup);
    if (found == context.board.vias.end()) {
      invalid("unknown via " + lookup);
    }
    const ViaDefinition& via = found->second;
    const std::string layer = string_field(query, "layer", "", true);
    const int layer_index = context.board.stackup.index_of(layer);
    if (layer_index < 0) invalid("unknown layer " + layer);
    Json out = query_result_ok(id);
    out["via_id"] = Json(via.id);
    out["kind"] = Json(via_kind_name(via.kind));
    out["layer_index"] = Json(static_cast<int64_t>(layer_index));
    out["present"] = Json(via.spans(layer_index));
    out["plated"] = Json(via.plated);
    out["connected"] = Json(via.electrically_connects() &&
                            via.spans(layer_index));
    out["start_layer"] = Json(via.start_layer);
    out["end_layer"] = Json(via.end_layer);
    out["drill_nm"] = Json(via.drill_nm);
    return out;
  }

  if (kind == "padstack") {
    const std::string pad_id = string_field(query, "id_ref", "", false);
    const std::string lookup = pad_id.empty() ? id : pad_id;
    const auto found = context.board.pads.find(lookup);
    if (found == context.board.pads.end()) invalid("unknown padstack " + lookup);
    const PadStack& pad = found->second;
    const std::string layer = string_field(query, "layer", "", true);
    Json out = query_result_ok(id);
    out["pad_id"] = Json(pad.id);
    out["plated"] = Json(pad.plated);
    out["electrically_connects"] = Json(pad.electrically_connects());
    const Shape* copper = pad.copper_on(layer);
    out["present"] = Json(copper != nullptr);
    if (copper != nullptr) {
      Shape shape = *copper;
      int64_t error_nm = 0;
      if (query.has("transform")) {
        const auto result = transform_shape(shape, parse_transform(query.at("transform")));
        shape = result.shape;
        error_nm += result.error_nm;
      }
      const BoundsResult bounds = shape_bounds(shape);
      out["shape"] = shape_json(shape);
      out["bounds"] = bounds_json(bounds.bounds, bounds.error_nm + error_nm);
    }
    if (pad.drill.has_value()) {
      Shape drill = *pad.drill;
      int64_t error_nm = 0;
      if (query.has("transform")) {
        const auto result = transform_shape(drill, parse_transform(query.at("transform")));
        drill = result.shape;
        error_nm += result.error_nm;
      }
      out["drill"] = shape_json(drill);
      out["drill_error_nm"] = Json(error_nm);
    }
    return out;
  }

  invalid("unknown query kind " + kind);
}

Json failed_result(const std::string& input_id, ErrorCode code,
                   const std::string& message, size_t total,
                   double elapsed_ms) {
  Json out = Json::object();
  out["schema_version"] = Json(static_cast<int64_t>(2));
  out["input_id"] = Json(input_id);
  out["status"] = Json("FAILED");
  out["completed"] = Json(static_cast<int64_t>(0));
  out["total"] = Json(static_cast<int64_t>(total));
  out["failed"] = Json(static_cast<int64_t>(0));
  out["results"] = Json::array();
  Json diagnostics = Json::array();
  Json diagnostic = Json::object();
  diagnostic["code"] = Json(error_code_name(code));
  diagnostic["severity"] = Json("error");
  diagnostic["message"] = Json(message);
  diagnostics.push_back(diagnostic);
  out["diagnostics"] = diagnostics;
  Json metrics = Json::object();
  metrics["elapsed_ms"] = Json(elapsed_ms);
  out["metrics"] = metrics;
  out["checkpoint"] = Json::object();
  Json kernel = Json::object();
  kernel["name"] = Json(kKernelName);
  kernel["version"] = Json(kKernelVersion);
  out["kernel"] = kernel;
  return out;
}

std::string lower_copy(std::string value) {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
  return value;
}

std::string read_file(const std::string& path) {
  std::ifstream stream(path, std::ios::binary | std::ios::ate);
  if (!stream) invalid("cannot open input artifact " + path);
  const std::streamsize size = stream.tellg();
  if (size < 0) invalid("cannot size input artifact");
  if (static_cast<size_t>(size) > kMaxFrameBytes) {
    throw KernelError(ErrorCode::InputTooLarge, "input artifact exceeds 4 MiB");
  }
  std::string bytes(static_cast<size_t>(size), '\0');
  stream.seekg(0, std::ios::beg);
  if (size > 0 &&
      !stream.read(bytes.data(), static_cast<std::streamsize>(bytes.size()))) {
    invalid("cannot read input artifact");
  }
  return bytes;
}

}  // namespace

Json kernel_info() {
  Json out = Json::object();
  out["name"] = Json(kKernelName);
  out["version"] = Json(kKernelVersion);
  out["schema_version"] = Json(static_cast<int64_t>(2));
  Json capabilities = Json::array();
  for (const char* item :
       {"integer_geometry", "arc_error_bound", "rtree_query", "padstack",
        "via_span", "frozen_input", "budget_checkpoint", "cancel_checkpoint",
        "error_isolation"}) {
    capabilities.push_back(Json(item));
  }
  out["capabilities"] = capabilities;
  Json limits = Json::object();
  limits["max_frame_bytes"] = Json(static_cast<int64_t>(kMaxFrameBytes));
  limits["max_objects"] = Json(static_cast<int64_t>(kMaxGeometryObjects));
  limits["coordinate_nm"] = Json(kMaxCoordinateNm);
  out["limits"] = limits;
  return out;
}

Json run_job(const std::string& input_bytes, const std::string& expected_sha256,
             const WorkerBudget& budget) {
  const auto started = Clock::now();
  auto elapsed_ms = [&]() {
    return std::chrono::duration<double, std::milli>(Clock::now() - started)
        .count();
  };

  if (budget.max_operations < 0) invalid("max_operations must be >= 0");
  std::string input_id;
  size_t total = 0;
  std::string actual_sha;
  Json results = Json::array();
  Json diagnostics = Json::array();
  int64_t completed = 0;
  int64_t failed = 0;
  int64_t operations = 0;
  bool cancelled = false;
  bool budget_stop = false;
  BoardData board;
  bool board_ready = false;

  try {
    if (input_bytes.size() > kMaxFrameBytes) {
      throw KernelError(ErrorCode::InputTooLarge, "input exceeds 4 MiB");
    }
    actual_sha = sha256_hex(input_bytes);
    if (!expected_sha256.empty() &&
        lower_copy(actual_sha) != lower_copy(expected_sha256)) {
      throw KernelError(ErrorCode::StaleInput,
                        "input artifact hash does not match the frozen request");
    }
    const Json document = Json::parse(input_bytes);
    if (!document.is_object()) invalid("job input must be an object");
    if (int_field(document, "schema_version", 0, true) != 2) {
      throw KernelError(ErrorCode::ModelInvalid,
                        "unsupported job input schema_version");
    }
    input_id = string_field(document, "input_id", "", true);
    if (!document.has("board")) invalid("job input has no board");
    if (!document.has("queries") || !document.at("queries").is_array()) {
      invalid("job input has no queries array");
    }
    total = document.at("queries").items().size();
    if (total > 10'000) {
      throw KernelError(ErrorCode::InputTooLarge, "too many queries");
    }

    Checkpoint checkpoint(budget.wall_seconds, budget.cancel_file);
    try {
      checkpoint.check();
      board = parse_board(document.at("board"), checkpoint);
      board_ready = true;
      if (document.has("rules")) {
        board.rules = parse_rules(document.at("rules"));
      }
      if (document.has("placement")) {
        board.placement = parse_placement(document.at("placement"), board.outline,
                                          board.width_nm, board.height_nm);
        board.has_placement = true;
      }
      if (document.has("routing")) {
        board.routing = parse_routing(
            document.at("routing"), board,
            static_cast<int64_t>(budget.wall_seconds * 1000.0));
        board.has_routing = true;
      }
      if (document.has("highspeed")) {
        std::vector<std::string> copper_layers;
        for (const LayerSpec& layer : board.stackup.layers()) {
          if (layer.kind == "copper") copper_layers.push_back(layer.id);
        }
        board.highspeed = parse_highspeed_json(document.at("highspeed"),
                                               copper_layers);
        board.has_highspeed = true;
      }
      if (budget.max_memory_mb > 0.0) {
        const double used_mb =
            static_cast<double>(board.index.memory_bytes()) / (1024.0 * 1024.0);
        if (used_mb > budget.max_memory_mb) {
          throw KernelError(ErrorCode::BudgetExhausted,
                            "index memory budget exhausted");
        }
      }
    } catch (const KernelError& error) {
      if (error.code() == ErrorCode::Cancelled) {
        cancelled = true;
      } else if (error.code() == ErrorCode::BudgetExhausted) {
        budget_stop = true;
      } else {
        throw;
      }
    }

    QueryContext context{board};
    for (const Json& query : document.at("queries").items()) {
      if (cancelled || budget_stop) break;
      if (operations >= budget.max_operations) {
        budget_stop = true;
        break;
      }
      try {
        checkpoint.check();
      } catch (const KernelError& error) {
        if (error.code() == ErrorCode::Cancelled) cancelled = true;
        else budget_stop = true;
        break;
      }
      ++operations;
      try {
        results.push_back(run_query(query, context));
        ++completed;
      } catch (const KernelError& error) {
        if (error.code() == ErrorCode::Cancelled ||
            error.code() == ErrorCode::BudgetExhausted) {
          if (error.code() == ErrorCode::Cancelled) cancelled = true;
          else budget_stop = true;
          break;
        }
        const std::string query_id =
            query.is_object() ? string_field(query, "id", "", false) : "";
        results.push_back(
            query_failure(query_id, error.code(), error.what()));
        ++failed;
        Json diagnostic = Json::object();
        diagnostic["code"] = Json(error_code_name(error.code()));
        diagnostic["severity"] = Json("error");
        diagnostic["query_id"] = Json(query_id);
        diagnostic["message"] = Json(error.what());
        diagnostics.push_back(diagnostic);
      } catch (const std::exception& error) {
        const std::string query_id =
            query.is_object() ? string_field(query, "id", "", false) : "";
        results.push_back(query_failure(query_id, ErrorCode::Internal,
                                        error.what()));
        ++failed;
        Json diagnostic = Json::object();
        diagnostic["code"] = Json("INTERNAL_ERROR");
        diagnostic["severity"] = Json("error");
        diagnostic["query_id"] = Json(query_id);
        diagnostic["message"] = Json(error.what());
        diagnostics.push_back(diagnostic);
      }
    }

    if (cancelled) {
      Json diagnostic = Json::object();
      diagnostic["code"] = Json("CANCELLED");
      diagnostic["severity"] = Json("info");
      diagnostic["message"] = Json("cancelled at a worker checkpoint");
      diagnostics.push_back(diagnostic);
    } else if (budget_stop) {
      Json diagnostic = Json::object();
      diagnostic["code"] = Json("BUDGET_EXHAUSTED");
      diagnostic["severity"] = Json("warning");
      diagnostic["message"] = Json(
          "worker budget exhausted; this is UNKNOWN/PARTIAL, not a proof "
          "that the design is impossible");
      diagnostics.push_back(diagnostic);
    }

    std::string status;
    if (cancelled) {
      status = "CANCELLED";
    } else if (budget_stop) {
      status = completed > 0 ? "PARTIAL" : "UNKNOWN";
    } else if (failed > 0) {
      status = completed > 0 ? "PARTIAL" : "FAILED";
    } else {
      status = "SUCCEEDED";
    }

    Json out = Json::object();
    out["schema_version"] = Json(static_cast<int64_t>(2));
    out["input_id"] = Json(input_id);
    out["status"] = Json(status);
    out["completed"] = Json(completed);
    out["total"] = Json(static_cast<int64_t>(total));
    out["failed"] = Json(failed);
    out["results"] = results;
    out["diagnostics"] = diagnostics;
    out["input_sha256"] = Json(actual_sha);
    Json kernel = Json::object();
    kernel["name"] = Json(kKernelName);
    kernel["version"] = Json(kKernelVersion);
    out["kernel"] = kernel;

    Json metrics = Json::object();
    metrics["elapsed_ms"] = Json(elapsed_ms());
    metrics["operations"] = Json(operations);
    metrics["objects"] = Json(static_cast<int64_t>(
        board_ready ? board.index.object_count() : 0));
    metrics["index_memory_bytes"] = Json(static_cast<int64_t>(
        board_ready ? board.index.memory_bytes() : 0));
    out["metrics"] = metrics;

    Json checkpoint_json = Json::object();
    checkpoint_json["completed_queries"] = Json(completed);
    checkpoint_json["next_query_index"] = Json(completed + failed);
    checkpoint_json["cancelled"] = Json(cancelled);
    checkpoint_json["budget_exhausted"] = Json(budget_stop);
    out["checkpoint"] = checkpoint_json;
    return out;
  } catch (const KernelError& error) {
    return failed_result(input_id, error.code(), error.what(), total,
                         elapsed_ms());
  } catch (const std::exception& error) {
    return failed_result(input_id, ErrorCode::Internal, error.what(), total,
                         elapsed_ms());
  }
}

Json handle_rpc(const Json& request) {
  auto error_response = [&](int code, const std::string& message,
                            const std::string& data_code) {
    Json response = Json::object();
    response["jsonrpc"] = Json("2.0");
    response["id"] = request.has("id") ? request.at("id") : Json(nullptr);
    Json error = Json::object();
    error["code"] = Json(static_cast<int64_t>(code));
    error["message"] = Json(message);
    Json data = Json::object();
    data["code"] = Json(data_code);
    error["data"] = data;
    response["error"] = error;
    return response;
  };

  try {
    if (!request.is_object()) {
      return error_response(-32600, "request must be an object", "INVALID_REQUEST");
    }
    const std::string jsonrpc = string_field(request, "jsonrpc", "", false);
    if (jsonrpc != "2.0") {
      return error_response(-32600, "jsonrpc must be 2.0", "INVALID_REQUEST");
    }
    const std::string method = string_field(request, "method", "", true);
    Json response = Json::object();
    response["jsonrpc"] = Json("2.0");
    response["id"] = request.has("id") ? request.at("id") : Json(nullptr);
    if (method == "kernel.info") {
      response["result"] = kernel_info();
      return response;
    }
    if (method != "job.run") {
      return error_response(-32601, "unknown method", "METHOD_NOT_FOUND");
    }
    if (!request.has("params") || !request.at("params").is_object()) {
      return error_response(-32602, "params must be an object", "INVALID_PARAMS");
    }
    const Json& params = request.at("params");
    const std::string input_path = string_field(params, "input_path", "", true);
    const std::string expected_sha =
        string_field(params, "input_sha256", "", true);
    WorkerBudget budget;
    if (params.has("budget")) {
      const Json& raw = params.at("budget");
      budget.wall_seconds = number_field(raw, "wall_seconds", 5.0);
      budget.max_operations = int_field(raw, "max_operations", 100000, false);
      budget.max_memory_mb = number_field(raw, "max_memory_mb", 0.0);
      budget.cancel_file = string_field(raw, "cancel_file", "", false);
    }
    const std::string bytes = read_file(input_path);
    response["result"] = run_job(bytes, expected_sha, budget);
    return response;
  } catch (const KernelError& error) {
    return error_response(-32602, error.what(), error_code_name(error.code()));
  } catch (const std::exception& error) {
    return error_response(-32603, error.what(), "INTERNAL_ERROR");
  }
}

}  // namespace mota
