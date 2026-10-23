#pragma once

#include <cstdint>
#include <vector>

struct CoarseCorridorRequest {
  int columns = 0;
  int rows = 0;
  int start = -1;
  int goal = -1;
  std::vector<uint8_t> blocked;  // 1 = blocked
};

// Deterministic 2D filler path used only as a soft routing corridor.
std::vector<int> plan_corridor(const CoarseCorridorRequest& request);
