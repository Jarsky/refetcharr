"""Ids for everything Refetcharr looks after, in one number space (1.9.0). A film keeps its Radarr movie id; a TV season and
an episode (the fallback when no season pack qualifies) get ids far above any Radarr id, so state.json, suggestions, the
ignore list and the API go on using plain integers.

    film     the Radarr movie id                          (below 1,000,000,000)
    season   1,000,000,000 + series id * 10,000 + season  (daily shows number their seasons by year, so 10,000)
    episode  3,000,000,000 + the Sonarr episode id
"""

TV_BASE = 1_000_000_000
EP_BASE = 3_000_000_000
SEASONS = 10_000


def kind(i) -> str:
    i = int(i)
    return "episode" if i >= EP_BASE else "season" if i >= TV_BASE else "film"


def is_tv(i) -> bool:
    return kind(i) != "film"


def season(series_id: int, number: int) -> int:
    return TV_BASE + int(series_id) * SEASONS + int(number)


def split_season(i) -> tuple[int, int]:
    """A season id -> (series id, season number)."""
    return divmod(int(i) - TV_BASE, SEASONS)


def episode(episode_id: int) -> int:
    return EP_BASE + int(episode_id)


def episode_of(i) -> int:
    """An episode item's id -> the Sonarr episode id."""
    return int(i) - EP_BASE


def series_of(i) -> int | None:
    """The Sonarr series id of a season id (an episode's needs a lookup: None)."""
    return split_season(i)[0] if kind(i) == "season" else None
