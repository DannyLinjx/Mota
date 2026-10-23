// Layer, via and package delay estimation, reference-plane continuity and
// controlled tuning regions.
//
// A delay number is only produced when every layer, via and package model on
// the path is present. Missing data leaves the check NOT_CHECKED. Tuning is
// only emitted inside a declared region and only after its meander geometry is
// checked against the other net of the pair.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <map>
#include <string>
#include <utility>
#include <vector>

#include "internal.hpp"

namespace mota {
namespace highspeed_detail {
namespace {

struct LayerLength {
  std::string layer_id;
  int index = -1;
  int64_t length_nm = 0;
};

std::vector<LayerLength> layer_lengths(const HighspeedInput& input,
                                       const std::vector<std::string>& nets) {
  std::vector<LayerLength> out;
  for (const HighspeedTrack& track : input.tracks) {
    if (std::find(nets.begin(), nets.end(), track.net) == nets.end()) continue;
    if (track.points.size() < 2) continue;
    const int64_t length = polyline_length(track.points);
    auto found = std::find_if(
        out.begin(), out.end(), [&](const LayerLength& entry) {
          return entry.layer_id == track.layer_id && entry.index == track.stackup_layer;
        });
    if (found == out.end()) {
      out.push_back(LayerLength{track.layer_id, track.stackup_layer, length});
    } else {
      found->length_nm += length;
    }
  }
  return out;
}

Vec2 point_at(const Vec2& a, const Vec2& b, double ratio) {
  Vec2 point;
  point.x = a.x + static_cast<int64_t>(
                    std::llround(static_cast<double>(b.x - a.x) * ratio));
  point.y = a.y + static_cast<int64_t>(
                    std::llround(static_cast<double>(b.y - a.y) * ratio));
  return point;
}

int64_t distance_to_net(const HighspeedInput& input, const std::string& net,
                        const Vec2& point, int64_t& nodes, int64_t budget) {
  int64_t best = std::numeric_limits<int64_t>::max();
  for (const HighspeedTrack& track : input.tracks) {
    if (track.net != net) continue;
    for (size_t i = 1; i < track.points.size(); ++i) {
      if (++nodes > budget) return -1;
      bool exact = false;
      best = std::min(best, point_segment_distance(
                                point, track.points[i - 1], track.points[i],
                                exact));
    }
  }
  return best;
}

}  // namespace

DelayEstimate estimate_delay(
    const HighspeedInput& input, const std::vector<std::string>& nets,
    const std::vector<std::pair<std::string, std::string>>& pins) {
  DelayEstimate estimate;
  for (const LayerLength& entry : layer_lengths(input, nets)) {
    if (entry.length_nm <= 0) continue;
    const LayerDelayModel* model = layer_model(input, entry.layer_id,
                                               entry.index);
    if (model == nullptr || model->propagation_ps_per_mm <= 0.0) {
      estimate.missing.push_back(
          "layer:" + (entry.layer_id.empty()
                          ? std::to_string(entry.index)
                          : entry.layer_id));
      continue;
    }
    estimate.delay_ps +=
        static_cast<double>(entry.length_nm) / 1.0e6 *
        model->propagation_ps_per_mm;
    estimate.length_nm += entry.length_nm;
  }
  for (const HighspeedVia& via : input.vias) {
    if (std::find(nets.begin(), nets.end(), via.net) == nets.end()) continue;
    if (!via.has_delay) {
      estimate.missing.push_back("via:" + via.id);
      continue;
    }
    estimate.delay_ps += via.delay_ps;
    if (via.stub_nm > 0) {
      // A stub without an explicit stub model cannot be called compensated.
      estimate.missing.push_back("via:" + via.id + ":stub");
    }
  }
  for (const auto& [component_id, pin] : pins) {
    if (component_id.empty()) continue;
    const PackageModel* model = package_model(input, component_id, pin);
    if (model == nullptr) {
      estimate.missing.push_back("package:" + component_id +
                                 (pin.empty() ? "" : ":" + pin));
      continue;
    }
    estimate.delay_ps += model->delay_ps;
    estimate.length_nm += model->length_nm;
  }
  if (!estimate.missing.empty()) {
    estimate.complete = false;
    estimate.reason = "MISSING_MODEL";
    estimate.message =
        "Delay is NOT_CHECKED: no model for " + estimate.missing.front() +
        (estimate.missing.size() > 1
             ? " (+" + std::to_string(estimate.missing.size() - 1) + " more)."
             : ".");
    estimate.refs = estimate.missing;
    return estimate;
  }
  estimate.complete = true;
  return estimate;
}

ReturnPathCheck check_return_path(const HighspeedInput& input,
                                  const std::vector<std::string>& nets) {
  ReturnPathCheck check;
  std::vector<std::string> missing;
  for (const HighspeedTrack& track : input.tracks) {
    if (std::find(nets.begin(), nets.end(), track.net) == nets.end()) continue;
    for (size_t i = 1; i < track.points.size(); ++i) {
      ++check.samples;
      const Vec2 midpoint =
          point_at(track.points[i - 1], track.points[i], 0.5);
      const LayerDelayModel* model =
          layer_model(input, track.layer_id, track.stackup_layer);
      if (model == nullptr) {
        missing.push_back("layer:" + (track.layer_id.empty()
                                          ? std::to_string(track.stackup_layer)
                                          : track.layer_id));
        continue;
      }
      if (model->reference_layer_id.empty()) {
        missing.push_back("layer:" + model->layer_id + ":reference");
        continue;
      }
      const ReferencePlane* plane =
          reference_plane(input, model->reference_layer_id, -1);
      if (plane == nullptr) {
        missing.push_back("plane:" + model->reference_layer_id);
        continue;
      }
      bool inside = false;
      for (const std::vector<Vec2>& polygon : plane->polygons) {
        if (polygon.size() >= 3 && polygon_contains(polygon, midpoint)) {
          inside = true;
          break;
        }
      }
      if (!inside) {
        ++check.crossings;
        check.refs.push_back("track:" + track.id + "@" +
                             std::to_string(midpoint.x) + "," +
                             std::to_string(midpoint.y));
      }
    }
  }
  if (!missing.empty()) {
    check.complete = false;
    check.reason = "MISSING_REFERENCE";
    check.message = "Return path is NOT_CHECKED: " + missing.front() +
                    " is not modelled.";
    return check;
  }
  check.complete = true;
  if (check.crossings > 0) {
    check.reason = "REFERENCE_PLANE_GAP";
    check.message = "Signal crosses a reference-plane split at " +
                    check.refs.front() + ".";
  }
  return check;
}

TuningPlan plan_tuning(const HighspeedInput& input, const DiffPair& pair,
                       const PairGeometry& geometry, bool delay_known,
                       double delay_skew_ps) {
  TuningPlan plan;
  if (!geometry.measured) {
    plan.attempted = false;
    plan.complete = false;
    plan.reason = geometry.reason;
    plan.message = "Tuning is NOT_CHECKED because the pair geometry is "
                   "unknown.";
    return plan;
  }
  const bool length_violation =
      pair.skew_tolerance_nm > 0 && geometry.skew_nm > pair.skew_tolerance_nm;
  const bool delay_violation =
      delay_known && pair.delay_skew_tolerance_ps > 0.0 &&
      std::abs(delay_skew_ps) > pair.delay_skew_tolerance_ps;
  if (!length_violation && !delay_violation) {
    plan.attempted = false;
    return plan;
  }

  const bool p_faster = delay_known ? delay_skew_ps < 0.0
                                    : geometry.p_length_nm < geometry.n_length_nm;
  const std::string target = p_faster ? pair.net_p : pair.net_n;
  const std::string other = p_faster ? pair.net_n : pair.net_p;
  plan.refs = {"pair:" + pair.id, "net:" + target};

  int64_t required = geometry.skew_nm;
  if (delay_violation) {
    const std::vector<LayerLength> layers = layer_lengths(input, {target});
    if (layers.empty()) {
      plan.attempted = true;
      plan.feasible = false;
      plan.complete = false;
      plan.reason = "MISSING_LAYER_MODEL";
      plan.message = "Cannot convert delay skew into length: net " + target +
                     " has no copper to tune.";
      return plan;
    }
    const LayerDelayModel* model =
        layer_model(input, layers.front().layer_id, layers.front().index);
    if (model == nullptr || model->propagation_ps_per_mm <= 0.0) {
      plan.attempted = true;
      plan.feasible = false;
      plan.complete = false;
      plan.reason = "MISSING_LAYER_MODEL";
      plan.message = "Cannot convert delay skew into length: net " + target +
                     " has no propagation model.";
      return plan;
    }
    required = static_cast<int64_t>(
        std::ceil(std::abs(delay_skew_ps) / model->propagation_ps_per_mm *
                  1.0e6));
  }
  plan.required_extra_nm = std::max<int64_t>(required, 0);
  plan.attempted = true;
  if (plan.required_extra_nm <= 0) {
    plan.message = "No extra length is required after the delay model.";
    return plan;
  }

  const TuningRegion* region = nullptr;
  for (const TuningRegion& candidate : input.tuning_regions) {
    if (candidate.net != target) continue;
    region = &candidate;
    break;
  }
  if (region == nullptr) {
    plan.feasible = false;
    plan.complete = false;
    plan.reason = "NO_TUNING_REGION";
    plan.message = "Net " + target + " needs " +
                   std::to_string(plan.required_extra_nm) +
                   " nm of extra length but has no declared tuning region. A "
                   "serpentine is never placed outside a controlled region.";
    return plan;
  }
  if (region->max_extra_nm > 0 && plan.required_extra_nm > region->max_extra_nm) {
    plan.feasible = false;
    plan.reason = "TUNING_CAPACITY";
    plan.message = "Tuning region " + region->id + " allows at most " +
                   std::to_string(region->max_extra_nm) + " nm of extra length.";
    return plan;
  }

  const int64_t axis_nm = segment_length(region->start, region->end);
  if (axis_nm <= 0) {
    plan.feasible = false;
    plan.reason = "DEGENERATE_REGION";
    plan.message = "Tuning region " + region->id + " has zero length.";
    return plan;
  }
  const int64_t amplitude = std::min<int64_t>(
      input.rules.tuning_amplitude_nm,
      region->width_nm > 0 ? region->width_nm / 2 : input.rules.tuning_amplitude_nm);
  if (amplitude <= 0) {
    plan.feasible = false;
    plan.reason = "DEGENERATE_REGION";
    plan.message = "Tuning region " + region->id + " has no usable width.";
    return plan;
  }

  int teeth = static_cast<int>(
      std::max<double>(1.0, std::ceil(static_cast<double>(plan.required_extra_nm) /
                                      (2.0 * static_cast<double>(amplitude)))));
  const double ax = static_cast<double>(region->end.x - region->start.x);
  const double ay = static_cast<double>(region->end.y - region->start.y);
  const double length = std::hypot(ax, ay);
  const double ux = ax / length;
  const double uy = ay / length;
  double px = -uy;
  double py = ux;

  // The meander is one-sided: it leaves the baseline only on the side that is
  // farther from the other net, so tuning cannot silently tighten the pair.
  {
    int64_t probe_nodes = 0;
    Vec2 side_a;
    side_a.x = region->start.x +
               static_cast<int64_t>(std::llround(px * static_cast<double>(amplitude)));
    side_a.y = region->start.y +
               static_cast<int64_t>(std::llround(py * static_cast<double>(amplitude)));
    Vec2 side_b;
    side_b.x = region->start.x -
               static_cast<int64_t>(std::llround(px * static_cast<double>(amplitude)));
    side_b.y = region->start.y -
               static_cast<int64_t>(std::llround(py * static_cast<double>(amplitude)));
    const int64_t distance_a = distance_to_net(
        input, other, side_a, probe_nodes, input.rules.max_search_nodes);
    const int64_t distance_b = distance_to_net(
        input, other, side_b, probe_nodes, input.rules.max_search_nodes);
    if (distance_a >= 0 && distance_b >= 0 && distance_b > distance_a) {
      px = -px;
      py = -py;
    }
  }

  TuningPatch patch;
  patch.pair_id = pair.id;
  patch.net = target;
  patch.layer_id = region->layer_id;
  patch.stackup_layer = region->stackup_layer;
  patch.start = region->start;
  patch.end = region->end;
  patch.required_extra_nm = plan.required_extra_nm;
  patch.amplitude_nm = amplitude;

  for (int attempt = 0; attempt < 8; ++attempt) {
    const double pitch = static_cast<double>(axis_nm) / teeth;
    if (pitch < static_cast<double>(input.rules.tuning_min_pitch_nm)) {
      plan.feasible = false;
      plan.reason = "TUNING_PITCH";
      plan.message = "Tuning region " + region->id + " cannot host " +
                     std::to_string(teeth) + " teeth at the minimum pitch.";
      return plan;
    }
    // Baseline zig-zag: every tooth climbs to +amplitude and returns, so the
    // meander stays on one side of the declared region axis.
    std::vector<Vec2> points;
    points.push_back(region->start);
    for (int index = 0; index < teeth; ++index) {
      const double peak =
          (static_cast<double>(index) + 0.5) / static_cast<double>(teeth);
      const double next =
          (static_cast<double>(index) + 1.0) / static_cast<double>(teeth);
      Vec2 high;
      high.x = region->start.x +
               static_cast<int64_t>(std::llround(
                   ux * length * peak + px * static_cast<double>(amplitude)));
      high.y = region->start.y +
               static_cast<int64_t>(std::llround(
                   uy * length * peak + py * static_cast<double>(amplitude)));
      points.push_back(high);
      Vec2 base;
      base.x = region->start.x +
               static_cast<int64_t>(std::llround(ux * length * next));
      base.y = region->start.y +
               static_cast<int64_t>(std::llround(uy * length * next));
      points.push_back(base);
    }
    const int64_t added = polyline_length(points) - axis_nm;
    patch.points = std::move(points);
    patch.added_length_nm = added;
    patch.teeth = teeth;
    patch.pitch_nm = static_cast<int64_t>(std::llround(pitch));
    if (added >= static_cast<int64_t>(
                     static_cast<double>(plan.required_extra_nm) * 0.95)) {
      break;
    }
    ++teeth;
  }
  plan.achieved_extra_nm = patch.added_length_nm;

  // Coupling control: every meander vertex must stay away from the other net.
  if (pair.gap_nm <= 0) {
    patch.controlled = false;
    patch.note = "NOT_CHECKED: intra-pair gap is not modelled.";
    plan.complete = false;
  } else {
    int64_t nodes = 0;
    const int64_t limit = pair.gap_nm + input.rules.tuning_coupling_margin_nm;
    for (const Vec2& point : patch.points) {
      const int64_t distance = distance_to_net(
          input, other, point, nodes, input.rules.max_search_nodes);
      if (distance < 0) {
        patch.controlled = false;
        patch.note = "NOT_CHECKED: coupling search budget exhausted.";
        plan.complete = false;
        break;
      }
      if (distance < pair.gap_nm) {
        plan.feasible = false;
        plan.reason = "TUNING_COUPLING";
        plan.message = "The tuning meander on " + target +
                       " comes closer than the intra-pair gap to " + other + ".";
        return plan;
      }
      if (distance < limit && patch.note.empty()) {
        patch.note = "Warning: meander is within gap+margin of " + other + ".";
      }
    }
    patch.controlled = plan.complete;
  }

  if (patch.added_length_nm <
      static_cast<int64_t>(static_cast<double>(plan.required_extra_nm) * 0.95)) {
    plan.feasible = false;
    plan.reason = "TUNING_CAPACITY";
    plan.message = "The controlled region only adds " +
                   std::to_string(patch.added_length_nm) + " nm of the " +
                   std::to_string(plan.required_extra_nm) + " nm required.";
    return plan;
  }

  if (patch.stackup_layer >= 0 && !patch.layer_id.empty()) {
    const LayerDelayModel* model =
        layer_model(input, patch.layer_id, patch.stackup_layer);
    if (model != nullptr && model->propagation_ps_per_mm > 0.0) {
      patch.added_delay_ps =
          static_cast<double>(patch.added_length_nm) / 1.0e6 *
          model->propagation_ps_per_mm;
    } else {
      patch.note = patch.note.empty()
                       ? "Added delay NOT_CHECKED: no layer model."
                       : patch.note + " Added delay NOT_CHECKED.";
      plan.complete = false;
    }
  }
  patch.id = "tune_" + pair.id + "_" + target;
  plan.patches.push_back(std::move(patch));
  return plan;
}

}  // namespace highspeed_detail
}  // namespace mota
