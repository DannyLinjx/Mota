// C11 — differential pairs, end-to-end channels and delay constraints.
//
// The analyzer never treats "same length" as "same delay", never invents a
// stackup, via or package model that was not supplied, and never upgrades a
// geometry or length PASS into a signal-integrity PASS: SI and PI stay
// NOT_CHECKED in every result.
//
// A channel is an explicit end-to-end object: one driver, one or more
// receivers, zero or more series elements (two pins joined by a component
// link) and declared branches. Lengths are measured on real copper, not on
// labels.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "mota/types.hpp"

namespace mota {

struct ChannelNode {
  std::string id;
  std::string kind;  // driver | receiver | series
  std::string net;
  std::string component_id;
  std::string pin;
  Vec2 position{};
  std::string layer_id;   // empty = any copper layer at this position
  int stackup_layer = -1;
};

struct ChannelLink {
  std::string id;
  std::string from;
  std::string to;
  std::string kind = "copper";  // copper | component
  int64_t internal_length_nm = 0;
};

struct Channel {
  std::string id;
  std::string net;
  std::string driver;                 // node id
  std::vector<std::string> receivers; // node ids
  std::vector<std::string> series;    // node ids (two per series element)
  std::vector<std::string> branches;  // node ids
  std::vector<ChannelNode> nodes;
  std::vector<ChannelLink> links;
  int64_t length_tolerance_nm = 0;    // 0 = no length-match requirement
  double target_delay_ps = 0.0;
  double delay_tolerance_ps = 0.0;
};

// Propagation model per copper layer. A layer with propagation_ps_per_mm <= 0
// has no model; a delay check that needs it is NOT_CHECKED.
struct LayerDelayModel {
  std::string layer_id;
  int stackup_layer = -1;
  int64_t thickness_nm = 0;
  std::string reference_layer_id;      // return-current reference plane
  double propagation_ps_per_mm = 0.0;
  std::string source;
};

// Reference plane copper with explicit splits. A signal segment whose
// reference point is outside every polygon crosses a plane gap.
struct ReferencePlane {
  std::string layer_id;
  int stackup_layer = -1;
  std::vector<std::vector<Vec2>> polygons;
};

struct PackageModel {
  std::string component_id;
  std::string pin;  // empty = whole component
  int64_t length_nm = 0;
  double delay_ps = 0.0;
  bool present = false;
  std::string source;
};

struct DiffPair {
  std::string id;
  std::string net_p;
  std::string net_n;
  int64_t gap_nm = 0;
  int64_t gap_tolerance_nm = 0;
  int64_t skew_tolerance_nm = 0;
  int64_t max_uncoupled_nm = 0;
  double delay_skew_tolerance_ps = 0.0;
  bool pair_layer_transitions = true;
};

struct HighspeedTrack {
  std::string id;
  std::string net;
  std::string layer_id;
  int stackup_layer = -1;
  int64_t width_nm = 0;
  std::vector<Vec2> points;
};

struct HighspeedVia {
  std::string id;
  std::string net;
  Vec2 center{};
  std::string start_layer_id;
  std::string end_layer_id;
  int start_layer = -1;
  int end_layer = -1;
  int64_t drill_nm = 0;
  int64_t stub_nm = 0;      // unused barrel length, reported but not guessed at
  double delay_ps = 0.0;
  bool has_delay = false;   // explicit via delay model
};

struct TuningRegion {
  std::string id;
  std::string net;
  std::string layer_id;
  int stackup_layer = -1;
  Vec2 start{};
  Vec2 end{};
  int64_t width_nm = 0;
  int64_t max_extra_nm = 0;
};

struct HighspeedRules {
  int64_t sample_step_nm = 100'000;      // 0.1 mm coupling sampling
  int64_t terminal_snap_nm = 1'000;      // terminal must sit on copper
  int64_t max_pair_samples = 20'000;
  int64_t tuning_amplitude_nm = 300'000;
  int64_t tuning_min_pitch_nm = 300'000;
  int64_t tuning_coupling_margin_nm = 100'000;
  int64_t max_search_nodes = 100'000;
  int64_t seed = 0;
};

struct HighspeedInput {
  std::vector<Channel> channels;
  std::vector<DiffPair> pairs;
  std::vector<HighspeedTrack> tracks;
  std::vector<HighspeedVia> vias;
  std::vector<LayerDelayModel> layer_models;
  std::vector<ReferencePlane> reference_planes;
  std::vector<PackageModel> package_models;
  std::vector<TuningRegion> tuning_regions;
  HighspeedRules rules;
};

struct CheckResult {
  std::string check_id;
  std::string kind;     // channel_topology | length_match | diff_coupling |
                        // diff_skew | diff_turn | pair_layer | delay |
                        // delay_skew | return_path | tuning
  std::string subject;  // channel / pair / net id
  std::string status;   // PASS | FAIL | NOT_CHECKED | SKIPPED
  std::string reason;   // typed code, empty on PASS
  std::string message;
  int64_t measured_nm = 0;
  int64_t limit_nm = 0;
  double measured_ps = 0.0;
  double limit_ps = 0.0;
  std::vector<std::string> object_refs;
};

// Result of a differential common search over the existing copper. Coupling is
// sampled on both nets; this structure never claims the pair was routed here.
struct BundleRoute {
  std::string pair_id;
  std::vector<std::string> nets;
  int64_t coupled_nm = 0;
  int64_t uncoupled_nm = 0;
  int64_t skew_nm = 0;
  int64_t turn_delta_nm = 0;
  int p_corners = 0;
  int n_corners = 0;
  int unmatched_corners = 0;
  bool turn_compensated = false;
  int shared_vias = 0;
  int p_vias = 0;
  int n_vias = 0;
  bool paired_layers = true;
  int64_t sample_step_nm = 0;
  std::string status;  // MEASURED | NOT_CHECKED | BUDGET
};

struct TuningPatch {
  std::string id;
  std::string net;
  std::string pair_id;
  std::string layer_id;
  int stackup_layer = -1;
  Vec2 start{};
  Vec2 end{};
  int64_t required_extra_nm = 0;
  int64_t added_length_nm = 0;
  double added_delay_ps = 0.0;
  int64_t amplitude_nm = 0;
  int64_t pitch_nm = 0;
  int teeth = 0;
  bool controlled = false;  // verified against the other net's copper
  std::vector<Vec2> points;
  std::string note;
};

struct HighspeedResult {
  std::string status;  // SUCCEEDED | PARTIAL | FAILED | UNKNOWN | NOT_CHECKED
  std::vector<CheckResult> checks;
  std::vector<BundleRoute> bundles;
  std::vector<TuningPatch> tuning;
  std::vector<std::string> violations;
  std::vector<std::string> not_checked;
  std::vector<std::string> diagnostics;
  std::string si_status;  // always NOT_CHECKED
  std::string pi_status;  // always NOT_CHECKED
  int pass_count = 0;
  int fail_count = 0;
  int not_checked_count = 0;
  int skipped_count = 0;
  int64_t search_nodes = 0;
  int64_t seed = 0;
  bool budget_hit = false;
  std::string semantic_hash;
};

HighspeedResult analyze_highspeed(const HighspeedInput& input);

}  // namespace mota
