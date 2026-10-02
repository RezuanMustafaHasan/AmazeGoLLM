import hashlib
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

Cell = tuple[StrictInt, StrictInt]


class Arrow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: StrictInt = Field(gt=0)
    path: list[Cell] = Field(min_length=2, max_length=4096)

    @property
    def direction(self) -> Cell:
        return (self.path[-1][0] - self.path[-2][0], self.path[-1][1] - self.path[-2][1])


class Level(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    number: StrictInt = Field(ge=1)
    name: str = Field(min_length=1, max_length=80)
    difficulty: Literal["easy", "medium", "hard", "expert"]
    shape: str = Field(default="rectangle", max_length=40)
    lives: StrictInt = Field(default=3, ge=1, le=10)
    matrix: list[list[StrictInt]] = Field(min_length=2, max_length=100)
    arrows: list[Arrow] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_board(self):
        width = len(self.matrix[0])
        if not 2 <= width <= 100 or any(len(row) != width for row in self.matrix):
            raise ValueError("matrix must be rectangular, with 2–100 rows and columns")
        if any(cell < -1 for row in self.matrix for cell in row):
            raise ValueError("matrix cells must be -1 (outside), 0 (empty), or a positive arrow ID")
        occupied: dict[Cell, int] = {}
        ids: set[int] = set()
        for arrow in self.arrows:
            if arrow.id in ids:
                raise ValueError(f"duplicate arrow ID: {arrow.id}")
            ids.add(arrow.id)
            if len(set(arrow.path)) != len(arrow.path):
                raise ValueError(f"arrow {arrow.id} crosses itself")
            for i, (row, col) in enumerate(arrow.path):
                if not (0 <= row < self.height and 0 <= col < width):
                    raise ValueError(f"arrow {arrow.id} has a cell outside the matrix")
                if (row, col) in occupied:
                    raise ValueError(f"arrows overlap at ({row}, {col})")
                if self.matrix[row][col] != arrow.id:
                    raise ValueError(f"matrix does not match arrow {arrow.id} at ({row}, {col})")
                occupied[row, col] = arrow.id
                if (
                    i
                    and sum(abs(a - b) for a, b in zip(arrow.path[i - 1], (row, col), strict=True))
                    != 1
                ):
                    raise ValueError(f"arrow {arrow.id} path must use adjacent orthogonal cells")
        for r, row in enumerate(self.matrix):
            for c, value in enumerate(row):
                if value > 0 and occupied.get((r, c)) != value:
                    raise ValueError(f"matrix cell ({r}, {c}) has no matching arrow path")
        return self

    @property
    def fingerprint(self):
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    @property
    def height(self):
        return len(self.matrix)

    @property
    def width(self):
        return len(self.matrix[0])

    def summary(self):
        return {
            "id": self.id,
            "number": self.number,
            "name": self.name,
            "difficulty": self.difficulty,
            "shape": self.shape,
            "rows": self.height,
            "columns": self.width,
            "arrow_count": len(self.arrows),
        }


class CreateSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    actor_type: Literal["human", "agent"] = "human"
    actor_name: str | None = Field(default=None, max_length=80)


class TapAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arrow_id: StrictInt = Field(gt=0)
    expected_revision: StrictInt = Field(ge=0)
    action_id: UUID


class HintAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=0)
    action_id: UUID


class QueuedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["tap", "hint"] = "tap"
    arrow_id: StrictInt | None = Field(default=None, gt=0)
    action_id: UUID

    @model_validator(mode="after")
    def validate_target(self):
        if (self.type == "tap") != (self.arrow_id is not None):
            raise ValueError("tap requires arrow_id; hint must omit arrow_id")
        return self


class BatchActions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=0)
    actions: list[QueuedAction] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_actions(self):
        if len({action.action_id for action in self.actions}) != len(self.actions):
            raise ValueError("Each action in a batch must have a unique action_id")
        return self
