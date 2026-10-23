#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#include "mota/highspeed.hpp"
#include "mota/highspeed_json.hpp"
#include "mota/json.hpp"
#include "test_common.hpp"

#ifndef MOTA_FIXTURE_DIR
#define MOTA_FIXTURE_DIR "platform_packs/highspeed-fixtures"
#endif

namespace {

using motatest::check;

struct Expectation {
  std::string kind;
  std::string status;
  int64_t measured_nm = 0;
  double measured_ps = 0.0;
  bool has_nm = false;
  bool has_ps = false;
};

struct Fixture {
  std::string id;
  std::string source;
  std::string expected_status;
  mota::HighspeedInput input;
  std::vector<Expectation> expectations;
  double tuning_ratio = 0.95;
};

mota::Json load_json(const std::filesystem::path& path) {
  std::ifstream stream(path, std::ios::binary);
  if (!stream) {
    throw std::runtime_error("cannot open fixture " + path.string());
  }
  std::string text((std::istreambuf_iterator<char>(stream)),
                   std::istreambuf_iterator<char>());
  return mota::Json::parse(text);
}

int64_t integer(const mota::Json& value, const std::string& key,
                int64_t fallback = 0) {
  return value.has(key) ? value.at(key).as_int() : fallback;
}

Fixture load_fixture(const std::filesystem::path& path) {
  const mota::Json document = load_json(path);
  Fixture fixture;
  fixture.id = document.at("id").as_string();
  fixture.source = document.at("source").as_string();
  fixture.expected_status = document.at("expected_status").as_string();
  std::vector<std::string> layers;
  for (const mota::Json& item : document.at("layers").items()) {
    layers.push_back(item.as_string());
  }
  fixture.input = mota::parse_highspeed_json(document, layers);
  if (document.has("expect_checks")) {
    for (const mota::Json& item : document.at("expect_checks").items()) {
      Expectation expectation;
      expectation.kind = item.at("kind").as_string();
      expectation.status = item.at("status").as_string();
      if (item.has("measured_nm")) {
        expectation.has_nm = true;
        expectation.measured_nm = integer(item, "measured_nm");
      }
      if (item.has("measured_ps")) {
        expectation.has_ps = true;
        expectation.measured_ps = item.at("measured_ps").as_double();
      }
      fixture.expectations.push_back(std::move(expectation));
    }
  }
  if (document.has("expect_tuning_min_ratio")) {
    fixture.tuning_ratio =
        document.at("expect_tuning_min_ratio").as_double();
  }
  return fixture;
}

const mota::CheckResult* find_check(const mota::HighspeedResult& result,
                                    const std::string& kind) {
  for (const mota::CheckResult& check : result.checks) {
    if (check.kind == kind) return &check;
  }
  return nullptr;
}

int count_kind(const mota::HighspeedResult& result, const std::string& kind) {
  int total = 0;
  for (const mota::CheckResult& check : result.checks) {
    if (check.kind == kind) ++total;
  }
  return total;
}

void check_common(const Fixture& fixture, const mota::HighspeedResult& result,
                  const std::string& label) {
  check(!result.semantic_hash.empty(), label + ": semantic hash");
  check(result.si_status == "NOT_CHECKED", label + ": SI is never auto-passed");
  check(result.pi_status == "NOT_CHECKED", label + ": PI is never auto-passed");
  check(result.status == fixture.expected_status,
        label + ": status " + result.status + " == " + fixture.expected_status);

  for (const mota::CheckResult& item : result.checks) {
    if (item.status == "PASS") {
      check(item.reason.empty(), label + ": PASS has no failure reason (" +
                                      item.check_id + ")");
    }
    if (item.status == "NOT_CHECKED") {
      check(!item.message.empty(),
            label + ": NOT_CHECKED is explained (" + item.check_id + ")");
      check(!item.reason.empty(),
            label + ": NOT_CHECKED has a typed reason (" + item.check_id + ")");
    }
    if (item.status == "FAIL") {
      check(!item.reason.empty(),
            label + ": FAIL has a typed reason (" + item.check_id + ")");
    }
  }
  if (result.fail_count > 0) {
    check(!result.violations.empty(), label + ": violations are listed");
    check(result.status == "FAILED", label + ": any FAIL is FAILED");
  }
  if (result.not_checked_count > 0) {
    check(result.status != "SUCCEEDED",
          label + ": NOT_CHECKED never yields SUCCEEDED");
  }
  if (result.checks.empty()) {
    check(result.status == "NOT_CHECKED", label + ": empty contract");
  }
  if (result.budget_hit) {
    check(result.status == "UNKNOWN" || result.status == "FAILED" ||
              result.status == "PARTIAL",
          label + ": budget stop is not a proof");
  }
}

void check_expectations(const Fixture& fixture,
                        const mota::HighspeedResult& result,
                        const std::string& label) {
  for (const Expectation& expectation : fixture.expectations) {
    const mota::CheckResult* found = find_check(result, expectation.kind);
    check(found != nullptr,
          label + ": check " + expectation.kind + " exists");
    if (found == nullptr) continue;
    check(found->status == expectation.status,
          label + ": " + expectation.kind + " is " + found->status +
              " (expected " + expectation.status + ")");
    if (expectation.has_nm) {
      motatest::check_near(found->measured_nm, expectation.measured_nm, 1,
                           label + ": " + expectation.kind + " length");
    }
    if (expectation.has_ps) {
      const double delta = found->measured_ps - expectation.measured_ps;
      check(delta < 1.0 && delta > -1.0,
            label + ": " + expectation.kind + " delay");
    }
  }
}

void check_bundles(const Fixture& fixture, const mota::HighspeedResult& result,
                   const std::string& label) {
  check(result.bundles.size() == fixture.input.pairs.size(),
        label + ": one bundle per pair");
  for (const mota::BundleRoute& bundle : result.bundles) {
    if (bundle.status != "MEASURED") continue;
    check(bundle.coupled_nm + bundle.uncoupled_nm > 0,
          label + ": bundle " + bundle.pair_id + " measured copper");
    const int64_t skew = bundle.skew_nm;
    check(skew >= 0, label + ": bundle skew is absolute");
    check(bundle.sample_step_nm > 0, label + ": sampling step recorded");
  }
}

void check_tuning(const Fixture& fixture, const mota::HighspeedResult& result,
                  const std::string& label) {
  for (const mota::TuningPatch& patch : result.tuning) {
    check(patch.teeth >= 1, label + ": tuning has teeth");
    check(patch.points.size() >= 5, label + ": tuning has real geometry");
    check(patch.required_extra_nm > 0, label + ": tuning target recorded");
    const double ratio =
        static_cast<double>(patch.added_length_nm) /
        static_cast<double>(patch.required_extra_nm);
    check(ratio >= fixture.tuning_ratio,
          label + ": tuning adds the required length");
    check(patch.controlled, label + ": tuning stays in a controlled region");
    check(patch.pitch_nm >= fixture.input.rules.tuning_min_pitch_nm,
          label + ": tuning pitch respects the minimum");
  }
}

void run_fixture(const std::filesystem::path& path) {
  const Fixture fixture = load_fixture(path);
  const std::string label = fixture.id;
  const mota::HighspeedResult first = mota::analyze_highspeed(fixture.input);
  const mota::HighspeedResult second = mota::analyze_highspeed(fixture.input);

  std::cout << label << " status=" << first.status
            << " pass=" << first.pass_count << " fail=" << first.fail_count
            << " not_checked=" << first.not_checked_count
            << " skipped=" << first.skipped_count
            << " checks=" << first.checks.size()
            << " bundles=" << first.bundles.size()
            << " tuning=" << first.tuning.size()
            << " nodes=" << first.search_nodes << "\n";
  for (const mota::BundleRoute& bundle : first.bundles) {
    std::cout << "  bundle " << bundle.pair_id << " " << bundle.status
              << " coupled=" << bundle.coupled_nm
              << " uncoupled=" << bundle.uncoupled_nm
              << " skew=" << bundle.skew_nm
              << " corners=" << bundle.p_corners << "/" << bundle.n_corners
              << " unmatched=" << bundle.unmatched_corners
              << " vias=" << bundle.p_vias << "/" << bundle.n_vias
              << " paired=" << (bundle.paired_layers ? 1 : 0) << "\n";
  }
  for (const mota::TuningPatch& patch : first.tuning) {
    std::cout << "  tuning " << patch.net << " required="
              << patch.required_extra_nm << " added=" << patch.added_length_nm
              << " teeth=" << patch.teeth << " pitch=" << patch.pitch_nm
              << " controlled=" << (patch.controlled ? 1 : 0) << "\n";
  }

  check_common(fixture, first, label);
  check_expectations(fixture, first, label);
  check_bundles(fixture, first, label);
  check_tuning(fixture, first, label);

  check(first.semantic_hash == second.semantic_hash,
        label + ": fixed seed replays the same semantic hash");
  check(first.status == second.status, label + ": replay status");
  check(first.checks.size() == second.checks.size(),
        label + ": replay check count");

  if (!fixture.input.channels.empty()) {
    check(count_kind(first, "channel_topology") ==
              static_cast<int>(fixture.input.channels.size()),
          label + ": every channel is checked");
  }
  if (!fixture.input.pairs.empty()) {
    check(count_kind(first, "diff_skew") ==
              static_cast<int>(fixture.input.pairs.size()),
          label + ": every pair is length-checked");
    check(count_kind(first, "delay_skew") ==
              static_cast<int>(fixture.input.pairs.size()),
          label + ": every pair is delay-checked");
  }
}

void test_tempered_labels_do_not_route() {
  const Fixture fixture =
      load_fixture(std::filesystem::path(MOTA_FIXTURE_DIR) /
                   "channel_series_matched.json");
  mota::HighspeedInput input = fixture.input;
  input.tracks.erase(
      std::remove_if(input.tracks.begin(), input.tracks.end(),
                     [](const mota::HighspeedTrack& track) {
                       return track.net == "N1";
                     }),
      input.tracks.end());
  const mota::HighspeedResult result = mota::analyze_highspeed(input);
  const mota::CheckResult* topology = find_check(result, "channel_topology");
  check(topology != nullptr, "tamper: topology check exists");
  if (topology != nullptr) {
    check(topology->status == "FAIL",
          "tamper: removing copper breaks the channel");
    check(topology->reason == "TERMINAL_NOT_ON_COPPER",
          "tamper: failure is localized to the terminal");
  }
  check(result.status == "FAILED", "tamper: failed channel is FAILED");
}

void test_budget_is_not_a_proof() {
  const Fixture fixture =
      load_fixture(std::filesystem::path(MOTA_FIXTURE_DIR) / "pair_matched.json");
  mota::HighspeedInput input = fixture.input;
  input.rules.max_pair_samples = 1;
  const mota::HighspeedResult result = mota::analyze_highspeed(input);
  check(result.status != "SUCCEEDED", "budget: never SUCCEEDED");
  check(result.budget_hit, "budget: hit is reported");
  const mota::CheckResult* coupling = find_check(result, "diff_coupling");
  check(coupling != nullptr && coupling->status != "PASS",
        "budget: coupling is not PASS at sample limit 1");
}

void test_unpaired_transition() {
  const Fixture fixture =
      load_fixture(std::filesystem::path(MOTA_FIXTURE_DIR) / "pair_matched.json");
  mota::HighspeedInput input = fixture.input;
  for (mota::HighspeedVia& via : input.vias) {
    if (via.net == "DP_N") via.center.x += 2000000;
  }
  const mota::HighspeedResult result = mota::analyze_highspeed(input);
  const mota::CheckResult* layer = find_check(result, "pair_layer");
  check(layer != nullptr, "unpaired: pair_layer check exists");
  if (layer != nullptr) {
    check(layer->status == "FAIL", "unpaired: transition is detected");
    check(layer->reason == "UNPAIRED_TRANSITION",
          "unpaired: typed reason");
  }
}

void test_missing_layer_model() {
  const Fixture fixture = load_fixture(
      std::filesystem::path(MOTA_FIXTURE_DIR) / "same_length_different_layer.json");
  mota::HighspeedInput input = fixture.input;
  input.layer_models.erase(
      std::remove_if(input.layer_models.begin(), input.layer_models.end(),
                     [](const mota::LayerDelayModel& model) {
                       return model.layer_id == "In1.Cu";
                     }),
      input.layer_models.end());
  const mota::HighspeedResult result = mota::analyze_highspeed(input);
  const mota::CheckResult* delay = find_check(result, "delay_skew");
  check(delay != nullptr, "missing model: delay_skew check exists");
  if (delay != nullptr) {
    check(delay->status == "NOT_CHECKED",
          "missing model: delay skew is NOT_CHECKED");
    check(delay->reason == "MISSING_MODEL",
          "missing model: typed reason");
  }
  check(result.status != "SUCCEEDED", "missing model: never SUCCEEDED");
  check(result.si_status == "NOT_CHECKED", "missing model: SI untouched");
}

void test_empty_contract() {
  mota::HighspeedInput input;
  const mota::HighspeedResult result = mota::analyze_highspeed(input);
  check(result.status == "NOT_CHECKED", "empty: nothing checked");
  check(result.checks.empty(), "empty: no checks invented");
  check(!result.diagnostics.empty(), "empty: diagnostic is recorded");
  check(result.si_status == "NOT_CHECKED", "empty: SI untouched");
}

}  // namespace

int main() {
  const std::filesystem::path directory(MOTA_FIXTURE_DIR);
  std::vector<std::filesystem::path> fixtures;
  for (const auto& entry : std::filesystem::directory_iterator(directory)) {
    if (entry.path().extension() == ".json") fixtures.push_back(entry.path());
  }
  std::sort(fixtures.begin(), fixtures.end());
  for (const std::filesystem::path& path : fixtures) run_fixture(path);

  test_tempered_labels_do_not_route();
  test_budget_is_not_a_proof();
  test_unpaired_transition();
  test_missing_layer_model();
  test_empty_contract();

  std::cout << "highspeed checks=" << motatest::checks
            << " failures=" << motatest::failures << "\n";
  return motatest::failures == 0 ? 0 : 1;
}
