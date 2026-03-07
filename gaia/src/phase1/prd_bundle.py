"""Structured PRD bundle models for reusable test generation."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from gaia.src.phase4.goal_driven.models import TestGoal


class PRDSource(BaseModel):
    type: str = Field(..., description="Source type: pdf/docx/md/txt/text/json")
    path: Optional[str] = Field(default=None, description="Original file path if available")
    content_hash: str = Field(..., description="Hash of ingested source text")
    title: Optional[str] = Field(default=None, description="Detected document title")


class PRDSection(BaseModel):
    title: str
    content: str
    order: int = 0


class PRDRequirement(BaseModel):
    id: str
    title: str
    description: str
    priority: str = "P1"
    category: str = "functional"
    source_section: Optional[str] = None


class PRDFlow(BaseModel):
    id: str
    title: str
    steps: List[str] = Field(default_factory=list)
    source_section: Optional[str] = None


class PRDNormalizedDocument(BaseModel):
    summary: str = ""
    sections: List[PRDSection] = Field(default_factory=list)
    requirements: List[PRDRequirement] = Field(default_factory=list)
    user_flows: List[PRDFlow] = Field(default_factory=list)
    kpis: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)


class PRDGoal(BaseModel):
    id: str
    title: str
    goal_text: str
    priority: str = "MUST"
    source_refs: List[str] = Field(default_factory=list)
    success_contract: str = "generic_feature_validation"
    keywords: List[str] = Field(default_factory=list)
    enabled: bool = True
    max_steps: int = 20
    test_data: Dict[str, Any] = Field(default_factory=dict)

    def to_test_goal(self, default_url: str | None = None) -> TestGoal:
        return TestGoal(
            id=self.id,
            name=self.title,
            description=self.goal_text,
            priority=self.priority,
            keywords=list(self.keywords),
            success_criteria=[self.goal_text],
            max_steps=max(1, int(self.max_steps)),
            start_url=default_url,
            test_data=dict(self.test_data),
        )


class PRDExecutionProfile(BaseModel):
    base_url: Optional[str] = None
    auth_mode: str = "user_or_saved"
    rail_scope: str = "smoke"
    runtime_hint: str = "gui"


class PRDMetadata(BaseModel):
    created_at: str
    updated_at: str
    generator: str = "gaia"
    generator_version: str = "v1"
    notes: List[str] = Field(default_factory=list)


class PRDBundle(BaseModel):
    schema_version: str = "gaia.prd_bundle.v1"
    project_name: str
    source: PRDSource
    normalized_prd: PRDNormalizedDocument
    generated_goals: List[PRDGoal] = Field(default_factory=list)
    execution_profile: PRDExecutionProfile = Field(default_factory=PRDExecutionProfile)
    metadata: PRDMetadata

    @property
    def suggested_filename(self) -> str:
        raw = (self.project_name or "prd-bundle").strip().lower().replace(" ", "-")
        safe = "".join(ch for ch in raw if ch.isalnum() or ch in {"-", "_"}).strip("-_")
        return f"{safe or 'prd-bundle'}.json"

    def goal_count(self) -> int:
        return len([goal for goal in self.generated_goals if goal.enabled])

    def base_url(self, override_url: str | None = None) -> str | None:
        return (override_url or self.execution_profile.base_url or "").strip() or None


def is_prd_bundle_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    version = str(payload.get("schema_version") or "").strip().lower()
    return version.startswith("gaia.prd_bundle.")


def bundle_output_path(root: Path | None = None, filename: str | None = None) -> Path:
    artifacts_root = root or (Path(__file__).resolve().parents[3] / "artifacts" / "prd_bundles")
    artifacts_root.mkdir(parents=True, exist_ok=True)
    return artifacts_root / (filename or "prd-bundle.json")
