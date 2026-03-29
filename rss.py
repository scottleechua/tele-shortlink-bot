import base64
import re
from dataclasses import dataclass

import httpx
import xml.etree.ElementTree as ET


@dataclass
class Episode:
    title: str
    guid: str
    pub_date: str
    season: int | None
    episode: int | None

    @property
    def suggested_slug(self) -> str | None:
        """Return sXXeXX slug if both season and episode are known, else None."""
        if self.season is not None and self.episode is not None:
            return f"s{self.season:02d}e{self.episode:02d}"
        return None

    @property
    def guid_b64(self) -> str:
        return base64.b64encode(self.guid.encode()).decode().rstrip("=")


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _extract_season_episode(item: ET.Element, ns: dict) -> tuple[int | None, int | None]:
    """Try itunes:season / itunes:episode tags first, then title heuristics."""
    season = _parse_int(
        (item.find("itunes:season", ns) or ET.Element("x")).text
    )
    episode = _parse_int(
        (item.find("itunes:episode", ns) or ET.Element("x")).text
    )

    if season is not None and episode is not None:
        return season, episode

    # Fallback: scan title for patterns like S02E04, s2e4, Ep 4, Episode 4
    title_el = item.find("title")
    title = title_el.text if title_el is not None else ""

    se_match = re.search(r"[Ss](\d+)[Ee](\d+)", title or "")
    if se_match:
        return int(se_match.group(1)), int(se_match.group(2))

    ep_match = re.search(r"[Ee]p(?:isode)?\.?\s*(\d+)", title or "")
    if ep_match:
        return season, int(ep_match.group(1))

    return season, episode


async def fetch_episodes(rss_url: str) -> list[Episode]:
    """Fetch RSS feed and return episodes in feed order (newest first)."""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(rss_url, timeout=15)
        resp.raise_for_status()

    root = ET.fromstring(resp.content)
    ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}

    channel = root.find("channel")
    if channel is None:
        raise ValueError("No <channel> found in RSS feed")

    episodes = []
    for item in channel.findall("item"):
        title_el = item.find("title")
        title = title_el.text.strip() if title_el is not None and title_el.text else "Untitled"

        guid_el = item.find("guid")
        if guid_el is None or not guid_el.text:
            continue  # skip items with no GUID
        guid = guid_el.text.strip()

        pub_date_el = item.find("pubDate")
        pub_date = pub_date_el.text.strip() if pub_date_el is not None and pub_date_el.text else ""

        season, episode = _extract_season_episode(item, ns)

        episodes.append(Episode(
            title=title,
            guid=guid,
            pub_date=pub_date,
            season=season,
            episode=episode,
        ))

    return episodes
