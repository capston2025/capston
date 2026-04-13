"""Terminal-only interactive benchmark mode helpers."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from gaia.src.gui.benchmark_mode import (
    BenchmarkPreset,
    build_benchmark_catalog,
    extract_url_host,
    find_preset,
    load_benchmark_registry,
    override_suite_urls,
    render_benchmark_reports_html,
    scan_benchmark_reports,
    save_benchmark_registry,
    upsert_benchmark_site_url,
)

PromptSelectFn = Callable[[str, Sequence[str], str | None], str]
PromptTextFn = Callable[[str, str | None], str]
OutputFn = Callable[[str], None]
ProcessFactory = Callable[..., subprocess.Popen[str]]
ReportOpener = Callable[[str], bool]
ScenarioFormOpener = Callable[..., Mapping[str, Any] | None]

SITE_ADD_OPTION = "사이트 추가"
SITE_EDIT_OPTION = "사이트 수정"
SITE_DELETE_OPTION = "사이트 삭제"
SITE_EXIT_OPTION = "종료"


def build_terminal_benchmark_catalog(
    payload: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, BenchmarkPreset]]:
    catalog = [{**item, "is_custom": False} for item in build_benchmark_catalog(payload)]
    preset_map: dict[str, BenchmarkPreset] = {}
    for item in catalog:
        preset = find_preset(str(item.get("key") or "").strip())
        if preset is not None:
            preset_map[preset.key] = preset

    custom_sites = payload.get("custom_sites") if isinstance(payload.get("custom_sites"), Mapping) else {}
    for raw_key in sorted(custom_sites):
        raw_entry = custom_sites.get(raw_key)
        if not isinstance(raw_entry, Mapping):
            continue
        site_key = str(raw_key or "").strip()
        label = str(raw_entry.get("label") or "").strip()
        default_url = str(raw_entry.get("default_url") or "").strip()
        suite_path = str(raw_entry.get("suite_path") or "").strip()
        host_aliases_raw = list(raw_entry.get("host_aliases") or [])
        host_aliases = tuple(
            str(item).strip().lower()
            for item in host_aliases_raw
            if str(item).strip()
        )
        if not site_key or not label or not default_url or not suite_path:
            continue
        site_state = (
            (payload.get("sites") or {}).get(site_key, {})
            if isinstance(payload.get("sites"), Mapping)
            else {}
        )
        site_state = site_state if isinstance(site_state, Mapping) else {}
        urls = build_url_history(
            {
                "default_url": str(site_state.get("default_url") or default_url).strip() or default_url,
                "urls": list(site_state.get("urls") or []),
            }
        )
        preset = BenchmarkPreset(
            key=site_key,
            label=label,
            default_url=default_url,
            suite_path=suite_path,
            host_aliases=host_aliases or tuple(filter(None, (extract_url_host(default_url),))),
        )
        preset_map[site_key] = preset
        catalog.append(
            {
                "key": site_key,
                "label": label,
                "default_url": urls[0] if urls else default_url,
                "urls": urls[:8],
                "suite_path": suite_path,
                "suite_available": bool(suite_path),
                "status_text": "커스텀",
                "is_custom": True,
            }
        )
    return catalog, preset_map


def create_custom_suite_payload(*, site_key: str, label: str, default_url: str) -> dict[str, Any]:
    return {
        "suite_id": f"{site_key}_public_v1",
        "site": {
            "name": label,
            "base_url": default_url,
            "mode": "public_browse",
        },
        "grader_configs": {},
        "scenarios": [],
    }


def create_custom_site_definition(
    *,
    site_key: str,
    label: str,
    default_url: str,
) -> dict[str, Any]:
    host = extract_url_host(default_url)
    host_aliases = tuple(
        dict.fromkeys(
            alias
            for alias in (
                host,
                host.removeprefix("www.") if host.startswith("www.") else "",
                f"www.{host}" if host and not host.startswith("www.") else "",
            )
            if alias
        )
    )
    return {
        "label": label,
        "default_url": default_url,
        "suite_path": f"gaia/tests/scenarios/custom_{site_key}_suite.json",
        "host_aliases": list(host_aliases),
    }


def upsert_custom_benchmark_site(
    payload: Mapping[str, Any],
    *,
    site_key: str,
    site_definition: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = dict(payload)
    custom_sites = dict(normalized.get("custom_sites") or {})
    custom_sites[site_key] = dict(site_definition)
    normalized["custom_sites"] = custom_sites
    normalized = upsert_benchmark_site_url(normalized, site_key, str(site_definition.get("default_url") or ""))
    return normalized


def delete_custom_benchmark_site(payload: Mapping[str, Any], site_key: str) -> dict[str, Any]:
    normalized = dict(payload)
    custom_sites = dict(normalized.get("custom_sites") or {})
    custom_sites.pop(site_key, None)
    normalized["custom_sites"] = custom_sites
    sites = dict(normalized.get("sites") or {})
    sites.pop(site_key, None)
    normalized["sites"] = sites
    return normalized


def load_suite_payload(workspace_root: Path, suite_path: str) -> dict[str, Any]:
    target = (workspace_root / str(suite_path)).resolve()
    if not target.exists():
        raise FileNotFoundError(f"benchmark suite not found: {target}")
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("benchmark suite must be a JSON object")
    payload.setdefault("scenarios", [])
    if not isinstance(payload.get("scenarios"), list):
        raise ValueError("benchmark suite scenarios must be a list")
    return payload


def save_suite_payload(target: Path, payload: Mapping[str, Any]) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = dict(payload)
    normalized.setdefault("scenarios", [])
    target.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reloaded = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(reloaded, dict):
        raise ValueError("saved suite is not a JSON object")
    return target


def build_url_history(site_entry: Mapping[str, Any]) -> list[str]:
    urls = [str(item).strip() for item in list(site_entry.get("urls") or []) if str(item).strip()]
    default_url = str(site_entry.get("default_url") or "").strip()
    if default_url:
        urls = [default_url] + [item for item in urls if item != default_url]
    return urls


def build_scenario_labels(suite_payload: Mapping[str, Any]) -> list[str]:
    labels: list[str] = []
    for raw in list(suite_payload.get("scenarios") or []):
        if not isinstance(raw, Mapping):
            continue
        scenario_id = str(raw.get("id") or "").strip() or "UNKNOWN"
        goal = str(raw.get("goal") or "").strip() or "-"
        labels.append(f"{scenario_id} | {goal}")
    return labels


def build_single_scenario_suite_payload(
    suite_payload: Mapping[str, Any],
    scenario_id: str,
) -> dict[str, Any]:
    target_id = str(scenario_id or "").strip()
    payload = dict(suite_payload)
    scenarios = [dict(row) for row in list(payload.get("scenarios") or []) if isinstance(row, Mapping)]
    selected = [row for row in scenarios if str(row.get("id") or "").strip() == target_id]
    if not selected:
        raise KeyError(f"scenario not found: {target_id}")
    payload["scenarios"] = selected
    return payload


def append_scenario_to_suite(
    suite_payload: Mapping[str, Any],
    scenario: Mapping[str, Any],
) -> dict[str, Any]:
    scenario_id = str(scenario.get("id") or "").strip()
    if not scenario_id:
        raise ValueError("scenario id is required")
    payload = dict(suite_payload)
    scenarios = [dict(row) for row in list(payload.get("scenarios") or []) if isinstance(row, Mapping)]
    existing_ids = {str(row.get("id") or "").strip() for row in scenarios}
    if scenario_id in existing_ids:
        raise ValueError(f"duplicate scenario id: {scenario_id}")
    scenarios.append(dict(scenario))
    payload["scenarios"] = scenarios
    return payload


def replace_scenario_in_suite(
    suite_payload: Mapping[str, Any],
    original_id: str,
    updated_scenario: Mapping[str, Any],
) -> dict[str, Any]:
    target_id = str(original_id or "").strip()
    updated_id = str(updated_scenario.get("id") or "").strip()
    if not target_id or not updated_id:
        raise ValueError("scenario id is required")
    payload = dict(suite_payload)
    scenarios = [dict(row) for row in list(payload.get("scenarios") or []) if isinstance(row, Mapping)]
    replaced = False
    for index, row in enumerate(scenarios):
        row_id = str(row.get("id") or "").strip()
        if row_id == target_id:
            scenarios[index] = dict(updated_scenario)
            replaced = True
            continue
        if row_id == updated_id and updated_id != target_id:
            raise ValueError(f"duplicate scenario id: {updated_id}")
    if not replaced:
        raise KeyError(f"scenario not found: {target_id}")
    payload["scenarios"] = scenarios
    return payload


def delete_scenario_from_suite(
    suite_payload: Mapping[str, Any],
    scenario_id: str,
) -> dict[str, Any]:
    target_id = str(scenario_id or "").strip()
    payload = dict(suite_payload)
    scenarios = [dict(row) for row in list(payload.get("scenarios") or []) if isinstance(row, Mapping)]
    filtered = [row for row in scenarios if str(row.get("id") or "").strip() != target_id]
    if len(filtered) == len(scenarios):
        raise KeyError(f"scenario not found: {target_id}")
    payload["scenarios"] = filtered
    return payload


def _default_scenario_name(current: Mapping[str, Any]) -> str:
    name = str(current.get("name") or "").strip()
    if name:
        return name
    scenario_id = str(current.get("id") or "").strip()
    if scenario_id:
        return scenario_id
    return ""


def _infer_scenario_prefix(existing_ids: set[str], default_url: str) -> str:
    prefixes: list[str] = []
    for item in sorted(existing_ids):
        token = str(item or "").strip().split("_", 1)[0].strip().upper()
        if token:
            prefixes.append(token)
    if prefixes:
        counts: dict[str, int] = {}
        for token in prefixes:
            counts[token] = counts.get(token, 0) + 1
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
    host = extract_url_host(default_url).removeprefix("www.")
    host_token = host.split(".", 1)[0].strip().upper()
    if host_token:
        return re.sub(r"[^A-Z0-9]+", "", host_token) or "SCN"
    return "SCN"


def _generate_scenario_id(*, test_name: str, existing_ids: set[str], default_url: str) -> str:
    prefix = _infer_scenario_prefix(existing_ids, default_url)
    max_index = 0
    pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)(?:_|$)")
    for item in existing_ids:
        matched = pattern.match(str(item or "").strip().upper())
        if matched:
            try:
                max_index = max(max_index, int(matched.group(1)))
            except Exception:
                continue
    next_index = max_index + 1
    suffix = _slugify(test_name).replace("-", "_").replace(".", "_").upper()
    suffix = re.sub(r"_+", "_", suffix).strip("_") or "BENCHMARK"
    return f"{prefix}_{next_index:03d}_{suffix}"


def prompt_scenario_fields(
    *,
    prompt_select: PromptSelectFn,
    prompt: PromptTextFn,
    prompt_non_empty: PromptTextFn,
    emit: OutputFn,
    existing: Mapping[str, Any] | None = None,
    existing_ids: set[str] | None = None,
    default_url: str = "",
) -> dict[str, Any]:
    del prompt_select, prompt
    current = dict(existing or {})
    constraints = dict(current.get("constraints") or {})
    reserved_ids = {str(item).strip() for item in (existing_ids or set()) if str(item).strip()}
    current_id = str(current.get("id") or "").strip()
    if current_id:
        reserved_ids.discard(current_id)

    test_name = str(
        prompt_non_empty(
            "테스트 이름",
            default=_default_scenario_name(current) or None,
        )
    ).strip()
    scenario_id = current_id or _generate_scenario_id(
        test_name=test_name,
        existing_ids=reserved_ids,
        default_url=str(current.get("url") or default_url or "").strip(),
    )

    url = str(
        prompt_non_empty(
            "url",
            default=str(current.get("url") or default_url or "").strip() or None,
        )
    ).strip()
    goal = str(prompt_non_empty("goal", default=str(current.get("goal") or "").strip() or None)).strip()
    time_budget_default = str(current.get("time_budget_sec") or 300)
    while True:
        time_budget_raw = str(prompt_non_empty("time_budget_sec", default=time_budget_default)).strip()
        try:
            time_budget_sec = max(1, int(time_budget_raw))
            break
        except Exception:
            emit("time_budget_sec는 1 이상의 정수여야 합니다.")

    scenario = dict(current)
    scenario["id"] = scenario_id
    if str(current.get("name") or "").strip() or not current_id or test_name != current_id:
        scenario["name"] = test_name
    else:
        scenario.pop("name", None)
    scenario["url"] = url
    scenario["goal"] = goal
    scenario["constraints"] = dict(constraints) if constraints else {
        "allow_navigation": True,
        "require_ref_only": True,
        "require_state_change": False,
    }
    scenario["time_budget_sec"] = time_budget_sec
    if "expected_signals" in current and not isinstance(current.get("expected_signals"), list):
        scenario.pop("expected_signals", None)
    elif "expected_signals" not in scenario and not current:
        scenario["expected_signals"] = []
    return scenario


def _build_scenario_payload(
    *,
    current: Mapping[str, Any],
    test_name: str,
    url: str,
    goal: str,
    time_budget_sec: int,
    existing_ids: set[str],
) -> dict[str, Any]:
    constraints = dict(current.get("constraints") or {})
    current_id = str(current.get("id") or "").strip()
    reserved_ids = {str(item).strip() for item in existing_ids if str(item).strip()}
    if current_id:
        reserved_ids.discard(current_id)
    scenario_id = current_id or _generate_scenario_id(
        test_name=test_name,
        existing_ids=reserved_ids,
        default_url=url,
    )
    scenario = dict(current)
    scenario["id"] = scenario_id
    if str(current.get("name") or "").strip() or not current_id or test_name != current_id:
        scenario["name"] = test_name
    else:
        scenario.pop("name", None)
    scenario["url"] = url
    scenario["goal"] = goal
    scenario["constraints"] = dict(constraints) if constraints else {
        "allow_navigation": True,
        "require_ref_only": True,
        "require_state_change": False,
    }
    scenario["time_budget_sec"] = time_budget_sec
    if "expected_signals" in current and not isinstance(current.get("expected_signals"), list):
        scenario.pop("expected_signals", None)
    elif "expected_signals" not in scenario and not current:
        scenario["expected_signals"] = []
    return scenario


def _applescript_quote(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def _prompt_macos_dialog(
    *,
    title: str,
    field_label: str,
    default: str,
    hidden: bool = False,
) -> str | None:
    prompt_text = _applescript_quote(field_label)
    title_text = _applescript_quote(title)
    default_text = _applescript_quote(default)
    hidden_flag = " with hidden answer" if hidden else ""
    script = (
        f'text returned of (display dialog "{prompt_text}" '
        f'default answer "{default_text}" with title "{title_text}"{hidden_flag})'
    )
    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        stderr = f"{proc.stderr or ''} {proc.stdout or ''}".lower()
        if "user canceled" in stderr or "cancel" in stderr:
            return None
        raise RuntimeError((proc.stderr or proc.stdout or "osascript dialog failed").strip())
    return str(proc.stdout or "").strip()


def _powershell_quote(value: str) -> str:
    return str(value or "").replace("'", "''")


def _find_windows_shell() -> str:
    for candidate in ("powershell", "pwsh"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise RuntimeError("powershell 또는 pwsh를 찾을 수 없습니다.")


def _prompt_windows_dialog(
    *,
    title: str,
    field_label: str,
    default: str,
) -> str | None:
    shell = _find_windows_shell()
    title_text = _powershell_quote(title)
    prompt_text = _powershell_quote(field_label)
    default_text = _powershell_quote(default)
    script = f"""
Add-Type -AssemblyName Microsoft.VisualBasic
$value = [Microsoft.VisualBasic.Interaction]::InputBox('{prompt_text}', '{title_text}', '{default_text}')
if ($value -eq $null) {{ exit 1 }}
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Write-Output $value
""".strip()
    proc = subprocess.run(
        [shell, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        stderr = f"{proc.stderr or ''} {proc.stdout or ''}".lower()
        if "cancel" in stderr or "canceled" in stderr:
            return None
        raise RuntimeError((proc.stderr or proc.stdout or "powershell dialog failed").strip())
    return str(proc.stdout or "").rstrip("\r\n")


def open_scenario_form_windows_dialogs(
    *,
    emit: OutputFn,
    existing: Mapping[str, Any] | None = None,
    existing_ids: set[str] | None = None,
    default_url: str = "",
    title: str = "새 테스트 추가",
) -> dict[str, Any] | None:
    del emit
    current = dict(existing or {})
    name_default = _default_scenario_name(current)
    url_default = str(current.get("url") or default_url or "").strip()
    goal_default = str(current.get("goal") or "").strip()
    timeout_default = str(current.get("time_budget_sec") or 300)

    test_name = _prompt_windows_dialog(title=title, field_label="테스트 이름", default=name_default)
    if test_name is None:
        return None
    test_name = test_name.strip()
    if not test_name:
        raise ValueError("테스트 이름을 입력해주세요.")

    url = _prompt_windows_dialog(title=title, field_label="url", default=url_default)
    if url is None:
        return None
    url = url.strip()
    if not url:
        raise ValueError("url을 입력해주세요.")

    goal = _prompt_windows_dialog(title=title, field_label="goal", default=goal_default)
    if goal is None:
        return None
    goal = goal.strip()
    if not goal:
        raise ValueError("goal을 입력해주세요.")

    timeout_raw = _prompt_windows_dialog(title=title, field_label="time_budget_sec", default=timeout_default)
    if timeout_raw is None:
        return None
    try:
        time_budget_sec = max(1, int(str(timeout_raw).strip()))
    except Exception as exc:
        raise ValueError("time_budget_sec는 1 이상의 정수여야 합니다.") from exc

    return _build_scenario_payload(
        current=current,
        test_name=test_name,
        url=url,
        goal=goal,
        time_budget_sec=time_budget_sec,
        existing_ids=set(existing_ids or set()),
    )


def open_scenario_form_macos_dialogs(
    *,
    emit: OutputFn,
    existing: Mapping[str, Any] | None = None,
    existing_ids: set[str] | None = None,
    default_url: str = "",
    title: str = "새 테스트 추가",
) -> dict[str, Any] | None:
    del emit
    current = dict(existing or {})
    name_default = _default_scenario_name(current)
    url_default = str(current.get("url") or default_url or "").strip()
    goal_default = str(current.get("goal") or "").strip()
    timeout_default = str(current.get("time_budget_sec") or 300)

    test_name = _prompt_macos_dialog(title=title, field_label="테스트 이름", default=name_default)
    if test_name is None:
        return None
    test_name = test_name.strip()
    if not test_name:
        raise ValueError("테스트 이름을 입력해주세요.")

    url = _prompt_macos_dialog(title=title, field_label="url", default=url_default)
    if url is None:
        return None
    url = url.strip()
    if not url:
        raise ValueError("url을 입력해주세요.")

    goal = _prompt_macos_dialog(title=title, field_label="goal", default=goal_default)
    if goal is None:
        return None
    goal = goal.strip()
    if not goal:
        raise ValueError("goal을 입력해주세요.")

    timeout_raw = _prompt_macos_dialog(title=title, field_label="time_budget_sec", default=timeout_default)
    if timeout_raw is None:
        return None
    try:
        time_budget_sec = max(1, int(str(timeout_raw).strip()))
    except Exception as exc:
        raise ValueError("time_budget_sec는 1 이상의 정수여야 합니다.") from exc

    return _build_scenario_payload(
        current=current,
        test_name=test_name,
        url=url,
        goal=goal,
        time_budget_sec=time_budget_sec,
        existing_ids=set(existing_ids or set()),
    )


def open_scenario_form_gui(
    *,
    emit: OutputFn,
    existing: Mapping[str, Any] | None = None,
    existing_ids: set[str] | None = None,
    default_url: str = "",
    title: str = "새 테스트 추가",
) -> dict[str, Any] | None:
    current = dict(existing or {})

    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except Exception as exc:
        if sys.platform == "darwin":
            emit(f"tkinter GUI를 사용할 수 없어 macOS 입력창으로 전환합니다: {exc}")
            try:
                return open_scenario_form_macos_dialogs(
                    emit=emit,
                    existing=current,
                    existing_ids=set(existing_ids or set()),
                    default_url=default_url,
                    title=title,
                )
            except Exception as fallback_exc:
                emit(f"macOS 입력창도 열지 못해 터미널 입력으로 전환합니다: {fallback_exc}")
                return None
        if sys.platform.startswith("win"):
            emit(f"tkinter GUI를 사용할 수 없어 Windows 입력창으로 전환합니다: {exc}")
            try:
                return open_scenario_form_windows_dialogs(
                    emit=emit,
                    existing=current,
                    existing_ids=set(existing_ids or set()),
                    default_url=default_url,
                    title=title,
                )
            except Exception as fallback_exc:
                emit(f"Windows 입력창도 열지 못해 터미널 입력으로 전환합니다: {fallback_exc}")
                return None
        emit(f"GUI 입력창을 사용할 수 없어 터미널 입력으로 전환합니다: {exc}")
        return None

    root: Any = None
    dialog: Any = None
    result: dict[str, Any] | None = None
    cancelled = True
    try:
        root = tk.Tk()
        root.withdraw()
        dialog = tk.Toplevel(root)
        dialog.title(title)
        dialog.resizable(False, False)

        frame = ttk.Frame(dialog, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)

        ttk.Label(frame, text="테스트 이름").grid(row=0, column=0, sticky="w")
        name_var = tk.StringVar(value=_default_scenario_name(current))
        name_entry = ttk.Entry(frame, textvariable=name_var, width=56)
        name_entry.grid(row=1, column=0, sticky="ew", pady=(4, 12))

        ttk.Label(frame, text="url").grid(row=2, column=0, sticky="w")
        url_var = tk.StringVar(value=str(current.get("url") or default_url or "").strip())
        url_entry = ttk.Entry(frame, textvariable=url_var, width=56)
        url_entry.grid(row=3, column=0, sticky="ew", pady=(4, 12))

        ttk.Label(frame, text="goal").grid(row=4, column=0, sticky="w")
        goal_text = tk.Text(frame, width=56, height=6, wrap="word")
        goal_text.grid(row=5, column=0, sticky="ew", pady=(4, 12))
        goal_text.insert("1.0", str(current.get("goal") or "").strip())

        ttk.Label(frame, text="time_budget_sec").grid(row=6, column=0, sticky="w")
        timeout_var = tk.StringVar(value=str(current.get("time_budget_sec") or 300))
        timeout_entry = ttk.Entry(frame, textvariable=timeout_var, width=20)
        timeout_entry.grid(row=7, column=0, sticky="w", pady=(4, 12))

        button_row = ttk.Frame(frame)
        button_row.grid(row=8, column=0, sticky="e")

        def _finish_cancel() -> None:
            nonlocal cancelled
            cancelled = True
            dialog.destroy()

        def _finish_submit() -> None:
            nonlocal result, cancelled
            test_name = str(name_var.get() or "").strip()
            url = str(url_var.get() or "").strip()
            goal = str(goal_text.get("1.0", "end-1c") or "").strip()
            timeout_raw = str(timeout_var.get() or "").strip()
            if not test_name:
                messagebox.showerror("입력 오류", "테스트 이름을 입력해주세요.", parent=dialog)
                name_entry.focus_set()
                return
            if not url:
                messagebox.showerror("입력 오류", "url을 입력해주세요.", parent=dialog)
                url_entry.focus_set()
                return
            if not goal:
                messagebox.showerror("입력 오류", "goal을 입력해주세요.", parent=dialog)
                goal_text.focus_set()
                return
            try:
                time_budget_sec = max(1, int(timeout_raw))
            except Exception:
                messagebox.showerror("입력 오류", "time_budget_sec는 1 이상의 정수여야 합니다.", parent=dialog)
                timeout_entry.focus_set()
                return
            result = _build_scenario_payload(
                current=current,
                test_name=test_name,
                url=url,
                goal=goal,
                time_budget_sec=time_budget_sec,
                existing_ids=set(existing_ids or set()),
            )
            cancelled = False
            dialog.destroy()

        ttk.Button(button_row, text="취소", command=_finish_cancel).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(button_row, text="저장", command=_finish_submit).grid(row=0, column=1)

        dialog.protocol("WM_DELETE_WINDOW", _finish_cancel)
        dialog.transient(root)
        dialog.grab_set()
        frame.columnconfigure(0, weight=1)
        name_entry.focus_set()
        dialog.bind("<Return>", lambda _event: _finish_submit())
        root.wait_window(dialog)
    except Exception as exc:
        emit(f"GUI 입력창을 열지 못해 터미널 입력으로 전환합니다: {exc}")
        return None
    finally:
        try:
            if dialog is not None and dialog.winfo_exists():
                dialog.destroy()
        except Exception:
            pass
        try:
            if root is not None:
                root.destroy()
        except Exception:
            pass

    if cancelled:
        emit("GUI 입력이 취소되어 터미널 입력으로 전환합니다.")
        return None
    return result


def write_benchmark_report_html(
    *,
    workspace_root: Path,
    preset: BenchmarkPreset,
    selected_url: str,
) -> Path:
    reports = _scan_benchmark_reports_for_preset(
        workspace_root=workspace_root,
        preset=preset,
        selected_url=selected_url,
    )
    html_doc = render_benchmark_reports_html(
        site_label=preset.label,
        selected_url=selected_url,
        reports=reports,
    )
    out_dir = workspace_root / "artifacts" / "tmp" / "benchmark_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"{preset.key}_benchmark_report.html"
    report_path.write_text(html_doc, encoding="utf-8")
    return report_path


def _summary_matches_preset(summary: Mapping[str, Any], preset: BenchmarkPreset, selected_url: str) -> bool:
    site = summary.get("site") if isinstance(summary.get("site"), Mapping) else {}
    base_url = str(site.get("base_url") or "").strip()
    host = extract_url_host(base_url)
    selected_host = extract_url_host(selected_url)
    if selected_host and host == selected_host:
        return True
    if host and any(alias in host for alias in preset.host_aliases):
        return True
    return False


def _scan_benchmark_reports_for_preset(
    *,
    workspace_root: Path,
    preset: BenchmarkPreset,
    selected_url: str,
    limit: int = 12,
) -> list[dict[str, Any]]:
    if find_preset(preset.key) is not None:
        return scan_benchmark_reports(
            workspace_root=workspace_root,
            site_key=preset.key,
            selected_url=selected_url,
            limit=limit,
        )
    root = workspace_root / "artifacts" / "benchmarks"
    if not root.exists():
        return []
    reports: list[dict[str, Any]] = []
    for summary_path in sorted(root.glob("*/summary.json"), reverse=True):
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(summary, Mapping):
            continue
        if not _summary_matches_preset(summary, preset, selected_url):
            continue
        result_path = summary_path.with_name("results.json")
        results: list[dict[str, Any]] = []
        if result_path.exists():
            try:
                parsed = json.loads(result_path.read_text(encoding="utf-8"))
                if isinstance(parsed, list):
                    results = [row for row in parsed if isinstance(row, dict)]
            except Exception:
                results = []
        reports.append(
            {
                "artifact_dir": str(summary_path.parent),
                "summary_path": str(summary_path),
                "results_path": str(result_path),
                "summary": dict(summary),
                "results": results,
            }
        )
        if len(reports) >= max(1, int(limit)):
            break
    return reports


def open_benchmark_report(report_path: Path, opener: ReportOpener = webbrowser.open_new_tab) -> bool:
    return bool(opener(report_path.resolve().as_uri()))


def run_benchmark_suite(
    *,
    workspace_root: Path,
    preset: BenchmarkPreset,
    target_url: str,
    suite_payload: Mapping[str, Any],
    emit: OutputFn,
    run_tag: str,
    timeout_cap: int = 600,
    process_factory: ProcessFactory = subprocess.Popen,
) -> dict[str, Any]:
    scenarios = [dict(row) for row in list(suite_payload.get("scenarios") or []) if isinstance(row, Mapping)]
    if not scenarios:
        emit("등록된 테스트가 없습니다. 먼저 테스트를 추가해주세요.")
        return {"status": "empty", "summary": {}, "results": [], "output_dir": ""}

    overridden = override_suite_urls(suite_payload, target_url)
    started = int(time.time())
    tmp_root = workspace_root / "artifacts" / "tmp" / "terminal_benchmark_mode"
    tmp_root.mkdir(parents=True, exist_ok=True)
    suite_slug = _slugify(run_tag)
    tmp_suite_path = tmp_root / f"{preset.key}_{suite_slug}_{started}.json"
    tmp_suite_path.write_text(json.dumps(overridden, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    output_dir = (workspace_root / "artifacts" / "benchmarks" / f"{preset.key}_{suite_slug}_{started}").resolve()
    cmd = [
        sys.executable,
        "scripts/run_goal_benchmark.py",
        "--suite",
        str(tmp_suite_path),
        "--repeats",
        "1",
        "--timeout-cap",
        str(max(600, int(timeout_cap))),
        "--session-prefix",
        f"terminal-{preset.key}",
        "--output-dir",
        str(output_dir),
    ]
    env = os.environ.copy()
    env.setdefault("GAIA_RAIL_ENABLED", "0")
    env.setdefault("GAIA_LLM_MODEL", env.get("GAIA_LLM_MODEL", "gpt-5.4"))
    if os.name == "nt":
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")

    emit(f"{preset.label} 벤치를 실행합니다.")
    emit(f"   - target: {target_url}")
    emit(f"   - suite: {tmp_suite_path}")

    process = process_factory(
        cmd,
        cwd=str(workspace_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )
    captured: list[str] = []
    if process.stdout is not None:
        for raw_line in process.stdout:
            line = str(raw_line or "").rstrip()
            if not line:
                continue
            captured.append(line)
            emit(line)
    return_code = process.wait()

    summary_path = output_dir / "summary.json"
    results_path = output_dir / "results.json"
    summary_payload = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    results_payload = json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else []
    status_counts = summary_payload.get("status_counts") if isinstance(summary_payload, Mapping) else {}
    status_counts = status_counts if isinstance(status_counts, Mapping) else {}
    emit(
        "벤치 실행 완료"
        f" | success={int(status_counts.get('SUCCESS') or 0)}"
        f" fail={int(status_counts.get('FAIL') or 0)}"
        f" | artifact={output_dir}"
    )
    return {
        "status": "success" if return_code == 0 else "failed",
        "summary": summary_payload,
        "results": results_payload,
        "output_dir": str(output_dir),
        "cmd": cmd,
        "captured": captured,
    }


def manage_benchmark_sites(
    *,
    workspace_root: Path,
    registry: Mapping[str, Any],
    action: str,
    prompt_select: PromptSelectFn,
    prompt: PromptTextFn,
    prompt_non_empty: PromptTextFn,
    emit: OutputFn,
) -> dict[str, Any]:
    normalized = dict(registry)
    catalog, preset_map = build_terminal_benchmark_catalog(normalized)
    custom_entries = [item for item in catalog if bool(item.get("is_custom"))]

    if action == SITE_ADD_OPTION:
        existing_keys = {str(item.get("key") or "").strip() for item in catalog}
        existing_labels = {str(item.get("label") or "").strip() for item in catalog}
        label = str(prompt_non_empty("추가할 사이트 이름", default=None)).strip()
        if label in existing_labels:
            emit(f"이미 존재하는 사이트 이름입니다: {label}")
            return normalized
        generated_key = _slugify(label).replace("-", "_")
        site_key = str(prompt("site_key (옵션)", default=generated_key or "custom_site")).strip() or generated_key or "custom_site"
        site_key = _slugify(site_key).replace("-", "_")
        while not site_key or site_key in existing_keys:
            if site_key in existing_keys:
                emit(f"이미 존재하는 site_key입니다: {site_key}")
            site_key = _slugify(str(prompt_non_empty("중복되지 않는 site_key", default=None)).strip()).replace("-", "_")
        default_url = str(prompt_non_empty("기본 링크", default=None)).strip()
        site_definition = create_custom_site_definition(site_key=site_key, label=label, default_url=default_url)
        suite_path = (workspace_root / str(site_definition["suite_path"])).resolve()
        save_suite_payload(
            suite_path,
            create_custom_suite_payload(site_key=site_key, label=label, default_url=default_url),
        )
        normalized = upsert_custom_benchmark_site(normalized, site_key=site_key, site_definition=site_definition)
        emit(f"🆕 사이트 추가 완료: {label} ({site_key})")
        return normalized

    if not custom_entries:
        emit("편집/삭제 가능한 커스텀 사이트가 아직 없습니다.")
        return normalized

    custom_labels = tuple(item["label"] for item in custom_entries) + ("이전으로",)
    selected_label = prompt_select(
        "커스텀 벤치 사이트를 선택하세요",
        custom_labels,
        default=custom_entries[0]["label"],
    )
    if selected_label == "이전으로":
        return normalized
    site_entry = next((item for item in custom_entries if item["label"] == selected_label), None)
    if site_entry is None:
        emit("선택한 커스텀 사이트를 찾지 못했습니다.")
        return normalized
    preset = preset_map.get(str(site_entry.get("key") or "").strip())
    if preset is None:
        emit("선택한 사이트 preset을 찾지 못했습니다.")
        return normalized

    if action == SITE_EDIT_OPTION:
        updated_label = str(prompt("사이트 이름", default=preset.label)).strip() or preset.label
        occupied_labels = {
            str(item.get("label") or "").strip()
            for item in catalog
            if str(item.get("key") or "").strip() != preset.key
        }
        if updated_label in occupied_labels:
            emit(f"이미 존재하는 사이트 이름입니다: {updated_label}")
            return normalized
        updated_url = str(prompt_non_empty("기본 링크", default=preset.default_url)).strip()
        updated_definition = create_custom_site_definition(
            site_key=preset.key,
            label=updated_label,
            default_url=updated_url,
        )
        suite_path = (workspace_root / str(updated_definition["suite_path"])).resolve()
        suite_payload = load_suite_payload(workspace_root, str(updated_definition["suite_path"]))
        suite_payload["site"] = {
            **dict(suite_payload.get("site") or {}),
            "name": updated_label,
            "base_url": updated_url,
        }
        save_suite_payload(suite_path, suite_payload)
        normalized = upsert_custom_benchmark_site(
            normalized,
            site_key=preset.key,
            site_definition=updated_definition,
        )
        emit(f"✏️ 사이트 수정 완료: {updated_label} ({preset.key})")
        return normalized

    suite_path = (workspace_root / str(preset.suite_path or "")).resolve()
    if suite_path.exists():
        suite_path.unlink()
    normalized = delete_custom_benchmark_site(normalized, preset.key)
    emit(f"🗑️ 사이트 삭제 완료: {preset.label} ({preset.key})")
    return normalized


def run_terminal_benchmark_mode(
    *,
    workspace_root: Path,
    prompt_select: PromptSelectFn,
    prompt: PromptTextFn,
    prompt_non_empty: PromptTextFn,
    emit: OutputFn = print,
    registry_path: Path | None = None,
    run_suite_handler: Callable[..., dict[str, Any]] = run_benchmark_suite,
    report_writer: Callable[..., Path] = write_benchmark_report_html,
    report_opener: Callable[[Path], bool] = open_benchmark_report,
    scenario_form_opener: ScenarioFormOpener = open_scenario_form_gui,
) -> int:
    registry = load_benchmark_registry(registry_path)

    while True:
        catalog, preset_map = build_terminal_benchmark_catalog(registry)
        site_options = tuple(item["label"] for item in catalog) + (
            SITE_ADD_OPTION,
            SITE_EDIT_OPTION,
            SITE_DELETE_OPTION,
            SITE_EXIT_OPTION,
        )
        selected_site = prompt_select(
            "벤치 사이트를 선택하세요",
            site_options,
            default=catalog[0]["label"] if catalog else SITE_EXIT_OPTION,
        )
        if selected_site == SITE_EXIT_OPTION:
            emit("벤치마킹 모드를 종료합니다.")
            return 0
        if selected_site in {SITE_ADD_OPTION, SITE_EDIT_OPTION, SITE_DELETE_OPTION}:
            registry = manage_benchmark_sites(
                workspace_root=workspace_root,
                registry=registry,
                action=selected_site,
                prompt_select=prompt_select,
                prompt=prompt,
                prompt_non_empty=prompt_non_empty,
                emit=emit,
            )
            save_benchmark_registry(registry, registry_path)
            continue

        site_entry = next((item for item in catalog if item["label"] == selected_site), None)
        if site_entry is None:
            emit("선택한 사이트를 찾지 못했습니다.")
            continue
        preset = preset_map.get(str(site_entry.get("key") or "").strip())
        if preset is None:
            emit("선택한 사이트 preset을 찾지 못했습니다.")
            continue

        selected_url, registry = _select_benchmark_url(
            registry=registry,
            site_entry=site_entry,
            preset=preset,
            prompt_select=prompt_select,
            prompt_non_empty=prompt_non_empty,
        )
        if not selected_url:
            continue
        save_benchmark_registry(registry, registry_path)

        while True:
            action = prompt_select(
                f"{preset.label} 작업을 선택하세요",
                ("새로운 테스트 추가", "기존 테스트 실행", "테스트 편집", "지표 확인", "이전으로"),
                default="기존 테스트 실행",
            )
            if action == "이전으로":
                break

            suite_path = (workspace_root / str(preset.suite_path or "")).resolve()
            suite_payload = load_suite_payload(workspace_root, preset.suite_path or "")

            if action == "새로운 테스트 추가":
                existing_ids = {
                    str(row.get("id") or "").strip()
                    for row in list(suite_payload.get("scenarios") or [])
                    if isinstance(row, Mapping)
                }
                new_scenario = scenario_form_opener(
                    emit=emit,
                    existing=None,
                    existing_ids=existing_ids,
                    default_url=selected_url,
                    title=f"{preset.label} 테스트 추가",
                )
                if new_scenario is None:
                    new_scenario = prompt_scenario_fields(
                        prompt_select=prompt_select,
                        prompt=prompt,
                        prompt_non_empty=prompt_non_empty,
                        emit=emit,
                        existing=None,
                        existing_ids=existing_ids,
                        default_url=selected_url,
                    )
                updated_payload = append_scenario_to_suite(suite_payload, new_scenario)
                save_suite_payload(suite_path, updated_payload)
                emit(f"💾 테스트 추가 완료: {new_scenario['id']}")
                continue

            if action == "기존 테스트 실행":
                run_mode = prompt_select(
                    "실행 범위를 선택하세요",
                    ("기존 테스트 전체 실행", "개별 실행", "이전으로"),
                    default="기존 테스트 전체 실행",
                )
                if run_mode == "이전으로":
                    continue
                if run_mode == "기존 테스트 전체 실행":
                    run_suite_handler(
                        workspace_root=workspace_root,
                        preset=preset,
                        target_url=selected_url,
                        suite_payload=suite_payload,
                        emit=emit,
                        run_tag="full_suite",
                    )
                    continue

                scenario_id = _select_scenario_id(
                    suite_payload=suite_payload,
                    prompt_select=prompt_select,
                    emit=emit,
                )
                if not scenario_id:
                    continue
                single_payload = build_single_scenario_suite_payload(suite_payload, scenario_id)
                run_suite_handler(
                    workspace_root=workspace_root,
                    preset=preset,
                    target_url=selected_url,
                    suite_payload=single_payload,
                    emit=emit,
                    run_tag=scenario_id,
                )
                continue

            if action == "테스트 편집":
                scenario_id = _select_scenario_id(
                    suite_payload=suite_payload,
                    prompt_select=prompt_select,
                    emit=emit,
                )
                if not scenario_id:
                    continue
                edit_action = prompt_select(
                    "테스트 편집 작업을 선택하세요",
                    ("수정", "삭제", "이전으로"),
                    default="수정",
                )
                if edit_action == "이전으로":
                    continue
                existing = _find_scenario(suite_payload, scenario_id)
                if existing is None:
                    emit(f"선택한 테스트를 찾지 못했습니다: {scenario_id}")
                    continue
                if edit_action == "삭제":
                    updated_payload = delete_scenario_from_suite(suite_payload, scenario_id)
                    save_suite_payload(suite_path, updated_payload)
                    emit(f"🗑️ 테스트 삭제 완료: {scenario_id}")
                    continue

                updated_scenario = prompt_scenario_fields(
                    prompt_select=prompt_select,
                    prompt=prompt,
                    prompt_non_empty=prompt_non_empty,
                    emit=emit,
                    existing=existing,
                    existing_ids={
                        str(row.get("id") or "").strip()
                        for row in list(suite_payload.get("scenarios") or [])
                        if isinstance(row, Mapping)
                    },
                    default_url=selected_url,
                )
                updated_payload = replace_scenario_in_suite(suite_payload, scenario_id, updated_scenario)
                save_suite_payload(suite_path, updated_payload)
                emit(f"✏️ 테스트 수정 완료: {updated_scenario['id']}")
                continue

            if action == "지표 확인":
                report_path = report_writer(
                    workspace_root=workspace_root,
                    preset=preset,
                    selected_url=selected_url,
                )
                report_opener(report_path)
                emit(f"📊 결과 보드 생성: {report_path}")
                continue


def _find_scenario(suite_payload: Mapping[str, Any], scenario_id: str) -> dict[str, Any] | None:
    target_id = str(scenario_id or "").strip()
    for raw in list(suite_payload.get("scenarios") or []):
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("id") or "").strip() == target_id:
            return dict(raw)
    return None


def _select_scenario_id(
    *,
    suite_payload: Mapping[str, Any],
    prompt_select: PromptSelectFn,
    emit: OutputFn,
) -> str | None:
    labels = build_scenario_labels(suite_payload)
    if not labels:
        emit("등록된 테스트가 없습니다. 먼저 테스트를 추가해주세요.")
        return None
    selection = prompt_select(
        "테스트를 선택하세요",
        tuple(labels) + ("이전으로",),
        default=labels[0],
    )
    if selection == "이전으로":
        return None
    return selection.split(" | ", 1)[0].strip()


def _select_benchmark_url(
    *,
    registry: Mapping[str, Any],
    site_entry: Mapping[str, Any],
    preset: BenchmarkPreset,
    prompt_select: PromptSelectFn,
    prompt_non_empty: PromptTextFn,
) -> tuple[str | None, dict[str, Any]]:
    urls = build_url_history(site_entry)
    default_url = str(site_entry.get("default_url") or preset.default_url).strip() or preset.default_url
    options = tuple(urls + ["직접 입력", "이전으로"])
    selected = prompt_select(
        f"{preset.label} 대상 링크를 선택하세요",
        options,
        default=default_url if default_url in options else (urls[0] if urls else "직접 입력"),
    )
    if selected == "이전으로":
        return None, dict(registry)
    if selected == "직접 입력":
        selected = str(prompt_non_empty("벤치 링크를 입력하세요", default=default_url or None)).strip()
    updated = upsert_benchmark_site_url(registry, preset.key, selected)
    return selected, updated


def _slugify(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip()).strip("-").lower() or "benchmark"
