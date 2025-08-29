from __future__ import annotations
from typing import Dict, Any, List
from selectolax.parser import HTMLParser
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

def _text(node):
    if not node:
        return None
    t = node.text().strip()
    return t or None

def parse_seo(html: str, base_url: str) -> Dict[str, Any]:
    tree = HTMLParser(html)
    soup = BeautifulSoup(html, "lxml")

    # Title
    title = None
    node_t = tree.css_first("title")
    if node_t:
        title = node_t.text().strip()

    # Meta
    meta_desc = ""
    meta_robots = ""
    meta_viewport = ""
    for m in soup.find_all("meta"):
        name = (m.get("name") or m.get("property") or "").lower()
        if name == "description":
            meta_desc = (m.get("content") or "").strip()
        if name == "robots":
            meta_robots = (m.get("content") or "").strip()
        if name == "viewport":
            meta_viewport = (m.get("content") or "").strip()

    # Canonical & hreflang
    canonical = None
    hreflangs = []
    for link in soup.find_all("link"):
        rel = (link.get("rel") or [""])[0].lower() if link.get("rel") else (link.get("rel") or "")
        if rel == "canonical":
            canonical = (link.get("href") or "").strip()
        if (link.get("rel") or [""])[0].lower() == "alternate" and link.get("hreflang"):
            hreflangs.append({
                "lang": link.get("hreflang"),
                "href": link.get("href")
            })

    # Headings
    headings = {
        "h1": [h.get_text(strip=True) for h in soup.find_all("h1")],
        "h2": [h.get_text(strip=True) for h in soup.find_all("h2")],
        "h3": [h.get_text(strip=True) for h in soup.find_all("h3")],
    }

    # Links
    a_tags = soup.find_all("a")
    internal = external = nofollow = 0
    parsed_base = urlparse(base_url)
    for a in a_tags:
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        rel = (a.get("rel") or []) or []
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        if "nofollow" in [r.lower() for r in rel]:
            nofollow += 1
        if parsed.netloc == parsed_base.netloc:
            internal += 1
        else:
            external += 1

    # JSON-LD types
    schema_types: List[str] = []
    for tag in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            import json
            data = json.loads(tag.text)
            def collect_types(obj):
                if isinstance(obj, dict):
                    t = obj.get("@type")
                    if t:
                        if isinstance(t, list):
                            schema_types.extend([str(x) for x in t])
                        else:
                            schema_types.append(str(t))
                    for v in obj.values():
                        collect_types(v)
                elif isinstance(obj, list):
                    for v in obj:
                        collect_types(v)
            collect_types(data)
        except Exception:
            continue

    # Issues
    issues = []
    if not title:
        issues.append("Missing <title>")
    if not meta_desc:
        issues.append("Missing meta description")
    if len(headings["h1"]) == 0:
        issues.append("Missing <h1>")
    if len(headings["h1"]) > 1:
        issues.append("Multiple <h1> tags")

    return {
        "title": title,
        "meta": {
            "description": meta_desc,
            "robots": meta_robots,
            "viewport": meta_viewport,
            "canonical": canonical,
            "hreflang": hreflangs,
        },
        "headings": headings,
        "links": {"internal": internal, "external": external, "nofollow": nofollow},
        "schema_types": sorted(set(schema_types)),
        "issues": issues,
    }
