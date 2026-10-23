// Multi-layer single-ended routing with a coarse corridor and sparse A*.
//
// The router is deterministic under a fixed seed. Hard obstacles, locked
// copper and board edges are never removed. A budget stop returns PARTIAL or
// UNKNOWN; INFEASIBLE is only used when every failed net was proven unreachable
// in the finite grid model.
#pragma once

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "mota/shapes.hpp"
#include "mota/types.hpp"

namespace mota {

struct RoutingPad {
  std::string id;
  std::string net;
  std::string component_id;
  std::string pin;
  bool plated = true;
  std::vector<std::pair<int, Shape>> layer_shapes;  // stackup layer index
};

struct RoutingTrack {
  std::string id;
  std::string net;
  int stackup_layer = -1;
  int64_t width_nm = 0;
  std::vector<Vec2> points;
  bool locked = false;
};

struct RoutingVia {
  std::string id;
  std::string net;
  Vec2 center{};
  int64_t drill_nm = 0;
  int64_t pad_outer_nm = 0;
  int start_layer = -1;  // stackup layer index
  int end_layer = -1;
  bool plated = true;
  bool locked = false;
};

struct RoutingHole {
  std::string id;
  Shape shape;
};

struct RoutingKeepout {
  std::string id;
  int stackup_layer = -1;
  std::vector<Vec2> outline;
};

struct RoutingNet {
  std::string net;
  std::vector<std::string> terminals;  // pad ids
  bool locked = false;
};

struct RoutingRules {
  int64_t grid_nm = 500'000;
  int64_t track_width_nm = 300'000;
  int64_t clearance_nm = 200'000;
  int64_t edge_clearance_nm = 500'000;
  int64_t via_drill_nm = 400'000;
  int64_t via_pad_nm = 800'000;
  double via_cost_factor = 6.0;
  double corridor_penalty = 0.5;
  int max_rounds = 3;
  int64_t max_expansions = 200'000;
  int64_t max_grid_cells = 8'000'000;
  int64_t max_millis = 30'000;
  int64_t seed = 0;
};

struct RoutingInput {
  std::vector<Vec2> outline;
  int64_t width_nm = 0;
  int64_t height_nm = 0;
  std::vector<int> copper_layers;  // stackup layer indices, outer to inner
  std::vector<std::string> copper_layer_ids;  // parallel to copper_layers
  std::vector<RoutingPad> pads;
  std::vector<RoutingTrack> tracks;
  std::vector<RoutingVia> vias;
  std::vector<RoutingHole> holes;
  std::vector<RoutingKeepout> keepouts;
  std::vector<RoutingNet> nets;
  RoutingRules rules;
};

struct RoutedTrack {
  std::string id;
  std::string net;
  int stackup_layer = -1;
  std::string layer_id;
  int64_t width_nm = 0;
  std::vector<Vec2> points;
  bool locked = false;
};

struct RoutedVia {
  std::string id;
  std::string net;
  Vec2 center{};
  int64_t drill_nm = 0;
  int64_t pad_outer_nm = 0;
  int start_layer = -1;
  int end_layer = -1;
  std::string start_layer_id;
  std::string end_layer_id;
  bool locked = false;
};

struct NetRouteReport {
  std::string net;
  int terminals = 0;
  bool routed = false;
  int u = 0;
  std::string reason;
  std::vector<std::string> unconnected_endpoints;
};

struct RoutingResult {
  std::string status;  // SUCCEEDED | PARTIAL | UNKNOWN | INFEASIBLE
  std::vector<RoutedTrack> tracks;
  std::vector<RoutedVia> vias;
  std::vector<std::string> unrouted_nets;
  std::vector<NetRouteReport> net_reports;
  std::vector<std::string> diagnostics;
  int u_total = 0;
  int short_count = 0;
  double wirelength_nm = 0.0;
  int via_count = 0;
  int expansions = 0;
  int rounds = 0;
  int grid_columns = 0;
  int grid_rows = 0;
  int grid_layers = 0;
  int64_t seed = 0;
  std::string semantic_hash;
};

RoutingResult route_board(const RoutingInput& input);

}  // namespace mota
