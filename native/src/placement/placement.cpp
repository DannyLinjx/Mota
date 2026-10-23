#include "mota/placement.hpp"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <map>
#include <optional>
#include <set>
#include <tuple>

#include "mota/sha256.hpp"

namespace mota {
namespace {

constexpr double kEpsilon = 1e-9;

struct Candidate {
  Vec2 point{};
  int rotation_deg = 0;
};

struct Snapshot {
  std::vector<Vec2> positions;
  std::vector<int> rotations;
  std::vector<bool> placed;
};

struct DecouplingPair {
  size_t cap = 0;
  size_t target = 0;
  int64_t max_nm = 0;
};

Aabb outline_bounds(const std::vector<Vec2>& outline) {
  Aabb bounds{outline[0].x, outline[0].y, outline[0].x, outline[0].y};
  for (const Vec2& point : outline) {
    bounds = bounds.merged(Aabb{point.x, point.y, point.x, point.y});
  }
  return bounds;
}

Aabb courtyard(const PlacementComponent& component, int64_t x, int64_t y,
               int rotation_deg) {
  const bool swapped = ((rotation_deg % 180) + 180) % 180 == 90;
  const int64_t width = swapped ? component.h_nm : component.w_nm;
  const int64_t height = swapped ? component.w_nm : component.h_nm;
  return Aabb{x - width / 2, y - height / 2, x + width / 2, y + height / 2};
}

bool inside_outline(const Aabb& area, const std::vector<Vec2>& outline) {
  const Vec2 corners[4] = {
      {area.min_x, area.min_y}, {area.max_x, area.min_y},
      {area.max_x, area.max_y}, {area.min_x, area.max_y}};
  for (const Vec2& corner : corners) {
    if (!polygon_contains(outline, corner)) return false;
  }
  return true;
}

bool overlaps(const Aabb& a, const Aabb& b, int64_t gap) {
  return a.min_x <= b.max_x + gap && b.min_x <= a.max_x + gap &&
         a.min_y <= b.max_y + gap && b.min_y <= a.max_y + gap;
}

int64_t distance_squared(const Vec2& a, const Vec2& b) {
  const __int128 dx = static_cast<__int128>(a.x) - b.x;
  const __int128 dy = static_cast<__int128>(a.y) - b.y;
  const __int128 value = dx * dx + dy * dy;
  return value > std::numeric_limits<int64_t>::max()
             ? std::numeric_limits<int64_t>::max()
             : static_cast<int64_t>(value);
}

double escape_pressure(const PlacementComponent& component) {
  const int64_t perimeter = 2 * (component.w_nm + component.h_nm);
  if (perimeter <= 0) return 0.0;
  const double pitch = component.pin_pitch_nm > 0
                           ? static_cast<double>(component.pin_pitch_nm)
                           : 1'000'000.0;
  return static_cast<double>(component.pins) * pitch /
         static_cast<double>(perimeter);
}

class Solver {
 public:
  explicit Solver(const PlacementInput& input) : input_(input) {
    components_ = input_.components;
    positions_.resize(components_.size());
    rotations_.resize(components_.size());
    placed_.assign(components_.size(), false);
    if (input_.outline.size() >= 3) {
      outline_ = input_.outline;
    } else if (input_.board_width_nm > 0 && input_.board_height_nm > 0) {
      const int64_t w = static_cast<int64_t>(input_.board_width_nm);
      const int64_t h = static_cast<int64_t>(input_.board_height_nm);
      outline_ = {{0, 0}, {w, 0}, {w, h}, {0, h}};
    }
    if (outline_.size() >= 3) board_ = outline_bounds(outline_);
    for (const ModuleRegion& region : input_.module_regions) {
      regions_.emplace(region.module, region.area);
    }
    build_partitions();
    for (size_t i = 0; i < components_.size(); ++i) {
      for (const std::string& target_id : components_[i].decoupling_for) {
        const auto found = index_of(target_id);
        if (!found.has_value()) {
          diagnostics_.push_back("DECOUPLING_TARGET_UNKNOWN:" +
                                 components_[i].id + ":" + target_id);
          continue;
        }
        pairs_.push_back(DecouplingPair{
            i, *found,
            input_.rules.decoupling_max_nm > 0
                ? input_.rules.decoupling_max_nm
                : 3'000'000});
      }
    }
    build_order();
  }

  PlacementResult solve() {
    PlacementResult result;
    result.seed = input_.rules.seed;
    if (components_.empty()) {
      result.status = "SUCCEEDED";
      result.diagnostics.push_back("NO_COMPONENTS");
      finish(&result);
      return result;
    }
    if (outline_.size() < 3) {
      result.status = "INFEASIBLE";
      result.hard_violations.push_back("BOARD_OUTLINE_MISSING");
      finish(&result);
      return result;
    }
    if (!validate_fixed(&result)) {
      result.status = "INFEASIBLE";
      finish(&result);
      return result;
    }
    if (order_.empty()) {
      result.status = "SUCCEEDED";
      result.patch = snapshot_moves();
      finish(&result);
      return result;
    }

    candidates_.resize(components_.size());
    for (size_t index : order_) {
      candidates_[index] = build_candidates(index);
    }

    dfs(0);
    if (found_) {
      local_optimize();
      result.status = "SUCCEEDED";
      result.patch = snapshot_moves();
      search_complete_ = false;
    } else if (budget_hit_) {
      result.status = best_depth_ > 0 ? "PARTIAL" : "UNKNOWN";
      result.diagnostics.push_back("SEARCH_BUDGET_EXHAUSTED");
      result.patch = best_depth_ > 0 ? best_partial_moves()
                                     : snapshot_moves();
      for (size_t index : order_) {
        if (!placed_[index]) {
          result.unplaced_components.push_back(components_[index].id);
        }
      }
    } else {
      result.status = "INFEASIBLE";
      result.hard_violations.push_back("NO_LEGAL_PLACEMENT");
      result.patch = snapshot_moves();
    }
    if (!found_ || !search_complete_) {
      result.search_exhausted = false;
    } else {
      result.search_exhausted = true;
    }
    result.search_nodes = nodes_;
    for (const std::string& item : diagnostics_) result.diagnostics.push_back(item);
    finish(&result);
    return result;
  }

 private:
  std::optional<size_t> index_of(const std::string& id) const {
    for (size_t i = 0; i < components_.size(); ++i) {
      if (components_[i].id == id) return i;
    }
    return std::nullopt;
  }

  void build_partitions() {
    // Explicit module regions are hard. Without them, derive soft bands from
    // module membership so a module's parts stay together; wirelength and
    // congestion decide inside the band.
    if (!input_.module_regions.empty() || outline_.size() < 3) return;
    std::map<std::string, int> counts;
    for (const PlacementComponent& component : components_) {
      if (!component.module.empty()) ++counts[component.module];
    }
    if (counts.size() < 2) return;
    std::vector<std::pair<std::string, int>> modules(counts.begin(), counts.end());
    std::sort(modules.begin(), modules.end(),
              [](const auto& a, const auto& b) {
                if (a.second != b.second) return a.second > b.second;
                return a.first < b.first;
              });
    int total = 0;
    for (const auto& entry : modules) total += entry.second;
    if (total <= 0) return;
    const int64_t inset = input_.rules.edge_clearance_nm;
    int64_t cursor = board_.min_x + inset;
    const int64_t usable = std::max<int64_t>(1, board_.width() - 2 * inset);
    for (size_t i = 0; i < modules.size(); ++i) {
      const int64_t span =
          i + 1 == modules.size()
              ? board_.max_x - inset - cursor
              : usable * modules[i].second / total;
      bands_[modules[i].first] =
          Aabb{cursor, board_.min_y + inset,
               cursor + std::max<int64_t>(1, span), board_.max_y - inset};
      cursor += std::max<int64_t>(1, span);
    }
  }

  void build_order() {
    std::vector<size_t> targets;
    std::vector<size_t> caps;
    for (size_t i = 0; i < components_.size(); ++i) {
      if (components_[i].fixed) continue;
      if (components_[i].decoupling_for.empty()) {
        targets.push_back(i);
      } else {
        caps.push_back(i);
      }
    }
    auto by_priority = [&](size_t a, size_t b) {
      if (components_[a].critical != components_[b].critical) {
        return components_[a].critical > components_[b].critical;
      }
      if (components_[a].pins != components_[b].pins) {
        return components_[a].pins > components_[b].pins;
      }
      return components_[a].id < components_[b].id;
    };
    std::sort(targets.begin(), targets.end(), by_priority);
    std::sort(caps.begin(), caps.end(), by_priority);
    order_ = targets;
    order_.insert(order_.end(), caps.begin(), caps.end());
  }

  bool validate_fixed(PlacementResult* result) {
    bool ok = true;
    for (size_t i = 0; i < components_.size(); ++i) {
      const PlacementComponent& component = components_[i];
      if (component.w_nm <= 0 || component.h_nm <= 0) {
        result->hard_violations.push_back("INVALID_COURTYARD:" + component.id);
        ok = false;
        continue;
      }
      if (!component.fixed) continue;
      positions_[i] = {component.x_nm, component.y_nm};
      rotations_[i] = component.rotation_deg;
      placed_[i] = true;
      const Aabb area = courtyard(component, component.x_nm, component.y_nm,
                                  component.rotation_deg);
      if (!inside_outline(area, outline_) ||
          area.min_x < board_.min_x + input_.rules.edge_clearance_nm ||
          area.max_x > board_.max_x - input_.rules.edge_clearance_nm ||
          area.min_y < board_.min_y + input_.rules.edge_clearance_nm ||
          area.max_y > board_.max_y - input_.rules.edge_clearance_nm) {
        result->hard_violations.push_back("FIXED_OUTSIDE_BOARD:" + component.id);
        ok = false;
      }
      const auto region = regions_.find(component.module);
      if (region != regions_.end() && !region->second.contains(area)) {
        result->hard_violations.push_back("FIXED_OUTSIDE_MODULE:" + component.id +
                                          ":" + component.module);
        ok = false;
      }
      for (const PlacementKeepout& keepout : input_.keepouts) {
        if (area.intersects(keepout.area)) {
          result->hard_violations.push_back("FIXED_IN_KEEPOUT:" +
                                            component.id + ":" + keepout.id);
          ok = false;
        }
      }
    }
    for (size_t i = 0; i < components_.size() && ok; ++i) {
      if (!components_[i].fixed) continue;
      const Aabb a = courtyard(components_[i], positions_[i].x, positions_[i].y,
                               rotations_[i]);
      for (size_t j = i + 1; j < components_.size(); ++j) {
        if (!components_[j].fixed) continue;
        const Aabb b = courtyard(components_[j], components_[j].x_nm,
                                 components_[j].y_nm, components_[j].rotation_deg);
        if (overlaps(a, b, input_.rules.courtyard_gap_nm)) {
          result->hard_violations.push_back("FIXED_OVERLAP:" +
                                            components_[i].id + ":" +
                                            components_[j].id);
          ok = false;
        }
      }
    }
    for (const DecouplingPair& pair : pairs_) {
      if (!components_[pair.cap].fixed || !components_[pair.target].fixed) continue;
      if (distance_squared(positions_[pair.cap], positions_[pair.target]) >
          static_cast<int64_t>(pair.max_nm) * pair.max_nm) {
        result->hard_violations.push_back(
            "DECOUPLING_DISTANCE:" + components_[pair.cap].id + ":" +
            components_[pair.target].id);
        ok = false;
      }
    }
    return ok;
  }

  std::vector<Candidate> build_candidates(size_t index) const {
    const PlacementComponent& component = components_[index];
    std::vector<Candidate> out;
    int64_t min_x = board_.min_x + input_.rules.edge_clearance_nm;
    int64_t max_x = board_.max_x - input_.rules.edge_clearance_nm;
    int64_t min_y = board_.min_y + input_.rules.edge_clearance_nm;
    int64_t max_y = board_.max_y - input_.rules.edge_clearance_nm;
    const auto region = regions_.find(component.module);
    if (region != regions_.end()) {
      min_x = std::max(min_x, region->second.min_x);
      max_x = std::min(max_x, region->second.max_x);
      min_y = std::max(min_y, region->second.min_y);
      max_y = std::min(max_y, region->second.max_y);
    }
    const int64_t step = std::max<int64_t>(1, input_.rules.grid_nm);
    std::set<std::tuple<int64_t, int64_t, int>> seen;
    const bool rotate = component.w_nm != component.h_nm;
    for (int rotation : (rotate ? std::vector<int>{0, 90} : std::vector<int>{0})) {
      const bool swapped = rotation == 90;
      const int64_t width = swapped ? component.h_nm : component.w_nm;
      const int64_t height = swapped ? component.w_nm : component.h_nm;
      const int64_t start_x = min_x + width / 2;
      const int64_t end_x = max_x - width / 2;
      const int64_t start_y = min_y + height / 2;
      const int64_t end_y = max_y - height / 2;
      if (end_x < start_x || end_y < start_y) continue;
      const int64_t count_x = (end_x - start_x) / step + 1;
      const int64_t count_y = (end_y - start_y) / step + 1;
      const int64_t total = count_x * count_y;
      const int64_t cap = std::max<int64_t>(1, input_.rules.max_candidates_per_component);
      const int64_t stride = total > cap ? (total + cap - 1) / cap : 1;
      for (int64_t iy = 0; iy < count_y; ++iy) {
        for (int64_t ix = 0; ix < count_x; ++ix) {
          if (stride > 1 && (ix + iy * count_x) % stride != 0) continue;
          const Vec2 point{start_x + ix * step, start_y + iy * step};
          const Aabb area = courtyard(component, point.x, point.y, rotation);
          if (!inside_outline(area, outline_)) continue;
          bool blocked = false;
          for (const PlacementKeepout& keepout : input_.keepouts) {
            if (area.intersects(keepout.area)) {
              blocked = true;
              break;
            }
          }
          if (blocked) continue;
          if (region != regions_.end() && !region->second.contains(area)) continue;
          if (seen.insert({point.x, point.y, rotation}).second) {
            out.push_back(Candidate{point, rotation});
          }
        }
      }
    }
    const Aabb current = courtyard(component, component.x_nm, component.y_nm,
                                   component.rotation_deg);
    bool current_blocked = false;
    for (const PlacementKeepout& keepout : input_.keepouts) {
      if (current.intersects(keepout.area)) {
        current_blocked = true;
        break;
      }
    }
    if (region != regions_.end() && !region->second.contains(current)) {
      current_blocked = true;
    }
    if (!current_blocked && inside_outline(current, outline_) &&
        current.min_x >= board_.min_x + input_.rules.edge_clearance_nm &&
        current.max_x <= board_.max_x - input_.rules.edge_clearance_nm &&
        current.min_y >= board_.min_y + input_.rules.edge_clearance_nm &&
        current.max_y <= board_.max_y - input_.rules.edge_clearance_nm &&
        seen.insert({component.x_nm, component.y_nm, component.rotation_deg}).second) {
      out.push_back(Candidate{{component.x_nm, component.y_nm},
                              component.rotation_deg});
    }
    return out;
  }

  bool dynamic_ok(size_t index, const Candidate& candidate) const {
    const Aabb area = courtyard(components_[index], candidate.point.x,
                                candidate.point.y, candidate.rotation_deg);
    for (size_t j = 0; j < components_.size(); ++j) {
      if (j == index || !placed_[j]) continue;
      const Aabb other = courtyard(components_[j], positions_[j].x,
                                   positions_[j].y, rotations_[j]);
      if (overlaps(area, other, input_.rules.courtyard_gap_nm)) return false;
    }
    for (const DecouplingPair& pair : pairs_) {
      if (pair.cap == index && placed_[pair.target]) {
        if (distance_squared(candidate.point, positions_[pair.target]) >
            static_cast<int64_t>(pair.max_nm) * pair.max_nm) {
          return false;
        }
      }
      if (pair.target == index && placed_[pair.cap]) {
        if (distance_squared(candidate.point, positions_[pair.cap]) >
            static_cast<int64_t>(pair.max_nm) * pair.max_nm) {
          return false;
        }
      }
    }
    return true;
  }

  double partial_score(size_t index, const Candidate& candidate) const {
    const PlacementComponent& component = components_[index];
    double score = escape_pressure(component);
    score += 0.05 * std::sqrt(static_cast<double>(distance_squared(
                       candidate.point, {component.x_nm, component.y_nm}))) /
             1e6;
    if (!component.module.empty()) {
      const auto region = regions_.find(component.module);
      if (region != regions_.end()) {
        const Vec2 center{region->second.center_x(), region->second.center_y()};
        score += std::sqrt(static_cast<double>(
                     distance_squared(candidate.point, center))) /
                 1e6;
      }
      const auto band = bands_.find(component.module);
      if (band != bands_.end()) {
        const Vec2 center{band->second.center_x(), band->second.center_y()};
        score += 0.25 * std::sqrt(static_cast<double>(
                           distance_squared(candidate.point, center))) /
                 1e6;
      }
    }
    for (const PlacementNet& net : input_.nets) {
      if (std::find(net.components.begin(), net.components.end(),
                    component.id) == net.components.end()) {
        continue;
      }
      int64_t min_x = candidate.point.x;
      int64_t max_x = candidate.point.x;
      int64_t min_y = candidate.point.y;
      int64_t max_y = candidate.point.y;
      int connected = 0;
      for (const std::string& id : net.components) {
        const auto other = index_of(id);
        if (!other.has_value() || !placed_[*other]) continue;
        min_x = std::min(min_x, positions_[*other].x);
        max_x = std::max(max_x, positions_[*other].x);
        min_y = std::min(min_y, positions_[*other].y);
        max_y = std::max(max_y, positions_[*other].y);
        ++connected;
      }
      if (connected > 0) {
        score += static_cast<double>((max_x - min_x) + (max_y - min_y)) / 1e6;
      }
    }
    return score;
  }

  void dfs(size_t depth) {
    if (budget_hit_ || found_) return;
    if (depth > static_cast<size_t>(best_depth_)) {
      best_depth_ = static_cast<int>(depth);
      best_partial_ = snapshot();
    }
    if (depth == order_.size()) {
      found_ = true;
      return;
    }
    const size_t index = order_[depth];
    std::vector<Candidate> feasible;
    feasible.reserve(candidates_[index].size());
    for (const Candidate& candidate : candidates_[index]) {
      if (dynamic_ok(index, candidate)) feasible.push_back(candidate);
    }
    std::sort(feasible.begin(), feasible.end(),
              [&](const Candidate& a, const Candidate& b) {
                const double sa = partial_score(index, a);
                const double sb = partial_score(index, b);
                if (std::abs(sa - sb) > kEpsilon) return sa < sb;
                return std::tie(a.point.x, a.point.y, a.rotation_deg) <
                       std::tie(b.point.x, b.point.y, b.rotation_deg);
              });
    for (const Candidate& candidate : feasible) {
      if (nodes_ >= input_.rules.max_search_nodes) {
        budget_hit_ = true;
        return;
      }
      ++nodes_;
      positions_[index] = candidate.point;
      rotations_[index] = candidate.rotation_deg;
      placed_[index] = true;
      dfs(depth + 1);
      if (found_ || budget_hit_) return;
      placed_[index] = false;
    }
  }

  void local_optimize() {
    for (int round = 0; round < input_.rules.local_rounds; ++round) {
      bool improved = false;
      for (size_t index : order_) {
        if (!placed_[index]) continue;
        const Candidate current{positions_[index], rotations_[index]};
        Candidate best = current;
        double best_cost = evaluate();
        for (const Candidate& candidate : candidates_[index]) {
          if (candidate.point.x == current.point.x &&
              candidate.point.y == current.point.y &&
              candidate.rotation_deg == current.rotation_deg) {
            continue;
          }
          placed_[index] = false;
          const bool allowed = dynamic_ok(index, candidate);
          placed_[index] = true;
          if (!allowed) continue;
          positions_[index] = candidate.point;
          rotations_[index] = candidate.rotation_deg;
          const double cost = evaluate();
          if (cost < best_cost - kEpsilon) {
            best_cost = cost;
            best = candidate;
          }
        }
        if (best.point.x != current.point.x ||
            best.point.y != current.point.y ||
            best.rotation_deg != current.rotation_deg) {
          positions_[index] = best.point;
          rotations_[index] = best.rotation_deg;
          improved = true;
        } else {
          positions_[index] = current.point;
          rotations_[index] = current.rotation_deg;
        }
      }
      if (!improved) break;
    }
  }

  CongestionReport congestion_at() const {
    return congestion_at(positions_, placed_);
  }

  CongestionReport congestion_at(const std::vector<Vec2>& positions,
                                 const std::vector<bool>& placed) const {
    CongestionReport report;
    const int64_t cell = std::max<int64_t>(1, input_.rules.congestion_cell_nm);
    report.cell_nm = cell;
    const int64_t width = std::max<int64_t>(1, board_.width());
    const int64_t height = std::max<int64_t>(1, board_.height());
    report.columns = static_cast<int>(std::min<int64_t>(200, (width + cell - 1) / cell));
    report.rows = static_cast<int>(std::min<int64_t>(200, (height + cell - 1) / cell));
    std::vector<double> loads(static_cast<size_t>(report.columns) * report.rows, 0.0);
    auto add_line = [&](const Vec2& a, const Vec2& b, double weight) {
      const int64_t dx = b.x - a.x;
      const int64_t dy = b.y - a.y;
      const int64_t steps = std::max<int64_t>(
          1, std::max(std::llabs(dx), std::llabs(dy)) / cell);
      for (int64_t i = 0; i <= steps; ++i) {
        const int64_t x = a.x + dx * i / steps;
        const int64_t y = a.y + dy * i / steps;
        const int column = static_cast<int>(
            std::clamp<int64_t>((x - board_.min_x) / cell, 0,
                                report.columns - 1));
        const int row = static_cast<int>(
            std::clamp<int64_t>((y - board_.min_y) / cell, 0,
                                report.rows - 1));
        loads[static_cast<size_t>(row) * report.columns + column] += weight;
      }
    };
    for (const PlacementNet& net : input_.nets) {
      std::vector<Vec2> centers;
      for (const std::string& id : net.components) {
        const auto index = index_of(id);
        if (!index.has_value() || !placed[*index]) continue;
        centers.push_back(positions[*index]);
      }
      if (centers.size() < 2) continue;
      const double weight = 1.0 / static_cast<double>(centers.size() - 1);
      for (size_t i = 1; i < centers.size(); ++i) {
        add_line(centers[0], centers[i], weight);
      }
    }
    for (size_t i = 0; i < components_.size(); ++i) {
      if (!placed[i]) continue;
      const int column = static_cast<int>(std::clamp<int64_t>(
          (positions[i].x - board_.min_x) / cell, 0, report.columns - 1));
      const int row = static_cast<int>(std::clamp<int64_t>(
          (positions[i].y - board_.min_y) / cell, 0, report.rows - 1));
      loads[static_cast<size_t>(row) * report.columns + column] +=
          escape_pressure(components_[i]) * 0.1;
    }
    double total = 0.0;
    for (double load : loads) {
      report.max_load = std::max(report.max_load, load);
      total += load;
    }
    report.mean_load = loads.empty() ? 0.0 : total / static_cast<double>(loads.size());
    std::vector<std::pair<double, size_t>> ranked;
    ranked.reserve(loads.size());
    for (size_t i = 0; i < loads.size(); ++i) {
      if (loads[i] > 0) ranked.emplace_back(loads[i], i);
    }
    std::sort(ranked.begin(), ranked.end(),
              [](const auto& a, const auto& b) {
                if (std::abs(a.first - b.first) > kEpsilon) return a.first > b.first;
                return a.second < b.second;
              });
    for (size_t i = 0; i < std::min<size_t>(10, ranked.size()); ++i) {
      const size_t offset = ranked[i].second;
      const int row = static_cast<int>(offset / report.columns);
      const int column = static_cast<int>(offset % report.columns);
      report.hot_cells.push_back(CongestionCell{
          column, row, board_.min_x + column * cell,
          board_.min_y + row * cell, ranked[i].first});
    }
    return report;
  }

  double evaluate() const {
    const CongestionReport congestion = congestion_at();
    double hpwl = 0.0;
    for (const PlacementNet& net : input_.nets) {
      int64_t min_x = std::numeric_limits<int64_t>::max();
      int64_t max_x = std::numeric_limits<int64_t>::min();
      int64_t min_y = std::numeric_limits<int64_t>::max();
      int64_t max_y = std::numeric_limits<int64_t>::min();
      int count = 0;
      for (const std::string& id : net.components) {
        const auto index = index_of(id);
        if (!index.has_value() || !placed_[*index]) continue;
        min_x = std::min(min_x, positions_[*index].x);
        max_x = std::max(max_x, positions_[*index].x);
        min_y = std::min(min_y, positions_[*index].y);
        max_y = std::max(max_y, positions_[*index].y);
        ++count;
      }
      if (count >= 2) hpwl += static_cast<double>((max_x - min_x) + (max_y - min_y));
    }
    const double diagonal = std::max<double>(
        1.0, std::sqrt(static_cast<double>(board_.width()) * board_.width() +
                       static_cast<double>(board_.height()) * board_.height()));
    double escape = 0.0;
    int placed_count = 0;
    for (size_t i = 0; i < components_.size(); ++i) {
      if (!placed_[i]) continue;
      escape += escape_pressure(components_[i]);
      ++placed_count;
    }
    if (placed_count > 0) escape /= placed_count;
    double module_cost = 0.0;
    std::map<std::string, std::vector<size_t>> modules;
    for (size_t i = 0; i < components_.size(); ++i) {
      if (!placed_[i] || components_[i].module.empty()) continue;
      modules[components_[i].module].push_back(i);
    }
    for (const auto& entry : modules) {
      if (entry.second.size() < 2) continue;
      double cx = 0.0;
      double cy = 0.0;
      for (size_t index : entry.second) {
        cx += static_cast<double>(positions_[index].x);
        cy += static_cast<double>(positions_[index].y);
      }
      cx /= static_cast<double>(entry.second.size());
      cy /= static_cast<double>(entry.second.size());
      for (size_t index : entry.second) {
        const double dx = static_cast<double>(positions_[index].x) - cx;
        const double dy = static_cast<double>(positions_[index].y) - cy;
        module_cost += std::sqrt(dx * dx + dy * dy);
      }
    }
    if (placed_count > 0) {
      module_cost /= static_cast<double>(placed_count) * diagonal;
    }
    double band_cost = 0.0;
    for (size_t i = 0; i < components_.size(); ++i) {
      if (!placed_[i]) continue;
      const auto band = bands_.find(components_[i].module);
      if (band == bands_.end()) continue;
      const Vec2 center{band->second.center_x(), band->second.center_y()};
      band_cost += std::sqrt(static_cast<double>(
          distance_squared(positions_[i], center)));
    }
    if (placed_count > 0) {
      band_cost /= static_cast<double>(placed_count) * diagonal;
    }
    double move_cost = 0.0;
    for (size_t i = 0; i < components_.size(); ++i) {
      if (!placed_[i]) continue;
      const Vec2 initial{components_[i].x_nm, components_[i].y_nm};
      move_cost += std::sqrt(static_cast<double>(
          distance_squared(positions_[i], initial)));
    }
    if (placed_count > 0) {
      move_cost /= static_cast<double>(placed_count) * diagonal;
    }
    return input_.rules.wirelength_weight * (hpwl / diagonal) +
           input_.rules.congestion_weight *
               (congestion.max_load * 0.25 + congestion.mean_load * 0.5) +
           input_.rules.escape_weight * escape +
           input_.rules.module_weight * module_cost +
           input_.rules.module_weight * 0.5 * band_cost +
           input_.rules.move_weight * move_cost;
  }

  Snapshot snapshot() const {
    return Snapshot{positions_, rotations_, placed_};
  }

  std::vector<PlacementMove> snapshot_moves() const {
    std::vector<PlacementMove> moves;
    for (size_t i = 0; i < components_.size(); ++i) {
      if (!placed_[i]) continue;
      moves.push_back(PlacementMove{components_[i].id, positions_[i].x,
                                    positions_[i].y, rotations_[i],
                                    components_[i].fixed});
    }
    return moves;
  }

  std::vector<PlacementMove> best_partial_moves() const {
    std::vector<PlacementMove> moves;
    if (best_partial_.positions.empty()) return moves;
    for (size_t i = 0; i < components_.size(); ++i) {
      if (!best_partial_.placed[i]) continue;
      moves.push_back(PlacementMove{
          components_[i].id, best_partial_.positions[i].x,
          best_partial_.positions[i].y, best_partial_.rotations[i],
          components_[i].fixed});
    }
    return moves;
  }

  void finish(PlacementResult* result) {
    if (result->status == "SUCCEEDED" || result->status == "PARTIAL") {
      const std::vector<Vec2>& positions =
          result->status == "SUCCEEDED" ? positions_ : best_partial_.positions;
      const std::vector<bool>& placed =
          result->status == "SUCCEEDED" ? placed_ : best_partial_.placed;
      result->congestion = congestion_at(positions, placed);
      double hpwl = 0.0;
      for (const PlacementNet& net : input_.nets) {
        int64_t min_x = std::numeric_limits<int64_t>::max();
        int64_t max_x = std::numeric_limits<int64_t>::min();
        int64_t min_y = std::numeric_limits<int64_t>::max();
        int64_t max_y = std::numeric_limits<int64_t>::min();
        int count = 0;
        for (const std::string& id : net.components) {
          const auto index = index_of(id);
          if (!index.has_value() || !placed[*index]) continue;
          min_x = std::min(min_x, positions[*index].x);
          max_x = std::max(max_x, positions[*index].x);
          min_y = std::min(min_y, positions[*index].y);
          max_y = std::max(max_y, positions[*index].y);
          ++count;
        }
        if (count >= 2) {
          hpwl += static_cast<double>((max_x - min_x) + (max_y - min_y));
        }
      }
      result->wirelength_nm = hpwl;
      double escape = 0.0;
      int count = 0;
      for (size_t i = 0; i < components_.size(); ++i) {
        if (!placed[i]) continue;
        escape += escape_pressure(components_[i]);
        ++count;
      }
      result->escape_pressure = count ? escape / count : 0.0;
    } else {
      result->congestion = congestion_at();
    }
    result->moves = static_cast<int64_t>(result->patch.size());
    result->search_nodes = nodes_;
    result->search_exhausted = search_complete_ && !budget_hit_;
    std::vector<PlacementMove> sorted = result->patch;
    std::sort(sorted.begin(), sorted.end(),
              [](const PlacementMove& a, const PlacementMove& b) {
                return a.component_id < b.component_id;
              });
    std::string canonical = result->status + "|" +
                            std::to_string(input_.rules.seed) + "|";
    for (const PlacementMove& move : sorted) {
      canonical += move.component_id + "," + std::to_string(move.x_nm) + "," +
                   std::to_string(move.y_nm) + "," +
                   std::to_string(move.rotation_deg) + "," +
                   (move.fixed ? "1" : "0") + ";";
    }
    for (const std::string& item : result->hard_violations) {
      canonical += "!" + item + ";";
    }
    canonical += "max=" + std::to_string(result->congestion.max_load);
    canonical += ";mean=" + std::to_string(result->congestion.mean_load);
    result->semantic_hash = sha256_hex(canonical);
  }

  const PlacementInput& input_;
  std::vector<PlacementComponent> components_;
  std::vector<Vec2> outline_;
  Aabb board_{};
  std::map<std::string, Aabb> regions_;
  std::map<std::string, Aabb> bands_;
  std::vector<DecouplingPair> pairs_;
  std::vector<size_t> order_;
  std::vector<std::vector<Candidate>> candidates_;
  std::vector<Vec2> positions_;
  std::vector<int> rotations_;
  std::vector<bool> placed_;
  std::vector<std::string> diagnostics_;
  Snapshot best_partial_;
  int best_depth_ = 0;
  bool found_ = false;
  bool budget_hit_ = false;
  bool search_complete_ = true;
  int64_t nodes_ = 0;
};

}  // namespace

PlacementResult place_components(const PlacementInput& input) {
  Solver solver(input);
  return solver.solve();
}

}  // namespace mota
