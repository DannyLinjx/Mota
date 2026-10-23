#include "mota/highspeed_json.hpp"

#include <algorithm>
#include <cstdint>
#include <string>
#include <vector>

#include "mota/types.hpp"

namespace mota {
namespace {

[[noreturn]] void fail(const std::string& message) {
  throw KernelError(ErrorCode::InvalidInput, "highspeed: " + message);
}

std::string text(const Json& value, const std::string& key,
                 const std::string& fallback = "") {
  if (!value.has(key)) {
    if (fallback.empty()) fail("missing field " + key);
    return fallback;
  }
  const Json& item = value.at(key);
  if (!item.is_string()) fail("field " + key + " must be a string");
  return item.as_string();
}

// Optional string: an absent key yields the fallback even when it is empty.
std::string optional_text(const Json& value, const std::string& key,
                          const std::string& fallback = "") {
  if (!value.has(key)) return fallback;
  const Json& item = value.at(key);
  if (!item.is_string()) fail("field " + key + " must be a string");
  return item.as_string();
}

int64_t number(const Json& value, const std::string& key, int64_t fallback) {
  if (!value.has(key)) return fallback;
  const Json& item = value.at(key);
  if (!item.is_number()) fail("field " + key + " must be a number");
  return item.as_int();
}

double real(const Json& value, const std::string& key, double fallback) {
  if (!value.has(key)) return fallback;
  const Json& item = value.at(key);
  if (!item.is_number()) fail("field " + key + " must be a number");
  return item.as_double();
}

bool flag(const Json& value, const std::string& key, bool fallback) {
  if (!value.has(key)) return fallback;
  const Json& item = value.at(key);
  if (!item.is_bool()) fail("field " + key + " must be a boolean");
  return item.as_bool();
}

Vec2 point(const Json& value, const std::string& what) {
  if (!value.is_object() || !value.has("x") || !value.has("y")) {
    fail(what + " must be an object with x and y");
  }
  return Vec2{number(value, "x", 0), number(value, "y", 0)};
}

std::vector<Vec2> points(const Json& value, const std::string& what) {
  if (!value.is_array()) fail(what + " must be an array of points");
  std::vector<Vec2> out;
  for (const Json& item : value.items()) out.push_back(point(item, what));
  return out;
}

class LayerLookup {
 public:
  explicit LayerLookup(const std::vector<std::string>& layers)
      : layers_(layers) {}

  int index(const std::string& name, const std::string& what) const {
    const auto found =
        std::find(layers_.begin(), layers_.end(), name);
    if (found == layers_.end()) {
      fail(what + " references unknown layer " + name);
    }
    return static_cast<int>(std::distance(layers_.begin(), found));
  }

 private:
  const std::vector<std::string>& layers_;
};

ChannelNode parse_node(const Json& value) {
  ChannelNode node;
  node.id = text(value, "id");
  node.kind = text(value, "kind", "receiver");
  node.net = text(value, "net");
  node.component_id = optional_text(value, "component_id");
  node.pin = optional_text(value, "pin");
  if (value.has("x_nm") || value.has("y_nm")) {
    node.position = Vec2{number(value, "x_nm", 0), number(value, "y_nm", 0)};
  }
  node.layer_id = optional_text(value, "layer");
  return node;
}

}  // namespace

HighspeedInput parse_highspeed_json(const Json& value,
                                    const std::vector<std::string>& layer_ids) {
  if (!value.is_object()) fail("request must be an object");
  const LayerLookup layers(layer_ids);
  HighspeedInput out;

  if (value.has("channels")) {
    for (const Json& item : value.at("channels").items()) {
      Channel channel;
      channel.id = text(item, "id");
      channel.net = optional_text(item, "net");
      channel.driver = text(item, "driver");
      if (item.has("receivers")) {
        for (const Json& entry : item.at("receivers").items()) {
          channel.receivers.push_back(entry.as_string());
        }
      }
      if (item.has("series")) {
        for (const Json& entry : item.at("series").items()) {
          channel.series.push_back(entry.as_string());
        }
      }
      if (item.has("branches")) {
        for (const Json& entry : item.at("branches").items()) {
          channel.branches.push_back(entry.as_string());
        }
      }
      if (item.has("nodes")) {
        for (const Json& entry : item.at("nodes").items()) {
          ChannelNode node = parse_node(entry);
          if (!node.layer_id.empty()) {
            node.stackup_layer = layers.index(
                node.layer_id, "channel " + channel.id + " node " + node.id);
          }
          channel.nodes.push_back(std::move(node));
        }
      }
      if (item.has("links")) {
        for (const Json& entry : item.at("links").items()) {
          ChannelLink link;
          link.id = text(entry, "id");
          link.from = text(entry, "from");
          link.to = text(entry, "to");
          link.kind = text(entry, "kind", "copper");
          link.internal_length_nm = number(entry, "internal_length_nm", 0);
          channel.links.push_back(std::move(link));
        }
      }
      channel.length_tolerance_nm = number(item, "length_tolerance_nm", 0);
      channel.target_delay_ps = real(item, "target_delay_ps", 0.0);
      channel.delay_tolerance_ps = real(item, "delay_tolerance_ps", 0.0);
      out.channels.push_back(std::move(channel));
    }
  }

  if (value.has("pairs")) {
    for (const Json& item : value.at("pairs").items()) {
      DiffPair pair;
      pair.id = text(item, "id");
      pair.net_p = text(item, "net_p");
      pair.net_n = text(item, "net_n");
      pair.gap_nm = number(item, "gap_nm", 0);
      pair.gap_tolerance_nm = number(item, "gap_tolerance_nm", 0);
      pair.skew_tolerance_nm = number(item, "skew_tolerance_nm", 0);
      pair.max_uncoupled_nm = number(item, "max_uncoupled_nm", 0);
      pair.delay_skew_tolerance_ps =
          real(item, "delay_skew_tolerance_ps", 0.0);
      pair.pair_layer_transitions =
          flag(item, "pair_layer_transitions", true);
      out.pairs.push_back(std::move(pair));
    }
  }

  if (value.has("tracks")) {
    for (const Json& item : value.at("tracks").items()) {
      HighspeedTrack track;
      track.id = text(item, "id");
      track.net = text(item, "net");
      track.layer_id = text(item, "layer");
      track.stackup_layer = layers.index(track.layer_id, "track " + track.id);
      track.width_nm = number(item, "width_nm", 0);
      track.points = points(item.at("points"), "track " + track.id + " points");
      out.tracks.push_back(std::move(track));
    }
  }

  if (value.has("vias")) {
    for (const Json& item : value.at("vias").items()) {
      HighspeedVia via;
      via.id = text(item, "id");
      via.net = text(item, "net");
      via.center = Vec2{number(item, "x_nm", 0), number(item, "y_nm", 0)};
      via.start_layer_id = optional_text(item, "start_layer");
      via.end_layer_id = optional_text(item, "end_layer");
      if (!via.start_layer_id.empty()) {
        via.start_layer = layers.index(via.start_layer_id, "via " + via.id);
      }
      if (!via.end_layer_id.empty()) {
        via.end_layer = layers.index(via.end_layer_id, "via " + via.id);
      }
      via.drill_nm = number(item, "drill_nm", 0);
      via.stub_nm = number(item, "stub_nm", 0);
      via.delay_ps = real(item, "delay_ps", 0.0);
      via.has_delay = flag(item, "has_delay", false);
      out.vias.push_back(std::move(via));
    }
  }

  if (value.has("layer_models")) {
    for (const Json& item : value.at("layer_models").items()) {
      LayerDelayModel model;
      model.layer_id = text(item, "layer");
      model.stackup_layer = layers.index(model.layer_id, "layer model");
      model.thickness_nm = number(item, "thickness_nm", 0);
      model.reference_layer_id = optional_text(item, "reference_layer");
      model.propagation_ps_per_mm =
          real(item, "propagation_ps_per_mm", 0.0);
      model.source = optional_text(item, "source");
      out.layer_models.push_back(std::move(model));
    }
  }

  if (value.has("reference_planes")) {
    for (const Json& item : value.at("reference_planes").items()) {
      ReferencePlane plane;
      plane.layer_id = text(item, "layer");
      plane.stackup_layer = layers.index(plane.layer_id, "reference plane");
      if (item.has("polygons")) {
        for (const Json& polygon : item.at("polygons").items()) {
          plane.polygons.push_back(
              points(polygon, "reference plane " + plane.layer_id + " polygon"));
        }
      }
      out.reference_planes.push_back(std::move(plane));
    }
  }

  if (value.has("package_models")) {
    for (const Json& item : value.at("package_models").items()) {
      PackageModel model;
      model.component_id = text(item, "component_id");
      model.pin = optional_text(item, "pin");
      model.length_nm = number(item, "length_nm", 0);
      model.delay_ps = real(item, "delay_ps", 0.0);
      model.present = flag(item, "present", true);
      model.source = optional_text(item, "source");
      out.package_models.push_back(std::move(model));
    }
  }

  if (value.has("tuning_regions")) {
    for (const Json& item : value.at("tuning_regions").items()) {
      TuningRegion region;
      region.id = text(item, "id");
      region.net = text(item, "net");
      region.layer_id = optional_text(item, "layer");
      if (!region.layer_id.empty()) {
        region.stackup_layer = layers.index(region.layer_id, "tuning region");
      }
      region.start = point(item.at("start"), "tuning region start");
      region.end = point(item.at("end"), "tuning region end");
      region.width_nm = number(item, "width_nm", 0);
      region.max_extra_nm = number(item, "max_extra_nm", 0);
      out.tuning_regions.push_back(std::move(region));
    }
  }

  if (value.has("rules")) {
    const Json& rules = value.at("rules");
    out.rules.sample_step_nm =
        number(rules, "sample_step_nm", out.rules.sample_step_nm);
    out.rules.terminal_snap_nm =
        number(rules, "terminal_snap_nm", out.rules.terminal_snap_nm);
    out.rules.max_pair_samples =
        number(rules, "max_pair_samples", out.rules.max_pair_samples);
    out.rules.tuning_amplitude_nm =
        number(rules, "tuning_amplitude_nm", out.rules.tuning_amplitude_nm);
    out.rules.tuning_min_pitch_nm =
        number(rules, "tuning_min_pitch_nm", out.rules.tuning_min_pitch_nm);
    out.rules.tuning_coupling_margin_nm = number(
        rules, "tuning_coupling_margin_nm", out.rules.tuning_coupling_margin_nm);
    out.rules.max_search_nodes =
        number(rules, "max_search_nodes", out.rules.max_search_nodes);
    out.rules.seed = number(rules, "seed", out.rules.seed);
  }
  return out;
}

}  // namespace mota
