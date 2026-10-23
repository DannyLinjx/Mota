// Physical copper connectivity extracted from real shapes, not net labels.
#pragma once

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "mota/shapes.hpp"
#include "mota/types.hpp"
#include "mota/zones.hpp"

namespace mota {

struct CopperNode {
  std::string id;
  std::string net;
  std::string kind;  // pad | track | via | fill
  std::string component_id;
  std::string pin;
  std::string zone_id;
  bool plated = true;
  bool is_fill = false;
  bool anchored = true;
  int64_t width_nm = 0;
  int64_t drill_nm = 0;
  int64_t pad_outer_nm = 0;
  Vec2 via_center{};
  int via_start = -1;
  int via_end = -1;
  std::vector<std::pair<int, Shape>> layer_shapes;
};

struct NetTieSpec {
  std::string id;
  std::string a;
  std::string b;
  bool allowed = true;
};

struct NetRequirement {
  std::string net;
  int required_groups = 1;
};

struct ZoneConnectivityInfo {
  std::string zone_id;
  std::string net;
  int layer_index = -1;
  std::vector<Vec2> outline;
  int islands_removed = 0;
  int64_t removed_area_nm2 = 0;
};

struct ConnectivityIssue {
  std::string code;
  std::string severity;  // error | warning | info
  std::string message;
  std::string net;
  std::vector<std::string> object_refs;
  int64_t count = 1;
  int64_t value_nm = 0;
};

struct NetConnectivityReport {
  std::string net;
  int pads = 0;
  int groups = 0;
  int required_groups = 1;
  int u = 0;
  bool connected = true;
  std::vector<std::string> unconnected_endpoints;
};

struct ConnectivityReport {
  std::vector<NetConnectivityReport> nets;
  int u_total = 0;
  int short_count = 0;
  int open_count = 0;
  int missing_endpoint_count = 0;
  std::vector<ConnectivityIssue> issues;
  std::vector<std::string> net_ties_used;
};

struct ConnectivityInput {
  std::vector<CopperNode> nodes;
  std::vector<NetTieSpec> net_ties;
  std::vector<std::string> required_endpoints;
  std::vector<NetRequirement> net_requirements;
  std::vector<ZoneConnectivityInfo> zones;
};

ConnectivityReport analyze_connectivity(const ConnectivityInput& input);

}  // namespace mota
