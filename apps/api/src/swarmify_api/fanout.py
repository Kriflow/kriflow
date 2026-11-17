"""The Items of a Fan-out Stage (ADR 0031), read from its `for_each` source in the run's files:
an earlier stage's cards, the cards picked at a pick gate, or the elements of a JSON list file an
earlier stage made or took. Runs cannot read Memory; the composition root hands it this."""

import json
import re
from pathlib import Path
from typing import Any

from swarmify_api.agents import run_artifacts
from swarmify_api.remote import offered
from swarmify_memory import ArtifactFile, Memory
from swarmify_runs import Item, Run, StageDef


def items(memory: Memory, run: Run, index: int, stage: StageDef) -> list[Item]:
    """Raises ValueError when the source is not there to read."""
    source = str(stage.config.get("for_each") or "")
    earlier = [s for s in run.stages[:index] if s.name == source]
    world = memory.world_folder(run.world_id)
    if earlier:
        at = earlier[-1]
        if at.type == "gate":
            return [_card(world, c) for c in _picked(memory, run, at.index)]
        cards = offered(memory.stage_artifacts(run.world_id, run.id, at.id))
        return [_card(world, c) for c in cards]
    folder = world / "runs" / run.id
    found = [folder / "given" / source] + [
        folder / s.id / "artifacts" / source for s in run.stages[:index]
    ]
    files = [f for f in found if f.is_file()]
    if not files:
        raise ValueError(f"no earlier stage of this run left {source} to fan out over")
    try:
        elements = json.loads(files[-1].read_text())
    except ValueError as exc:
        raise ValueError(f"{source} is not JSON: {exc}") from exc
    if not isinstance(elements, list):
        raise ValueError(f"{source} must hold a JSON list, one Item per element")
    return [_element(i, e) for i, e in enumerate(elements)]


def _picked(memory: Memory, run: Run, index: int) -> list[ArtifactFile]:
    """The cards the latest answer of the pick gate at `index` picked, in pick order."""
    decision = next((d for d in reversed(run.decisions) if d.stage_index == index), None)
    if decision is None:
        return []
    paths = getattr(decision, "artifacts", None) or [decision.artifact]
    cards = offered(run_artifacts(memory, run, index))
    by_path = {c.path: c for c in cards}
    picked = [by_path[p] for p in paths if p in by_path]
    return picked or [c for c in cards if c.title == decision.choice][:1]


def _card(world: Path, card: ArtifactFile) -> Item:
    stem = Path(card.name).stem
    key = stem.removeprefix(f"{card.type}-") or stem
    return Item(_safe(key), card.title or key, file=str(world / card.path))


def _element(index: int, element: Any) -> Item:
    fields = element if isinstance(element, dict) else {}
    key = str(fields.get("id") or index)
    return Item(_safe(key), str(fields.get("title") or key), data=element)


def _safe(key: str) -> str:
    """A key that names a folder: letters, digits, dot, dash and underscore."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", key).strip(".-")[:80] or "item"
