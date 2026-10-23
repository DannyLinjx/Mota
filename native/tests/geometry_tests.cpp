#include "test_entry.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <random>
#include <string>
#include <vector>

#ifdef __APPLE__
#include <sys/resource.h>
#endif

#include "mota/board.hpp"
#include "mota/rtree.hpp"
#include "mota/shapes.hpp"
#include "mota/types.hpp"
#include "test_common.hpp"

namespace {

using motatest::check;
using motatest::check_near;
using motatest::check_throws;

constexpr int64_t kMm = 1'000'000;
int64_t mm(int64_t value) { return value * kMm; }

mota::Json g_rtree_metrics = mota::Json::object();

int64_t rss_bytes() {
#ifdef __APPLE__
  struct rusage usage {};
  getrusage(RUSAGE_SELF, &usage);
  return static_cast<int64_t>(usage.ru_maxrss);
#else
  return 0;
#endif
}

void test_checked_arithmetic() {
  check_throws([] { mota::checked_add(INT64_MAX, 1); },
               mota::ErrorCode::Overflow, "int64 add overflow is rejected");
  check_throws([] { mota::checked_mul(INT64_MAX / 2, 3); },
               mota::ErrorCode::Overflow, "int64 mul overflow is rejected");
  check_throws([] { mota::validate_point({mota::kMaxCoordinateNm + 1, 0}); },
               mota::ErrorCode::CoordinateOutOfRange,
               "coordinate beyond the declared range is rejected");
  mota::validate_point({mota::kMaxCoordinateNm, -mota::kMaxCoordinateNm});
  const mota::SqrtResult exact = mota::isqrt_round(25);
  check(exact.value == 5 && exact.exact, "sqrt(25) is exact 5");
  const mota::SqrtResult nearest = mota::isqrt_round(3);
  check(nearest.value == 2 && !nearest.exact, "sqrt(3) rounds to 2");
  bool exact_flag = false;
  const int64_t diagonal = mota::point_point_distance(
      {-mota::kMaxCoordinateNm, -mota::kMaxCoordinateNm},
      {mota::kMaxCoordinateNm, mota::kMaxCoordinateNm}, exact_flag);
  check_near(diagonal, 2'828'427'124'746'190LL, 1'000,
             "extreme diagonal distance stays in int64");
}

void test_transform() {
  mota::Transform top;
  top.origin = {mm(10), mm(20)};
  const mota::Vec2 rotated = top.apply({mm(1), mm(0)});
  check(rotated.x == mm(11) && rotated.y == mm(20), "top rotation 0");

  top.rotation_deg = 90;
  const mota::Vec2 quarter = top.apply({mm(1), mm(0)});
  check(quarter.x == mm(10) && quarter.y == mm(21), "top rotation 90");

  mota::Transform bottom;
  bottom.origin = {0, 0};
  bottom.bottom = true;
  const mota::Vec2 mirrored = bottom.apply({mm(1), mm(2)});
  check(mirrored.x == -mm(1) && mirrored.y == mm(2), "bottom mirror x");

  bottom.rotation_deg = 90;
  const mota::Vec2 mirrored_quarter = bottom.apply({mm(1), mm(2)});
  check(mirrored_quarter.x == -mm(2) && mirrored_quarter.y == -mm(1),
        "bottom mirror then rotation 90");

  mota::Transform arbitrary;
  arbitrary.rotation_deg = 37;
  const mota::Vec2 tilted = arbitrary.apply({mm(1), 0});
  check_near(tilted.x, 798'636, 2, "37 degree x");
  check_near(tilted.y, 601'815, 2, "37 degree y");
  check(arbitrary.rounding_error_nm() == 1,
        "arbitrary rotation declares a 1 nm bound");
  check(top.rounding_error_nm() == 0, "90 degree rotation is exact");

  const mota::Shape rect = mota::make_round_rect({0, 0}, mm(10), mm(5), mm(2));
  const auto swapped = mota::transform_shape(rect, top);
  check(swapped.shape.kind == mota::ShapeKind::RoundRect,
        "90 degree round rect stays analytic");
  check(swapped.shape.half_w == mm(5) && swapped.shape.half_h == mm(10),
        "90 degree round rect swaps half extents");

  const mota::Shape arc = mota::make_arc({0, 0}, mm(10), 0, 1'570'796, 0);
  const auto mirrored_arc = mota::transform_shape(arc, bottom);
  check(mirrored_arc.shape.kind == mota::ShapeKind::Arc,
        "arc transform keeps an arc");
  const mota::DistanceResult arc_hit =
      mota::shape_distance(mirrored_arc.shape, mota::make_point({-mm(10), 0}));
  check(arc_hit.distance_nm <= arc_hit.error_nm + mirrored_arc.error_nm,
        "bottom mirror maps the arc quadrant within the declared bound");
  mota::Transform rotated_arc_transform;
  rotated_arc_transform.rotation_deg = 90;
  const auto rotated_shape = mota::transform_shape(arc, rotated_arc_transform);
  const mota::DistanceResult rotated_arc =
      mota::shape_distance(rotated_shape.shape, mota::make_point({-mm(10), 0}));
  check(rotated_arc.distance_nm <= rotated_arc.error_nm + rotated_shape.error_nm,
        "rotation 90 maps the arc quadrant within the declared bound");
}

void test_distances() {
  bool exact = false;
  check_near(mota::point_point_distance({0, 0}, {mm(3), mm(4)}, exact), mm(5),
             0, "3-4-5 point distance");
  check(exact, "3-4-5 distance is exact");

  check_near(mota::point_segment_distance({0, 0}, {mm(10), -mm(5)},
                                          {mm(10), mm(5)}, exact),
             mm(10), 0, "perpendicular point-segment distance");
  check(exact, "perpendicular distance is exact");

  const mota::Shape a = mota::make_segment({0, 0}, {mm(10), mm(10)});
  const mota::Shape b = mota::make_segment({0, mm(10)}, {mm(10), 0});
  check(mota::shape_distance(a, b).distance_nm == 0,
        "crossing segments intersect");

  const mota::Shape circle = mota::make_circle({0, 0}, mm(5));
  check(mota::shape_distance(circle, mota::make_point({mm(3), mm(4)})).distance_nm == 0,
        "point on the circle boundary intersects");
  check_near(mota::shape_distance(circle, mota::make_point({mm(6), mm(8)}))
                 .distance_nm,
             mm(5), 1, "point outside circle");

  const mota::Shape rect = mota::make_round_rect({0, 0}, mm(10), mm(5), mm(2));
  check(mota::shape_distance(rect, mota::make_point({mm(10), 0})).distance_nm == 0,
        "rounded rect boundary point");
  check_near(mota::shape_distance(rect, mota::make_point({mm(13), 0})).distance_nm,
             mm(3), 1, "rounded rect outside distance");
  check_near(mota::shape_distance(rect, mota::make_point({mm(20), mm(20)}))
                 .distance_nm,
             18'808'652, 2, "rounded rect diagonal distance");

  const mota::Shape slot = mota::make_slot({0, 0}, {mm(10), 0}, mm(2));
  check_near(mota::shape_distance(slot, mota::make_point({mm(5), mm(5)}))
                 .distance_nm,
             mm(3), 1, "slot distance");
  check(mota::shape_distance(slot, mota::make_point({mm(5), 0})).distance_nm == 0,
        "slot interior");

  const mota::Shape square = mota::make_polygon(
      {{-mm(5), -mm(5)}, {mm(5), -mm(5)}, {mm(5), mm(5)}, {-mm(5), mm(5)}});
  check(mota::shape_distance(square, mota::make_point({0, 0})).distance_nm == 0,
        "polygon interior");
  check_near(mota::shape_distance(square, mota::make_point({mm(8), 0})).distance_nm,
             mm(3), 1, "polygon outside distance");
  const mota::Shape crossing = mota::make_segment({-mm(10), 0}, {mm(10), 0});
  check(mota::shape_distance(square, crossing).distance_nm == 0,
        "segment crossing a polygon intersects");

  const mota::Shape arc = mota::make_arc({0, 0}, mm(10), 0, 1'570'796, 0);
  check_near(mota::shape_distance(arc, mota::make_point({mm(10), mm(10)}))
                 .distance_nm,
             4'142'136, 10, "point at 45 degrees to a 90 degree arc");
  check_near(mota::shape_distance(arc, mota::make_point({-mm(10), 0}))
                 .distance_nm,
             14'142'136, 2,
             "point outside the arc angular span uses the nearer endpoint");
  check(mota::shape_distance(arc, mota::make_point({0, mm(10)})).distance_nm <= 2,
        "arc endpoint at 90 degrees");

  const mota::Shape circle2 = mota::make_circle({mm(8), 0}, mm(2));
  check(mota::shape_distance(circle, circle2).distance_nm == mm(1),
        "circle-circle gap");
  const mota::Shape rect_inner = mota::make_round_rect({mm(8), 0}, mm(2), mm(2), 0);
  check(mota::shape_distance(rect, rect_inner).distance_nm == 0,
        "overlapping rounded rect and circle");

  const mota::BoundsResult bounds = mota::shape_bounds(arc);
  check(bounds.bounds.min_x == -mm(10) && bounds.bounds.max_y == mm(10),
        "arc bounds are conservative");
}

void test_arc_error_bound() {
  const mota::Shape arc = mota::make_arc({0, 0}, mm(10), 0, 4'712'389, 0);
  const mota::Polyline line = mota::flatten_arc(arc, 50);
  check(line.error_nm <= 60, "flatten error stays near the requested bound");
  check(line.points.size() > 10, "flatten produces a meaningful polyline");

  const std::vector<mota::Vec2> probes = {
      {mm(14), mm(14)}, {mm(-14), mm(14)}, {mm(-14), -mm(14)}, {0, 0}};
  for (const mota::Vec2& probe : probes) {
    const mota::DistanceResult analytic =
        mota::shape_distance(arc, mota::make_point(probe));
    int64_t best = INT64_MAX;
    for (size_t i = 0; i + 1 < line.points.size(); ++i) {
      const mota::Shape segment =
          mota::make_segment(line.points[i], line.points[i + 1]);
      const mota::DistanceResult current =
          mota::shape_distance(segment, mota::make_point(probe));
      best = std::min(best, current.distance_nm - current.error_nm);
    }
    check(std::llabs(best - analytic.distance_nm) <= line.error_nm + 3,
          "flattened distance stays inside the declared error bound");
  }
}

void test_rtree_and_memory() {
  std::mt19937_64 rng(20261021);
  std::uniform_int_distribution<int64_t> coordinate(-mm(50), mm(50));
  std::uniform_int_distribution<int64_t> size(1'000, mm(2));
  constexpr size_t kObjects = 100'000;
  std::vector<mota::Aabb> boxes;
  boxes.reserve(kObjects);
  for (size_t i = 0; i < kObjects; ++i) {
    const int64_t x = coordinate(rng);
    const int64_t y = coordinate(rng);
    const int64_t w = size(rng);
    const int64_t h = size(rng);
    boxes.push_back(mota::Aabb{x, y, x + w, y + h});
  }
  mota::RTree tree;
  const auto build_start = std::chrono::steady_clock::now();
  tree.build(boxes);
  const auto build_end = std::chrono::steady_clock::now();
  const mota::RTreeStats stats = tree.stats();
  check(stats.objects == kObjects, "R-tree keeps every object");
  check(stats.nodes > 0 && stats.max_depth < 32, "R-tree depth is bounded");

  const auto query_start = std::chrono::steady_clock::now();
  size_t mismatches = 0;
  size_t hits = 0;
  for (int sample = 0; sample < 200; ++sample) {
    const size_t pick = static_cast<size_t>(rng() % kObjects);
    const mota::Aabb area = boxes[pick].expanded(mm(5));
    std::vector<size_t> found;
    tree.query(area, found);
    std::sort(found.begin(), found.end());
    std::vector<size_t> brute;
    for (size_t i = 0; i < boxes.size(); ++i) {
      if (boxes[i].intersects(area)) brute.push_back(i);
    }
    if (found != brute) ++mismatches;
    hits += found.size();
  }
  const auto query_end = std::chrono::steady_clock::now();
  check(mismatches == 0, "R-tree query equals brute force on 200 samples");

  mota::Json metrics = mota::Json::object();
  metrics["objects"] = mota::Json(static_cast<int64_t>(kObjects));
  metrics["nodes"] = mota::Json(static_cast<int64_t>(stats.nodes));
  metrics["leaves"] = mota::Json(static_cast<int64_t>(stats.leaves));
  metrics["max_depth"] = mota::Json(static_cast<int64_t>(stats.max_depth));
  metrics["index_memory_bytes"] =
      mota::Json(static_cast<int64_t>(stats.memory_bytes));
  metrics["build_ms"] = mota::Json(std::chrono::duration<double, std::milli>(
                                       build_end - build_start)
                                       .count());
  metrics["query_ms"] = mota::Json(std::chrono::duration<double, std::milli>(
                                       query_end - query_start)
                                       .count());
  metrics["hits"] = mota::Json(static_cast<int64_t>(hits));
  metrics["rss_bytes"] = mota::Json(rss_bytes());
  check(stats.memory_bytes < 64u * 1024u * 1024u,
        "100k-object index stays below 64 MiB");

  std::cout << "rtree objects=" << kObjects << " nodes=" << stats.nodes
            << " depth=" << stats.max_depth
            << " memory=" << stats.memory_bytes
            << " build_ms=" << (build_end - build_start).count() / 1e6
            << " query_ms=" << (query_end - query_start).count() / 1e6
            << " rss=" << rss_bytes() << "\n";
  g_rtree_metrics = metrics;
}

void test_board_structures() {
  std::vector<mota::LayerSpec> raw = {
      {"F.Cu", 1, "copper", 35'000, "copper"},
      {"D1", 2, "dielectric", 750'000, "FR4"},
      {"In1.Cu", 3, "copper", 35'000, "copper"},
      {"D2", 4, "dielectric", 750'000, "FR4"},
      {"In2.Cu", 5, "copper", 35'000, "copper"},
      {"D3", 6, "dielectric", 750'000, "FR4"},
      {"B.Cu", 7, "copper", 35'000, "copper"},
  };
  mota::Stackup stackup(raw);
  check(stackup.copper_count() == 4, "stackup copper count");
  check(stackup.total_thickness_nm() == 2'390'000, "stackup thickness sum");
  check(stackup.first_copper_index() == 0 && stackup.last_copper_index() == 6,
        "stackup outer copper indices");

  auto make_via = [&](const std::string& id, mota::ViaKind kind,
                      const std::string& start,
                      const std::string& end) {
    mota::ViaDefinition via;
    via.id = id;
    via.kind = kind;
    via.start_layer = start;
    via.end_layer = end;
    via.drill_nm = mm(2) / 10;
    via.pad = mota::make_circle({0, 0}, mm(1) / 2);
    via.resolve(stackup);
    return via;
  };
  const mota::ViaDefinition through =
      make_via("v_through", mota::ViaKind::Through, "F.Cu", "B.Cu");
  check(through.spans(2) && through.spans(4), "through via spans inner layers");
  const mota::ViaDefinition blind =
      make_via("v_blind", mota::ViaKind::Blind, "F.Cu", "In1.Cu");
  check(blind.spans(2) && !blind.spans(4), "blind via stops at In1");
  const mota::ViaDefinition buried =
      make_via("v_buried", mota::ViaKind::Buried, "In1.Cu", "In2.Cu");
  check(buried.electrically_connects(), "buried via is plated");
  make_via("v_micro", mota::ViaKind::Micro, "In1.Cu", "In2.Cu");
  check_throws(
      [&] {
        make_via("v_bad_through", mota::ViaKind::Through, "In1.Cu", "B.Cu");
      },
      mota::ErrorCode::ModelInvalid, "through via must span outer layers");
  check_throws(
      [&] {
        make_via("v_bad_buried", mota::ViaKind::Buried, "F.Cu", "In2.Cu");
      },
      mota::ErrorCode::ModelInvalid, "buried via cannot reach an outer layer");

  mota::PadStack npth;
  npth.id = "H1";
  npth.plated = false;
  npth.drill = mota::make_circle({0, 0}, mm(1));
  npth.validate(stackup);
  check(!npth.electrically_connects(), "NPTH never connects layers");
  check(npth.copper_on("F.Cu") == nullptr, "NPTH has no copper");

  mota::PadStack pad;
  pad.id = "U1:1";
  pad.copper_by_layer["F.Cu"] = mota::make_circle({0, 0}, mm(1));
  pad.copper_by_layer["B.Cu"] = mota::make_round_rect({0, 0}, mm(1), mm(1), 0);
  pad.drill = mota::make_circle({0, 0}, 300'000);
  pad.validate(stackup);
  check(pad.electrically_connects(), "plated padstack connects");
  check(pad.copper_on("F.Cu")->kind == mota::ShapeKind::Circle,
        "per-layer pad copper differs");
  check(pad.copper_on("In1.Cu") == nullptr, "pad copper is layer specific");

  mota::PadStack custom;
  custom.id = "CUSTOM1";
  custom.copper_by_layer["F.Cu"] = mota::make_polygon(
      {{-mm(2), -mm(1)}, {mm(2), -mm(1)}, {mm(3), 0},
       {mm(2), mm(1)}, {-mm(2), mm(1)}, {-mm(3), 0}});
  custom.drill = mota::make_slot({-200'000, 0}, {200'000, 0}, 150'000);
  custom.validate(stackup);
  check(custom.copper_on("F.Cu")->kind == mota::ShapeKind::Polygon,
        "custom pad uses a polygon copper shape");
  check(custom.drill->kind == mota::ShapeKind::Slot,
        "slot drill is preserved");
  mota::Transform tilted_pad;
  tilted_pad.rotation_deg = 37;
  tilted_pad.bottom = true;
  const auto transformed_custom =
      mota::transform_shape(*custom.copper_on("F.Cu"), tilted_pad);
  check(transformed_custom.shape.kind == mota::ShapeKind::Polygon,
        "custom pad transform keeps a polygon");
  check(transformed_custom.error_nm <= 1,
        "custom pad transform states a 1 nm bound");
  const mota::BoundsResult custom_bounds = mota::shape_bounds(transformed_custom.shape);
  check(custom_bounds.bounds.min_x < custom_bounds.bounds.max_x,
        "custom pad bounds are valid after mirror and rotation");

  std::vector<mota::LayerSpec> bad_order = raw;
  std::swap(bad_order[1].order, bad_order[2].order);
  check_throws([&] { mota::Stackup bad(bad_order); },
               mota::ErrorCode::ModelInvalid, "stackup order is validated");

  mota::BoardIndex index;
  index.add("F.Cu", "track_f", mota::make_segment({0, 0}, {mm(10), 0}));
  index.add("B.Cu", "track_b", mota::make_segment({0, 0}, {mm(10), 0}));
  index.add("F.Cu", "pad_f", mota::make_circle({mm(20), 0}, mm(1)));
  index.build(stackup);
  check(index.object_count() == 3, "board index object count");
  const std::vector<std::string> front =
      index.query("F.Cu", mota::Aabb{-mm(1), -mm(1), mm(11), mm(1)});
  check(front.size() == 1 && front[0] == "track_f", "layer query filters B.Cu");
  const std::vector<std::string> back =
      index.query("B.Cu", mota::Aabb{-mm(1), -mm(1), mm(11), mm(1)});
  check(back.size() == 1 && back[0] == "track_b", "B.Cu query is separate");
  check_throws(
      [&] { index.query("In1.Cu", mota::Aabb{0, 0, 1, 1}); },
      mota::ErrorCode::InvalidInput, "unknown index layer is rejected");
  check(index.layer_stats().size() == 2, "per-layer index stats");
}

}  // namespace

mota::Json geometry_tests() {
  test_checked_arithmetic();
  test_transform();
  test_distances();
  test_arc_error_bound();
  test_rtree_and_memory();
  test_board_structures();
  mota::Json metrics = mota::Json::object();
  metrics["checks"] = mota::Json(static_cast<int64_t>(motatest::checks));
  metrics["failures"] = mota::Json(static_cast<int64_t>(motatest::failures));
  metrics["rtree"] = g_rtree_metrics;
  return metrics;
}

int main(int argc, char** argv) {
  std::string report_path;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--report" && i + 1 < argc) report_path = argv[++i];
  }
  const mota::Json geometry = geometry_tests();
  const mota::Json worker = worker_tests();
  mota::Json report = mota::Json::object();
  report["geometry"] = geometry;
  report["worker"] = worker;
  report["checks"] = mota::Json(static_cast<int64_t>(motatest::checks));
  report["failures"] = mota::Json(static_cast<int64_t>(motatest::failures));
  if (!report_path.empty()) {
    std::ofstream stream(report_path);
    if (stream) stream << report.dump() << "\n";
  }
  std::cout << "checks=" << motatest::checks
            << " failures=" << motatest::failures << "\n";
  return motatest::failures == 0 ? 0 : 1;
}
