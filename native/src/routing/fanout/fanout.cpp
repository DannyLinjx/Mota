#include "mota/fanout.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <functional>
#include <limits>
#include <map>
#include <set>
#include <string>
#include <unordered_map>
#include <vector>

#include "mota/connectivity.hpp"
#include "mota/sha256.hpp"

namespace mota {
namespace {

struct Candidate {
  size_t ball = 0;
  Vec2 site{};
  std::string kind;  // dogbone | via_in_pad
  int target_layer = 0;
  std::vector<Vec2> points;  // dogbone track, empty for via-in-pad
  double cost = 0.0;
  int direction = 0;
};

struct Escape {
  Candidate candidate;
};

struct Context {
  explicit Context(const FanoutInput& fanout_input) : input(fanout_input) {}
  const FanoutInput& input;
  Aabb board{};
  int channel_capacity = 0;
  bool via_process_ok = false;
  bool microvia_process_ok = false;
  std::vector<std::vector<Candidate>> candidates;
  std::vector<bool> placed;
  std::vector<Escape> escapes;
  std::vector<FanoutFailure> failures;
  std::set<std::pair<int64_t, int64_t>> used_sites;
  int64_t nodes = 0;
  bool budget_hit = false;
};

Aabb board_bounds(const FanoutInput& input) {
  if (input.board_outline.size() >= 3) {
    Aabb bounds{input.board_outline[0].x, input.board_outline[0].y,
                input.board_outline[0].x, input.board_outline[0].y};
    for (const Vec2& point : input.board_outline) {
      bounds = bounds.merged(Aabb{point.x, point.y, point.x, point.y});
    }
    return bounds;
  }
  Aabb bounds{input.balls[0].center.x, input.balls[0].center.y,
              input.balls[0].center.x, input.balls[0].center.y};
  for (const FanoutBall& ball : input.balls) {
    bounds = bounds.merged(Aabb{ball.center.x, ball.center.y,
                                ball.center.x, ball.center.y});
  }
  return bounds.expanded(input.rules.board_margin_nm);
}

bool inside_board(const Context& context, const Vec2& point,
                  int64_t radius) {
  if (context.input.board_outline.size() >= 3) {
    if (!polygon_contains(context.input.board_outline, point)) return false;
  }
  const int64_t margin =
      context.input.rules.edge_clearance_nm + std::max<int64_t>(0, radius);
  return point.x >= context.board.min_x + margin &&
         point.x <= context.board.max_x - margin &&
         point.y >= context.board.min_y + margin &&
         point.y <= context.board.max_y - margin;
}

Shape ball_pad(const FanoutBall& ball) {
  return make_circle(ball.center, std::max<int64_t>(1, ball.pad_diameter_nm / 2));
}

bool via_process_ok(int64_t drill, int64_t pad, int64_t min_ring) {
  if (drill <= 0 || pad <= drill) return false;
  return (pad - drill) / 2 >= min_ring;
}

int channel_capacity(const FanoutRules& rules) {
  const int64_t gap = rules.pitch_nm - rules.pad_diameter_nm;
  if (gap <= rules.clearance_nm) return 0;
  const int64_t divisor = rules.trace_width_nm + rules.clearance_nm;
  if (divisor <= 0) return 0;
  return static_cast<int>((gap - rules.clearance_nm) / divisor);
}

int64_t distance_squared(const Vec2& a, const Vec2& b) {
  const __int128 dx = static_cast<__int128>(a.x) - b.x;
  const __int128 dy = static_cast<__int128>(a.y) - b.y;
  const __int128 value = dx * dx + dy * dy;
  return value > std::numeric_limits<int64_t>::max()
             ? std::numeric_limits<int64_t>::max()
             : static_cast<int64_t>(value);
}

bool via_clear_of_balls(const Context& context, const Vec2& site,
                        int64_t via_radius, size_t owner,
                        bool include_owner) {
  for (size_t i = 0; i < context.input.balls.size(); ++i) {
    if (i == owner && !include_owner) continue;
    const FanoutBall& other = context.input.balls[i];
    const int64_t required = via_radius + other.pad_diameter_nm / 2 +
                             context.input.rules.clearance_nm;
    if (distance_squared(site, other.center) <
        static_cast<int64_t>(required) * required) {
      return false;
    }
  }
  return true;
}

bool track_clear_of_balls(const Context& context, const Candidate& candidate,
                          size_t owner) {
  if (candidate.points.size() < 2) return true;
  const Shape track = make_slot(candidate.points.front(),
                                candidate.points.back(),
                                std::max<int64_t>(1, context.input.rules.trace_width_nm / 2));
  for (size_t i = 0; i < context.input.balls.size(); ++i) {
    if (i == owner) continue;
    if (shape_distance(track, ball_pad(context.input.balls[i])).distance_nm <
        context.input.rules.clearance_nm) {
      return false;
    }
  }
  return true;
}

bool via_conflicts_placed(const Context& context, const Candidate& candidate,
                          int64_t via_radius) {
  for (const Escape& escape : context.escapes) {
    const Candidate& other = escape.candidate;
    const int64_t other_radius =
        std::max<int64_t>(1, (other.kind == "via_in_pad"
                                  ? context.input.rules.microvia_pad_nm
                                  : context.input.rules.via_pad_nm) / 2);
    const int64_t required =
        via_radius + other_radius + context.input.rules.clearance_nm;
    if (distance_squared(candidate.site, other.site) <
        static_cast<int64_t>(required) * required) {
      return true;
    }
    if (other.points.size() >= 2) {
      const Shape track = make_slot(
          other.points.front(), other.points.back(),
          std::max<int64_t>(1, context.input.rules.trace_width_nm / 2));
      if (shape_distance(make_circle(candidate.site, via_radius), track)
              .distance_nm < context.input.rules.clearance_nm) {
        return true;
      }
    }
  }
  return false;
}

bool track_conflicts_placed(const Context& context, const Candidate& candidate) {
  if (candidate.points.size() < 2) return false;
  const Shape track = make_slot(
      candidate.points.front(), candidate.points.back(),
      std::max<int64_t>(1, context.input.rules.trace_width_nm / 2));
  for (const Escape& escape : context.escapes) {
    const Candidate& other = escape.candidate;
    const int64_t other_radius =
        std::max<int64_t>(1, (other.kind == "via_in_pad"
                                  ? context.input.rules.microvia_pad_nm
                                  : context.input.rules.via_pad_nm) / 2);
    if (shape_distance(track, make_circle(other.site, other_radius))
            .distance_nm < context.input.rules.clearance_nm) {
      return true;
    }
    if (other.points.size() >= 2) {
      const Shape other_track = make_slot(
          other.points.front(), other.points.back(),
          std::max<int64_t>(1, context.input.rules.trace_width_nm / 2));
      if (shape_distance(track, other_track).distance_nm <
          context.input.rules.clearance_nm) {
        return true;
      }
    }
  }
  return false;
}

bool candidate_conflicts(const Context& context, const Candidate& candidate) {
  const int64_t via_radius =
      std::max<int64_t>(1, (candidate.kind == "via_in_pad"
                                ? context.input.rules.microvia_pad_nm
                                : context.input.rules.via_pad_nm) / 2);
  return via_conflicts_placed(context, candidate, via_radius) ||
         track_conflicts_placed(context, candidate);
}

bool try_commit(Context& context, const Candidate& candidate) {
  const int64_t radius =
      std::max<int64_t>(1, (candidate.kind == "via_in_pad"
                                ? context.input.rules.microvia_pad_nm
                                : context.input.rules.via_pad_nm) /
                               2);
  if (!inside_board(context, candidate.site, radius)) return false;
  if (context.used_sites.count({candidate.site.x, candidate.site.y})) return false;
  if (candidate_conflicts(context, candidate)) return false;
  context.escapes.push_back(Escape{candidate});
  context.used_sites.insert({candidate.site.x, candidate.site.y});
  context.placed[candidate.ball] = true;
  return true;
}

void undo(Context& context) {
  if (context.escapes.empty()) return;
  const Escape& escape = context.escapes.back();
  context.used_sites.erase({escape.candidate.site.x, escape.candidate.site.y});
  context.placed[escape.candidate.ball] = false;
  context.escapes.pop_back();
}

std::vector<Candidate> generate_candidates(Context& context, size_t index) {
  const FanoutInput& input = context.input;
  const FanoutBall& ball = input.balls[index];
  const FanoutRules& rules = input.rules;
  std::vector<Candidate> out;
  if (input.signal_layers.empty()) return out;
  const Vec2 center = ball.center;

  if (rules.allow_via_in_pad && context.microvia_process_ok &&
      rules.microvia_pad_nm <= ball.pad_diameter_nm) {
    const Vec2 site = center;
    if (inside_board(context, site, rules.microvia_pad_nm / 2) &&
        via_clear_of_balls(context, site, rules.microvia_pad_nm / 2, index,
                           false)) {
      Candidate candidate;
      candidate.ball = index;
      candidate.site = site;
      candidate.kind = "via_in_pad";
      candidate.target_layer = input.signal_layers.front();
      candidate.cost = 1.0;
      candidate.direction = 8;
      out.push_back(std::move(candidate));
    }
  }

  if (rules.allow_dogbone && rules.allow_through_via && context.via_process_ok) {
    std::vector<Vec2> sites;
    const int64_t half = rules.pitch_nm / 2;
    sites.push_back({center.x - half, center.y - half});
    sites.push_back({center.x + half, center.y - half});
    sites.push_back({center.x + half, center.y + half});
    sites.push_back({center.x - half, center.y + half});
    int min_row = std::numeric_limits<int>::max();
    int max_row = std::numeric_limits<int>::min();
    int min_col = std::numeric_limits<int>::max();
    int max_col = std::numeric_limits<int>::min();
    for (const FanoutBall& other : input.balls) {
      min_row = std::min(min_row, other.row);
      max_row = std::max(max_row, other.row);
      min_col = std::min(min_col, other.col);
      max_col = std::max(max_col, other.col);
    }
    const bool perimeter = ball.row == min_row || ball.row == max_row ||
                           ball.col == min_col || ball.col == max_col;
    if (perimeter) {
      const Vec2 bga_center{
          (context.board.min_x + context.board.max_x) / 2,
          (context.board.min_y + context.board.max_y) / 2};
      const int sign_x = center.x >= bga_center.x ? 1 : -1;
      const int sign_y = center.y >= bga_center.y ? 1 : -1;
      const int64_t outward = half + rules.via_pad_nm / 2 + rules.clearance_nm;
      sites.push_back({center.x + sign_x * outward, center.y});
      sites.push_back({center.x, center.y + sign_y * outward});
    }
    std::set<std::pair<int64_t, int64_t>> seen;
    for (const Vec2& site : sites) {
      if (!seen.insert({site.x, site.y}).second) continue;
      if (!inside_board(context, site, rules.via_pad_nm / 2)) continue;
      if (!via_clear_of_balls(context, site, rules.via_pad_nm / 2, index,
                              true)) {
        continue;
      }
      Candidate base;
      base.ball = index;
      base.site = site;
      base.kind = "dogbone";
      base.points = {center, site};
      if (!track_clear_of_balls(context, base, index)) continue;
      const int64_t dx = site.x - center.x;
      const int64_t dy = site.y - center.y;
      base.direction = dx < 0 && dy < 0 ? 0 : dx > 0 && dy < 0 ? 1
                       : dx > 0 && dy > 0 ? 2 : dx < 0 && dy > 0 ? 3
                       : dx != 0 ? 4 : 5;
      base.cost = std::sqrt(static_cast<double>(dx) * dx +
                            static_cast<double>(dy) * dy) /
                      1e6 +
                  (base.direction >= 4 ? 0.2 : 0.0);
      for (size_t layer_index = 0; layer_index < input.signal_layers.size();
           ++layer_index) {
        Candidate candidate = base;
        candidate.target_layer = input.signal_layers[layer_index];
        candidate.cost += layer_index * 0.01;
        out.push_back(std::move(candidate));
      }
    }
  }
  std::sort(out.begin(), out.end(), [](const Candidate& a, const Candidate& b) {
    if (std::abs(a.cost - b.cost) > 1e-9) return a.cost < b.cost;
    if (a.site.x != b.site.x) return a.site.x < b.site.x;
    if (a.site.y != b.site.y) return a.site.y < b.site.y;
    return a.kind < b.kind;
  });
  if (out.size() > 16) out.resize(16);
  return out;
}

bool group_relation(const FanoutGroup& group, const Candidate& previous,
                    const Candidate& current, const FanoutBall& previous_ball,
                    const FanoutBall& current_ball, int64_t pitch) {
  if (!group.adjacent) return true;
  if (group.kind == "differential") {
    const int64_t dx = std::llabs(current.site.x - previous.site.x);
    const int64_t dy = std::llabs(current.site.y - previous.site.y);
    if (std::max(dx, dy) > pitch * 3 / 2) return false;
    return (previous.direction >= 8) == (current.direction >= 8);
  }
  if (previous.direction != current.direction) return false;
  const int64_t ball_dx = current_ball.center.x - previous_ball.center.x;
  const int64_t ball_dy = current_ball.center.y - previous_ball.center.y;
  if (std::llabs(ball_dx) >= std::llabs(ball_dy)) {
    return (current.site.x - previous.site.x) *
               (ball_dx >= 0 ? 1 : -1) >
           0;
  }
  return (current.site.y - previous.site.y) *
             (ball_dy >= 0 ? 1 : -1) >
         0;
}

bool assign_group(Context& context, const FanoutGroup& group) {
  const FanoutInput& input = context.input;
  std::unordered_map<std::string, size_t> index;
  for (size_t i = 0; i < input.balls.size(); ++i) index[input.balls[i].id] = i;
  std::vector<size_t> balls;
  for (const std::string& id : group.balls) {
    const auto found = index.find(id);
    if (found == index.end()) return false;
    balls.push_back(found->second);
  }
  if (balls.size() < 2) return true;
  std::sort(balls.begin(), balls.end(), [&](size_t a, size_t b) {
    if (input.balls[a].group_order != input.balls[b].group_order) {
      return input.balls[a].group_order < input.balls[b].group_order;
    }
    if (input.balls[a].row != input.balls[b].row) {
      return input.balls[a].row < input.balls[b].row;
    }
    return input.balls[a].col < input.balls[b].col;
  });

  for (int layer : input.signal_layers) {
    const size_t before = context.escapes.size();
    std::function<bool(size_t, const Candidate&, const FanoutBall&)> dfs =
        [&](size_t depth, const Candidate& previous,
            const FanoutBall& previous_ball) -> bool {
      if (depth == balls.size()) return true;
      if (context.nodes >= input.rules.max_search_nodes) {
        context.budget_hit = true;
        return false;
      }
      const size_t ball_index = balls[depth];
      for (const Candidate& candidate : context.candidates[ball_index]) {
        if (candidate.target_layer != layer) continue;
        if (depth > 0 &&
            !group_relation(group, previous, candidate, previous_ball,
                            input.balls[ball_index], input.rules.pitch_nm)) {
          continue;
        }
        ++context.nodes;
        if (!try_commit(context, candidate)) continue;
        if (dfs(depth + 1, candidate, input.balls[ball_index])) return true;
        undo(context);
      }
      return false;
    };
    Candidate dummy;
    if (dfs(0, dummy, input.balls[balls.front()])) return true;
    while (context.escapes.size() > before) undo(context);
  }
  return false;
}

std::vector<FanoutFailure> group_failure(const Context& context,
                                         const FanoutGroup& group) {
  std::vector<FanoutFailure> failures;
  size_t candidate_count = 0;
  for (const FanoutBall& ball : context.input.balls) {
    for (const std::string& id : group.balls) {
      if (ball.id == id) {
        for (size_t i = 0; i < context.input.balls.size(); ++i) {
          if (context.input.balls[i].id == id) {
            candidate_count += context.candidates[i].size();
            break;
          }
        }
      }
    }
  }
  for (const std::string& id : group.balls) {
    FanoutFailure failure;
    failure.ball_id = id;
    failure.net = "";
    failure.reason = "GROUP_RESERVATION_FAILED";
    failure.factors = {"group=" + group.id,
                       "kind=" + group.kind,
                       std::string("same_layer=") +
                           (group.same_layer ? "1" : "0"),
                       std::string("adjacent=") +
                           (group.adjacent ? "1" : "0"),
                       "candidates=" + std::to_string(candidate_count),
                       "signal_layers=" +
                           std::to_string(context.input.signal_layers.size())};
    for (const FanoutBall& ball : context.input.balls) {
      if (ball.id == id) {
        failure.net = ball.net;
        failure.attempted_site = ball.center;
        break;
      }
    }
    failures.push_back(std::move(failure));
  }
  return failures;
}

std::string semantic_hash(const FanoutResult& result) {
  std::string canonical = result.status + "|" + std::to_string(result.seed) +
                          "|" + std::to_string(result.escaped_count) + "|";
  std::vector<FanoutVia> vias = result.vias;
  std::sort(vias.begin(), vias.end(),
            [](const FanoutVia& a, const FanoutVia& b) {
              return a.ball_id < b.ball_id;
            });
  for (const FanoutVia& via : vias) {
    canonical += "V:" + via.ball_id + "," + via.kind + "," +
                 std::to_string(via.site.x) + "," + std::to_string(via.site.y) +
                 "," + std::to_string(via.end_layer) + "|";
  }
  std::vector<FanoutFailure> failures = result.failures;
  std::sort(failures.begin(), failures.end(),
            [](const FanoutFailure& a, const FanoutFailure& b) {
              return a.ball_id < b.ball_id;
            });
  for (const FanoutFailure& failure : failures) {
    canonical += "F:" + failure.ball_id + "," + failure.reason + "|";
  }
  return sha256_hex(canonical);
}

}  // namespace

FanoutResult fanout_bga(const FanoutInput& input) {
  FanoutResult result;
  result.seed = input.rules.seed;
  result.channel_capacity = channel_capacity(input.rules);
  result.required_count = static_cast<int>(std::count_if(
      input.balls.begin(), input.balls.end(),
      [](const FanoutBall& ball) { return ball.required; }));
  Context context(input);
  context.board = board_bounds(input);
  context.channel_capacity = result.channel_capacity;
  context.via_process_ok = via_process_ok(
      input.rules.via_drill_nm, input.rules.via_pad_nm,
      input.rules.min_annular_ring_nm);
  context.microvia_process_ok = via_process_ok(
      input.rules.microvia_drill_nm, input.rules.microvia_pad_nm,
      input.rules.min_annular_ring_nm);
  context.placed.assign(input.balls.size(), false);
  context.candidates.resize(input.balls.size());
  for (size_t i = 0; i < input.balls.size(); ++i) {
    context.candidates[i] = generate_candidates(context, i);
  }

  std::set<size_t> grouped;
  for (const FanoutGroup& group : input.groups) {
    for (const std::string& id : group.balls) {
      for (size_t i = 0; i < input.balls.size(); ++i) {
        if (input.balls[i].id == id) grouped.insert(i);
      }
    }
    if (assign_group(context, group)) continue;
    for (const FanoutFailure& failure : group_failure(context, group)) {
      context.failures.push_back(failure);
    }
  }

  std::vector<size_t> remaining;
  for (size_t i = 0; i < input.balls.size(); ++i) {
    if (context.placed[i] || grouped.count(i)) continue;
    remaining.push_back(i);
  }
  std::sort(remaining.begin(), remaining.end(), [&](size_t a, size_t b) {
    if (context.candidates[a].size() != context.candidates[b].size()) {
      return context.candidates[a].size() < context.candidates[b].size();
    }
    if (input.balls[a].row != input.balls[b].row) {
      return input.balls[a].row < input.balls[b].row;
    }
    return input.balls[a].col < input.balls[b].col;
  });
  for (size_t index : remaining) {
    bool placed = false;
    for (const Candidate& candidate : context.candidates[index]) {
      if (try_commit(context, candidate)) {
        placed = true;
        break;
      }
    }
    if (placed) continue;
    if (!input.balls[index].required) continue;
    FanoutFailure failure;
    failure.ball_id = input.balls[index].id;
    failure.net = input.balls[index].net;
    failure.attempted_site = input.balls[index].center;
    if (!context.via_process_ok && !context.microvia_process_ok) {
      failure.reason = "VIA_PROCESS";
    } else if (context.candidates[index].empty()) {
      failure.reason = context.channel_capacity == 0 &&
                               !input.rules.allow_via_in_pad
                           ? "CHANNEL_CAPACITY"
                           : "NO_VIA_SITE";
    } else {
      failure.reason = "NO_VIA_SITE";
    }
    failure.factors = {
        "pitch=" + std::to_string(input.rules.pitch_nm),
        "pad=" + std::to_string(input.balls[index].pad_diameter_nm),
        "via_drill=" + std::to_string(input.rules.via_drill_nm),
        "via_pad=" + std::to_string(input.rules.via_pad_nm),
        "min_ring=" + std::to_string(input.rules.min_annular_ring_nm),
        "clearance=" + std::to_string(input.rules.clearance_nm),
        "via_in_pad=" + std::string(input.rules.allow_via_in_pad ? "1" : "0"),
        "channel_capacity=" + std::to_string(context.channel_capacity),
        "candidates=" + std::to_string(context.candidates[index].size()),
    };
    context.failures.push_back(std::move(failure));
  }

  for (const Escape& escape : context.escapes) {
    const FanoutBall& ball = input.balls[escape.candidate.ball];
    FanoutVia via;
    via.id = "fanout-via:" + ball.id;
    via.ball_id = ball.id;
    via.net = ball.net;
    via.site = escape.candidate.site;
    via.kind = escape.candidate.kind;
    if (via.kind == "via_in_pad") {
      via.drill_nm = input.rules.microvia_drill_nm;
      via.pad_outer_nm = input.rules.microvia_pad_nm;
    } else {
      via.drill_nm = input.rules.via_drill_nm;
      via.pad_outer_nm = input.rules.via_pad_nm;
    }
    via.start_layer = 0;
    via.end_layer = escape.candidate.target_layer;
    via.start_layer_id = input.signal_layer_ids.empty()
                             ? "F.Cu"
                             : "F.Cu";
    via.end_layer_id =
        input.signal_layer_ids.empty()
            ? std::to_string(via.end_layer)
            : input.signal_layer_ids[
                  static_cast<size_t>(std::min<size_t>(
                      input.signal_layer_ids.size() - 1,
                      std::distance(input.signal_layers.begin(),
                                    std::find(input.signal_layers.begin(),
                                              input.signal_layers.end(),
                                              via.end_layer))))];
    result.vias.push_back(std::move(via));
    if (!escape.candidate.points.empty()) {
      FanoutTrack track;
      track.id = "fanout-track:" + ball.id;
      track.ball_id = ball.id;
      track.net = ball.net;
      track.stackup_layer = 0;
      track.layer_id = "F.Cu";
      track.width_nm = input.rules.trace_width_nm;
      track.points = escape.candidate.points;
      result.tracks.push_back(std::move(track));
    }
    if (escape.candidate.kind == "via_in_pad") {
      ++result.via_in_pad_count;
    } else {
      ++result.dogbone_count;
    }
  }
  result.failures = context.failures;
  for (const FanoutFailure& failure : result.failures) {
    result.unescaped_balls.push_back(failure.ball_id);
  }
  std::sort(result.unescaped_balls.begin(), result.unescaped_balls.end());
  result.escaped_count = 0;
  for (const FanoutVia& via : result.vias) {
    for (const FanoutBall& ball : input.balls) {
      if (ball.id == via.ball_id && ball.required) {
        ++result.escaped_count;
        break;
      }
    }
  }
  result.search_nodes = context.nodes;
  result.budget_hit = context.budget_hit;

  // Independent connectivity: pads + tracks + vias, then per-ball touch checks.
  ConnectivityInput connectivity;
  for (const FanoutBall& ball : input.balls) {
    CopperNode node;
    node.id = "ball:" + ball.id;
    node.net = ball.net;
    node.kind = "pad";
    node.plated = true;
    node.layer_shapes.emplace_back(0, ball_pad(ball));
    connectivity.nodes.push_back(std::move(node));
  }
  for (const FanoutTrack& track : result.tracks) {
    CopperNode node;
    node.id = track.id;
    node.net = track.net;
    node.kind = "track";
    node.width_nm = track.width_nm;
    node.layer_shapes.emplace_back(
        0, make_slot(track.points.front(), track.points.back(),
                     std::max<int64_t>(1, track.width_nm / 2)));
    connectivity.nodes.push_back(std::move(node));
  }
  for (const FanoutVia& via : result.vias) {
    CopperNode node;
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
        0, make_circle(via.site, std::max<int64_t>(1, via.pad_outer_nm / 2)));
    if (via.end_layer != 0) {
      node.layer_shapes.emplace_back(
          via.end_layer,
          make_circle(via.site, std::max<int64_t>(1, via.pad_outer_nm / 2)));
    }
    connectivity.nodes.push_back(std::move(node));
  }
  const ConnectivityReport report = analyze_connectivity(connectivity);
  if (report.short_count > 0) {
    result.status = "PARTIAL";
  }
  bool connectivity_failed = false;
  for (const FanoutVia& via : result.vias) {
    const FanoutBall* ball = nullptr;
    for (const FanoutBall& candidate : input.balls) {
      if (candidate.id == via.ball_id) {
        ball = &candidate;
        break;
      }
    }
    if (ball == nullptr) continue;
    bool connected = false;
    if (via.kind == "via_in_pad") {
      connected = shape_distance(ball_pad(*ball),
                                 make_circle(via.site, via.pad_outer_nm / 2))
                      .distance_nm <= 0;
    } else {
      for (const FanoutTrack& track : result.tracks) {
        if (track.ball_id != via.ball_id) continue;
        const Shape slot = make_slot(
            track.points.front(), track.points.back(),
            std::max<int64_t>(1, track.width_nm / 2));
        connected =
            shape_distance(ball_pad(*ball), slot).distance_nm <= 0 &&
            shape_distance(slot,
                           make_circle(via.site, via.pad_outer_nm / 2))
                    .distance_nm <= 0;
        break;
      }
    }
    if (!connected) connectivity_failed = true;
  }
  if (report.short_count > 0) connectivity_failed = true;

  result.required_count = static_cast<int>(std::count_if(
      input.balls.begin(), input.balls.end(),
      [](const FanoutBall& ball) { return ball.required; }));
  if (result.escaped_count == result.required_count && !connectivity_failed &&
      report.short_count == 0) {
    result.status = "SUCCEEDED";
  } else if (result.escaped_count > 0) {
    result.status = "PARTIAL";
  } else if (context.budget_hit) {
    result.status = "UNKNOWN";
  } else {
    bool all_proven = !result.failures.empty();
    for (const FanoutFailure& failure : result.failures) {
      if (failure.reason != "NO_VIA_SITE" &&
          failure.reason != "CHANNEL_CAPACITY" &&
          failure.reason != "VIA_PROCESS" &&
          failure.reason != "GROUP_RESERVATION_FAILED" &&
          failure.reason != "OUTSIDE_BOARD") {
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
