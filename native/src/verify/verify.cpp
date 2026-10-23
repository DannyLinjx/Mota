#include "mota/verify.hpp"

#include <algorithm>
#include <limits>
#include <map>
#include <set>

#include "mota/rtree.hpp"

namespace mota {
namespace {

struct Entry {
  size_t node = 0;
  int layer = -1;
  Shape shape;
  Aabb bounds{};
};

struct PairDistance {
  int64_t distance_nm = std::numeric_limits<int64_t>::max();
  int64_t error_nm = 0;
};

struct PairKey {
  size_t a = 0;
  size_t b = 0;
  int layer = -1;

  bool operator<(const PairKey& other) const {
    if (a != other.a) return a < other.a;
    if (b != other.b) return b < other.b;
    return layer < other.layer;
  }
};

std::string net_class_of(const DrcInput& input, const std::string& net) {
  const auto found = input.net_classes.find(net);
  return found == input.net_classes.end() ? "signal" : found->second;
}

bool class_selected(const DrcRule& rule, const DrcInput& input,
                    const std::string& net) {
  if (rule.net_class.empty()) return true;
  return net_class_of(input, net) == rule.net_class;
}

std::string node_ref(const CopperNode& node) { return node.id; }

int64_t board_edge_distance(const Shape& shape,
                            const std::vector<Vec2>& outline) {
  const Aabb bounds = shape_bounds(shape).bounds;
  const Vec2 center{bounds.center_x(), bounds.center_y()};
  if (!polygon_contains(outline, center)) return 0;
  int64_t best = std::numeric_limits<int64_t>::max();
  for (size_t i = 0; i < outline.size(); ++i) {
    const Shape edge = make_segment(outline[i],
                                    outline[(i + 1) % outline.size()]);
    const DistanceResult distance = shape_distance(shape, edge);
    best = std::min(best, distance.distance_nm);
  }
  return best == std::numeric_limits<int64_t>::max() ? 0 : best;
}

std::string check_status(int64_t checked, size_t violations) {
  if (checked == 0) return "NOT_CHECKED";
  return violations == 0 ? "PASS" : "FAIL";
}

}  // namespace

DrcReport run_drc(const DrcInput& input) {
  DrcReport report;
  if (input.rules.empty()) {
    report.status = "NOT_CHECKED";
    report.review_ready = false;
    report.diagnostics.push_back("NO_RULES");
    return report;
  }
  if (input.board_outline.size() < 3) {
    report.status = "NOT_CHECKED";
    report.review_ready = false;
    report.diagnostics.push_back("BOARD_OUTLINE_MISSING");
    return report;
  }

  std::vector<Entry> entries;
  std::map<int, std::vector<size_t>> local_entries;
  for (size_t node_index = 0; node_index < input.nodes.size(); ++node_index) {
    for (const auto& layer_shape : input.nodes[node_index].layer_shapes) {
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
    for (size_t global : layer.second) boxes.push_back(entries[global].bounds);
    RTree tree;
    tree.build(std::move(boxes));
    trees.emplace(layer.first, std::move(tree));
  }

  int64_t max_clearance = 0;
  for (const DrcRule& rule : input.rules) {
    if (rule.kind == "clearance" || rule.kind == "short") {
      max_clearance = std::max(max_clearance, rule.value_nm);
    }
  }

  std::map<PairKey, PairDistance> pairs;
  if (max_clearance >= 0) {
    for (size_t i = 0; i < entries.size(); ++i) {
      const Entry& entry = entries[i];
      const auto tree = trees.find(entry.layer);
      if (tree == trees.end()) continue;
      std::vector<size_t> hits;
      tree->second.query(entry.bounds.expanded(max_clearance + 2), hits);
      for (size_t local : hits) {
        const size_t j = local_entries[entry.layer][local];
        if (j <= i) continue;
        const Entry& other = entries[j];
        if (other.node == entry.node) continue;
        const CopperNode& a = input.nodes[entry.node];
        const CopperNode& b = input.nodes[other.node];
        if (a.net == b.net) continue;
        PairKey key{std::min(entry.node, other.node),
                    std::max(entry.node, other.node), entry.layer};
        const DistanceResult distance = shape_distance(entry.shape, other.shape);
        PairDistance& record = pairs[key];
        if (distance.distance_nm < record.distance_nm) {
          record.distance_nm = distance.distance_nm;
          record.error_nm = distance.error_nm;
        }
      }
    }
  }

  for (const DrcRule& rule : input.rules) {
    DrcCheck check;
    check.id = rule.id;
    check.kind = rule.kind;
    check.required = rule.required;

    if (rule.kind == "short" || rule.kind == "clearance") {
      int64_t checked = 0;
      for (const auto& pair : pairs) {
        const PairKey& key = pair.first;
        if (key.a >= input.nodes.size() || key.b >= input.nodes.size()) continue;
        const CopperNode& a = input.nodes[key.a];
        const CopperNode& b = input.nodes[key.b];
        if (!class_selected(rule, input, a.net) ||
            !class_selected(rule, input, b.net)) {
          continue;
        }
        if (rule.layer >= 0 && key.layer != rule.layer) continue;
        ++checked;
        const int64_t effective = pair.second.distance_nm - pair.second.error_nm;
        const bool violates = rule.kind == "short"
                                  ? pair.second.distance_nm <= 1
                                  : effective < rule.value_nm;
        if (!violates) continue;
        DrcViolation violation;
        violation.code = rule.kind == "short" ? "COPPER_SHORT" : "CLEARANCE";
        violation.message = rule.kind == "short"
                                ? "different nets touch: " + a.net + " / " + b.net
                                : "copper spacing " + std::to_string(effective) +
                                      " nm is below " +
                                      std::to_string(rule.value_nm) + " nm";
        violation.net = a.net;
        violation.object_refs = {node_ref(a), node_ref(b)};
        violation.actual_nm = effective;
        violation.required_nm = rule.value_nm;
        check.violations.push_back(std::move(violation));
      }
      if (checked == 0) {
        // No pair fell inside the broad-phase halo. If at least two selected
        // nets share a layer, the rule did examine a meaningful net pair and
        // the absence of a near pair is a PASS, not "nothing to check".
        std::map<std::string, std::set<int>> layers_by_net;
        for (const CopperNode& node : input.nodes) {
          if (node.net.empty() || !class_selected(rule, input, node.net)) continue;
          for (const auto& layer_shape : node.layer_shapes) {
            layers_by_net[node.net].insert(layer_shape.first);
          }
        }
        std::vector<std::string> names;
        for (const auto& entry : layers_by_net) names.push_back(entry.first);
        bool shared_layer = false;
        for (size_t i = 0; i < names.size() && !shared_layer; ++i) {
          for (size_t j = i + 1; j < names.size() && !shared_layer; ++j) {
            for (int layer : layers_by_net[names[i]]) {
              if (layers_by_net[names[j]].count(layer)) {
                shared_layer = true;
                break;
              }
            }
          }
        }
        if (shared_layer) checked = 1;
      }
      check.checked_objects = checked;
      check.coverage = "compared " + std::to_string(checked) +
                       " different-net node pairs on shared layers";
    } else if (rule.kind == "track_width") {
      for (const CopperNode& node : input.nodes) {
        if (node.kind != "track" || !class_selected(rule, input, node.net)) continue;
        ++check.checked_objects;
        if (node.width_nm >= rule.value_nm) continue;
        check.violations.push_back(DrcViolation{
            "TRACK_WIDTH", "track width below the rule", node.net, {node.id},
            node.width_nm, rule.value_nm});
      }
      check.coverage = "measured " + std::to_string(check.checked_objects) +
                       " tracks";
    } else if (rule.kind == "via_drill") {
      for (const CopperNode& node : input.nodes) {
        if (node.kind != "via") continue;
        ++check.checked_objects;
        if (node.drill_nm >= rule.value_nm) continue;
        check.violations.push_back(DrcViolation{
            "VIA_DRILL", "via drill below the rule", node.net, {node.id},
            node.drill_nm, rule.value_nm});
      }
      check.coverage = "measured " + std::to_string(check.checked_objects) +
                       " vias";
    } else if (rule.kind == "via_annular_ring") {
      for (const CopperNode& node : input.nodes) {
        if (node.kind != "via") continue;
        ++check.checked_objects;
        const int64_t ring = (node.pad_outer_nm - node.drill_nm) / 2;
        if (ring >= rule.value_nm) continue;
        check.violations.push_back(DrcViolation{
            "VIA_ANNULAR_RING", "via annular ring below the rule", node.net,
            {node.id}, ring, rule.value_nm});
      }
      check.coverage = "measured " + std::to_string(check.checked_objects) +
                       " vias";
    } else if (rule.kind == "board_edge_clearance") {
      for (const CopperNode& node : input.nodes) {
        int64_t best = std::numeric_limits<int64_t>::max();
        for (const auto& layer_shape : node.layer_shapes) {
          best = std::min(best,
                          board_edge_distance(layer_shape.second,
                                              input.board_outline));
        }
        if (best == std::numeric_limits<int64_t>::max()) continue;
        ++check.checked_objects;
        if (best >= rule.value_nm) continue;
        check.violations.push_back(DrcViolation{
            "BOARD_EDGE", "copper is too close to the board edge", node.net,
            {node.id}, best, rule.value_nm});
      }
      check.coverage = "measured " + std::to_string(check.checked_objects) +
                       " copper nodes";
    } else if (rule.kind == "hole_clearance") {
      for (const HoleSpec& hole : input.holes) {
        for (const CopperNode& node : input.nodes) {
          int64_t best = std::numeric_limits<int64_t>::max();
          for (const auto& layer_shape : node.layer_shapes) {
            best = std::min(best,
                            shape_distance(layer_shape.second, hole.shape)
                                .distance_nm);
          }
          if (best == std::numeric_limits<int64_t>::max()) continue;
          ++check.checked_objects;
          if (best >= rule.value_nm) continue;
          check.violations.push_back(DrcViolation{
              "HOLE_CLEARANCE", "copper is too close to a hole", node.net,
              {node.id, hole.id}, best, rule.value_nm});
        }
      }
      check.coverage = "measured " + std::to_string(check.checked_objects) +
                       " copper/hole pairs";
    } else {
      check.status = "NOT_CHECKED";
      check.message = "unsupported rule kind " + rule.kind;
      report.diagnostics.push_back("UNSUPPORTED_RULE_KIND:" + rule.id);
      report.checks.push_back(std::move(check));
      continue;
    }

    check.status = check_status(check.checked_objects, check.violations.size());
    if (check.status == "NOT_CHECKED") {
      check.message = "no applicable object was examined";
    } else if (check.status == "PASS") {
      check.message = "all examined objects meet the rule";
    } else {
      check.message = std::to_string(check.violations.size()) + " violation(s)";
    }
    report.checks.push_back(std::move(check));
  }

  for (const DrcCheck& check : report.checks) {
    if (check.required) ++report.required_checks;
    if (check.status == "PASS") {
      ++report.passed_checks;
    } else if (check.status == "FAIL") {
      ++report.failed_checks;
    } else {
      ++report.not_checked_checks;
    }
    report.violation_count += static_cast<int>(check.violations.size());
  }
  const bool any_failed = std::any_of(
      report.checks.begin(), report.checks.end(),
      [](const DrcCheck& check) { return check.status == "FAIL"; });
  const bool required_not_checked = std::any_of(
      report.checks.begin(), report.checks.end(), [](const DrcCheck& check) {
        return check.required && check.status == "NOT_CHECKED";
      });
  if (any_failed) {
    report.status = "FAIL";
  } else if (required_not_checked || report.required_checks == 0) {
    report.status = "NOT_CHECKED";
    if (report.required_checks == 0) report.diagnostics.push_back("NO_REQUIRED_RULES");
  } else {
    report.status = "PASS";
  }
  report.review_ready = report.status == "PASS" &&
                        report.required_checks > 0;
  return report;
}

}  // namespace mota
