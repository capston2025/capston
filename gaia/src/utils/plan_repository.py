"""Helpers for loading and saving test plans and DOM snapshots."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, Iterable, List, Sequence
from urllib.parse import urlparse

from gaia.src.utils.models import DomElement, TestScenario


class PlanRepository:
    """Loads Phase 1 artifacts (plans + DOM snapshots) from disk."""

    def __init__(self, root_dir: Path | str | None = None) -> None:
        default_root = Path(__file__).resolve().parents[3] / "artifacts" / "mock_data"
        self._root = Path(root_dir) if root_dir else default_root
        self._plans: Dict[str, List[TestScenario]] = {}
        self._doms: Dict[str, List[DomElement]] = {}
        self._url_index: Dict[str, List[str]] = {}
        self._load_artifacts()

    # ------------------------------------------------------------------
    def plans_for_profile(self, profile: str) -> List[TestScenario]:
        return list(self._plans.get(profile, []))

    def dom_for_profile(self, profile: str) -> List[DomElement]:
        return list(self._doms.get(profile, []))

    def plans_for_url(self, url: str) -> List[TestScenario]:
        host = urlparse(url).netloc.lower()
        if not host:
            return []

        matched_profiles: List[str] = []
        for pattern, profiles in self._url_index.items():
            if pattern in host:
                matched_profiles.extend(profiles)

        for profile in matched_profiles:
            plans = self._plans.get(profile)
            if plans:
                return list(plans)
        return []

    def dom_for_url(self, url: str) -> List[DomElement]:
        host = urlparse(url).netloc.lower()
        if not host:
            return []

        matched_profiles: List[str] = []
        for pattern, profiles in self._url_index.items():
            if pattern in host:
                matched_profiles.extend(profiles)

        for profile in matched_profiles:
            dom_elements = self._doms.get(profile)
            if dom_elements:
                return list(dom_elements)
        return []

    # ------------------------------------------------------------------
    def _load_artifacts(self) -> None:
        if not self._root.exists():
            return

        # Load from mock_data (legacy)
        for path in self._root.glob("*_test_plan.json"):
            data = self._read_json(path)
            if not isinstance(data, dict):
                continue
            profile = str(data.get("profile") or path.stem.replace("_test_plan", "")).strip()
            scenarios_raw: Sequence[dict] = data.get("test_scenarios", [])  # type: ignore[arg-type]
            scenarios: List[TestScenario] = []
            for raw in scenarios_raw:
                try:
                    scenarios.append(TestScenario.model_validate(raw))
                except Exception:
                    continue
            if scenarios:
                self._plans[profile] = scenarios

        for path in self._root.glob("*_dom.json"):
            data = self._read_json(path)
            if not isinstance(data, dict):
                continue
            profile = str(data.get("profile") or path.stem.replace("_dom", "")).strip()
            elements_raw: Sequence[dict] = data.get("elements", [])  # type: ignore[arg-type]
            elements: List[DomElement] = []
            for raw in elements_raw:
                try:
                    elements.append(DomElement.model_validate(raw))
                except Exception:
                    continue
            if elements:
                self._doms[profile] = elements

            patterns: Iterable[str] = data.get("url_patterns", [])  # type: ignore[arg-type]
            for pattern in patterns:
                key = str(pattern).lower().strip()
                if not key:
                    continue
                self._url_index.setdefault(key, []).append(profile)

        # Load from plans directory (cached analysis results)
        plans_dir = self._root.parent / "plans"
        if plans_dir.exists():
            for path in plans_dir.glob("*_plan.json"):
                data = self._read_json(path)
                if not isinstance(data, dict):
                    continue

                # Extract profile and URL
                profile = str(data.get("profile", "")).strip()
                url = str(data.get("url", "")).strip()

                if not profile:
                    continue

                # Load scenarios
                scenarios_raw: Sequence[dict] = data.get("test_scenarios", [])  # type: ignore[arg-type]
                scenarios: List[TestScenario] = []
                for raw in scenarios_raw:
                    try:
                        scenarios.append(TestScenario.model_validate(raw))
                    except Exception:
                        continue

                if scenarios:
                    self._plans[profile] = scenarios

                    # Index by URL netloc for fast lookup
                    if url:
                        host = urlparse(url).netloc.lower()
                        if host:
                            self._url_index.setdefault(host, []).append(profile)

    def _read_json(self, path: Path) -> dict | list | None:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Save methods
    # ------------------------------------------------------------------
    def save_plan_for_url(
        self,
        url: str,
        scenarios: List[TestScenario],
        pdf_hash: str | None = None
    ) -> Path:
        """
        Save test plan for a URL.

        Args:
            url: Target URL (optional, can be empty)
            scenarios: List of test scenarios
            pdf_hash: Optional hash of source PDF (for cache validation)

        Returns:
            Path to saved file
        """
        # Create plans directory if it doesn't exist
        plans_dir = self._root.parent / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename
        if url:
            url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
            host = urlparse(url).netloc.replace(".", "_") or "unknown"
            filename = f"{host}_{url_hash}_plan.json"
        elif pdf_hash:
            filename = f"pdf_{pdf_hash}_plan.json"
        else:
            # Fallback: use timestamp
            import time
            filename = f"plan_{int(time.time())}.json"

        file_path = plans_dir / filename

        # Prepare data
        profile = host if url else (pdf_hash or "default")
        data = {
            "profile": profile,
            "url": url or "",
            "pdf_hash": pdf_hash,
            "test_scenarios": [s.model_dump() for s in scenarios]
        }

        # Save to file
        with file_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # Update in-memory cache
        self._plans[host] = scenarios

        return file_path


__all__ = ["PlanRepository"]
