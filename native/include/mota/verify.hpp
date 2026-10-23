// Independent design-rule checks over frozen copper geometry.
//
// The verifier never consumes a router's "complete" flag. Every rule records
// how many objects it actually examined; a rule with nothing to examine is
// NOT_CHECKED, and an empty required rule set can never be a pass.
#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <vector>

#include "mota/connectivity.hpp"
#include "mota/types.hpp"

namespace mota {

struct HoleSpec {
  std::string id;
  Shape shape;
  bool plated = false;
};

struct DrcRule {
  std::string id;
  std::string kind;  // short | clearance | track_width | via_drill |
                     // via_annular_ring | board_edge_clearance | hole_clearance
  bool required = true;
  int64_t value_nm = 0;
  std::string net_class;
  int layer = -1;
};

struct DrcViolation {
  std::string code;
  std::string message;
  std::string net;
  std::vector<std::string> object_refs;
  int64_t actual_nm = 0;
  int64_t required_nm = 0;
};

struct DrcCheck {
  std::string id;
  std::string kind;
  bool required = true;
  std::string status;  // PASS | FAIL | NOT_CHECKED
  int64_t checked_objects = 0;
  std::string message;
  std::string coverage;
  std::vector<DrcViolation> violations;
};

struct DrcReport {
  std::string status;  // PASS | FAIL | NOT_CHECKED
  bool review_ready = false;
  int required_checks = 0;
  int passed_checks = 0;
  int failed_checks = 0;
  int not_checked_checks = 0;
  int violation_count = 0;
  std::vector<DrcCheck> checks;
  std::vector<std::string> diagnostics;
};

struct DrcInput {
  std::vector<CopperNode> nodes;
  std::vector<HoleSpec> holes;
  std::vector<Vec2> board_outline;
  std::map<std::string, std::string> net_classes;
  std::vector<DrcRule> rules;
};

DrcReport run_drc(const DrcInput& input);

}  // namespace mota
