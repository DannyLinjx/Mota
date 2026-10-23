#include "mota/board.hpp"

#include <algorithm>
#include <set>

namespace mota {

Stackup::Stackup(std::vector<LayerSpec> layers) : layers_(std::move(layers)) {
  validate();
}

void Stackup::validate() const {
  if (layers_.size() < 2) {
    throw KernelError(ErrorCode::ModelInvalid,
                      "stackup needs at least two copper layers");
  }
  std::set<std::string> ids;
  int previous_order = 0;
  int copper = 0;
  for (const LayerSpec& layer : layers_) {
    if (layer.id.empty()) {
      throw KernelError(ErrorCode::ModelInvalid, "stackup layer id is empty");
    }
    if (!ids.insert(layer.id).second) {
      throw KernelError(ErrorCode::ModelInvalid,
                        "duplicate stackup layer id " + layer.id);
    }
    if (layer.order <= previous_order) {
      throw KernelError(ErrorCode::ModelInvalid,
                        "stackup layers must be ordered by increasing order");
    }
    previous_order = layer.order;
    if (layer.kind != "copper" && layer.kind != "dielectric" &&
        layer.kind != "mask") {
      throw KernelError(ErrorCode::ModelInvalid,
                        "unknown stackup layer kind " + layer.kind);
    }
    if (layer.thickness_nm <= 0) {
      throw KernelError(ErrorCode::ModelInvalid,
                        "stackup layer " + layer.id + " has no thickness");
    }
    if (layer.kind == "copper") ++copper;
  }
  if (copper < 2) {
    throw KernelError(ErrorCode::ModelInvalid,
                      "stackup needs at least two copper layers");
  }
}

int Stackup::index_of(const std::string& id) const {
  for (size_t i = 0; i < layers_.size(); ++i) {
    if (layers_[i].id == id) return static_cast<int>(i);
  }
  return -1;
}

int Stackup::copper_count() const {
  return static_cast<int>(std::count_if(layers_.begin(), layers_.end(),
                                        [](const LayerSpec& layer) {
                                          return layer.kind == "copper";
                                        }));
}

int Stackup::first_copper_index() const {
  for (size_t i = 0; i < layers_.size(); ++i) {
    if (layers_[i].kind == "copper") return static_cast<int>(i);
  }
  return -1;
}

int Stackup::last_copper_index() const {
  for (size_t i = layers_.size(); i-- > 0;) {
    if (layers_[i].kind == "copper") return static_cast<int>(i);
  }
  return -1;
}

std::vector<int> Stackup::copper_indices() const {
  std::vector<int> out;
  for (size_t i = 0; i < layers_.size(); ++i) {
    if (layers_[i].kind == "copper") out.push_back(static_cast<int>(i));
  }
  return out;
}

bool Stackup::is_copper(int index) const {
  return index >= 0 && index < static_cast<int>(layers_.size()) &&
         layers_[static_cast<size_t>(index)].kind == "copper";
}

int64_t Stackup::total_thickness_nm() const {
  int64_t total = 0;
  for (const LayerSpec& layer : layers_) {
    total = checked_add(total, layer.thickness_nm);
  }
  return total;
}

const char* via_kind_name(ViaKind kind) {
  switch (kind) {
    case ViaKind::Through: return "through";
    case ViaKind::Blind: return "blind";
    case ViaKind::Buried: return "buried";
    case ViaKind::Micro: return "micro";
  }
  return "unknown";
}

ViaKind parse_via_kind(const std::string& text) {
  if (text == "through") return ViaKind::Through;
  if (text == "blind") return ViaKind::Blind;
  if (text == "buried") return ViaKind::Buried;
  if (text == "micro") return ViaKind::Micro;
  throw KernelError(ErrorCode::InvalidInput, "unknown via kind " + text);
}

bool ViaDefinition::spans(int layer_index) const {
  return start_index >= 0 && layer_index >= start_index &&
         layer_index <= end_index;
}

void ViaDefinition::resolve(const Stackup& stackup) {
  if (id.empty()) {
    throw KernelError(ErrorCode::ModelInvalid, "via id is empty");
  }
  start_index = stackup.index_of(start_layer);
  end_index = stackup.index_of(end_layer);
  if (start_index < 0 || end_index < 0) {
    throw KernelError(ErrorCode::ModelInvalid,
                      "via " + id + " references an unknown layer");
  }
  if (!stackup.is_copper(start_index) || !stackup.is_copper(end_index)) {
    throw KernelError(ErrorCode::ModelInvalid,
                      "via " + id + " must start and end on copper");
  }
  if (start_index >= end_index) {
    throw KernelError(ErrorCode::ModelInvalid,
                      "via " + id + " has an empty or inverted layer span");
  }
  const int first = stackup.first_copper_index();
  const int last = stackup.last_copper_index();
  const bool starts_outer = start_index == first;
  const bool ends_outer = end_index == last;
  switch (kind) {
    case ViaKind::Through:
      if (!starts_outer || !ends_outer) {
        throw KernelError(ErrorCode::ModelInvalid,
                          "through via " + id + " must span all copper layers");
      }
      break;
    case ViaKind::Blind:
      if (starts_outer == ends_outer) {
        throw KernelError(ErrorCode::ModelInvalid,
                          "blind via " + id + " needs one outer layer");
      }
      break;
    case ViaKind::Buried:
      if (starts_outer || ends_outer) {
        throw KernelError(ErrorCode::ModelInvalid,
                          "buried via " + id + " must stay on inner layers");
      }
      break;
    case ViaKind::Micro: {
      const std::vector<int> copper = stackup.copper_indices();
      const auto start_position =
          std::find(copper.begin(), copper.end(), start_index);
      const auto end_position =
          std::find(copper.begin(), copper.end(), end_index);
      if (start_position == copper.end() || end_position == copper.end() ||
          end_position - start_position != 1) {
        throw KernelError(ErrorCode::ModelInvalid,
                          "micro via " + id + " must span adjacent copper");
      }
      break;
    }
  }
  validate();
}

void ViaDefinition::validate() const {
  if (drill_nm <= 0) {
    throw KernelError(ErrorCode::ModelInvalid,
                      "via " + id + " has no drill");
  }
  if (pad.kind != ShapeKind::Circle && pad.kind != ShapeKind::RoundRect &&
      pad.kind != ShapeKind::Slot) {
    throw KernelError(ErrorCode::ModelInvalid,
                      "via " + id + " pad must be a circle, rounded rect or slot");
  }
}

const Shape* PadStack::copper_on(const std::string& layer) const {
  const auto it = copper_by_layer.find(layer);
  return it == copper_by_layer.end() ? nullptr : &it->second;
}

void PadStack::validate(const Stackup& stackup) const {
  if (id.empty()) {
    throw KernelError(ErrorCode::ModelInvalid, "padstack id is empty");
  }
  for (const auto& entry : copper_by_layer) {
    if (!stackup.is_copper(stackup.index_of(entry.first))) {
      throw KernelError(ErrorCode::ModelInvalid,
                        "padstack " + id + " has copper on non-copper layer " +
                            entry.first);
    }
    shape_bounds(entry.second);
  }
  if (plated && copper_by_layer.empty()) {
    throw KernelError(ErrorCode::ModelInvalid,
                      "plated padstack " + id + " has no copper shape");
  }
  if (drill.has_value()) {
    if (drill->kind != ShapeKind::Circle && drill->kind != ShapeKind::Slot) {
      throw KernelError(ErrorCode::ModelInvalid,
                        "padstack " + id + " drill must be a circle or slot");
    }
  }
}

void BoardIndex::add(const std::string& layer, const std::string& id,
                     Shape shape) {
  objects_.push_back(BoardObject{id, layer, std::move(shape)});
  built_ = false;
}

void BoardIndex::build(const Stackup& stackup) {
  stackup.validate();
  by_layer_.clear();
  trees_.clear();
  for (size_t i = 0; i < objects_.size(); ++i) {
    const BoardObject& object = objects_[i];
    const int layer_index = stackup.index_of(object.layer);
    if (layer_index < 0 || !stackup.is_copper(layer_index)) {
      throw KernelError(ErrorCode::ModelInvalid,
                        "object " + object.id + " is on non-copper layer " +
                            object.layer);
    }
    by_layer_[object.layer].push_back(i);
  }
  for (auto& entry : by_layer_) {
    std::vector<Aabb> boxes;
    boxes.reserve(entry.second.size());
    for (size_t index : entry.second) {
      boxes.push_back(shape_bounds(objects_[index].shape).bounds);
    }
    RTree tree;
    tree.build(std::move(boxes));
    trees_.emplace(entry.first, std::move(tree));
  }
  built_ = true;
}

std::vector<std::string> BoardIndex::query(const std::string& layer,
                                           const Aabb& area) const {
  if (!built_) {
    throw KernelError(ErrorCode::Internal, "BoardIndex::query before build");
  }
  const auto tree = trees_.find(layer);
  if (tree == trees_.end()) {
    const auto known = by_layer_.find(layer);
    if (known == by_layer_.end()) {
      throw KernelError(ErrorCode::InvalidInput,
                        "unknown index layer " + layer);
    }
    return {};
  }
  std::vector<size_t> hits;
  tree->second.query(area, hits);
  std::vector<std::string> ids;
  ids.reserve(hits.size());
  std::vector<size_t> indices;
  indices.reserve(hits.size());
  for (size_t local : hits) indices.push_back(by_layer_.at(layer)[local]);
  std::sort(indices.begin(), indices.end());
  for (size_t index : indices) ids.push_back(objects_[index].id);
  return ids;
}

size_t BoardIndex::memory_bytes() const {
  size_t total = objects_.capacity() * sizeof(BoardObject);
  for (const BoardObject& object : objects_) {
    total += object.id.capacity() + object.layer.capacity();
    total += object.shape.points.capacity() * sizeof(Vec2);
  }
  for (const auto& entry : by_layer_) {
    total += entry.second.capacity() * sizeof(size_t);
  }
  for (const auto& entry : trees_) {
    total += entry.second.stats().memory_bytes;
  }
  return total;
}

std::vector<LayerIndexStats> BoardIndex::layer_stats() const {
  std::vector<LayerIndexStats> out;
  for (const auto& entry : trees_) {
    const RTreeStats stats = entry.second.stats();
    out.push_back(LayerIndexStats{entry.first, stats.objects, stats.nodes,
                                  stats.max_depth, stats.memory_bytes});
  }
  return out;
}

}  // namespace mota
