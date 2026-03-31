"""
Exploratory Testing Agent

완전 자율 탐색 모드 - 화면의 모든 UI 요소를 자동으로 찾아서 테스트
"""

from __future__ import annotations
import time
import json
import math
import os
import re
from typing import Any, Dict, List, Optional, Set, Callable, Tuple
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from gaia.src.phase4.memory.models import MemoryActionRecord, MemorySummaryRecord
from gaia.src.phase4.memory.retriever import MemoryRetriever
from gaia.src.phase4.memory.store import MemoryStore
from gaia.src.phase4.orchestrator import MasterOrchestrator
from gaia.src.phase4.tool_loop_detector import ToolLoopDetector
from gaia.src.phase4.browser_error_utils import add_no_retry_hint, extract_reason_fields
from .exploration_artifacts_runtime import (
    generate_gif as generate_gif_impl,
    save_screenshot_to_file as save_screenshot_to_file_impl,
    save_step_artifact_payload as save_step_artifact_payload_impl,
    setup_recording_dir as setup_recording_dir_impl,
    write_result_json as write_result_json_impl,
)
from .exploration_memory_runtime import (
    extract_domain as extract_domain_impl,
    is_login_page_with_no_elements as is_login_page_with_no_elements_impl,
    memory_context as memory_context_impl,
    record_action_memory as record_action_memory_impl,
    request_user_intervention as request_user_intervention_impl,
)
from .exploration_cache_runtime import (
    cosine_similarity as cosine_similarity_impl,
    embed_text as embed_text_impl,
    get_llm_cache_key as get_llm_cache_key_impl,
    load_llm_cache as load_llm_cache_impl,
    load_semantic_cache as load_semantic_cache_impl,
    record_exploration_summary as record_exploration_summary_impl,
    resolve_llm_cache_path as resolve_llm_cache_path_impl,
    resolve_semantic_cache_path as resolve_semantic_cache_path_impl,
    save_llm_cache as save_llm_cache_impl,
    save_semantic_cache as save_semantic_cache_impl,
    semantic_cache_lookup as semantic_cache_lookup_impl,
    semantic_cache_store as semantic_cache_store_impl,
    semantic_cache_text as semantic_cache_text_impl,
)
from .exploratory_browser_runtime import (
    capture_screenshot as capture_screenshot_impl,
    check_console_errors as check_console_errors_impl,
    get_current_url as get_current_url_impl,
)
from .exploration_ui_runtime import (
    detect_active_modal_region as detect_active_modal_region_impl,
    find_close_menu_selector as find_close_menu_selector_impl,
    find_open_menu_selector as find_open_menu_selector_impl,
    is_bbox_inside_region as is_bbox_inside_region_impl,
    is_mcp_transport_error as is_mcp_transport_error_impl,
    normalize_bbox as normalize_bbox_impl,
    recover_mcp_host as recover_mcp_host_impl,
    should_open_menu_for_action as should_open_menu_for_action_impl,
)
from .exploration_decision_runtime import (
    build_exploration_prompt as build_exploration_prompt_impl,
    decide_next_exploration_action as decide_next_exploration_action_impl,
    parse_exploration_decision as parse_exploration_decision_impl,
)
from .exploration_actions_runtime import (
    action_signature as action_signature_impl,
    auth_field_bucket as auth_field_bucket_impl,
    auth_field_needs_input as auth_field_needs_input_impl,
    auth_field_order as auth_field_order_impl,
    boost_action_priority as boost_action_priority_impl,
    build_action_for_element as build_action_for_element_impl,
    build_navigation_actions as build_navigation_actions_impl,
    build_saucedemo_item_actions as build_saucedemo_item_actions_impl,
    element_label as element_label_impl,
    enqueue_frontier_action as enqueue_frontier_action_impl,
    generate_testable_actions as generate_testable_actions_impl,
    has_login_form as has_login_form_impl,
    has_pending_inputs as has_pending_inputs_impl,
    has_tested_inputs as has_tested_inputs_impl,
    is_high_priority_element as is_high_priority_element_impl,
    is_toggle_action as is_toggle_action_impl,
    normalize_action_description as normalize_action_description_impl,
    normalize_seed_urls as normalize_seed_urls_impl,
    resolve_navigation_target as resolve_navigation_target_impl,
    select_frontier_action as select_frontier_action_impl,
    state_key as state_key_impl,
)
from .exploration_dom_runtime import (
    build_element_id as build_element_id_impl,
    determine_input_value as determine_input_value_impl,
    evaluate_selector as evaluate_selector_impl,
    fallback_selector_for_element as fallback_selector_for_element_impl,
    find_element_by_id as find_element_by_id_impl,
    find_selector_by_element_id as find_selector_by_element_id_impl,
    get_select_state as get_select_state_impl,
    get_toggle_state as get_toggle_state_impl,
    is_selector_safe as is_selector_safe_impl,
    pick_select_option as pick_select_option_impl,
)
from .exploration_validation_runtime import (
    aggregate_validation_summary as aggregate_validation_summary_impl,
    append_validation_report as append_validation_report_impl,
    create_action_failure_issue as create_action_failure_issue_impl,
    create_error_issue as create_error_issue_impl,
    create_intent_issue as create_intent_issue_impl,
    is_expected_non_bug_console_error as is_expected_non_bug_console_error_impl,
    report_console_errors as report_console_errors_impl,
    verify_action_intent as verify_action_intent_impl,
)
from .exploration_summary_runtime import (
    calculate_coverage as calculate_coverage_impl,
    call_llm_text_only as call_llm_text_only_impl,
    determine_completion_reason as determine_completion_reason_impl,
    hash_url as hash_url_impl,
    print_summary as print_summary_impl,
)

from .exploratory_models import (
    ExplorationConfig,
    ExplorationResult,
    ExplorationStep,
    ExplorationDecision,
    TestableAction,
    FoundIssue,
    IssueType,
    PageState,
    ElementState,
)
from .models import DOMElement


class _ExploratoryFilterValidationAdapter:
    """Filter validation adapter for ExploratoryAgent."""

    def __init__(self, agent: "ExploratoryAgent"):
        self.agent = agent

    def analyze_dom(self) -> List[DOMElement]:
        return self.agent._analyze_dom()

    def apply_select(self, element_id: int, value: str) -> Dict[str, Any]:
        selector = self.agent._element_full_selectors.get(element_id) or self.agent._element_selectors.get(element_id)
        ref_id = self.agent._element_ref_ids.get(element_id)
        success, error = self.agent._execute_action(
            "select",
            selector=selector or None,
            ref_id=ref_id or None,
            value=value,
        )
        meta = dict(self.agent._last_exec_meta or {})
        return {
            "success": bool(success),
            "effective": bool(meta.get("effective", success)),
            "reason_code": str(meta.get("reason_code") or ("ok" if success else "failed")),
            "reason": str(meta.get("reason") or error or ""),
            "state_change": meta.get("state_change") if isinstance(meta.get("state_change"), dict) else {},
        }

    def click_element(self, element_id: int) -> Dict[str, Any]:
        selector = self.agent._element_full_selectors.get(element_id) or self.agent._element_selectors.get(element_id)
        ref_id = self.agent._element_ref_ids.get(element_id)
        before_url = self.current_url()
        success, error = self.agent._execute_action(
            "click",
            selector=selector or None,
            ref_id=ref_id or None,
            value=None,
        )
        meta = dict(self.agent._last_exec_meta or {})
        return {
            "success": bool(success),
            "effective": bool(meta.get("effective", success)),
            "reason_code": str(meta.get("reason_code") or ("ok" if success else "failed")),
            "reason": str(meta.get("reason") or error or ""),
            "state_change": meta.get("state_change") if isinstance(meta.get("state_change"), dict) else {},
            "before_url": before_url,
            "after_url": self.current_url(),
        }

    def scroll_for_pagination(self, anchor_element_id: int) -> Dict[str, Any]:
        selector = self.agent._element_full_selectors.get(anchor_element_id) or self.agent._element_selectors.get(anchor_element_id)
        ref_id = self.agent._element_ref_ids.get(anchor_element_id)
        success, error = self.agent._execute_action(
            "scroll",
            selector=selector or None,
            ref_id=ref_id or None,
            value="bottom",
        )
        meta = dict(self.agent._last_exec_meta or {})
        return {
            "success": bool(success),
            "effective": bool(meta.get("effective", success)),
            "reason_code": str(meta.get("reason_code") or ("ok" if success else "failed")),
            "reason": str(meta.get("reason") or error or ""),
            "state_change": meta.get("state_change") if isinstance(meta.get("state_change"), dict) else {},
        }

    def wait_for_pagination_probe(self, wait_ms: int = 900) -> Dict[str, Any]:
        success, error = self.agent._execute_action(
            "wait",
            value={"timeMs": int(max(100, wait_ms))},
        )
        meta = dict(self.agent._last_exec_meta or {})
        return {
            "success": bool(success),
            "effective": bool(meta.get("effective", success)),
            "reason_code": str(meta.get("reason_code") or ("ok" if success else "failed")),
            "reason": str(meta.get("reason") or error or ""),
            "state_change": meta.get("state_change") if isinstance(meta.get("state_change"), dict) else {},
        }

    def reload_page(self, wait_ms: int = 900) -> Dict[str, Any]:
        current_url = self.current_url()
        success, error = self.agent._execute_action(
            "goto",
            url=current_url,
        )
        if wait_ms > 0:
            try:
                self.agent._execute_action("wait", value={"timeMs": int(max(100, wait_ms))})
            except Exception:
                pass
        meta = dict(self.agent._last_exec_meta or {})
        return {
            "success": bool(success),
            "effective": bool(meta.get("effective", success)),
            "reason_code": str(meta.get("reason_code") or ("ok" if success else "failed")),
            "reason": str(meta.get("reason") or error or ""),
            "state_change": meta.get("state_change") if isinstance(meta.get("state_change"), dict) else {},
        }

    def resolve_ref(self, element_id: int) -> str:
        return str(self.agent._element_ref_ids.get(element_id) or "")

    def current_url(self) -> str:
        return self.agent._get_current_url()

    def record_reason(self, code: str) -> None:
        key = str(code or "").strip()
        if not key:
            return
        counts = self.agent._validation_reason_counts
        counts[key] = int(counts.get(key, 0)) + 1
        self.agent._validation_reason_counts = counts

    def log(self, message: str) -> None:
        self.agent._log(message)

    def capture_case_attachment(self, label: str) -> Optional[Dict[str, Any]]:
        shot = self.agent._capture_screenshot()
        if not isinstance(shot, str) or not shot.strip():
            return None
        return {
            "kind": "image_base64",
            "mime": "image/png",
            "data": shot,
            "label": str(label or "").strip(),
        }


class ExploratoryAgent:
    """
    완전 자율 탐색 에이전트

    목표 없이 화면의 모든 UI 요소를 탐색하고 테스트
    버그, 에러, 이상 동작을 자동으로 감지
    """

    def __init__(
        self,
        mcp_host_url: Optional[str] = None,
        gemini_api_key: Optional[str] = None,
        llm_api_key: Optional[str] = None,
        session_id: str = "exploratory",
        config: Optional[ExplorationConfig] = None,
        log_callback: Optional[Callable[[str], None]] = None,
        screenshot_callback: Optional[Callable[[str], None]] = None,
        user_intervention_callback: Optional[Callable[[str, str], bool]] = None,
    ):
        self.mcp_host_url = (
            mcp_host_url
            or os.getenv("GAIA_MCP_HOST_URL")
            or os.getenv("MCP_HOST_URL")
            or "http://127.0.0.1:8001"
        ).rstrip("/")
        self.session_id = session_id
        self.config = config or ExplorationConfig()
        self._log_callback = log_callback
        self._screenshot_callback = screenshot_callback
        self._user_intervention_callback = user_intervention_callback

        # Vision LLM 클라이언트 초기화 (CLI에서 선택한 provider/model 우선)
        provider = (
            os.getenv("GAIA_LLM_PROVIDER")
            or os.getenv("VISION_PROVIDER")
            or "openai"
        ).strip().lower()
        if llm_api_key:
            if provider == "gemini":
                os.environ.setdefault("GEMINI_API_KEY", llm_api_key)
            else:
                os.environ.setdefault("OPENAI_API_KEY", llm_api_key)
        elif gemini_api_key and provider == "gemini":
            os.environ.setdefault("GEMINI_API_KEY", gemini_api_key)

        from gaia.src.phase4.llm_vision_client import get_vision_client

        self.llm = get_vision_client()

        # 탐색 상태 추적
        self._visited_pages: Dict[str, PageState] = {}  # url_hash -> PageState
        self._tested_elements: Set[str] = set()  # element_id
        self._action_history: List[str] = []
        self._found_issues: List[FoundIssue] = []

        # 현재 페이지 상태
        self._current_url: str = ""
        self._element_selectors: Dict[int, str] = {}  # DOM ID -> selector
        self._element_full_selectors: Dict[int, str] = {}  # DOM ID -> full selector
        self._element_ref_ids: Dict[int, str] = {}  # DOM ID -> ref id
        self._selector_to_ref_id: Dict[str, str] = {}  # selector/full_selector -> ref id
        self._active_snapshot_id: str = ""
        self._active_dom_hash: str = ""
        self._active_snapshot_epoch: int = 0
        self._active_scoped_container_ref: str = ""
        self._last_container_source_summary: Dict[str, int] = {}
        self._last_context_snapshot: Dict[str, Any] = {}
        self._last_role_snapshot: Dict[str, Any] = {}
        self._active_modal_region: Optional[Dict[str, float]] = None
        self._last_exec_meta: Dict[str, Any] = {}
        self._action_attempts: Dict[
            str, int
        ] = {}  # url_hash:element_id:action_type -> count
        self._action_frontier: List[Dict[str, str]] = []
        self._action_frontier_set: Set[str] = set()
        self._state_action_history: Dict[str, Set[str]] = {}
        self._current_state_key: Optional[str] = None
        self._toggle_action_history: Dict[str, int] = {}
        self._seed_urls: List[str] = []

        # LLM 응답 캐시
        self._llm_cache: Dict[str, str] = {}
        self._llm_cache_path = self._resolve_llm_cache_path()
        self._load_llm_cache()

        # LLM 시맨틱 캐시
        self._semantic_cache: List[Dict[str, object]] = []
        self._semantic_cache_path = self._resolve_semantic_cache_path()
        self._load_semantic_cache()

        # 실행 기억(KB)
        self._memory_store = MemoryStore(enabled=True)
        self._memory_retriever = MemoryRetriever(self._memory_store)
        self._memory_episode_id: Optional[int] = None
        self._memory_domain: str = ""
        self._runtime_phase: str = "COLLECT"
        self._progress_counter: int = 0
        self._no_progress_counter: int = 0
        self._auth_completed_fields: Set[str] = set()
        self._tool_loop_detector = ToolLoopDetector(
            warning_threshold=2,
            critical_threshold=3,
            ping_pong_warning_threshold=3,
            ping_pong_critical_threshold=4,
        )
        self._forced_completion_reason: str = ""
        self._auth_intervention_asked: bool = False
        self._auth_input_values: Dict[str, str] = {}
        self._validation_checks: List[Dict[str, Any]] = []
        self._validation_summary: Dict[str, Any] = {}
        self._verification_report: Dict[str, Any] = {}
        self._validation_reason_counts: Dict[str, int] = {}

    def _log(self, message: str):
        """로그 출력"""
        print(f"[ExploratoryAgent] {message}")
        if self._log_callback:
            self._log_callback(message)

    @staticmethod
    def _fatal_llm_reason(raw_reason: str) -> Optional[str]:
        text = (raw_reason or "").lower()
        if not text:
            return None
        if "insufficient_quota" in text:
            return (
                "LLM 호출 중단: OpenAI API quota/billing 부족 "
                "(429 insufficient_quota)."
            )
        if "invalid_api_key" in text or "incorrect api key" in text:
            return "LLM 호출 중단: API 키가 유효하지 않습니다."
        if "authentication" in text or "unauthorized" in text or "401" in text:
            return "LLM 호출 중단: 인증 오류(401/Unauthorized)."
        if "forbidden" in text or "403" in text:
            return "LLM 호출 중단: 권한 오류(403 Forbidden)."
        if "empty_response_from_codex_exec" in text or "empty_response_from_model" in text:
            return (
                "LLM 호출 중단: 모델 응답이 비어 있습니다. "
                "Codex CLI 버전/로그인 상태를 확인하고 다시 시도하세요."
            )
        if "failed to read prompt from stdin" in text or "not valid utf-8" in text:
            return (
                "LLM 호출 중단: Codex CLI 입력 인코딩(UTF-8) 오류입니다. "
                "최신 코드로 업데이트 후 다시 실행하세요."
            )
        if "codex exec failed" in text or "unexpected argument" in text:
            return (
                "LLM 호출 중단: Codex CLI 실행 인자/버전 오류입니다. "
                "`codex exec --help`로 옵션 호환성을 확인하세요."
            )
        return None

    def _setup_recording_dir(self, session_id: str) -> Path:
        return setup_recording_dir_impl(session_id)

    def _save_screenshot_to_file(
        self,
        screenshot_base64: str,
        screenshots_dir: Path,
        step_num: int,
        suffix: str = "",
    ) -> str:
        return save_screenshot_to_file_impl(self, screenshot_base64, screenshots_dir, step_num, suffix)

    def _save_step_artifact_payload(
        self,
        screenshots_dir: Optional[Path],
        step: ExplorationStep,
        before_path: str = "",
        after_path: str = "",
    ) -> None:
        save_step_artifact_payload_impl(self, screenshots_dir, step, before_path, after_path)

    def _write_result_json(self, result: ExplorationResult) -> Optional[str]:
        return write_result_json_impl(self, result)

    def _generate_gif(self, screenshots_dir: Path, output_path: Path) -> bool:
        return generate_gif_impl(self, screenshots_dir, output_path)

    def _generate_feature_description(
        self, action: Optional[TestableAction], context: str = ""
    ) -> Dict[str, str]:
        """
        액션에 대한 기능 중심 설명 생성

        Returns:
            {
                "feature_description": "로그인 기능 테스트",
                "test_scenario": "사용자 인증 플로우",
                "business_impact": "사용자가 시스템에 접근할 수 없음"
            }
        """
        if not action:
            return {
                "feature_description": "탐색 종료",
                "test_scenario": "",
                "business_impact": "",
            }

        # 액션 타입과 요소 정보를 기반으로 기능 추론
        action_type = action.action_type
        description = action.description.lower()

        # 패턴 매칭으로 기능 추론
        feature_patterns = {
            # 로그인/인증 관련
            ("login", "로그인", "sign in", "username", "password", "email"): {
                "feature": "로그인/인증 기능 테스트",
                "scenario": "사용자 인증 플로우",
                "impact": "사용자가 서비스에 접근할 수 없음",
            },
            # 회원가입 관련
            ("signup", "register", "회원가입", "create account"): {
                "feature": "회원가입 기능 테스트",
                "scenario": "신규 사용자 등록 플로우",
                "impact": "신규 사용자 유치 불가",
            },
            # 장바구니 관련
            ("cart", "add to cart", "장바구니", "basket", "remove"): {
                "feature": "장바구니 기능 테스트",
                "scenario": "상품 구매 플로우",
                "impact": "사용자가 상품을 구매할 수 없음",
            },
            # 체크아웃/결제 관련
            ("checkout", "payment", "결제", "구매", "order", "buy"): {
                "feature": "체크아웃/결제 기능 테스트",
                "scenario": "결제 프로세스",
                "impact": "매출 손실 발생",
            },
            # 검색 관련
            ("search", "검색", "find", "query"): {
                "feature": "검색 기능 테스트",
                "scenario": "상품/콘텐츠 검색 플로우",
                "impact": "사용자가 원하는 정보를 찾을 수 없음",
            },
            # 네비게이션 관련
            ("menu", "nav", "link", "back", "home", "메뉴"): {
                "feature": "네비게이션 테스트",
                "scenario": "사이트 탐색 플로우",
                "impact": "사용자 경험 저하",
            },
            # 상품 상세 관련
            ("product", "detail", "상품", "item"): {
                "feature": "상품 상세 페이지 테스트",
                "scenario": "상품 정보 확인 플로우",
                "impact": "구매 결정에 필요한 정보 부족",
            },
            # 정렬/필터 관련
            ("sort", "filter", "정렬", "필터", "dropdown"): {
                "feature": "정렬/필터 기능 테스트",
                "scenario": "상품 탐색 플로우",
                "impact": "사용자가 원하는 조건으로 검색 불가",
            },
        }

        for keywords, info in feature_patterns.items():
            if any(kw in description for kw in keywords):
                return {
                    "feature_description": info["feature"],
                    "test_scenario": info["scenario"],
                    "business_impact": info["impact"],
                }

        # 기본값: 액션 타입 기반
        default_features = {
            "click": "UI 상호작용 테스트",
            "fill": "입력 필드 테스트",
            "select": "선택 기능 테스트",
            "hover": "호버 상태 테스트",
        }

        return {
            "feature_description": default_features.get(
                action_type, f"{action_type} 액션 테스트"
            ),
            "test_scenario": "일반 UI 테스트",
            "business_impact": "사용자 경험 영향",
        }

    def _group_steps_into_scenarios(
        self, steps: List[ExplorationStep]
    ) -> List[Dict[str, Any]]:
        """
        연속된 스텝들을 테스트 시나리오로 그룹화
        """
        scenarios = []
        current_scenario = None

        for step in steps:
            scenario_name = step.test_scenario or "기타 테스트"

            if current_scenario and current_scenario["name"] == scenario_name:
                # 같은 시나리오에 추가
                current_scenario["steps"].append(step.step_number)
                if step.success:
                    current_scenario["passed"] += 1
                else:
                    current_scenario["failed"] += 1
            else:
                # 새 시나리오 시작
                if current_scenario:
                    current_scenario["result"] = (
                        "pass" if current_scenario["failed"] == 0 else "fail"
                    )
                    scenarios.append(current_scenario)

                current_scenario = {
                    "name": scenario_name,
                    "feature": step.feature_description,
                    "steps": [step.step_number],
                    "passed": 1 if step.success else 0,
                    "failed": 0 if step.success else 1,
                }

        # 마지막 시나리오 추가
        if current_scenario:
            current_scenario["result"] = (
                "pass" if current_scenario["failed"] == 0 else "fail"
            )
            scenarios.append(current_scenario)

        return scenarios

    def _resolve_llm_cache_path(self) -> str:
        return resolve_llm_cache_path_impl()

    def _resolve_semantic_cache_path(self) -> str:
        return resolve_semantic_cache_path_impl()

    def _load_llm_cache(self) -> None:
        load_llm_cache_impl(self)

    def _save_llm_cache(self) -> None:
        save_llm_cache_impl(self)

    def _load_semantic_cache(self) -> None:
        load_semantic_cache_impl(self)

    def _save_semantic_cache(self) -> None:
        save_semantic_cache_impl(self)

    @staticmethod
    def _extract_domain(url: str) -> str:
        return extract_domain_impl(url)

    def _memory_context(self) -> str:
        return memory_context_impl(self)

    def _record_action_memory(
        self,
        *,
        step_number: int,
        action_type: str,
        selector: str,
        success: bool,
        error: Optional[str],
    ) -> None:
        record_action_memory_impl(
            self,
            step_number=step_number,
            action_type=action_type,
            selector=selector,
            success=success,
            error=error,
        )

    def _record_exploration_summary(
        self,
        *,
        result: ExplorationResult,
    ) -> None:
        record_exploration_summary_impl(self, result=result)

    def _get_llm_cache_key(
        self,
        prompt: str,
        screenshot: Optional[str],
        action_signature: str,
    ) -> str:
        return get_llm_cache_key_impl(prompt, screenshot, action_signature)

    def _semantic_cache_text(
        self,
        page_state: PageState,
        testable_actions: List[TestableAction],
    ) -> str:
        return semantic_cache_text_impl(self, page_state, testable_actions)

    def _embed_text(self, text: str) -> List[float]:
        return embed_text_impl(text)

    def _cosine_similarity(self, left: List[float], right: List[float]) -> float:
        return cosine_similarity_impl(left, right)

    def _semantic_cache_lookup(
        self, text: str, action_signature: str, threshold: float = 0.95
    ) -> Optional[str]:
        return semantic_cache_lookup_impl(self, text, action_signature, threshold)

    def _semantic_cache_store(
        self, text: str, response: str, action_signature: str
    ) -> None:
        semantic_cache_store_impl(self, text, response, action_signature)

    def _is_login_page_with_no_elements(self, page_state: PageState) -> bool:
        return is_login_page_with_no_elements_impl(page_state)

    def _request_user_intervention(self, reason: str, current_url: str) -> bool:
        return request_user_intervention_impl(self, reason, current_url)

    def _infer_runtime_phase(self, page_state: PageState) -> str:
        elements = page_state.interactive_elements or []
        text_blob = " ".join(
            [
                str(getattr(e, "text", "") or "")
                + " "
                + str(getattr(e, "description", "") or "")
                + " "
                + str(getattr(e, "selector", "") or "")
                for e in elements[:300]
            ]
        ).lower()
        if any(k in text_blob for k in ("로그인", "회원가입", "password", "signin", "auth")):
            return "AUTH"
        if any(k in text_blob for k in ("완료", "성공", "applied", "saved", "completed")):
            return "VERIFY"
        if any(k in text_blob for k in ("실행", "조합", "생성", "run", "generate", "submit")):
            return "APPLY"
        if any(k in text_blob for k in ("필터", "정렬", "설정", "옵션", "filter", "sort", "option")):
            return "COMPOSE"
        return "COLLECT"

    def explore(self, start_url: str) -> ExplorationResult:
        """
        완전 자율 탐색 시작

        화면의 모든 요소를 찾아서 테스트하고, 버그를 자동으로 발견
        """
        session_id = f"exploration_{int(time.time())}"
        start_time = time.time()
        steps: List[ExplorationStep] = []

        # 녹화 설정
        screenshots_dir = None
        screenshot_paths: List[str] = []
        if self.config.enable_recording:
            screenshots_dir = self._setup_recording_dir(session_id)
            self._log(f"📹 녹화 활성화: {screenshots_dir}")

        self._log("=" * 60)
        self._log("🔍 완전 자율 탐색 모드 시작")
        self._log(f"   시작 URL: {start_url}")
        is_time_budget_mode = (
            self.config.loop_mode == "time"
            and int(self.config.time_budget_seconds or 0) > 0
        )
        if is_time_budget_mode:
            self._log(f"   실행 모드: time_budget ({int(self.config.time_budget_seconds)}s)")
        else:
            self._log(f"   최대 액션: {self.config.max_actions}")
        self._log("=" * 60)

        self._memory_domain = self._extract_domain(start_url)
        self._memory_episode_id = None
        try:
            self._memory_store.garbage_collect(retention_days=30)
            self._memory_episode_id = self._memory_store.start_episode(
                provider=(os.getenv("GAIA_LLM_PROVIDER") or "openai"),
                model=(os.getenv("GAIA_LLM_MODEL") or os.getenv("VISION_MODEL") or "unknown"),
                runtime="terminal",
                domain=self._memory_domain,
                goal_text="exploratory testing",
                url=start_url,
            )
        except Exception:
            self._memory_episode_id = None

        # 시작 URL로 이동
        self._log(f"📍 시작 URL로 이동")
        self._execute_action("goto", url=start_url)
        time.sleep(2)  # 페이지 로드 대기
        self._current_url = start_url
        self._seed_urls = self._normalize_seed_urls(start_url)

        action_count = 0
        master_orchestrator = MasterOrchestrator()

        while True:
            elapsed = time.time() - start_time
            if is_time_budget_mode:
                if elapsed >= int(self.config.time_budget_seconds):
                    self._log(
                        f"⏱️ 시간 예산 도달 ({int(self.config.time_budget_seconds)}s), 탐색을 종료합니다."
                    )
                    break
            elif action_count >= self.config.max_actions:
                break
            action_count += 1
            step_start = time.time()

            self._log(f"\n{'=' * 60}")
            if is_time_budget_mode:
                self._log(
                    f"📌 Step {action_count} (elapsed {int(elapsed)}s / {int(self.config.time_budget_seconds)}s)"
                )
            else:
                self._log(f"📌 Step {action_count}/{self.config.max_actions}")
            self._log(f"{'=' * 60}")

            # 1. 현재 페이지 상태 분석
            page_state = self._analyze_current_page()
            if not page_state:
                self._log("⚠️  페이지 분석 실패, 잠시 대기 후 재시도")
                time.sleep(2)
                page_state = self._analyze_current_page()
                if not page_state:
                    self._log("❌ 페이지 분석 실패, 탐색 중단")
                    break

            self._log(f"📊 페이지 분석 완료:")
            self._log(f"   - URL: {page_state.url}")
            self._log(
                f"   - 상호작용 가능한 요소: {len(page_state.interactive_elements)}개"
            )

            untested = [e for e in page_state.interactive_elements if not e.tested]
            self._log(f"   - 미테스트 요소: {len(untested)}개")
            self._runtime_phase = self._infer_runtime_phase(page_state)
            master_orchestrator.set_phase(self._runtime_phase)
            self._log(f"   - phase: {self._runtime_phase}")

            if self._runtime_phase == "AUTH" and not self._auth_intervention_asked:
                should_continue = self._request_user_intervention(
                    reason=(
                        "로그인 요청이 왔습니다. 어떻게 할까요? "
                        "아이디/비밀번호를 알려주거나 수동 로그인 후 계속 진행할 수 있습니다."
                    ),
                    current_url=page_state.url,
                )
                if not should_continue:
                    self._log("🛑 인증 사용자 입력 대기 상태로 실행을 중지합니다.")
                    break
                self._auth_intervention_asked = True
            elif self._runtime_phase != "AUTH":
                self._auth_intervention_asked = False

            # 로그인 페이지 감지 및 사용자 개입 요청
            if self._is_login_page_with_no_elements(page_state):
                self._log(
                    "🔐 로그인 페이지 감지됨 (요소 접근 불가 - cross-origin iframe 또는 특수 인증)"
                )
                if not self._request_user_intervention(
                    reason=(
                        "로그인이 필요합니다. 로그인 요청왔는데 어떻게 할까요? "
                        "아이디 비밀번호를 알려주세요."
                    ),
                    current_url=page_state.url,
                ):
                    self._log("탐색 중단 (auth_required)")
                    break

                # 사용자가 로그인 완료 후 페이지 재분석
                self._log("🔄 로그인 후 페이지 재분석...")
                time.sleep(3)
                page_state = self._analyze_current_page()
                if page_state:
                    self._log(
                        f"✅ 로그인 후 {len(page_state.interactive_elements)}개 요소 발견"
                    )
                else:
                    self._log("⚠️  페이지 재분석 실패")
                    break

            # 2. 스크린샷 캡처
            screenshot = self._capture_screenshot()

            # 3. 콘솔 에러 확인
            console_errors = self._check_console_errors()
            if console_errors:
                self._log(f"⚠️  콘솔 에러 발견: {len(console_errors)}개")
                self._report_console_errors(console_errors, screenshot)

            # 4. LLM에게 다음 액션 결정 요청
            decision = self._decide_next_exploration_action(
                page_state=page_state,
                screenshot=screenshot,
                action_count=action_count,
            )

            self._log(f"🤖 LLM 결정:")
            self._log(f"   - 계속 탐색: {decision.should_continue}")
            if decision.selected_action:
                self._log(f"   - 액션: {decision.selected_action.action_type}")
                self._log(f"   - 대상: {decision.selected_action.description}")
            self._log(f"   - 이유: {decision.reasoning}")

            # 5. 탐색 종료 판단
            if not decision.should_continue:
                self._log(f"✅ 탐색 완료: {decision.reasoning}")

                step = ExplorationStep(
                    step_number=action_count,
                    url=page_state.url,
                    decision=decision,
                    success=True,
                    duration_ms=int((time.time() - step_start) * 1000),
                )
                steps.append(step)
                break

            # 6. 액션이 없으면 탐색 완료
            if not decision.selected_action:
                self._log("✅ 더 이상 테스트할 요소가 없습니다")

                step = ExplorationStep(
                    step_number=action_count,
                    url=page_state.url,
                    decision=decision,
                    success=True,
                    duration_ms=int((time.time() - step_start) * 1000),
                )
                steps.append(step)
                break

            # 7. 스크린샷 (액션 실행 직전) - GIF용으로 저장
            action_for_step = decision.selected_action
            selector_for_step = ""
            if action_for_step and action_for_step.action_type != "navigate":
                selector_for_step = (
                    self._find_selector_by_element_id(
                        action_for_step.element_id,
                        page_state,
                    )
                    or ""
                )
            loop_tool = self._loop_guard_tool_name(action_for_step, page_state)
            loop_params = {
                "url_hash": page_state.url_hash,
                "phase": self._runtime_phase,
            }
            loop_guard = self._tool_loop_detector.check(loop_tool, loop_params)
            if loop_guard.stuck:
                self._log(
                    f"🛑 loop_guard({loop_guard.detector}/{loop_guard.level}): {loop_guard.message}"
                )
                if loop_guard.level == "critical":
                    shifted = self._force_context_shift(page_state, start_url)
                    self._tool_loop_detector.record(
                        loop_tool,
                        loop_params,
                        progress=bool(shifted),
                        result_hash="context_shift" if shifted else loop_guard.detector,
                    )
                    if shifted:
                        continue
                alt_action = self._select_loop_escape_action(
                    page_state,
                    action_for_step if action_for_step else TestableAction(
                        element_id="",
                        action_type="click",
                        description="",
                        priority=0.0,
                    ),
                )
                if alt_action is not None:
                    self._log(
                        f"↪️ loop_guard 대체 액션: {alt_action.action_type} / {alt_action.description}"
                    )
                    decision.selected_action = alt_action
                    action_for_step = alt_action
                    selector_for_step = (
                        self._find_selector_by_element_id(
                            action_for_step.element_id,
                            page_state,
                        )
                        or ""
                    )
                    loop_params = {
                        "url_hash": page_state.url_hash,
                        "phase": self._runtime_phase,
                    }
                    loop_tool = self._loop_guard_tool_name(action_for_step, page_state)

            # 7. 스크린샷 (액션 실행 직전) - GIF용으로 저장
            screenshot_before = screenshot
            before_path = ""
            if screenshots_dir and screenshot_before:
                before_path = self._save_screenshot_to_file(
                    screenshot_before,
                    screenshots_dir,
                    action_count,
                    suffix="before",
                )
                if before_path:
                    screenshot_paths.append(before_path)

            # 8. 액션 실행
            pre_action_phase = str(self._runtime_phase or "").upper()
            auth_submit_trace = bool(
                decision.selected_action
                and decision.selected_action.action_type == "click"
                and pre_action_phase == "AUTH"
                and any(
                    token in str(decision.selected_action.description or "").lower()
                    for token in ("로그인", "login", "sign in", "회원가입", "sign up", "register")
                )
            )
            auth_submit_trace_enabled = str(os.getenv("GAIA_TRACE_AUTH_SUBMIT", "0")).strip().lower() in {
                "1", "true", "yes", "on"
            }
            action_started_at = time.perf_counter()
            success, error, issues = self._execute_exploration_action(
                decision=decision,
                page_state=page_state,
            )
            action_elapsed_ms = int((time.perf_counter() - action_started_at) * 1000)
            if auth_submit_trace and auth_submit_trace_enabled:
                self._log(
                    f"⏱️ auth_submit trace: execute_action={action_elapsed_ms}ms "
                    f"success={success} reason_code={self._last_exec_meta.get('reason_code')}"
                )
            reason_code = str(
                self._last_exec_meta.get("reason_code")
                or ("ok" if success else "unknown_error")
            )
            progress = bool(success and not issues)
            if progress:
                self._progress_counter += 1
                self._no_progress_counter = 0
            else:
                self._no_progress_counter += 1
            self._tool_loop_detector.record(
                loop_tool,
                loop_params,
                progress=progress,
                result_hash=str(
                    reason_code if reason_code else ("ok" if progress else "no_progress")
                ),
            )
            master_orchestrator.record_progress(
                changed=progress,
                signal={
                    "phase": self._runtime_phase,
                    "step": action_count,
                    "issues": len(issues or []),
                },
            )
            master_directive = master_orchestrator.next_directive(auth_required=False)
            if master_directive.kind == "handoff" and master_directive.reason == "no_progress":
                if self.config.non_stop_mode:
                    self._log(
                        "🧭 no_progress 감지: 무중단 모드로 사용자 개입 없이 전략 전환을 계속합니다."
                    )
                else:
                    should_continue = self._request_user_intervention(
                        reason=(
                            "상태 변화가 연속으로 감지되지 않았습니다. "
                            "브라우저에서 수동 전환 후 계속할지 선택해 주세요."
                        ),
                        current_url=page_state.url,
                    )
                    if not should_continue:
                        self._log("사용자 요청으로 탐색을 중단합니다.")
                        break
            selector_for_memory = ""
            if decision.selected_action:
                if decision.selected_action.action_type == "navigate":
                    selector_for_memory = decision.selected_action.element_id
                else:
                    selector_for_memory = (
                        self._find_selector_by_element_id(
                            decision.selected_action.element_id,
                            page_state,
                        )
                        or ""
                    )
                self._record_action_memory(
                    step_number=action_count,
                    action_type=decision.selected_action.action_type,
                    selector=selector_for_memory,
                    success=success,
                    error=error,
                )

            # 9. 액션 결과 기록
            self._action_history.append(
                f"Step {action_count}: {decision.selected_action.action_type} on {decision.selected_action.description}"
            )

            # 9-1. 액션 시도 횟수 기록
            attempt_key = (
                f"{page_state.url_hash}:{decision.selected_action.element_id}"
                f":{decision.selected_action.action_type}"
            )
            self._action_attempts[attempt_key] = (
                self._action_attempts.get(attempt_key, 0) + 1
            )

            # 9-2. 토글 액션 히스토리 기록
            if self._is_toggle_action(decision.selected_action):
                toggle_key = (
                    f"{page_state.url_hash}:{decision.selected_action.element_id}:"
                    f"{self._normalize_action_description(decision.selected_action)}"
                )
                self._toggle_action_history[toggle_key] = (
                    self._toggle_action_history.get(toggle_key, 0) + 1
                )

            # 9-3. 상태별 액션 기록
            if self._current_state_key:
                transient_failure_codes = {
                    "request_exception",
                    "stale_snapshot",
                    "snapshot_not_found",
                }
                should_mark_state_action = success or (
                    reason_code not in transient_failure_codes
                )
                if should_mark_state_action:
                    self._state_action_history.setdefault(
                        self._current_state_key, set()
                    ).add(
                        f"{decision.selected_action.element_id}:{decision.selected_action.action_type}"
                    )

            # 10. 요소를 테스트 완료로 마킹
            if decision.selected_action:
                self._tested_elements.add(decision.selected_action.element_id)

            # 11. 스크린샷 (액션 실행 후) - 결과 확인용 (GIF에는 포함 안함)
            time.sleep(1)  # UI 변화 대기
            screenshot_started_at = time.perf_counter()
            screenshot_after = self._capture_screenshot()
            screenshot_elapsed_ms = int((time.perf_counter() - screenshot_started_at) * 1000)
            after_path = ""
            if screenshots_dir and screenshot_after:
                after_path = self._save_screenshot_to_file(
                    screenshot_after,
                    screenshots_dir,
                    action_count,
                    suffix="after",
                )

            # 12. 새로운 페이지 발견 확인
            current_url_started_at = time.perf_counter()
            new_url = self._get_current_url()
            current_url_elapsed_ms = int((time.perf_counter() - current_url_started_at) * 1000)
            new_pages = 1 if new_url != page_state.url else 0
            if new_pages:
                self._log(f"🆕 새 페이지 발견: {new_url}")

            after_state_started_at = time.perf_counter()
            after_state = self._analyze_current_page()
            after_state_elapsed_ms = int((time.perf_counter() - after_state_started_at) * 1000)
            if auth_submit_trace and auth_submit_trace_enabled:
                self._log(
                    "⏱️ auth_submit trace: "
                    f"capture_screenshot={screenshot_elapsed_ms}ms "
                    f"get_current_url={current_url_elapsed_ms}ms "
                    f"analyze_current_page={after_state_elapsed_ms}ms"
                )
            if (
                not success
                and decision.selected_action
                and decision.selected_action.action_type == "click"
                and pre_action_phase == "AUTH"
                and after_state
            ):
                err_lower = str(error or "").lower()
                desc_lower = str(decision.selected_action.description or "").lower()
                is_auth_submit_timeout = (
                    ("로그인" in desc_lower or "login" in desc_lower)
                    and "read timed out" in err_lower
                )
                auth_resolved = (
                    str(self._runtime_phase or "").upper() != "AUTH"
                    or not self._has_login_form(after_state)
                )
                if is_auth_submit_timeout and auth_resolved:
                    success = True
                    error = None
                    self._last_exec_meta = dict(self._last_exec_meta or {})
                    self._last_exec_meta["reason_code"] = "ok"
                    self._last_exec_meta["reason"] = "auth_submit_timeout_but_effective"
                    self._last_exec_meta["effective"] = True
                    issues = [
                        issue
                        for issue in issues
                        if "액션 실행 실패" not in str(getattr(issue, "title", ""))
                    ]
                    self._log("♻️ 로그인 제출 timeout 발생했지만 후속 상태 검증으로 성공 처리")
            if success and decision.selected_action and after_state:
                expected_input = None
                before_select_state = None
                before_toggle_state = None
                selector = None
                if decision.selected_action.action_type == "fill":
                    expected_input = self._determine_input_value(
                        decision.selected_action, decision.input_values
                    )
                if decision.selected_action.action_type in ["select", "click"]:
                    selector = self._find_selector_by_element_id(
                        decision.selected_action.element_id, page_state
                    )
                if decision.selected_action.action_type == "select":
                    before_select_state = self._get_select_state(selector)
                if decision.selected_action.action_type == "click":
                    before_toggle_state = self._get_toggle_state(selector)
                intent_ok, intent_reason = self._verify_action_intent(
                    action=decision.selected_action,
                    before_state=page_state,
                    after_state=after_state,
                    before_url=page_state.url,
                    after_url=new_url,
                    screenshot_before=screenshot_before,
                    screenshot_after=screenshot_after,
                    expected_input=expected_input,
                    before_select_state=before_select_state,
                    before_toggle_state=before_toggle_state,
                )
                if not intent_ok and intent_reason:
                    # select 액션은 화면/URL 변화가 미세해 의도 검증 오탐이 자주 발생한다.
                    # 범용 탐색 품질을 위해 기본 이슈 기록에서 제외하고 실제 오류 신호(console/http)만 반영한다.
                    if decision.selected_action.action_type != "select":
                        issues.append(
                            self._create_intent_issue(
                                action=decision.selected_action,
                                url=page_state.url,
                                reason=intent_reason,
                                screenshot_before=screenshot_before,
                                screenshot_after=screenshot_after,
                            )
                        )
                if (
                    success
                    and decision.selected_action.action_type == "fill"
                    and str(self._runtime_phase or "").upper() == "AUTH"
                    and intent_ok
                ):
                    bucket = self._auth_field_bucket(decision.selected_action)
                    if bucket:
                        self._auth_completed_fields.add(bucket)
                if success and decision.selected_action and intent_ok:
                    acted = self._find_element_by_id(decision.selected_action.element_id, page_state)
                    acted_container_ref = str(getattr(acted, "container_ref_id", "") or "").strip()
                    acted_container_source = str(getattr(acted, "container_source", "") or "").strip()
                    if (
                        acted_container_ref
                        and acted_container_source == "semantic-first"
                        and str(decision.selected_action.action_type or "").strip().lower() in {"click", "select"}
                    ):
                        self._active_scoped_container_ref = acted_container_ref
                    else:
                        self._active_scoped_container_ref = ""

            step_validation_checks: List[Dict[str, Any]] = []
            if (
                success
                and decision.selected_action
                and str(decision.selected_action.action_type or "").strip().lower() == "select"
            ):
                goal_text = (
                    str(decision.selected_action.description or "").strip()
                    or str(decision.reasoning or "").strip()
                    or "filter validation"
                )
                report = self._run_filter_semantic_validation(goal_text)
                step_validation_checks = self._append_validation_report(report, action_count)

            # 12-1. 기능 중심 설명 생성
            feature_info = self._generate_feature_description(
                decision.selected_action if decision else None
            )

            # 13. Step 결과 저장
            step = ExplorationStep(
                step_number=action_count,
                url=page_state.url,
                decision=decision,
                success=success,
                error_message=error,
                feature_description=feature_info["feature_description"],
                test_scenario=feature_info["test_scenario"],
                business_impact=feature_info["business_impact"],
                issues_found=issues,
                validation_checks=step_validation_checks,
                new_pages_found=new_pages,
                screenshot_before=screenshot_before,
                screenshot_after=screenshot_after,
                duration_ms=int((time.time() - step_start) * 1000),
            )
            steps.append(step)
            self._save_step_artifact_payload(
                screenshots_dir,
                step,
                before_path=before_path,
                after_path=after_path,
            )

            # 14. 발견된 이슈 추가
            self._found_issues.extend(issues)
            if issues:
                self._log(f"🚨 이슈 발견: {len(issues)}개")
                for issue in issues:
                    self._log(f"   - [{issue.severity}] {issue.title}")

            # 15. 실패한 경우 계속 진행할지 판단
            if not success and error:
                self._log(f"⚠️  액션 실패: {error}")
                reason_code = str(self._last_exec_meta.get("reason_code") or "unknown_error")
                recovery = self._memory_retriever.retrieve_recovery(
                    domain=self._memory_domain,
                    goal_text="exploratory testing",
                    reason_code=reason_code,
                    limit=2,
                )
                recovery_text = self._memory_retriever.format_for_prompt(recovery, max_items=2)
                if recovery_text:
                    self._log(f"🧠 복구 힌트({reason_code}): {recovery_text}")
                # 실패해도 계속 진행 (다른 요소 테스트)

            # 다음 스텝 전 대기
            time.sleep(0.5)

        # 탐색 완료
        duration = time.time() - start_time
        completion_reason = self._determine_completion_reason(
            action_count,
            steps,
            duration_seconds=duration,
        )

        # GIF 생성 (녹화가 활성화된 경우)
        gif_path = None
        if screenshots_dir and self.config.generate_gif and screenshot_paths:
            gif_filename = screenshots_dir.parent / f"{session_id}.gif"
            if self._generate_gif(screenshots_dir, gif_filename):
                gif_path = str(gif_filename)

        # 테스트 시나리오 그룹화
        test_scenarios = self._group_steps_into_scenarios(steps)
        if self._verification_report:
            self._verification_report = dict(self._verification_report)
        else:
            self._verification_report = {}
        self._verification_report["reason_code_summary"] = dict(self._validation_reason_counts or {})
        self._verification_report["container_source_summary"] = dict(self._last_container_source_summary or {})
        self._verification_report["active_scoped_container_ref"] = str(self._active_scoped_container_ref or "")

        # 최종 결과 생성
        result = ExplorationResult(
            session_id=session_id,
            start_url=start_url,
            total_actions=action_count,
            total_pages_visited=len(self._visited_pages),
            total_elements_tested=len(self._tested_elements),
            coverage=self._calculate_coverage(),
            issues_found=self._found_issues,
            steps=steps,
            completion_reason=completion_reason,
            recording_gif_path=gif_path,
            screenshots_dir=str(screenshots_dir) if screenshots_dir else None,
            test_scenarios_summary=test_scenarios,
            validation_summary=dict(self._validation_summary or {}),
            validation_checks=list(self._validation_checks or []),
            verification_report=dict(self._verification_report or {}),
            completed_at=datetime.now(),
            duration_seconds=duration,
        )
        self._record_exploration_summary(result=result)
        result_json_path = self._write_result_json(result)
        if result_json_path:
            self._log(f"🧾 결과 JSON 저장: {result_json_path}")

        # 결과 요약 출력
        self._print_summary(result)

        return result

    def _analyze_current_page(self) -> Optional[PageState]:
        """현재 페이지의 모든 상호작용 가능한 요소 분석"""
        try:
            # URL 가져오기
            current_url = self._get_current_url()
            url_hash = self._hash_url(current_url)

            # DOM 분석
            dom_elements = self._analyze_dom()
            # 요소가 0개라도 PageState를 반환 (사용자 개입 감지를 위해)
            if not dom_elements:
                dom_elements = []
            self._active_modal_region = self._detect_active_modal_region(dom_elements)
            if self._active_modal_region:
                self._log("🪟 모달 컨텍스트 감지: 모달 영역 내부 요소만 후보화")

            # AutoCrawler 방식: 중요 요소만 필터링 (광고/푸터 제외)
            interactive_elements = []
            for idx, el in enumerate(dom_elements):
                if not bool(getattr(el, "is_visible", True)):
                    continue
                if not bool(getattr(el, "is_enabled", True)):
                    continue

                # 클릭 가능하거나 입력 가능한 요소만
                is_interactive = el.tag in [
                    "button",
                    "a",
                    "input",
                    "select",
                    "textarea",
                ] or el.role in ["button", "link", "tab", "menuitem"]

                if not is_interactive:
                    continue

                if self._active_modal_region and not self._is_bbox_inside_region(
                    el.bounding_box, self._active_modal_region
                ):
                    continue

                # 광고/푸터/불필요한 요소 제외
                text_lower = el.text.lower() if el.text else ""
                selector_lower = self._element_selectors.get(idx, "").lower()

                # 제외할 키워드
                exclude_keywords = [
                    "advertisement",
                    "ad-",
                    "adsbygoogle",
                    "google_ads",
                    "footer",
                    "cookie",
                    "privacy",
                    "terms",
                    "share",
                    "facebook",
                    "twitter",
                    "instagram",
                    "광고",
                    "공유",
                    "쿠키",
                    "개인정보",
                ]

                should_exclude = any(
                    keyword in text_lower or keyword in selector_lower
                    for keyword in exclude_keywords
                )

                if should_exclude:
                    continue

                ref_id = str(self._element_ref_ids.get(idx) or "").strip()
                if not ref_id:
                    continue

                selector = self._element_full_selectors.get(
                    idx
                ) or self._element_selectors.get(idx, "")
                element_id = self._build_element_id(url_hash, el, selector)
                tested = element_id in self._tested_elements

                interactive_elements.append(
                    ElementState(
                        element_id=element_id,
                        ref_id=ref_id,
                        tag=el.tag,
                        text=el.text,
                        selector=selector,
                        role=el.role,
                        type=el.type,
                        aria_label=el.aria_label,
                        title=el.title,
                        href=el.href,
                        placeholder=el.placeholder,
                        bounding_box=el.bounding_box,
                        options=el.options,
                        container_name=el.container_name,
                        container_role=el.container_role,
                        container_ref_id=el.container_ref_id,
                        container_source=el.container_source,
                        context_text=el.context_text,
                        group_action_labels=el.group_action_labels,
                        role_ref_role=el.role_ref_role,
                        role_ref_name=el.role_ref_name,
                        role_ref_nth=el.role_ref_nth,
                        tested=tested,
                    )
                )

            # AutoCrawler 최적화: 최대 60개로 제한 (우선순위: 중요 요소 우선)
            if len(interactive_elements) > 60:
                high_priority = [
                    e for e in interactive_elements if self._is_high_priority_element(e)
                ]
                remaining = [e for e in interactive_elements if e not in high_priority]
                interactive_elements = (
                    high_priority + remaining[: max(0, 60 - len(high_priority))]
                )
                self._log(
                    "⚡ 요소 샘플링: "
                    f"{len(high_priority) + len(remaining)}개 → {len(interactive_elements)}개"
                )

            # PageState 생성
            page_state = PageState(
                url=current_url,
                url_hash=url_hash,
                interactive_elements=interactive_elements,
            )

            # 방문 기록 업데이트
            if url_hash in self._visited_pages:
                existing = self._visited_pages[url_hash]
                existing.visit_count += 1
                existing.last_visited_at = datetime.now()
                existing.interactive_elements = interactive_elements
            else:
                self._visited_pages[url_hash] = page_state

            return page_state

        except Exception as e:
            self._log(f"페이지 분석 실패: {e}")
            return None

    def _analyze_dom(self, scope_container_ref_id: Optional[str] = None) -> List[DOMElement]:
        """MCP Host를 통해 DOM 분석"""
        scoped_ref = str(
            scope_container_ref_id
            or self._active_scoped_container_ref
            or ""
        ).strip()
        payload = {
            "action": "browser_snapshot",
            "params": {
                "session_id": self.session_id,
                "scope_container_ref_id": scoped_ref,
            },
        }
        try:
            response = None
            last_exc: Optional[Exception] = None
            for attempt in range(2):
                try:
                    from gaia.src.phase4.mcp_local_dispatch_runtime import execute_mcp_action

                    response = execute_mcp_action(
                        self.mcp_host_url,
                        action="browser_snapshot",
                        params=dict(payload.get("params") or {}),
                        timeout=(5, 25),
                    )
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    if (
                        attempt == 0
                        and self._is_mcp_transport_error(str(exc))
                        and self._recover_mcp_host(context="browser_snapshot")
                    ):
                        continue
                    raise
            if response is None and last_exc is not None:
                raise last_exc
            if hasattr(response, "json"):
                try:
                    data = response.json()
                except Exception:
                    data = {"error": response.text or "invalid_json_response"}
            else:
                data = response.payload or {"error": response.text or "invalid_json_response"}

            if response.status_code >= 400:
                detail = data.get("detail") or data.get("error") or getattr(response, "text", "") or "HTTP error"
                self._log(f"DOM 분석 오류: HTTP {response.status_code} - {detail}")
                return []

            if "error" in data:
                self._log(f"DOM 분석 오류: {data['error']}")
                return []

            raw_elements = data.get("elements", []) or data.get("dom_elements", [])
            raw_elements_by_ref = data.get("elements_by_ref")
            if not raw_elements and scoped_ref:
                self._active_scoped_container_ref = ""
                return self._analyze_dom(scope_container_ref_id="")

            # 셀렉터 맵 초기화
            self._element_selectors = {}
            self._element_full_selectors = {}
            self._element_ref_ids = {}
            self._selector_to_ref_id = {}
            self._active_snapshot_id = str(data.get("snapshot_id") or "")
            self._active_dom_hash = str(data.get("dom_hash") or "")
            self._active_snapshot_epoch = int(data.get("epoch") or 0)
            if str(data.get("scope_container_ref_id") or "").strip():
                self._active_scoped_container_ref = str(data.get("scope_container_ref_id") or "").strip()
            self._last_context_snapshot = (
                data.get("context_snapshot") if isinstance(data.get("context_snapshot"), dict) else {}
            )
            self._last_role_snapshot = (
                data.get("role_snapshot") if isinstance(data.get("role_snapshot"), dict) else {}
            )

            if isinstance(raw_elements_by_ref, dict):
                for rid, meta in raw_elements_by_ref.items():
                    ref_key = str(rid or "").strip()
                    if not ref_key or not isinstance(meta, dict):
                        continue
                    selector = str(meta.get("selector") or "").strip()
                    full_selector = str(meta.get("full_selector") or "").strip()
                    if selector:
                        self._selector_to_ref_id.setdefault(selector, ref_key)
                    if full_selector:
                        self._selector_to_ref_id.setdefault(full_selector, ref_key)

            # DOMElement로 변환
            elements = []
            for idx, el in enumerate(raw_elements):
                attrs = el.get("attributes", {})
                disabled_attr = attrs.get("disabled")
                disabled_flag = (
                    disabled_attr is not None
                    and str(disabled_attr).strip().lower() not in {"false", "0", "none"}
                )
                aria_disabled_flag = str(attrs.get("aria-disabled") or "").strip().lower() == "true"
                gaia_disabled_flag = str(attrs.get("gaia-disabled") or "").strip().lower() == "true"
                is_enabled = not (disabled_flag or aria_disabled_flag or gaia_disabled_flag)

                # 셀렉터 저장
                selector = el.get("selector", "")
                full_selector = el.get("full_selector") or selector
                ref_id = el.get("ref_id") or el.get("ref") or ""
                if selector:
                    self._element_selectors[idx] = selector
                if full_selector:
                    self._element_full_selectors[idx] = full_selector
                if isinstance(ref_id, str) and ref_id:
                    self._element_ref_ids[idx] = ref_id
                    if selector:
                        self._selector_to_ref_id[selector] = ref_id
                    if full_selector:
                        self._selector_to_ref_id[full_selector] = ref_id

                elements.append(
                    DOMElement(
                        id=idx,
                        tag=el.get("tag", ""),
                        text=el.get("text", "")[:100],
                        role=attrs.get("role"),
                        type=attrs.get("type"),
                        placeholder=attrs.get("placeholder"),
                        aria_label=attrs.get("aria-label"),
                        aria_modal=attrs.get("aria-modal"),
                        title=attrs.get("title"),
                        class_name=attrs.get("class"),
                        href=attrs.get("href"),
                        bounding_box=el.get("bounding_box"),
                        options=attrs.get("options"),
                        selected_value=str(attrs.get("selected_value") or ""),
                        container_name=attrs.get("container_name"),
                        container_role=attrs.get("container_role"),
                        container_ref_id=attrs.get("container_ref_id") or attrs.get("container_dom_ref"),
                        container_source=attrs.get("container_source"),
                        context_text=attrs.get("context_text"),
                        group_action_labels=attrs.get("group_action_labels"),
                        is_visible=bool(el.get("is_visible", True)),
                        is_enabled=is_enabled,
                    )
                )

            source_summary: Dict[str, int] = {}
            for item in elements:
                source = str(getattr(item, "container_source", None) or "").strip()
                if not source:
                    continue
                source_summary[source] = int(source_summary.get(source, 0)) + 1
            self._last_container_source_summary = source_summary

            return elements

        except Exception as e:
            self._log(f"DOM 분석 실패: {e}")
            return []

    @staticmethod
    def _is_mcp_transport_error(error_text: str) -> bool:
        return is_mcp_transport_error_impl(error_text)

    def _recover_mcp_host(self, *, context: str) -> bool:
        return recover_mcp_host_impl(self, context=context)

    def _normalize_bbox(self, bbox: Optional[dict]) -> Optional[Tuple[float, float, float, float]]:
        return normalize_bbox_impl(bbox)

    def _detect_active_modal_region(
        self, dom_elements: List[DOMElement]
    ) -> Optional[Dict[str, float]]:
        return detect_active_modal_region_impl(self, dom_elements)

    def _is_bbox_inside_region(
        self,
        bbox: Optional[dict],
        region: Dict[str, float],
    ) -> bool:
        return is_bbox_inside_region_impl(self, bbox, region)

    def _capture_screenshot(self) -> Optional[str]:
        return capture_screenshot_impl(self)

    def _check_console_errors(self) -> List[str]:
        return check_console_errors_impl(self)

    def _get_current_url(self) -> str:
        return get_current_url_impl(self)

    def _decide_next_exploration_action(
        self,
        page_state: PageState,
        screenshot: Optional[str],
        action_count: int,
    ) -> ExplorationDecision:
        return decide_next_exploration_action_impl(
            self, page_state, screenshot, action_count
        )

    def _generate_testable_actions(self, page_state: PageState) -> List[TestableAction]:
        return generate_testable_actions_impl(self, page_state)

    def _enqueue_frontier_action(
        self,
        page_state: PageState,
        action: TestableAction,
    ) -> None:
        enqueue_frontier_action_impl(self, page_state, action)

    def _has_pending_inputs(self, page_state: PageState) -> bool:
        return has_pending_inputs_impl(self, page_state)

    def _has_tested_inputs(self, page_state: PageState) -> bool:
        return has_tested_inputs_impl(self, page_state)

    def _has_login_form(self, page_state: PageState) -> bool:
        return has_login_form_impl(self, page_state)

    def _auth_field_order(self, bucket: Optional[str]) -> int:
        return auth_field_order_impl(self, bucket)

    def _auth_field_bucket(self, action: TestableAction) -> Optional[str]:
        return auth_field_bucket_impl(self, action)

    def _auth_field_needs_input(
        self,
        action: TestableAction,
        page_state: PageState,
    ) -> bool:
        return auth_field_needs_input_impl(self, action, page_state)

    def _is_high_priority_element(self, element: ElementState) -> bool:
        return is_high_priority_element_impl(self, element)

    def _boost_action_priority(self, action: TestableAction) -> TestableAction:
        return boost_action_priority_impl(self, action)

    def _normalize_seed_urls(self, start_url: str) -> List[str]:
        return normalize_seed_urls_impl(self, start_url)

    def _build_navigation_actions(self, page_state: PageState) -> List[TestableAction]:
        return build_navigation_actions_impl(self, page_state)

    def _build_saucedemo_item_actions(
        self,
        page_state: PageState,
        seen: Set[str],
    ) -> List[TestableAction]:
        return build_saucedemo_item_actions_impl(self, page_state, seen)

    def _resolve_navigation_target(self, element_id: str, current_url: str) -> str:
        return resolve_navigation_target_impl(self, element_id, current_url)

    def _element_label(self, element: ElementState) -> str:
        return element_label_impl(self, element)

    def _action_signature(self, actions: List[TestableAction]) -> str:
        return action_signature_impl(self, actions)

    def _normalize_action_description(self, action: TestableAction) -> str:
        return normalize_action_description_impl(self, action)

    def _build_action_for_element(
        self, element: ElementState, action_type: str
    ) -> TestableAction:
        return build_action_for_element_impl(self, element, action_type)

    def _state_key(self, page_state: PageState, actions: List[TestableAction]) -> str:
        return state_key_impl(self, page_state, actions)

    def _is_toggle_action(self, action: TestableAction) -> bool:
        return is_toggle_action_impl(self, action)

    def _select_frontier_action(
        self,
        page_state: PageState,
        testable_actions: List[TestableAction],
    ) -> Optional[TestableAction]:
        return select_frontier_action_impl(self, page_state, testable_actions)

    def _build_exploration_prompt(
        self,
        page_state: PageState,
        testable_actions: List[TestableAction],
        action_count: int,
        memory_context: str = "",
    ) -> str:
        return build_exploration_prompt_impl(
            self, page_state, testable_actions, action_count, memory_context
        )

    def _parse_exploration_decision(
        self,
        response_text: str,
        testable_actions: List[TestableAction],
    ) -> ExplorationDecision:
        return parse_exploration_decision_impl(self, response_text, testable_actions)

    def _execute_exploration_action(
        self,
        decision: ExplorationDecision,
        page_state: PageState,
    ) -> tuple[bool, Optional[str], List[FoundIssue]]:
        """탐색 액션 실행 및 이슈 감지"""

        if not decision.selected_action:
            return True, None, []

        action = decision.selected_action
        issues = []
        element_state = self._find_element_by_id(action.element_id, page_state)

        if action.action_type == "navigate":
            target_url = self._resolve_navigation_target(
                action.element_id, page_state.url
            )
            self._log(f"🎯 이동: {target_url}")
            success, error = self._execute_action("goto", url=target_url)
            if not success and error:
                issues.append(
                    self._create_action_failure_issue(
                        action=action,
                        error_message=error,
                        url=page_state.url,
                    )
                )
            return success, error, issues

        # 셀렉터 찾기
        selector = self._find_selector_by_element_id(action.element_id, page_state) or ""
        resolved_ref_id = (
            str(getattr(element_state, "ref_id", "") or "").strip()
            if element_state
            else ""
        )
        if not resolved_ref_id and not selector:
            return False, f"대상 ref/selector를 찾을 수 없음: {action.element_id}", []

        self._log(f"🎯 실행: {action.action_type} on {action.description}")

        try:
            # 액션 실행 전 에러 수 확인
            errors_before = len(self._check_console_errors())

            # 액션 실행
            if action.action_type == "click":
                did_open_menu = False
                if self._should_open_menu_for_action(action, selector):
                    menu_selector = self._find_open_menu_selector(page_state)
                    if menu_selector:
                        self._log("ℹ️ 메뉴 항목 클릭 전 메뉴 열기 시도")
                        self._execute_action("click", selector=menu_selector)
                        time.sleep(0.5)
                        did_open_menu = True
                self._execute_action(
                    "scrollIntoView",
                    selector=selector or None,
                    ref_id=resolved_ref_id or None,
                    hint_text=action.description,
                )
                success, error = self._execute_action(
                    "click",
                    selector=selector or None,
                    ref_id=resolved_ref_id or None,
                    hint_text=action.description,
                )
                if did_open_menu:
                    close_selector = self._find_close_menu_selector(page_state)
                    if close_selector:
                        self._log("ℹ️ 메뉴 항목 클릭 후 메뉴 닫기 건너뜀")
            elif action.action_type == "fill":
                # 입력값 결정
                value = self._determine_input_value(action, decision.input_values)
                success, error = self._execute_action(
                    "fill",
                    selector=selector or None,
                    ref_id=resolved_ref_id or None,
                    value=value,
                    hint_text=action.description,
                )

                # 셀렉터 실패 시 좌표 기반 입력 fallback
                if not success:
                    bounding_box = element_state.bounding_box if element_state else None
                    if bounding_box:
                        center_x = bounding_box.get("center_x")
                        center_y = bounding_box.get("center_y")
                        if center_x is None or center_y is None:
                            x = bounding_box.get("x")
                            y = bounding_box.get("y")
                            width = bounding_box.get("width")
                            height = bounding_box.get("height")
                            if (
                                x is not None
                                and y is not None
                                and width is not None
                                and height is not None
                            ):
                                center_x = x + width / 2
                                center_y = y + height / 2
                        if center_x is not None and center_y is not None:
                            self._log("⚠️ fill 실패, 좌표 기반 입력 fallback 시도")
                            success, error = self._execute_action(
                                "fillAt",
                                value={"x": center_x, "y": center_y, "text": value},
                            )
            elif action.action_type == "select":
                # 실제 option 목록에서 유효한 값 선택
                select_value = self._pick_select_option(element_state)
                success, error = self._execute_action(
                    "select",
                    selector=selector or None,
                    ref_id=resolved_ref_id or None,
                    value=select_value,
                    hint_text=action.description,
                )
                if success:
                    self._last_exec_meta = dict(self._last_exec_meta or {})
                    self._last_exec_meta["selected_value"] = select_value
            elif action.action_type == "hover":
                success, error = self._execute_action(
                    "hover",
                    selector=selector or None,
                    ref_id=resolved_ref_id or None,
                    hint_text=action.description,
                )
            else:
                success, error = False, f"지원하지 않는 액션: {action.action_type}"

            # 액션 실행 후 에러 수 확인
            time.sleep(0.5)
            errors_after = len(self._check_console_errors())

            # 새로운 에러 발생했으면 이슈로 기록
            if errors_after > errors_before:
                new_errors = self._check_console_errors()[errors_before:]
                issue = self._create_error_issue(
                    action=action,
                    error_logs=new_errors,
                    url=page_state.url,
                )
                if issue is not None:
                    issues.append(issue)

            # 액션 실패도 이슈로 기록
            if not success and error:
                err_lower = str(error or "").lower()
                desc_lower = str(action.description or "").lower()
                is_auth_login_timeout = (
                    str(self._runtime_phase or "").upper() == "AUTH"
                    and ("로그인" in desc_lower or "login" in desc_lower)
                    and "read timed out" in err_lower
                )
                if not is_auth_login_timeout:
                    issue = self._create_action_failure_issue(
                        action=action,
                        error_message=error,
                        url=page_state.url,
                    )
                    issues.append(issue)

            return success, error, issues

        except Exception as e:
            return False, str(e), []

    def _should_open_menu_for_action(
        self,
        action: TestableAction,
        selector: str,
    ) -> bool:
        return should_open_menu_for_action_impl(self, action, selector)

    def _find_open_menu_selector(self, page_state: PageState) -> Optional[str]:
        return find_open_menu_selector_impl(self, page_state)

    def _find_close_menu_selector(self, page_state: PageState) -> Optional[str]:
        return find_close_menu_selector_impl(self, page_state)

    def _execute_action(
        self,
        action: str,
        selector: Optional[str] = None,
        ref_id: Optional[str] = None,
        value: Optional[object] = None,
        url: Optional[str] = None,
        hint_text: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """MCP Host를 통해 액션 실행"""
        self._last_exec_meta = {}
        try:
            request_timeout = float(
                os.getenv("GAIA_MCP_REQUEST_TIMEOUT_SEC", str(self.config.action_timeout))
            )
        except Exception:
            request_timeout = float(self.config.action_timeout)
        request_timeout = max(20.0, min(request_timeout, 45.0))
        selector_text = " ".join(
            part for part in [str(selector or ""), str(hint_text or "")]
            if part
        ).lower()
        auth_submit_action = bool(
            action == "click"
            and (
                "로그인" in selector_text
                or "login" in selector_text
                or "sign in" in selector_text
                or "회원가입" in selector_text
                or "sign up" in selector_text
                or "register" in selector_text
            )
        )
        resolved_ref_id = ref_id
        is_element_action = action in {
            "click",
            "fill",
            "press",
            "hover",
            "scroll",
            "scrollIntoView",
            "select",
            "dragAndDrop",
            "dragSlider",
        }
        if (
            not resolved_ref_id
            and selector
            and is_element_action
            and self._active_snapshot_id
        ):
            resolved_ref_id = self._selector_to_ref_id.get(selector)

        if is_element_action and not (resolved_ref_id and self._active_snapshot_id):
            _ = self._analyze_dom()
            if selector:
                resolved_ref_id = self._selector_to_ref_id.get(selector)
            self._last_exec_meta = {
                "reason_code": "ref_required",
                "reason": "Ref-only policy: snapshot_id + ref_id required",
                "effective": False,
                "state_change": {},
                "attempt_logs": [],
            }
            if not (resolved_ref_id and self._active_snapshot_id):
                return False, "[ref_required] Ref-only policy: snapshot_id + ref_id required"

        if resolved_ref_id and is_element_action and self._active_snapshot_id:
            ref_params: Dict[str, object] = {
                "session_id": self.session_id,
                "snapshot_id": self._active_snapshot_id,
                "ref_id": resolved_ref_id,
                "action": action,
                "url": url or "",
                "verify": False if auth_submit_action else True,
                "selector_hint": hint_text or selector or "",
            }
            if value is not None:
                ref_params["value"] = value
            try:
                from gaia.src.phase4.mcp_local_dispatch_runtime import execute_mcp_action

                response = execute_mcp_action(
                    self.mcp_host_url,
                    action="browser_act",
                    params=ref_params,
                    timeout=(10, request_timeout),
                )
                data = response.payload if not hasattr(response, "json") else response.json()
                success = bool(data.get("success"))
                effective = bool(data.get("effective", True))
                if success and effective:
                    self._last_exec_meta = {
                        "reason_code": "ok",
                        "reason": "ok",
                        "effective": True,
                        "state_change": data.get("state_change") if isinstance(data.get("state_change"), dict) else {},
                        "attempt_logs": data.get("attempt_logs") if isinstance(data.get("attempt_logs"), list) else [],
                        "snapshot_id_used": str(data.get("snapshot_id_used") or ""),
                        "ref_id_used": str(data.get("ref_id_used") or ""),
                    }
                    return True, None
                reason_code, reason = extract_reason_fields(data, response.status_code)
                if str(reason_code) in {
                    "snapshot_not_found",
                    "stale_snapshot",
                    "ref_required",
                    "not_found",
                    "ambiguous_ref_target",
                    "no_state_change",
                    "not_actionable",
                    "http_400",
                }:
                    _ = self._analyze_dom()
                    refreshed_ref_id = resolved_ref_id
                    if selector:
                        refreshed_ref_id = (
                            self._selector_to_ref_id.get(selector) or refreshed_ref_id
                        )
                    should_retry = (
                        bool(refreshed_ref_id and self._active_snapshot_id)
                        and (
                            str(reason_code)
                            in {
                                "snapshot_not_found",
                                "stale_snapshot",
                                "ref_required",
                                "not_found",
                                "ambiguous_ref_target",
                                "http_400",
                            }
                            or str(refreshed_ref_id)
                            != str(ref_params.get("ref_id") or "")
                        )
                    )
                    if should_retry:
                        retry_params: Dict[str, object] = dict(ref_params)
                        retry_params["snapshot_id"] = self._active_snapshot_id
                        retry_params["ref_id"] = refreshed_ref_id
                        retry_response = execute_mcp_action(
                            self.mcp_host_url,
                            action="browser_act",
                            params=retry_params,
                            timeout=(10, request_timeout),
                        )
                        retry_data = retry_response.payload if not hasattr(retry_response, "json") else retry_response.json()
                        retry_success = bool(retry_data.get("success"))
                        retry_effective = bool(retry_data.get("effective", True))
                        if retry_success and retry_effective:
                            self._last_exec_meta = {
                                "reason_code": "ok",
                                "reason": "ok",
                                "effective": True,
                                "state_change": retry_data.get("state_change") if isinstance(retry_data.get("state_change"), dict) else {},
                                "attempt_logs": retry_data.get("attempt_logs") if isinstance(retry_data.get("attempt_logs"), list) else [],
                                "snapshot_id_used": str(retry_data.get("snapshot_id_used") or ""),
                                "ref_id_used": str(retry_data.get("ref_id_used") or ""),
                            }
                            self._log("♻️ stale/ref 오류 복구: 최신 snapshot/ref 재매핑 후 재시도 성공")
                            return True, None
                        retry_reason_code, retry_reason = extract_reason_fields(
                            retry_data,
                            retry_response.status_code,
                        )
                        reason_code = retry_reason_code or reason_code
                        reason = retry_reason or reason
                self._last_exec_meta = {
                    "reason_code": str(reason_code),
                    "reason": str(reason),
                    "effective": bool(effective),
                    "state_change": data.get("state_change") if isinstance(data.get("state_change"), dict) else {},
                    "attempt_logs": data.get("attempt_logs") if isinstance(data.get("attempt_logs"), list) else [],
                    "snapshot_id_used": str(data.get("snapshot_id_used") or ""),
                    "ref_id_used": str(data.get("ref_id_used") or ""),
                }
                self._log(f"❌ Ref action failed: [{reason_code}] {reason}")
                return False, f"[{reason_code}] {reason}"
            except Exception as exc:
                if self._is_mcp_transport_error(str(exc)):
                    self._recover_mcp_host(context="ref_action")
                self._last_exec_meta = {
                    "reason_code": "request_exception",
                    "reason": add_no_retry_hint(str(exc)),
                    "effective": False,
                    "state_change": {},
                    "attempt_logs": [],
                }
                return False, add_no_retry_hint(str(exc))

        params: Dict[str, object] = {
            "session_id": self.session_id,
            "action": action,
            "url": url or "",
        }

        if value is not None:
            if action == "evaluate":
                params["fn"] = value
            else:
                params["value"] = value
        if action == "goto" and url:
            params["value"] = url
        if action == "wait" and selector:
            params["selector"] = selector

        try:
            from gaia.src.phase4.mcp_local_dispatch_runtime import execute_mcp_action

            response = execute_mcp_action(
                self.mcp_host_url,
                action="browser_act",
                params=params,
                timeout=(10, request_timeout),
            )

            # HTTP 상태 코드 로깅
            if response.status_code != 200:
                self._log(f"⚠️  HTTP {response.status_code}: {str(getattr(response, 'text', '') or '')[:200]}")

            data = response.payload if not hasattr(response, "json") else response.json()

            if data.get("success"):
                self._last_exec_meta = {
                    "reason_code": str(data.get("reason_code") or "ok"),
                    "reason": str(data.get("reason") or "ok"),
                    "effective": bool(data.get("effective", True)),
                    "state_change": data.get("state_change") if isinstance(data.get("state_change"), dict) else {},
                    "attempt_logs": data.get("attempt_logs") if isinstance(data.get("attempt_logs"), list) else [],
                }
                return True, None
            else:
                error_msg = (
                    data.get("error")
                    or data.get("detail")
                    or f"Unknown error (response: {data})"
                )
                self._last_exec_meta = {
                    "reason_code": str(data.get("reason_code") or data.get("error") or "unknown_error"),
                    "reason": str(error_msg),
                    "effective": bool(data.get("effective", False)),
                    "state_change": data.get("state_change") if isinstance(data.get("state_change"), dict) else {},
                    "attempt_logs": data.get("attempt_logs") if isinstance(data.get("attempt_logs"), list) else [],
                }
                self._log(f"❌ Action failed: {error_msg}")
                return False, error_msg

        except Exception as e:
            if self._is_mcp_transport_error(str(e)):
                self._recover_mcp_host(context=f"action:{action}")
            self._last_exec_meta = {
                "reason_code": "request_exception",
                "reason": add_no_retry_hint(str(e)),
                "effective": False,
                "state_change": {},
                "attempt_logs": [],
            }
            return False, add_no_retry_hint(str(e))

    def _select_loop_escape_action(
        self,
        page_state: PageState,
        blocked_action: Optional[TestableAction],
    ) -> Optional[TestableAction]:
        candidates = self._generate_testable_actions(page_state)
        if not candidates:
            return None
        blocked_element = str(blocked_action.element_id or "") if blocked_action else ""
        blocked_type = str(blocked_action.action_type or "") if blocked_action else ""

        ranked = sorted(candidates, key=lambda item: float(item.priority), reverse=True)
        for candidate in ranked:
            if str(candidate.element_id or "") == blocked_element:
                continue
            if str(candidate.action_type or "") == blocked_type:
                continue
            if str(candidate.action_type or "") == "select":
                desc = str(candidate.description or "").strip()
                if not desc or desc.endswith(":") or desc.endswith("："):
                    continue
            return candidate
        return None

    def _loop_guard_tool_name(
        self,
        action: Optional[TestableAction],
        page_state: PageState,
    ) -> str:
        if action is None:
            return "none"
        element = self._find_element_by_id(str(action.element_id), page_state)
        tag = (element.tag or "").strip().lower() if element else ""
        role = (element.role or "").strip().lower() if element else ""
        bucket = tag or role or "generic"
        return f"{str(action.action_type or 'unknown').strip().lower()}:{bucket}"

    def _force_context_shift(self, page_state: PageState, start_url: str) -> bool:
        self._log("🧭 loop_guard 임계치: 컨텍스트 강제 전환(재스냅샷 + phase shift)")
        _ = self._analyze_dom()

        nav_candidates = self._build_navigation_actions(page_state)
        nav_candidates.sort(key=lambda item: float(item.priority), reverse=True)
        for nav_action in nav_candidates[:2]:
            target_url = self._resolve_navigation_target(nav_action.element_id, page_state.url)
            if not target_url:
                continue
            ok, err = self._execute_action("goto", url=target_url)
            if not ok:
                self._log(f"⚠️ context shift navigate 실패: {err}")
                continue
            time.sleep(1.0)
            shifted_state = self._analyze_current_page()
            if shifted_state:
                previous_phase = self._runtime_phase
                self._runtime_phase = self._infer_runtime_phase(shifted_state)
                self._no_progress_counter = 0
                self._current_state_key = None
                self._log(
                    f"🔄 phase shift: {previous_phase} -> {self._runtime_phase} "
                    f"(url={shifted_state.url})"
                )
                return True

        fallback_urls = []
        if page_state.url:
            fallback_urls.append(page_state.url)
        if start_url and start_url not in fallback_urls:
            fallback_urls.append(start_url)
        if self._current_url and self._current_url not in fallback_urls:
            fallback_urls.append(self._current_url)

        for fallback_url in fallback_urls:
            ok, err = self._execute_action("goto", url=fallback_url)
            if not ok:
                self._log(f"⚠️ context shift re-sync 실패: {err}")
                continue
            time.sleep(1.0)
            shifted_state = self._analyze_current_page()
            if shifted_state:
                previous_phase = self._runtime_phase
                self._runtime_phase = self._infer_runtime_phase(shifted_state)
                self._no_progress_counter = 0
                self._current_state_key = None
                self._log(
                    f"🔄 phase shift(resync): {previous_phase} -> {self._runtime_phase} "
                    f"(url={shifted_state.url})"
                )
                return True

        self._log("⚠️ 컨텍스트 전환 실패: 기존 전략으로 계속 진행합니다.")
        return False

    def _evaluate_selector(self, selector: str, script: str) -> Optional[str]:
        return evaluate_selector_impl(self, selector, script)

    def _get_select_state(self, selector: Optional[str]) -> Optional[dict]:
        return get_select_state_impl(self, selector)

    def _pick_select_option(self, element_state: Optional[Any]) -> str:
        return pick_select_option_impl(self, element_state)

    def _get_toggle_state(self, selector: Optional[str]) -> Optional[dict]:
        return get_toggle_state_impl(self, selector)

    def _build_element_id(
        self,
        url_hash: str,
        element: DOMElement,
        selector: str,
    ) -> str:
        return build_element_id_impl(self, url_hash, element, selector)

    def _find_selector_by_element_id(
        self,
        element_id: str,
        page_state: PageState,
    ) -> Optional[str]:
        return find_selector_by_element_id_impl(self, element_id, page_state)

    def _find_element_by_id(
        self,
        element_id: str,
        page_state: PageState,
    ) -> Optional[ElementState]:
        return find_element_by_id_impl(self, element_id, page_state)

    def _is_selector_safe(self, selector: str) -> bool:
        return is_selector_safe_impl(self, selector)

    def _fallback_selector_for_element(
        self,
        element: ElementState,
        page_state: PageState,
    ) -> Optional[str]:
        return fallback_selector_for_element_impl(self, element, page_state)

    def _determine_input_value(
        self,
        action: TestableAction,
        input_values: Dict[str, str],
    ) -> str:
        return determine_input_value_impl(self, action, input_values)

    def _create_error_issue(
        self,
        action: TestableAction,
        error_logs: List[Any],
        url: str,
    ) -> Optional[FoundIssue]:
        return create_error_issue_impl(self, action, error_logs, url)

    def _create_action_failure_issue(
        self,
        action: TestableAction,
        error_message: str,
        url: str,
    ) -> FoundIssue:
        return create_action_failure_issue_impl(self, action, error_message, url)

    def _create_intent_issue(
        self,
        action: TestableAction,
        url: str,
        reason: str,
        screenshot_before: Optional[str] = None,
        screenshot_after: Optional[str] = None,
    ) -> FoundIssue:
        return create_intent_issue_impl(
            self,
            action,
            url,
            reason,
            screenshot_before=screenshot_before,
            screenshot_after=screenshot_after,
        )

    def _verify_action_intent(
        self,
        action: TestableAction,
        before_state: PageState,
        after_state: PageState,
        before_url: str,
        after_url: str,
        screenshot_before: Optional[str],
        screenshot_after: Optional[str],
        expected_input: Optional[str],
        before_select_state: Optional[dict],
        before_toggle_state: Optional[dict],
    ) -> tuple[bool, Optional[str]]:
        return verify_action_intent_impl(
            self,
            action,
            before_state,
            after_state,
            before_url,
            after_url,
            screenshot_before,
            screenshot_after,
            expected_input,
            before_select_state,
            before_toggle_state,
        )

    def _run_filter_semantic_validation(self, goal_text: str) -> Dict[str, Any]:
        try:
            from .filter_validation_engine import build_filter_validation_config, run_filter_validation

            adapter = _ExploratoryFilterValidationAdapter(self)
            forced_selected = str((self._last_exec_meta or {}).get("selected_value") or "").strip()
            report = run_filter_validation(
                adapter=adapter,
                goal_text=goal_text,
                config=build_filter_validation_config(
                    max_pages=2,
                    max_cases=3,
                    use_current_selection_only=False,
                    forced_selected_value=forced_selected,
                ),
            )
            return report if isinstance(report, dict) else {}
        except Exception as exc:
            self._log(f"⚠️ filter semantic validation 실패: {exc}")
            return {}

    def _append_validation_report(self, report: Dict[str, Any], step_number: int) -> List[Dict[str, Any]]:
        return append_validation_report_impl(self, report, step_number)

    @staticmethod
    def _aggregate_validation_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        return aggregate_validation_summary_impl(rows)

    def _find_element_by_selector(
        self, selector: Optional[str], page_state: PageState
    ) -> Optional[ElementState]:
        if not selector:
            return None
        for element in page_state.interactive_elements:
            if element.selector == selector:
                return element
        return None

    @staticmethod
    def _normalize_text(value: Optional[str]) -> str:
        if not value:
            return ""
        return " ".join(value.split()).strip().lower()

    @staticmethod
    def _normalize_url_for_compare(url: str) -> str:
        if not url:
            return ""
        normalized = url.split("#")[0].rstrip("/")
        return normalized

    def _report_console_errors(
        self, console_errors: List[str], screenshot: Optional[str]
    ):
        return report_console_errors_impl(self, console_errors, screenshot)

    @staticmethod
    def _is_expected_non_bug_console_error(log_text: str) -> bool:
        return is_expected_non_bug_console_error_impl(log_text)

    def _calculate_coverage(self) -> Dict[str, Any]:
        return calculate_coverage_impl(self._visited_pages, self._tested_elements)

    def _determine_completion_reason(
        self,
        action_count: int,
        steps: List[ExplorationStep],
        duration_seconds: float = 0.0,
    ) -> str:
        return determine_completion_reason_impl(
            self._forced_completion_reason,
            self.config,
            action_count,
            steps,
            duration_seconds,
        )

    def _print_summary(self, result: ExplorationResult):
        return print_summary_impl(self._log, result)

    def _hash_url(self, url: str) -> str:
        return hash_url_impl(url)

    def _call_llm_text_only(self, prompt: str) -> str:
        return call_llm_text_only_impl(self.llm, prompt)
