// Packed R-tree over integer AABBs.
//
// Built by recursive median split on the widest centre axis (capacity 16).
// This is a Mota-owned replacement for Boost.Geometry in C6: Boost is not
// installed, and the query contract, memory accounting and tests are explicit
// here. Bulk build only; incremental updates arrive with the zone/DRC work.
#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <numeric>
#include <vector>

#include "mota/types.hpp"

namespace mota {

struct RTreeStats {
  size_t objects = 0;
  size_t nodes = 0;
  size_t leaves = 0;
  size_t max_depth = 0;
  size_t memory_bytes = 0;
  Aabb bounds{};
};

class RTree {
 public:
  static constexpr size_t kLeafCapacity = 16;

  void build(std::vector<Aabb> boxes) {
    boxes_ = std::move(boxes);
    nodes_.clear();
    order_.resize(boxes_.size());
    std::iota(order_.begin(), order_.end(), size_t{0});
    for (const Aabb& box : boxes_) {
      if (box.min_x > box.max_x || box.min_y > box.max_y) {
        throw KernelError(ErrorCode::InvalidInput, "inverted AABB");
      }
      validate_point(Vec2{box.min_x, box.min_y});
      validate_point(Vec2{box.max_x, box.max_y});
    }
    nodes_.reserve(boxes_.size() / 2 + 1);
    if (boxes_.empty()) return;
    root_ = build_node(0, boxes_.size(), 1);
  }

  void query(const Aabb& area, std::vector<size_t>& out) const {
    out.clear();
    if (nodes_.empty()) return;
    query_node(root_, area, out);
  }

  size_t size() const { return boxes_.size(); }

  const Aabb& box(size_t index) const { return boxes_.at(index); }

  RTreeStats stats() const {
    RTreeStats out;
    out.objects = boxes_.size();
    out.nodes = nodes_.size();
    out.memory_bytes = nodes_.capacity() * sizeof(Node) +
                       boxes_.capacity() * sizeof(Aabb) +
                       order_.capacity() * sizeof(size_t);
    size_t leaves = 0;
    size_t depth = 0;
    for (const Node& node : nodes_) {
      if (node.left < 0) ++leaves;
      depth = std::max(depth, static_cast<size_t>(node.depth));
      out.memory_bytes += node.items.capacity() * sizeof(size_t);
    }
    out.leaves = leaves;
    out.max_depth = depth;
    if (!nodes_.empty()) out.bounds = nodes_[root_].bounds;
    return out;
  }

 private:
  struct Node {
    Aabb bounds{};
    int left = -1;
    int right = -1;
    int depth = 0;
    std::vector<size_t> items;
  };

  int build_node(size_t begin, size_t end, int depth) {
    Node node;
    node.depth = depth;
    node.bounds = boxes_[order_[begin]];
    for (size_t i = begin + 1; i < end; ++i) {
      node.bounds = node.bounds.merged(boxes_[order_[i]]);
    }
    if (end - begin <= kLeafCapacity) {
      node.items.assign(order_.begin() + static_cast<long>(begin),
                        order_.begin() + static_cast<long>(end));
      nodes_.push_back(std::move(node));
      return static_cast<int>(nodes_.size() - 1);
    }
    const int64_t width = node.bounds.width();
    const int64_t height = node.bounds.height();
    const bool split_x = width >= height;
    const size_t middle = begin + (end - begin) / 2;
    std::nth_element(
        order_.begin() + static_cast<long>(begin),
        order_.begin() + static_cast<long>(middle),
        order_.begin() + static_cast<long>(end),
        [&](size_t lhs, size_t rhs) {
          const Aabb& a = boxes_[lhs];
          const Aabb& b = boxes_[rhs];
          return split_x ? a.center_x() < b.center_x()
                         : a.center_y() < b.center_y();
        });
    const int index = static_cast<int>(nodes_.size());
    nodes_.push_back(std::move(node));
    const int left = build_node(begin, middle, depth + 1);
    const int right = build_node(middle, end, depth + 1);
    nodes_[static_cast<size_t>(index)].left = left;
    nodes_[static_cast<size_t>(index)].right = right;
    return index;
  }

  void query_node(int index, const Aabb& area, std::vector<size_t>& out) const {
    const Node& node = nodes_[static_cast<size_t>(index)];
    if (!node.bounds.intersects(area)) return;
    if (node.left < 0) {
      for (size_t item : node.items) {
        if (boxes_[item].intersects(area)) out.push_back(item);
      }
      return;
    }
    query_node(node.left, area, out);
    query_node(node.right, area, out);
  }

  std::vector<Aabb> boxes_;
  std::vector<size_t> order_;
  std::vector<Node> nodes_;
  int root_ = -1;
};

}  // namespace mota
