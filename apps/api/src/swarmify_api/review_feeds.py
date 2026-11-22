"""Review Feeds: what waits on the operator in an installed package's own app, brought into Needs
you so the operator has one inbox. A package declares its feed in its Manifest (`[reviews]`: a
`label`, the [[config]] key `url` of its base URL, and a `path`); each world fills that URL in on
its Connectors page. The core GETs it once a minute with a short timeout and expects:

    {"open_url": "https://...",                      # what the phone opens; http(s) only
     "items": [{"id": "d1",                          # required, stable: pushed once per id
                "kind": "move",                      # grouped by in the notice
                "title": "Tail swish",               # named in a push for one new item
                "run_id": "run_01..."}]}             # a Swarmify run (or the family's first)

Other fields are ignored. An empty `items` is nothing waiting. A feed that is down, slow or
answers anything else is logged once and shows nothing; Needs you reads the last answers held
here, so it never waits on a feed.
"""

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from swarmify_api.upkeep import Notice
from swarmify_connectors import Connectors
from swarmify_worlds import Worlds

log = logging.getLogger(__name__)

TIMEOUT = 2.0


def fetch(url: str) -> Any:
    """The feed's JSON. Raises on a refusal, a timeout or a body that is not JSON."""
    res = httpx.get(url, timeout=TIMEOUT)
    res.raise_for_status()
    return res.json()


@dataclass(frozen=True)
class Feed:
    world_id: str
    url: str
    label: str
    open_url: str | None
    items: list[dict[str, Any]]


class RunFeed(BaseModel):
    """A feed's items from one run's family, for its run page."""

    label: str
    count: int
    open_url: str | None


SHOWN_WORKFLOWS = 3
"""How many workflows a notice names before "and N more"."""


def plural(word: str, n: int) -> str:
    """`word` for `n` things: "prop", "props", "boxes", "stories"."""
    if n == 1 or word.endswith("s"):
        return word
    if word.endswith(("x", "ch", "sh", "z")):
        return word + "es"
    if word.endswith("y") and word[-2:-1] not in ("a", "e", "i", "o", "u", ""):
        return word[:-1] + "ies"
    return word + "s"


class ReviewFeeds:
    """`workflow_of(world_id, run_id)` is the name of that world's run's workflow, else None
    (never the run's own name, which an Ad Hoc Run takes from its free-text goal)."""

    def __init__(
        self,
        worlds: Worlds,
        registry: Connectors,
        workflow_of: Callable[[str, str], str | None],
        get: Callable[[str], Any] | None = None,
    ) -> None:
        self.worlds, self.registry, self.workflow_of = worlds, registry, workflow_of
        self.get = get or fetch
        self.found: list[Feed] = []
        # ponytail: in memory like WebPush.seen; a restart takes the first answer as the baseline,
        # so an item made while the app was down is shown but never pushed.
        self.seen: dict[str, set[str]] = {}
        self.failing: set[str] = set()

    def refresh(self) -> str | None:
        """Read every world's feeds. Returns the push for items not seen before, at most one a
        pass, or None. A feed's first answer only sets what was seen."""
        found, new = [], []
        for world in self.worlds.list_all():
            for _, label, url in self.registry.review_feeds(world.id):
                feed = self._read(world.id, label, url)
                if feed is None:
                    continue
                found.append(feed)
                ids = {str(i["id"]) for i in feed.items}
                if url in self.seen:
                    new += [(feed, i) for i in feed.items if str(i["id"]) not in self.seen[url]]
                self.seen[url] = self.seen.get(url, set()) | ids
        self.found = found
        if not new:
            return None
        if len(new) == 1:
            feed, item = new[0]
            return f"{feed.label}: {item.get('title') or item['id']} needs you."
        labels = Counter(feed.label for feed, _ in new)
        return " ".join(f"{n} new {label} need you." for label, n in labels.items())

    def _read(self, world_id: str, label: str, url: str) -> Feed | None:
        try:
            answer = self.get(url)
            items = [i for i in answer["items"] if isinstance(i, dict) and i.get("id")]
            open_url = answer.get("open_url")
        except Exception as exc:  # whatever a package's app does, upkeep goes on
            if url not in self.failing:
                log.warning("review feed %s unreadable: %s", url, str(exc) or type(exc).__name__)
            self.failing.add(url)
            return None
        self.failing.discard(url)
        if not (isinstance(open_url, str) and open_url.startswith(("https://", "http://"))):
            open_url = None
        return Feed(world_id, url, label, open_url, items)

    def notices(self) -> list[Notice]:
        """One notice a feed with items: "3 Things need you: 2 moves, 1 prop. 3 from Daily cut."
        Runs are named by their workflow, the top three, then how many more."""
        out = []
        for f in self.found:
            if not f.items:
                continue
            kinds = Counter(str(i.get("kind") or "other") for i in f.items)
            flows = Counter(
                name
                for i in f.items
                if i.get("run_id") and (name := self.workflow_of(f.world_id, str(i["run_id"])))
            )
            text = f"{len(f.items)} {f.label} need you: "
            text += ", ".join(f"{n} {plural(k, n)}" for k, n in kinds.most_common()) + "."
            top = flows.most_common(SHOWN_WORKFLOWS)
            if top:
                text += " " + ", ".join(f"{n} from {name}" for name, n in top)
                more = flows.total() - sum(n for _, n in top)
                text += f" and {more} more." if more else "."
            out.append(Notice(kind="feed", text=text, world_id=f.world_id, url=f.open_url))
        return out

    def for_run(self, world_id: str, family: set[str]) -> list[RunFeed]:
        out = []
        for f in self.found:
            n = sum(1 for i in f.items if f.world_id == world_id and i.get("run_id") in family)
            if n:
                out.append(RunFeed(label=f.label, count=n, open_url=f.open_url))
        return out


def router(feeds: ReviewFeeds, family: Callable[[str], set[str]]) -> APIRouter:
    """`family(run_id)` is the run's id and those of the runs it came from."""
    api = APIRouter(prefix="/api", tags=["notices"])

    @api.get("/worlds/{world_id}/runs/{run_id}/review-feed")
    def run_feed(world_id: str, run_id: str) -> list[RunFeed]:
        return feeds.for_run(world_id, family(run_id))

    return api
