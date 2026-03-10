from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any, List, Optional

from gaia.src.phase4.memory.models import MemorySummaryRecord
from .exploratory_models import ExplorationResult, PageState, TestableAction


def resolve_llm_cache_path() -> str:
    repo_root = Path(__file__).resolve().parents[4]
    return str(repo_root / "artifacts" / "llm_cache.json")


def resolve_semantic_cache_path() -> str:
    repo_root = Path(__file__).resolve().parents[4]
    return str(repo_root / "artifacts" / "cache" / "semantic_llm_cache.json")


def load_llm_cache(agent: Any) -> None:
    try:
        if os.path.exists(agent._llm_cache_path):
            with open(agent._llm_cache_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                agent._llm_cache = {k: str(v) for k, v in data.items()}
    except Exception as exc:
        agent._log(f"⚠️ LLM 캐시 로드 실패: {exc}")


def save_llm_cache(agent: Any) -> None:
    try:
        os.makedirs(os.path.dirname(agent._llm_cache_path), exist_ok=True)
        with open(agent._llm_cache_path, "w", encoding="utf-8") as handle:
            json.dump(agent._llm_cache, handle, ensure_ascii=False, indent=2)
    except Exception as exc:
        agent._log(f"⚠️ LLM 캐시 저장 실패: {exc}")


def load_semantic_cache(agent: Any) -> None:
    try:
        if os.path.exists(agent._semantic_cache_path):
            with open(agent._semantic_cache_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list):
                agent._semantic_cache = data
    except Exception as exc:
        agent._log(f"⚠️ 시맨틱 캐시 로드 실패: {exc}")


def save_semantic_cache(agent: Any) -> None:
    try:
        os.makedirs(os.path.dirname(agent._semantic_cache_path), exist_ok=True)
        with open(agent._semantic_cache_path, "w", encoding="utf-8") as handle:
            json.dump(agent._semantic_cache, handle, ensure_ascii=False)
    except Exception as exc:
        agent._log(f"⚠️ 시맨틱 캐시 저장 실패: {exc}")


def record_exploration_summary(agent: Any, *, result: ExplorationResult) -> None:
    if not agent._memory_store.enabled:
        return
    status = "success" if result.completion_reason and "완료" in result.completion_reason else "finished"
    try:
        agent._memory_store.add_dialog_summary(
            MemorySummaryRecord(
                episode_id=agent._memory_episode_id,
                domain=agent._memory_domain,
                command="/ai",
                summary=(
                    f"actions={result.total_actions}, pages={result.total_pages_visited}, "
                    f"issues={len(result.issues_found)}, reason={result.completion_reason}"
                ),
                status=status,
                metadata={
                    "total_actions": result.total_actions,
                    "total_pages": result.total_pages_visited,
                    "issues": len(result.issues_found),
                    "completion_reason": result.completion_reason,
                },
            )
        )
    except Exception:
        return


def get_llm_cache_key(prompt: str, screenshot: Optional[str], action_signature: str) -> str:
    digest = hashlib.md5()
    digest.update(prompt.encode("utf-8"))
    digest.update(action_signature.encode("utf-8"))
    if screenshot:
        digest.update(screenshot.encode("utf-8"))
    return digest.hexdigest()


def semantic_cache_text(agent: Any, page_state: PageState, testable_actions: List[TestableAction]) -> str:
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
        f"tested={len(agent._tested_elements)};"
        f"history={';'.join(agent._action_history[-3:])}"
    )
    action_signature = agent._action_signature(testable_actions)
    return (
        f"{page_state.url}\n{element_summary}\n{state_summary}\n"
        f"signature={action_signature}\n{actions_text}"
    )


def embed_text(text: str) -> List[float]:
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


def cosine_similarity(left: List[float], right: List[float]) -> float:
    if not left or not right:
        return 0.0
    length = min(len(left), len(right))
    dot = sum(left[i] * right[i] for i in range(length))
    left_norm = math.sqrt(sum(left[i] * left[i] for i in range(length)))
    right_norm = math.sqrt(sum(right[i] * right[i] for i in range(length)))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def semantic_cache_lookup(agent: Any, text: str, action_signature: str, threshold: float = 0.95) -> Optional[str]:
    if not agent._semantic_cache:
        return None
    query_embedding = embed_text(text)
    best_score = 0.0
    best_response: Optional[str] = None
    for entry in agent._semantic_cache:
        embedding = entry.get("embedding")
        response = entry.get("response")
        signature = entry.get("signature")
        if signature != action_signature:
            continue
        if not isinstance(embedding, list) or not isinstance(response, str):
            continue
        score = cosine_similarity(query_embedding, embedding)
        if score > best_score:
            best_score = score
            best_response = response
    if best_response and best_score >= threshold:
        agent._log(f"🧠 시맨틱 캐시 hit (score={best_score:.2f})")
        return best_response
    return None


def semantic_cache_store(agent: Any, text: str, response: str, action_signature: str) -> None:
    embedding = embed_text(text)
    agent._semantic_cache.append(
        {
            "embedding": embedding,
            "response": response,
            "text": text[:500],
            "signature": action_signature,
        }
    )
    if len(agent._semantic_cache) > 200:
        agent._semantic_cache = agent._semantic_cache[-200:]
    save_semantic_cache(agent)
