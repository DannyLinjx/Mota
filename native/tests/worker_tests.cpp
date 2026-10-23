#include "test_entry.hpp"

#include <cstdio>
#include <fstream>
#include <string>
#include <vector>

#include <unistd.h>

#include "mota/sha256.hpp"
#include "mota/types.hpp"
#include "mota/worker.hpp"
#include "test_common.hpp"

namespace {

using motatest::check;

constexpr int64_t kMm = 1'000'000;
int64_t mm(int64_t value) { return value * kMm; }

mota::Json point_json(int64_t x, int64_t y) {
  mota::Json value = mota::Json::object();
  value["kind"] = mota::Json("point");
  mota::Json point = mota::Json::object();
  point["x"] = mota::Json(x);
  point["y"] = mota::Json(y);
  value["p"] = point;
  return value;
}

mota::Json segment_json(int64_t ax, int64_t ay, int64_t bx, int64_t by) {
  mota::Json value = mota::Json::object();
  value["kind"] = mota::Json("segment");
  mota::Json a = mota::Json::object();
  a["x"] = mota::Json(ax);
  a["y"] = mota::Json(ay);
  mota::Json b = mota::Json::object();
  b["x"] = mota::Json(bx);
  b["y"] = mota::Json(by);
  value["a"] = a;
  value["b"] = b;
  return value;
}

mota::Json distance_query(const std::string& id, int64_t px, int64_t py) {
  mota::Json query = mota::Json::object();
  query["id"] = mota::Json(id);
  query["kind"] = mota::Json("distance");
  query["a"] = point_json(px, py);
  query["b"] = segment_json(0, -mm(1), 0, mm(1));
  return query;
}

mota::Json simple_query(const std::string& id, const std::string& kind) {
  mota::Json query = mota::Json::object();
  query["id"] = mota::Json(id);
  query["kind"] = mota::Json(kind);
  return query;
}

std::string sample_input() {
  mota::Json document = mota::Json::object();
  document["schema_version"] = mota::Json(static_cast<int64_t>(2));
  document["input_id"] = mota::Json("c6-worker-fixture");

  mota::Json board = mota::Json::object();
  board["width_nm"] = mota::Json(mm(40));
  board["height_nm"] = mota::Json(mm(30));
  board["thickness_nm"] = mota::Json(static_cast<int64_t>(1'600'000));

  mota::Json layers = mota::Json::array();
  auto add_layer = [&](const std::string& id, int order, const std::string& kind,
                       int64_t thickness, const std::string& material) {
    mota::Json layer = mota::Json::object();
    layer["id"] = mota::Json(id);
    layer["order"] = mota::Json(static_cast<int64_t>(order));
    layer["kind"] = mota::Json(kind);
    layer["thickness_nm"] = mota::Json(thickness);
    layer["material_ref"] = mota::Json(material);
    layers.push_back(layer);
  };
  add_layer("F.Cu", 1, "copper", 35'000, "copper");
  add_layer("D1", 2, "dielectric", 1'530'000, "FR4");
  add_layer("B.Cu", 3, "copper", 35'000, "copper");
  mota::Json stackup = mota::Json::object();
  stackup["layers"] = layers;
  board["stackup"] = stackup;

  mota::Json objects = mota::Json::array();
  mota::Json track = mota::Json::object();
  track["id"] = mota::Json("track_f");
  track["layer"] = mota::Json("F.Cu");
  track["shape"] = segment_json(0, 0, mm(10), 0);
  objects.push_back(track);
  mota::Json pad_object = mota::Json::object();
  pad_object["id"] = mota::Json("pad_f");
  pad_object["layer"] = mota::Json("F.Cu");
  mota::Json circle = mota::Json::object();
  circle["kind"] = mota::Json("circle");
  mota::Json center = mota::Json::object();
  center["x"] = mota::Json(mm(20));
  center["y"] = mota::Json(0);
  circle["center"] = center;
  circle["radius"] = mota::Json(mm(1));
  pad_object["shape"] = circle;
  objects.push_back(pad_object);
  board["objects"] = objects;

  mota::Json pads = mota::Json::array();
  mota::Json pad = mota::Json::object();
  pad["id"] = mota::Json("U1:1");
  pad["plated"] = mota::Json(true);
  mota::Json copper = mota::Json::object();
  copper["F.Cu"] = circle;
  pad["copper_by_layer"] = copper;
  mota::Json drill = mota::Json::object();
  drill["kind"] = mota::Json("circle");
  drill["center"] = center;
  drill["radius"] = mota::Json(300'000);
  pad["drill"] = drill;
  pads.push_back(pad);
  board["pads"] = pads;

  mota::Json vias = mota::Json::array();
  mota::Json via = mota::Json::object();
  via["id"] = mota::Json("v1");
  via["kind"] = mota::Json("through");
  via["start_layer"] = mota::Json("F.Cu");
  via["end_layer"] = mota::Json("B.Cu");
  via["drill_nm"] = mota::Json(400'000);
  via["pad"] = circle;
  vias.push_back(via);
  board["vias"] = vias;
  document["board"] = board;

  mota::Json queries = mota::Json::array();
  queries.push_back(distance_query("q1", mm(5), 0));
  queries.push_back(simple_query("q2", "stackup"));
  queries.push_back(simple_query("q3", "index_stats"));
  document["queries"] = queries;
  return document.dump();
}

mota::Json run(const std::string& input, int64_t max_operations = 100,
               const std::string& cancel_file = "",
               const std::string& expected_sha = "") {
  mota::WorkerBudget budget;
  budget.wall_seconds = 5.0;
  budget.max_operations = max_operations;
  budget.cancel_file = cancel_file;
  return mota::run_job(input, expected_sha.empty() ? mota::sha256_hex(input)
                                                  : expected_sha,
                       budget);
}

bool has_diagnostic(const mota::Json& result, const std::string& code) {
  if (!result.has("diagnostics")) return false;
  for (const mota::Json& item : result.at("diagnostics").items()) {
    if (item.at("code").as_string() == code) return true;
  }
  return false;
}

void test_sha256() {
  check(mota::sha256_hex("abc") ==
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        "sha256 abc vector");
  check(mota::sha256_hex("") ==
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "sha256 empty vector");
}

void test_success_and_freeze() {
  const std::string input = sample_input();
  const std::string sha = mota::sha256_hex(input);
  const mota::Json result = run(input);
  check(result.at("status").as_string() == "SUCCEEDED", "job succeeds");
  check(result.at("completed").as_int() == 3, "all queries complete");
  check(result.at("total").as_int() == 3, "frozen denominator is three");
  check(result.at("input_sha256").as_string() == sha, "result binds input hash");
  const mota::Json& first = result.at("results").items().front();
  check(first.at("distance_nm").as_int() == mm(5), "worker distance result");
  check(first.at("exact").is_bool(), "distance exactness is reported");

  const mota::Json stale = run(input, 100, "", "0000000000000000");
  check(stale.at("status").as_string() == "FAILED", "tampered hash fails");
  check(has_diagnostic(stale, "STALE_JOB_INPUT"), "stale input diagnostic");

  const mota::Json malformed = mota::run_job("{not json", "", mota::WorkerBudget{});
  check(malformed.at("status").as_string() == "FAILED", "malformed input fails");
  check(has_diagnostic(malformed, "INVALID_INPUT"), "malformed input diagnostic");
}

void test_budget_and_cancel() {
  const std::string input = sample_input();
  const mota::Json partial = run(input, 1);
  check(partial.at("status").as_string() == "PARTIAL", "budget stop is PARTIAL");
  check(partial.at("completed").as_int() == 1, "one query completed");
  check(partial.at("checkpoint").at("next_query_index").as_int() == 1,
        "checkpoint records the next query");
  check(has_diagnostic(partial, "BUDGET_EXHAUSTED"), "budget diagnostic");

  const mota::Json unknown = run(input, 0);
  check(unknown.at("status").as_string() == "UNKNOWN", "zero budget is UNKNOWN");
  check(unknown.at("status").as_string() != "INFEASIBLE",
        "budget stop is not INFEASIBLE");
  check(unknown.at("completed").as_int() == 0, "no query completed");
  check(has_diagnostic(unknown, "BUDGET_EXHAUSTED"), "UNKNOWN carries a reason");

  mota::WorkerBudget tiny_memory;
  tiny_memory.max_operations = 100;
  tiny_memory.max_memory_mb = 0.0001;
  const mota::Json memory = mota::run_job(input, mota::sha256_hex(input),
                                          tiny_memory);
  check(memory.at("status").as_string() == "UNKNOWN",
        "memory budget stop is UNKNOWN");
  check(has_diagnostic(memory, "BUDGET_EXHAUSTED"),
        "memory budget diagnostic");

  const std::string cancel_file =
      "/tmp/mota_c6_cancel_" + std::to_string(static_cast<long>(getpid())) + ".flag";
  {
    std::ofstream stream(cancel_file);
    stream << "cancel\n";
  }
  const mota::Json cancelled = run(input, 100, cancel_file);
  check(cancelled.at("status").as_string() == "CANCELLED",
        "cancel file yields CANCELLED");
  check(cancelled.at("results").items().empty(), "cancel stops before queries");
  check(has_diagnostic(cancelled, "CANCELLED"), "cancel diagnostic");
  std::remove(cancel_file.c_str());
}

void test_error_isolation() {
  mota::Json document = mota::Json::parse(sample_input());
  mota::Json queries = mota::Json::array();
  queries.push_back(distance_query("good1", mm(5), 0));
  mota::Json bad = mota::Json::object();
  bad["id"] = mota::Json("bad");
  bad["kind"] = mota::Json("distance");
  bad["a"] = point_json(mota::kMaxCoordinateNm + 1, 0);
  bad["b"] = segment_json(0, -mm(1), 0, mm(1));
  queries.push_back(bad);
  queries.push_back(distance_query("good2", mm(7), 0));
  document["queries"] = queries;
  const std::string input = document.dump();
  const mota::Json result = run(input);
  check(result.at("status").as_string() == "PARTIAL",
        "one bad query does not crash the worker");
  check(result.at("completed").as_int() == 2, "two good queries complete");
  check(result.at("failed").as_int() == 1, "one query is isolated as failed");
  const mota::Json& failed = result.at("results").items()[1];
  check(failed.at("status").as_string() == "FAILED", "bad query marked failed");
  check(failed.at("error").at("code").as_string() == "COORDINATE_OUT_OF_RANGE",
        "bad query has a typed code");
  check(has_diagnostic(result, "COORDINATE_OUT_OF_RANGE"),
        "job diagnostic names the code");
}

void test_rpc() {
  mota::Json info_request = mota::Json::object();
  info_request["jsonrpc"] = mota::Json("2.0");
  info_request["id"] = mota::Json("r0");
  info_request["method"] = mota::Json("kernel.info");
  const mota::Json info = mota::handle_rpc(info_request);
  check(info.at("result").at("version").as_string() == "0.1.0-c6",
        "kernel.info version");
  check(info.at("result").at("limits").at("max_frame_bytes").as_int() ==
            static_cast<int64_t>(mota::kMaxFrameBytes),
        "kernel.info frame limit");

  const std::string input = sample_input();
  const std::string path =
      "/tmp/mota_c6_input_" + std::to_string(static_cast<long>(getpid())) + ".json";
  {
    std::ofstream stream(path);
    stream << input;
  }
  mota::Json request = mota::Json::object();
  request["jsonrpc"] = mota::Json("2.0");
  request["id"] = mota::Json("r1");
  request["method"] = mota::Json("job.run");
  mota::Json params = mota::Json::object();
  params["input_path"] = mota::Json(path);
  params["input_sha256"] = mota::Json(mota::sha256_hex(input));
  mota::Json budget = mota::Json::object();
  budget["wall_seconds"] = mota::Json(5.0);
  budget["max_operations"] = mota::Json(static_cast<int64_t>(100));
  params["budget"] = budget;
  request["params"] = params;
  const mota::Json response = mota::handle_rpc(request);
  check(response.at("result").at("status").as_string() == "SUCCEEDED",
        "JSON-RPC job.run succeeds");
  std::remove(path.c_str());

  mota::Json missing = request;
  mota::Json missing_params = mota::Json::object();
  missing_params["input_path"] = mota::Json("/tmp/mota_c6_missing_input.json");
  missing_params["input_sha256"] = mota::Json("00");
  missing["params"] = missing_params;
  const mota::Json error = mota::handle_rpc(missing);
  check(error.has("error"), "missing artifact is a JSON-RPC error");
  check(error.at("error").at("data").at("code").as_string() == "INVALID_INPUT",
        "missing artifact error code");
}

}  // namespace

mota::Json worker_tests() {
  const int start_checks = motatest::checks;
  const int start_failures = motatest::failures;
  test_sha256();
  test_success_and_freeze();
  test_budget_and_cancel();
  test_error_isolation();
  test_rpc();
  mota::Json metrics = mota::Json::object();
  metrics["checks"] = mota::Json(
      static_cast<int64_t>(motatest::checks - start_checks));
  metrics["failures"] = mota::Json(
      static_cast<int64_t>(motatest::failures - start_failures));
  return metrics;
}
