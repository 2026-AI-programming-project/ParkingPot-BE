"""Run from the project root: python -m scripts.export_openapi."""

import json
from pathlib import Path

from app.main import app

if __name__ == "__main__":
    path = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Exported {path}")
