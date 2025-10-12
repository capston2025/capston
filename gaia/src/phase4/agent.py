"""Playwright MCP orchestration for runtime discovery."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

import requests

from gaia.src.phase1.analyzer import SpecAnalyzer
from gaia.src.tracker.checklist import ChecklistTracker
from gaia.src.utils.config import CONFIG, MCPConfig
from gaia.src.utils.models import DomElement, TestScenario


_ID_RE = re.compile(r"#([a-z0-9_-]+)")
_CLASS_RE = re.compile(r"\.([a-z0-9_-]+)")
_ATTR_RE = re.compile(r"\[([^=\]]+)=['\"]?([^'\"]+)['\"]?\]")
_TEXT_RE = re.compile(r":has-text\(['\"]([^'\"]+)['\"]\)")
_TAG_RE = re.compile(r"^[a-z][a-z0-9_-]*")
_TOKEN_SPLIT_RE = re.compile(r"[^0-9a-z가-힣_-]+")
_NON_ASCII_RE = re.compile(r"[^\x00-\x7f]")

_CSS_STOPWORDS = {
    "button",
    "input",
    "form",
    "link",
    "text",
    "type",
    "field",
    "item",
    "row",
    "col",
    "icon",
    "label",
    "wrap",
    "container",
    "group",
    "list",
    "card",
    "title",
    "body",
    "content",
    "section",
}

_GENERIC_CLASSES = {
    "btn",
    "button",
    "primary",
    "secondary",
    "active",
    "selected",
    "disabled",
    "focus",
    "hover",
    "link",
    "item",
    "text",
}

_STRONG_ATTRIBUTES = {
    "name",
    "data-testid",
    "data-test",
    "aria-label",
    "placeholder",
    "href",
    "value",
    "for",
    "id",
    "title",
}


@dataclass(slots=True)
class _SelectorHints:
    raw: str
    tag: str | None
    ids: set[str]
    classes: set[str]
    attributes: Dict[str, str]
    text_fragments: set[str]
    keywords: set[str]


@dataclass(slots=True)
class _ElementHints:
    raw_selector: str
    tag: str | None
    ids: set[str]
    classes: set[str]
    attributes: Dict[str, str]
    text_fragments: set[str]
    keywords: set[str]


class MCPClient:
    """Thin wrapper around the Playwright MCP host."""

    def __init__(self, config: MCPConfig | None = None) -> None:
        self.config = config or CONFIG.mcp

    def analyze_dom(self, url: str) -> List[DomElement]:
        payload = {"action": "analyze_page", "params": {"url": url}}
        try:
            response = requests.post(
                f"{self.config.host_url}/execute",
                json=payload,
                timeout=self.config.request_timeout,
            )
            response.raise_for_status()
        except requests.RequestException:
            return self._fallback_elements(url)

        data = response.json() if response.content else {}
        elements_raw = data.get("elements", []) if isinstance(data, dict) else []
        if not elements_raw:
            return self._fallback_elements(url)

        elements: List[DomElement] = []
        for element in elements_raw:
            try:
                elements.append(DomElement.model_validate(element))
            except Exception:
                continue
        if not elements:
            return self._fallback_elements(url)
        return elements

    def _fallback_elements(self, url: str) -> List[DomElement]:  # noqa: ARG002 - url for future heuristics
        return [
            DomElement(
                tag="input",
                selector="input[type='text'], input[type='email'], #username, #user_id",
                text="",
                attributes={"type": "text"},
                element_type="input",
            ),
            DomElement(
                tag="input",
                selector="input[type='password'], #password, #user_pwd",
                text="",
                attributes={"type": "password"},
                element_type="input",
            ),
            DomElement(
                tag="button",
                selector="button[type='submit'], input[type='submit'], button:has-text('로그인'), button:has-text('LOGIN')",
                text="로그인",
                attributes={"type": "submit"},
                element_type="button",
            ),
        ]


class AgentOrchestrator:
    """Coordinates DOM discovery, planning, and checklist tracking."""

    def __init__(
        self,
        analyzer: SpecAnalyzer | None = None,
        tracker: ChecklistTracker | None = None,
        mcp_client: MCPClient | None = None,
    ) -> None:
        self.analyzer = analyzer or SpecAnalyzer()
        self.tracker = tracker or ChecklistTracker()
        self.mcp_client = mcp_client or MCPClient()
        self._last_dom: List[DomElement] = []
        self._last_blocked: Dict[str, List[str]] = {}

    def plan_for_url(
        self,
        url: str,
        *,
        document_text: str | None = None,
        scenarios: Sequence[TestScenario] | None = None,
    ) -> List[TestScenario]:
        dom_elements = self.mcp_client.analyze_dom(url)
        provided_scenarios = list(scenarios or [])

        if not provided_scenarios:
            provided_scenarios = self.analyzer.generate_from_context(dom_elements, document_text)
        if not provided_scenarios:
            provided_scenarios = self.analyzer.generate_from_spec(document_text or "")

        self.tracker.seed_from_scenarios(provided_scenarios)
        executable, blocked = self._partition_scenarios(provided_scenarios, dom_elements)
        self._last_dom = dom_elements
        self._last_blocked = blocked

        self._auto_mark(dom_elements)
        return executable

    @property
    def last_dom_snapshot(self) -> List[DomElement]:
        return list(self._last_dom)

    @property
    def blocked_scenarios(self) -> Dict[str, List[str]]:
        return dict(self._last_blocked)

    def _auto_mark(self, elements: Sequence[DomElement]) -> None:
        for element in elements:
            selector = element.selector.lower()
            element_json = json.dumps(element.model_dump(), ensure_ascii=False)
            if "login" in selector or "로그인" in element.text.lower():
                self.tracker.mark_found("TC_001", evidence=element_json)
            if "signup" in selector or "회원가입" in element.text.lower():
                self.tracker.mark_by_predicate("가입", evidence=element_json)

    def _partition_scenarios(
        self,
        scenarios: Sequence[TestScenario],
        dom_elements: Sequence[DomElement],
    ) -> Tuple[List[TestScenario], Dict[str, List[str]]]:
        executable: List[TestScenario] = []
        blocked: Dict[str, List[str]] = {}
        for scenario in scenarios:
            missing = self._missing_selectors(scenario, dom_elements)
            if missing:
                blocked[scenario.id] = missing
            else:
                executable.append(scenario)
        return executable, blocked

    def _missing_selectors(
        self,
        scenario: TestScenario,
        dom_elements: Sequence[DomElement],
    ) -> List[str]:
        if not scenario.steps:
            return []

        dom_hints = [self._element_hints(element) for element in dom_elements]
        missing: List[str] = []
        for step in scenario.steps:
            selector = step.selector.strip()
            if not selector:
                # Try to auto-match based on step description
                auto_selector = self._auto_match_selector(step.description, dom_elements)
                if auto_selector:
                    # Update step selector in-place
                    step.selector = auto_selector
                else:
                    missing.append(f"[empty] {step.description}")
                continue

            candidates = [part.strip() for part in selector.split(",") if part.strip()]
            if not candidates:
                missing.append(selector)
                continue

            selector_hints = [self._parse_selector(candidate) for candidate in candidates]
            if not any(self._selector_matches(hints, dom_hints) for hints in selector_hints):
                missing.append(selector)
        return missing

    def _auto_match_selector(
        self,
        description: str,
        dom_elements: Sequence[DomElement],
    ) -> str | None:
        """
        Attempt to automatically match a step description to a DOM element selector.

        Args:
            description: Step description (e.g., "로그인 버튼 클릭", "이메일 입력")
            dom_elements: Available DOM elements

        Returns:
            Selector string if match found, None otherwise
        """
        if not description or not dom_elements:
            return None

        desc_lower = description.lower()
        desc_keywords = self._extract_keywords(description, allow_short=True)

        # Scoring candidates
        candidates: List[Tuple[DomElement, int]] = []

        for element in dom_elements:
            score = 0
            element_text = (element.text or "").lower()
            element_selector = (element.selector or "").lower()

            # Direct text match (highest priority)
            if element_text and element_text in desc_lower:
                score += 10
            elif desc_lower in element_text:
                score += 8

            # Keyword overlap
            element_hints = self._element_hints(element)
            keyword_overlap = desc_keywords & element_hints.keywords
            if keyword_overlap:
                score += len(keyword_overlap) * 2

            # Attribute matching (aria-label, placeholder, etc.)
            for attr_key, attr_value in (element.attributes or {}).items():
                if not isinstance(attr_value, str):
                    continue
                attr_lower = attr_value.lower()
                if attr_lower in desc_lower or any(kw in attr_lower for kw in desc_keywords):
                    score += 3

            # Element type heuristics
            if "입력" in desc_lower or "input" in desc_lower or "텍스트" in desc_lower:
                if element.element_type == "input" or element.tag == "input":
                    score += 5
            elif "버튼" in desc_lower or "button" in desc_lower or "클릭" in desc_lower:
                if element.element_type == "button" or element.tag == "button":
                    score += 5
            elif "링크" in desc_lower or "link" in desc_lower:
                if element.element_type == "link" or element.tag == "a":
                    score += 5

            if score > 0:
                candidates.append((element, score))

        # Return best match if score is sufficient
        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            best_element, best_score = candidates[0]
            if best_score >= 5:  # Minimum confidence threshold
                return best_element.selector

        return None

    def _selector_matches(
        self,
        hints: _SelectorHints,
        dom_hints: Sequence[_ElementHints],
    ) -> bool:
        if not hints.raw:
            return False

        raw_lower = hints.raw.lower()
        for element in dom_hints:
            if self._raw_selector_hit(raw_lower, element.raw_selector):
                return True

            score = 0

            if hints.tag and element.tag and hints.tag == element.tag:
                score += 2

            if hints.ids and hints.ids & element.ids:
                score += 6

            if hints.classes:
                overlap = hints.classes & element.classes
                if overlap:
                    score += min(4, len(overlap) * 2)

            if hints.attributes:
                for key, value in hints.attributes.items():
                    element_value = element.attributes.get(key)
                    if element_value == value:
                        score += 5
                    elif element_value and value in element_value:
                        score += 2

            if hints.text_fragments:
                if hints.text_fragments & element.text_fragments:
                    score += 5

            if hints.keywords:
                keyword_overlap = hints.keywords & element.keywords
                if keyword_overlap:
                    score += min(3, len(keyword_overlap))

            if score >= 6:
                return True

        return False

    def _raw_selector_hit(self, candidate: str, element_selector: str) -> bool:
        selector = element_selector.strip().lower()
        if not selector:
            return False
        if candidate == selector:
            return True
        if candidate in selector or selector in candidate:
            return True
        return False

    def _parse_selector(self, selector: str) -> _SelectorHints:
        lowered = selector.lower()
        tag_match = _TAG_RE.match(lowered)
        tag = tag_match.group(0) if tag_match else None

        ids = {match for match in _ID_RE.findall(lowered)}
        classes = {
            cls
            for cls in _CLASS_RE.findall(lowered)
            if cls not in _GENERIC_CLASSES and len(cls) > 1
        }

        attributes: Dict[str, str] = {}
        for attr, value in _ATTR_RE.findall(lowered):
            attr_norm = attr.strip().lower()
            value_norm = value.strip().lower()
            if not attr_norm or not value_norm:
                continue
            if attr_norm in _STRONG_ATTRIBUTES or attr_norm.startswith("data-"):
                attributes[attr_norm] = value_norm

        text_fragments = {
            frag.strip().lower()
            for frag in _TEXT_RE.findall(lowered)
            if frag.strip()
        }

        keywords = self._extract_keywords(selector)

        return _SelectorHints(
            raw=selector,
            tag=tag,
            ids=ids,
            classes=classes,
            attributes=attributes,
            text_fragments=text_fragments,
            keywords=keywords,
        )

    def _element_hints(self, element: DomElement) -> _ElementHints:
        raw_selector = (element.selector or "").strip()
        lowered_selector = raw_selector.lower()

        tag = (element.tag or "").strip().lower() or None
        if not tag:
            match = _TAG_RE.match(lowered_selector)
            if match:
                tag = match.group(0)

        ids: set[str] = set(_ID_RE.findall(lowered_selector))
        attributes: Dict[str, str] = {}
        classes: set[str] = set(_CLASS_RE.findall(lowered_selector))

        attrs = element.attributes or {}
        for key, value in attrs.items():
            if value is None:
                continue
            if not isinstance(value, str):
                value_str = str(value)
            else:
                value_str = value
            attr_key = key.strip().lower()
            attr_value = value_str.strip().lower()
            if not attr_key or not attr_value:
                continue

            if attr_key == "id":
                ids.add(attr_value)

            if attr_key == "class":
                classes.update(
                    part
                    for part in attr_value.split()
                    if part and part not in _GENERIC_CLASSES
                )
                continue

            if attr_key in _STRONG_ATTRIBUTES or attr_key.startswith("data-"):
                attributes[attr_key] = attr_value
                if attr_key == "aria-label":
                    attributes.setdefault("label", attr_value)

            if attr_key == "type" and attr_value:
                attributes.setdefault("type", attr_value)

        classes = {cls for cls in classes if cls and cls not in _GENERIC_CLASSES}

        text_sources = []
        if element.text:
            text_sources.append(str(element.text))
        for attr_key in ("aria-label", "placeholder", "value", "title", "alt"):
            attr_val = attrs.get(attr_key)
            if attr_val:
                text_sources.append(str(attr_val))

        text_fragments: set[str] = set()
        for source in text_sources:
            normalized = source.strip()
            if not normalized:
                continue
            text_fragments.add(normalized.lower())
            text_fragments.update(self._extract_keywords(normalized, allow_short=True))

        keywords = set(text_fragments)
        keywords.update(self._extract_keywords(" ".join(classes)))
        keywords.update(self._extract_keywords(raw_selector))

        return _ElementHints(
            raw_selector=raw_selector,
            tag=tag,
            ids=ids,
            classes=classes,
            attributes=attributes,
            text_fragments=text_fragments,
            keywords=keywords,
        )

    def _extract_keywords(
        self,
        source: str,
        *,
        allow_short: bool = False,
    ) -> set[str]:
        if not source:
            return set()

        lowered = source.lower()
        tokens = _TOKEN_SPLIT_RE.split(lowered)
        keywords: set[str] = set()
        for token in tokens:
            token = token.strip()
            if not token:
                continue
            if len(token) <= 1 and not allow_short:
                continue
            if token.isdigit():
                continue
            if token in _CSS_STOPWORDS:
                continue
            if token in _GENERIC_CLASSES:
                continue
            keywords.add(token)
        return keywords
