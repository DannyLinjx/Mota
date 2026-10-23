#include "routing/global/corridor.hpp"

#include <algorithm>
#include <functional>
#include <limits>
#include <queue>
#include <vector>

std::vector<int> plan_corridor(const CoarseCorridorRequest& request) {
  if (request.columns <= 0 || request.rows <= 0) return {};
  const int count = request.columns * request.rows;
  if (request.start < 0 || request.goal < 0 || request.start >= count ||
      request.goal >= count) {
    return {};
  }
  if (request.blocked.size() != static_cast<size_t>(count)) return {};
  if (request.blocked[static_cast<size_t>(request.start)] ||
      request.blocked[static_cast<size_t>(request.goal)]) {
    return {};
  }
  if (request.start == request.goal) return {request.start};

  const int goal_x = request.goal % request.columns;
  const int goal_y = request.goal / request.columns;
  auto heuristic = [&](int index) {
    const int x = index % request.columns;
    const int y = index / request.columns;
    return std::abs(x - goal_x) + std::abs(y - goal_y);
  };

  std::vector<int> g(static_cast<size_t>(count), -1);
  std::vector<int> parent(static_cast<size_t>(count), -1);
  using Item = std::pair<int, int>;
  std::priority_queue<Item, std::vector<Item>, std::greater<Item>> queue;
  g[static_cast<size_t>(request.start)] = 0;
  queue.emplace(heuristic(request.start), request.start);
  const int dx[4] = {1, -1, 0, 0};
  const int dy[4] = {0, 0, 1, -1};
  while (!queue.empty()) {
    const auto [f, current] = queue.top();
    queue.pop();
    (void)f;
    if (current == request.goal) {
      std::vector<int> path;
      int cursor = current;
      while (cursor >= 0) {
        path.push_back(cursor);
        if (cursor == request.start) break;
        cursor = parent[static_cast<size_t>(cursor)];
      }
      std::reverse(path.begin(), path.end());
      return path;
    }
    const int x = current % request.columns;
    const int y = current / request.columns;
    for (int direction = 0; direction < 4; ++direction) {
      const int nx = x + dx[direction];
      const int ny = y + dy[direction];
      if (nx < 0 || ny < 0 || nx >= request.columns || ny >= request.rows) continue;
      const int next = ny * request.columns + nx;
      if (request.blocked[static_cast<size_t>(next)]) continue;
      const int candidate = g[static_cast<size_t>(current)] + 1;
      if (g[static_cast<size_t>(next)] >= 0 &&
          g[static_cast<size_t>(next)] <= candidate) {
        continue;
      }
      g[static_cast<size_t>(next)] = candidate;
      parent[static_cast<size_t>(next)] = current;
      queue.emplace(candidate + heuristic(next), next);
    }
  }
  return {};
}
