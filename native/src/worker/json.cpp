#include "mota/json.hpp"

#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <limits>
#include <sstream>

#include "mota/types.hpp"

namespace mota {

class JsonParser;

namespace {

[[noreturn]] void fail(const std::string& message, size_t offset) {
  throw KernelError(ErrorCode::InvalidInput,
                    "JSON error at byte " + std::to_string(offset) + ": " +
                        message);
}

void append_utf8(std::string& out, uint32_t codepoint) {
  if (codepoint <= 0x7F) {
    out.push_back(static_cast<char>(codepoint));
  } else if (codepoint <= 0x7FF) {
    out.push_back(static_cast<char>(0xC0 | (codepoint >> 6)));
    out.push_back(static_cast<char>(0x80 | (codepoint & 0x3F)));
  } else if (codepoint <= 0xFFFF) {
    out.push_back(static_cast<char>(0xE0 | (codepoint >> 12)));
    out.push_back(static_cast<char>(0x80 | ((codepoint >> 6) & 0x3F)));
    out.push_back(static_cast<char>(0x80 | (codepoint & 0x3F)));
  } else {
    out.push_back(static_cast<char>(0xF0 | (codepoint >> 18)));
    out.push_back(static_cast<char>(0x80 | ((codepoint >> 12) & 0x3F)));
    out.push_back(static_cast<char>(0x80 | ((codepoint >> 6) & 0x3F)));
    out.push_back(static_cast<char>(0x80 | (codepoint & 0x3F)));
  }
}

void dump_value(const Json& value, std::string& out) {
  switch (value.type()) {
    case Json::Type::Null:
      out += "null";
      return;
    case Json::Type::Bool:
      out += value.as_bool() ? "true" : "false";
      return;
    case Json::Type::Int:
      out += std::to_string(value.as_int());
      return;
    case Json::Type::Double: {
      const double number = value.as_double();
      if (!std::isfinite(number)) {
        throw KernelError(ErrorCode::Internal, "non-finite double in JSON");
      }
      std::ostringstream stream;
      stream << std::setprecision(12) << number;
      out += stream.str();
      return;
    }
    case Json::Type::String: {
      out.push_back('"');
      for (char raw : value.as_string()) {
        const unsigned char c = static_cast<unsigned char>(raw);
        switch (c) {
          case '"': out += "\\\""; break;
          case '\\': out += "\\\\"; break;
          case '\b': out += "\\b"; break;
          case '\f': out += "\\f"; break;
          case '\n': out += "\\n"; break;
          case '\r': out += "\\r"; break;
          case '\t': out += "\\t"; break;
          default:
            if (c < 0x20) {
              const char* hex = "0123456789abcdef";
              out += "\\u00";
              out.push_back(hex[(c >> 4) & 0xF]);
              out.push_back(hex[c & 0xF]);
            } else {
              out.push_back(raw);
            }
        }
      }
      out.push_back('"');
      return;
    }
    case Json::Type::Array: {
      out.push_back('[');
      bool first = true;
      for (const Json& item : value.items()) {
        if (!first) out.push_back(',');
        first = false;
        dump_value(item, out);
      }
      out.push_back(']');
      return;
    }
    case Json::Type::Object: {
      out.push_back('{');
      bool first = true;
      for (const auto& entry : value.fields()) {
        if (!first) out.push_back(',');
        first = false;
        Json key(entry.first);
        dump_value(key, out);
        out.push_back(':');
        dump_value(entry.second, out);
      }
      out.push_back('}');
      return;
    }
  }
}

}  // namespace

bool Json::as_bool() const {
  if (!is_bool()) throw KernelError(ErrorCode::InvalidInput, "expected bool");
  return bool_;
}

int64_t Json::as_int() const {
  if (!is_int()) throw KernelError(ErrorCode::InvalidInput, "expected integer");
  return int_;
}

double Json::as_double() const {
  if (is_int()) return static_cast<double>(int_);
  if (!is_double()) {
    throw KernelError(ErrorCode::InvalidInput, "expected number");
  }
  return double_;
}

const std::string& Json::as_string() const {
  if (!is_string()) throw KernelError(ErrorCode::InvalidInput, "expected string");
  return string_;
}

const std::vector<Json>& Json::items() const {
  if (!is_array()) throw KernelError(ErrorCode::InvalidInput, "expected array");
  return array_;
}

std::vector<Json>& Json::items() {
  if (!is_array()) throw KernelError(ErrorCode::InvalidInput, "expected array");
  return array_;
}

const std::map<std::string, Json>& Json::fields() const {
  if (!is_object()) throw KernelError(ErrorCode::InvalidInput, "expected object");
  return object_;
}

std::map<std::string, Json>& Json::fields() {
  if (!is_object()) throw KernelError(ErrorCode::InvalidInput, "expected object");
  return object_;
}

bool Json::has(const std::string& key) const {
  return is_object() && object_.count(key) != 0;
}

const Json& Json::at(const std::string& key) const {
  if (!is_object()) throw KernelError(ErrorCode::InvalidInput, "expected object");
  const auto it = object_.find(key);
  if (it == object_.end()) {
    throw KernelError(ErrorCode::InvalidInput, "missing field " + key);
  }
  return it->second;
}

Json& Json::operator[](const std::string& key) {
  if (type_ == Type::Null) type_ = Type::Object;
  if (!is_object()) throw KernelError(ErrorCode::InvalidInput, "expected object");
  return object_[key];
}

void Json::push_back(Json value) {
  if (!is_array()) throw KernelError(ErrorCode::InvalidInput, "expected array");
  array_.push_back(std::move(value));
}

std::string Json::dump() const {
  std::string out;
  dump_value(*this, out);
  return out;
}

class JsonParser {
 public:
  JsonParser(const std::string& text, size_t max_depth)
      : text_(text), max_depth_(max_depth) {}

  Json parse() {
    skip_space();
    Json value = parse_value(0);
    skip_space();
    if (position_ != text_.size()) fail("trailing data", position_);
    return value;
  }

 private:
  void skip_space() {
    while (position_ < text_.size()) {
      const char c = text_[position_];
      if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
        ++position_;
      } else {
        break;
      }
    }
  }

  char peek() const {
    return position_ < text_.size() ? text_[position_] : '\0';
  }

  bool consume(const char* literal) {
    const size_t length = std::char_traits<char>::length(literal);
    if (text_.compare(position_, length, literal) == 0) {
      position_ += length;
      return true;
    }
    return false;
  }

  Json parse_value(size_t depth) {
    if (depth > max_depth_) fail("nesting too deep", position_);
    skip_space();
    const char c = peek();
    if (c == '{') return parse_object(depth + 1);
    if (c == '[') return parse_array(depth + 1);
    if (c == '"') return Json(parse_string());
    if (c == 't') {
      if (!consume("true")) fail("invalid literal", position_);
      return Json(true);
    }
    if (c == 'f') {
      if (!consume("false")) fail("invalid literal", position_);
      return Json(false);
    }
    if (c == 'n') {
      if (!consume("null")) fail("invalid literal", position_);
      return Json(nullptr);
    }
    if (c == '-' || (c >= '0' && c <= '9')) return parse_number();
    fail("unexpected character", position_);
  }

  Json parse_object(size_t depth) {
    if (peek() != '{') fail("expected object", position_);
    ++position_;
    Json object = Json::object();
    skip_space();
    if (peek() == '}') {
      ++position_;
      return object;
    }
    while (true) {
      skip_space();
      if (peek() != '"') fail("object key must be a string", position_);
      const std::string key = parse_string();
      if (object.has(key)) fail("duplicate object key " + key, position_);
      skip_space();
      if (peek() != ':') fail("expected ':'", position_);
      ++position_;
      object[key] = parse_value(depth);
      skip_space();
      const char c = peek();
      if (c == ',') {
        ++position_;
        continue;
      }
      if (c == '}') {
        ++position_;
        return object;
      }
      fail("expected ',' or '}'", position_);
    }
  }

  Json parse_array(size_t depth) {
    if (peek() != '[') fail("expected array", position_);
    ++position_;
    Json array = Json::array();
    skip_space();
    if (peek() == ']') {
      ++position_;
      return array;
    }
    while (true) {
      array.push_back(parse_value(depth));
      skip_space();
      const char c = peek();
      if (c == ',') {
        ++position_;
        continue;
      }
      if (c == ']') {
        ++position_;
        return array;
      }
      fail("expected ',' or ']'", position_);
    }
  }

  std::string parse_string() {
    if (peek() != '"') fail("expected string", position_);
    ++position_;
    std::string out;
    while (position_ < text_.size()) {
      const unsigned char c = static_cast<unsigned char>(text_[position_++]);
      if (c == '"') return out;
      if (c < 0x20) fail("control character in string", position_ - 1);
      if (c != '\\') {
        out.push_back(static_cast<char>(c));
        continue;
      }
      if (position_ >= text_.size()) fail("truncated escape", position_);
      const char escape = text_[position_++];
      switch (escape) {
        case '"': out.push_back('"'); break;
        case '\\': out.push_back('\\'); break;
        case '/': out.push_back('/'); break;
        case 'b': out.push_back('\b'); break;
        case 'f': out.push_back('\f'); break;
        case 'n': out.push_back('\n'); break;
        case 'r': out.push_back('\r'); break;
        case 't': out.push_back('\t'); break;
        case 'u': out += parse_unicode_escape(); break;
        default: fail("unknown escape", position_ - 1);
      }
    }
    fail("unterminated string", position_);
  }

  std::string parse_unicode_escape() {
    uint32_t codepoint = read_hex4();
    if (codepoint >= 0xD800 && codepoint <= 0xDBFF) {
      if (!consume("\\u")) fail("missing low surrogate", position_);
      const uint32_t low = read_hex4();
      if (low < 0xDC00 || low > 0xDFFF) fail("invalid low surrogate", position_);
      codepoint = 0x10000 + ((codepoint - 0xD800) << 10) + (low - 0xDC00);
    } else if (codepoint >= 0xDC00 && codepoint <= 0xDFFF) {
      fail("unexpected low surrogate", position_);
    }
    std::string out;
    append_utf8(out, codepoint);
    return out;
  }

  uint32_t read_hex4() {
    if (position_ + 4 > text_.size()) fail("truncated \\u escape", position_);
    uint32_t value = 0;
    for (int i = 0; i < 4; ++i) {
      const char c = text_[position_++];
      value <<= 4;
      if (c >= '0' && c <= '9') value |= static_cast<uint32_t>(c - '0');
      else if (c >= 'a' && c <= 'f') value |= static_cast<uint32_t>(c - 'a' + 10);
      else if (c >= 'A' && c <= 'F') value |= static_cast<uint32_t>(c - 'A' + 10);
      else fail("invalid hex digit", position_ - 1);
    }
    return value;
  }

  Json parse_number() {
    const size_t begin = position_;
    if (peek() == '-') ++position_;
    if (peek() == '0') {
      ++position_;
    } else if (peek() >= '1' && peek() <= '9') {
      while (peek() >= '0' && peek() <= '9') ++position_;
    } else {
      fail("invalid number", position_);
    }
    bool is_double = false;
    if (peek() == '.') {
      is_double = true;
      ++position_;
      if (!(peek() >= '0' && peek() <= '9')) fail("invalid fraction", position_);
      while (peek() >= '0' && peek() <= '9') ++position_;
    }
    if (peek() == 'e' || peek() == 'E') {
      is_double = true;
      ++position_;
      if (peek() == '+' || peek() == '-') ++position_;
      if (!(peek() >= '0' && peek() <= '9')) fail("invalid exponent", position_);
      while (peek() >= '0' && peek() <= '9') ++position_;
    }
    const std::string token = text_.substr(begin, position_ - begin);
    if (!is_double) {
      errno = 0;
      char* end = nullptr;
      const long long value = std::strtoll(token.c_str(), &end, 10);
      if (errno == ERANGE || end == nullptr ||
          static_cast<size_t>(end - token.c_str()) != token.size()) {
        fail("integer out of range", begin);
      }
      return Json(static_cast<int64_t>(value));
    }
    char* end = nullptr;
    const double value = std::strtod(token.c_str(), &end);
    if (end == nullptr || static_cast<size_t>(end - token.c_str()) != token.size() ||
        !std::isfinite(value)) {
      fail("invalid number", begin);
    }
    return Json(value);
  }

  const std::string& text_;
  size_t max_depth_;
  size_t position_ = 0;
};

Json Json::parse(const std::string& text, size_t max_depth) {
  JsonParser parser(text, max_depth);
  return parser.parse();
}

}  // namespace mota
