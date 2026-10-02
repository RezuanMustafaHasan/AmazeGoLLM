"""Generate, validate, or replace the Git-backed local level catalog."""

import argparse
import json
import random
import shutil
from pathlib import Path

from backend.app.catalog import load_levels
from backend.app.config import Settings
from backend.app.models import Level

DIRECTIONS = [(0, 1), (1, 0), (0, -1), (-1, 0)]
NAMES = [
    "A little untangling",
    "Room to breathe",
    "Around the bend",
    "Small beginnings",
    "The winding way",
    "A gentle ripple",
    "Diamond in the rough",
    "Take a detour",
    "In full bloom",
    "Heart of the maze",
    "A new perspective",
    "Find your rhythm",
    "A quiet current",
    "The long way home",
    "Perfect symmetry",
    "Follow the thread",
    "Open a window",
    "Circles of thought",
    "One at a time",
    "A change of direction",
    "Crossing paths",
    "Between the lines",
    "Go with the flow",
    "A lovely tangle",
    "The bigger picture",
    "Soft edges",
    "Unfolding",
    "A little patience",
    "Hidden in plain sight",
    "A steady hand",
    "The turning point",
    "Wild at heart",
    "A thousand corners",
    "Mind the gap",
    "A quiet escape",
    "The butterfly effect",
    "Head in the clouds",
    "The art of letting go",
    "Keep looking",
    "A tangled garden",
    "Thread the needle",
    "All roads lead out",
    "Into the blue",
    "The final stretch",
    "A beautiful mess",
    "In your own time",
    "Almost there",
    "Clear your mind",
]


def inside_shape(shape, r, c, rows, cols):
    y, x = 2 * r / (rows - 1) - 1, 2 * c / (cols - 1) - 1
    if shape == "diamond":
        return abs(x) + abs(y) <= 1.08
    if shape == "circle":
        return x * x + y * y <= 1.08
    if shape == "heart":
        # A broad pair of lobes tapers into a point below.
        return (
            (x - 0.42) ** 2 + (y + 0.38) ** 2 < 0.36
            or (x + 0.42) ** 2 + (y + 0.38) ** 2 < 0.36
            or (-0.3 <= y <= 0.96 and abs(x) < 0.8 * (1 - y))
        )
    if shape == "diagonal":
        return abs(x + y) < 0.64
    if shape == "butterfly":
        return abs(x) <= 0.95 and abs(y) < 0.32 + 0.72 * abs(x)
    return True


def generate_level(number: int, rows: int, cols: int, shape: str, seed: int) -> Level:
    rng = random.Random(seed)
    matrix = [
        [0 if inside_shape(shape, r, c, rows, cols) else -1 for c in range(cols)]
        for r in range(rows)
    ]
    cells = [(r, c) for r in range(rows) for c in range(cols) if matrix[r][c] == 0]
    arrows = []
    filled = 0
    for _ in range(len(cells) * 15):
        if filled >= len(cells) * 0.92:
            break
        start = rng.choice(cells)
        if matrix[start[0]][start[1]] != 0:
            continue
        path = [start]
        target = rng.randint(5, 15 if number < 13 else 23)
        for _ in range(target):
            r, c = path[-1]
            options = [
                (r + dr, c + dc)
                for dr, dc in DIRECTIONS
                if 0 <= r + dr < rows
                and 0 <= c + dc < cols
                and matrix[r + dr][c + dc] == 0
                and (r + dr, c + dc) not in path
            ]
            if not options:
                break
            if len(path) > 1 and rng.random() < 0.52:
                dr, dc = r - path[-2][0], c - path[-2][1]
                if (r + dr, c + dc) in options:
                    options = [(r + dr, c + dc)]
            path.append(rng.choice(options))
        while len(path) >= 2:
            r, c = path[-1]
            dr, dc = r - path[-2][0], c - path[-2][1]
            r, c = r + dr, c + dc
            clear = True
            while 0 <= r < rows and 0 <= c < cols:
                if matrix[r][c] > 0:
                    clear = False
                    break
                r, c = r + dr, c + dc
            if clear:
                break
            path.pop()
        if len(path) < 2:
            continue
        arrow_id = len(arrows) + 1
        arrows.append({"id": arrow_id, "path": path})
        for r, c in path:
            matrix[r][c] = arrow_id
        filled += len(path)
    difficulty = (
        "easy"
        if number <= 12
        else "medium"
        if number <= 24
        else ("hard" if number <= 36 else "expert")
    )
    return Level.model_validate(
        {
            "schema_version": 1,
            "id": f"level-{number:03d}",
            "number": number,
            "name": NAMES[(number - 1) % len(NAMES)],
            "difficulty": difficulty,
            "shape": shape,
            "lives": 3,
            "matrix": matrix,
            "arrows": arrows,
        }
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["generate", "validate", "replace"])
    parser.add_argument("--count", type=int, default=48)
    parser.add_argument(
        "--source", type=Path, help="Replacement JSON directory (only with replace)"
    )
    args = parser.parse_args()
    if args.source and args.command != "replace":
        parser.error("--source is only available with replace")
    settings = Settings()
    if args.command == "replace" and args.source:
        # Validate the entire source before deleting any local or remote data.
        levels = load_levels(args.source)
        if args.source.resolve() != settings.levels_dir.resolve():
            settings.levels_dir.mkdir(parents=True, exist_ok=True)
            for old_file in settings.levels_dir.glob("*.json"):
                old_file.unlink()
            for source_file in args.source.glob("*.json"):
                shutil.copy2(source_file, settings.levels_dir / source_file.name)
    if args.command == "generate":
        settings.levels_dir.mkdir(parents=True, exist_ok=True)
        (settings.levels_dir.parent / "level.schema.json").write_text(
            json.dumps(Level.model_json_schema(), indent=2) + "\n"
        )
        shapes = [
            "rectangle",
            "rectangle",
            "diamond",
            "circle",
            "heart",
            "diagonal",
            "rectangle",
            "butterfly",
        ]
        for n in range(1, args.count + 1):
            rows = 11 + ((n - 1) // 8) * 4 + ((n - 1) % 3) * 2
            cols = 13 + ((n - 1) // 8) * 3 + ((n - 1) % 4)
            shape = shapes[(n - 1) % len(shapes)]
            level = generate_level(n, rows, cols, shape, seed=2026 + n * 31)
            output = settings.levels_dir / f"{level.id}.json"
            output.write_text(json.dumps(level.model_dump(mode="json"), indent=2) + "\n")
    levels = load_levels(settings.levels_dir)
    print(f"Validated {len(levels)} levels: all matrices and paths agree, all are solvable.")
    if args.command in {"generate", "validate"}:
        for level in levels:
            print(
                f"  {level.id}: {level.height}×{level.width}, "
                f"{len(level.arrows)} arrows, {level.shape}, {level.difficulty}"
            )


if __name__ == "__main__":
    main()
