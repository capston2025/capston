from __future__ import annotations


import os
import hashlib
import json as json_module
from typing import Any, Dict, List


def build_snapshot_dom_hash(url: str, elements: List[Dict[str, Any]]) -> str:
    compact: List[Dict[str, Any]] = []
    for el in elements:
        attrs = el.get("attributes") or {}
        compact.append(
            {
                "tag": el.get("tag", ""),
                "text": (el.get("text") or "")[:80],
                "selector": el.get("selector", ""),
                "full_selector": el.get("full_selector", ""),
                "frame_index": el.get("frame_index", 0),
                "role": attrs.get("role", ""),
                "type": attrs.get("type", ""),
                "aria_label": attrs.get("aria-label", ""),
            }
        )

    raw = json_module.dumps(
        {
            "url": (url or "").strip(),
            "elements": compact,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def apply_selector_strategy(elements: List[Dict[str, Any]], strategy: str) -> None:
    select_index = 0
    tag_indices: Dict[str, int] = {}

    for element in elements:
        tag = element.get("tag") or ""
        text = (element.get("text") or "").strip()
        attrs = element.get("attributes") or {}

        if tag == "select":
            element["selector"] = f"select >> nth={select_index}"
            select_index += 1
            continue

        if strategy == "role":
            role = attrs.get("role")
            aria_label = attrs.get("aria-label") or ""
            placeholder = attrs.get("placeholder") or ""
            safe_text = text.replace('"', "'") if text else ""
            safe_label = aria_label.replace('"', "'") if aria_label else ""
            safe_placeholder = placeholder.replace('"', "'") if placeholder else ""

            if role and safe_text:
                element["selector"] = f'role={role}[name="{safe_text}"]'
                continue
            if safe_label:
                element["selector"] = f'[aria-label="{safe_label}"]'
                continue
            if safe_placeholder and tag in {"input", "textarea"}:
                element["selector"] = f'{tag}[placeholder="{safe_placeholder}"]'
                continue

        if strategy == "nth":
            index = tag_indices.get(tag, 0)
            element["selector"] = f"{tag} >> nth={index}"
            tag_indices[tag] = index + 1
            continue

        if strategy == "text" and ":has-text" in (element.get("selector") or ""):
            safe_text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()
            safe_text = safe_text.replace('"', "'") if safe_text else ""
            if safe_text:
                element["selector"] = f"text={safe_text}"



async def analyze_page_elements(page, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """현재 페이지에서 상호작용 가능한 요소를 추출합니다 (iframe 포함)."""
    try:
        try:
            await page.wait_for_load_state("networkidle", timeout=2000)
        except Exception:
            await page.wait_for_timeout(2000)

        # 모든 프레임(메인 + iframe)에서 요소 수집
        all_elements = []
        frames = page.frames

        print(f"Analyzing {len(frames)} frames (main + iframes)...")

        for frame_index, frame in enumerate(frames):
            try:
                # 각 프레임에서 요소 수집
                frame_elements = await frame.evaluate("""
            () => {
                const elements = [];
                let gaiaRefSeq = 0;

                const scanRoots = (() => {
                    const roots = [document];
                    const seen = new Set([document]);
                    const queue = [document];
                    while (queue.length > 0) {
                        const root = queue.shift();
                        let nodes = [];
                        try {
                            nodes = Array.from(root.querySelectorAll('*'));
                        } catch (_) {
                            nodes = [];
                        }
                        for (const node of nodes) {
                            if (!node || !node.shadowRoot) continue;
                            if (seen.has(node.shadowRoot)) continue;
                            seen.add(node.shadowRoot);
                            roots.push(node.shadowRoot);
                            queue.push(node.shadowRoot);
                        }
                    }
                    return roots;
                })();

                function queryAll(selector) {
                    const out = [];
                    const seen = new Set();
                    for (const root of scanRoots) {
                        let found = [];
                        try {
                            found = Array.from(root.querySelectorAll(selector));
                        } catch (_) {
                            continue;
                        }
                        for (const el of found) {
                            if (!el || seen.has(el)) continue;
                            seen.add(el);
                            out.push(el);
                        }
                    }
                    return out;
                }

                function getActionability(el) {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    const displayVisible = style.display !== 'none' && style.visibility !== 'hidden';
                    const opacity = Number(style.opacity || '1');
                    const pointerEvents = (style.pointerEvents || '').toLowerCase();
                    const hasRect = rect.width > 1 && rect.height > 1;
                    const onViewport =
                        rect.bottom >= -2 &&
                        rect.right >= -2 &&
                        rect.top <= (window.innerHeight + 2) &&
                        rect.left <= (window.innerWidth + 2);
                    const disabled =
                        el.disabled === true ||
                        String(el.getAttribute('disabled') || '').toLowerCase() === 'true' ||
                        String(el.getAttribute('aria-disabled') || '').toLowerCase() === 'true';
                    // OpenClaw-aligned split:
                    // - collect visibility: allow offscreen candidates (no viewport gating)
                    // - execution-time actionability: handled at action phase (scroll/reveal/probe)
                    const collectVisible = displayVisible && opacity > 0.02 && pointerEvents !== 'none' && hasRect;
                    const visible = collectVisible;
                    return {
                        visible,
                        actionable: collectVisible && !disabled,
                        disabled,
                        opacity,
                        onViewport,
                        pointerEvents: style.pointerEvents || '',
                    };
                }

                function isVisible(el) {
                    return getActionability(el).visible;
                }

                function assignDomRef(el) {
                    const existing = (el.getAttribute('data-gaia-dom-ref') || '').trim();
                    if (existing) {
                        return existing;
                    }
                    const tag = (el.tagName || 'el').toLowerCase();
                    const ref = `gaia-${tag}-${Date.now().toString(36)}-${gaiaRefSeq++}`;
                    try {
                        el.setAttribute('data-gaia-dom-ref', ref);
                    } catch (_) {}
                    return ref;
                }

                function getUniqueSelector(el) {
                    if (el.id) {
                        if (window.CSS && typeof CSS.escape === 'function') {
                            return `#${CSS.escape(el.id)}`;
                        }
                        return `${el.tagName.toLowerCase()}[id="${el.id}"]`;
                    }

                    if (el.name) return `${el.tagName.toLowerCase()}[name="${el.name}"]`;

                    if (el.dataset.testid) return `[data-testid="${el.dataset.testid}"]`;

                    if (el.getAttribute('aria-label')) {
                        return `${el.tagName.toLowerCase()}[aria-label="${el.getAttribute('aria-label')}"]`;
                    }

                    // 입력 요소는 텍스트나 클래스로 넘어가기 전에 placeholder를 확인
                    if (el.tagName === 'INPUT' && el.placeholder) {
                        return `${el.tagName.toLowerCase()}[placeholder="${el.placeholder}"]`;
                    }

                    const text = el.innerText?.trim();
                    if (text && text.length < 50) {
                        return `${el.tagName.toLowerCase()}:has-text("${text}")`;
                    }

                    if (el.className && typeof el.className === 'string') {
                        const classes = el.className.split(' ').filter(c =>
                            c &&
                            !c.match(/^(active|hover|focus|selected)/) &&
                            !c.match(/^(sc-|css-|makeStyles-|emotion-)/)
                        );
                        if (classes.length > 0) {
                            return `${el.tagName.toLowerCase()}.${classes.slice(0, 2).join('.')}`;
                        }
                    }

                    const parent = el.parentElement;
                    if (parent) {
                        const siblings = Array.from(parent.children);
                        const index = siblings.indexOf(el) + 1;
                        return `${el.tagName.toLowerCase()}:nth-child(${index})`;
                    }

                    return el.tagName.toLowerCase();
                }

                function getBoundingBox(el) {
                    const rect = el.getBoundingClientRect();
                    return {
                        x: rect.x,
                        y: rect.y,
                        width: rect.width,
                        height: rect.height,
                        center_x: rect.x + rect.width / 2,
                        center_y: rect.y + rect.height / 2
                    };
                }

                function normalizeText(value) {
                    return String(value || '').replace(/\s+/g, ' ').trim();
                }

                function compactLines(value, limit = 2) {
                    const lines = String(value || '')
                        .split(/\\n+/)
                        .map((line) => normalizeText(line))
                        .filter(Boolean);
                    return lines.slice(0, limit).join(' | ');
                }

                function accessibleName(el) {
                    if (!(el instanceof HTMLElement)) return '';
                    const aria = normalizeText(el.getAttribute('aria-label'));
                    if (aria) return aria;
                    const labelledBy = normalizeText(el.getAttribute('aria-labelledby'));
                    if (labelledBy) {
                        const parts = labelledBy
                            .split(/\s+/)
                            .map((id) => document.getElementById(id))
                            .filter(Boolean)
                            .map((node) => normalizeText(node.textContent || ''))
                            .filter(Boolean);
                        if (parts.length > 0) return parts.join(' ');
                    }
                    const title = normalizeText(el.getAttribute('title'));
                    if (title) return title;
                    const placeholder = normalizeText(el.getAttribute('placeholder'));
                    if (placeholder) return placeholder;
                    return normalizeText(el.innerText || el.textContent || '');
                }

                const INTERACTIVE_SELECTOR = 'button,[role="button"],a[href],[role="link"],input[type="button"],input[type="submit"],select,textarea,input:not([type="hidden"])';
                const containerMetricsCache = new WeakMap();

                function semanticContainerCandidates(targetEl, startNode = null) {
                    const candidates = [];
                    let current = startNode instanceof Element
                        ? startNode
                        : (targetEl instanceof Element ? targetEl.parentElement : null);
                    let distance = 0;
                    while (current && current instanceof HTMLElement && distance < 10) {
                        const tag = (current.tagName || '').toLowerCase();
                        if (tag === 'body' || tag === 'html') break;
                        candidates.push({ el: current, distance });
                        current = current.parentElement;
                        distance += 1;
                    }
                    return candidates;
                }

                function semanticContainerName(candidate) {
                    if (!(candidate instanceof HTMLElement)) return '';
                    return containerName(candidate);
                }

                function semanticStructureScore(candidateEl, metrics) {
                    if (!(candidateEl instanceof HTMLElement) || !metrics) return -Infinity;
                    let score = 0;
                    if (metrics.semanticRoleScore > 0) score += 4;
                    if (metrics.semanticTagScore > 0) score += 3;
                    if (metrics.headingPresent) score += 3;
                    const explicitName = semanticContainerName(candidateEl);
                    if (explicitName) score += 2;
                    if (metrics.repeatedSiblingPattern) score += 2;
                    if (metrics.interactiveDescendants >= 2) score += 1.5;
                    else if (metrics.interactiveDescendants === 1) score += 0.75;
                    if (metrics.meaningfulTextBlock) score += 1;
                    if (metrics.areaRatio > 0.90) score -= 4;
                    else if (metrics.areaRatio > 0.75) score -= 2;
                    if (metrics.genericWrapperOnly) score -= 3;
                    return score;
                }

                function namedSemanticContainer(targetEl, startNode = null) {
                    const candidates = semanticContainerCandidates(targetEl, startNode);
                    let best = null;
                    let bestScore = -Infinity;
                    for (const candidate of candidates) {
                        const el = candidate.el;
                        const metrics = getContainerMetrics(el);
                        if (!metrics) continue;
                        const structuralScore = semanticStructureScore(el, metrics);
                        if (structuralScore < 6.0) continue;
                        const semanticScore = scoreSemanticContainer(el, targetEl, candidate.distance);
                        if (semanticScore < 4.0) continue;
                        const combinedScore = structuralScore + semanticScore;
                        if (combinedScore <= bestScore) continue;
                        best = {
                            el,
                            score: combinedScore,
                            distance: candidate.distance,
                            source: 'semantic-first',
                        };
                        bestScore = combinedScore;
                    }
                    return best;
                }

                function repeatedSiblingPattern(el) {
                    if (!(el instanceof HTMLElement) || !(el.parentElement instanceof HTMLElement)) return false;
                    const parent = el.parentElement;
                    const tag = (el.tagName || '').toLowerCase();
                    const role = normalizeText(el.getAttribute('role')).toLowerCase();
                    let similar = 0;
                    for (const child of Array.from(parent.children)) {
                        if (!(child instanceof HTMLElement)) continue;
                        const childTag = (child.tagName || '').toLowerCase();
                        const childRole = normalizeText(child.getAttribute('role')).toLowerCase();
                        if (tag && childTag === tag) {
                            similar += 1;
                            continue;
                        }
                        if (role && childRole && childRole === role) {
                            similar += 1;
                        }
                    }
                    return similar >= 3;
                }

                function getContainerMetrics(el) {
                    if (!(el instanceof HTMLElement)) return null;
                    const cached = containerMetricsCache.get(el);
                    if (cached) return cached;

                    const tag = (el.tagName || '').toLowerCase();
                    const role = normalizeText(el.getAttribute('role')).toLowerCase();
                    const classBlob = normalizeText(el.className).toLowerCase();
                    const heading = el.querySelector('h1,h2,h3,h4,h5,h6,[role="heading"]');
                    const headingName = heading ? accessibleName(heading) : '';
                    const textBlob = normalizeText(el.innerText || el.textContent || '');
                    const rect = el.getBoundingClientRect();
                    const viewportArea = Math.max(1, window.innerWidth * window.innerHeight);
                    const rectArea = Math.max(0, rect.width) * Math.max(0, rect.height);
                    const areaRatio = rectArea / viewportArea;
                    const interactiveDescendants = el.querySelectorAll(INTERACTIVE_SELECTOR).length;

                    const metrics = {
                        tag,
                        role,
                        headingName,
                        headingPresent: Boolean(headingName),
                        semanticRoleScore: ['listitem', 'row', 'article', 'region', 'group'].includes(role) ? 4 : 0,
                        semanticTagScore: ['li', 'tr', 'article', 'section'].includes(tag) ? 3 : 0,
                        weakClassHint: /(card|item|row|list|result|product|course|subject)/.test(classBlob),
                        repeatedSiblingPattern: repeatedSiblingPattern(el),
                        interactiveDescendants,
                        meaningfulTextBlock: textBlob.length >= 20 && textBlob.length <= 500,
                        genericWrapperOnly:
                            !['listitem', 'row', 'article', 'region', 'group'].includes(role)
                            && !['li', 'tr', 'article', 'section'].includes(tag)
                            && !headingName
                            && interactiveDescendants < 2
                            && textBlob.length < 24,
                        areaRatio,
                        textBlob,
                    };
                    containerMetricsCache.set(el, metrics);
                    return metrics;
                }

                function scoreSemanticContainer(candidate, targetEl, distance = 0) {
                    const metrics = getContainerMetrics(candidate);
                    if (!metrics) return -Infinity;
                    let score = 0;
                    score += metrics.semanticRoleScore;
                    score += metrics.semanticTagScore;
                    if (metrics.headingPresent) score += 3;
                    if (metrics.repeatedSiblingPattern) score += 2;
                    if (metrics.interactiveDescendants >= 2) score += 2;
                    else if (metrics.interactiveDescendants === 1) score += 1;
                    if (metrics.meaningfulTextBlock) score += 1;
                    if (metrics.weakClassHint) score += 0.5;
                    if (metrics.areaRatio > 0.90) score -= 3;
                    else if (metrics.areaRatio > 0.75) score -= 2;
                    else if (metrics.areaRatio > 0.55) score -= 1;
                    if (metrics.genericWrapperOnly) score -= 2;
                    score += Math.max(0, 2 - (distance * 0.35));
                    return score;
                }

                function bestSemanticContainer(targetEl, startNode = null) {
                    const semanticMatch = namedSemanticContainer(targetEl, startNode);
                    if (semanticMatch && semanticMatch.el instanceof HTMLElement) {
                        return semanticMatch;
                    }
                    const candidates = semanticContainerCandidates(targetEl, startNode);
                    let best = null;
                    let bestScore = -Infinity;
                    for (const candidate of candidates) {
                        const score = scoreSemanticContainer(candidate.el, targetEl, candidate.distance);
                        if (score > bestScore) {
                            best = candidate.el;
                            bestScore = score;
                        }
                    }
                    if (!(best instanceof HTMLElement)) return null;
                    if (bestScore < 3.0) return null;
                    return { el: best, score: bestScore, source: 'scored-fallback' };
                }

                function containerName(container) {
                    if (!(container instanceof HTMLElement)) return '';
                    const metrics = getContainerMetrics(container);
                    const headingName = metrics?.headingName || '';
                    if (headingName) return headingName;
                    const labelled = accessibleName(container);
                    if (labelled) return labelled;
                    const leadLink = container.querySelector('a[href]');
                    const leadLinkName = leadLink ? accessibleName(leadLink) : '';
                    if (leadLinkName) return leadLinkName;
                    const emphasis = container.querySelector('strong,b,[data-testid*="title"],[data-testid*="name"]');
                    const emphasisName = emphasis ? accessibleName(emphasis) : '';
                    if (emphasisName) return emphasisName;
                    return compactLines(container.innerText || container.textContent || '', 2);
                }

                function siblingActionLabels(container) {
                    if (!(container instanceof HTMLElement)) return [];
                    const labels = [];
                    const nodes = Array.from(
                        container.querySelectorAll('button,[role="button"],a[href],[role="link"],input[type="button"],input[type="submit"]')
                    );
                    for (const node of nodes) {
                        const label = accessibleName(node);
                        if (label && !labels.includes(label)) labels.push(label);
                    }
                    return labels.slice(0, 8);
                }

                function containerContextText(container) {
                    if (!(container instanceof HTMLElement)) return '';
                    const fragments = [];
                    const seen = new Set();
                    const push = (value) => {
                        const normalized = normalizeText(value);
                        if (!normalized || seen.has(normalized)) return;
                        seen.add(normalized);
                        fragments.push(normalized);
                    };

                    const metrics = getContainerMetrics(container);
                    if (metrics?.headingName) push(metrics.headingName);

                    const leadLink = container.querySelector('a[href]');
                    if (leadLink) push(accessibleName(leadLink));

                    const metaNodes = Array.from(
                        container.querySelectorAll(
                            'small,time,strong,b,[data-testid*="meta"],[data-testid*="badge"],[data-testid*="title"],[data-testid*="name"],[class*="badge"],[class*="meta"],[class*="price"],[class*="credit"],[class*="time"]'
                        )
                    );
                    for (const node of metaNodes.slice(0, 6)) {
                        push(accessibleName(node));
                    }

                    if (fragments.length < 3) {
                        const fallbackLines = String(container.innerText || container.textContent || '')
                            .split(/\\n+/)
                            .map((line) => normalizeText(line))
                            .filter(Boolean);
                        for (const line of fallbackLines) {
                            push(line);
                            if (fragments.length >= 4) break;
                        }
                    }

                    return fragments.slice(0, 4).join(' | ');
                }

                function withContext(el, attrs = {}) {
                    if (!(el instanceof HTMLElement)) return attrs;
                    const containerMatch = bestSemanticContainer(el);
                    const container = containerMatch && containerMatch.el instanceof HTMLElement ? containerMatch.el : null;
                    if (!(container instanceof HTMLElement)) return attrs;
                    const containerDomRef = assignDomRef(container);
                    const parentMatch = bestSemanticContainer(container, container.parentElement);
                    const parentContainer = parentMatch && parentMatch.el instanceof HTMLElement ? parentMatch.el : null;
                    const parentDomRef = parentContainer instanceof HTMLElement ? assignDomRef(parentContainer) : '';
                    attrs.container_name = containerName(container);
                    attrs.container_role = normalizeText(container.getAttribute('role')) || normalizeText(container.tagName).toLowerCase();
                    attrs.container_ref_id = containerDomRef;
                    attrs.container_dom_ref = containerDomRef;
                    attrs.container_parent_ref_id = parentDomRef || '';
                    attrs.container_parent_dom_ref = parentDomRef || '';
                    attrs.context_text = containerContextText(container) || compactLines(container.innerText || container.textContent || '', 3);
                    attrs.group_action_labels = siblingActionLabels(container);
                    attrs.container_source = containerMatch && containerMatch.source ? String(containerMatch.source) : '';
                    if (containerMatch && Number.isFinite(containerMatch.score)) {
                        attrs.context_score_hint = Number(containerMatch.score.toFixed(2));
                    }
                    return attrs;
                }

                queryAll('input, textarea, select').forEach(el => {
                    const actionability = getActionability(el);
                    if (!actionability.visible) return;

                    const entry = {
                        tag: el.tagName.toLowerCase(),
                        dom_ref: assignDomRef(el),
                        selector: getUniqueSelector(el),
                        text: '',
                        attributes: {
                            type: el.type || 'text',
                            id: el.id || null,
                            name: el.name || null,
                            placeholder: el.placeholder || '',
                            'aria-label': el.getAttribute('aria-label') || '',
                            title: el.getAttribute('title') || '',
                            'gaia-visible-strict': actionability.visible ? 'true' : 'false',
                            'gaia-actionable': actionability.actionable ? 'true' : 'false',
                            'gaia-disabled': actionability.disabled ? 'true' : 'false',
                            'gaia-on-viewport': actionability.onViewport ? 'true' : 'false',
                            'gaia-pointer-events': actionability.pointerEvents || '',
                            'gaia-opacity': String(actionability.opacity),
                        },
                        bounding_box: getBoundingBox(el),
                        element_type: 'input',
                        actionable: actionability.actionable,
                        visible_strict: actionability.visible,
                    };

                    // select 요소의 option 목록 수집 (최대 20개)
                    if (el.tagName.toLowerCase() === 'select') {
                        const opts = [];
                        const optEls = el.querySelectorAll('option');
                        const limit = Math.min(optEls.length, 20);
                        for (let i = 0; i < limit; i++) {
                            const o = optEls[i];
                            opts.push({ value: o.value, text: (o.textContent || '').trim() });
                        }
                        if (optEls.length > 20) {
                            opts.push({ value: '__truncated__', text: '...' + (optEls.length - 20) + ' more' });
                        }
                        entry.attributes['options'] = opts;
                        // 현재 선택된 값도 기록
                        entry.attributes['selected_value'] = el.value || '';
                    }

                    withContext(el, entry.attributes);

                    elements.push(entry);
                });

                // 버튼과 상호작용 가능한 역할 요소를 수집
                // 상호작용 UI에서 자주 사용하는 ARIA 역할
                queryAll(`
                    button,
                    a:not([href]),
                    [role="button"],
                    [role="tab"],
                    [role="menuitem"],
                    [role="menuitemcheckbox"],
                    [role="menuitemradio"],
                    [role="option"],
                    [role="radio"],
                    [role="switch"],
                    [role="treeitem"],
                    [role="link"],
                    [type="submit"],
                    input[type="button"]
                `.replace(/\s+/g, '')).forEach(el => {
                    const actionability = getActionability(el);
                    if (!actionability.visible) return;

                    let text = el.innerText?.trim() || el.value || '';
                    if (!text) {
                        text = el.getAttribute('aria-label') || el.getAttribute('title') || '';
                    }
                    if (!text) {
                        const svg = el.querySelector('svg');
                        if (svg) {
                            text = svg.getAttribute('aria-label') || svg.getAttribute('title') || '[icon]';
                        }
                    }

                    // For switches/toggles, try to find nearby label text
                    if (el.getAttribute('role') === 'switch' && (!text || text === 'on' || text === 'off')) {
                        // Look for label in parent container
                        const parent = el.parentElement;
                        if (parent) {
                            const parentContainer = parent.parentElement;
                            if (parentContainer) {
                                const label = parentContainer.querySelector('label');
                                if (label && label.innerText) {
                                    text = label.innerText.trim();
                                }
                            }
                        }
                    }

                    elements.push({
                        tag: el.tagName.toLowerCase(),
                        dom_ref: assignDomRef(el),
                        selector: getUniqueSelector(el),
                        text: text,
                        attributes: {
                            type: el.type || 'button',
                            'aria-label': el.getAttribute('aria-label') || '',
                            title: el.getAttribute('title') || '',
                            role: el.getAttribute('role') || '',
                            'gaia-visible-strict': actionability.visible ? 'true' : 'false',
                            'gaia-actionable': actionability.actionable ? 'true' : 'false',
                            'gaia-disabled': actionability.disabled ? 'true' : 'false',
                            'gaia-on-viewport': actionability.onViewport ? 'true' : 'false',
                            'gaia-pointer-events': actionability.pointerEvents || '',
                            'gaia-opacity': String(actionability.opacity),
                        },
                        bounding_box: getBoundingBox(el),
                        element_type: 'button',
                        actionable: actionability.actionable,
                        visible_strict: actionability.visible,
                    });
                    withContext(el, elements[elements.length - 1].attributes);
                });

                // 페이지네이션/네비게이션 시그널 수집 (아이콘형 next/prev 포함)
                queryAll('button, a, [role="button"], [role="link"]').forEach(el => {
                    const actionability = getActionability(el);
                    if (!actionability.visible) return;

                    const rawText = (el.innerText || el.textContent || '').trim();
                    const ariaLabel = (el.getAttribute('aria-label') || '').trim();
                    const title = (el.getAttribute('title') || '').trim();
                    const cls = (el.className && typeof el.className === 'string') ? el.className : '';
                    const dataPage = (el.getAttribute('data-page') || '').trim();
                    const ariaCurrent = (el.getAttribute('aria-current') || '').trim();
                    const role = (el.getAttribute('role') || '').trim();
                    const blob = `${rawText} ${ariaLabel} ${title} ${cls} ${dataPage}`.toLowerCase();
                    const hasPaginationSignal =
                        /(pagination|pager|page-|page_|\\bpage\\b|next|prev|previous|다음|이전|chevron|arrow)/.test(blob)
                        || !!ariaCurrent
                        || /^[<>‹›«»→←]+$/.test(rawText);
                    if (!hasPaginationSignal) return;

                    const text = rawText || ariaLabel || title || dataPage || '[page-nav]';
                    elements.push({
                        tag: el.tagName.toLowerCase(),
                        dom_ref: assignDomRef(el),
                        selector: getUniqueSelector(el),
                        text: text,
                        attributes: {
                            role: role,
                            class: cls || '',
                            'aria-label': ariaLabel,
                            title: title,
                            'aria-current': ariaCurrent,
                            'data-page': dataPage,
                            'gaia-visible-strict': actionability.visible ? 'true' : 'false',
                            'gaia-actionable': actionability.actionable ? 'true' : 'false',
                            'gaia-disabled': actionability.disabled ? 'true' : 'false',
                            'gaia-on-viewport': actionability.onViewport ? 'true' : 'false',
                            'gaia-pointer-events': actionability.pointerEvents || '',
                            'gaia-opacity': String(actionability.opacity),
                        },
                        bounding_box: getBoundingBox(el),
                        element_type: 'pagination',
                        actionable: actionability.actionable,
                        visible_strict: actionability.visible,
                    });
                    withContext(el, elements[elements.length - 1].attributes);
                });

                queryAll('[onclick], [class*="btn"], [class*="button"], [class*="cursor-pointer"]').forEach(el => {
                    const actionability = getActionability(el);
                    if (!actionability.visible) return;
                    if (el.tagName === 'BUTTON') return;
                    if (el.tagName === 'A' && el.hasAttribute('href')) return;

                    const style = window.getComputedStyle(el);
                    if (style.cursor === 'pointer' || el.onclick) {
                        const text = el.innerText?.trim() || '';
                        if (text && text.length < 100) {
                            elements.push({
                                tag: el.tagName.toLowerCase(),
                                dom_ref: assignDomRef(el),
                                selector: getUniqueSelector(el),
                                text: text,
                                attributes: {
                            class: el.className,
                            'aria-label': el.getAttribute('aria-label') || '',
                            title: el.getAttribute('title') || '',
                            'gaia-visible-strict': actionability.visible ? 'true' : 'false',
                            'gaia-actionable': actionability.actionable ? 'true' : 'false',
                            'gaia-disabled': actionability.disabled ? 'true' : 'false',
                            'gaia-on-viewport': actionability.onViewport ? 'true' : 'false',
                            'gaia-pointer-events': actionability.pointerEvents || '',
                            'gaia-opacity': String(actionability.opacity),
                        },
                        bounding_box: getBoundingBox(el),
                        element_type: 'clickable',
                        actionable: actionability.actionable,
                        visible_strict: actionability.visible,
                    });
                            withContext(el, elements[elements.length - 1].attributes);
                        }
                    }
                });

                queryAll('a[href]').forEach(el => {
                    const actionability = getActionability(el);
                    if (!actionability.visible) return;

                    const href = el.href;
                    let text = el.innerText?.trim() || '';

                    if (!text) {
                        const img = el.querySelector('img');
                        text = (img && img.getAttribute('alt')) ||
                            el.getAttribute('aria-label') ||
                            el.getAttribute('title') ||
                            '[link]';
                    }

                    elements.push({
                        tag: 'a',
                        dom_ref: assignDomRef(el),
                        selector: getUniqueSelector(el),
                        text: text,
                        attributes: {
                            href: href,
                            target: el.target || '',
                            'aria-label': el.getAttribute('aria-label') || '',
                            title: el.getAttribute('title') || '',
                            'gaia-visible-strict': actionability.visible ? 'true' : 'false',
                            'gaia-actionable': actionability.actionable ? 'true' : 'false',
                            'gaia-disabled': actionability.disabled ? 'true' : 'false',
                            'gaia-on-viewport': actionability.onViewport ? 'true' : 'false',
                            'gaia-pointer-events': actionability.pointerEvents || '',
                            'gaia-opacity': String(actionability.opacity),
                        },
                        bounding_box: getBoundingBox(el),
                        element_type: 'link',
                        actionable: actionability.actionable,
                        visible_strict: actionability.visible,
                    });
                    withContext(el, elements[elements.length - 1].attributes);
                });

                // 시맨틱/구조 신호 수집 (OpenClaw 스타일 보강)
                queryAll(`
                    [aria-controls],
                    [aria-expanded],
                    [aria-haspopup],
                    [tabindex]:not([tabindex="-1"]),
                    [data-testid],
                    [data-test],
                    [data-qa],
                    [contenteditable="true"],
                    summary,
                    details > summary,
                    tr,
                    td,
                    li,
                    article,
                    [role="row"],
                    [role="cell"],
                    [role="gridcell"],
                    [role="listitem"],
                    [class*="row"],
                    [class*="item"],
                    [class*="card"],
                    [class*="list"]
                `.replace(/\s+/g, '')).forEach(el => {
                    const actionability = getActionability(el);
                    if (!actionability.visible) return;
                    if (!el || !el.tagName) return;

                    const tag = el.tagName.toLowerCase();
                    if (['html', 'body', 'head', 'meta', 'style', 'script', 'link'].includes(tag)) return;

                    const role = (el.getAttribute('role') || '').trim().toLowerCase();
                    const ariaLabel = (el.getAttribute('aria-label') || '').trim();
                    const title = (el.getAttribute('title') || '').trim();
                    const text = (el.innerText || '').trim();
                    const testid =
                        (el.getAttribute('data-testid') || '').trim() ||
                        (el.getAttribute('data-test') || '').trim() ||
                        (el.getAttribute('data-qa') || '').trim();
                    const style = window.getComputedStyle(el);
                    const pointerLike = style.cursor === 'pointer';
                    const roleValue = (role || '').toLowerCase();
                    const classBlob = (el.className && typeof el.className === 'string') ? el.className.toLowerCase() : '';
                    const rowLike =
                        roleValue === 'row' ||
                        roleValue === 'cell' ||
                        roleValue === 'gridcell' ||
                        roleValue === 'listitem' ||
                        ['tr', 'td', 'li', 'article'].includes(tag) ||
                        /(?:^|\\s)(row|item|card|list)(?:-|_|\\s|$)/.test(classBlob);
                    const hasClickableChild = !!el.querySelector('a,button,[role="button"],[role="link"],[onclick]');
                    const textualCandidate = !!text && text.length >= 2 && text.length <= 320;
                    const box = getBoundingBox(el);

                    // 너무 의미 없는 wrapper 노드는 제외
                    const hasSignal =
                        !!role ||
                        !!ariaLabel ||
                        !!title ||
                        !!testid ||
                        pointerLike ||
                        (text && text.length <= 180) ||
                        (rowLike && (pointerLike || hasClickableChild || textualCandidate));
                    if (!hasSignal) return;
                    if (box.width <= 0 || box.height <= 0) return;

                    elements.push({
                        tag: tag,
                        dom_ref: assignDomRef(el),
                        selector: getUniqueSelector(el),
                        text: text ? text.slice(0, 260) : '',
                        attributes: {
                            role: role,
                            'aria-label': ariaLabel,
                            'aria-modal': el.getAttribute('aria-modal') || '',
                            title: title,
                            class: el.className || '',
                            placeholder: el.getAttribute('placeholder') || '',
                            'aria-controls': el.getAttribute('aria-controls') || '',
                            'aria-expanded': el.getAttribute('aria-expanded') || '',
                            'aria-haspopup': el.getAttribute('aria-haspopup') || '',
                            tabindex: el.getAttribute('tabindex') || '',
                            'data-testid': testid,
                            'gaia-visible-strict': actionability.visible ? 'true' : 'false',
                            'gaia-actionable': actionability.actionable ? 'true' : 'false',
                            'gaia-disabled': actionability.disabled ? 'true' : 'false',
                            'gaia-on-viewport': actionability.onViewport ? 'true' : 'false',
                            'gaia-pointer-events': actionability.pointerEvents || '',
                            'gaia-opacity': String(actionability.opacity),
                        },
                        bounding_box: box,
                        element_type: 'semantic',
                        actionable: actionability.actionable,
                        visible_strict: actionability.visible,
                    });
                });

                return elements;
            }
        """)

                # None 체크
                if frame_elements is None:
                    frame_elements = []

                selector_strategy = os.environ.get("MCP_SELECTOR_STRATEGY", "text")
                ctx["apply_selector_strategy"](frame_elements, selector_strategy)

                # 프레임 정보 추가
                frame_name = frame.name or f"frame_{frame_index}"
                is_main_frame = frame == page.main_frame

                print(
                    f"  Frame {frame_index} ({frame_name}): {len(frame_elements)} elements"
                )

                # 각 요소에 프레임 정보 추가
                for elem in frame_elements:
                    elem["frame_index"] = frame_index
                    elem["frame_name"] = frame_name
                    elem["is_main_frame"] = is_main_frame

                    # iframe 내부 요소는 selector에 frame 정보 추가
                    if not is_main_frame:
                        # iframe selector 생성 (name 또는 index 사용)
                        if frame.name:
                            frame_selector = f'iframe[name="{frame.name}"]'
                        else:
                            frame_selector = f"iframe:nth-of-type({frame_index})"
                        elem["frame_selector"] = frame_selector
                        # 전체 selector는 "frame_selector >>> element_selector" 형식
                        elem["full_selector"] = (
                            f"{frame_selector} >>> {elem['selector']}"
                        )
                    else:
                        elem["full_selector"] = elem["selector"]

                all_elements.extend(frame_elements)

            except Exception as frame_error:
                import traceback

                print(
                    f"  Error analyzing frame {frame_index} ({frame.name or 'unnamed'}): {frame_error}"
                )
                print(f"  Traceback: {traceback.format_exc()}")
                continue

        # 중복 제거 후 시그널 점수 기반으로 상위 요소 유지 (밀도는 높이고 노이즈는 억제)
        all_elements = ctx["dedupe_elements_by_dom_ref"](all_elements)

        try:
            max_elements = int(os.getenv("GAIA_DOM_MAX_ELEMENTS", "2200"))
        except Exception:
            max_elements = 2200
        max_elements = max(200, min(max_elements, 8000))
        if len(all_elements) > max_elements:
            all_elements = sorted(
                all_elements,
                key=ctx["element_signal_score"],
                reverse=True,
            )[:max_elements]

        print(f"Total found {len(all_elements)} interactive/semantic elements across all frames")
        # 디버깅용으로 처음 10개 요소를 출력합니다
        if len(all_elements) <= 10:
            element_strs = [
                f"{e.get('tag', '')}:{e.get('text', '')[:20]}" for e in all_elements
            ]
            print(f"  Elements: {element_strs}")
        return {"elements": all_elements}

    except Exception as e:
        current_url = getattr(page, "url", "unknown")
        print(f"Error analyzing page {current_url}: {e}")
        return {"error": str(e)}


async def snapshot_page(
    url: str = None,
    session_id: str = "default",
    scope_container_ref_id: str = "",
    ctx: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    ctx = ctx or {}
    """페이지 스냅샷 생성 (snapshot_id/dom_hash/ref 포함)."""
    if not ctx["playwright_instance"]:
        raise ctx["HTTPException"](status_code=503, detail="Playwright is not initialized.")


    # 세션을 가져오거나 생성합니다
    session = ctx["ensure_session"](
    active_sessions=ctx["active_sessions"],
    session_id=session_id,
    playwright_getter=ctx["get_playwright_instance"],
    screencast_subscribers=ctx["screencast_subscribers"],
    frame_setter=ctx["set_current_screencast_frame"],
    logger=ctx["logger"],
    )

    page = await session.get_or_create_page()

    def _is_retryable_page_detach_error(exc: BaseException) -> bool:
        message = str(exc or "").strip().lower()
        if not message:
            return False
        return (
            "frame has been detached" in message
            or "target page, context or browser has been closed" in message
        )

    async def _goto_with_retry(target_page: Any, target_url: str, *, timeout: int) -> None:
        try:
            await target_page.goto(target_url, timeout=timeout)
        except Exception as exc:
            if not _is_retryable_page_detach_error(exc):
                raise
            await target_page.wait_for_timeout(150)
            await target_page.goto(target_url, timeout=timeout)

    async def _screenshot_with_retry(target_page: Any, **kwargs: Any) -> bytes:
        try:
            return await target_page.screenshot(**kwargs)
        except Exception as exc:
            if not _is_retryable_page_detach_error(exc):
                raise
            await target_page.wait_for_timeout(150)
            return await target_page.screenshot(**kwargs)

    async def _title_with_retry(target_page: Any) -> str:
        try:
            return await target_page.title()
        except Exception as exc:
            if not _is_retryable_page_detach_error(exc):
                raise
            await target_page.wait_for_timeout(150)
            return await target_page.title()

    # URL이 주어지고 현재 브라우저 URL과 다를 때에만 이동합니다
    if url:
        current_browser_url = page.url
        current_normalized = ctx["normalize_url"](current_browser_url)
        requested_normalized = ctx["normalize_url"](url)


        print(
            f"[analyze_page] Current browser URL: {current_browser_url} (normalized: {current_normalized})"
        )
        print(
            f"[analyze_page] Requested URL: {url} (normalized: {requested_normalized})"
        )

        if current_normalized != requested_normalized:
            print(f"[analyze_page] URLs differ, navigating to: {url}")
            await _goto_with_retry(page, url, timeout=30000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            # 이동 후 React/Figma SPA가 하이드레이션되도록 대기합니다
            await page.wait_for_timeout(3000)

        # session.current_url을 실제 브라우저 URL과 항상 동기화합니다
        session.current_url = page.url
        print(f"[analyze_page] Synced session.current_url to: {session.current_url}")

    # 요소를 수집하고 현재 URL을 응답에 추가합니다
    result = await ctx["analyze_page_elements"](page)

    should_retry_snapshot = False
    if isinstance(result, dict):
        err_text = str(result.get("error") or "").strip().lower()
        if (
            "frame has been detached" in err_text
            or "target page, context or browser has been closed" in err_text
        ):
            should_retry_snapshot = True
    try:
        if not should_retry_snapshot and bool(page.is_closed()):
            should_retry_snapshot = True
    except Exception:
        if not should_retry_snapshot:
            should_retry_snapshot = True
    if should_retry_snapshot:
        page = await session.get_or_create_page()
        result = await ctx["analyze_page_elements"](page)

    elements = result.get("elements", []) if isinstance(result, dict) else []
    if isinstance(elements, list):
        elements = ctx["dedupe_elements_by_dom_ref"](elements)

    scoped_container_ref_id = str(scope_container_ref_id or "").strip()
    if scoped_container_ref_id and isinstance(elements, list):
        scoped_elements: List[Dict[str, Any]] = []
        for elem in elements:
            if not isinstance(elem, dict):
                continue
            attrs = elem.get("attributes") if isinstance(elem.get("attributes"), dict) else {}
            elem_dom_ref = str(elem.get("dom_ref") or "").strip()
            container_ref = str(attrs.get("container_ref_id") or attrs.get("container_dom_ref") or "").strip()
            parent_container_ref = str(
                attrs.get("container_parent_ref_id") or attrs.get("container_parent_dom_ref") or ""
            ).strip()
            if (
                elem_dom_ref == scoped_container_ref_id
                or container_ref == scoped_container_ref_id
                or parent_container_ref == scoped_container_ref_id
            ):
                scoped_elements.append(elem)
        if scoped_elements:
            elements = scoped_elements
    tab_index = ctx["get_tab_index"](page)

    session.snapshot_epoch += 1
    epoch = session.snapshot_epoch
    dom_hash = ctx["build_snapshot_dom_hash"](page.url, elements)
    snapshot_id = f"{session.session_id}:{epoch}:{dom_hash[:12]}"
    captured_at = int(time.time() * 1000)

    for idx, elem in enumerate(elements):
        frame_index = int(elem.get("frame_index", 0) or 0)
        ref_id = f"t{tab_index}-f{frame_index}-e{idx}"
        elem["ref_id"] = ref_id
        elem["scope"] = {
            "tab_index": tab_index,
            "frame_index": frame_index,
            "is_main_frame": bool(elem.get("is_main_frame", True)),
        }

    role_refs = ctx["build_role_refs_from_elements"](elements)

    for elem in elements:
        if not isinstance(elem, dict):
            continue
        ref_id = str(elem.get("ref_id") or "").strip()
        attrs = elem.get("attributes") if isinstance(elem.get("attributes"), dict) else {}
        role_ref = role_refs.get(ref_id) if ref_id else None
        if not isinstance(role_ref, dict):
            continue
        elem["role_ref_role"] = role_ref.get("role")
        elem["role_ref_name"] = role_ref.get("name")
        elem["role_ref_nth"] = role_ref.get("nth")
        attrs["role_ref_role"] = role_ref.get("role")
        attrs["role_ref_name"] = role_ref.get("name")
        attrs["role_ref_nth"] = role_ref.get("nth")

    context_snapshot = ctx["build_context_snapshot_from_elements"](elements)


    elements_by_ref: Dict[str, Dict[str, Any]] = {
        elem["ref_id"]: elem for elem in elements if isinstance(elem, dict) and elem.get("ref_id")
    }
    snapshot_record = {
        "snapshot_id": snapshot_id,
        "session_id": session_id,
        "url": page.url,
        "tab_index": tab_index,
        "dom_hash": dom_hash,
        "epoch": epoch,
        "captured_at": captured_at,
        "scope_container_ref_id": scoped_container_ref_id,
        "elements_by_ref": elements_by_ref,
        "context_snapshot": context_snapshot,
    }
    session.snapshots[snapshot_id] = snapshot_record
    session.current_snapshot_id = snapshot_id
    session.current_dom_hash = dom_hash

    # 오래된 스냅샷 정리
    if len(session.snapshots) > 20:
        oldest = sorted(
            session.snapshots.items(),
            key=lambda item: int((item[1] or {}).get("epoch", 0)),
        )
        for old_snapshot_id, _ in oldest[: len(session.snapshots) - 20]:
            session.snapshots.pop(old_snapshot_id, None)

    result["url"] = page.url
    result["snapshot_id"] = snapshot_id
    result["dom_hash"] = dom_hash
    result["epoch"] = epoch
    result["tab_index"] = tab_index
    result["captured_at"] = captured_at
    result["dom_elements"] = elements
    result["context_snapshot"] = context_snapshot
    result["scope_container_ref_id"] = scoped_container_ref_id
    try:
        result["evidence"] = await ctx["collect_page_evidence"](page)

    except Exception:
        result["evidence"] = {}
    return result