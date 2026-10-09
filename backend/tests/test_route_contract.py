"""Keep the pre-refactor public route surface intact."""
import json
from pathlib import Path
from app.bootstrap import app

def test_existing_routes_survive_module_extraction():
    expected = {tuple(item) for item in json.loads(Path(__file__).with_name("api_route_baseline.json").read_text(encoding="utf-8"))}
    paths = app.openapi()["paths"]
    actual = {(method.upper(), path) for path, operations in paths.items() for method in operations}
    assert expected <= actual
    assert "get" in paths["/api/knowledge-base/activity-logs"]
