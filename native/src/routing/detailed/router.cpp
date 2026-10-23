#include "mota/routing.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <limits>
#include <map>
#include <optional>
#include <queue>
#include <set>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include "mota/connectivity.hpp"
#include "mota/sha256.hpp"
#include "routing/global/corridor.hpp"

namespace mota {
namespace {

using Clock = std::chrono::steady_clock;
constexpr int16_t kBlockedCell = -2;
constexpr int kCoarseFactor = 4;

struct Grid {
  int columns = 0;
  int rows = 0;
  int layers = 0;
  int64_t cell_nm = 1;
  Vec2 origin{};
  std::vector<int16_t> owner;

  size_t count() const {
    return static_cast<size_t>(columns) * rows * layers;
  }
  bool in_bounds(int x, int y, int layer) const {
    return x >= 0 && y >= 0 && x < columns && y < rows && layer >= 0 &&
           layer < layers;
  }
  uint64_t state(int x, int y, int layer) const {
    return (static_cast<uint64_t>(layer) * rows + y) * columns + x;
  }
  void decode(uint64_t state_value, int& x, int& y, int& layer) const {
    const int plane = rows * columns;
    layer = static_cast<int>(state_value / plane);
    const int rem = static_cast<int>(state_value % plane);
    y = rem / columns;
    x = rem % columns;
  }
  bool free(uint64_t state_value, int net) const {
    const int16_t value = owner[state_value];
    return value == -1 || value == net;
  }
  bool mark(uint64_t state_value, int net) {
    int16_t& value = owner[state_value];
    if (net < 0) {
      value = kBlockedCell;
      return false;
    }
    if (value == -1 || value == net) {
      value = static_cast<int16_t>(net);
      return true;
    }
    value = kBlockedCell;
    return false;
  }
  Vec2 center(int x, int y) const {
    return Vec2{origin.x + x * cell_nm + cell_nm / 2,
                origin.y + y * cell_nm + cell_nm / 2};
  }
};

struct TerminalCellSet {
  std::vector<uint64_t> states;
  Vec2 point{};
  bool ok = false;
  std::string reason;
};

struct AStarResult {
  enum class Status { Found, NoPath, Budget };
  Status status = Status::NoPath;
  std::vector<uint64_t> path;
};

struct Attempt {
  bool ok = false;
  std::string reason;
  std::vector<RoutedTrack> tracks;
  std::vector<RoutedVia> vias;
};

struct Evaluation {
  int u_total = 0;
  int short_count = 0;
  double wirelength_nm = 0.0;
  std::vector<NetRouteReport> reports;
  std::vector<std::string> unrouted;
};

struct Candidate {
  std::vector<RoutedTrack> tracks;
  std::vector<RoutedVia> vias;
  Evaluation evaluation;
  std::map<std::string, std::string> failures;
};

struct Context {
  explicit Context(const RoutingInput& routing_input) : input(routing_input) {}
  const RoutingInput& input;
  Grid base;
  std::unordered_map<std::string, int> net_index;
  std::unordered_map<std::string, const RoutingPad*> pad_by_id;
  std::unordered_map<int, int> grid_layer_of_stackup;
  std::vector<std::string> grid_layer_ids;
  std::vector<std::vector<TerminalCellSet>> terminal_cells;
  int64_t budget = 0;
  int expansions = 0;
  Clock::time_point deadline;
  bool budget_hit = false;
  bool timed_out = false;
};

Aabb outline_bounds(const std::vector<Vec2>& outline) {
  Aabb bounds{outline[0].x, outline[0].y, outline[0].x, outline[0].y};
  for (const Vec2& point : outline) {
    bounds = bounds.merged(Aabb{point.x, point.y, point.x, point.y});
  }
  return bounds;
}

std::string layer_id_for(const RoutingInput& input, int grid_layer) {
  if (grid_layer >= 0 &&
      grid_layer < static_cast<int>(input.copper_layer_ids.size())) {
    return input.copper_layer_ids[static_cast<size_t>(grid_layer)];
  }
  return std::to_string(grid_layer);
}

int clamp_cell(int value, int limit) {
  return std::clamp(value, 0, std::max(0, limit - 1));
}

int64_t distance_squared(const Vec2& a, const Vec2& b) {
  const __int128 dx = static_cast<__int128>(a.x) - b.x;
  const __int128 dy = static_cast<__int128>(a.y) - b.y;
  const __int128 value = dx * dx + dy * dy;
  return value > std::numeric_limits<int64_t>::max()
             ? std::numeric_limits<int64_t>::max()
             : static_cast<int64_t>(value);
}

bool stamp_shape(Grid& grid, const Shape& shape, int layer, int net,
                 int64_t expand) {
  if (layer < 0 || layer >= grid.layers) return true;
  const Aabb bounds =
      shape_bounds(shape).bounds.expanded(std::max<int64_t>(0, expand));
  const int x0 = clamp_cell(
      static_cast<int>(std::floor(
          static_cast<double>(bounds.min_x - grid.origin.x) / grid.cell_nm)),
      grid.columns);
  const int x1 = clamp_cell(
      static_cast<int>(std::ceil(
          static_cast<double>(bounds.max_x - grid.origin.x) / grid.cell_nm)),
      grid.columns);
  const int y0 = clamp_cell(
      static_cast<int>(std::floor(
          static_cast<double>(bounds.min_y - grid.origin.y) / grid.cell_nm)),
      grid.rows);
  const int y1 = clamp_cell(
      static_cast<int>(std::ceil(
          static_cast<double>(bounds.max_y - grid.origin.y) / grid.cell_nm)),
      grid.rows);
  bool ok = true;
  for (int y = y0; y <= y1; ++y) {
    for (int x = x0; x <= x1; ++x) {
      const Vec2 point = grid.center(x, y);
      if (shape_distance(shape, make_point(point)).distance_nm <= expand + 1) {
        if (!grid.mark(grid.state(x, y, layer), net)) ok = false;
      }
    }
  }
  return ok;
}

bool stamp_all(Grid& grid, const Shape& shape, int net, int64_t expand) {
  bool ok = true;
  for (int layer = 0; layer < grid.layers; ++layer) {
    if (!stamp_shape(grid, shape, layer, net, expand)) ok = false;
  }
  return ok;
}

bool spans(int start, int end, int layer) {
  if (start > end) std::swap(start, end);
  return layer >= start && layer <= end;
}

bool routable(const Grid& grid, int net, int x, int y, int layer,
              const RoutingRules& rules);

std::optional<Grid> build_grid(
    const RoutingInput& input,
    const std::unordered_map<std::string, int>& net_index, int64_t& cells,
    std::string& diagnostic, std::unordered_map<int, int>& grid_layer_of_stackup,
    std::vector<std::string>& grid_layer_ids) {
  const int64_t cell = std::max<int64_t>(1, input.rules.grid_nm);
  const Aabb bounds = input.outline.size() >= 3
                          ? outline_bounds(input.outline)
                          : Aabb{0, 0, input.width_nm, input.height_nm};
  const int64_t width = input.width_nm > 0 ? input.width_nm : bounds.width();
  const int64_t height = input.height_nm > 0 ? input.height_nm : bounds.height();
  if (width <= 0 || height <= 0 || input.outline.size() < 3) {
    diagnostic = "BOARD_OUTLINE_MISSING";
    return std::nullopt;
  }
  Grid grid;
  grid.cell_nm = cell;
  grid.layers = static_cast<int>(input.copper_layers.size());
  if (grid.layers <= 0) {
    diagnostic = "NO_COPPER_LAYERS";
    return std::nullopt;
  }
  grid.origin = {bounds.min_x, bounds.min_y};
  grid.columns = static_cast<int>((width + cell - 1) / cell);
  grid.rows = static_cast<int>((height + cell - 1) / cell);
  cells = static_cast<int64_t>(grid.columns) * grid.rows * grid.layers;
  if (cells > input.rules.max_grid_cells) {
    diagnostic = "GRID_TOO_LARGE";
    return std::nullopt;
  }
  grid.owner.assign(static_cast<size_t>(cells), -1);
  for (size_t i = 0; i < input.copper_layers.size(); ++i) {
    grid_layer_of_stackup[input.copper_layers[i]] = static_cast<int>(i);
    grid_layer_ids.push_back(
        i < input.copper_layer_ids.size() ? input.copper_layer_ids[i]
                                          : std::to_string(input.copper_layers[i]));
  }

  const int64_t margin =
      input.rules.edge_clearance_nm + input.rules.track_width_nm / 2;
  for (int layer = 0; layer < grid.layers; ++layer) {
    for (int y = 0; y < grid.rows; ++y) {
      for (int x = 0; x < grid.columns; ++x) {
        const Vec2 point = grid.center(x, y);
        if (point.x < bounds.min_x + margin ||
            point.x > bounds.max_x - margin ||
            point.y < bounds.min_y + margin ||
            point.y > bounds.max_y - margin ||
            !polygon_contains(input.outline, point)) {
          grid.owner[grid.state(x, y, layer)] = kBlockedCell;
        }
      }
    }
  }

  const int64_t copper_expand =
      input.rules.clearance_nm + input.rules.track_width_nm / 2;
  for (const RoutingHole& hole : input.holes) {
    stamp_all(grid, hole.shape, -1, copper_expand);
  }
  for (const RoutingKeepout& keepout : input.keepouts) {
    const auto found = grid_layer_of_stackup.find(keepout.stackup_layer);
    if (found == grid_layer_of_stackup.end()) continue;
    stamp_shape(grid, make_polygon(keepout.outline), found->second, -1,
                copper_expand);
  }

  for (const RoutingPad& pad : input.pads) {
    int net = -2;
    const auto found = net_index.find(pad.net);
    if (found != net_index.end() && pad.plated) net = found->second;
    for (const auto& layer_shape : pad.layer_shapes) {
      const auto layer = grid_layer_of_stackup.find(layer_shape.first);
      if (layer == grid_layer_of_stackup.end()) continue;
      stamp_shape(grid, layer_shape.second, layer->second, net, copper_expand);
    }
  }
  for (const RoutingTrack& track : input.tracks) {
    int net = -2;
    const auto found = net_index.find(track.net);
    if (found != net_index.end()) net = found->second;
    const auto layer = grid_layer_of_stackup.find(track.stackup_layer);
    if (layer == grid_layer_of_stackup.end()) continue;
    for (size_t i = 0; i + 1 < track.points.size(); ++i) {
      stamp_shape(grid,
                  make_slot(track.points[i], track.points[i + 1],
                            std::max<int64_t>(1, track.width_nm / 2)),
                  layer->second, net, copper_expand);
    }
  }
  for (const RoutingVia& via : input.vias) {
    int net = -2;
    const auto found = net_index.find(via.net);
    if (found != net_index.end() && via.plated) net = found->second;
    const int start = grid_layer_of_stackup.count(via.start_layer)
                          ? grid_layer_of_stackup.at(via.start_layer)
                          : -1;
    const int end = grid_layer_of_stackup.count(via.end_layer)
                        ? grid_layer_of_stackup.at(via.end_layer)
                        : -1;
    if (net < 0 || start < 0 || end < 0) continue;
    const Shape pad = make_circle(via.center,
                                  std::max<int64_t>(1, via.pad_outer_nm / 2));
    for (int layer = 0; layer < grid.layers; ++layer) {
      if (!spans(start, end, layer)) continue;
      stamp_shape(grid, pad, layer, net, copper_expand);
    }
  }
  return grid;
}

void resolve_terminal_cells(Context& context) {
  const RoutingInput& input = context.input;
  context.terminal_cells.resize(input.nets.size());
  for (size_t net_index = 0; net_index < input.nets.size(); ++net_index) {
    const RoutingNet& net = input.nets[net_index];
    auto& cells = context.terminal_cells[net_index];
    cells.resize(net.terminals.size());
    for (size_t terminal_index = 0; terminal_index < net.terminals.size();
         ++terminal_index) {
      TerminalCellSet& set = cells[terminal_index];
      const std::string& pad_id = net.terminals[terminal_index];
      const auto pad_found = context.pad_by_id.find(pad_id);
      if (pad_found == context.pad_by_id.end()) {
        set.reason = "TERMINAL_NOT_FOUND";
        continue;
      }
      const RoutingPad& pad = *pad_found->second;
      if (!pad.plated) {
        set.reason = "TERMINAL_NOT_PLATED";
        continue;
      }
      if (pad.net != net.net) {
        set.reason = "TERMINAL_NET_MISMATCH";
        continue;
      }
      std::set<uint64_t> unique;
      for (const auto& layer_shape : pad.layer_shapes) {
        const auto layer = context.grid_layer_of_stackup.find(layer_shape.first);
        if (layer == context.grid_layer_of_stackup.end()) continue;
        const Aabb bounds = shape_bounds(layer_shape.second).bounds;
        const Vec2 point{bounds.center_x(), bounds.center_y()};
        if (set.point.x == 0 && set.point.y == 0) set.point = point;
        const int center_x = static_cast<int>(
            (point.x - context.base.origin.x) / context.base.cell_nm);
        const int center_y = static_cast<int>(
            (point.y - context.base.origin.y) / context.base.cell_nm);
        for (int radius = 0; radius <= 3 && unique.empty(); ++radius) {
          for (int dy = -radius; dy <= radius; ++dy) {
            for (int dx = -radius; dx <= radius; ++dx) {
              const int x = center_x + dx;
              const int y = center_y + dy;
              if (!context.base.in_bounds(x, y, layer->second)) continue;
              const Vec2 candidate = context.base.center(x, y);
              if (shape_distance(layer_shape.second, make_point(candidate))
                      .distance_nm > 1) {
                continue;
              }
              const uint64_t state = context.base.state(x, y, layer->second);
              if (routable(context.base, static_cast<int>(net_index), x, y,
                           layer->second, context.input.rules)) {
                unique.insert(state);
              }
            }
          }
        }
        if (unique.empty() && context.base.in_bounds(center_x, center_y,
                                                     layer->second)) {
          const uint64_t state = context.base.state(center_x, center_y,
                                                    layer->second);
          if (routable(context.base, static_cast<int>(net_index), center_x,
                       center_y, layer->second, context.input.rules)) {
            unique.insert(state);
          }
        }
      }
      set.states.assign(unique.begin(), unique.end());
      if (set.states.empty()) {
        set.reason = "TERMINAL_BLOCKED";
      } else {
        set.ok = true;
      }
    }
  }
}

std::unordered_set<int> build_corridor(const Context& context, int net,
                                       Vec2 from, Vec2 to) {
  const int columns = std::max(1, (context.base.columns + kCoarseFactor - 1) /
                                      kCoarseFactor);
  const int rows = std::max(1, (context.base.rows + kCoarseFactor - 1) /
                                   kCoarseFactor);
  std::vector<uint8_t> blocked(static_cast<size_t>(columns) * rows, 0);
  for (int y = 0; y < rows; ++y) {
    for (int x = 0; x < columns; ++x) {
      const int fx = clamp_cell(x * kCoarseFactor + kCoarseFactor / 2,
                                context.base.columns);
      const int fy = clamp_cell(y * kCoarseFactor + kCoarseFactor / 2,
                                context.base.rows);
      bool free = false;
      for (int layer = 0; layer < context.base.layers; ++layer) {
        const uint64_t state = context.base.state(fx, fy, layer);
        if (context.base.free(state, net)) {
          free = true;
          break;
        }
      }
      blocked[static_cast<size_t>(y) * columns + x] = free ? 0 : 1;
    }
  }
  auto coarse = [&](Vec2 point) {
    const int x = clamp_cell(static_cast<int>(
                                 (point.x - context.base.origin.x) /
                                 (context.base.cell_nm * kCoarseFactor)),
                             columns);
    const int y = clamp_cell(static_cast<int>(
                                 (point.y - context.base.origin.y) /
                                 (context.base.cell_nm * kCoarseFactor)),
                             rows);
    return y * columns + x;
  };
  CoarseCorridorRequest request;
  request.columns = columns;
  request.rows = rows;
  request.start = coarse(from);
  request.goal = coarse(to);
  request.blocked = std::move(blocked);
  const std::vector<int> path = plan_corridor(request);
  std::unordered_set<int> corridor;
  for (int index : path) {
    corridor.insert(index);
    const int x = index % columns;
    const int y = index / columns;
    for (int dy = -1; dy <= 1; ++dy) {
      for (int dx = -1; dx <= 1; ++dx) {
        const int nx = x + dx;
        const int ny = y + dy;
        if (nx >= 0 && ny >= 0 && nx < columns && ny < rows) {
          corridor.insert(ny * columns + nx);
        }
      }
    }
  }
  return corridor;
}

bool via_legal(const Grid& grid, int net, int x, int y,
               const RoutingRules& rules) {
  const int radius = std::max(
      1, static_cast<int>(std::ceil(
             (rules.via_pad_nm / 2.0 + rules.clearance_nm) / grid.cell_nm)));
  for (int layer = 0; layer < grid.layers; ++layer) {
    for (int dy = -radius; dy <= radius; ++dy) {
      for (int dx = -radius; dx <= radius; ++dx) {
        const int nx = x + dx;
        const int ny = y + dy;
        if (!grid.in_bounds(nx, ny, layer)) return false;
        if (!grid.free(grid.state(nx, ny, layer), net)) return false;
      }
    }
  }
  return true;
}

bool routable(const Grid& grid, int net, int x, int y, int layer,
              const RoutingRules& rules) {
  if (!grid.in_bounds(x, y, layer)) return false;
  const int radius = std::max(
      1, static_cast<int>(std::ceil(
             (rules.track_width_nm / 2.0 + rules.clearance_nm) / grid.cell_nm)));
  for (int dy = -radius; dy <= radius; ++dy) {
    for (int dx = -radius; dx <= radius; ++dx) {
      const int nx = x + dx;
      const int ny = y + dy;
      if (!grid.in_bounds(nx, ny, layer)) return false;
      if (!grid.free(grid.state(nx, ny, layer), net)) return false;
    }
  }
  return true;
}

struct QueueItem {
  int64_t f = 0;
  uint64_t state = 0;
  bool operator>(const QueueItem& other) const {
    if (f != other.f) return f > other.f;
    return state > other.state;
  }
};

AStarResult astar(const Grid& grid, int net,
                  const std::vector<uint64_t>& starts,
                  const std::unordered_set<uint64_t>& targets,
                  const std::unordered_set<int>& corridor, int coarse_columns,
                  const RoutingRules& rules, int64_t& budget, int& expansions,
                  Clock::time_point deadline, bool& timed_out) {
  AStarResult result;
  if (starts.empty() || targets.empty()) return result;
  int min_x = grid.columns;
  int max_x = 0;
  int min_y = grid.rows;
  int max_y = 0;
  for (uint64_t target : targets) {
    int x = 0;
    int y = 0;
    int layer = 0;
    grid.decode(target, x, y, layer);
    min_x = std::min(min_x, x);
    max_x = std::max(max_x, x);
    min_y = std::min(min_y, y);
    max_y = std::max(max_y, y);
  }
  auto heuristic = [&](uint64_t state) {
    int x = 0;
    int y = 0;
    int layer = 0;
    grid.decode(state, x, y, layer);
    const int dx = x < min_x ? min_x - x : (x > max_x ? x - max_x : 0);
    const int dy = y < min_y ? min_y - y : (y > max_y ? y - max_y : 0);
    return static_cast<int64_t>(dx + dy) * grid.cell_nm;
  };
  std::unordered_map<uint64_t, int64_t> g_score;
  std::unordered_map<uint64_t, uint64_t> parent;
  std::priority_queue<QueueItem, std::vector<QueueItem>,
                      std::greater<QueueItem>>
      queue;
  g_score.reserve(starts.size() * 2 + 1024);
  for (uint64_t start : starts) {
    if (!g_score.count(start)) {
      g_score[start] = 0;
      queue.push(QueueItem{heuristic(start), start});
    }
    if (targets.count(start)) {
      result.status = AStarResult::Status::Found;
      result.path = {start};
      return result;
    }
  }
  const int64_t via_cost =
      static_cast<int64_t>(std::llround(grid.cell_nm * rules.via_cost_factor));
  const int64_t corridor_penalty =
      static_cast<int64_t>(std::llround(grid.cell_nm * rules.corridor_penalty));
  const int dx[4] = {1, -1, 0, 0};
  const int dy[4] = {0, 0, 1, -1};
  while (!queue.empty()) {
    if (budget <= 0) {
      result.status = AStarResult::Status::Budget;
      return result;
    }
    if ((expansions & 1023) == 0 && Clock::now() > deadline) {
      timed_out = true;
      result.status = AStarResult::Status::Budget;
      return result;
    }
    const QueueItem item = queue.top();
    queue.pop();
    const auto current_g = g_score.find(item.state);
    if (current_g == g_score.end()) continue;
    if (item.f != current_g->second + heuristic(item.state)) continue;
    if (targets.count(item.state)) {
      uint64_t cursor = item.state;
      while (true) {
        result.path.push_back(cursor);
        const auto parent_found = parent.find(cursor);
        if (parent_found == parent.end()) break;
        cursor = parent_found->second;
      }
      std::reverse(result.path.begin(), result.path.end());
      result.status = AStarResult::Status::Found;
      return result;
    }
    int x = 0;
    int y = 0;
    int layer = 0;
    grid.decode(item.state, x, y, layer);
    for (int direction = 0; direction < 4; ++direction) {
      const int nx = x + dx[direction];
      const int ny = y + dy[direction];
      if (!grid.in_bounds(nx, ny, layer)) continue;
      const uint64_t next = grid.state(nx, ny, layer);
      if (!routable(grid, net, nx, ny, layer, rules)) continue;
      int64_t cost = grid.cell_nm;
      if (!corridor.empty()) {
        const int coarse = (ny / kCoarseFactor) * coarse_columns +
                           (nx / kCoarseFactor);
        if (!corridor.count(coarse)) cost += corridor_penalty;
      }
      const int64_t candidate = current_g->second + cost;
      auto found = g_score.find(next);
      if (found == g_score.end() || candidate < found->second) {
        g_score[next] = candidate;
        parent[next] = item.state;
        queue.push(QueueItem{candidate + heuristic(next), next});
        ++expansions;
        --budget;
      }
    }
    if (via_legal(grid, net, x, y, rules)) {
      for (int other = 0; other < grid.layers; ++other) {
        if (other == layer) continue;
        const uint64_t next = grid.state(x, y, other);
        if (!grid.free(next, net)) continue;
        const int64_t candidate = current_g->second + via_cost;
        auto found = g_score.find(next);
        if (found == g_score.end() || candidate < found->second) {
          g_score[next] = candidate;
          parent[next] = item.state;
          queue.push(QueueItem{candidate + heuristic(next), next});
          ++expansions;
          --budget;
        }
      }
    }
  }
  result.status = AStarResult::Status::NoPath;
  return result;
}

void geometry_from_path(const RoutingInput& input, const Grid& grid, int net,
                        const std::vector<uint64_t>& path, int& track_counter,
                        int& via_counter, std::vector<RoutedTrack>& tracks,
                        std::vector<RoutedVia>& vias) {
  if (path.size() < 2) return;
  int layer = 0;
  int x = 0;
  int y = 0;
  grid.decode(path.front(), x, y, layer);
  std::vector<Vec2> run{grid.center(x, y)};
  int run_layer = layer;
  auto finish_run = [&]() {
    if (run.size() < 2) return;
    RoutedTrack track;
    track.id = "route:" + input.nets[static_cast<size_t>(net)].net + ":" +
               std::to_string(track_counter++);
    track.net = input.nets[static_cast<size_t>(net)].net;
    track.stackup_layer = input.copper_layers[static_cast<size_t>(run_layer)];
    track.layer_id = layer_id_for(input, run_layer);
    track.width_nm = input.rules.track_width_nm;
    track.points = run;
    tracks.push_back(std::move(track));
  };
  for (size_t i = 1; i < path.size(); ++i) {
    grid.decode(path[i], x, y, layer);
    if (layer != run_layer) {
      finish_run();
      RoutedVia via;
      via.id = "via:" + input.nets[static_cast<size_t>(net)].net + ":" +
               std::to_string(via_counter++);
      via.net = input.nets[static_cast<size_t>(net)].net;
      via.center = grid.center(x, y);
      via.drill_nm = input.rules.via_drill_nm;
      via.pad_outer_nm = input.rules.via_pad_nm;
      via.start_layer = input.copper_layers.front();
      via.end_layer = input.copper_layers.back();
      via.start_layer_id = input.copper_layer_ids.empty()
                               ? std::to_string(via.start_layer)
                               : input.copper_layer_ids.front();
      via.end_layer_id = input.copper_layer_ids.empty()
                             ? std::to_string(via.end_layer)
                             : input.copper_layer_ids.back();
      vias.push_back(std::move(via));
      run.clear();
      run.push_back(grid.center(x, y));
      run_layer = layer;
      continue;
    }
    run.push_back(grid.center(x, y));
    while (run.size() >= 3) {
      const Vec2& a = run[run.size() - 3];
      const Vec2& b = run[run.size() - 2];
      const Vec2& c = run[run.size() - 1];
      const bool same_x = a.x == b.x && b.x == c.x;
      const bool same_y = a.y == b.y && b.y == c.y;
      if (!same_x && !same_y) break;
      run.erase(run.end() - 2);
    }
  }
  finish_run();
}

Attempt route_net(Context& context, Grid& grid, int net_index) {
  const RoutingInput& input = context.input;
  const RoutingNet& net = input.nets[static_cast<size_t>(net_index)];
  Attempt attempt;
  if (net.locked) {
    attempt.ok = true;
    return attempt;
  }
  if (net.terminals.empty()) {
    attempt.ok = false;
    attempt.reason = "NO_TERMINALS";
    return attempt;
  }
  if (net.terminals.size() < 2) {
    attempt.ok = true;
    return attempt;
  }
  const auto& cells = context.terminal_cells[static_cast<size_t>(net_index)];
  if (cells.size() != net.terminals.size()) {
    attempt.reason = "TERMINAL_NOT_FOUND";
    return attempt;
  }
  for (const TerminalCellSet& cell : cells) {
    if (!cell.ok) {
      attempt.reason = cell.reason.empty() ? "TERMINAL_BLOCKED" : cell.reason;
      return attempt;
    }
  }
  std::vector<uint64_t> tree = cells.front().states;
  std::vector<size_t> remaining;
  for (size_t i = 1; i < cells.size(); ++i) remaining.push_back(i);
  std::sort(remaining.begin(), remaining.end(), [&](size_t a, size_t b) {
    const int64_t da = distance_squared(cells.front().point, cells[a].point);
    const int64_t db = distance_squared(cells.front().point, cells[b].point);
    if (da != db) return da < db;
    return a < b;
  });
  const int coarse_columns =
      std::max(1, (context.base.columns + kCoarseFactor - 1) / kCoarseFactor);
  int track_counter = 0;
  int via_counter = 0;
  for (size_t terminal : remaining) {
    std::unordered_set<uint64_t> targets(cells[terminal].states.begin(),
                                         cells[terminal].states.end());
    double sum_x = 0.0;
    double sum_y = 0.0;
    for (uint64_t state : tree) {
      int x = 0;
      int y = 0;
      int layer = 0;
      grid.decode(state, x, y, layer);
      const Vec2 point = grid.center(x, y);
      sum_x += static_cast<double>(point.x);
      sum_y += static_cast<double>(point.y);
    }
    const Vec2 tree_center{
        static_cast<int64_t>(sum_x / static_cast<double>(tree.size())),
        static_cast<int64_t>(sum_y / static_cast<double>(tree.size()))};
    const auto corridor = build_corridor(context, net_index, tree_center,
                                         cells[terminal].point);
    const AStarResult result =
        astar(grid, net_index, tree, targets, corridor, coarse_columns,
              input.rules, context.budget, context.expansions, context.deadline,
              context.timed_out);
    if (result.status == AStarResult::Status::Budget) {
      context.budget_hit = true;
      attempt.reason = "BUDGET";
      return attempt;
    }
    if (result.status == AStarResult::Status::NoPath) {
      attempt.reason = "NO_PATH_PROVEN";
      return attempt;
    }
    geometry_from_path(input, grid, net_index, result.path, track_counter,
                       via_counter, attempt.tracks, attempt.vias);
    tree.insert(tree.end(), result.path.begin(), result.path.end());
  }
  Grid backup = grid;
  const int64_t copper_expand =
      input.rules.clearance_nm + input.rules.track_width_nm / 2;
  for (const RoutedTrack& track : attempt.tracks) {
    const auto layer = context.grid_layer_of_stackup.find(track.stackup_layer);
    if (layer == context.grid_layer_of_stackup.end()) continue;
    for (size_t i = 0; i + 1 < track.points.size(); ++i) {
      if (!stamp_shape(grid,
                       make_slot(track.points[i], track.points[i + 1],
                                 std::max<int64_t>(1, track.width_nm / 2)),
                       layer->second, net_index, copper_expand)) {
        grid = backup;
        attempt.reason = "CLEARANCE_CONFLICT";
        return attempt;
      }
    }
  }
  const int64_t via_expand =
      input.rules.clearance_nm + input.rules.via_pad_nm / 2;
  for (const RoutedVia& via : attempt.vias) {
    const Shape pad =
        make_circle(via.center, std::max<int64_t>(1, via.pad_outer_nm / 2));
    if (!stamp_all(grid, pad, net_index, via_expand)) {
      grid = backup;
      attempt.reason = "CLEARANCE_CONFLICT";
      return attempt;
    }
  }
  attempt.ok = true;
  return attempt;
}

std::vector<RoutedTrack> existing_tracks(const RoutingInput& input) {
  std::vector<RoutedTrack> out;
  for (const RoutingTrack& track : input.tracks) {
    RoutedTrack routed;
    routed.id = track.id;
    routed.net = track.net;
    routed.stackup_layer = track.stackup_layer;
    routed.layer_id = layer_id_for(input, 0);
    for (size_t i = 0; i < input.copper_layers.size(); ++i) {
      if (input.copper_layers[i] == track.stackup_layer) {
        routed.layer_id = layer_id_for(input, static_cast<int>(i));
        break;
      }
    }
    routed.width_nm = track.width_nm;
    routed.points = track.points;
    routed.locked = track.locked;
    out.push_back(std::move(routed));
  }
  return out;
}

std::vector<RoutedVia> existing_vias(const RoutingInput& input) {
  std::vector<RoutedVia> out;
  for (const RoutingVia& via : input.vias) {
    RoutedVia routed;
    routed.id = via.id;
    routed.net = via.net;
    routed.center = via.center;
    routed.drill_nm = via.drill_nm;
    routed.pad_outer_nm = via.pad_outer_nm;
    routed.start_layer = via.start_layer;
    routed.end_layer = via.end_layer;
    for (size_t i = 0; i < input.copper_layers.size(); ++i) {
      if (input.copper_layers[i] == via.start_layer) {
        routed.start_layer_id = layer_id_for(input, static_cast<int>(i));
      }
      if (input.copper_layers[i] == via.end_layer) {
        routed.end_layer_id = layer_id_for(input, static_cast<int>(i));
      }
    }
    routed.locked = via.locked;
    out.push_back(std::move(routed));
  }
  return out;
}

std::vector<CopperNode> build_nodes(const Context& context,
                                    const std::vector<RoutedTrack>& tracks,
                                    const std::vector<RoutedVia>& vias) {
  const RoutingInput& input = context.input;
  std::vector<CopperNode> nodes;
  for (const RoutingPad& pad : input.pads) {
    if (!pad.plated || pad.net.empty()) continue;
    CopperNode node;
    node.id = "pad:" + pad.id;
    node.net = pad.net;
    node.kind = "pad";
    node.component_id = pad.component_id;
    node.pin = pad.pin;
    node.plated = pad.plated;
    for (const auto& layer_shape : pad.layer_shapes) {
      const auto layer = context.grid_layer_of_stackup.find(layer_shape.first);
      if (layer == context.grid_layer_of_stackup.end()) continue;
      node.layer_shapes.emplace_back(layer->second, layer_shape.second);
    }
    if (!node.layer_shapes.empty()) nodes.push_back(std::move(node));
  }
  for (size_t i = 0; i < input.tracks.size(); ++i) {
    const RoutingTrack& track = input.tracks[i];
    const auto layer = context.grid_layer_of_stackup.find(track.stackup_layer);
    if (layer == context.grid_layer_of_stackup.end()) continue;
    CopperNode node;
    node.id = "existing:" + track.id;
    node.net = track.net;
    node.kind = "track";
    node.width_nm = track.width_nm;
    for (size_t j = 0; j + 1 < track.points.size(); ++j) {
      node.layer_shapes.emplace_back(
          layer->second,
          make_slot(track.points[j], track.points[j + 1],
                    std::max<int64_t>(1, track.width_nm / 2)));
    }
    nodes.push_back(std::move(node));
  }
  for (size_t i = 0; i < input.vias.size(); ++i) {
    const RoutingVia& via = input.vias[i];
    if (!via.plated) continue;
    const int start = context.grid_layer_of_stackup.count(via.start_layer)
                          ? context.grid_layer_of_stackup.at(via.start_layer)
                          : -1;
    const int end = context.grid_layer_of_stackup.count(via.end_layer)
                        ? context.grid_layer_of_stackup.at(via.end_layer)
                        : -1;
    if (start < 0 || end < 0) continue;
    CopperNode node;
    node.id = "existing-via:" + via.id;
    node.net = via.net;
    node.kind = "via";
    node.plated = true;
    node.drill_nm = via.drill_nm;
    node.pad_outer_nm = via.pad_outer_nm;
    node.via_center = via.center;
    node.via_start = std::min(start, end);
    node.via_end = std::max(start, end);
    for (int layer = 0; layer < context.base.layers; ++layer) {
      if (!spans(node.via_start, node.via_end, layer)) continue;
      node.layer_shapes.emplace_back(
          layer, make_circle(via.center,
                             std::max<int64_t>(1, via.pad_outer_nm / 2)));
    }
    nodes.push_back(std::move(node));
  }
  for (const RoutedTrack& track : tracks) {
    const auto layer = context.grid_layer_of_stackup.find(track.stackup_layer);
    if (layer == context.grid_layer_of_stackup.end()) continue;
    CopperNode node;
    node.id = track.id;
    node.net = track.net;
    node.kind = "track";
    node.width_nm = track.width_nm;
    for (size_t j = 0; j + 1 < track.points.size(); ++j) {
      node.layer_shapes.emplace_back(
          layer->second,
          make_slot(track.points[j], track.points[j + 1],
                    std::max<int64_t>(1, track.width_nm / 2)));
    }
    nodes.push_back(std::move(node));
  }
  for (const RoutedVia& via : vias) {
    const int start = context.grid_layer_of_stackup.count(via.start_layer)
                          ? context.grid_layer_of_stackup.at(via.start_layer)
                          : -1;
    const int end = context.grid_layer_of_stackup.count(via.end_layer)
                        ? context.grid_layer_of_stackup.at(via.end_layer)
                        : -1;
    if (start < 0 || end < 0) continue;
    CopperNode node;
    node.id = via.id;
    node.net = via.net;
    node.kind = "via";
    node.plated = true;
    node.drill_nm = via.drill_nm;
    node.pad_outer_nm = via.pad_outer_nm;
    node.via_center = via.center;
    node.via_start = std::min(start, end);
    node.via_end = std::max(start, end);
    for (int layer = 0; layer < context.base.layers; ++layer) {
      if (!spans(node.via_start, node.via_end, layer)) continue;
      node.layer_shapes.emplace_back(
          layer, make_circle(via.center,
                             std::max<int64_t>(1, via.pad_outer_nm / 2)));
    }
    nodes.push_back(std::move(node));
  }
  return nodes;
}

Evaluation evaluate(const Context& context,
                    const std::vector<RoutedTrack>& tracks,
                    const std::vector<RoutedVia>& vias,
                    const std::map<std::string, std::string>& failures) {
  Evaluation evaluation;
  ConnectivityInput connectivity;
  connectivity.nodes = build_nodes(context, tracks, vias);
  const ConnectivityReport report = analyze_connectivity(connectivity);
  evaluation.u_total = report.u_total;
  evaluation.short_count = report.short_count;
  std::map<std::string, const NetConnectivityReport*> by_net;
  for (const NetConnectivityReport& net : report.nets) {
    by_net[net.net] = &net;
  }
  for (const RoutingNet& net : context.input.nets) {
    NetRouteReport item;
    item.net = net.net;
    item.terminals = static_cast<int>(net.terminals.size());
    const auto found = by_net.find(net.net);
    if (found != by_net.end()) {
      item.u = found->second->u;
      item.unconnected_endpoints = found->second->unconnected_endpoints;
    } else {
      item.u = net.terminals.size() > 1
                   ? static_cast<int>(net.terminals.size()) - 1
                   : 0;
      item.unconnected_endpoints = net.terminals;
    }
    const auto failure = failures.find(net.net);
    if (failure != failures.end()) item.reason = failure->second;
    if (item.u > 0 && item.reason.empty()) item.reason = "CONNECTIVITY";
    item.routed = item.u == 0 && !net.terminals.empty();
    if (!item.routed) evaluation.unrouted.push_back(net.net);
    evaluation.reports.push_back(std::move(item));
  }
  std::sort(evaluation.unrouted.begin(), evaluation.unrouted.end());
  for (const RoutedTrack& track : tracks) {
    for (size_t i = 0; i + 1 < track.points.size(); ++i) {
      const Vec2 delta{track.points[i + 1].x - track.points[i].x,
                       track.points[i + 1].y - track.points[i].y};
      evaluation.wirelength_nm +=
          std::sqrt(static_cast<double>(delta.x) * delta.x +
                    static_cast<double>(delta.y) * delta.y);
    }
  }
  return evaluation;
}

bool better(const Candidate& candidate, const Candidate& current) {
  if (candidate.evaluation.u_total != current.evaluation.u_total) {
    return candidate.evaluation.u_total < current.evaluation.u_total;
  }
  if (candidate.evaluation.unrouted.size() !=
      current.evaluation.unrouted.size()) {
    return candidate.evaluation.unrouted.size() <
           current.evaluation.unrouted.size();
  }
  if (std::abs(candidate.evaluation.wirelength_nm -
               current.evaluation.wirelength_nm) > 1.0) {
    return candidate.evaluation.wirelength_nm <
           current.evaluation.wirelength_nm;
  }
  return candidate.vias.size() < current.vias.size();
}

bool proven_failure(const std::string& reason) {
  return reason == "NO_PATH_PROVEN" || reason == "TERMINAL_BLOCKED" ||
         reason == "TERMINAL_NOT_FOUND" || reason == "TERMINAL_NOT_PLATED" ||
         reason == "TERMINAL_NET_MISMATCH" || reason == "NO_TERMINALS";
}

std::string semantic_hash(const RoutingResult& result) {
  std::vector<RoutedTrack> tracks = result.tracks;
  std::sort(tracks.begin(), tracks.end(),
            [](const RoutedTrack& a, const RoutedTrack& b) {
              return a.id < b.id;
            });
  std::vector<RoutedVia> vias = result.vias;
  std::sort(vias.begin(), vias.end(),
            [](const RoutedVia& a, const RoutedVia& b) {
              return a.id < b.id;
            });
  std::string canonical = result.status + "|" + std::to_string(result.seed) +
                          "|" + std::to_string(result.u_total) + "|";
  for (const RoutedTrack& track : tracks) {
    canonical += track.id + "," + track.net + "," +
                 std::to_string(track.stackup_layer) + "," +
                 std::to_string(track.width_nm) + ":";
    for (const Vec2& point : track.points) {
      canonical += std::to_string(point.x) + "/" + std::to_string(point.y) + ";";
    }
    canonical += "|";
  }
  for (const RoutedVia& via : vias) {
    canonical += via.id + "," + via.net + "," + std::to_string(via.center.x) +
                 "," + std::to_string(via.center.y) + "," +
                 std::to_string(via.start_layer) + "," +
                 std::to_string(via.end_layer) + "|";
  }
  return sha256_hex(canonical);
}

}  // namespace

RoutingResult route_board(const RoutingInput& input) {
  RoutingResult result;
  result.seed = input.rules.seed;
  if (input.nets.empty()) {
    result.status = "SUCCEEDED";
    result.diagnostics.push_back("NO_NETS");
    result.semantic_hash = semantic_hash(result);
    return result;
  }
  Context context(input);
  for (size_t i = 0; i < input.nets.size(); ++i) {
    context.net_index[input.nets[i].net] = static_cast<int>(i);
  }
  for (const RoutingPad& pad : input.pads) {
    context.pad_by_id[pad.id] = &pad;
  }
  int64_t cells = 0;
  std::string diagnostic;
  const auto grid = build_grid(input, context.net_index, cells, diagnostic,
                               context.grid_layer_of_stackup,
                               context.grid_layer_ids);
  if (!grid.has_value()) {
    result.status = "UNKNOWN";
    result.diagnostics.push_back(diagnostic);
    result.semantic_hash = semantic_hash(result);
    return result;
  }
  context.base = *grid;
  result.grid_columns = context.base.columns;
  result.grid_rows = context.base.rows;
  result.grid_layers = context.base.layers;
  context.budget = std::max<int64_t>(0, input.rules.max_expansions);
  context.deadline = Clock::now() + std::chrono::milliseconds(
                                        std::max<int64_t>(1, input.rules.max_millis));
  resolve_terminal_cells(context);

  const std::vector<RoutedTrack> fixed_tracks = existing_tracks(input);
  const std::vector<RoutedVia> fixed_vias = existing_vias(input);
  std::vector<int> order(input.nets.size());
  for (size_t i = 0; i < order.size(); ++i) order[i] = static_cast<int>(i);
  std::sort(order.begin(), order.end(), [&](int a, int b) {
    const size_t ta = input.nets[static_cast<size_t>(a)].terminals.size();
    const size_t tb = input.nets[static_cast<size_t>(b)].terminals.size();
    if (ta != tb) return ta > tb;
    return input.nets[static_cast<size_t>(a)].net <
           input.nets[static_cast<size_t>(b)].net;
  });

  Candidate best;
  bool have_best = false;
  int rounds = 0;
  for (int round = 0; round < std::max(1, input.rules.max_rounds); ++round) {
    Grid working = context.base;
    Candidate candidate;
    candidate.tracks = fixed_tracks;
    candidate.vias = fixed_vias;
    std::map<std::string, std::string> failures;
    bool stop = false;
    for (int net_index : order) {
      const RoutingNet& net = input.nets[static_cast<size_t>(net_index)];
      if (net.locked) continue;
      Attempt attempt = route_net(context, working, net_index);
      if (!attempt.ok) {
        failures[net.net] = attempt.reason;
        if (attempt.reason == "BUDGET") {
          stop = true;
          break;
        }
        continue;
      }
      candidate.tracks.insert(candidate.tracks.end(), attempt.tracks.begin(),
                              attempt.tracks.end());
      candidate.vias.insert(candidate.vias.end(), attempt.vias.begin(),
                            attempt.vias.end());
      if (context.budget <= 0 ||
          Clock::now() > context.deadline) {
        context.budget_hit = true;
        stop = true;
        break;
      }
    }
    candidate.failures = failures;
    candidate.evaluation = evaluate(context, candidate.tracks, candidate.vias,
                                    failures);
    ++rounds;
    if (!have_best || better(candidate, best)) {
      best = candidate;
      have_best = true;
    }
    if (best.evaluation.u_total == 0 && best.evaluation.short_count == 0) {
      break;
    }
    if (stop || context.budget_hit) break;
    if (failures.empty()) break;
    if (round + 1 >= std::max(1, input.rules.max_rounds)) break;
    std::vector<int> next;
    for (int net_index : order) {
      if (failures.count(input.nets[static_cast<size_t>(net_index)].net)) {
        next.push_back(net_index);
      }
    }
    for (int net_index : order) {
      if (!failures.count(input.nets[static_cast<size_t>(net_index)].net)) {
        next.push_back(net_index);
      }
    }
    order = next;
  }

  result.tracks = best.tracks;
  result.vias = best.vias;
  result.unrouted_nets = best.evaluation.unrouted;
  result.net_reports = best.evaluation.reports;
  result.u_total = best.evaluation.u_total;
  result.short_count = best.evaluation.short_count;
  result.wirelength_nm = best.evaluation.wirelength_nm;
  result.via_count = static_cast<int>(best.vias.size());
  result.expansions = context.expansions;
  result.rounds = rounds;
  if (context.budget_hit) {
    result.diagnostics.push_back("BUDGET_EXHAUSTED");
  }
  if (result.short_count > 0) {
    result.diagnostics.push_back("COPPER_SHORT");
  }
  for (const NetRouteReport& report : best.evaluation.reports) {
    if (!report.routed) {
      result.diagnostics.push_back("UNROUTED:" + report.net + ":" +
                                   (report.reason.empty() ? "OPEN"
                                                          : report.reason));
    }
  }
  const int completed =
      static_cast<int>(input.nets.size() - best.evaluation.unrouted.size());
  if (result.u_total == 0 && result.short_count == 0) {
    result.status = "SUCCEEDED";
  } else if (completed > 0) {
    result.status = "PARTIAL";
  } else if (context.budget_hit) {
    result.status = "UNKNOWN";
  } else {
    bool all_proven = !best.failures.empty();
    for (const auto& failure : best.failures) {
      if (!proven_failure(failure.second)) {
        all_proven = false;
        break;
      }
    }
    result.status = all_proven ? "INFEASIBLE" : "UNKNOWN";
  }
  result.semantic_hash = semantic_hash(result);
  return result;
}

}  // namespace mota
