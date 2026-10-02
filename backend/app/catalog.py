import json
from pathlib import Path

from backend.app.engine import solution
from backend.app.models import Level


def load_levels(directory: Path) -> list[Level]:
    levels = []
    for path in sorted(directory.glob("*.json")):
        data = path.read_text()
        if data.startswith("version https://git-lfs.github.com/spec/v1"):
            raise ValueError("Level files are Git LFS pointers. Run git lfs pull before starting.")
        levels.append(Level.model_validate(json.loads(data)))
    if not levels:
        raise ValueError(f"No levels found in {directory}. Run npm run levels:generate.")
    if len({level.id for level in levels}) != len(levels) or len(
        {level.number for level in levels}
    ) != len(levels):
        raise ValueError("Level IDs and level numbers must be unique")
    for level in levels:
        solution(level)
    return levels
