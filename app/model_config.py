"""Safe loading and validation for the demo model catalog."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.schemas import Sensitivity, TaskType


class ModelDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1)
    tier: str = Field(pattern="^(small|medium|large)$")
    context_window: int = Field(gt=0)
    capabilities: set[TaskType]
    input_price_per_million: float = Field(ge=0)
    output_price_per_million: float = Field(ge=0)
    approved_for: set[Sensitivity]
    is_local: bool


class ModelCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[ModelDefinition] = Field(min_length=1)

    def model_by_id(self, model_id: str) -> ModelDefinition:
        return next(model for model in self.models if model.model_id == model_id)


def load_model_catalog(path: Path | None = None) -> ModelCatalog:
    """Parse YAML with safe_load and validate every configured model."""
    catalog_path = path or Path(__file__).resolve().parents[1] / "models.yaml"
    with catalog_path.open("r", encoding="utf-8") as source:
        raw_catalog = yaml.safe_load(source)
    catalog = ModelCatalog.model_validate(raw_catalog)
    model_ids = [model.model_id for model in catalog.models]
    if len(model_ids) != len(set(model_ids)):
        raise ValueError("Model identifiers must be unique")
    return catalog
