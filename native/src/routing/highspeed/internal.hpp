// Shared helpers for the C11 highspeed sources.
#pragma once

#include <algorithm>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "mota/highspeed.hpp"
#include "mota/shapes.hpp"
#include "mota/types.hpp"

namespace mota {
namespace highspeed_detail {

inline int64_t segment_length(const Vec2& a, const Vec2& b) {
  const __int128 dx = static_cast<__int128>(b.x) - a.x;
  const __int128 dy = static_cast<__int128>(b.y) - a.y;
  return isqrt_round(dx * dx + dy * dy).value;
}

inline int64_t polyline_length(const std::vector<Vec2>& points) {
  int64_t total = 0;
  for (size_t i = 1; i < points.size(); ++i) {
    total += segment_length(points[i - 1], points[i]);
  }
  return total;
}

inline const LayerDelayModel* layer_model(const HighspeedInput& input,
                                          const std::string& layer_id,
                                          int layer_index) {
  for (const LayerDelayModel& model : input.layer_models) {
    if (!layer_id.empty() && model.layer_id == layer_id) return &model;
    if (layer_id.empty() && layer_index >= 0 &&
        model.stackup_layer == layer_index) {
      return &model;
    }
  }
  return nullptr;
}

inline const ReferencePlane* reference_plane(const HighspeedInput& input,
                                            const std::string& layer_id,
                                            int layer_index) {
  for (const ReferencePlane& plane : input.reference_planes) {
    if (!layer_id.empty() && plane.layer_id == layer_id) return &plane;
    if (layer_id.empty() && layer_index >= 0 &&
        plane.stackup_layer == layer_index) {
      return &plane;
    }
  }
  return nullptr;
}

inline const PackageModel* package_model(const HighspeedInput& input,
                                         const std::string& component_id,
                                         const std::string& pin) {
  const PackageModel* fallback = nullptr;
  for (const PackageModel& model : input.package_models) {
    if (!model.present || model.component_id != component_id) continue;
    if (!pin.empty() && model.pin == pin) return &model;
    if (model.pin.empty()) fallback = &model;
  }
  return fallback;
}

inline bool same_point(const Vec2& a, const Vec2& b, int64_t tolerance) {
  const int64_t dx = a.x > b.x ? a.x - b.x : b.x - a.x;
  const int64_t dy = a.y > b.y ? a.y - b.y : b.y - a.y;
  return dx <= tolerance && dy <= tolerance;
}

struct ChannelPath {
  std::string receiver;      // node id
  std::string receiver_ref;  // component:pin
  int64_t length_nm = 0;
  bool measured = false;
  std::string reason;
  std::string message;
  std::vector<std::string> refs;
};

struct ChannelSolve {
  std::vector<ChannelPath> paths;
  std::string topology_status;  // PASS | FAIL | NOT_CHECKED
  std::string topology_reason;
  std::string topology_message;
  std::vector<std::string> topology_refs;
  std::vector<std::string> open_links;
  bool budget_hit = false;
};

ChannelSolve solve_channel(const HighspeedInput& input, const Channel& channel);

struct PairGeometry {
  bool measured = false;
  std::string reason;
  std::string message;
  int64_t p_length_nm = 0;
  int64_t n_length_nm = 0;
  int64_t coupled_nm = 0;
  int64_t uncoupled_nm = 0;
  int64_t skew_nm = 0;
  int64_t sampled_step_nm = 0;
  int p_corners = 0;
  int n_corners = 0;
  int unmatched_corners = 0;
  int64_t turn_delta_nm = 0;
  bool turn_compensated = false;
  bool paired_layers = true;
  int shared_vias = 0;
  int p_vias = 0;
  int n_vias = 0;
  std::vector<std::string> unpaired_vias;
  std::vector<std::string> refs;
};

PairGeometry analyze_pair(const HighspeedInput& input, const DiffPair& pair,
                          int64_t& nodes, bool& budget_hit);

struct DelayEstimate {
  bool complete = false;
  std::string reason;
  std::string message;
  double delay_ps = 0.0;
  int64_t length_nm = 0;
  std::vector<std::string> missing;
  std::vector<std::string> refs;
};

DelayEstimate estimate_delay(
    const HighspeedInput& input, const std::vector<std::string>& nets,
    const std::vector<std::pair<std::string, std::string>>& pins);

struct ReturnPathCheck {
  bool complete = false;
  std::string reason;
  std::string message;
  int64_t samples = 0;
  int64_t crossings = 0;
  std::vector<std::string> refs;
};

ReturnPathCheck check_return_path(const HighspeedInput& input,
                                  const std::vector<std::string>& nets);

struct TuningPlan {
  std::vector<TuningPatch> patches;
  bool attempted = false;
  bool feasible = true;
  bool complete = true;
  std::string reason;
  std::string message;
  int64_t required_extra_nm = 0;
  int64_t achieved_extra_nm = 0;
  std::vector<std::string> refs;
};

TuningPlan plan_tuning(const HighspeedInput& input, const DiffPair& pair,
                       const PairGeometry& geometry, bool delay_known,
                       double delay_skew_ps);

}  // namespace highspeed_detail
}  // namespace mota
