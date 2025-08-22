# app/seo.py
from __future__ import annotations

import os
import re
import json
import time
import inspect
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

# Import the Playwright fetcher (added in app/browser_fetch.py)
from .browser_fetch import fetch_rendered  # type: ignore

# =========================
# Fast, universal rendered scan
# =========================

FAST_SCAN = os.getenv("FAST_SCAN", "1").lower() not in ("0", "false", "no")

UA_DEFAULT = os.getenv(
    "CRAWL_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Shorter timeouts in FAST mode
DEFAULT_TIMEOUT = float(os.getenv("HTTP_TIMEOUT_SEC", "12" if FAST_SCAN else "25"))

# One optional, global proxy (applied site-agnostically) — used only for httpx fallbacks/checks
PROXY_DEFAULT = os.getenv("PROXY_DEFAULT")  # e.g., http://user:pass@proxy:8080

# PSI (optional; skipped in FAST mode)
PSI_API_KEY = os.getenv("GOOGLE_PSI_API_KEY") or os.getenv("PAGESPEED_API_KEY")

# WAF cooldown
WAF_COOLDOWN_SEC = int(os.getenv("WAF_COOLDOWN_SEC", "900"))
_WAF_COOLDOWN: Dict[str, float] = {}  # host -> until_ts

# Playwright navigation tuning
PW_WAIT_UNTIL = os.getenv("PLAYWRIGHT_WAIT_UNTIL", "networkidle")  # 'load'|'domcontentloaded'|'networkidle'
PW_WAIT_MS_AFTER = int(os.getenv("PLAYWRIGHT_WAIT_MS_AFTER", "800"))
PW_TIMEOUT_MS = int(float(os.getenv("PLAYWRIGHT_TIMEOUT_MS", str(int(DEFAULT_TIMEOUT * 1000)))))

def _host(url: str) -> str:
    return urlsplit(url).netloc.lower()

def _in_cooldown(h: str) -> bool:
    return _WAF_COOLDOWN.get(h, 0) > time.time()

def _enter_cooldown(h: str):
    _WAF_COOLDOWN[h] = time.time() + WAF_COOLDOWN_SEC

def _looks_like_waf(html_bytes: bytes) -> bool:
    if not html_bytes:
        return False
    t = html_bytes[:8000].decode(errors="ignore").lower()
    signals = (
        "access denied", "request blocked", "you have been blocked",
        "the owner of this website", "reference #", "malicious or automated"
    )
    return any(s in t for s in signals)

def _origin_referer(u: str) -> str:
    try:
        p = urlsplit(u)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}/"
    except Exception:
        pass
    return ""

def build_headers_for(url: str) -> Dict[str, str]:
    # Still used for secondary requests (robots/link checks/psi) and parity
    return {
        "User-Agent": UA_DEFAULT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": os.getenv("CRAWL_LANG", "en-US,en;q=0.9"),
        "Upgrade-Insecure-Requests": "1",
        "DNT": "1",
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not(A:Brand";v="8"',
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-mobile": "?0",
        "Referer": _origin_referer(url),
        # Let the server compress:
        "Accept-Encoding": "gzip, deflate, br",
    }

# -----------------------------
# httpx cross-version proxy shim
# -----------------------------
def _client_kwargs(base_kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Make AsyncClient kwargs compatible across httpx versions:
    - If constructor supports 'proxies', use it (dict or str).
    - Else if supports 'proxy', use it (str).
    - Else, fall back to env vars (HTTP_PROXY/HTTPS_PROXY).
    """
    if not PROXY_DEFAULT:
        return base_kwargs

    params = inspect.signature(httpx.AsyncClient.__init__).parameters
    # Prefer 'proxies' if available
    if "proxies" in params:
        base_kwargs["proxies"] = {"all": PROXY_DEFAULT}
        return base_kwargs
    # Otherwise try 'proxy'
    if "proxy" in params:
        base_kwargs["proxy"] = PROXY_DEFAULT
        return base_kwargs

    # Last resort: environment fallback
    os.environ.setdefault("HTTP_PROXY", PROXY_DEFAULT)
    os.environ.setdefault("HTTPS_PROXY", PROXY_DEFAULT)
    return base_kwargs

# =====================================
# Primary fetch now uses Playwright DOM
# =====================================
async def fetch(url: str, timeout: float = DEFAULT_TIMEOUT) -> Tuple[int, bytes, Dict[str, str], Dict[str, Any]]:
    """
    Returns: (load_ms, body_bytes, headers_lower, netinfo)
    - body_bytes is the fully rendered DOM (UTF-8).
    - headers are from initial navigation response when available.
    - netinfo includes final_url, redirects (best-effort), status_code.
    """
    # Playwright renders; time measures nav + settle wait.
    r = await fetch_rendered(
        url,
        wait_until=PW_WAIT_UNTIL,
        wait_ms_after=PW_WAIT_MS_AFTER,
        timeout_ms=PW_TIMEOUT_MS,
        user_agent=UA_DEFAULT,
        viewport=(1366, 768),
        screenshot=False if FAST_SCAN else False,  # keep off by default
    )
    body = (r.html or "").encode("utf-8", "ignore")
    headers_lower = {k.lower(): v for k, v in (r.headers or {}).items()}
    netinfo = {
        "http_version": "unknown",            # Playwright doesn't expose easily here
        "final_url": r.final_url or url,
        "redirects": None,                    # Not directly exposed; could be inferred separately
        "status_code": int(r.status or 0),
    }
    return int(r.timing_ms), body, headers_lower, netinfo

# ==================
# HTML parsing utils
# ==================
def _text(node) -> Optional[str]:
    try:
        return (node.get_text(separator=" ", strip=True) or "").strip()
    except Exception:
        return None

def _safe_json_loads(s: str) -> Any:
    try:
        return json.loads(s)
    except Exception:
        return None

def _norm_list(urls: List[str]) -> List[str]:
    seen, out = set(), []
    for u in urls:
        if not u:
            continue
        if u not in seen:
            seen.add(u); out.append(u)
    return out

def extract_structured_data_full(body: bytes, base_url: str) -> Dict[str, Any]:
    soup = BeautifulSoup(body, "lxml")
    json_ld: List[Any] = []
    for tag in soup.find_all("script", type=lambda v: v and "ld+json" in v.lower()):
        txt = tag.string or tag.get_text() or ""
        data = _safe_json_loads(txt)
        if data is None:
            continue
        if isinstance(data, dict) and "@graph" in data and isinstance(data["@graph"], list):
            json_ld.extend([x for x in data["@graph"] if isinstance(x, dict)])
        elif isinstance(data, list):
            json_ld.extend([x for x in data if isinstance(x, dict)])
        elif isinstance(data, dict):
            json_ld.append(data)

    microdata = soup.select("[itemscope]")
    rdfa = soup.select("[vocab], [typeof], [property]")
    return {
        "json_ld": json_ld,
        "microdata": [{"count": len(microdata)}] if microdata else [],
        "rdfa": [{"count": len(rdfa)}] if rdfa else [],
    }

_SD_REQUIRED = {
    "Article": ["headline"],
    "BlogPosting": ["headline"],
    "NewsArticle": ["headline"],
    "Product": ["name"],
    "Event": ["name", "startDate"],
    "Organization": ["name"],
    "LocalBusiness": ["name", "address"],
    "FAQPage": ["mainEntity"],
    "HowTo": ["name", "step"],
}
def _sd_req(t: str) -> List[str]: return _SD_REQUIRED.get(t, [])

def _jsonld_items(anyv: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in anyv or []:
        if isinstance(it, dict):
            out.append(it)
    return out

def validate_jsonld(jsonld_any: List[Any]) -> Dict[str, Any]:
    items = _jsonld_items(jsonld_any)
    report = []
    for it in items:
        typ = it.get("@type")
        tval = (typ[0] if isinstance(typ, list) and typ else (typ or "Unknown"))
        req = _sd_req(str(tval))
        missing = [f for f in req if f not in it or (isinstance(it.get(f), str) and not it.get(f).strip())]
        report.append({"type": tval, "missing": missing, "ok": len(missing) == 0 if req else True})
    summary = {"total_items": len(items), "ok_count": sum(1 for r in report if r["ok"]), "has_errors": any(not r["ok"] for r in report)}
    return {"summary": summary, "items": report}

def _localname(t: Optional[str]) -> Optional[str]:
    if not t:
        return None
    if "#" in t:
        t = t.rsplit("#", 1)[-1]
    if "/" in t:
        t = t.rstrip("/").rsplit("/", 1)[-1]
    t = t.strip()
    return t or None

def structured_types_present(jsonld: List[Any]) -> Dict[str, Any]:
    types: set[str] = set()
    for item in _jsonld_items(jsonld):
        t = item.get("@type")
        if isinstance(t, list):
            for x in t:
                if isinstance(x, str):
                    types.add(_localname(x) or x)
        elif isinstance(t, str):
            types.add(_localname(t) or t)
    return {"types": sorted(types)}

def _extract_text_for_density(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)

def keyword_density(text: str, top_n: int = 10) -> List[Dict[str, Any]]:
    STOPWORDS = {"the","and","for","are","but","not","you","your","with","have","this","that","was","from","they",
                 "his","her","she","him","has","had","were","will","what","when","where","who","why","how","can",
                 "all","any","each","few","more","most","other","some","such","no","nor","too","very","of","to",
                 "in","on","by","is","as","at","it","or","be","we","an","a","our","us","if","out","up","so","do",
                 "did","does","their","its","than","then"}
    words = re.findall(r"[A-Za-z]{3,}", text.lower())
    freq: Dict[str, int] = {}
    for w in words:
        if w in STOPWORDS:
            continue
        freq[w] = freq.get(w, 0) + 1
    total = sum(freq.values()) or 1
    items = sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    return [{"word": w, "count": c, "percent": round(100.0 * c / total, 2)} for w, c in items]

# ------------
# parse_html()
# ------------
def parse_html(url: str, body: bytes, headers: Dict[str, str], load_ms: int) -> Dict[str, Any]:
    # NOTE: 'body' is rendered HTML now
    soup = BeautifulSoup(body or b"", "lxml")

    # basics
    title = (soup.title.string.strip() if soup.title and soup.title.string else None)
    if not title:
        ogt = soup.find("meta", attrs={"property": "og:title"})
        title = ogt.get("content").strip() if ogt and ogt.get("content") else None

    desc = soup.find("meta", attrs={"name": "description"})
    description = (desc.get("content").strip() if desc and desc.get("content") else None)

    can = soup.find("link", rel=lambda v: v and "canonical" in [r.lower() for r in (v if isinstance(v, list) else [v])])
    canonical = can.get("href").strip() if can and can.get("href") else None

    rob = soup.find("meta", attrs={"name": "robots"})
    robots_meta = (rob.get("content").strip() if rob and rob.get("content") else None)

    # og / twitter
    open_graph: Dict[str, str] = {}
    for m in soup.find_all("meta", attrs={"property": True}):
        prop = m.get("property", "")
        if prop and prop.lower().startswith("og:"):
            open_graph[prop.lower()] = m.get("content", "")
    twitter_card: Dict[str, str] = {}
    for m in soup.find_all("meta", attrs={"name": True}):
        name = m.get("name", "")
        if name and name.lower().startswith("twitter:"):
            twitter_card[name.lower()] = m.get("content", "")

    # headings
    headings = {
        "h1": [_text(x) for x in soup.find_all("h1") if _text(x)],
        "h2": [_text(x) for x in soup.find_all("h2") if _text(x)],
        "h3": [_text(x) for x in soup.find_all("h3") if _text(x)],
        "h4": [_text(x) for x in soup.find_all("h4") if _text(x)],
        "h5": [_text(x) for x in soup.find_all("h5") if _text(x)],
        "h6": [_text(x) for x in soup.find_all("h6") if _text(x)],
    }

    # links (rendered DOM)
    base_host = _host(url)
    internal, external, nofollow = [], [], []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        absu = urljoin(url, href)
        h = _host(absu)
        (internal if (h == base_host or not h) else external).append(absu)
        rel = " ".join((a.get("rel") or [])).lower()
        if "nofollow" in rel:
            nofollow.append(absu)
    internal = _norm_list(internal)
    external = _norm_list(external)
    nofollow = _norm_list(nofollow)

    # images / alt coverage (after lazy-load thanks to PW)
    imgs = soup.find_all("img")
    total_imgs = len(imgs)
    missing_alt = []
    alt_ok = 0
    for im in imgs:
        alt = (im.get("alt") or "").strip()
        if alt:
            alt_ok += 1
        else:
            src = im.get("src") or ""
            missing_alt.append({"src": urljoin(url, src)} if src else {"src": ""})
    alt_percent = round(100.0 * alt_ok / total_imgs, 2) if total_imgs else 100.0

    # hreflang
    hreflang = []
    for l in soup.find_all("link", rel=True, href=True):
        rels = [r.lower() for r in (l.get("rel") if isinstance(l.get("rel"), list) else [l.get("rel")])]
        if "alternate" in rels and l.get("hreflang"):
            hreflang.append({"hreflang": l.get("hreflang"), "href": urljoin(url, l.get("href"))})

    # AMP
    amp_link = soup.find("link", rel=lambda v: v and "amphtml" in [r.lower() for r in (v if isinstance(v, list) else [v])])
    amp_url = amp_link.get("href").strip() if amp_link and amp_link.get("href") else None
    is_amp = False
    try:
        html_tag = soup.find("html")
        if html_tag:
            is_amp = any(attr.lower() in ("amp", "⚡") for attr in html_tag.attrs)
    except Exception:
        pass

    # structured data
    sd = extract_structured_data_full(body or b"", url)
    sd_types = structured_types_present(sd.get("json_ld") or [])
    sd_validation = validate_jsonld(sd.get("json_ld") or [])

    # keyword density
    text_content = _extract_text_for_density(soup)
    kd_top = keyword_density(text_content, top_n=10)

    # checks
    vpm = soup.find("meta", attrs={"name": "viewport"})
    viewport_value = vpm.get("content", "").strip() if vpm and vpm.get("content") else ""
    robots_val = (robots_meta or "").lower()
    robots_index = "noindex" not in robots_val
    robots_follow = "nofollow" not in robots_val
    xrt = (headers.get("x-robots-tag") or "").lower()
    xrt_index = "noindex" not in xrt
    xrt_follow = "nofollow" not in xrt
    # lang/charset
    lang = None
    try:
        lang = soup.html.get("lang") if soup.html else None
    except Exception:
        pass
    charset = None
    mcs = soup.find("meta", attrs={"charset": True})
    if mcs:
        charset = mcs.get("charset")
    if not charset:
        mct = soup.find("meta", attrs={"http-equiv": True, "content": True})
        if mct and str(mct.get("http-equiv", "")).lower() == "content-type":
            c = mct.get("content", "").lower()
            if "charset=" in c:
                charset = c.split("charset=", 1)[-1].strip()
    # compression from headers (from nav response if available)
    enc = (headers.get("content-encoding") or "").lower()
    enc_map = {"br": "Brotli", "gzip": "gzip", "deflate": "deflate", "zstd": "zstd"}
    pretty_enc = next((v for k, v in enc_map.items() if k in enc), None)

    checks = {
        "canonical": {"ok": bool(canonical)},
        "viewport_meta": {"present": bool(viewport_value), "value": viewport_value, "ok": bool(viewport_value)},
        "h1_count": {"count": len(headings["h1"]), "ok": 1 <= len(headings["h1"]) <= 2},
        "alt_coverage": {"ok": alt_percent >= 80.0, "percent": alt_percent, "total_imgs": total_imgs},
        "robots_meta_index": {"value": "index" if robots_index else "noindex", "ok": robots_index},
        "robots_meta_follow": {"value": "follow" if robots_follow else "nofollow", "ok": robots_follow},
        "x_robots_tag": {"raw": xrt, "ok": xrt_index},
        "lang": {"ok": bool(lang), "value": lang or ""},
        "charset": {"ok": bool(charset), "value": charset or ""},
        "compression": {"ok": bool(pretty_enc), "value": pretty_enc or "none"},
    }

    has_og = bool(open_graph)
    has_tw = bool(twitter_card)

    return {
        "url": url,
        "title": title,
        "description": description,
        "canonical": canonical,
        "robots_meta": robots_meta,
        "open_graph": open_graph,
        "twitter_card": twitter_card,
        "has_open_graph": has_og,
        "has_twitter_card": has_tw,
        "headings": headings,
        "h1": headings["h1"],
        "h2": headings["h2"],
        "h3": headings["h3"],
        "h4": headings["h4"],
        "h5": headings["h5"],
        "h6": headings["h6"],
        "internal_links": internal,
        "external_links": external,
        "nofollow_links": nofollow,
        "images_missing_alt": missing_alt,
        "hreflang": hreflang,
        "json_ld": sd.get("json_ld") or [],
        "microdata": sd.get("microdata") or [],
        "rdfa": sd.get("rdfa") or [],
        "sd_types": sd_types,
        "json_ld_validation": sd_validation,
        "keyword_density_top": kd_top,
        "is_amp": bool(is_amp),
        "amp_url": amp_url,
        "checks": checks,
    }

# ------------------------
# Robots/sitemaps (simple)
# ------------------------
async def robots_and_sitemaps(url: str) -> Dict[str, Any]:
    try:
        p = urlsplit(url)
        robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
        kwargs = _client_kwargs({
            "follow_redirects": True,
            "timeout": 6 if FAST_SCAN else 10,
            "headers": build_headers_for(url),
        })
        async with httpx.AsyncClient(**kwargs) as client:
            r = await client.get(robots_url)
            txt = r.text if r.status_code < 500 else ""
    except Exception:
        txt = ""

    sitemaps = []
    blocked = None
    if txt:
        for line in txt.splitlines():
            l = line.strip()
            if not l or l.startswith("#"):
                continue
            if l.lower().startswith("sitemap:"):
                sm = l.split(":", 1)[1].strip()
                sitemaps.append({"url": sm})
        # naive disallow check for UA: *
        ua_any = False
        disallows: List[str] = []
        for line in txt.splitlines():
            l = line.strip()
            if not l:
                continue
            low = l.lower()
            if low.startswith("user-agent:"):
                ua_any = ("*" in low)
            elif ua_any and low.startswith("disallow:"):
                path = l.split(":", 1)[1].strip() or "/"
                disallows.append(path)
            elif low.startswith("user-agent:") and ua_any:
                break
        path = urlsplit(url).path or "/"
        blocked = any(path.startswith(d) for d in disallows if d)
    return {"robots_url": f"{urlsplit(url).scheme}://{urlsplit(url).netloc}/robots.txt", "blocked_by_robots": blocked, "sitemaps": sitemaps}

# ------------------------
# Link check (tiny, fast)
# ------------------------
async def _check_urls(urls: List[str], limit: int) -> List[Dict[str, Any]]:
    sample = urls[:limit]
    out: List[Dict[str, Any]] = []
    if not sample:
        return out
    timeout = httpx.Timeout(5.0 if FAST_SCAN else 10.0)
    kwargs = _client_kwargs({
        "follow_redirects": True,
        "timeout": timeout,
        "trust_env": True,
        "limits": httpx.Limits(max_keepalive_connections=4, max_connections=6),
        "headers": build_headers_for(sample[0]) if sample else {},
    })
    async with httpx.AsyncClient(**kwargs) as client:
        for u in sample:
            try:
                r = await client.head(u)
                # Some hosts 405/403 on HEAD → try GET quickly
                if r.status_code in (405, 403, 400):
                    r = await client.get(u)
                out.append({"url": u, "final_url": str(r.url), "status": r.status_code, "redirects": len(r.history)})
            except Exception as e:
                out.append({"url": u, "final_url": u, "status": None, "error": str(e), "redirects": 0})
    return out

# -------------------------
# PageSpeed Insights (opt.)
# -------------------------
async def _fetch_psi(url: str) -> Dict[str, Any]:
    if FAST_SCAN or not PSI_API_KEY:
        return {"enabled": False, "message": "FAST_SCAN on or PSI key missing."}
    api = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
    async def one(strategy: str) -> Dict[str, Any]:
        params = {"url": url, "strategy": strategy, "key": PSI_API_KEY, "category": "PERFORMANCE"}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(api, params=params)
                data = r.json()
        except Exception as e:
            return {"score": None, "metrics": {}, "error": str(e)}
        try:
            lhr = data["lighthouseResult"]
            score = round((lhr["categories"]["performance"]["score"] or 0) * 100)
            audits = lhr.get("audits", {})
            def val(a, k="numericValue"): return audits.get(a, {}).get(k)
            metrics = {
                "FCP": val("first-contentful-paint"),
                "LCP": val("largest-contentful-paint"),
                "CLS": audits.get("cumulative-layout-shift", {}).get("numericValue"),
                "TBT": val("total-blocking-time"),
                "TTI": val("interactive"),
                "Speed Index": val("speed-index"),
            }
            return {"score": score, "metrics": metrics}
        except Exception as e:
            return {"score": None, "metrics": {}, "error": f"PSI parse failed: {e}"}
    return {"enabled": True, "mobile": await one("mobile"), "desktop": await one("desktop")}

# =========
# analyze()
# =========
async def analyze(url: str) -> Dict[str, Any]:
    host = _host(url)
    if _in_cooldown(host):
        return {
            "url": url,
            "status_code": 0,
            "errors": [f"Temporarily cooled down for {host} after WAF block."],
        }

    # 1) Rendered fetch
    load_ms, body, headers, netinfo = await fetch(url)
    result = parse_html(url, body, headers, load_ms)

    # Basic perf block
    result["status_code"] = int(netinfo.get("status_code") or 0)
    result["content_length"] = int(headers.get("content-length") or len(body or b""))
    result["load_time_ms"] = load_ms
    result["performance"] = {
        "load_time_ms": load_ms,
        "page_size_bytes": result["content_length"],
        "http_version": netinfo.get("http_version"),
        "final_url": netinfo.get("final_url") or url,
        "redirects": netinfo.get("redirects"),
        "https": {
            "is_https": str(netinfo.get("final_url") or url).startswith("https://"),
            "ssl_checked": False,
            "ssl_ok": None,
        },
    }

    # Update indexable with coarse signals + status
    indexable = (result["status_code"] != 404) \
        and result["checks"]["robots_meta_index"]["ok"] \
        and result["checks"]["x_robots_tag"]["ok"]
    result["checks"]["indexable"] = {"value": "Yes" if indexable else "No", "ok": indexable}

    # 2) WAF fallback: if blocked and AMP exists, analyze AMP to populate signals
    if _looks_like_waf(body) and result.get("amp_url"):
        load2, body2, hdr2, net2 = await fetch(result["amp_url"])
        if body2:
            amp_res = parse_html(result["amp_url"], body2, hdr2, load2)
            for k in ("title","description","canonical","robots_meta","open_graph","twitter_card","has_open_graph","has_twitter_card",
                      "headings","h1","h2","h3","h4","h5","h6","internal_links","external_links","nofollow_links",
                      "images_missing_alt","hreflang","json_ld","microdata","rdfa","sd_types","json_ld_validation",
                      "keyword_density_top"):
                if k in amp_res:
                    result[k] = amp_res[k]
            result.setdefault("notes", []).append("Canonical blocked by WAF; AMP analyzed instead.")
        else:
            _enter_cooldown(host)
            result.setdefault("errors", []).append("WAF/CDN blocked both canonical and AMP.")

    # 3) Robots/sitemaps (polite)
    try:
        result["crawl_checks"] = await robots_and_sitemaps(result["performance"]["final_url"])
    except Exception:
        result["crawl_checks"] = {"robots_url": None, "blocked_by_robots": None, "sitemaps": []}

    # 4) Link status sample (small in FAST mode)
    try:
        lim_int = 4 if FAST_SCAN else 10
        lim_ext = 4 if FAST_SCAN else 10
        result["link_checks"] = {
            "internal": await _check_urls(result.get("internal_links", []), lim_int),
            "external": await _check_urls(result.get("external_links", []), lim_ext),
        }
    except Exception:
        result["link_checks"] = {"internal": [], "external": []}

    # 5) PageSpeed/CrUX (skipped in FAST mode)
    try:
        psi = await _fetch_psi(result["performance"]["final_url"])
        result["pagespeed"] = psi
        if psi.get("enabled"):
            result.setdefault("performance", {})
            result["performance"]["mobile_score"] = psi.get("mobile", {}).get("score")
            result["performance"]["desktop_score"] = psi.get("desktop", {}).get("score")
    except Exception as e:
        result["pagespeed"] = {"enabled": False, "message": f"PSI error: {e}"}

    return result
