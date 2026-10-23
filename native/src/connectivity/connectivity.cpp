#include "mota/connectivity.hpp"

#include <algorithm>
#include <map>
#include <numeric>
#include <set>

#include "mota/rtree.hpp"

namespace mota {
namespace {

struct UnionFind {
  std::vector<int> parent;
  explicit UnionFind(size_t size) : parent(size) {
    std::iota(parent.begin(), parent.end(), 0);
  }
  int find(int value) {
    while (parent[static_cast<size_t>(value)] != value) {
      parent[static_cast<size_t>(value)] =
          parent[static_cast<size_t>(parent[static_cast<size_t>(value)])];
      value = parent[static_cast<size_t>(value)];
    }
    return value;
  }
  void unite(int a, int b) {
    a = find(a);
    b = find(b);
    if (a != b) parent[static_cast<size_t>(a)] = b;
  }
};

struct Entry {
  size_t node = 0;
  int layer = -1;
  Shape shape;
  Aabb bounds{};
};

std::string pair_key(size_t a, size_t b) {
  if (a > b) std::swap(a, b);
  return std::to_string(a) + ":" + std::to_string(b);
}

void add_issue(std::vector<ConnectivityIssue>& issues, const std::string& code,
               const std::string& severity, const std::string& message,
               const std::string& net, std::vector<std::string> refs,
               int64_t count = 1, int64_t value_nm = 0) {
  issues.push_back(ConnectivityIssue{code, severity, message, net,
                                     std::move(refs), count, value_nm});
}

}  // namespace

ConnectivityReport analyze_connectivity(const ConnectivityInput& input) {
  ConnectivityReport report;
  const size_t node_count = input.nodes.size();
  UnionFind uf(node_count);

  std::vector<Entry> entries;
  std::map<int, std::vector<size_t>> local_entries;
  for (size_t node_index = 0; node_index < input.nodes.size(); ++node_index) {
    const CopperNode& node = input.nodes[node_index];
    for (const auto& layer_shape : node.layer_shapes) {
      Entry entry;
      entry.node = node_index;
      entry.layer = layer_shape.first;
      entry.shape = layer_shape.second;
      entry.bounds = shape_bounds(entry.shape).bounds;
      entries.push_back(std::move(entry));
    }
  }
  for (size_t i = 0; i < entries.size(); ++i) {
    local_entries[entries[i].layer].push_back(i);
  }
  std::map<int, RTree> trees;
  for (const auto& layer : local_entries) {
    std::vector<Aabb> boxes;
    boxes.reserve(layer.second.size());
    for (size_t global : layer.second) boxes.push_back(entries[global].bounds);
    RTree tree;
    tree.build(std::move(boxes));
    trees.emplace(layer.first, std::move(tree));
  }

  std::set<std::string> checked;
  std::map<std::string, size_t> by_id;
  for (size_t i = 0; i < input.nodes.size(); ++i) by_id[input.nodes[i].id] = i;

  std::set<std::string> tie_pairs;
  for (const NetTieSpec& tie : input.net_ties) {
    const auto a = by_id.find(tie.a);
    const auto b = by_id.find(tie.b);
    if (a == by_id.end() || b == by_id.end()) {
      add_issue(report.issues, "NET_TIE_UNRESOLVED", "error",
                "net-tie " + tie.id + " references a missing copper node",
                "", {tie.a, tie.b});
      continue;
    }
    if (!tie.allowed) {
      add_issue(report.issues, "NET_TIE_NOT_ALLOWED", "error",
                "net-tie " + tie.id + " is not allowed by the frozen contract",
                "", {tie.a, tie.b});
      continue;
    }
    uf.unite(static_cast<int>(a->second), static_cast<int>(b->second));
    tie_pairs.insert(pair_key(a->second, b->second));
    report.net_ties_used.push_back(tie.id);
  }

  for (size_t i = 0; i < entries.size(); ++i) {
    const Entry& entry = entries[i];
    const auto tree = trees.find(entry.layer);
    if (tree == trees.end()) continue;
    std::vector<size_t> hits;
    tree->second.query(entry.bounds.expanded(1), hits);
    for (size_t local : hits) {
      const size_t j = local_entries[entry.layer][local];
      if (j <= i) continue;
      const Entry& other = entries[j];
      if (other.node == entry.node) continue;
      const std::string key =
          pair_key(entry.node, other.node) + ":" + std::to_string(entry.layer);
      if (!checked.insert(key).second) continue;
      const DistanceResult distance = shape_distance(entry.shape, other.shape);
      if (distance.distance_nm > 1) continue;
      const CopperNode& a = input.nodes[entry.node];
      const CopperNode& b = input.nodes[other.node];
      const bool tied = tie_pairs.count(pair_key(entry.node, other.node)) != 0;
      if (!a.net.empty() && !b.net.empty() && a.net != b.net) {
        if (tied) {
          uf.unite(static_cast<int>(entry.node), static_cast<int>(other.node));
          continue;
        }
        ++report.short_count;
        add_issue(report.issues, "COPPER_SHORT", "error",
                  "different nets " + a.net + " and " + b.net +
                      " touch on the same layer",
                  a.net, {a.id, b.id}, 1, distance.distance_nm);
        continue;
      }
      uf.unite(static_cast<int>(entry.node), static_cast<int>(other.node));
    }
  }

  std::set<std::string> known;
  for (const CopperNode& node : input.nodes) known.insert(node.id);
  for (const std::string& endpoint : input.required_endpoints) {
    if (known.count(endpoint) == 0) {
      ++report.missing_endpoint_count;
      add_issue(report.issues, "MISSING_ENDPOINT", "error",
                "required endpoint " + endpoint + " has no copper node",
                "", {endpoint});
    }
  }

  std::map<std::string, int> required_groups;
  for (const NetRequirement& requirement : input.net_requirements) {
    required_groups[requirement.net] = std::max(1, requirement.required_groups);
  }

  std::map<std::string, std::vector<size_t>> pads_by_net;
  for (size_t i = 0; i < input.nodes.size(); ++i) {
    const CopperNode& node = input.nodes[i];
    if (node.kind == "pad" && !node.net.empty() && node.plated) {
      pads_by_net[node.net].push_back(i);
    }
  }
  for (const auto& entry : pads_by_net) {
    NetConnectivityReport net;
    net.net = entry.first;
    net.pads = static_cast<int>(entry.second.size());
    net.required_groups = required_groups.count(entry.first)
                              ? required_groups[entry.first]
                              : 1;
    std::map<int, int> sizes;
    for (size_t index : entry.second) ++sizes[uf.find(static_cast<int>(index))];
    net.groups = static_cast<int>(sizes.size());
    int best_root = uf.find(static_cast<int>(entry.second.front()));
    int best_size = sizes[best_root];
    for (const auto& group : sizes) {
      if (group.second > best_size) {
        best_size = group.second;
        best_root = group.first;
      }
    }
    for (size_t index : entry.second) {
      if (uf.find(static_cast<int>(index)) != best_root) {
        net.unconnected_endpoints.push_back(input.nodes[index].id);
      }
    }
    net.connected = net.groups <= net.required_groups;
    net.u = std::max(0, net.groups - net.required_groups);
    report.u_total += net.u;
    if (net.u > 0) ++report.open_count;
    if (net.u > 0) {
      add_issue(report.issues, "NET_OPEN", "error",
                "net " + entry.first + " needs " + std::to_string(net.u) +
                    " more connection(s)",
                entry.first, net.unconnected_endpoints, net.u);
    }
    report.nets.push_back(std::move(net));
  }
  std::sort(report.nets.begin(), report.nets.end(),
            [](const NetConnectivityReport& a, const NetConnectivityReport& b) {
              return a.net < b.net;
            });

  for (const ZoneConnectivityInfo& zone : input.zones) {
    if (zone.islands_removed > 0) {
      add_issue(report.issues, "PLANE_ISLAND", "warning",
                "zone " + zone.zone_id + " had " +
                    std::to_string(zone.islands_removed) +
                    " unanchored island(s) removed",
                zone.net, {"zone:" + zone.zone_id}, zone.islands_removed,
                zone.removed_area_nm2);
    }
  }
  for (size_t i = 0; i < input.nodes.size(); ++i) {
    const CopperNode& node = input.nodes[i];
    if (node.is_fill && !node.anchored) {
      add_issue(report.issues, "PLANE_ISLAND", "error",
                "fill component " + node.id + " is not anchored to net " +
                    node.net,
                node.net, {node.id});
    }
  }

  for (const ZoneConnectivityInfo& zone : input.zones) {
    if (zone.outline.size() < 3) continue;
    for (size_t i = 0; i < input.nodes.size(); ++i) {
      const CopperNode& pad = input.nodes[i];
      if (pad.kind != "pad" || pad.net != zone.net || !pad.plated) continue;
      bool on_layer = false;
      Vec2 center{};
      for (const auto& layer_shape : pad.layer_shapes) {
        if (layer_shape.first != zone.layer_index) continue;
        on_layer = true;
        const Aabb bounds = shape_bounds(layer_shape.second).bounds;
        center = {bounds.center_x(), bounds.center_y()};
      }
      if (!on_layer || !polygon_contains(zone.outline, center)) continue;
      const int root = uf.find(static_cast<int>(i));
      bool connected_to_fill = false;
      for (size_t j = 0; j < input.nodes.size(); ++j) {
        const CopperNode& other = input.nodes[j];
        if (!other.is_fill || other.zone_id != zone.zone_id) continue;
        if (uf.find(static_cast<int>(j)) == root) {
          connected_to_fill = true;
          break;
        }
      }
      if (!connected_to_fill) {
        add_issue(report.issues, "THERMAL_DISCONNECT", "error",
                  "pad " + pad.id + " lies in zone " + zone.zone_id +
                      " but has no thermal or solid copper connection",
                  pad.net, {pad.id, "zone:" + zone.zone_id});
      }
    }
  }

  for (size_t i = 0; i < input.nodes.size(); ++i) {
    const CopperNode& via = input.nodes[i];
    if (via.kind != "via") continue;
    if (via.via_start < 0 || via.via_end <= via.via_start ||
        via.layer_shapes.empty()) {
      add_issue(report.issues, "VIA_SPAN_INVALID", "error",
                "via " + via.id + " has an invalid layer span", via.net,
                {via.id});
      continue;
    }
    if (!via.plated && !via.net.empty()) {
      add_issue(report.issues, "VIA_SPAN_INVALID", "error",
                "NPTH " + via.id + " cannot be used as a net via", via.net,
                {via.id});
    }
    const Shape via_pad = make_circle(via.via_center, via.pad_outer_nm / 2);
    std::set<std::string> reported;
    for (const Entry& entry : entries) {
      if (entry.node == i) continue;
      if (entry.layer >= via.via_start && entry.layer <= via.via_end) continue;
      const CopperNode& other = input.nodes[entry.node];
      if (other.net != via.net) continue;
      if (shape_distance(via_pad, entry.shape).distance_nm > 1) continue;
      if (!reported.insert(other.id).second) continue;
      add_issue(report.issues, "VIA_SPAN_OPEN", "error",
                "via " + via.id + " does not span " + other.id +
                    " but its pad touches that copper",
                via.net, {via.id, other.id});
    }
  }

  std::sort(report.issues.begin(), report.issues.end(),
            [](const ConnectivityIssue& a, const ConnectivityIssue& b) {
              if (a.code != b.code) return a.code < b.code;
              return a.message < b.message;
            });
  return report;
}

}  // namespace mota
