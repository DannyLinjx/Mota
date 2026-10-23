// Minimal, strict JSON value used by the C6 worker protocol.
//
// Deliberately small: int64 is preserved, duplicate keys and trailing data are
// rejected, nesting is bounded, and strings reject raw control characters.
#pragma once

#include <cstddef>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace mota {

class JsonParser;

class Json {
 public:
  enum class Type { Null, Bool, Int, Double, String, Array, Object };

  Json() = default;
  Json(std::nullptr_t) {}
  explicit Json(bool value) : type_(Type::Bool), bool_(value) {}
  explicit Json(int value) : type_(Type::Int), int_(value) {}
  explicit Json(int64_t value) : type_(Type::Int), int_(value) {}
  explicit Json(double value) : type_(Type::Double), double_(value) {}
  explicit Json(const char* value)
      : type_(Type::String), string_(value == nullptr ? "" : value) {}
  explicit Json(std::string value)
      : type_(Type::String), string_(std::move(value)) {}

  static Json array() {
    Json value;
    value.type_ = Type::Array;
    return value;
  }
  static Json object() {
    Json value;
    value.type_ = Type::Object;
    return value;
  }

  Type type() const { return type_; }
  bool is_null() const { return type_ == Type::Null; }
  bool is_bool() const { return type_ == Type::Bool; }
  bool is_int() const { return type_ == Type::Int; }
  bool is_double() const { return type_ == Type::Double; }
  bool is_number() const { return is_int() || is_double(); }
  bool is_string() const { return type_ == Type::String; }
  bool is_array() const { return type_ == Type::Array; }
  bool is_object() const { return type_ == Type::Object; }

  bool as_bool() const;
  int64_t as_int() const;
  double as_double() const;
  const std::string& as_string() const;

  const std::vector<Json>& items() const;
  std::vector<Json>& items();
  const std::map<std::string, Json>& fields() const;
  std::map<std::string, Json>& fields();

  bool has(const std::string& key) const;
  const Json& at(const std::string& key) const;
  Json& operator[](const std::string& key);
  void push_back(Json value);

  std::string dump() const;
  static Json parse(const std::string& text, size_t max_depth = 64);

 private:
  friend class JsonParser;
  Type type_ = Type::Null;
  bool bool_ = false;
  int64_t int_ = 0;
  double double_ = 0.0;
  std::string string_;
  std::vector<Json> array_;
  std::map<std::string, Json> object_;
};

}  // namespace mota
