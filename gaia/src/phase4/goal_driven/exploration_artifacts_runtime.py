from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Optional

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


def setup_recording_dir(session_id: str) -> Path:
    repo_root = Path(__file__).resolve().parents[4]
    screenshots_dir = (
        repo_root / "artifacts" / "exploration_results" / session_id / "screenshots"
    )
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    return screenshots_dir


def save_screenshot_to_file(
    agent,
    screenshot_base64: str,
    screenshots_dir: Path,
    step_num: int,
    suffix: str = "",
) -> str:
    if not screenshot_base64:
        return ""
    try:
        if "," in screenshot_base64:
            screenshot_base64 = screenshot_base64.split(",")[1]

        img_data = base64.b64decode(screenshot_base64)
        filename = f"step_{step_num:03d}_{suffix}.png" if suffix else f"step_{step_num:03d}.png"
        filepath = screenshots_dir / filename
        with open(filepath, "wb") as handle:
            handle.write(img_data)
        return str(filepath)
    except Exception as exc:
        agent._log(f"⚠️ 스크린샷 저장 실패: {exc}")
        return ""


def save_step_artifact_payload(
    agent,
    screenshots_dir: Optional[Path],
    step,
    before_path: str = "",
    after_path: str = "",
) -> None:
    if screenshots_dir is None:
        return
    try:
        steps_dir = screenshots_dir.parent / "steps"
        steps_dir.mkdir(parents=True, exist_ok=True)
        payload = step.model_dump(mode="json")
        payload["files"] = {"before": before_path, "after": after_path}
        if agent._last_exec_meta:
            payload["exec_meta"] = dict(agent._last_exec_meta)
        out_path = steps_dir / f"step_{int(step.step_number):03d}.json"
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except Exception as exc:
        agent._log(f"⚠️ 스텝 산출물 저장 실패: {exc}")


def write_result_json(agent, result) -> Optional[str]:
    try:
        repo_root = Path(__file__).resolve().parents[4]
        results_root = repo_root / "artifacts" / "exploration_results"
        results_root.mkdir(parents=True, exist_ok=True)
        session_dir = results_root / str(result.session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        payload = result.model_dump(mode="json")

        session_file = session_dir / "exploration_result.json"
        with open(session_file, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

        top_level_file = results_root / f"{result.session_id}.json"
        with open(top_level_file, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return str(top_level_file)
    except Exception as exc:
        agent._log(f"⚠️ 결과 JSON 저장 실패: {exc}")
        return None


def generate_gif(agent, screenshots_dir: Path, output_path: Path) -> bool:
    if Image is None:
        agent._log("⚠️ PIL이 설치되지 않아 GIF를 생성할 수 없습니다")
        return False

    try:
        png_files = sorted(screenshots_dir.glob("step_*_before.png"))
        if len(png_files) < 2:
            png_files = sorted(screenshots_dir.glob("step_*.png"))
        if len(png_files) < 2:
            agent._log("⚠️ GIF 생성을 위한 스크린샷이 부족합니다")
            return False

        images = []
        for png_file in png_files:
            img = Image.open(png_file)
            max_width = 800
            if img.width > max_width:
                ratio = max_width / img.width
                new_size = (max_width, int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            images.append(img)

        images[0].save(
            output_path,
            save_all=True,
            append_images=images[1:],
            duration=1000,
            loop=0,
        )
        agent._log(f"🎬 GIF 생성 완료: {output_path}")
        return True
    except Exception as exc:
        agent._log(f"⚠️ GIF 생성 실패: {exc}")
        return False
