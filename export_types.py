from dataclasses import dataclass
from typing import Any


@dataclass
class BodyMeshSettings:
    size: float
    curvature: int


@dataclass
class ExportBody:
    fusion_body: Any
    group_name: str
    settings: BodyMeshSettings
    step_path: str
