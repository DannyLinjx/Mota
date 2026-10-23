// Differential common search over real copper geometry.
//
// Coupling is sampled along both nets; a sample only counts as coupled when a
// counterpart exists on the same layer within the declared intra-pair gap
// window. Turns are matched corner by corner, and layer transitions are
// matched via by via. Nothing here assumes that two nets labelled as a pair
// were actually routed together.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <string>
#include <utility>
#include <vector>

#include "internal.hpp"

namespace mota {
namespace highspeed_detail {
namespace {

struct Sample {
  Vec2 point{};
  int layer = -1;
  int64_t arc_nm = 0;
};

struct PolylineGeometry {
  int layer = -1;
  std::string layer_id;
  std::vector<Vec2> points;
  int64_t length_nm = 0;
};

struct Corner {
  Vec2 point{};
  int layer = -1;
  int64_t arc_nm = 0;
  bool matched = false;
};

std::vector<PolylineGeometry> net_geometry(const HighspeedInput& input,
                                           const std::string& net,
                                           int64_t& total_nm) {
  std::vector<PolylineGeometry> out;
  total_nm = 0;
  for (const HighspeedTrack& track : input.tracks) {
    if (track.net != net || track.points.size() < 2) continue;
    PolylineGeometry geometry;
    geometry.layer = track.stackup_layer;
    geometry.layer_id = track.layer_id;
    geometry.points = track.points;
    geometry.length_nm = polyline_length(track.points);
    total_nm += geometry.length_nm;
    out.push_back(std::move(geometry));
  }
  return out;
}

std::vector<Sample> sample_path(const std::vector<PolylineGeometry>& geometry,
                                int64_t step_nm, int64_t limit,
                                bool& budget_hit) {
  std::vector<Sample> samples;
  if (step_nm <= 0) return samples;
  for (const PolylineGeometry& path : geometry) {
    int64_t arc = 0;
    for (size_t i = 1; i < path.points.size(); ++i) {
      const Vec2& a = path.points[i - 1];
      const Vec2& b = path.points[i];
      const int64_t length = segment_length(a, b);
      if (length == 0) continue;
      int64_t travelled = 0;
      while (travelled <= length) {
        if (static_cast<int64_t>(samples.size()) >= limit) {
          budget_hit = true;
          return samples;
        }
        const double ratio =
            static_cast<double>(travelled) / static_cast<double>(length);
        Sample sample;
        sample.point.x =
            a.x + static_cast<int64_t>(std::llround(
                      static_cast<double>(b.x - a.x) * ratio));
        sample.point.y =
            a.y + static_cast<int64_t>(std::llround(
                      static_cast<double>(b.y - a.y) * ratio));
        sample.layer = path.layer;
        sample.arc_nm = arc + travelled;
        samples.push_back(sample);
        travelled += step_nm;
      }
      arc += length;
    }
    if (!path.points.empty()) {
      if (static_cast<int64_t>(samples.size()) >= limit) {
        budget_hit = true;
        return samples;
      }
      Sample sample;
      sample.point = path.points.back();
      sample.layer = path.layer;
      sample.arc_nm = arc;
      samples.push_back(sample);
    }
  }
  return samples;
}

int64_t coupling_distance(const std::vector<PolylineGeometry>& others,
                          const Sample& sample, int64_t& nodes,
                          int64_t budget) {
  int64_t best = std::numeric_limits<int64_t>::max();
  for (const PolylineGeometry& path : others) {
    if (path.layer != sample.layer) continue;
    for (size_t i = 1; i < path.points.size(); ++i) {
      if (++nodes > budget) return -1;
      bool exact = false;
      const int64_t distance = point_segment_distance(
          sample.point, path.points[i - 1], path.points[i], exact);
      best = std::min(best, distance);
    }
  }
  return best;
}

std::vector<Corner> corners_of(const std::vector<PolylineGeometry>& geometry) {
  std::vector<Corner> corners;
  for (const PolylineGeometry& path : geometry) {
    int64_t arc = 0;
    for (size_t i = 1; i < path.points.size(); ++i) {
      const Vec2& a = path.points[i - 1];
      const Vec2& b = path.points[i];
      arc += segment_length(a, b);
      if (i + 1 >= path.points.size()) continue;
      const Vec2& c = path.points[i + 1];
      const double ax = static_cast<double>(b.x - a.x);
      const double ay = static_cast<double>(b.y - a.y);
      const double bx = static_cast<double>(c.x - b.x);
      const double by = static_cast<double>(c.y - b.y);
      const double na = std::hypot(ax, ay);
      const double nb = std::hypot(bx, by);
      if (na == 0.0 || nb == 0.0) continue;
      const double cosine = (ax * bx + ay * by) / (na * nb);
      const double angle = std::acos(std::clamp(cosine, -1.0, 1.0));
      if (angle < 0.5236) continue;  // 30 degrees
      corners.push_back(Corner{b, path.layer, arc, false});
    }
  }
  return corners;
}

}  // namespace

PairGeometry analyze_pair(const HighspeedInput& input, const DiffPair& pair,
                          int64_t& nodes, bool& budget_hit) {
  PairGeometry result;
  result.refs = {"net:" + pair.net_p, "net:" + pair.net_n};
  if (pair.gap_nm <= 0) {
    result.reason = "MISSING_GAP";
    result.message =
        "Pair " + pair.id + " has no intra-pair gap model; coupling cannot be "
        "measured from a zero gap.";
    return result;
  }

  int64_t p_length = 0;
  int64_t n_length = 0;
  const std::vector<PolylineGeometry> p_geometry =
      net_geometry(input, pair.net_p, p_length);
  const std::vector<PolylineGeometry> n_geometry =
      net_geometry(input, pair.net_n, n_length);
  if (p_geometry.empty() || n_geometry.empty()) {
    result.reason = "NO_COPPER";
    result.message = "Pair " + pair.id + " has no routed copper for " +
                     (p_geometry.empty() ? pair.net_p : pair.net_n) + ".";
    return result;
  }
  result.p_length_nm = p_length;
  result.n_length_nm = n_length;
  result.skew_nm = p_length > n_length ? p_length - n_length
                                      : n_length - p_length;

  bool sample_budget = false;
  const std::vector<Sample> p_samples = sample_path(
      p_geometry, input.rules.sample_step_nm, input.rules.max_pair_samples,
      sample_budget);
  const std::vector<Sample> n_samples = sample_path(
      n_geometry, input.rules.sample_step_nm, input.rules.max_pair_samples,
      sample_budget);
  if (sample_budget) {
    budget_hit = true;
    result.reason = "SEARCH_BUDGET";
    result.message = "Coupling sampling exceeded max_pair_samples; the pair is "
                     "UNKNOWN, not uncoupled.";
    return result;
  }

  const int64_t window = pair.gap_nm + pair.gap_tolerance_nm;
  int64_t coupled = 0;
  int64_t uncoupled = 0;
  for (const Sample& sample : p_samples) {
    const int64_t distance =
        coupling_distance(n_geometry, sample, nodes,
                          input.rules.max_search_nodes);
    if (distance < 0) {
      budget_hit = true;
      result.reason = "SEARCH_BUDGET";
      result.message = "Coupling search exceeded max_search_nodes; the pair is "
                       "UNKNOWN.";
      return result;
    }
    if (distance != std::numeric_limits<int64_t>::max() && distance <= window) {
      coupled += input.rules.sample_step_nm;
    } else {
      uncoupled += input.rules.sample_step_nm;
    }
  }
  for (const Sample& sample : n_samples) {
    const int64_t distance =
        coupling_distance(p_geometry, sample, nodes,
                          input.rules.max_search_nodes);
    if (distance < 0) {
      budget_hit = true;
      result.reason = "SEARCH_BUDGET";
      result.message = "Coupling search exceeded max_search_nodes; the pair is "
                       "UNKNOWN.";
      return result;
    }
    if (distance != std::numeric_limits<int64_t>::max() && distance <= window) {
      coupled += input.rules.sample_step_nm;
    } else {
      uncoupled += input.rules.sample_step_nm;
    }
  }
  // Both directions are sampled, so halve the paired accounting.
  result.coupled_nm = coupled / 2;
  result.uncoupled_nm = uncoupled / 2;
  result.sampled_step_nm = input.rules.sample_step_nm;

  // Corner matching: an unmatched corner is an uncompensated turn.
  std::vector<Corner> p_corners = corners_of(p_geometry);
  std::vector<Corner> n_corners = corners_of(n_geometry);
  result.p_corners = static_cast<int>(p_corners.size());
  result.n_corners = static_cast<int>(n_corners.size());
  const int64_t corner_window =
      std::max<int64_t>(window * 2, input.rules.sample_step_nm * 3);
  int64_t turn_delta = 0;
  for (Corner& corner : p_corners) {
    int64_t best = std::numeric_limits<int64_t>::max();
    Corner* match = nullptr;
    for (Corner& other : n_corners) {
      if (other.layer != corner.layer) continue;
      bool exact = false;
      const int64_t distance =
          point_point_distance(corner.point, other.point, exact);
      if (distance <= corner_window && distance < best) {
        best = distance;
        match = &other;
      }
    }
    if (match == nullptr) continue;
    corner.matched = true;
    match->matched = true;
    const int64_t delta = corner.arc_nm > match->arc_nm
                              ? corner.arc_nm - match->arc_nm
                              : match->arc_nm - corner.arc_nm;
    turn_delta = std::max(turn_delta, delta);
  }
  int unmatched = 0;
  for (const Corner& corner : p_corners) {
    if (!corner.matched) ++unmatched;
  }
  for (const Corner& corner : n_corners) {
    if (!corner.matched) ++unmatched;
  }
  result.unmatched_corners = unmatched;
  result.turn_delta_nm = turn_delta;
  result.turn_compensated =
      unmatched == 0 && turn_delta <= std::max<int64_t>(
                                        input.rules.sample_step_nm,
                                        pair.skew_tolerance_nm);

  // Layer transitions must be paired: same span, next to each other.
  const int64_t via_window =
      window + input.rules.terminal_snap_nm;
  std::vector<const HighspeedVia*> p_vias;
  std::vector<const HighspeedVia*> n_vias;
  for (const HighspeedVia& via : input.vias) {
    if (via.net == pair.net_p) p_vias.push_back(&via);
    if (via.net == pair.net_n) n_vias.push_back(&via);
  }
  result.p_vias = static_cast<int>(p_vias.size());
  result.n_vias = static_cast<int>(n_vias.size());
  std::vector<bool> used(n_vias.size(), false);
  for (const HighspeedVia* via : p_vias) {
    bool matched = false;
    for (size_t index = 0; index < n_vias.size(); ++index) {
      if (used[index]) continue;
      const HighspeedVia* other = n_vias[index];
      if (other->start_layer != via->start_layer) continue;
      if (other->end_layer != via->end_layer) continue;
      bool exact = false;
      const int64_t distance =
          point_point_distance(via->center, other->center, exact);
      if (distance > via_window) continue;
      used[index] = true;
      matched = true;
      ++result.shared_vias;
      break;
    }
    if (!matched) result.unpaired_vias.push_back(via->id);
  }
  for (size_t index = 0; index < n_vias.size(); ++index) {
    if (!used[index]) result.unpaired_vias.push_back(n_vias[index]->id);
  }
  result.paired_layers = result.unpaired_vias.empty();
  result.measured = true;
  return result;
}

}  // namespace highspeed_detail
}  // namespace mota
