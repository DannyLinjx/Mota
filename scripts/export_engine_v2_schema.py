#!/usr/bin/env python
"""Write the engine-v2 JSON Schema from the Pydantic contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mota.engine_v2.schema import EngineDocumentV2  # noqa: E402


def main() -> int:
    schema = EngineDocumentV2.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://mota.local/schemas/engine-v2.json"
    schema["title"] = "Mota Complex PCB Engine v2 document"
    target = ROOT / "schemas" / "engine-v2.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print(f"Wrote {target} ({len(schema.get('$defs', {}))} definitions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
