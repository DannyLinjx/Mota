// End-to-end channel solving on real copper.
//
// Every terminal must sit on copper of its own net. Lengths are path lengths
// through track segments, via barrels (zero planar length) and series
// component links, so a channel cannot be "measured" from pad names.
#include <algorithm>
#include <cstdint>
#include <functional>
#include <limits>
#include <map>
#include <numeric>
#include <queue>
#include <set>
#include <string>
#include <tuple>
#include <unordered_map>
#include <utility>
#include <vector>

#include "internal.hpp"

namespace mota {
namespace highspeed_detail {
namespace {

struct VertexKey {
  std::string net;
  int layer = -1;
  int64_t x = 0;
  int64_t y = 0;

  bool operator<(const VertexKey& other) const {
    if (net != other.net) return net < other.net;
    if (layer != other.layer) return layer < other.layer;
    if (x != other.x) return x < other.x;
    return y < other.y;
  }
};

class DisjointSet {
 public:
  explicit DisjointSet(size_t size) : parent_(size), rank_(size, 0) {
    std::iota(parent_.begin(), parent_.end(), 0);
  }
  int find(int value) {
    while (parent_[value] != value) {
      parent_[value] = parent_[parent_[value]];
      value = parent_[value];
    }
    return value;
  }
  void join(int a, int b) {
    a = find(a);
    b = find(b);
    if (a == b) return;
    if (rank_[a] < rank_[b]) std::swap(a, b);
    parent_[b] = a;
    if (rank_[a] == rank_[b]) ++rank_[a];
  }

 private:
  std::vector<int> parent_;
  std::vector<int> rank_;
};

struct Edge {
  int to = -1;
  int64_t weight = 0;
};

struct Graph {
  std::map<VertexKey, int> copper;
  std::unordered_map<std::string, int> terminals;  // node id -> terminal vertex
  std::unordered_map<std::string, int> terminal_copper;  // node id -> copper
  std::vector<std::vector<Edge>> edges;
  DisjointSet copper_sets{0};

  int vertex(const VertexKey& key) {
    const auto found = copper.find(key);
    if (found != copper.end()) return found->second;
    const int index = static_cast<int>(edges.size());
    copper.emplace(key, index);
    edges.emplace_back();
    return index;
  }

  void connect(int a, int b, int64_t weight) {
    if (a < 0 || b < 0 || a == b) return;
    edges[a].push_back(Edge{b, weight});
    edges[b].push_back(Edge{a, weight});
  }
};

bool terminal_vertex(const Graph& graph, const ChannelNode& node,
                     int64_t snap_nm, int& index) {
  if (node.stackup_layer >= 0) {
    const VertexKey key{node.net, node.stackup_layer, node.position.x,
                        node.position.y};
    const auto exact = graph.copper.find(key);
    if (exact != graph.copper.end()) {
      index = exact->second;
      return true;
    }
  }
  int64_t best = std::numeric_limits<int64_t>::max();
  int best_index = -1;
  for (const auto& entry : graph.copper) {
    if (entry.first.net != node.net) continue;
    if (node.stackup_layer >= 0 && entry.first.layer != node.stackup_layer) {
      continue;
    }
    if (!same_point(Vec2{entry.first.x, entry.first.y}, node.position,
                    snap_nm)) {
      continue;
    }
    const int64_t dx = entry.first.x - node.position.x;
    const int64_t dy = entry.first.y - node.position.y;
    const int64_t distance = (dx < 0 ? -dx : dx) + (dy < 0 ? -dy : dy);
    if (distance < best) {
      best = distance;
      best_index = entry.second;
    }
  }
  if (best_index < 0) return false;
  index = best_index;
  return true;
}

std::string pin_ref(const ChannelNode& node) {
  if (node.component_id.empty()) return "node:" + node.id;
  return node.component_id + ":" + node.pin;
}

}  // namespace

ChannelSolve solve_channel(const HighspeedInput& input, const Channel& channel) {
  ChannelSolve solve;

  std::unordered_map<std::string, const ChannelNode*> nodes;
  for (const ChannelNode& node : channel.nodes) {
    if (node.id.empty()) {
      solve.topology_status = "FAIL";
      solve.topology_reason = "NODE_WITHOUT_ID";
      solve.topology_message = "A channel node has an empty id.";
      return solve;
    }
    if (nodes.count(node.id) != 0) {
      solve.topology_status = "FAIL";
      solve.topology_reason = "DUPLICATE_NODE";
      solve.topology_message = "Channel " + channel.id + " declares node " +
                               node.id + " twice.";
      solve.topology_refs.push_back("node:" + node.id);
      return solve;
    }
    nodes.emplace(node.id, &node);
  }

  if (channel.driver.empty() || nodes.count(channel.driver) == 0) {
    solve.topology_status = "FAIL";
    solve.topology_reason = "MISSING_DRIVER";
    solve.topology_message =
        "Channel " + channel.id + " has no declared driver node.";
    return solve;
  }
  if (channel.receivers.empty()) {
    solve.topology_status = "FAIL";
    solve.topology_reason = "MISSING_RECEIVER";
    solve.topology_message =
        "Channel " + channel.id + " has no receiver; an end-to-end channel "
        "needs at least one sink.";
    return solve;
  }
  std::vector<std::string> unknown;
  for (const std::string& id : channel.receivers) {
    if (nodes.count(id) == 0) unknown.push_back(id);
  }
  for (const std::string& id : channel.series) {
    if (nodes.count(id) == 0) unknown.push_back(id);
  }
  for (const std::string& id : channel.branches) {
    if (nodes.count(id) == 0) unknown.push_back(id);
  }
  if (!unknown.empty()) {
    solve.topology_status = "FAIL";
    solve.topology_reason = "UNKNOWN_NODE";
    solve.topology_message = "Channel " + channel.id + " references nodes " +
                             "that are not declared: " + unknown.front() + ".";
    for (const std::string& id : unknown) {
      solve.topology_refs.push_back("node:" + id);
    }
    return solve;
  }

  // Build copper vertices and edges for every net the channel touches.
  std::set<std::string> nets;
  for (const ChannelNode& node : channel.nodes) nets.insert(node.net);
  Graph graph;
  for (const HighspeedTrack& track : input.tracks) {
    if (nets.count(track.net) == 0) continue;
    for (size_t i = 1; i < track.points.size(); ++i) {
      const Vec2& a = track.points[i - 1];
      const Vec2& b = track.points[i];
      const int first = graph.vertex(
          VertexKey{track.net, track.stackup_layer, a.x, a.y});
      const int second = graph.vertex(
          VertexKey{track.net, track.stackup_layer, b.x, b.y});
      graph.connect(first, second, segment_length(a, b));
    }
  }
  graph.copper_sets = DisjointSet(graph.edges.size());
  for (const HighspeedTrack& track : input.tracks) {
    if (nets.count(track.net) == 0) continue;
    for (size_t i = 1; i < track.points.size(); ++i) {
      const Vec2& a = track.points[i - 1];
      const Vec2& b = track.points[i];
      const auto first = graph.copper.find(
          VertexKey{track.net, track.stackup_layer, a.x, a.y});
      const auto second = graph.copper.find(
          VertexKey{track.net, track.stackup_layer, b.x, b.y});
      if (first != graph.copper.end() && second != graph.copper.end()) {
        graph.copper_sets.join(first->second, second->second);
      }
    }
  }
  for (const HighspeedVia& via : input.vias) {
    if (nets.count(via.net) == 0) continue;
    if (via.start_layer < 0 || via.end_layer < 0) continue;
    const int start =
        graph.vertex(VertexKey{via.net, via.start_layer, via.center.x,
                               via.center.y});
    const auto end_key =
        VertexKey{via.net, via.end_layer, via.center.x, via.center.y};
    const auto end = graph.copper.find(end_key);
    if (end == graph.copper.end()) {
      solve.open_links.push_back("via:" + via.id);
      continue;
    }
    graph.connect(start, end->second, 0);
    graph.copper_sets.join(start, end->second);
  }

  // Resolve terminals.
  std::vector<std::string> unresolved;
  for (const ChannelNode& node : channel.nodes) {
    int index = -1;
    if (!terminal_vertex(graph, node, input.rules.terminal_snap_nm, index)) {
      unresolved.push_back(node.id);
      continue;
    }
    const int terminal = static_cast<int>(graph.edges.size());
    graph.edges.emplace_back();
    graph.connect(terminal, index, 0);
    graph.terminals.emplace(node.id, terminal);
    graph.terminal_copper.emplace(node.id, index);
  }

  // Declared links: copper links must already be connected by copper, and
  // component links are the series elements that join two nets.
  std::vector<std::string> broken_links;
  std::vector<ChannelLink> component_links;
  for (const ChannelLink& link : channel.links) {
    if (nodes.count(link.from) == 0 || nodes.count(link.to) == 0) {
      broken_links.push_back(link.id + " (unknown node)");
      continue;
    }
    if (link.kind == "component") {
      component_links.push_back(link);
      continue;
    }
    const auto from = graph.terminal_copper.find(link.from);
    const auto to = graph.terminal_copper.find(link.to);
    if (from == graph.terminal_copper.end() ||
        to == graph.terminal_copper.end()) {
      broken_links.push_back(link.id + " (terminal off copper)");
      continue;
    }
    if (graph.copper_sets.find(from->second) !=
        graph.copper_sets.find(to->second)) {
      broken_links.push_back(link.id);
    }
  }

  for (const ChannelLink& link : component_links) {
    const auto from = graph.terminals.find(link.from);
    const auto to = graph.terminals.find(link.to);
    if (from == graph.terminals.end() || to == graph.terminals.end()) {
      broken_links.push_back(link.id + " (series pin off copper)");
      continue;
    }
    graph.connect(from->second, to->second,
                  std::max<int64_t>(0, link.internal_length_nm));
  }

  // Without declared links the topology is "driver must reach every sink".
  if (channel.links.empty()) {
    const auto driver = graph.terminal_copper.find(channel.driver);
    if (driver != graph.terminal_copper.end()) {
      for (const std::string& id : channel.receivers) {
        const auto sink = graph.terminal_copper.find(id);
        if (sink == graph.terminal_copper.end()) {
          broken_links.push_back("node:" + id);
          continue;
        }
        if (graph.copper_sets.find(driver->second) !=
            graph.copper_sets.find(sink->second)) {
          broken_links.push_back("node:" + id);
        }
      }
    }
  }

  std::vector<std::string> kind_mismatch;
  for (const std::string& id : channel.series) {
    const auto found = nodes.find(id);
    if (found != nodes.end() && found->second->kind != "series") {
      kind_mismatch.push_back(id);
    }
  }
  if (!kind_mismatch.empty()) {
    solve.topology_status = "FAIL";
    solve.topology_reason = "SERIES_KIND_MISMATCH";
    solve.topology_message = "Node " + kind_mismatch.front() +
                             " is declared as a series element but its kind is "
                             "not 'series'.";
    for (const std::string& id : kind_mismatch) {
      solve.topology_refs.push_back("node:" + id);
    }
    return solve;
  }
  if (!channel.series.empty() && component_links.empty()) {
    solve.topology_status = "FAIL";
    solve.topology_reason = "MISSING_COMPONENT_LINK";
    solve.topology_message =
        "Channel " + channel.id + " declares series pins but no component "
        "link joins them; the two nets cannot be one channel.";
    solve.topology_refs.push_back("channel:" + channel.id);
    return solve;
  }

  if (!unresolved.empty()) {
    solve.topology_status = "FAIL";
    solve.topology_reason = "TERMINAL_NOT_ON_COPPER";
    solve.topology_message =
        "Terminal " + unresolved.front() + " of channel " + channel.id +
        " is not on copper of its net; the channel cannot be measured.";
    for (const std::string& id : unresolved) {
      solve.topology_refs.push_back("node:" + id);
    }
    return solve;
  }
  if (!broken_links.empty()) {
    solve.topology_status = "FAIL";
    solve.topology_reason = "TOPOLOGY_OPEN";
    solve.topology_message = "Declared channel links are not backed by copper: " +
                             broken_links.front() + ".";
    solve.topology_refs = broken_links;
    return solve;
  }
  solve.topology_status = "PASS";
  solve.topology_message = "driver -> " +
                           std::to_string(channel.series.size()) +
                           " series pin(s) -> " +
                           std::to_string(channel.receivers.size()) +
                           " receiver(s) is copper-connected.";

  const auto driver = graph.terminals.find(channel.driver);
  if (driver == graph.terminals.end()) return solve;

  // Dijkstra from the driver over copper, vias and series elements.
  const int64_t infinite = std::numeric_limits<int64_t>::max();
  std::vector<int64_t> distance(graph.edges.size(), infinite);
  using Entry = std::pair<int64_t, int>;
  std::priority_queue<Entry, std::vector<Entry>, std::greater<Entry>> queue;
  distance[driver->second] = 0;
  queue.push({0, driver->second});
  int64_t expansions = 0;
  while (!queue.empty()) {
    const auto [current, node_index] = queue.top();
    queue.pop();
    if (current != distance[node_index]) continue;
    ++expansions;
    if (expansions > input.rules.max_search_nodes) {
      solve.budget_hit = true;
      break;
    }
    for (const Edge& edge : graph.edges[node_index]) {
      if (edge.weight < 0) continue;
      const int64_t next = current + edge.weight;
      if (next < distance[edge.to]) {
        distance[edge.to] = next;
        queue.push({next, edge.to});
      }
    }
  }

  for (const std::string& id : channel.receivers) {
    ChannelPath path;
    path.receiver = id;
    const auto& receiver = *nodes.at(id);
    path.receiver_ref = pin_ref(receiver);
    const auto sink = graph.terminals.find(id);
    if (sink == graph.terminals.end() || distance[sink->second] == infinite) {
      path.measured = false;
      path.reason = "UNREACHABLE_RECEIVER";
      path.message = "No copper path from driver " + channel.driver + " to " +
                     id + ".";
      path.refs = {"node:" + id, "node:" + channel.driver};
      solve.paths.push_back(std::move(path));
      continue;
    }
    path.measured = true;
    path.length_nm = distance[sink->second];
    solve.paths.push_back(std::move(path));
  }
  return solve;
}

}  // namespace highspeed_detail
}  // namespace mota
