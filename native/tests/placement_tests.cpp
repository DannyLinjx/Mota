#include <algorithm>
#include <cmath>
#include <string>
#include <vector>

#include "mota/placement.hpp"
#include "mota/types.hpp"
#include "test_common.hpp"

namespace {

using motatest::check;

constexpr int64_t kMm = 1'000'000;
int64_t mm(int64_t value) { return value * kMm; }

mota::PlacementInput board(int64_t width, int64_t height) {
  mota::PlacementInput input;
  input.outline = {{0, 0}, {width, 0}, {width, height}, {0, height}};
  input.board_width_nm = static_cast<double>(width);
  input.board_height_nm = static_cast<double>(height);
  input.rules.grid_nm = mm(1);
  input.rules.edge_clearance_nm = mm(5) / 10;
  input.rules.courtyard_gap_nm = mm(4) / 10;
  input.rules.decoupling_max_nm = mm(6);
  input.rules.congestion_cell_nm = mm(2);
  input.rules.local_rounds = 2;
  input.rules.max_search_nodes = 20'000;
  return input;
}

mota::PlacementComponent component(const std::string& id, int64_t x, int64_t y,
                                   int64_t w, int64_t h, bool fixed = false,
                                   bool critical = false, int pins = 2,
                                   int64_t pitch = 0) {
  mota::PlacementComponent out;
  out.id = id;
  out.reference = id;
  out.x_nm = x;
  out.y_nm = y;
  out.w_nm = w;
  out.h_nm = h;
  out.fixed = fixed;
  out.critical = critical;
  out.pins = pins;
  out.pin_pitch_nm = pitch;
  return out;
}

mota::Aabb courtyard_of(const mota::PlacementComponent& component,
                        int64_t x, int64_t y, int rotation) {
  const bool swapped = ((rotation % 180) + 180) % 180 == 90;
  const int64_t width = swapped ? component.h_nm : component.w_nm;
  const int64_t height = swapped ? component.w_nm : component.h_nm;
  return mota::Aabb{x - width / 2, y - height / 2,
                    x + width / 2, y + height / 2};
}

const mota::PlacementMove* move_of(const mota::PlacementResult& result,
                                   const std::string& id) {
  for (const mota::PlacementMove& move : result.patch) {
    if (move.component_id == id) return &move;
  }
  return nullptr;
}

bool overlaps(const mota::Aabb& a, const mota::Aabb& b, int64_t gap) {
  return a.min_x <= b.max_x + gap && b.min_x <= a.max_x + gap &&
         a.min_y <= b.max_y + gap && b.min_y <= a.max_y + gap;
}

void test_legal_placement() {
  mota::PlacementInput input = board(mm(40), mm(30));
  input.components = {
      component("J1", mm(8), mm(8), mm(10), mm(8), true, true, 4),
      component("U1", mm(20), mm(20), mm(8), mm(5), false, true, 12),
      component("C1", mm(25), mm(20), mm(6), mm(4)),
  };
  input.nets = {{"N1", {"J1", "U1"}}, {"N2", {"U1", "C1"}}};
  const mota::PlacementResult result = mota::place_components(input);
  check(result.status == "SUCCEEDED", "legal board places all components");
  check(result.patch.size() == 3, "patch contains every component");
  check(result.hard_violations.empty(), "no hard violation");
  const mota::PlacementMove* fixed = move_of(result, "J1");
  check(fixed != nullptr && fixed->x_nm == mm(8) && fixed->y_nm == mm(8),
        "fixed connector stays at its position");
  for (size_t i = 0; i < input.components.size(); ++i) {
    const mota::PlacementMove* a = move_of(result, input.components[i].id);
    check(a != nullptr, "component has a patch move");
    if (a == nullptr) continue;
    for (size_t j = i + 1; j < input.components.size(); ++j) {
      const mota::PlacementMove* b = move_of(result, input.components[j].id);
      if (b == nullptr) continue;
      const mota::Aabb ca = courtyard_of(input.components[i], a->x_nm, a->y_nm,
                                         a->rotation_deg);
      const mota::Aabb cb = courtyard_of(input.components[j], b->x_nm, b->y_nm,
                                         b->rotation_deg);
      check(!overlaps(ca, cb, input.rules.courtyard_gap_nm),
            "courtyards keep the required gap");
    }
  }
  check(result.congestion.columns > 0 && result.congestion.rows > 0,
        "congestion map is produced");
  check(result.congestion.max_load > 0, "net demand reaches congestion cells");
  check(!result.congestion.hot_cells.empty(), "hot congestion cells are named");
  check(result.wirelength_nm > 0, "HPWL is reported");
  check(result.search_nodes > 0, "bounded search consumed nodes");
}

void test_keepout_and_fixed() {
  mota::PlacementInput input = board(mm(30), mm(20));
  input.components = {
      component("J1", mm(5), mm(5), mm(8), mm(6), true, true, 4),
      component("U1", mm(25), mm(10), mm(4), mm(4), false, true, 8),
  };
  input.keepouts = {{"K1", mota::Aabb{mm(12), mm(4), mm(20), mm(16)}}};
  const mota::PlacementResult result = mota::place_components(input);
  check(result.status == "SUCCEEDED", "placement avoids a local keepout");
  const mota::PlacementMove* u1 = move_of(result, "U1");
  check(u1 != nullptr, "U1 placed");
  if (u1 != nullptr) {
    const mota::Aabb area =
        courtyard_of(input.components[1], u1->x_nm, u1->y_nm, u1->rotation_deg);
    check(!area.intersects(input.keepouts[0].area),
          "courtyard does not enter the keepout");
  }
  const mota::PlacementMove* j1 = move_of(result, "J1");
  check(j1 != nullptr && j1->x_nm == mm(5) && j1->y_nm == mm(5),
        "fixed connector is not moved");
}

void test_module_region_and_decoupling() {
  mota::PlacementInput input = board(mm(40), mm(30));
  input.components = {
      component("U1", mm(6), mm(6), mm(6), mm(6), true, true, 16),
      component("C1", mm(20), mm(20), mm(2), mm(1), false, true, 2),
  };
  input.components[1].module = "power";
  input.components[1].decoupling_for = {"U1"};
  input.module_regions = {{"power", mota::Aabb{mm(2), mm(2), mm(16), mm(16)}}};
  input.rules.decoupling_max_nm = mm(6);
  const mota::PlacementResult result = mota::place_components(input);
  check(result.status == "SUCCEEDED", "module and decoupling constraints place");
  const mota::PlacementMove* cap = move_of(result, "C1");
  check(cap != nullptr, "decoupling capacitor placed");
  if (cap != nullptr) {
    const mota::Aabb area =
        courtyard_of(input.components[1], cap->x_nm, cap->y_nm, cap->rotation_deg);
    check(input.module_regions[0].area.contains(area),
          "module component stays inside its region");
    const int64_t dx = cap->x_nm - mm(6);
    const int64_t dy = cap->y_nm - mm(6);
    check(dx * dx + dy * dy <= mm(6) * mm(6),
          "decoupling capacitor is within the distance limit");
  }
}

void test_fixed_overlap_is_infeasible() {
  mota::PlacementInput input = board(mm(30), mm(20));
  input.components = {
      component("A", mm(5), mm(5), mm(8), mm(8), true, true, 4),
      component("B", mm(7), mm(7), mm(8), mm(8), true, true, 4),
  };
  const mota::PlacementResult result = mota::place_components(input);
  check(result.status == "INFEASIBLE", "fixed overlap is infeasible");
  check(std::find(result.hard_violations.begin(), result.hard_violations.end(),
                  "FIXED_OVERLAP:A:B") != result.hard_violations.end(),
        "fixed overlap names both objects");
}

void test_fixed_keepout_is_infeasible() {
  mota::PlacementInput input = board(mm(30), mm(20));
  input.components = {
      component("A", mm(10), mm(10), mm(6), mm(6), true, true, 4),
  };
  input.keepouts = {{"K1", mota::Aabb{mm(8), mm(8), mm(12), mm(12)}}};
  const mota::PlacementResult result = mota::place_components(input);
  check(result.status == "INFEASIBLE", "fixed object in keepout is infeasible");
  check(std::find(result.hard_violations.begin(), result.hard_violations.end(),
                  "FIXED_IN_KEEPOUT:A:K1") != result.hard_violations.end(),
        "fixed keepout violation is named");
}

void test_no_space_is_infeasible() {
  mota::PlacementInput input = board(mm(10), mm(10));
  input.components = {component("U1", mm(5), mm(5), mm(2), mm(2))};
  input.keepouts = {{"K1", mota::Aabb{0, 0, mm(10), mm(10)}}};
  const mota::PlacementResult result = mota::place_components(input);
  check(result.status == "INFEASIBLE", "fully blocked board is infeasible");
  check(std::find(result.hard_violations.begin(), result.hard_violations.end(),
                  "NO_LEGAL_PLACEMENT") != result.hard_violations.end(),
        "no legal placement is proven by exhausted candidates");
}

void test_budget_stop_is_not_infeasible() {
  mota::PlacementInput input = board(mm(30), mm(20));
  input.components = {
      component("U1", mm(5), mm(5), mm(4), mm(4)),
      component("U2", mm(15), mm(5), mm(4), mm(4)),
      component("U3", mm(25), mm(5), mm(4), mm(4)),
  };
  input.rules.max_search_nodes = 0;
  const mota::PlacementResult unknown = mota::place_components(input);
  check(unknown.status == "UNKNOWN", "zero node budget is UNKNOWN");
  check(unknown.status != "INFEASIBLE", "budget stop is not infeasible");

  input.rules.max_search_nodes = 1;
  const mota::PlacementResult partial = mota::place_components(input);
  check(partial.status == "PARTIAL", "one node budget returns a partial patch");
  check(partial.status != "INFEASIBLE", "partial is not infeasible");
  check(!partial.unplaced_components.empty(), "partial names unplaced components");
}

void test_hard_keepout_beats_wirelength() {
  mota::PlacementInput input = board(mm(30), mm(20));
  input.components = {
      component("A", mm(5), mm(10), mm(6), mm(6), true, true, 4),
      component("B", mm(25), mm(10), mm(4), mm(4), false, true, 4),
  };
  input.keepouts = {{"K1", mota::Aabb{mm(9), 0, mm(15), mm(20)}}};
  input.nets = {{"N", {"A", "B"}}};
  const mota::PlacementResult result = mota::place_components(input);
  check(result.status == "SUCCEEDED", "placement succeeds around a hard keepout");
  const mota::PlacementMove* b = move_of(result, "B");
  check(b != nullptr, "B placed");
  if (b != nullptr) {
    const mota::Aabb area =
        courtyard_of(input.components[1], b->x_nm, b->y_nm, b->rotation_deg);
    check(!area.intersects(input.keepouts[0].area),
          "wirelength cannot buy a keepout violation");
  }
}

std::vector<mota::PlacementMove> replay(mota::PlacementInput input,
                                        int64_t seed,
                                        mota::PlacementResult* out) {
  input.rules.seed = seed;
  *out = mota::place_components(input);
  return out->patch;
}

void test_seed_replay() {
  mota::PlacementInput input = board(mm(40), mm(30));
  input.components = {
      component("J1", mm(6), mm(6), mm(8), mm(6), true, true, 4),
      component("U1", mm(15), mm(15), mm(6), mm(6), false, true, 12),
      component("U2", mm(22), mm(8), mm(5), mm(5), false, true, 8),
      component("C1", mm(30), mm(20), mm(3), mm(2)),
      component("C2", mm(10), mm(22), mm(3), mm(2)),
  };
  input.nets = {{"N1", {"J1", "U1"}}, {"N2", {"U1", "U2"}},
                {"N3", {"U1", "C1", "C2"}}};
  mota::PlacementResult first;
  mota::PlacementResult second;
  const auto patch_a = replay(input, 42, &first);
  const auto patch_b = replay(input, 42, &second);
  check(first.status == "SUCCEEDED" && second.status == "SUCCEEDED",
        "seed replay succeeds");
  check(first.semantic_hash == second.semantic_hash,
        "same seed yields the same semantic hash");
  check(patch_a.size() == patch_b.size(), "same seed yields the same patch size");
  for (size_t i = 0; i < std::min(patch_a.size(), patch_b.size()); ++i) {
    check(patch_a[i].component_id == patch_b[i].component_id &&
              patch_a[i].x_nm == patch_b[i].x_nm &&
              patch_a[i].y_nm == patch_b[i].y_nm &&
              patch_a[i].rotation_deg == patch_b[i].rotation_deg,
          "same seed yields the same geometry");
  }
  mota::PlacementResult other;
  replay(input, 43, &other);
  check(other.status == "SUCCEEDED", "different seed still produces a legal result");
  check(other.semantic_hash != first.semantic_hash,
        "seed is part of the semantic hash");
}

void test_congestion_and_escape_feedback() {
  mota::PlacementInput input = board(mm(40), mm(30));
  input.components = {
      component("U1", mm(20), mm(15), mm(10), mm(10), false, true, 100,
                mm(5) / 10),
      component("U2", mm(28), mm(15), mm(6), mm(6), false, false, 8),
  };
  input.nets = {{"N", {"U1", "U2"}}};
  const mota::PlacementResult result = mota::place_components(input);
  check(result.status == "SUCCEEDED", "fine-pitch placement succeeds");
  check(result.escape_pressure > 0, "escape pressure is reported");
  check(result.congestion.max_load > 0, "congestion load is reported");
  check(result.congestion.hot_cells.size() <= 10, "hot cell list is bounded");
}

}  // namespace

int main() {
  test_legal_placement();
  test_keepout_and_fixed();
  test_module_region_and_decoupling();
  test_fixed_overlap_is_infeasible();
  test_fixed_keepout_is_infeasible();
  test_no_space_is_infeasible();
  test_budget_stop_is_not_infeasible();
  test_hard_keepout_beats_wirelength();
  test_seed_replay();
  test_congestion_and_escape_feedback();
  std::cout << "placement checks=" << motatest::checks
            << " failures=" << motatest::failures << "\n";
  return motatest::failures == 0 ? 0 : 1;
}
