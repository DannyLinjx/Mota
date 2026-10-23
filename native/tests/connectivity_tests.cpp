#include <string>
#include <vector>

#include "mota/connectivity.hpp"
#include "mota/types.hpp"
#include "test_common.hpp"

namespace {

using motatest::check;

constexpr int64_t kMm = 1'000'000;
int64_t mm(int64_t value) { return value * kMm; }

mota::CopperNode pad(const std::string& id, const std::string& net, int layer,
                     const mota::Shape& shape) {
  mota::CopperNode node;
  node.id = "pad:" + id;
  node.net = net;
  node.kind = "pad";
  node.component_id = "U1";
  node.pin = id;
  node.layer_shapes.emplace_back(layer, shape);
  return node;
}

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
                     mota::Vec2 center, int start, int end, bool plated = true) {
  mota::CopperNode node;
  node.id = "via:" + id;
  node.net = net;
  node.kind = "via";
  node.plated = plated;
  node.via_start = start;
  node.via_end = end;
  node.drill_nm = 400'000;
  node.pad_outer_nm = mm(1);
  node.via_center = center;
  if (plated) {
    for (int layer = start; layer <= end; ++layer) {
      node.layer_shapes.emplace_back(
          layer, mota::make_circle(center, mm(1) / 2));
    }
  }
  return node;
}

mota::CopperNode fill(const std::string& id, const std::string& net, int layer,
                      const std::string& zone, mota::Vec2 a, mota::Vec2 b) {
  mota::CopperNode node;
  node.id = "fill:" + id;
  node.net = net;
  node.kind = "fill";
  node.is_fill = true;
  node.anchored = true;
  node.zone_id = zone;
  node.layer_shapes.emplace_back(
      layer, mota::make_polygon({{a.x, a.y}, {b.x, a.y}, {b.x, b.y}, {a.x, b.y}}));
  return node;
}

bool has_issue(const mota::ConnectivityReport& report, const std::string& code) {
  for (const mota::ConnectivityIssue& issue : report.issues) {
    if (issue.code == code) return true;
  }
  return false;
}

mota::ConnectivityInput base_input() {
  mota::ConnectivityInput input;
  input.nodes = {
      pad("P1", "N", 0, mota::make_circle({0, 0}, mm(1))),
      pad("P2", "N", 0, mota::make_circle({mm(10), 0}, mm(1))),
      track("T1", "N", 0, {0, 0}, {mm(10), 0}, mm(1)),
  };
  return input;
}

void test_connected_net() {
  const mota::ConnectivityReport report = mota::analyze_connectivity(base_input());
  check(report.nets.size() == 1, "one net");
  check(report.nets[0].groups == 1, "one copper group");
  check(report.nets[0].u == 0, "U is zero");
  check(report.u_total == 0 && report.short_count == 0, "no open or short");
}

void test_broken_track_is_an_open() {
  mota::ConnectivityInput input = base_input();
  input.nodes[2] = track("T1", "N", 0, {0, 0}, {mm(4), 0}, mm(1));
  const mota::ConnectivityReport report = mota::analyze_connectivity(input);
  check(report.nets[0].groups == 2, "broken copper has two groups");
  check(report.nets[0].u == 1, "one merge is required");
  check(report.open_count == 1, "one open net");
  check(has_issue(report, "NET_OPEN"), "NET_OPEN is reported");
  check(report.nets[0].unconnected_endpoints.size() == 1 &&
            report.nets[0].unconnected_endpoints[0] == "pad:P2",
        "unconnected endpoint names the far pad");
}

void test_different_net_touch_is_a_short() {
  mota::ConnectivityInput input = base_input();
  input.nodes.push_back(pad("P3", "M", 0, mota::make_circle({mm(5), 0}, mm(1))));
  const mota::ConnectivityReport report = mota::analyze_connectivity(input);
  check(report.short_count == 1, "one different-net contact");
  check(has_issue(report, "COPPER_SHORT"), "COPPER_SHORT is reported");
}

void test_net_tie_is_explicit() {
  mota::ConnectivityInput input;
  input.nodes = {
      pad("A", "GND", 0, mota::make_circle({0, 0}, mm(1))),
      pad("B", "GND_SENSE", 0, mota::make_circle({mm(5), 0}, mm(1))),
      track("T", "GND", 0, {0, 0}, {mm(5), 0}, mm(1)),
  };
  input.net_ties = {mota::NetTieSpec{"NT1", "track:T", "pad:B", true}};
  const mota::ConnectivityReport tied = mota::analyze_connectivity(input);
  check(tied.short_count == 0, "allowed net-tie is not a short");
  check(tied.net_ties_used.size() == 1, "net-tie is recorded");

  input.net_ties[0].allowed = false;
  const mota::ConnectivityReport blocked = mota::analyze_connectivity(input);
  check(blocked.short_count == 1, "disallowed net-tie remains a short");
  check(has_issue(blocked, "NET_TIE_NOT_ALLOWED"), "disallowed tie is reported");
}

void test_npth_never_connects_and_via_does() {
  mota::ConnectivityInput input;
  input.nodes = {
      pad("PF", "N", 0, mota::make_circle({0, 0}, mm(1))),
      pad("PB", "N", 2, mota::make_circle({0, 0}, mm(1))),
  };
  const mota::ConnectivityReport separated = mota::analyze_connectivity(input);
  check(separated.nets[0].groups == 2, "layers are separate without a via");

  input.nodes.push_back(via("V", "N", {0, 0}, 0, 2, true));
  const mota::ConnectivityReport connected = mota::analyze_connectivity(input);
  check(connected.nets[0].groups == 1, "plated via joins the layers");

  mota::ConnectivityInput npth = input;
  npth.nodes.back() = via("V", "N", {0, 0}, 0, 2, false);
  const mota::ConnectivityReport blocked = mota::analyze_connectivity(npth);
  check(blocked.nets[0].groups == 2, "NPTH does not join layers");
  check(has_issue(blocked, "VIA_SPAN_INVALID"), "NPTH net via is rejected");
}

void test_via_span_open() {
  mota::ConnectivityInput input;
  input.nodes = {
      via("V", "N", {mm(5), 0}, 0, 2, true),
      track("TB", "N", 4, {mm(4), 0}, {mm(6), 0}, mm(1)),
  };
  const mota::ConnectivityReport report = mota::analyze_connectivity(input);
  check(has_issue(report, "VIA_SPAN_OPEN"),
        "same-net copper outside the via span is reported");
}

void test_thermal_and_plane_islands() {
  mota::ConnectivityInput input;
  input.nodes = {
      pad("P1", "GND", 0, mota::make_circle({mm(5), mm(5)}, mm(1))),
  };
  input.zones.push_back(mota::ZoneConnectivityInfo{
      "Z1", "GND", 0,
      {{0, 0}, {mm(10), 0}, {mm(10), mm(10)}, {0, mm(10)}},
      1, mm(2)});
  const mota::ConnectivityReport isolated = mota::analyze_connectivity(input);
  check(has_issue(isolated, "THERMAL_DISCONNECT"),
        "pad inside a zone without fill is a thermal disconnect");
  check(has_issue(isolated, "PLANE_ISLAND"), "removed island is reported");

  input.nodes.push_back(fill("Z1:0", "GND", 0, "Z1", {mm(3), mm(3)},
                             {mm(7), mm(7)}));
  const mota::ConnectivityReport connected = mota::analyze_connectivity(input);
  check(!has_issue(connected, "THERMAL_DISCONNECT"),
        "connected fill clears the thermal issue");
}

void test_missing_endpoint_and_u_total() {
  mota::ConnectivityInput input;
  input.nodes = {
      pad("P1", "N", 0, mota::make_circle({0, 0}, mm(1))),
      pad("P2", "N", 0, mota::make_circle({mm(10), 0}, mm(1))),
      pad("P3", "N", 0, mota::make_circle({mm(20), 0}, mm(1))),
  };
  input.required_endpoints = {"pad:P2", "pad:missing"};
  const mota::ConnectivityReport report = mota::analyze_connectivity(input);
  check(report.nets[0].u == 2, "three groups need two merges");
  check(report.u_total == 2, "U total accumulates merges");
  check(report.missing_endpoint_count == 1, "missing endpoint is fatal");
  check(has_issue(report, "MISSING_ENDPOINT"), "missing endpoint is reported");
}

std::vector<mota::Vec2> rect_outline(int64_t x0, int64_t y0, int64_t x1,
                                     int64_t y1) {
  return {{x0, y0}, {x1, y0}, {x1, y1}, {x0, y1}};
}

mota::ZoneSpec zone_spec(const std::string& id, const std::string& net,
                         std::vector<mota::Vec2> outline) {
  mota::ZoneSpec zone;
  zone.id = id;
  zone.net = net;
  zone.layer = "F.Cu";
  zone.outline = std::move(outline);
  zone.clearance_nm = mm(2) / 10;
  zone.thermal_gap_nm = mm(2) / 10;
  zone.thermal_spoke_width_nm = mm(2) / 10;
  return zone;
}

bool rect_contains(const mota::FillRect& rect, int64_t x, int64_t y) {
  return x >= rect.x0 && x <= rect.x1 && y >= rect.y0 && y <= rect.y1;
}

void test_zone_fill_and_thermal() {
  const std::vector<mota::Vec2> board =
      rect_outline(0, 0, mm(20), mm(20));
  mota::ZoneSpec zone = zone_spec("Z1", "GND",
                                  rect_outline(mm(2), mm(2), mm(18), mm(18)));
  std::vector<mota::ZoneAnchor> anchors = {
      {"pad:P1", mota::make_circle({mm(10), mm(10)}, mm(1)), true},
      {"pad:P2", mota::make_circle({mm(5), mm(5)}, mm(1)), true},
  };
  const mota::ZoneFillResult fill = mota::fill_zone(zone, board, {}, anchors, {});
  check(!fill.non_rectilinear, "rectilinear zone is supported");
  check(!fill.components.empty(), "zone produces copper");
  check(fill.kept_area_nm2 > 0, "zone has a positive kept area");
  bool anchored = true;
  bool has_spokes = false;
  bool covered_pad = false;
  for (const mota::FillComponent& component : fill.components) {
    anchored = anchored && component.anchored;
    has_spokes = has_spokes || !component.spokes.empty();
    for (const mota::FillRect& rect : component.rects) {
      if (rect_contains(rect, mm(10), mm(10))) covered_pad = true;
    }
  }
  check(anchored, "all kept components are anchored");
  check(has_spokes, "thermal spokes are generated");
  check(!covered_pad, "thermal gap keeps main fill off the pad centre");
}

void test_zone_island_is_removed() {
  const std::vector<mota::Vec2> board =
      rect_outline(0, 0, mm(20), mm(20));
  const std::vector<mota::Vec2> split = {
      {0, 0}, {mm(98) / 10, 0}, {mm(98) / 10, mm(20)},
      {mm(102) / 10, mm(20)}, {mm(102) / 10, 0}, {mm(20), 0},
      {mm(20), mm(20)}, {0, mm(20)}};
  mota::ZoneSpec zone = zone_spec("Z2", "GND", split);
  std::vector<mota::ZoneAnchor> anchors = {
      {"pad:P1", mota::make_circle({mm(5), mm(10)}, mm(1)), true},
  };
  const mota::ZoneFillResult fill = mota::fill_zone(zone, board, {}, anchors, {});
  check(fill.islands_removed >= 1, "unanchored plane island is removed");
  check(fill.removed_area_nm2 > 0, "removed island area is recorded");
  check(fill.components.size() == 1, "only the anchored island remains");
  check(fill.components[0].anchored, "kept island is anchored");
}

void test_zone_keepout_and_hole() {
  const std::vector<mota::Vec2> board =
      rect_outline(0, 0, mm(20), mm(20));
  mota::ZoneSpec zone = zone_spec("Z3", "GND",
                                  rect_outline(mm(2), mm(2), mm(18), mm(18)));
  std::vector<mota::ZoneAnchor> anchors = {
      {"pad:P1", mota::make_circle({mm(3), mm(3)}, mm(1)), true},
  };
  mota::ZoneObstacle keepout;
  keepout.id = "keepout:K1";
  keepout.expand_clearance = false;
  keepout.shape = mota::make_polygon(rect_outline(mm(8), mm(8), mm(12), mm(12)));
  mota::ZoneFillResult fill = mota::fill_zone(zone, board, {keepout}, anchors, {});
  bool covers_keepout = false;
  for (const mota::FillComponent& component : fill.components) {
    for (const mota::FillRect& rect : component.rects) {
      if (rect_contains(rect, mm(10), mm(10))) covers_keepout = true;
    }
  }
  check(!covers_keepout, "keepout is removed from the fill");

  mota::ZoneObstacle hole;
  hole.id = "hole:H1";
  hole.expand_clearance = false;
  hole.shape = mota::make_circle({mm(10), mm(10)}, mm(1));
  fill = mota::fill_zone(zone, board, {hole}, anchors, {});
  bool covers_hole = false;
  for (const mota::FillComponent& component : fill.components) {
    for (const mota::FillRect& rect : component.rects) {
      if (rect_contains(rect, mm(10), mm(10))) covers_hole = true;
    }
  }
  check(!covers_hole, "NPTH hole is removed from the fill");
}

void test_zone_priority() {
  const std::vector<mota::Vec2> board =
      rect_outline(0, 0, mm(20), mm(20));
  mota::ZoneSpec high = zone_spec("ZH", "HIGH",
                                  rect_outline(0, 0, mm(12), mm(12)));
  high.priority = 10;
  mota::ZoneSpec low = zone_spec("ZL", "LOW",
                                 rect_outline(mm(8), mm(8), mm(20), mm(20)));
  low.priority = 0;
  const mota::ZoneFillResult high_fill = mota::fill_zone(
      high, board, {},
      {{"pad:PH", mota::make_circle({mm(2), mm(2)}, mm(1)), true}}, {});
  std::vector<mota::FillRect> committed;
  for (const mota::FillComponent& component : high_fill.components) {
    committed.insert(committed.end(), component.rects.begin(),
                     component.rects.end());
  }
  const mota::ZoneFillResult low_fill = mota::fill_zone(
      low, board, {},
      {{"pad:PL", mota::make_circle({mm(18), mm(18)}, mm(1)), true}},
      committed);
  bool low_covers_high = false;
  for (const mota::FillComponent& component : low_fill.components) {
    for (const mota::FillRect& rect : component.rects) {
      if (rect_contains(rect, mm(10), mm(10))) low_covers_high = true;
    }
  }
  check(!low_covers_high, "lower-priority fill does not overwrite higher fill");
}

void test_non_rectilinear_zone_is_rejected() {
  const std::vector<mota::Vec2> board =
      rect_outline(0, 0, mm(20), mm(20));
  mota::ZoneSpec zone = zone_spec(
      "ZT", "GND", {{mm(2), mm(2)}, {mm(18), mm(2)}, {mm(10), mm(18)}});
  const mota::ZoneFillResult fill = mota::fill_zone(
      zone, board, {},
      {{"pad:P1", mota::make_circle({mm(10), mm(10)}, mm(1)), true}}, {});
  check(fill.non_rectilinear, "triangle zone is not silently approximated");
  check(fill.components.empty(), "non-rectilinear zone produces no fill");
  check(fill.diagnostic == "ZONE_NON_RECTILINEAR",
        "non-rectilinear zone diagnostic");
}

}  // namespace

int main() {
  test_connected_net();
  test_broken_track_is_an_open();
  test_different_net_touch_is_a_short();
  test_net_tie_is_explicit();
  test_npth_never_connects_and_via_does();
  test_via_span_open();
  test_thermal_and_plane_islands();
  test_missing_endpoint_and_u_total();
  test_zone_fill_and_thermal();
  test_zone_island_is_removed();
  test_zone_keepout_and_hole();
  test_zone_priority();
  test_non_rectilinear_zone_is_rejected();
  std::cout << "connectivity checks=" << motatest::checks
            << " failures=" << motatest::failures << "\n";
  return motatest::failures == 0 ? 0 : 1;
}
