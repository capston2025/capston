"""PRD bundle load/save helpers."""
from __future__ import annotations

import json
from pathlib import Path

from gaia.src.phase1.prd_bundle import PRDBundle, bundle_output_path, is_prd_bundle_payload


class PRDBundleRepository:
    def __init__(self, root_dir: Path | str | None = None) -> None:
        self._root = Path(root_dir) if root_dir else (Path(__file__).resolve().parents[3] / "artifacts" / "prd_bundles")
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root_dir(self) -> Path:
        return self._root

    def load_bundle(self, path: Path | str) -> PRDBundle:
        bundle_path = Path(path).expanduser()
        if not bundle_path.exists():
            raise FileNotFoundError(bundle_path)
        payload = json.loads(bundle_path.read_text(encoding="utf-8"))
        if not is_prd_bundle_payload(payload):
            raise ValueError(f"PRD bundle 형식이 아닙니다: {bundle_path}")
        return PRDBundle.model_validate(payload)

    def save_bundle(self, bundle: PRDBundle, output_path: Path | str | None = None) -> Path:
        path = Path(output_path).expanduser() if output_path else bundle_output_path(self._root, bundle.suggested_filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
        return path
