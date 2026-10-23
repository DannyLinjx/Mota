// BGA ball-map fanout: channel pre-check, via-site generation, group-aware
// allocation and real escape geometry.
//
// A ball is only accepted when a legal via template and a real dog-bone or
// via-in-pad geometry exist. Failures name the ball and the limiting factor;
// process-invalid vias are never emitted.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "mota/shapes.hpp"
#include "mota/types.hpp"

namespace mota {

struct FanoutBall {
  std::string id;
  std::string net;
  int row = 0;
  int col = 0;
  Vec2 center{};
  int64_t pad_diameter_nm = 0;
  std::string group;
  int group_order = 0;
  bool required = true;
  bool power = false;
};

struct FanoutGroup {
  std::string id;
  std::string kind;  // differential | byte_lane | clock
  std::vector<std::string> balls;  // ordered
  bool same_layer = true;
  bool adjacent = true;
};

struct FanoutRules {
  int64_t pitch_nm = 0;
  int64_t pad_diameter_nm = 0;
  int64_t trace_width_nm = 100'000;
  int64_t clearance_nm = 100'000;
  int64_t via_drill_nm = 200'000;
  int64_t via_pad_nm = 400'000;
  int64_t microvia_drill_nm = 100'000;
  int64_t microvia_pad_nm = 250'000;
  int64_t min_annular_ring_nm = 80'000;
  int64_t board_margin_nm = 1'500'000;
  int64_t edge_clearance_nm = 200'000;
  bool allow_dogbone = true;
  bool allow_via_in_pad = false;
  bool allow_through_via = true;
  int64_t max_search_nodes = 20'000;
  int64_t seed = 0;
};

struct FanoutInput {
  std::string package_id;
  std::string package_source;
  std::vector<FanoutBall> balls;
  std::vector<FanoutGroup> groups;
  std::vector<int> signal_layers;  // stackup layer indices for escape
  std::vector<std::string> signal_layer_ids;
  FanoutRules rules;
  std::vector<Vec2> board_outline;
};

struct FanoutVia {
  std::string id;
  std::string ball_id;
  std::string net;
  Vec2 site{};
  int64_t drill_nm = 0;
  int64_t pad_outer_nm = 0;
  int start_layer = 0;
  int end_layer = 0;
  std::string start_layer_id;
  std::string end_layer_id;
  std::string kind;  // dogbone | via_in_pad
};

struct FanoutTrack {
  std::string id;
  std::string ball_id;
  std::string net;
  int stackup_layer = 0;
  std::string layer_id;
  int64_t width_nm = 0;
  std::vector<Vec2> points;
};

struct FanoutFailure {
  std::string ball_id;
  std::string net;
  std::string reason;
  std::vector<std::string> factors;
  Vec2 attempted_site{};
};

struct FanoutResult {
  std::string status;  // SUCCEEDED | PARTIAL | UNKNOWN | INFEASIBLE
  std::vector<FanoutVia> vias;
  std::vector<FanoutTrack> tracks;
  std::vector<FanoutFailure> failures;
  std::vector<std::string> unescaped_balls;
  int required_count = 0;
  int escaped_count = 0;
  int channel_capacity = 0;
  int via_in_pad_count = 0;
  int dogbone_count = 0;
  int64_t search_nodes = 0;
  int64_t seed = 0;
  bool budget_hit = false;
  std::string semantic_hash;
};

FanoutResult fanout_bga(const FanoutInput& input);

}  // namespace mota
