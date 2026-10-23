#include <string>
#include <vector>

#include "mota/types.hpp"
#include "mota/verify.hpp"
#include "test_common.hpp"

namespace {

using motatest::check;

constexpr int64_t kMm = 1'000'000;
int64_t mm(int64_t value) { return value * kMm; }

mota::CopperNode track(const std::string& id, const std::string& net, int layer,
                       mota::Vec2 a, mota::Vec2 b, int64_t width) {
  mota::CopperNode node;
  node.id = "track:" + id;
  node.net = net;
  node.kind = "track";
  node.width_nm = width;
  node.layer_shapes.emplace_back(layer, mota::make_slot(a, b, width / 2));
  return node;
}

mota::CopperNode via(const std::string& id, const std::string& net,
                     mota::Vec2 center, int64_t drill, int64_t pad_outer) {
  mota::CopperNode node;
  node.id = "via:" + id;
  node.net = net;
  node.kind = "via";
  node.drill_nm = drill;
  node.pad_outer_nm = pad_outer;
  node.via_center = center;
  node.via_start = 0;
  node.via_end = 2;
  node.layer_shapes.emplace_back(0, mota::make_circle(center, pad_outer / 2));
  node.layer_shapes.emplace_back(2, mota::make_circle(center, pad_outer / 2));
  return node;
}

mota::DrcRule rule(const std::string& id, const std::string& kind,
                   int64_t value, bool required = true) {
  mota::DrcRule out;
  out.id = id;
  out.kind = kind;
  out.value_nm = value;
  out.required = required;
  return out;
}

std::vector<mota::Vec2> board() {
  return {{0, 0}, {mm(30), 0}, {mm(30), mm(20)}, {0, mm(20)}};
}

bool has_check(const mota::DrcReport& report, const std::string& id,
               const std::string& status) {
  for (const mota::DrcCheck& check : report.checks) {
    if (check.id == id && check.status == status) return true;
  }
  return false;
}

void test_empty_rules_are_not_a_pass() {
  mota::DrcInput input;
  input.board_outline = board();
  input.nodes = {track("T", "N", 0, {0, 0}, {mm(10), 0}, mm(1))};
  const mota::DrcReport report = mota::run_drc(input);
  check(report.status == "NOT_CHECKED", "empty rules are NOT_CHECKED");
  check(!report.review_ready, "empty rules cannot be review ready");
  check(report.diagnostics.size() == 1 && report.diagnostics[0] == "NO_RULES",
        "empty rules emit NO_RULES");
}

void test_clearance_and_short() {
  mota::DrcInput input;
  input.board_outline = board();
  input.nodes = {
      track("A", "N", 0, {0, mm(5)}, {mm(20), mm(5)}, mm(2) / 10),
      track("B", "M", 0, {0, mm(54) / 10}, {mm(20), mm(54) / 10}, mm(2) / 10),
  };
  input.rules = {rule("DRC_CLEARANCE", "clearance", mm(3) / 10)};
  const mota::DrcReport report = mota::run_drc(input);
  check(report.status == "FAIL", "clearance below the rule fails");
  check(has_check(report, "DRC_CLEARANCE", "FAIL"), "clearance check fails");
  const mota::DrcCheck& check_row = report.checks[0];
  check(check_row.checked_objects == 1, "one different-net pair was examined");
  check(check_row.violations.size() == 1, "one clearance violation");
  check(check_row.violations[0].object_refs.size() == 2,
        "violation names both objects");

  input.nodes[1] = track("B", "M", 0, {0, mm(515) / 100}, {mm(20), mm(515) / 100},
                         mm(2) / 10);
  input.rules.push_back(rule("COPPER_SHORTS", "short", 0));
  const mota::DrcReport shorted = mota::run_drc(input);
  check(shorted.status == "FAIL", "touching nets fail");
  check(has_check(shorted, "COPPER_SHORTS", "FAIL"), "short check fails");
  check(shorted.violation_count >= 2, "short is reported by both rules");
}

void test_width_and_via_rules() {
  mota::DrcInput input;
  input.board_outline = board();
  input.nodes = {
      track("T", "N", 0, {mm(2), mm(2)}, {mm(10), mm(2)}, mm(2) / 10),
      via("V", "N", {mm(5), mm(5)}, mm(3) / 10, mm(8) / 10),
  };
  input.rules = {
      rule("DRC_TRACK_WIDTH", "track_width", mm(3) / 10),
      rule("DRC_VIA_DRILL", "via_drill", mm(4) / 10),
      rule("DRC_VIA_RING", "via_annular_ring", mm(3) / 10),
  };
  const mota::DrcReport report = mota::run_drc(input);
  check(report.status == "FAIL", "width/drill/ring violations fail");
  check(has_check(report, "DRC_TRACK_WIDTH", "FAIL"), "track width fails");
  check(has_check(report, "DRC_VIA_DRILL", "FAIL"), "via drill fails");
  check(has_check(report, "DRC_VIA_RING", "FAIL"), "via ring fails");

  input.rules[0].value_nm = mm(2) / 10;
  input.rules[1].value_nm = mm(3) / 10;
  input.rules[2].value_nm = mm(2) / 10;
  const mota::DrcReport passing = mota::run_drc(input);
  check(passing.status == "PASS", "rules pass with exact dimensions");
  check(passing.review_ready, "all required checks pass");
}

void test_board_edge_and_holes() {
  mota::DrcInput input;
  input.board_outline = board();
  input.nodes = {track("EDGE", "N", 0, {mm(5), mm(1) / 10},
                       {mm(15), mm(1) / 10}, mm(2) / 10)};
  input.rules = {rule("DRC_EDGE", "board_edge_clearance", mm(5) / 10)};
  const mota::DrcReport edge = mota::run_drc(input);
  check(edge.status == "FAIL", "copper too close to the board edge fails");

  mota::DrcInput holes;
  holes.board_outline = board();
  holes.nodes = {track("H", "N", 0, {mm(5), mm(11)}, {mm(15), mm(11)},
                       mm(2) / 10)};
  holes.holes = {mota::HoleSpec{"H1", mota::make_circle({mm(10), mm(10)}, mm(5) / 10),
                                false}};
  holes.rules = {rule("DRC_HOLES", "hole_clearance", mm(3) / 10)};
  const mota::DrcReport hole = mota::run_drc(holes);
  check(hole.status == "PASS", "copper keeps the hole clearance");
  holes.rules[0].value_nm = mm(5) / 10;
  const mota::DrcReport tight = mota::run_drc(holes);
  check(tight.status == "FAIL", "copper too close to a hole fails");
}

void test_no_applicable_objects_is_not_checked() {
  mota::DrcInput input;
  input.board_outline = board();
  input.nodes = {track("T", "N", 0, {mm(2), mm(2)}, {mm(10), mm(2)}, mm(2) / 10)};
  input.rules = {rule("DRC_VIA_DRILL", "via_drill", mm(4) / 10)};
  const mota::DrcReport report = mota::run_drc(input);
  check(report.status == "NOT_CHECKED", "rule without vias is NOT_CHECKED");
  check(!report.review_ready, "NOT_CHECKED cannot be review ready");
  check(has_check(report, "DRC_VIA_DRILL", "NOT_CHECKED"),
        "coverage records zero examined objects");
}

void test_optional_failure_is_not_hidden() {
  mota::DrcInput input;
  input.board_outline = board();
  input.nodes = {track("T", "N", 0, {mm(2), mm(2)}, {mm(10), mm(2)}, mm(1) / 10)};
  input.rules = {rule("OPTIONAL_WIDTH", "track_width", mm(3) / 10, false)};
  const mota::DrcReport report = mota::run_drc(input);
  check(report.status == "FAIL", "optional failure still fails the report");
  check(!report.review_ready, "optional failure is not review ready");
}

}  // namespace

int main() {
  test_empty_rules_are_not_a_pass();
  test_clearance_and_short();
  test_width_and_via_rules();
  test_board_edge_and_holes();
  test_no_applicable_objects_is_not_checked();
  test_optional_failure_is_not_hidden();
  std::cout << "drc checks=" << motatest::checks
            << " failures=" << motatest::failures << "\n";
  return motatest::failures == 0 ? 0 : 1;
}
