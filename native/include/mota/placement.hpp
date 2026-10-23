// Constraint-driven placement with legalisation and bounded search.
//
// Hard constraints (fixed objects, board/keepouts, module regions, courtyard
// gaps, decoupling distance) are never traded for wirelength. The search is a
// finite, node-budgeted CP-style backtracking search; INFEASIBLE is only
// reported after the finite candidate space was exhausted or a fixed object
// already violates a hard constraint. A budget stop is PARTIAL/UNKNOWN.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "mota/shapes.hpp"
#include "mota/types.hpp"

namespace mota {

struct PlacementComponent {
  std::string id;
  std::string reference;
  std::string module;
  int64_t x_nm = 0;
  int64_t y_nm = 0;
  int64_t w_nm = 0;
  int64_t h_nm = 0;
  int rotation_deg = 0;
  bool fixed = false;
  bool critical = false;
  int pins = 0;
  int64_t pin_pitch_nm = 0;
  std::vector<std::string> decoupling_for;
};

struct PlacementNet {
  std::string net;
  std::vector<std::string> components;  // unique component ids
};

struct PlacementKeepout {
  std::string id;
  Aabb area{};
};

struct ModuleRegion {
  std::string module;
  Aabb area{};
};

struct PlacementRules {
  int64_t grid_nm = 1'270'000;
  int64_t edge_clearance_nm = 500'000;
  int64_t courtyard_gap_nm = 400'000;
  int64_t decoupling_max_nm = 3'000'000;
  int64_t congestion_cell_nm = 2'000'000;
  double wirelength_weight = 1.0;
  double congestion_weight = 1.0;
  double escape_weight = 0.5;
  double module_weight = 0.5;
  double move_weight = 0.1;
  int local_rounds = 4;
  int64_t seed = 0;
  int64_t max_search_nodes = 20'000;
  int max_candidates_per_component = 2'000;
};

struct PlacementInput {
  std::vector<Vec2> outline;
  double board_width_nm = 0;
  double board_height_nm = 0;
  std::vector<PlacementComponent> components;
  std::vector<PlacementNet> nets;
  std::vector<PlacementKeepout> keepouts;
  std::vector<ModuleRegion> module_regions;
  PlacementRules rules;
};

struct PlacementMove {
  std::string component_id;
  int64_t x_nm = 0;
  int64_t y_nm = 0;
  int rotation_deg = 0;
  bool fixed = false;
};

struct CongestionCell {
  int column = 0;
  int row = 0;
  int64_t x_nm = 0;
  int64_t y_nm = 0;
  double load = 0.0;
};

struct CongestionReport {
  int columns = 0;
  int rows = 0;
  int64_t cell_nm = 0;
  double max_load = 0.0;
  double mean_load = 0.0;
  std::vector<CongestionCell> hot_cells;
};

struct PlacementResult {
  std::string status;  // SUCCEEDED | PARTIAL | UNKNOWN | INFEASIBLE
  std::vector<PlacementMove> patch;
  std::vector<std::string> unplaced_components;
  std::vector<std::string> hard_violations;
  std::vector<std::string> diagnostics;
  CongestionReport congestion;
  double wirelength_nm = 0.0;
  double escape_pressure = 0.0;
  int64_t moves = 0;
  int64_t search_nodes = 0;
  bool search_exhausted = true;
  int64_t seed = 0;
  std::string semantic_hash;
};

PlacementResult place_components(const PlacementInput& input);

}  // namespace mota
