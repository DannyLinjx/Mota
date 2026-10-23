// JSON binding for the C11 highspeed request.
//
// One parser serves both the kernel worker and the C++ fixtures so the request
// schema cannot drift between them.
#pragma once

#include <string>
#include <vector>

#include "mota/highspeed.hpp"
#include "mota/json.hpp"

namespace mota {

// `layer_ids` is the ordered copper layer list; every layer name in the
// request must resolve against it.
HighspeedInput parse_highspeed_json(const Json& value,
                                    const std::vector<std::string>& layer_ids);

}  // namespace mota
