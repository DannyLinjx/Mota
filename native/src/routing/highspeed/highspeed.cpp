// C11 orchestration: channel checks, differential bundle measurement, delay
// constraints and tuning. Geometry/length results never upgrade SI or PI.
#include <algorithm>
#include <cstdint>
#include <limits>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include "mota/sha256.hpp"

#include "internal.hpp"

namespace mota {
namespace highspeed_detail {
namespace {

CheckResult& add_check(HighspeedResult& result, const std::string& kind,
                       const std::string& subject, const std::string& status,
                       const std::string& reason, const std::string& message) {
  CheckResult check;
  check.kind = kind;
  check.subject = subject;
  check.status = status;
  check.reason = reason;
  check.message = message;
  std::string id = kind + ":" + subject;
  int suffix = 1;
  const auto exists = [&](const std::string& candidate) {
    return std::any_of(result.checks.begin(), result.checks.end(),
                       [&](const CheckResult& item) {
                         return item.check_id == candidate;
                       });
  };
  while (exists(id)) {
    id = kind + ":" + subject + "#" + std::to_string(++suffix);
  }
  check.check_id = id;
  result.checks.push_back(std::move(check));
  return result.checks.back();
}

std::vector<std::string> channel_nets(const Channel& channel) {
  std::vector<std::string> nets;
  for (const ChannelNode& node : channel.nodes) {
    if (node.net.empty()) continue;
    if (std::find(nets.begin(), nets.end(), node.net) == nets.end()) {
      nets.push_back(node.net);
    }
  }
  return nets;
}

std::vector<std::pair<std::string, std::string>> channel_pins(
    const Channel& channel) {
  std::vector<std::pair<std::string, std::string>> pins;
  for (const ChannelNode& node : channel.nodes) {
    if (node.component_id.empty()) continue;
    const auto entry = std::make_pair(node.component_id, node.pin);
    if (std::find(pins.begin(), pins.end(), entry) == pins.end()) {
      pins.push_back(entry);
    }
  }
  return pins;
}

std::string point_key(const std::vector<Vec2>& points) {
  std::string out;
  for (const Vec2& point : points) {
    out += std::to_string(point.x);
    out += ",";
    out += std::to_string(point.y);
    out += ";";
  }
  return out;
}

std::string semantic_payload(const HighspeedInput& input,
                             const HighspeedResult& result) {
  std::string text = "highspeed|seed=" + std::to_string(input.rules.seed);
  for (const Channel& channel : input.channels) {
    text += "|ch:" + channel.id + ":" + channel.net + "|tol=" +
            std::to_string(channel.length_tolerance_nm) + "|delay=" +
            std::to_string(channel.target_delay_ps) + "/" +
            std::to_string(channel.delay_tolerance_ps);
    for (const ChannelNode& node : channel.nodes) {
      text += "|n:" + node.id + ":" + node.kind + ":" + node.net + ":" +
              std::to_string(node.position.x) + "," +
              std::to_string(node.position.y);
    }
    for (const ChannelLink& link : channel.links) {
      text += "|l:" + link.id + ":" + link.from + "-" + link.to + ":" +
              link.kind + ":" + std::to_string(link.internal_length_nm);
    }
  }
  for (const DiffPair& pair : input.pairs) {
    text += "|pair:" + pair.id + ":" + pair.net_p + "/" + pair.net_n +
            "|gap=" + std::to_string(pair.gap_nm) + "/" +
            std::to_string(pair.gap_tolerance_nm) + "|skew=" +
            std::to_string(pair.skew_tolerance_nm) + "|uncoupled=" +
            std::to_string(pair.max_uncoupled_nm) + "|delay=" +
            std::to_string(pair.delay_skew_tolerance_ps);
  }
  for (const HighspeedTrack& track : input.tracks) {
    text += "|t:" + track.id + ":" + track.net + ":" + track.layer_id + ":" +
            std::to_string(track.stackup_layer) + ":" +
            point_key(track.points);
  }
  for (const HighspeedVia& via : input.vias) {
    text += "|v:" + via.id + ":" + via.net + ":" +
            std::to_string(via.center.x) + "," +
            std::to_string(via.center.y) + ":" +
            std::to_string(via.start_layer) + "-" +
            std::to_string(via.end_layer) + ":" +
            std::to_string(via.delay_ps);
  }
  for (const LayerDelayModel& model : input.layer_models) {
    text += "|lm:" + model.layer_id + ":" +
            std::to_string(model.propagation_ps_per_mm) + ":" +
            model.reference_layer_id;
  }
  for (const PackageModel& model : input.package_models) {
    text += "|pm:" + model.component_id + ":" + model.pin + ":" +
            std::to_string(model.length_nm) + ":" +
            std::to_string(model.delay_ps) + ":" +
            (model.present ? "1" : "0");
  }
  for (const TuningRegion& region : input.tuning_regions) {
    text += "|tr:" + region.id + ":" + region.net + ":" +
            std::to_string(region.max_extra_nm);
  }
  for (const CheckResult& check : result.checks) {
    text += "|c:" + check.check_id + ":" + check.status + ":" +
            check.reason + ":" + std::to_string(check.measured_nm) + ":" +
            std::to_string(check.measured_ps);
  }
  for (const TuningPatch& patch : result.tuning) {
    text += "|tp:" + patch.id + ":" + std::to_string(patch.added_length_nm) +
            ":" + point_key(patch.points);
  }
  return text;
}

}  // namespace

HighspeedResult analyze_highspeed_impl(const HighspeedInput& input) {
  HighspeedResult result;
  result.seed = input.rules.seed;
  result.si_status = "NOT_CHECKED";
  result.pi_status = "NOT_CHECKED";
  int64_t nodes = 0;
  bool budget_hit = false;

  for (const Channel& channel : input.channels) {
    const ChannelSolve solve = solve_channel(input, channel);
    const std::vector<std::string> nets = channel_nets(channel);
    const std::vector<std::pair<std::string, std::string>> pins =
        channel_pins(channel);

    if (solve.budget_hit) {
      budget_hit = true;
    }
    CheckResult& topology = add_check(
        result, "channel_topology", channel.id, solve.topology_status,
        solve.topology_reason,
        solve.topology_status == "PASS" ? solve.topology_message
                                        : solve.topology_message);
    topology.object_refs = solve.topology_refs;
    if (!solve.open_links.empty() &&
        std::find(topology.object_refs.begin(), topology.object_refs.end(),
                  solve.open_links.front()) == topology.object_refs.end()) {
      topology.object_refs.push_back(solve.open_links.front());
    }
    if (solve.topology_status == "PASS" && !solve.open_links.empty()) {
      topology.status = "FAIL";
      topology.reason = "OFF_COPPER_VIA";
      topology.message = "A via is not attached to copper at " +
                         solve.open_links.front() + ".";
      topology.object_refs = solve.open_links;
    }

    // Length match across all receivers of the channel.
    if (channel.length_tolerance_nm > 0) {
      bool measured = !solve.paths.empty();
      int64_t shortest = std::numeric_limits<int64_t>::max();
      int64_t longest = 0;
      std::string unmeasured;
      for (const ChannelPath& path : solve.paths) {
        if (!path.measured) {
          measured = false;
          if (unmeasured.empty()) unmeasured = path.receiver;
          continue;
        }
        shortest = std::min(shortest, path.length_nm);
        longest = std::max(longest, path.length_nm);
      }
      if (!measured && unmeasured.empty() && !channel.receivers.empty()) {
        unmeasured = channel.receivers.front();
      }
      CheckResult& check = add_check(
          result, "length_match", channel.id, "NOT_CHECKED", "", "");
      check.limit_nm = channel.length_tolerance_nm;
      if (channel.receivers.size() < 2) {
        check.reason = "SINGLE_RECEIVER";
        check.message =
            "A length window is declared but there is only one receiver; "
            "there is nothing to match.";
        check.object_refs = {"channel:" + channel.id};
      } else if (solve.budget_hit) {
        check.reason = "SEARCH_BUDGET";
        check.message = "Channel paths stopped at the search budget; the "
                        "length window is UNKNOWN.";
        check.object_refs = {"channel:" + channel.id};
      } else if (!measured) {
        check.reason = "PATH_UNMEASURED";
        check.message = "Receiver " + unmeasured +
                        " has no measured copper path.";
        check.object_refs = {"node:" + unmeasured};
      } else {
        check.measured_nm = longest - shortest;
        check.status = check.measured_nm <= channel.length_tolerance_nm
                           ? "PASS" : "FAIL";
        if (check.status == "FAIL") {
          check.reason = "LENGTH_SKEW";
        }
        check.message = "Receiver path length spread " +
                        std::to_string(check.measured_nm) + " nm against a " +
                        std::to_string(channel.length_tolerance_nm) +
                        " nm window.";
        check.object_refs = {"channel:" + channel.id};
        for (const ChannelPath& path : solve.paths) {
          check.object_refs.push_back("node:" + path.receiver);
        }
      }
    }

    // Delay: needs layer, via and package models. Anything missing stays
    // NOT_CHECKED and can never be reported as timing compliance.
    const DelayEstimate delay = estimate_delay(input, nets, pins);
    {
      CheckResult& check = add_check(result, "delay", channel.id,
                                     "NOT_CHECKED", "", "");
      check.limit_ps = channel.target_delay_ps + channel.delay_tolerance_ps;
      check.measured_ps = delay.delay_ps;
      check.measured_nm = delay.length_nm;
      check.object_refs = delay.refs.empty()
                              ? std::vector<std::string>{"channel:" + channel.id}
                              : delay.refs;
      if (!delay.complete) {
        check.reason = delay.reason;
        check.message = delay.message;
      } else if (channel.target_delay_ps <= 0.0) {
        check.status = "SKIPPED";
        check.reason = "NO_LIMIT";
        check.message = "Models are complete (" +
                        std::to_string(delay.delay_ps) + " ps) but the channel "
                        "declares no delay target.";
      } else {
        const double delta = delay.delay_ps - channel.target_delay_ps;
        const bool inside = std::abs(delta) <= channel.delay_tolerance_ps;
        check.status = inside ? "PASS" : "FAIL";
        if (!inside) check.reason = "DELAY_OUT_OF_WINDOW";
        check.message = "Propagated delay " +
                        std::to_string(delay.delay_ps) + " ps against target " +
                        std::to_string(channel.target_delay_ps) + " ± " +
                        std::to_string(channel.delay_tolerance_ps) + " ps.";
      }
    }

    const ReturnPathCheck return_path = check_return_path(input, nets);
    {
      CheckResult& check = add_check(result, "return_path", channel.id,
                                     "NOT_CHECKED", "", "");
      check.object_refs = return_path.refs.empty()
                              ? std::vector<std::string>{"channel:" + channel.id}
                              : return_path.refs;
      if (!return_path.complete) {
        check.reason = return_path.reason;
        check.message = return_path.message;
      } else if (return_path.crossings > 0) {
        check.status = "FAIL";
        check.reason = return_path.reason;
        check.message = return_path.message;
      } else if (return_path.samples == 0) {
        check.status = "NOT_CHECKED";
        check.reason = "NO_GEOMETRY";
        check.message = "No copper segments were available to test the "
                        "return path.";
      } else {
        check.status = "PASS";
        check.message = std::to_string(return_path.samples) +
                        " segment midpoints stay over the reference plane.";
      }
    }
  }

  for (const DiffPair& pair : input.pairs) {
    const PairGeometry geometry = analyze_pair(input, pair, nodes, budget_hit);
    BundleRoute bundle;
    bundle.pair_id = pair.id;
    bundle.nets = {pair.net_p, pair.net_n};
    bundle.coupled_nm = geometry.coupled_nm;
    bundle.uncoupled_nm = geometry.uncoupled_nm;
    bundle.skew_nm = geometry.skew_nm;
    bundle.turn_delta_nm = geometry.turn_delta_nm;
    bundle.p_corners = geometry.p_corners;
    bundle.n_corners = geometry.n_corners;
    bundle.unmatched_corners = geometry.unmatched_corners;
    bundle.turn_compensated = geometry.turn_compensated;
    bundle.shared_vias = geometry.shared_vias;
    bundle.p_vias = geometry.p_vias;
    bundle.n_vias = geometry.n_vias;
    bundle.paired_layers = geometry.paired_layers;
    bundle.sample_step_nm = geometry.sampled_step_nm;
    bundle.status = geometry.measured ? "MEASURED" : "NOT_CHECKED";
    if (budget_hit && !geometry.measured) bundle.status = "BUDGET";

    {
      CheckResult& check = add_check(result, "diff_coupling", pair.id,
                                     "NOT_CHECKED", "", "");
      check.measured_nm = geometry.uncoupled_nm;
      check.limit_nm = pair.max_uncoupled_nm;
      check.object_refs = geometry.refs;
      if (!geometry.measured) {
        check.reason = geometry.reason;
        check.message = geometry.message;
      } else if (pair.max_uncoupled_nm <= 0) {
        check.status = "SKIPPED";
        check.reason = "NO_LIMIT";
        check.message = "Measured uncoupled length " +
                        std::to_string(geometry.uncoupled_nm) +
                        " nm, but no uncoupled limit is declared.";
      } else if (geometry.uncoupled_nm > pair.max_uncoupled_nm) {
        check.status = "FAIL";
        check.reason = "UNCOUPLED_LENGTH";
        check.message = "Uncoupled length " +
                        std::to_string(geometry.uncoupled_nm) +
                        " nm exceeds " +
                        std::to_string(pair.max_uncoupled_nm) +
                        " nm (sampled at " +
                        std::to_string(geometry.sampled_step_nm) + " nm).";
      } else {
        check.status = "PASS";
        check.message = "Uncoupled length " +
                        std::to_string(geometry.uncoupled_nm) + " nm is within " +
                        std::to_string(pair.max_uncoupled_nm) + " nm.";
      }
    }

    {
      CheckResult& check = add_check(result, "diff_skew", pair.id,
                                     "NOT_CHECKED", "", "");
      check.measured_nm = geometry.skew_nm;
      check.limit_nm = pair.skew_tolerance_nm;
      check.object_refs = geometry.refs;
      if (!geometry.measured) {
        check.reason = geometry.reason;
        check.message = geometry.message;
      } else if (pair.skew_tolerance_nm <= 0) {
        check.status = "SKIPPED";
        check.reason = "NO_LIMIT";
        check.message = "Pair length skew " +
                        std::to_string(geometry.skew_nm) +
                        " nm measured; no skew window is declared.";
      } else {
        check.status = geometry.skew_nm <= pair.skew_tolerance_nm ? "PASS"
                                                                  : "FAIL";
        if (check.status == "FAIL") check.reason = "SKEW";
        check.message = "Pair length skew " +
                        std::to_string(geometry.skew_nm) + " nm against " +
                        std::to_string(pair.skew_tolerance_nm) + " nm.";
      }
    }

    {
      CheckResult& check = add_check(result, "diff_turn", pair.id,
                                     "NOT_CHECKED", "", "");
      check.measured_nm = geometry.turn_delta_nm;
      check.object_refs = geometry.refs;
      if (!geometry.measured) {
        check.reason = geometry.reason;
        check.message = geometry.message;
      } else if (geometry.unmatched_corners > 0) {
        check.status = "FAIL";
        check.reason = "UNCOMPENSATED_TURN";
        check.message =
            std::to_string(geometry.unmatched_corners) +
            " corner(s) have no counterpart on the other net; the turn pair "
            "is not compensated.";
      } else if (!geometry.turn_compensated) {
        check.status = "FAIL";
        check.reason = "TURN_ARC_MISMATCH";
        check.message = "Matched corners differ by " +
                        std::to_string(geometry.turn_delta_nm) +
                        " nm of arc length.";
      } else {
        check.status = "PASS";
        check.message = std::to_string(geometry.p_corners) + "/" +
                        std::to_string(geometry.n_corners) +
                        " corners matched within " +
                        std::to_string(geometry.turn_delta_nm) + " nm.";
      }
    }

    {
      CheckResult& check = add_check(result, "pair_layer", pair.id,
                                     "NOT_CHECKED", "", "");
      check.measured_nm = geometry.shared_vias;
      check.object_refs = geometry.unpaired_vias;
      if (!geometry.measured) {
        check.reason = geometry.reason;
        check.message = geometry.message;
      } else if (!pair.pair_layer_transitions) {
        check.status = "SKIPPED";
        check.reason = "NOT_REQUIRED";
        check.message = "Paired layer transitions are not required.";
      } else if (geometry.p_vias == 0 && geometry.n_vias == 0) {
        check.status = "SKIPPED";
        check.reason = "NO_TRANSITION";
        check.message = "Neither net changes layer; nothing to pair.";
      } else if (!geometry.paired_layers) {
        check.status = "FAIL";
        check.reason = "UNPAIRED_TRANSITION";
        check.message = "Layer transition " + geometry.unpaired_vias.front() +
                        " has no counterpart at the same span.";
      } else {
        check.status = "PASS";
        check.message = std::to_string(geometry.shared_vias) +
                        " layer transition(s) paired across the pair.";
      }
    }

    const DelayEstimate p_delay =
        estimate_delay(input, {pair.net_p}, {});
    const DelayEstimate n_delay =
        estimate_delay(input, {pair.net_n}, {});
    bool delay_known = p_delay.complete && n_delay.complete;
    double delay_skew = delay_known ? p_delay.delay_ps - n_delay.delay_ps : 0.0;
    if (budget_hit && !geometry.measured) delay_known = false;
    {
      CheckResult& check = add_check(result, "delay_skew", pair.id,
                                     "NOT_CHECKED", "", "");
      check.measured_ps = delay_skew;
      check.limit_ps = pair.delay_skew_tolerance_ps;
      check.object_refs = geometry.refs;
      if (!delay_known) {
        std::string why = "the pair geometry is not measured";
        std::string reason = geometry.measured ? "MISSING_MODEL"
                                               : geometry.reason;
        if (!p_delay.complete && !p_delay.missing.empty()) {
          why = p_delay.missing.front();
          reason = "MISSING_MODEL";
        } else if (!n_delay.complete && !n_delay.missing.empty()) {
          why = n_delay.missing.front();
          reason = "MISSING_MODEL";
        }
        check.reason = reason.empty() ? "NOT_MEASURED" : reason;
        check.message = "Pair delay skew is NOT_CHECKED: " + why + ".";
      } else if (pair.delay_skew_tolerance_ps <= 0.0) {
        check.status = "SKIPPED";
        check.reason = "NO_LIMIT";
        check.message = "Delay skew " + std::to_string(delay_skew) +
                        " ps measured; no delay window is declared.";
      } else {
        const bool inside =
            std::abs(delay_skew) <= pair.delay_skew_tolerance_ps;
        check.status = inside ? "PASS" : "FAIL";
        if (!inside) check.reason = "DELAY_SKEW";
        check.message = "Pair delay skew " + std::to_string(delay_skew) +
                        " ps against " +
                        std::to_string(pair.delay_skew_tolerance_ps) + " ps.";
      }
    }

    const TuningPlan plan =
        plan_tuning(input, pair, geometry, delay_known, delay_skew);
    if (plan.attempted) {
      CheckResult& check = add_check(result, "tuning", pair.id, "NOT_CHECKED",
                                     "", "");
      check.measured_nm = plan.achieved_extra_nm;
      check.limit_nm = plan.required_extra_nm;
      check.object_refs = plan.refs;
      if (!plan.feasible) {
        check.status = "FAIL";
        check.reason = plan.reason;
        check.message = plan.message;
      } else if (plan.patches.empty()) {
        check.status = "SKIPPED";
        check.reason = "NO_TUNING_NEEDED";
        check.message = plan.message.empty()
                            ? "No tuning patch was required."
                            : plan.message;
      } else if (!plan.complete) {
        check.reason = "TUNING_NOT_CHECKED";
        check.message = plan.patches.front().note;
      } else {
        check.status = "PASS";
        check.message = "Tuning patch adds " +
                        std::to_string(plan.achieved_extra_nm) + " nm of " +
                        std::to_string(plan.required_extra_nm) +
                        " nm required inside a controlled region.";
      }
    }
    for (const TuningPatch& patch : plan.patches) {
      result.tuning.push_back(patch);
    }
    result.bundles.push_back(std::move(bundle));
  }

  result.search_nodes = nodes;
  result.budget_hit = budget_hit;

  for (const CheckResult& check : result.checks) {
    if (check.status == "PASS") {
      ++result.pass_count;
    } else if (check.status == "FAIL") {
      ++result.fail_count;
      result.violations.push_back(check.check_id);
    } else if (check.status == "NOT_CHECKED") {
      ++result.not_checked_count;
      result.not_checked.push_back(check.check_id);
    } else {
      ++result.skipped_count;
    }
  }

  if (input.channels.empty() && input.pairs.empty()) {
    result.diagnostics.push_back(
        "No channel or differential pair was declared; nothing was checked.");
  }
  if (budget_hit) {
    result.diagnostics.push_back(
        "Search budget was exhausted; affected checks are UNKNOWN and are "
        "never reported as PASS.");
  }
  if (!result.checks.empty()) {
    result.diagnostics.push_back(
        "Geometry, length and delay checks do not constitute a signal- or "
        "power-integrity sign-off; SI and PI remain NOT_CHECKED.");
  }

  if (result.fail_count > 0) {
    result.status = "FAILED";
  } else if (budget_hit) {
    result.status = "UNKNOWN";
  } else if (result.checks.empty()) {
    result.status = "NOT_CHECKED";
  } else if (result.not_checked_count > 0 || result.skipped_count > 0) {
    result.status = "PARTIAL";
  } else {
    result.status = "SUCCEEDED";
  }

  result.semantic_hash = sha256_hex(semantic_payload(input, result));
  return result;
}

}  // namespace highspeed_detail

HighspeedResult analyze_highspeed(const HighspeedInput& input) {
  return highspeed_detail::analyze_highspeed_impl(input);
}

}  // namespace mota
