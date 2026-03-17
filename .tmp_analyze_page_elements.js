
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

                function namedSemanticContainer(targetEl, startNode = null) {
                    const candidates = semanticContainerCandidates(targetEl, startNode);
                    for (const candidate of candidates) {
                        const el = candidate.el;
                        const metrics = getContainerMetrics(el);
                        if (!metrics) continue;
                        const isSemantic = metrics.semanticRoleScore > 0 || metrics.semanticTagScore > 0;
                        if (!isSemantic) continue;
                        if (metrics.areaRatio > 0.90) continue;
                        const name = semanticContainerName(el);
                        if (!name) continue;
                        const score = scoreSemanticContainer(el, targetEl, candidate.distance);
                        if (score < 3.0) continue;
                        return {
                            el,
                            score,
                            distance: candidate.distance,
                            source: 'semantic-first',
                        };
                    }
                    return null;
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
                            .split(/\n+/)
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
        