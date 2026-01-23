"""
Exploratory Testing Agent

완전 자율 탐색 모드 - 화면의 모든 UI 요소를 자동으로 찾아서 테스트
"""

from __future__ import annotations
import time
import json
import hashlib
import math
import os
import re
import base64
import requests
from typing import Any, Dict, List, Optional, Set, Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

# GIF 생성을 위한 선택적 import
try:
    from PIL import Image
    import io

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

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


class ExploratoryAgent:
    """
    완전 자율 탐색 에이전트

    목표 없이 화면의 모든 UI 요소를 탐색하고 테스트
    버그, 에러, 이상 동작을 자동으로 감지
    """

    def __init__(
        self,
        mcp_host_url: str = "http://localhost:8000",
        gemini_api_key: Optional[str] = None,
        session_id: str = "exploratory",
        config: Optional[ExplorationConfig] = None,
        log_callback: Optional[Callable[[str], None]] = None,
        screenshot_callback: Optional[Callable[[str], None]] = None,
        user_intervention_callback: Optional[Callable[[str, str], bool]] = None,
    ):
        self.mcp_host_url = mcp_host_url
        self.session_id = session_id
        self.config = config or ExplorationConfig()
        self._log_callback = log_callback
        self._screenshot_callback = screenshot_callback
        self._user_intervention_callback = user_intervention_callback

        # Gemini 클라이언트 초기화
        from gaia.src.phase4.llm_vision_client_gemini import GeminiVisionClient

        self.llm = GeminiVisionClient(api_key=gemini_api_key)

        # 탐색 상태 추적
        self._visited_pages: Dict[str, PageState] = {}  # url_hash -> PageState
        self._tested_elements: Set[str] = set()  # element_id
        self._action_history: List[str] = []
        self._found_issues: List[FoundIssue] = []

        # 현재 페이지 상태
        self._current_url: str = ""
        self._element_selectors: Dict[int, str] = {}  # DOM ID -> selector
        self._element_full_selectors: Dict[int, str] = {}  # DOM ID -> full selector
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

    def _log(self, message: str):
        """로그 출력"""
        print(f"[ExploratoryAgent] {message}")
        if self._log_callback:
            self._log_callback(message)

    def _setup_recording_dir(self, session_id: str) -> Path:
        """녹화용 디렉토리 설정"""
        repo_root = Path(__file__).resolve().parents[4]
        screenshots_dir = (
            repo_root / "artifacts" / "exploration_results" / session_id / "screenshots"
        )
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        return screenshots_dir

    def _save_screenshot_to_file(
        self, screenshot_base64: str, screenshots_dir: Path, step_num: int
    ) -> str:
        """스크린샷을 파일로 저장"""
        if not screenshot_base64:
            return ""
        try:
            # base64 데이터에서 헤더 제거
            if "," in screenshot_base64:
                screenshot_base64 = screenshot_base64.split(",")[1]

            img_data = base64.b64decode(screenshot_base64)
            filename = f"step_{step_num:03d}.png"
            filepath = screenshots_dir / filename

            with open(filepath, "wb") as f:
                f.write(img_data)

            return str(filepath)
        except Exception as e:
            self._log(f"⚠️ 스크린샷 저장 실패: {e}")
            return ""

    def _generate_gif(self, screenshots_dir: Path, output_path: Path) -> bool:
        """스크린샷들로 GIF 생성"""
        if not HAS_PIL:
            self._log("⚠️ PIL이 설치되지 않아 GIF를 생성할 수 없습니다")
            return False

        try:
            png_files = sorted(screenshots_dir.glob("step_*.png"))
            if len(png_files) < 2:
                self._log("⚠️ GIF 생성을 위한 스크린샷이 부족합니다")
                return False

            images = []
            for png_file in png_files:
                img = Image.open(png_file)
                # 크기 조정 (너무 크면 GIF가 무거워짐)
                max_width = 800
                if img.width > max_width:
                    ratio = max_width / img.width
                    new_size = (max_width, int(img.height * ratio))
                    img = img.resize(new_size, Image.Resampling.LANCZOS)
                images.append(img)

            # GIF 저장 (각 프레임 1초)
            images[0].save(
                output_path,
                save_all=True,
                append_images=images[1:],
                duration=1000,  # 1초 per frame
                loop=0,
            )
            self._log(f"🎬 GIF 생성 완료: {output_path}")
            return True
        except Exception as e:
            self._log(f"⚠️ GIF 생성 실패: {e}")
            return False

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
        repo_root = Path(__file__).resolve().parents[4]
        return str(repo_root / "artifacts" / "llm_cache.json")

    def _resolve_semantic_cache_path(self) -> str:
        repo_root = Path(__file__).resolve().parents[4]
        return str(repo_root / "artifacts" / "cache" / "semantic_llm_cache.json")

    def _load_llm_cache(self) -> None:
        try:
            if os.path.exists(self._llm_cache_path):
                with open(self._llm_cache_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    self._llm_cache = {k: str(v) for k, v in data.items()}
        except Exception as exc:
            self._log(f"⚠️ LLM 캐시 로드 실패: {exc}")

    def _save_llm_cache(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._llm_cache_path), exist_ok=True)
            with open(self._llm_cache_path, "w", encoding="utf-8") as handle:
                json.dump(self._llm_cache, handle, ensure_ascii=False, indent=2)
        except Exception as exc:
            self._log(f"⚠️ LLM 캐시 저장 실패: {exc}")

    def _load_semantic_cache(self) -> None:
        try:
            if os.path.exists(self._semantic_cache_path):
                with open(self._semantic_cache_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, list):
                    self._semantic_cache = data
        except Exception as exc:
            self._log(f"⚠️ 시맨틱 캐시 로드 실패: {exc}")

    def _save_semantic_cache(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._semantic_cache_path), exist_ok=True)
            with open(self._semantic_cache_path, "w", encoding="utf-8") as handle:
                json.dump(self._semantic_cache, handle, ensure_ascii=False)
        except Exception as exc:
            self._log(f"⚠️ 시맨틱 캐시 저장 실패: {exc}")

    def _get_llm_cache_key(
        self,
        prompt: str,
        screenshot: Optional[str],
        action_signature: str,
    ) -> str:
        digest = hashlib.md5()
        digest.update(prompt.encode("utf-8"))
        digest.update(action_signature.encode("utf-8"))
        if screenshot:
            digest.update(screenshot.encode("utf-8"))
        return digest.hexdigest()

    def _semantic_cache_text(
        self,
        page_state: PageState,
        testable_actions: List[TestableAction],
    ) -> str:
        actions_text = "\n".join(
            f"{action.action_type}:{action.description}"
            for action in testable_actions[:60]
        )
        element_summary = ",".join(
            sorted(
                {f"{el.tag}:{el.text[:20]}" for el in page_state.interactive_elements}
            )
        )
        state_summary = (
            f"tested={len(self._tested_elements)};"
            f"history={';'.join(self._action_history[-3:])}"
        )
        action_signature = self._action_signature(testable_actions)
        return (
            f"{page_state.url}\n{element_summary}\n{state_summary}\n"
            f"signature={action_signature}\n{actions_text}"
        )

    def _embed_text(self, text: str) -> List[float]:
        tokens = re.findall(r"[\w가-힣]+", text.lower())
        dim = 128
        vector = [0.0] * dim
        for token in tokens:
            token_hash = hashlib.md5(token.encode("utf-8")).hexdigest()
            index = int(token_hash[:8], 16) % dim
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0:
            vector = [value / norm for value in vector]
        return vector

    def _cosine_similarity(self, left: List[float], right: List[float]) -> float:
        if not left or not right:
            return 0.0
        length = min(len(left), len(right))
        dot = sum(left[i] * right[i] for i in range(length))
        left_norm = math.sqrt(sum(left[i] * left[i] for i in range(length)))
        right_norm = math.sqrt(sum(right[i] * right[i] for i in range(length)))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return dot / (left_norm * right_norm)

    def _semantic_cache_lookup(
        self, text: str, action_signature: str, threshold: float = 0.95
    ) -> Optional[str]:
        if not self._semantic_cache:
            return None
        query_embedding = self._embed_text(text)
        best_score = 0.0
        best_response: Optional[str] = None
        for entry in self._semantic_cache:
            embedding = entry.get("embedding")
            response = entry.get("response")
            signature = entry.get("signature")
            if signature != action_signature:
                continue
            if not isinstance(embedding, list) or not isinstance(response, str):
                continue
            score = self._cosine_similarity(query_embedding, embedding)
            if score > best_score:
                best_score = score
                best_response = response
        if best_response and best_score >= threshold:
            self._log(f"🧠 시맨틱 캐시 hit (score={best_score:.2f})")
            return best_response
        return None

    def _semantic_cache_store(
        self, text: str, response: str, action_signature: str
    ) -> None:
        embedding = self._embed_text(text)
        self._semantic_cache.append(
            {
                "embedding": embedding,
                "response": response,
                "text": text[:500],
                "signature": action_signature,
            }
        )
        if len(self._semantic_cache) > 200:
            self._semantic_cache = self._semantic_cache[-200:]
        self._save_semantic_cache()

    def _is_login_page_with_no_elements(self, page_state: PageState) -> bool:
        """
        로그인 페이지이면서 요소를 찾지 못한 경우 감지

        Args:
            page_state: 현재 페이지 상태

        Returns:
            bool: 사용자 개입이 필요한 로그인 페이지인 경우 True
        """
        # URL에 로그인 관련 키워드가 포함되어 있는지 확인
        login_keywords = ["login", "signin", "auth", "sso", "portal"]
        url_lower = page_state.url.lower()
        has_login_keyword = any(keyword in url_lower for keyword in login_keywords)

        # 요소가 0개이거나 매우 적은 경우
        has_few_elements = len(page_state.interactive_elements) <= 2

        return has_login_keyword and has_few_elements

    def _request_user_intervention(self, reason: str, current_url: str) -> bool:
        """
        사용자 개입 요청

        Args:
            reason: 개입이 필요한 이유 (예: "로그인 필요", "캡챠 해결 필요")
            current_url: 현재 URL

        Returns:
            bool: 사용자가 작업을 완료했으면 True, 탐색 중단하려면 False
        """
        self._log("=" * 60)
        self._log("⏸️  사용자 개입 필요")
        self._log(f"   이유: {reason}")
        self._log(f"   현재 URL: {current_url}")
        self._log("=" * 60)

        # 콜백이 있으면 콜백 사용
        if self._user_intervention_callback:
            return self._user_intervention_callback(reason, current_url)

        # 콜백이 없으면 기본 input() 사용
        print(f"\n🔔 사용자 개입이 필요합니다!")
        print(f"이유: {reason}")
        print(f"현재 URL: {current_url}")
        print(f"\n브라우저에서 필요한 작업(로그인 등)을 완료한 후,")
        user_input = (
            input("계속하려면 'c' 또는 'continue'를 입력하세요 (중단: 'q'): ")
            .strip()
            .lower()
        )

        if user_input in ["c", "continue", "yes", "y"]:
            self._log("✅ 사용자가 작업을 완료했습니다. 탐색을 계속합니다.")
            return True
        else:
            self._log("❌ 사용자가 탐색 중단을 요청했습니다.")
            return False

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
        self._log(f"   최대 액션: {self.config.max_actions}")
        self._log("=" * 60)

        # 시작 URL로 이동
        self._log(f"📍 시작 URL로 이동")
        self._execute_action("goto", url=start_url)
        time.sleep(2)  # 페이지 로드 대기
        self._current_url = start_url
        self._seed_urls = self._normalize_seed_urls(start_url)

        action_count = 0

        while action_count < self.config.max_actions:
            action_count += 1
            step_start = time.time()

            self._log(f"\n{'=' * 60}")
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

            # 로그인 페이지 감지 및 사용자 개입 요청
            if self._is_login_page_with_no_elements(page_state):
                self._log(
                    "🔐 로그인 페이지 감지됨 (요소 접근 불가 - cross-origin iframe 또는 특수 인증)"
                )

                if not self._request_user_intervention(
                    reason="로그인이 필요합니다. 브라우저에서 수동으로 로그인해주세요.",
                    current_url=page_state.url,
                ):
                    self._log("탐색 중단")
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
            screenshot_before = screenshot
            if screenshots_dir and screenshot_before:
                saved_path = self._save_screenshot_to_file(
                    screenshot_before, screenshots_dir, action_count
                )
                if saved_path:
                    screenshot_paths.append(saved_path)

            # 8. 액션 실행
            success, error, issues = self._execute_exploration_action(
                decision=decision,
                page_state=page_state,
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
                self._state_action_history.setdefault(
                    self._current_state_key, set()
                ).add(
                    f"{decision.selected_action.element_id}:{decision.selected_action.action_type}"
                )

            self._action_attempts[attempt_key] = (
                self._action_attempts.get(attempt_key, 0) + 1
            )

            # 9-2. 상태별 액션 기록
            if self._current_state_key:
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
            screenshot_after = self._capture_screenshot()

            # 12. 새로운 페이지 발견 확인
            new_url = self._get_current_url()
            new_pages = 1 if new_url != page_state.url else 0
            if new_pages:
                self._log(f"🆕 새 페이지 발견: {new_url}")

            after_state = self._analyze_current_page()
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
                    issues.append(
                        self._create_intent_issue(
                            action=decision.selected_action,
                            url=page_state.url,
                            reason=intent_reason,
                            screenshot_before=screenshot_before,
                            screenshot_after=screenshot_after,
                        )
                    )

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
                new_pages_found=new_pages,
                screenshot_before=screenshot_before,
                screenshot_after=screenshot_after,
                duration_ms=int((time.time() - step_start) * 1000),
            )
            steps.append(step)

            # 14. 발견된 이슈 추가
            self._found_issues.extend(issues)
            if issues:
                self._log(f"🚨 이슈 발견: {len(issues)}개")
                for issue in issues:
                    self._log(f"   - [{issue.severity}] {issue.title}")

            # 15. 실패한 경우 계속 진행할지 판단
            if not success and error:
                self._log(f"⚠️  액션 실패: {error}")
                # 실패해도 계속 진행 (다른 요소 테스트)

            # 다음 스텝 전 대기
            time.sleep(0.5)

        # 탐색 완료
        duration = time.time() - start_time
        completion_reason = self._determine_completion_reason(action_count, steps)

        # GIF 생성 (녹화가 활성화된 경우)
        gif_path = None
        if screenshots_dir and self.config.generate_gif and screenshot_paths:
            gif_filename = screenshots_dir.parent / f"{session_id}.gif"
            if self._generate_gif(screenshots_dir, gif_filename):
                gif_path = str(gif_filename)

        # 테스트 시나리오 그룹화
        test_scenarios = self._group_steps_into_scenarios(steps)

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
            completed_at=datetime.now(),
            duration_seconds=duration,
        )

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

            # AutoCrawler 방식: 중요 요소만 필터링 (광고/푸터 제외)
            interactive_elements = []
            for idx, el in enumerate(dom_elements):
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

                selector = self._element_full_selectors.get(
                    idx
                ) or self._element_selectors.get(idx, "")
                element_id = self._build_element_id(url_hash, el, selector)
                tested = element_id in self._tested_elements

                interactive_elements.append(
                    ElementState(
                        element_id=element_id,
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

    def _analyze_dom(self) -> List[DOMElement]:
        """MCP Host를 통해 DOM 분석"""
        try:
            response = requests.post(
                f"{self.mcp_host_url}/execute",
                json={
                    "action": "analyze_page",
                    "params": {"session_id": self.session_id},
                },
                timeout=30,
            )
            data = response.json()

            if "error" in data:
                self._log(f"DOM 분석 오류: {data['error']}")
                return []

            raw_elements = data.get("elements", []) or data.get("dom_elements", [])

            # 셀렉터 맵 초기화
            self._element_selectors = {}
            self._element_full_selectors = {}

            # DOMElement로 변환
            elements = []
            for idx, el in enumerate(raw_elements):
                attrs = el.get("attributes", {})

                # 셀렉터 저장
                selector = el.get("selector", "")
                full_selector = el.get("full_selector") or selector
                if selector:
                    self._element_selectors[idx] = selector
                if full_selector:
                    self._element_full_selectors[idx] = full_selector

                elements.append(
                    DOMElement(
                        id=idx,
                        tag=el.get("tag", ""),
                        text=el.get("text", "")[:100],
                        role=attrs.get("role"),
                        type=attrs.get("type"),
                        placeholder=attrs.get("placeholder"),
                        aria_label=attrs.get("aria-label"),
                        href=attrs.get("href"),
                        bounding_box=el.get("bounding_box"),
                    )
                )

            return elements

        except Exception as e:
            self._log(f"DOM 분석 실패: {e}")
            return []

    def _capture_screenshot(self) -> Optional[str]:
        """스크린샷 캡처"""
        try:
            response = requests.post(
                f"{self.mcp_host_url}/execute",
                json={
                    "action": "capture_screenshot",
                    "params": {"session_id": self.session_id},
                },
                timeout=30,
            )
            data = response.json()
            screenshot = data.get("screenshot")

            if screenshot and self._screenshot_callback:
                self._screenshot_callback(screenshot)

            return screenshot

        except Exception as e:
            self._log(f"스크린샷 캡처 실패: {e}")
            return None

    def _check_console_errors(self) -> List[str]:
        """콘솔 에러 확인"""
        try:
            response = requests.post(
                f"{self.mcp_host_url}/execute",
                json={
                    "action": "get_console_logs",
                    "params": {"session_id": self.session_id, "type": "error"},
                },
                timeout=10,
            )
            data = response.json()
            logs = data.get("logs", [])
            return logs

        except Exception as e:
            self._log(f"콘솔 로그 확인 실패: {e}")
            return []

    def _get_current_url(self) -> str:
        """현재 URL 가져오기"""
        try:
            response = requests.post(
                f"{self.mcp_host_url}/execute",
                json={
                    "action": "get_current_url",
                    "params": {"session_id": self.session_id},
                },
                timeout=10,
            )
            data = response.json()
            return data.get("url", self._current_url)

        except Exception as e:
            return self._current_url

    def _decide_next_exploration_action(
        self,
        page_state: PageState,
        screenshot: Optional[str],
        action_count: int,
    ) -> ExplorationDecision:
        """LLM에게 다음 탐색 액션 결정 요청"""

        # 테스트 가능한 액션 목록 생성
        testable_actions = self._generate_testable_actions(page_state)
        self._log(f"   - 테스트 가능한 액션: {len(testable_actions)}개")
        if not testable_actions:
            preview = [
                f"{el.tag}:{self._element_label(el)}"
                for el in page_state.interactive_elements[:10]
            ]
            self._log(f"   - 요소 샘플: {preview}")

        if not testable_actions:
            if self.config.test_navigation and self._action_frontier:
                frontier_action = self._select_frontier_action(page_state, [])
                if frontier_action:
                    return ExplorationDecision(
                        should_continue=True,
                        selected_action=frontier_action,
                        reasoning="BFS 큐에 남은 액션으로 계속 탐색",
                        confidence=0.4,
                    )
            return ExplorationDecision(
                should_continue=False,
                reasoning="더 이상 테스트할 요소가 없습니다",
                confidence=1.0,
            )

        state_key = self._state_key(page_state, testable_actions)
        self._current_state_key = state_key
        visited_actions = self._state_action_history.get(state_key, set())
        unvisited = [
            action
            for action in testable_actions
            if f"{action.element_id}:{action.action_type}" not in visited_actions
        ]
        if unvisited:
            if self._has_pending_inputs(page_state):
                fill_actions = [
                    action for action in unvisited if action.action_type == "fill"
                ]
                if fill_actions:
                    fill_actions.sort(key=lambda x: x.priority, reverse=True)
                    return ExplorationDecision(
                        should_continue=True,
                        selected_action=fill_actions[0],
                        reasoning="미입력 필드 우선 입력",
                        confidence=0.75,
                    )
            non_fill = [action for action in unvisited if action.action_type != "fill"]
            if non_fill:
                non_fill.sort(key=lambda x: x.priority, reverse=True)
                return ExplorationDecision(
                    should_continue=True,
                    selected_action=non_fill[0],
                    reasoning="상태 기반 탐색: 미실행 액션 우선",
                    confidence=0.7,
                )

        if self.config.test_navigation and not self._has_pending_inputs(page_state):
            frontier_action = self._select_frontier_action(page_state, testable_actions)
            if frontier_action:
                return ExplorationDecision(
                    should_continue=True,
                    selected_action=frontier_action,
                    reasoning="BFS 탐색: 큐에 등록된 액션 우선 선택",
                    confidence=0.6,
                )
            if self._action_frontier:
                self._log("ℹ️ BFS 큐는 남아있지만 현재 페이지에서 매칭 실패")

        # 프롬프트 구성
        prompt = self._build_exploration_prompt(
            page_state=page_state,
            testable_actions=testable_actions,
            action_count=action_count,
        )

        try:
            action_signature = self._action_signature(testable_actions)
            cache_key = self._get_llm_cache_key(prompt, screenshot, action_signature)
            response_text = self._llm_cache.get(cache_key)

            if response_text:
                self._log("🧠 LLM 캐시 hit")
            else:
                semantic_text = self._semantic_cache_text(page_state, testable_actions)
                response_text = self._semantic_cache_lookup(
                    semantic_text, action_signature
                )

            if not response_text:
                # Gemini API 호출
                if screenshot:
                    response_text = self.llm.analyze_with_vision(prompt, screenshot)
                else:
                    response_text = self._call_gemini_text_only(prompt)

                self._llm_cache[cache_key] = response_text
                if len(self._llm_cache) > 200:
                    self._llm_cache.pop(next(iter(self._llm_cache)))
                self._save_llm_cache()

                semantic_text = self._semantic_cache_text(page_state, testable_actions)
                self._semantic_cache_store(
                    semantic_text, response_text, action_signature
                )

            # JSON 파싱
            decision = self._parse_exploration_decision(response_text, testable_actions)

            if not decision.should_continue and testable_actions:
                fallback_action = sorted(
                    testable_actions, key=lambda x: x.priority, reverse=True
                )[0]
                return ExplorationDecision(
                    should_continue=True,
                    selected_action=fallback_action,
                    reasoning="남은 액션이 있어 탐색 지속",
                    confidence=0.5,
                )

            return decision

        except Exception as e:
            self._log(f"LLM 결정 실패: {e}")
            # 기본 결정: 첫 번째 미테스트 요소 선택
            if testable_actions:
                return ExplorationDecision(
                    should_continue=True,
                    selected_action=testable_actions[0],
                    reasoning=f"LLM 오류로 기본 액션 선택: {e}",
                    confidence=0.3,
                )
            else:
                return ExplorationDecision(
                    should_continue=False,
                    reasoning="테스트할 요소 없음",
                    confidence=1.0,
                )

    def _generate_testable_actions(self, page_state: PageState) -> List[TestableAction]:
        """페이지 상태에서 테스트 가능한 액션 목록 생성"""
        actions = []

        recent_action_counts: Dict[str, int] = {}
        for entry in self._action_history[-5:]:
            if ": " in entry:
                action_part = entry.split(": ", 1)[1]
                action_type = action_part.split(" on ", 1)[0]
                recent_action_counts[action_type] = (
                    recent_action_counts.get(action_type, 0) + 1
                )

        pending_inputs = self._has_pending_inputs(page_state)
        has_tested_inputs = self._has_tested_inputs(page_state)
        actions_with_status: List[tuple[TestableAction, bool]] = []

        for element in page_state.interactive_elements:
            # 이미 테스트한 요소는 우선순위 낮게
            priority = 0.3 if element.tested else 0.8

            element_label = self._element_label(element)

            # 액션 타입 결정
            if element.tag == "input":
                if element.type in ["text", "email", "password", "search"]:
                    action_type = "fill"
                    field_hint = element_label or element.type or ""
                    if element.type == "password":
                        description = f"비밀번호 입력: {field_hint}"
                    elif element.type == "email":
                        description = f"이메일 입력: {field_hint}"
                    else:
                        description = f"텍스트 입력({element.type}): {field_hint}"
                elif element.type in ["submit", "button", "image"]:
                    action_type = "click"
                    if self._has_login_form(page_state):
                        description = "버튼: Login"
                    else:
                        description = f"Input: {element.type or element_label}"
                elif element.type in ["checkbox", "radio"]:
                    action_type = "click"
                    description = f"체크박스/라디오: {element_label or element.type}"
                else:
                    action_type = "click"
                    description = f"Input: {element.type or element_label}"
            elif element.tag == "a":
                action_type = "click"
                link_label = element_label or "[icon link]"
                description = f"링크: {link_label}"
                # 외부 링크는 탐색 대상에서 제외
                if element.href:
                    resolved = urljoin(page_state.url, element.href)
                    current_host = urlparse(page_state.url).netloc
                    target_host = urlparse(resolved).netloc
                    if current_host and target_host and current_host != target_host:
                        continue
            elif element.tag == "button":
                action_type = "click"
                button_label = element_label or "[icon]"
                description = f"버튼: {button_label}"
            elif element.tag == "select":
                action_type = "select"
                description = f"드롭다운: {element_label}"
            else:
                action_type = "click"
                description = f"{element.tag}: {element_label or element.role}"
            # 최근 액션과 동일한 타입이면 우선순위 낮춤
            recent_count = recent_action_counts.get(action_type, 0)
            if recent_count >= 2:
                priority *= 0.6
            elif recent_count == 1:
                priority *= 0.8

            # Guard: 필수 입력이 남아있으면 제출/확인 버튼 제외
            if pending_inputs and action_type == "click":
                if self._has_login_form(page_state):
                    if element.tag == "input" and (element.type or "").lower() in [
                        "submit",
                        "button",
                        "image",
                    ]:
                        continue
                    if element.tag == "button" and "login" in description.lower():
                        continue
                if element.tag == "input" and (element.type or "").lower() in [
                    "submit",
                    "button",
                    "image",
                ]:
                    if not has_tested_inputs:
                        continue
                if element.tag == "button":
                    submit_keywords = [
                        "submit",
                        "login",
                        "log in",
                        "sign in",
                        "next",
                        "continue",
                        "confirm",
                        "ok",
                        "로그인",
                        "다음",
                        "확인",
                        "완료",
                    ]
                    label_lower = description.lower()
                    if any(keyword in label_lower for keyword in submit_keywords):
                        if not has_tested_inputs:
                            continue
                        priority *= 0.7

            # Guard: 토글 액션은 페이지당 1회씩만 허용
            if action_type == "click":
                temp_action = TestableAction(
                    element_id=element.element_id,
                    action_type=action_type,
                    description=description,
                    priority=priority,
                    reasoning="",
                )
                if self._is_toggle_action(temp_action):
                    toggle_key = (
                        f"{page_state.url_hash}:{element.element_id}:"
                        f"{self._normalize_action_description(temp_action)}"
                    )
                    if self._toggle_action_history.get(toggle_key, 0) >= 1:
                        continue

            # 동일 요소의 반복 시도는 우선순위 낮추거나 제외
            attempt_key = f"{page_state.url_hash}:{element.element_id}:{action_type}"
            attempt_count = self._action_attempts.get(attempt_key, 0)
            max_attempts = 2
            if (
                element.tag == "a"
                or "back" in description.lower()
                or "next" in description.lower()
            ):
                max_attempts = 4
            if attempt_count >= max_attempts:
                continue
            if attempt_count >= 1:
                priority *= 0.5

            # 링크는 새 페이지 탐색을 우선
            if element.tag == "a" and element.href:
                resolved = urljoin(page_state.url, element.href)
                if resolved:
                    current_host = urlparse(page_state.url).netloc
                    target_host = urlparse(resolved).netloc
                    if target_host and target_host != current_host:
                        priority *= 0.5
                    else:
                        href_hash = self._hash_url(resolved)
                        if href_hash not in self._visited_pages:
                            priority = min(priority * 1.3, 1.0)

            # 파괴적 액션 회피
            if self.config.avoid_destructive:
                destructive_keywords = [
                    "delete",
                    "삭제",
                    "제거",
                    "clear",
                    "reset",
                    "logout",
                    "log out",
                    "sign out",
                    "reset app state",
                ]
                if any(
                    keyword in description.lower() for keyword in destructive_keywords
                ):
                    if any(
                        keyword in description.lower()
                        for keyword in self.config.allow_destructive_keywords
                    ):
                        priority *= 0.6
                    elif action_type == "click":
                        continue
                    priority *= 0.1

            action = TestableAction(
                element_id=element.element_id,
                action_type=action_type,
                description=description,
                priority=priority,
                reasoning=f"{'미테스트' if not element.tested else '재테스트'} 요소",
            )

            action = self._boost_action_priority(action)

            if (
                action.action_type == "click"
                and not element.tested
                and not pending_inputs
                and not self._is_toggle_action(action)
            ):
                self._enqueue_frontier_action(page_state, action)

            actions_with_status.append((action, element.tested))

        actions = [action for action, _ in actions_with_status]
        has_untested = any(not tested for _, tested in actions_with_status)
        if has_untested:
            actions = [action for action, tested in actions_with_status if not tested]
        actions.extend(self._build_navigation_actions(page_state))

        # 우선순위로 정렬
        actions.sort(key=lambda x: x.priority, reverse=True)

        max_actions = 60
        if len(actions) > max_actions:
            category_buckets: Dict[str, List[TestableAction]] = {}
            for action in actions:
                if action.action_type == "fill":
                    category = "fill"
                elif action.action_type == "select":
                    category = "select"
                elif action.action_type == "navigate":
                    category = "navigate"
                elif action.action_type == "click":
                    if "[icon link]" in action.description:
                        category = "icon_link"
                    elif "[icon]" in action.description:
                        category = "icon_button"
                    elif action.description.startswith("링크:"):
                        category = "link"
                    elif action.description.startswith("버튼:"):
                        category = "button"
                    elif action.description.startswith("체크박스"):
                        category = "toggle"
                    else:
                        category = "click"
                else:
                    category = action.action_type
                category_buckets.setdefault(category, []).append(action)

            balanced: List[TestableAction] = []
            per_category = max(2, max_actions // max(len(category_buckets), 1))
            for category in [
                "fill",
                "select",
                "navigate",
                "icon_link",
                "icon_button",
                "link",
                "button",
                "toggle",
                "click",
            ]:
                bucket = category_buckets.get(category, [])
                if not bucket:
                    continue
                balanced.extend(bucket[:per_category])

            if len(balanced) < max_actions:
                remaining = [action for action in actions if action not in balanced]
                balanced.extend(remaining[: max_actions - len(balanced)])

            return balanced[:max_actions]

        return actions

    def _enqueue_frontier_action(
        self,
        page_state: PageState,
        action: TestableAction,
    ) -> None:
        key = f"{page_state.url_hash}:{action.element_id}:{action.action_type}"
        if key in self._action_frontier_set:
            return
        self._action_frontier.append(
            {
                "url_hash": page_state.url_hash,
                "element_id": action.element_id,
                "action_type": action.action_type,
            }
        )
        self._action_frontier_set.add(key)

    def _has_pending_inputs(self, page_state: PageState) -> bool:
        for element in page_state.interactive_elements:
            if element.tag != "input":
                continue
            input_type = (element.type or "text").lower()
            if input_type in ["submit", "button", "hidden", "image"]:
                continue
            if not element.tested:
                return True
        return False

    def _has_tested_inputs(self, page_state: PageState) -> bool:
        for element in page_state.interactive_elements:
            if element.tag != "input":
                continue
            input_type = (element.type or "text").lower()
            if input_type in ["submit", "button", "hidden", "image"]:
                continue
            if element.tested:
                return True
        return False

    def _has_login_form(self, page_state: PageState) -> bool:
        has_password = False
        has_user_input = False
        for element in page_state.interactive_elements:
            if element.tag != "input":
                continue
            input_type = (element.type or "text").lower()
            if input_type == "password":
                has_password = True
            if input_type in ["text", "email"]:
                has_user_input = True
        return has_password and has_user_input

    def _is_high_priority_element(self, element: ElementState) -> bool:
        label = self._element_label(element).lower()
        selector = (element.selector or "").lower()
        haystack = f"{label} {selector}".strip()
        if not haystack:
            return False
        return any(
            keyword in haystack for keyword in self.config.high_priority_keywords
        )

    def _boost_action_priority(self, action: TestableAction) -> TestableAction:
        description = action.description.lower()
        if any(
            keyword in description for keyword in self.config.high_priority_keywords
        ):
            action.priority = min(1.0, action.priority + 0.35)
        return action

    def _normalize_seed_urls(self, start_url: str) -> List[str]:
        seeds: List[str] = []
        for url in self.config.seed_urls:
            if not url:
                continue
            if url.startswith("http://") or url.startswith("https://"):
                seeds.append(url)
            else:
                seeds.append(urljoin(start_url, url))
        return list(dict.fromkeys(seeds))

    def _build_navigation_actions(self, page_state: PageState) -> List[TestableAction]:
        actions: List[TestableAction] = []
        seen: Set[str] = set()
        pending_inputs = self._has_pending_inputs(page_state)
        base_priority = 0.95 if not pending_inputs else 0.4
        for url in self._seed_urls:
            resolved = urljoin(page_state.url, url)
            if self._hash_url(resolved) in self._visited_pages:
                continue
            element_id = f"navigate:{resolved}"
            attempt_key = f"{page_state.url_hash}:{element_id}:navigate"
            if self._action_attempts.get(attempt_key, 0) >= 3:
                continue
            if element_id in seen:
                continue
            seen.add(element_id)
            actions.append(
                TestableAction(
                    element_id=element_id,
                    action_type="navigate",
                    description=f"URL 이동: {resolved}",
                    priority=base_priority,
                    reasoning="탐색 시드",
                )
            )

        actions.extend(self._build_saucedemo_item_actions(page_state, seen))
        return actions

    def _build_saucedemo_item_actions(
        self,
        page_state: PageState,
        seen: Set[str],
    ) -> List[TestableAction]:
        if "saucedemo.com" not in page_state.url:
            return []
        if "inventory.html" not in page_state.url:
            return []
        parsed = urlparse(page_state.url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        actions: List[TestableAction] = []
        pending_inputs = self._has_pending_inputs(page_state)
        base_priority = 0.9 if not pending_inputs else 0.35
        pattern = re.compile(r"item_(\d+)_")
        for element in page_state.interactive_elements:
            selector = element.selector or ""
            match = pattern.search(selector)
            if not match:
                continue
            item_id = match.group(1)
            target_url = f"{base_url}/inventory-item.html?id={item_id}"
            element_id = f"navigate:{target_url}"
            attempt_key = f"{page_state.url_hash}:{element_id}:navigate"
            if self._action_attempts.get(attempt_key, 0) >= 3:
                continue
            if element_id in seen:
                continue
            seen.add(element_id)
            actions.append(
                TestableAction(
                    element_id=element_id,
                    action_type="navigate",
                    description=f"상품 상세 이동: id={item_id}",
                    priority=base_priority,
                    reasoning="상품 상세 직접 이동",
                )
            )
        return actions

    def _resolve_navigation_target(self, element_id: str, current_url: str) -> str:
        target = element_id
        if element_id.startswith("navigate:"):
            target = element_id.split(":", 1)[1]
        if not target:
            return current_url
        return urljoin(current_url, target)

    def _element_label(self, element: ElementState) -> str:
        parts = [
            element.text or "",
            element.aria_label or "",
            element.title or "",
            element.placeholder or "",
            element.role or "",
        ]
        label = next((part for part in parts if part), "")
        return label.strip()

    def _action_signature(self, actions: List[TestableAction]) -> str:
        entries = [
            f"{action.action_type}:{self._normalize_action_description(action)}"
            for action in actions
        ]
        digest = hashlib.md5("|".join(entries).encode("utf-8")).hexdigest()[:12]
        return digest

    def _normalize_action_description(self, action: TestableAction) -> str:
        description = action.description.lower()
        if self._is_toggle_action(action):
            for keyword in [
                "add to cart",
                "remove",
                "open",
                "close",
                "show",
                "hide",
                "expand",
                "collapse",
            ]:
                if keyword in description:
                    return keyword
        return action.description

    def _build_action_for_element(
        self, element: ElementState, action_type: str
    ) -> TestableAction:
        label = self._element_label(element)
        if element.tag == "input":
            if element.type in ["text", "email", "password", "search"]:
                description = f"텍스트 입력({element.type}): {label or element.type}"
            elif element.type in ["checkbox", "radio"]:
                description = f"체크박스/라디오: {label or element.type}"
            else:
                description = f"Input: {element.type or label}"
        elif element.tag == "a":
            description = f"링크: {label or 'Link'}"
        elif element.tag == "button":
            description = f"버튼: {label or 'Button'}"
        elif element.tag == "select":
            description = f"드롭다운: {label}"
        else:
            description = f"{element.tag}: {label or element.role}"

        return TestableAction(
            element_id=element.element_id,
            action_type=action_type,
            description=description,
            priority=0.5,
            reasoning="BFS fallback",
        )

    def _state_key(self, page_state: PageState, actions: List[TestableAction]) -> str:
        action_signature = self._action_signature(actions)
        return f"{page_state.url_hash}:{action_signature}"

    def _is_toggle_action(self, action: TestableAction) -> bool:
        label = action.description.lower()
        toggle_keywords = [
            "add to cart",
            "remove",
            "open",
            "close",
            "show",
            "hide",
            "expand",
            "collapse",
        ]
        return any(keyword in label for keyword in toggle_keywords)

    def _select_frontier_action(
        self,
        page_state: PageState,
        testable_actions: List[TestableAction],
    ) -> Optional[TestableAction]:
        if not self._action_frontier:
            return None

        action_map = {
            f"{page_state.url_hash}:{action.element_id}:{action.action_type}": action
            for action in testable_actions
        }
        element_map = {el.element_id: el for el in page_state.interactive_elements}
        for entry in list(self._action_frontier):
            if entry["url_hash"] != page_state.url_hash:
                continue
            key = f"{entry['url_hash']}:{entry['element_id']}:{entry['action_type']}"
            action = action_map.get(key)
            if action:
                self._action_frontier.remove(entry)
                self._action_frontier_set.discard(key)
                return action
            element = element_map.get(entry["element_id"])
            if element:
                self._action_frontier.remove(entry)
                self._action_frontier_set.discard(key)
                return self._build_action_for_element(element, entry["action_type"])

        return None

    def _build_exploration_prompt(
        self,
        page_state: PageState,
        testable_actions: List[TestableAction],
        action_count: int,
    ) -> str:
        """탐색 프롬프트 생성"""

        # 테스트 가능한 액션을 텍스트로 변환 (최대 30개)
        actions_text = "\n".join(
            [
                f"[{i}] {action.action_type.upper()}: {action.description} (우선순위: {action.priority:.2f})"
                for i, action in enumerate(testable_actions[:60])
            ]
        )

        # 최근 액션 히스토리
        recent_history = (
            "\n".join(self._action_history[-5:])
            if self._action_history
            else "없음 (첫 탐색)"
        )

        # 발견된 이슈 요약
        issues_summary = (
            f"{len(self._found_issues)}개 이슈 발견"
            if self._found_issues
            else "아직 이슈 없음"
        )

        prompt = f"""당신은 웹 애플리케이션 탐색 테스트 에이전트입니다.
화면의 모든 UI 요소를 자율적으로 탐색하고 테스트하여 버그를 찾는 것이 목표입니다.

## 현재 상황
- URL: {page_state.url}
- 탐색 진행: {action_count}/{self.config.max_actions} 액션
- 테스트 완료 요소: {len(self._tested_elements)}개
- 발견된 이슈: {issues_summary}

## 최근 수행한 액션
{recent_history}

## 테스트 가능한 액션 목록 (우선순위 순)
{actions_text}

## 지시사항
1. **우선순위 고려**: 미테스트 요소를 우선 선택하세요
2. **다양성**: 같은 유형만 계속 테스트하지 말고 다양한 UI 요소를 테스트하세요
3. **탐색 확대**: 방문하지 않은 링크나 새 페이지로 이어질 요소를 우선 선택하세요
4. **외부 링크 제외**: 현재 도메인 밖으로 이동하는 링크는 선택하지 마세요
5. **BFS 탐색**: 새로 발견된 내부 링크는 발견 순서대로 우선 선택하세요
6. **버그 탐지**: 에러 메시지, 깨진 UI, 예상치 못한 동작을 찾으세요
7. **종료 조건**: 더 이상 테스트할 요소가 없거나, 충분히 탐색했다면 should_continue: false

## 입력값 생성 규칙 (fill 액션인 경우)
- **중요**: 화면에 테스트 계정 정보가 보이면 반드시 그 값을 사용하세요!
- 사용자명/아이디 필드: input_values에 "username" 키로 값 지정
- 비밀번호 필드: input_values에 "password" 키로 값 지정
- 이메일 필드: "test.explorer@example.com"
- 일반 텍스트: "Test input"

## 응답 형식 (JSON만, 마크다운 없이)
{{
    "should_continue": true | false,
    "selected_action_index": 액션 인덱스 (0-59, 선택 안 하면 null),
    "input_values": {{"username": "사용자명", "password": "비밀번호"}},  // fill 액션인 경우, 필요한 키만 포함
    "reasoning": "이 액션을 선택한 이유 또는 종료 이유",
    "confidence": 0.0~1.0,
    "expected_outcome": "예상되는 결과"
}}

JSON 응답:"""

        return prompt

    def _parse_exploration_decision(
        self,
        response_text: str,
        testable_actions: List[TestableAction],
    ) -> ExplorationDecision:
        """LLM 응답을 ExplorationDecision으로 파싱"""
        # 마크다운 코드 블록 제거
        text = response_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            data = json.loads(text)

            should_continue = data.get("should_continue", True)
            action_index = data.get("selected_action_index")
            selected_action = None

            if action_index is not None and 0 <= action_index < len(testable_actions):
                selected_action = testable_actions[action_index]

            return ExplorationDecision(
                should_continue=should_continue,
                selected_action=selected_action,
                input_values=data.get("input_values", {}),
                reasoning=data.get("reasoning", ""),
                confidence=data.get("confidence", 0.5),
                expected_outcome=data.get("expected_outcome", ""),
            )

        except (json.JSONDecodeError, ValueError) as e:
            self._log(f"JSON 파싱 실패: {e}")
            # 기본값: 첫 번째 액션 선택
            if testable_actions:
                return ExplorationDecision(
                    should_continue=True,
                    selected_action=testable_actions[0],
                    reasoning=f"파싱 오류로 기본 액션 선택: {e}",
                    confidence=0.3,
                )
            else:
                return ExplorationDecision(
                    should_continue=False,
                    reasoning="파싱 오류 및 액션 없음",
                    confidence=0.0,
                )

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
        selector = self._find_selector_by_element_id(action.element_id, page_state)
        if not selector:
            return False, f"셀렉터를 찾을 수 없음: {action.element_id}", []

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
                self._execute_action("scrollIntoView", selector=selector)
                success, error = self._execute_action("click", selector=selector)
                if did_open_menu:
                    close_selector = self._find_close_menu_selector(page_state)
                    if close_selector:
                        self._log("ℹ️ 메뉴 항목 클릭 후 메뉴 닫기 건너뜀")
            elif action.action_type == "fill":
                # 입력값 결정
                value = self._determine_input_value(action, decision.input_values)
                success, error = self._execute_action(
                    "fill", selector=selector, value=value
                )

                # 셀렉터 실패 시 좌표 기반 입력 fallback
                if not success:
                    element_state = self._find_element_by_id(
                        action.element_id, page_state
                    )
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
                success, error = self._execute_action(
                    "select", selector=selector, value="1"
                )
            elif action.action_type == "hover":
                success, error = self._execute_action("hover", selector=selector)
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
                issues.append(issue)

            # 액션 실패도 이슈로 기록
            if not success and error:
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
        description = action.description.lower()
        selector_lower = selector.lower()
        if "sidebar" in selector_lower or "menu" in selector_lower:
            return "링크" in description or "메뉴" in description
        return False

    def _find_open_menu_selector(self, page_state: PageState) -> Optional[str]:
        for element in page_state.interactive_elements:
            if element.tag != "button":
                continue
            label = (element.text or "").lower()
            aria_label = (element.aria_label or "").lower()
            combined = f"{label} {aria_label}".strip()
            if not combined:
                continue
            if "menu" in combined and "close" not in combined and "open" in combined:
                selector = self._find_selector_by_element_id(
                    element.element_id, page_state
                )
                if selector:
                    return selector
        return None

    def _find_close_menu_selector(self, page_state: PageState) -> Optional[str]:
        for element in page_state.interactive_elements:
            if element.tag != "button":
                continue
            label = (element.text or "").lower()
            aria_label = (element.aria_label or "").lower()
            combined = f"{label} {aria_label}".strip()
            if not combined:
                continue
            if "menu" in combined and "close" in combined:
                selector = self._find_selector_by_element_id(
                    element.element_id, page_state
                )
                if selector:
                    return selector
        return None

    def _execute_action(
        self,
        action: str,
        selector: Optional[str] = None,
        value: Optional[object] = None,
        url: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """MCP Host를 통해 액션 실행"""

        params: Dict[str, object] = {
            "session_id": self.session_id,
            "action": action,
            "url": url or "",
            "selector": selector or "",
        }

        if value is not None:
            params["value"] = value
        if action == "goto" and url:
            params["value"] = url

        try:
            response = requests.post(
                f"{self.mcp_host_url}/execute",
                json={"action": "execute_action", "params": params},
                timeout=self.config.action_timeout,
            )

            # HTTP 상태 코드 로깅
            if response.status_code != 200:
                self._log(f"⚠️  HTTP {response.status_code}: {response.text[:200]}")

            data = response.json()

            if data.get("success"):
                return True, None
            else:
                error_msg = (
                    data.get("error")
                    or data.get("detail")
                    or f"Unknown error (response: {data})"
                )
                self._log(f"❌ Action failed: {error_msg}")
                return False, error_msg

        except Exception as e:
            return False, str(e)

    def _evaluate_selector(self, selector: str, script: str) -> Optional[str]:
        params: Dict[str, object] = {
            "session_id": self.session_id,
            "action": "evaluate",
            "url": "",
            "selector": selector,
            "value": script,
        }
        try:
            response = requests.post(
                f"{self.mcp_host_url}/execute",
                json={"action": "execute_action", "params": params},
                timeout=self.config.action_timeout,
            )
            data = response.json()
            if not data.get("success"):
                return None
            result = data.get("result")
            return str(result) if result is not None else None
        except Exception:
            return None

    def _get_select_state(self, selector: Optional[str]) -> Optional[dict]:
        if not selector:
            return None
        result = self._evaluate_selector(
            selector,
            """
            el => JSON.stringify({
                value: el.value ?? '',
                text: (el.selectedOptions && el.selectedOptions[0]
                    ? el.selectedOptions[0].textContent
                    : '')
            })
            """,
        )
        if not result:
            return None
        try:
            return json.loads(result)
        except Exception:
            return None

    def _get_toggle_state(self, selector: Optional[str]) -> Optional[dict]:
        if not selector:
            return None
        result = self._evaluate_selector(
            selector,
            """
            el => JSON.stringify({
                checked: typeof el.checked === 'boolean' ? el.checked : null,
                pressed: (el.getAttribute && el.getAttribute('aria-pressed'))
                    ? el.getAttribute('aria-pressed') === 'true'
                    : null,
                selected: (el.getAttribute && el.getAttribute('aria-selected'))
                    ? el.getAttribute('aria-selected') === 'true'
                    : null,
                expanded: (el.getAttribute && el.getAttribute('aria-expanded'))
                    ? el.getAttribute('aria-expanded') === 'true'
                    : null
            })
            """,
        )
        if not result:
            return None
        try:
            return json.loads(result)
        except Exception:
            return None

    def _build_element_id(
        self,
        url_hash: str,
        element: DOMElement,
        selector: str,
    ) -> str:
        """요소 고유 ID 생성"""
        if selector:
            return f"{url_hash}:{selector}"

        parts = [
            element.tag,
            element.type or "",
            element.placeholder or "",
            element.aria_label or "",
            element.text[:30] if element.text else "",
        ]
        filtered = [part for part in parts if part]
        if not filtered:
            return f"{url_hash}:{element.tag}"
        return f"{url_hash}:" + ":".join(filtered)

    def _find_selector_by_element_id(
        self,
        element_id: str,
        page_state: PageState,
    ) -> Optional[str]:
        """element_id로 셀렉터 찾기"""
        element = self._find_element_by_id(element_id, page_state)
        if not element:
            return None
        selector = element.selector
        if selector and self._is_selector_safe(selector):
            return selector
        fallback = self._fallback_selector_for_element(element, page_state)
        return fallback or selector

    def _find_element_by_id(
        self,
        element_id: str,
        page_state: PageState,
    ) -> Optional[ElementState]:
        """element_id로 ElementState 찾기"""
        for element in page_state.interactive_elements:
            if element.element_id == element_id:
                return element
        return None

    def _is_selector_safe(self, selector: str) -> bool:
        if not selector:
            return False
        if selector.startswith("role=") or selector.startswith("text="):
            return True
        if "[" in selector or "]" in selector:
            return False
        parts = selector.split(".")
        for part in parts[1:]:
            segment = part.split(" ")[0].split(">")[0]
            if ":" in segment:
                return False
        return True

    def _fallback_selector_for_element(
        self,
        element: ElementState,
        page_state: PageState,
    ) -> Optional[str]:
        label = self._element_label(element)
        if element.tag == "select":
            select_index = 0
            for candidate in page_state.interactive_elements:
                if candidate.tag == "select":
                    if candidate.element_id == element.element_id:
                        return f"select >> nth={select_index}"
                    select_index += 1
            return "select"

        if element.tag == "input":
            if element.placeholder:
                return f'input[placeholder="{element.placeholder}"]'
            if element.aria_label:
                return f'input[aria-label="{element.aria_label}"]'
            if element.type:
                input_index = 0
                for candidate in page_state.interactive_elements:
                    if candidate.tag == "input" and candidate.type == element.type:
                        if candidate.element_id == element.element_id:
                            return f'input[type="{element.type}"] >> nth={input_index}'
                        input_index += 1

        if element.aria_label:
            return f'[aria-label="{element.aria_label}"]'
        if element.role:
            if label:
                return f'role={element.role}[name="{label}"]'
            return f"role={element.role}"
        if label and len(label) <= 40:
            return f'text="{label}"'
        return None

    def _determine_input_value(
        self,
        action: TestableAction,
        input_values: Dict[str, str],
    ) -> str:
        """입력 필드에 넣을 값 결정"""
        desc_lower = action.description.lower()

        if "saucedemo.com" in (self._current_url or ""):
            if "password" in desc_lower or "비밀번호" in desc_lower:
                return "secret_sauce"
            if "username" in desc_lower or "사용자" in desc_lower:
                return "standard_user"

        # 명시적으로 제공된 값 사용 (LLM이 제공한 input_values 우선)
        if input_values:
            # 비밀번호 필드면 password 키 찾기
            if "비밀번호" in desc_lower or "password" in desc_lower:
                for key in ["password", "비밀번호", "pw", "secret"]:
                    if key in input_values:
                        self._log(f"📝 비밀번호 입력: {input_values[key]}")
                        return input_values[key]
            # 사용자명/텍스트 필드면 username 키 찾기
            else:
                for key in ["username", "user", "id", "아이디", "사용자"]:
                    if key in input_values:
                        self._log(f"📝 사용자명 입력: {input_values[key]}")
                        return input_values[key]
            # 매칭 안 되면 첫 번째 값 사용
            first_key = list(input_values.keys())[0]
            first_value = input_values[first_key]
            self._log(f"📝 입력값 사용 (첫번째): {first_key}={first_value}")
            return first_value

        # 기본값 생성
        if "email" in desc_lower or "이메일" in desc_lower:
            return "test.explorer@example.com"
        elif "password" in desc_lower or "비밀번호" in desc_lower:
            return "TestPass123!"
        elif "name" in desc_lower or "이름" in desc_lower:
            return "Test User"
        elif "phone" in desc_lower or "전화" in desc_lower:
            return "010-1234-5678"
        elif "search" in desc_lower or "검색" in desc_lower:
            return "test"
        else:
            return "Test input"

    def _create_error_issue(
        self,
        action: TestableAction,
        error_logs: List[str],
        url: str,
    ) -> FoundIssue:
        """콘솔 에러 이슈 생성"""
        issue_id = f"ERR_{int(time.time())}_{len(self._found_issues)}"

        return FoundIssue(
            issue_id=issue_id,
            issue_type=IssueType.ERROR,
            severity="high",
            title=f"JavaScript 에러 발생: {action.description}",
            description=f"액션 실행 후 콘솔 에러가 발생했습니다.\n\n에러 로그:\n"
            + "\n".join(error_logs[:5]),
            url=url,
            steps_to_reproduce=[
                f"1. {url}로 이동",
                f"2. {action.description}를 {action.action_type}",
            ],
            error_message=error_logs[0] if error_logs else None,
            console_logs=error_logs,
        )

    def _create_action_failure_issue(
        self,
        action: TestableAction,
        error_message: str,
        url: str,
    ) -> FoundIssue:
        """액션 실패 이슈 생성"""
        issue_id = f"FAIL_{int(time.time())}_{len(self._found_issues)}"

        return FoundIssue(
            issue_id=issue_id,
            issue_type=IssueType.UNEXPECTED_BEHAVIOR,
            severity="medium",
            title=f"액션 실행 실패: {action.description}",
            description=f"액션을 실행했지만 실패했습니다.\n\n오류: {error_message}",
            url=url,
            steps_to_reproduce=[
                f"1. {url}로 이동",
                f"2. {action.description}를 {action.action_type}",
            ],
            error_message=error_message,
        )

    def _create_intent_issue(
        self,
        action: TestableAction,
        url: str,
        reason: str,
        screenshot_before: Optional[str] = None,
        screenshot_after: Optional[str] = None,
    ) -> FoundIssue:
        issue_id = f"INTENT_{int(time.time())}_{len(self._found_issues)}"
        return FoundIssue(
            issue_id=issue_id,
            issue_type=IssueType.UNEXPECTED_BEHAVIOR,
            severity="low",
            title=f"의도한 결과 미확인: {action.description}",
            description=f"액션 실행 후 의도한 변화가 감지되지 않았습니다.\n\n사유: {reason}",
            url=url,
            steps_to_reproduce=[
                f"1. {url}로 이동",
                f"2. {action.description}를 {action.action_type}",
            ],
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
        if action.action_type == "navigate":
            target_url = self._resolve_navigation_target(action.element_id, before_url)
            if self._normalize_url_for_compare(
                after_url
            ) == self._normalize_url_for_compare(target_url):
                return True, None
            if after_url != before_url:
                return True, None
            return False, f"URL 이동이 확인되지 않음: {target_url}"

        if action.action_type == "fill":
            selector = self._find_selector_by_element_id(
                action.element_id, before_state
            )
            if not selector:
                return True, None
            if not expected_input:
                return True, None
            current_value = self._evaluate_selector(
                selector, "el => (el.value ?? el.textContent ?? '').toString()"
            )
            if current_value is None:
                return True, None
            if self._normalize_text(expected_input) in self._normalize_text(
                current_value
            ):
                return True, None
            return False, "입력값 반영이 확인되지 않음"

        if action.action_type == "hover":
            return True, None

        if action.action_type == "select":
            selector = self._find_selector_by_element_id(
                action.element_id, before_state
            )
            if not selector:
                return True, None
            after_select_state = self._get_select_state(selector)
            expected_label = None
            if ":" in action.description:
                expected_label = action.description.split(":", 1)[1].strip()
            if expected_label and after_select_state:
                after_text = self._normalize_text(after_select_state.get("text"))
                if self._normalize_text(expected_label) in after_text:
                    return True, None
            if before_select_state and after_select_state:
                if before_select_state.get("value") != after_select_state.get("value"):
                    return True, None
                if self._normalize_text(
                    before_select_state.get("text")
                ) != self._normalize_text(after_select_state.get("text")):
                    return True, None
            if after_select_state and (
                after_select_state.get("value") or after_select_state.get("text")
            ):
                return True, None
            return False, "드롭다운 선택 결과가 확인되지 않음"

        if action.action_type in ["click", "select"]:
            if after_url != before_url:
                return True, None

            if (
                screenshot_before
                and screenshot_after
                and screenshot_before != screenshot_after
            ):
                return True, None

            before_count = len(before_state.interactive_elements)
            after_count = len(after_state.interactive_elements)
            if before_count != after_count:
                return True, None

            element_before = self._find_element_by_id(action.element_id, before_state)
            selector = element_before.selector if element_before else None
            element_after = (
                self._find_element_by_selector(selector, after_state)
                if selector
                else None
            )
            if selector and element_after is None:
                return True, None

            if selector:
                toggle_state = self._get_toggle_state(selector)
                if toggle_state:
                    if before_toggle_state and toggle_state != before_toggle_state:
                        return True, None
                    if toggle_state.get("checked") is True:
                        return True, None
                    if toggle_state.get("pressed") is True:
                        return True, None
                    if toggle_state.get("selected") is True:
                        return True, None
                    if toggle_state.get("expanded") is True:
                        return True, None
            if element_before and element_after:
                if self._normalize_text(element_before.text) != self._normalize_text(
                    element_after.text
                ):
                    return True, None
                if (element_before.aria_label or "").strip() != (
                    element_after.aria_label or ""
                ).strip():
                    return True, None

            return False, "URL/DOM 변화가 감지되지 않음"

        return True, None

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
        """콘솔 에러 리포트"""
        issue_id = f"CONSOLE_{int(time.time())}"

        issue = FoundIssue(
            issue_id=issue_id,
            issue_type=IssueType.ERROR,
            severity="medium",
            title=f"콘솔 에러 감지: {len(console_errors)}개",
            description=f"페이지 로드 시 콘솔 에러가 발견되었습니다.\n\n"
            + "\n".join(console_errors[:5]),
            url=self._current_url,
            steps_to_reproduce=[f"1. {self._current_url}로 이동"],
            console_logs=console_errors,
            screenshot_before=screenshot,
        )

        self._found_issues.append(issue)

    def _calculate_coverage(self) -> Dict[str, Any]:
        """테스트 커버리지 계산"""
        total_elements = 0
        tested_elements = len(self._tested_elements)

        for page in self._visited_pages.values():
            total_elements += len(page.interactive_elements)

        return {
            "total_interactive_elements": total_elements,
            "tested_elements": tested_elements,
            "coverage_percentage": (tested_elements / total_elements * 100)
            if total_elements > 0
            else 0,
            "total_pages": len(self._visited_pages),
        }

    def _determine_completion_reason(
        self,
        action_count: int,
        steps: List[ExplorationStep],
    ) -> str:
        """탐색 종료 이유 결정"""
        if action_count >= self.config.max_actions:
            return f"최대 액션 수 도달 ({self.config.max_actions})"
        elif steps and not steps[-1].decision.should_continue:
            return steps[-1].decision.reasoning
        else:
            return "탐색 완료"

    def _print_summary(self, result: ExplorationResult):
        """탐색 결과 요약 출력"""
        self._log("\n" + "=" * 60)
        self._log("🎉 탐색 완료!")
        self._log("=" * 60)
        self._log(f"총 액션 수: {result.total_actions}")
        self._log(f"방문한 페이지: {result.total_pages_visited}개")
        self._log(f"테스트한 요소: {result.total_elements_tested}개")
        self._log(f"커버리지: {result.get_coverage_percentage():.1f}%")
        self._log(f"발견한 이슈: {len(result.issues_found)}개")

        if result.issues_found:
            critical = len([i for i in result.issues_found if i.severity == "critical"])
            high = len([i for i in result.issues_found if i.severity == "high"])
            medium = len([i for i in result.issues_found if i.severity == "medium"])
            low = len([i for i in result.issues_found if i.severity == "low"])

            self._log(f"  - Critical: {critical}개")
            self._log(f"  - High: {high}개")
            self._log(f"  - Medium: {medium}개")
            self._log(f"  - Low: {low}개")

        self._log(f"소요 시간: {result.duration_seconds:.1f}초")
        self._log(f"종료 이유: {result.completion_reason}")
        self._log("=" * 60)

    def _hash_url(self, url: str) -> str:
        """URL 해시 생성 (중복 방지)"""
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        query = parsed.query or ""
        if any(key in query for key in ["id=", "item=", "product="]):
            base_url = f"{base_url}?{query}"
        return hashlib.md5(base_url.encode()).hexdigest()[:12]

    def _call_gemini_text_only(self, prompt: str) -> str:
        """스크린샷 없이 텍스트만으로 Gemini 호출"""
        from google.genai import types

        response = self.llm.client.models.generate_content(
            model=self.llm.model,
            contents=[types.Content(parts=[types.Part(text=prompt)])],
            config=types.GenerateContentConfig(
                max_output_tokens=4096,
                temperature=0.2,
            ),
        )

        return response.text if response.text else ""
