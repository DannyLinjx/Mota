// Stackup, padstack, via span and per-layer object index.
#pragma once

#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "mota/rtree.hpp"
#include "mota/shapes.hpp"
#include "mota/types.hpp"

namespace mota {

struct LayerSpec {
  std::string id;
  int order = 0;
  std::string kind;  // copper | dielectric | mask
  int64_t thickness_nm = 0;
  std::string material_ref;
};

class Stackup {
 public:
  Stackup() = default;
  explicit Stackup(std::vector<LayerSpec> layers);

  const std::vector<LayerSpec>& layers() const { return layers_; }
  int index_of(const std::string& id) const;
  int copper_count() const;
  int first_copper_index() const;
  int last_copper_index() const;
  std::vector<int> copper_indices() const;
  bool is_copper(int index) const;
  int64_t total_thickness_nm() const;
  void validate() const;

 private:
  std::vector<LayerSpec> layers_;
};

enum class ViaKind { Through, Blind, Buried, Micro };

const char* via_kind_name(ViaKind kind);
ViaKind parse_via_kind(const std::string& text);

struct ViaDefinition {
  std::string id;
  std::string net;
  ViaKind kind = ViaKind::Through;
  std::string start_layer;
  std::string end_layer;
  int64_t drill_nm = 0;
  Shape pad;
  bool plated = true;
  bool locked = false;

  int start_index = -1;
  int end_index = -1;

  bool spans(int layer_index) const;
  bool electrically_connects() const { return plated && start_index >= 0; }
  void resolve(const Stackup& stackup);
  void validate() const;
};

struct PadStack {
  std::string id;
  std::string net;
  std::string component_id;
  std::string pin;
  bool plated = true;
  std::map<std::string, Shape> copper_by_layer;
  std::optional<Shape> drill;

  const Shape* copper_on(const std::string& layer) const;
  bool electrically_connects() const { return plated; }
  void validate(const Stackup& stackup) const;
};

struct TrackSpec {
  std::string id;
  std::string net;
  int layer = -1;
  std::vector<Vec2> points;
  int64_t width_nm = 0;
  bool locked = false;
};

struct KeepoutSpec {
  std::string id;
  int layer = -1;
  std::vector<Vec2> outline;
};

struct BoardObject {
  std::string id;
  std::string layer;
  Shape shape;
};

struct LayerIndexStats {
  std::string layer;
  size_t objects = 0;
  size_t nodes = 0;
  size_t max_depth = 0;
  size_t memory_bytes = 0;
};

class BoardIndex {
 public:
  void add(const std::string& layer, const std::string& id, Shape shape);
  void build(const Stackup& stackup);
  std::vector<std::string> query(const std::string& layer,
                                 const Aabb& area) const;
  size_t object_count() const { return objects_.size(); }
  size_t memory_bytes() const;
  std::vector<LayerIndexStats> layer_stats() const;
  const std::vector<BoardObject>& objects() const { return objects_; }

 private:
  std::vector<BoardObject> objects_;
  std::map<std::string, std::vector<size_t>> by_layer_;
  std::map<std::string, RTree> trees_;
  bool built_ = false;
};

}  // namespace mota
