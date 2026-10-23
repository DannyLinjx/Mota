#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <string>
#include <vector>

#include "mota/connectivity.hpp"
#include "mota/fanout.hpp"
#include "mota/json.hpp"
#include "mota/types.hpp"
#include "test_common.hpp"

#ifndef MOTA_FIXTURE_DIR
#define MOTA_FIXTURE_DIR "platform_packs/bga-fixtures"
#endif

namespace {

using motatest::check;

struct Fixture {
  std::string id;
  std::string source;
  mota::FanoutInput input;
  std::string expected_status;
  int min_failures = 0;
  bool all_required = false;
  bool expect_via_in_pad = false;
  std::string package_contains;
};

int64_t integer(const mota::Json& value, const std::string& key,
                int64_t fallback = 0) {
  return value.has(key) ? value.at(key).as_int() : fallback;
}

bool boolean(const mota::Json& value, const std::string& key,
             bool fallback = false) {
  return value.has(key) ? value.at(key).as_bool() : fallback;
}

mota::Json load_json(const std::filesystem::path& path) {
  std::ifstream stream(path, std::ios::binary);
  if (!stream) {
    throw std::runtime_error("cannot open fixture " + path.string());
  }
  std::string text((std::istreambuf_iterator<char>(stream)),
                   std::istreambuf_iterator<char>());
  return mota::Json::parse(text);
}

Fixture load_fixture(const std::filesystem::path& path) {
  const mota::Json document = load_json(path);
  Fixture fixture;
  fixture.id = document.at("id").as_string();
  fixture.source = document.at("source").as_string();
  const int64_t rows = integer(document, "rows");
  const int64_t cols = integer(document, "cols");
  const int64_t pitch = integer(document, "pitch_nm");
  const int64_t pad = integer(document, "pad_diameter_nm");
  const int64_t margin = integer(document, "board_margin_nm", 1'000'000);
  fixture.input.package_id = fixture.id;
  fixture.input.package_source = fixture.source;
  mota::FanoutRules& rules = fixture.input.rules;
  rules.pitch_nm = pitch;
  rules.pad_diameter_nm = pad;
  rules.board_margin_nm = margin;

  const mota::Json& process = document.at("process");
  rules.trace_width_nm = integer(process, "trace_width_nm", 100'000);
  rules.clearance_nm = integer(process, "clearance_nm", 100'000);
  rules.via_drill_nm = integer(process, "via_drill_nm", 200'000);
  rules.via_pad_nm = integer(process, "via_pad_nm", 400'000);
  rules.microvia_drill_nm = integer(process, "microvia_drill_nm", 100'000);
  rules.microvia_pad_nm = integer(process, "microvia_pad_nm", 250'000);
  rules.min_annular_ring_nm = integer(process, "min_annular_ring_nm", 80'000);
  rules.edge_clearance_nm = integer(process, "edge_clearance_nm", 200'000);
  rules.allow_dogbone = boolean(process, "allow_dogbone", true);
  rules.allow_via_in_pad = boolean(process, "allow_via_in_pad", false);
  rules.allow_through_via = boolean(process, "allow_through_via", true);
  rules.max_search_nodes = integer(process, "max_search_nodes", 20'000);
  rules.seed = integer(process, "seed", 7);

  std::map<std::string, int> layer_index;
  const mota::Json& layers = document.at("stackup").at("layers");
  for (size_t index = 0; index < layers.items().size(); ++index) {
    const mota::Json& layer = layers.items()[index];
    layer_index[layer.at("id").as_string()] = static_cast<int>(index);
  }
  for (const mota::Json& item : document.at("signal_layers").items()) {
    const std::string name = item.as_string();
    const auto found = layer_index.find(name);
    if (found == layer_index.end()) continue;
    fixture.input.signal_layers.push_back(found->second);
    fixture.input.signal_layer_ids.push_back(name);
  }

  for (const mota::Json& item : document.at("groups").items()) {
    mota::FanoutGroup group;
    group.id = item.at("id").as_string();
    group.kind = item.at("kind").as_string();
    group.same_layer = boolean(item, "same_layer", true);
    group.adjacent = boolean(item, "adjacent", true);
    for (const mota::Json& ball : item.at("balls").items()) {
      group.balls.push_back(ball.as_string());
    }
    fixture.input.groups.push_back(std::move(group));
  }

  for (const mota::Json& item : document.at("balls").items()) {
    mota::FanoutBall ball;
    ball.id = item.at("id").as_string();
    ball.net = item.at("net").as_string();
    ball.row = static_cast<int>(integer(item, "row"));
    ball.col = static_cast<int>(integer(item, "col"));
    ball.center = {margin + (ball.col - 1) * pitch,
                   margin + (ball.row - 1) * pitch};
    ball.pad_diameter_nm = pad;
    ball.group = item.has("group") ? item.at("group").as_string() : "";
    ball.group_order = static_cast<int>(integer(item, "order", 0));
    ball.required = boolean(item, "required", true);
    ball.power = boolean(item, "power", false);
    fixture.input.balls.push_back(std::move(ball));
  }
  const int64_t width = 2 * margin + (cols - 1) * pitch;
  const int64_t height = 2 * margin + (rows - 1) * pitch;
  fixture.input.board_outline = {{0, 0}, {width, 0}, {width, height},
                                 {0, height}};

  if (document.has("expected")) {
    const mota::Json& expected = document.at("expected");
    fixture.expected_status =
        expected.has("status") ? expected.at("status").as_string() : "";
    fixture.min_failures = static_cast<int>(integer(expected, "min_failures", 0));
    fixture.all_required = boolean(expected, "all_required", false);
    fixture.expect_via_in_pad = boolean(expected, "expect_via_in_pad", false);
    fixture.package_contains =
        expected.has("package_contains")
            ? expected.at("package_contains").as_string()
            : "";
  }
  return fixture;
}

const mota::FanoutBall* find_ball(const Fixture& fixture,
                                  const std::string& id) {
  for (const mota::FanoutBall& ball : fixture.input.balls) {
    if (ball.id == id) return &ball;
  }
  return nullptr;
}

const mota::FanoutVia* find_via(const mota::FanoutResult& result,
                                const std::string& ball_id) {
  for (const mota::FanoutVia& via : result.vias) {
    if (via.ball_id == ball_id) return &via;
  }
  return nullptr;
}

const mota::FanoutTrack* find_track(const mota::FanoutResult& result,
                                    const std::string& ball_id) {
  for (const mota::FanoutTrack& track : result.tracks) {
    if (track.ball_id == ball_id) return &track;
  }
  return nullptr;
}

void check_via_process(const Fixture& fixture,
                       const mota::FanoutResult& result,
                       const std::string& label) {
  const mota::FanoutRules& rules = fixture.input.rules;
  for (const mota::FanoutVia& via : result.vias) {
    const mota::FanoutBall* ball = find_ball(fixture, via.ball_id);
    check(ball != nullptr, label + ": via names a known ball");
    if (ball == nullptr) continue;
    if (via.kind == "via_in_pad") {
      check(via.drill_nm == rules.microvia_drill_nm &&
                via.pad_outer_nm == rules.microvia_pad_nm,
            label + ": via-in-pad uses the microvia template");
      check(via.pad_outer_nm <= ball->pad_diameter_nm,
            label + ": via-in-pad fits inside the ball pad");
      check((via.pad_outer_nm - via.drill_nm) / 2 >=
                rules.min_annular_ring_nm,
            label + ": via-in-pad annular ring is legal");
    } else {
      check(via.drill_nm == rules.via_drill_nm &&
                via.pad_outer_nm == rules.via_pad_nm,
            label + ": dogbone via uses the through template");
      check((via.pad_outer_nm - via.drill_nm) / 2 >=
                rules.min_annular_ring_nm,
            label + ": dogbone annular ring is legal");
    }
    bool layer_ok = false;
    for (int layer : fixture.input.signal_layers) {
      if (layer == via.end_layer) layer_ok = true;
    }
    check(layer_ok, label + ": via lands on a declared signal layer");
  }
}

void check_escape_connectivity(const Fixture& fixture,
                               const mota::FanoutResult& result,
                               const std::string& label) {
  mota::ConnectivityInput connectivity;
  for (const mota::FanoutBall& ball : fixture.input.balls) {
    mota::CopperNode node;
    node.id = "ball:" + ball.id;
    node.net = ball.net;
    node.kind = "pad";
    node.plated = true;
    node.layer_shapes.emplace_back(
        0, mota::make_circle(ball.center, ball.pad_diameter_nm / 2));
    connectivity.nodes.push_back(std::move(node));
  }
  for (const mota::FanoutTrack& track : result.tracks) {
    mota::CopperNode node;
    node.id = track.id;
    node.net = track.net;
    node.kind = "track";
    node.width_nm = track.width_nm;
    node.layer_shapes.emplace_back(
        0, mota::make_slot(track.points.front(), track.points.back(),
                           std::max<int64_t>(1, track.width_nm / 2)));
    connectivity.nodes.push_back(std::move(node));
  }
  for (const mota::FanoutVia& via : result.vias) {
    mota::CopperNode node;
    node.id = via.id;
    node.net = via.net;
    node.kind = "via";
    node.plated = true;
    node.drill_nm = via.drill_nm;
    node.pad_outer_nm = via.pad_outer_nm;
    node.via_center = via.site;
    node.via_start = 0;
    node.via_end = via.end_layer;
    node.layer_shapes.emplace_back(
        0, mota::make_circle(via.site, via.pad_outer_nm / 2));
    if (via.end_layer != 0) {
      node.layer_shapes.emplace_back(
          via.end_layer, mota::make_circle(via.site, via.pad_outer_nm / 2));
    }
    connectivity.nodes.push_back(std::move(node));
  }
  const mota::ConnectivityReport report = mota::analyze_connectivity(connectivity);
  check(report.short_count == 0, label + ": escapes do not short");

  for (const mota::FanoutVia& via : result.vias) {
    const mota::FanoutBall* ball = find_ball(fixture, via.ball_id);
    if (ball == nullptr) continue;
    const mota::Shape pad = mota::make_circle(
        ball->center, ball->pad_diameter_nm / 2);
    const mota::Shape via_pad =
        mota::make_circle(via.site, via.pad_outer_nm / 2);
    if (via.kind == "via_in_pad") {
      check(mota::shape_distance(pad, via_pad).distance_nm == 0,
            label + ": via-in-pad touches the ball pad");
      continue;
    }
    const mota::FanoutTrack* track = find_track(result, via.ball_id);
    check(track != nullptr, label + ": dogbone has a track");
    if (track == nullptr) continue;
    const mota::Shape slot = mota::make_slot(
        track->points.front(), track->points.back(),
        std::max<int64_t>(1, track->width_nm / 2));
    check(mota::shape_distance(pad, slot).distance_nm == 0,
          label + ": dogbone track touches the ball pad");
    check(mota::shape_distance(slot, via_pad).distance_nm == 0,
          label + ": dogbone track touches its via");
  }
}

void check_group_reservations(const Fixture& fixture,
                              const mota::FanoutResult& result,
                              const std::string& label) {
  for (const mota::FanoutGroup& group : fixture.input.groups) {
    int escaped = 0;
    int layer = -1;
    bool same = true;
    for (const std::string& id : group.balls) {
      const mota::FanoutVia* via = find_via(result, id);
      if (via == nullptr) continue;
      ++escaped;
      if (layer < 0) layer = via->end_layer;
      if (via->end_layer != layer) same = false;
    }
    if (escaped == static_cast<int>(group.balls.size()) && escaped > 0) {
      check(same, label + ": group " + group.id + " shares one target layer");
    }
  }
}

void run_fixture(const std::filesystem::path& path) {
  const Fixture fixture = load_fixture(path);
  const mota::FanoutResult result = mota::fanout_bga(fixture.input);
  const std::string label = fixture.id;
  std::cout << label << " status=" << result.status
            << " required=" << result.required_count
            << " escaped=" << result.escaped_count
            << " failures=" << result.failures.size()
            << " vias=" << result.vias.size()
            << " dogbone=" << result.dogbone_count
            << " via_in_pad=" << result.via_in_pad_count
            << " channel=" << result.channel_capacity
            << " nodes=" << result.search_nodes;
  if (!result.failures.empty()) {
    std::cout << " first_failure=" << result.failures.front().ball_id << ":"
              << result.failures.front().reason;
  }
  std::cout << "\n";
  check(!result.semantic_hash.empty(), label + ": semantic hash");
  check_via_process(fixture, result, label);
  check_escape_connectivity(fixture, result, label);
  check_group_reservations(fixture, result, label);

  if (fixture.expected_status == "SUCCEEDED") {
    check(result.status == "SUCCEEDED", label + ": status SUCCEEDED");
    check(result.failures.empty(), label + ": no localized failures");
    check(result.escaped_count == result.required_count,
          label + ": all required balls escaped");
    check(result.escaped_count == static_cast<int>(fixture.input.balls.size()),
          label + ": every ball escaped");
    check(result.channel_capacity >= 0, label + ": channel estimate");
    check(result.search_nodes >= 0, label + ": search metrics");
    if (fixture.expect_via_in_pad) {
      check(result.via_in_pad_count > 0, label + ": via-in-pad path used");
    }
    if (!fixture.package_contains.empty()) {
      check(fixture.source.find(fixture.package_contains) != std::string::npos,
            label + ": public package source is named");
    }
    return;
  }

  check(result.status == "INFEASIBLE" || result.status == "PARTIAL",
        label + ": infeasible variant is localized");
  check(static_cast<int>(result.failures.size()) >= fixture.min_failures,
        label + ": failure count reaches the frozen expectation");
  check(!result.unescaped_balls.empty(), label + ": failed balls are listed");
  for (const mota::FanoutFailure& failure : result.failures) {
    check(find_ball(fixture, failure.ball_id) != nullptr,
          label + ": failure names a real ball");
    check(!failure.reason.empty(), label + ": failure has a reason");
    check(!failure.factors.empty(), label + ": failure names limiting factors");
  }
}

void test_fixture_set() {
  const std::filesystem::path directory(MOTA_FIXTURE_DIR);
  std::vector<std::filesystem::path> files;
  for (const auto& entry : std::filesystem::directory_iterator(directory)) {
    if (entry.path().extension() == ".json") files.push_back(entry.path());
  }
  std::sort(files.begin(), files.end());
  check(files.size() >= 5, "both pitch families and the platform fixture exist");
  for (const auto& path : files) run_fixture(path);
}

void test_seed_replay() {
  const Fixture fixture =
      load_fixture(std::filesystem::path(MOTA_FIXTURE_DIR) /
                   "synthetic_0p8_feasible.json");
  const mota::FanoutResult first = mota::fanout_bga(fixture.input);
  const mota::FanoutResult second = mota::fanout_bga(fixture.input);
  check(first.status == "SUCCEEDED" && second.status == "SUCCEEDED",
        "fixture replay succeeds");
  check(first.semantic_hash == second.semantic_hash,
        "same seed yields the same fanout hash");
  check(first.vias.size() == second.vias.size(), "same via count");
  check(first.tracks.size() == second.tracks.size(), "same track count");
  for (size_t i = 0; i < std::min(first.vias.size(), second.vias.size()); ++i) {
    check(first.vias[i].ball_id == second.vias[i].ball_id &&
              first.vias[i].site.x == second.vias[i].site.x &&
              first.vias[i].site.y == second.vias[i].site.y,
          "same escape site under the same seed");
  }
}

void test_platform_is_one_package() {
  const Fixture fixture =
      load_fixture(std::filesystem::path(MOTA_FIXTURE_DIR) /
                   "platform_stm32h743_tfbga240.json");
  const mota::FanoutResult result = mota::fanout_bga(fixture.input);
  check(result.status == "SUCCEEDED", "public package fanout succeeds");
  check(result.required_count == 240, "platform fixture has 240 active balls");
  check(result.escaped_count == 240, "all platform balls escape");
  check(result.failures.empty(), "platform fixture has no failure");
}

}  // namespace

int main() {
  test_fixture_set();
  test_seed_replay();
  test_platform_is_one_package();
  std::cout << "fanout checks=" << motatest::checks
            << " failures=" << motatest::failures << "\n";
  return motatest::failures == 0 ? 0 : 1;
}
