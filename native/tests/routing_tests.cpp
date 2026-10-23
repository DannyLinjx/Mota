#include <string>
#include <vector>

#include "mota/routing.hpp"
#include "mota/types.hpp"
#include "test_common.hpp"

namespace {

using motatest::check;

constexpr int64_t kMm = 1'000'000;
int64_t mm(int64_t value) { return value * kMm; }

mota::RoutingInput board(int layers) {
  mota::RoutingInput input;
  input.outline = {{0, 0}, {mm(40), 0}, {mm(40), mm(30)}, {0, mm(30)}};
  input.width_nm = mm(40);
  input.height_nm = mm(30);
  for (int layer = 0; layer < layers; ++layer) {
    input.copper_layers.push_back(layer);
    if (layers == 2) {
      input.copper_layer_ids.push_back(layer == 0 ? "F.Cu" : "B.Cu");
    } else if (layer == 0) {
      input.copper_layer_ids.push_back("F.Cu");
    } else if (layer == layers - 1) {
      input.copper_layer_ids.push_back("B.Cu");
    } else {
      input.copper_layer_ids.push_back("In" + std::to_string(layer) + ".Cu");
    }
  }
  input.rules.grid_nm = mm(1);
  input.rules.track_width_nm = 300'000;
  input.rules.clearance_nm = 200'000;
  input.rules.edge_clearance_nm = 500'000;
  input.rules.via_drill_nm = 400'000;
  input.rules.via_pad_nm = 800'000;
  input.rules.via_cost_factor = 6.0;
  input.rules.corridor_penalty = 0.5;
  input.rules.max_rounds = 3;
  input.rules.max_expansions = 200'000;
  input.rules.max_grid_cells = 8'000'000;
  input.rules.max_millis = 5'000;
  input.rules.seed = 1;
  return input;
}

mota::RoutingPad pad(const std::string& id, const std::string& net, int64_t x,
                     int64_t y, int layers, bool smd = false) {
  mota::RoutingPad out;
  out.id = id;
  out.net = net;
  out.component_id = "U1";
  out.pin = id;
  out.plated = true;
  const int count = smd ? 1 : layers;
  for (int layer = 0; layer < count; ++layer) {
    out.layer_shapes.emplace_back(layer,
                                  mota::make_circle({x, y}, 800'000));
  }
  return out;
}

std::vector<mota::Vec2> keepout_outline(int64_t x0, int64_t y0, int64_t x1,
                                        int64_t y1) {
  return {{x0, y0}, {x1, y0}, {x1, y1}, {x0, y1}};
}

bool has_locked_track(const mota::RoutingResult& result,
                      const std::string& id) {
  for (const mota::RoutedTrack& track : result.tracks) {
    if (track.id == id) return track.locked;
  }
  return false;
}

bool track_enters(const mota::RoutingResult& result, const mota::Aabb& area) {
  for (const mota::RoutedTrack& track : result.tracks) {
    for (const mota::Vec2& point : track.points) {
      if (point.x > area.min_x && point.x < area.max_x &&
          point.y > area.min_y && point.y < area.max_y) {
        return true;
      }
    }
  }
  return false;
}

void test_two_layer_three_terminal() {
  mota::RoutingInput input = board(2);
  input.pads = {
      pad("P1", "N", mm(5), mm(5), 2),
      pad("P2", "N", mm(35), mm(5), 2),
      pad("P3", "N", mm(20), mm(20), 2),
  };
  input.nets = {{"N", {"P1", "P2", "P3"}, false}};
  const mota::RoutingResult result = mota::route_board(input);
  check(result.status == "SUCCEEDED", "two-layer three-terminal net routes");
  check(result.u_total == 0, "three terminals are connected");
  check(!result.tracks.empty(), "route patch contains tracks");
  check(result.net_reports.size() == 1 && result.net_reports[0].routed,
        "net report is routed");
  check(result.grid_layers == 2, "grid keeps both copper layers");
  check(result.semantic_hash.size() == 64, "semantic hash is produced");
}

void test_two_layer_obstacle() {
  mota::RoutingInput input = board(2);
  input.pads = {
      pad("P1", "N", mm(5), mm(10), 2),
      pad("P2", "N", mm(35), mm(10), 2),
  };
  input.nets = {{"N", {"P1", "P2"}, false}};
  input.keepouts = {
      {"K1", 0, keepout_outline(mm(15), mm(10), mm(25), mm(20))},
      {"K2", 1, keepout_outline(mm(15), mm(10), mm(25), mm(20))},
  };
  const mota::RoutingResult result = mota::route_board(input);
  check(result.status == "SUCCEEDED", "route goes around a keepout");
  check(result.u_total == 0, "obstacle net is connected");
  check(!track_enters(result, mota::Aabb{mm(15), mm(10), mm(25), mm(20)}),
        "no track point enters the keepout");
}

void test_two_nets_cross() {
  mota::RoutingInput input = board(2);
  input.pads = {
      pad("A1", "A", mm(5), mm(15), 2),
      pad("A2", "A", mm(35), mm(15), 2),
      pad("B1", "B", mm(20), mm(5), 2),
      pad("B2", "B", mm(20), mm(25), 2),
  };
  input.nets = {{"A", {"A1", "A2"}, false}, {"B", {"B1", "B2"}, false}};
  const mota::RoutingResult result = mota::route_board(input);
  check(result.status == "SUCCEEDED", "crossing nets both route");
  check(result.u_total == 0, "both crossing nets are connected");
  check(result.short_count == 0, "crossing nets do not short");
  check(result.via_count >= 1, "a layer change uses a legal via");
  check(result.net_reports.size() == 2, "both requested nets stay in the report");
}

void test_four_layer_smd() {
  mota::RoutingInput input = board(4);
  input.pads = {
      pad("P1", "N", mm(5), mm(10), 4, true),
      pad("P2", "N", mm(35), mm(10), 4, true),
  };
  input.nets = {{"N", {"P1", "P2"}, false}};
  input.keepouts = {
      {"K1", 0, keepout_outline(mm(15), 0, mm(25), mm(30))},
  };
  const mota::RoutingResult result = mota::route_board(input);
  check(result.status == "SUCCEEDED", "four-layer SMD net routes on inner layers");
  check(result.u_total == 0, "four-layer net is connected");
  check(result.grid_layers == 4, "four copper layers are modelled");
  check(result.via_count >= 1, "four-layer route uses a via");
}

void test_six_layer_smd() {
  mota::RoutingInput input = board(6);
  input.pads = {
      pad("P1", "N", mm(5), mm(10), 6, true),
      pad("P2", "N", mm(35), mm(10), 6, true),
  };
  input.nets = {{"N", {"P1", "P2"}, false}};
  input.keepouts = {
      {"K1", 0, keepout_outline(mm(15), 0, mm(25), mm(30))},
      {"K2", 5, keepout_outline(mm(15), 0, mm(25), mm(30))},
  };
  const mota::RoutingResult result = mota::route_board(input);
  check(result.status == "SUCCEEDED", "six-layer SMD net routes");
  check(result.u_total == 0, "six-layer net is connected");
  check(result.grid_layers == 6, "six copper layers are modelled");
  check(result.via_count >= 1, "six-layer route uses a via");
}

void test_locked_track_preserved() {
  mota::RoutingInput input = board(2);
  input.pads = {
      pad("N1", "N", mm(5), mm(5), 2),
      pad("N2", "N", mm(15), mm(5), 2),
      pad("M1", "M", mm(5), mm(20), 2),
      pad("M2", "M", mm(35), mm(20), 2),
  };
  input.nets = {{"N", {"N1", "N2"}, true}, {"M", {"M1", "M2"}, false}};
  mota::RoutingTrack locked;
  locked.id = "LOCK1";
  locked.net = "N";
  locked.stackup_layer = 0;
  locked.width_nm = 300'000;
  locked.points = {{mm(5), mm(5)}, {mm(15), mm(5)}};
  locked.locked = true;
  input.tracks = {locked};
  const mota::RoutingResult result = mota::route_board(input);
  check(result.status == "SUCCEEDED", "locked net plus routed net succeeds");
  check(result.u_total == 0, "locked copper keeps net N connected");
  check(has_locked_track(result, "LOCK1"), "locked track is preserved");
  check(result.net_reports.size() == 2, "both nets remain in the denominator");
}

void test_disconnected_board_is_infeasible() {
  mota::RoutingInput input = board(2);
  input.pads = {
      pad("P1", "N", mm(5), mm(10), 2),
      pad("P2", "N", mm(35), mm(10), 2),
  };
  input.nets = {{"N", {"P1", "P2"}, false}};
  input.keepouts = {
      {"K1", 0, keepout_outline(mm(20), 0, mm(21), mm(30))},
      {"K2", 1, keepout_outline(mm(20), 0, mm(21), mm(30))},
  };
  const mota::RoutingResult result = mota::route_board(input);
  check(result.status == "INFEASIBLE", "split board proves no path");
  check(result.u_total > 0, "unrouted net stays in U");
  check(result.unrouted_nets.size() == 1, "unrouted net stays in the list");
}

void test_budget_is_unknown() {
  mota::RoutingInput input = board(2);
  input.pads = {
      pad("P1", "N", mm(5), mm(10), 2),
      pad("P2", "N", mm(35), mm(10), 2),
  };
  input.nets = {{"N", {"P1", "P2"}, false}};
  input.rules.max_expansions = 0;
  const mota::RoutingResult result = mota::route_board(input);
  check(result.status == "UNKNOWN", "zero expansion budget is UNKNOWN");
  check(result.status != "INFEASIBLE", "budget exhaustion is not infeasibility");
}

void test_seed_replay() {
  mota::RoutingInput input = board(2);
  input.pads = {
      pad("A1", "A", mm(5), mm(15), 2),
      pad("A2", "A", mm(35), mm(15), 2),
      pad("B1", "B", mm(20), mm(5), 2),
      pad("B2", "B", mm(20), mm(25), 2),
  };
  input.nets = {{"A", {"A1", "A2"}, false}, {"B", {"B1", "B2"}, false}};
  const mota::RoutingResult first = mota::route_board(input);
  const mota::RoutingResult second = mota::route_board(input);
  check(first.status == "SUCCEEDED" && second.status == "SUCCEEDED",
        "seed replay succeeds");
  check(first.semantic_hash == second.semantic_hash,
        "same seed yields the same semantic hash");
  check(first.tracks.size() == second.tracks.size(),
        "same seed yields the same track count");
  check(first.vias.size() == second.vias.size(),
        "same seed yields the same via count");
  for (size_t i = 0; i < std::min(first.tracks.size(), second.tracks.size());
       ++i) {
    check(first.tracks[i].points.size() == second.tracks[i].points.size(),
          "same seed yields the same point count");
  }
}

}  // namespace

int main() {
  test_two_layer_three_terminal();
  test_two_layer_obstacle();
  test_two_nets_cross();
  test_four_layer_smd();
  test_six_layer_smd();
  test_locked_track_preserved();
  test_disconnected_board_is_infeasible();
  test_budget_is_unknown();
  test_seed_replay();
  std::cout << "routing checks=" << motatest::checks
            << " failures=" << motatest::failures << "\n";
  return motatest::failures == 0 ? 0 : 1;
}
