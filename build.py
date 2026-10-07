"""Build the homepage (index.html) and the GitHub profile README from profile.yaml and ORCID.

Usage:
    python build.py                 # writes index.html and profile-README.md
    python build.py --readme PATH   # also write the profile README to PATH

Publications come from the public ORCID record. Author lists, journal, volume and pages
come from Crossref (journal DOIs) or DataCite (arXiv and other DataCite DOIs).
If the network is unavailable, the previous papers.json cache is used.
"""
import datetime
import html
import json
import re
import sys
import urllib.request
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
UA = "zhongdahuang-homepage-build (mailto:dominic.huang@ubc.ca)"


def get_json(url, accept="application/json"):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def norm(title):
    t = re.sub(r"<[^>]+>", "", title).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def clean_title(t):
    """Keep <sub>, <sup>, <i>; escape everything else; drop a trailing period."""
    t = re.sub(r"\s+", " ", t).strip().rstrip(".")
    keep = {}

    def stash(m):
        key = f"\x00{len(keep)}\x00"
        keep[key] = m.group(0).lower()
        return key

    t = re.sub(r"</?(sub|sup|i)>", stash, t, flags=re.I)
    t = html.escape(t, quote=False)
    for k, v in keep.items():
        t = t.replace(k, v)
    return t


def initials(given):
    parts = re.split(r"[\s]+", given.strip())
    out = []
    for p in parts:
        sub = [s for s in p.split("-") if s]
        out.append("-".join(s[0].upper() + "." for s in sub))
    return " ".join(out)


def crossref(doi):
    m = get_json(f"https://api.crossref.org/works/{doi}")["message"]
    authors = ", ".join(
        (initials(a["given"]) + " " if a.get("given") else "") + a.get("family", a.get("name", ""))
        for a in m.get("author", [])
    )
    parts = (m.get("issued") or m.get("published-print"))["date-parts"][0]
    year = parts[0]
    month = parts[1] if len(parts) > 1 else None
    if m.get("published-print"):
        year = m["published-print"]["date-parts"][0][0]
    return {
        "title": (m.get("title") or [""])[0],
        "authors": authors,
        "journal": (m.get("container-title") or [""])[0],
        "volume": m.get("volume"),
        "issue": m.get("issue"),
        "pages": m.get("page") if m.get("volume") else None,
        "number": m.get("article-number"),
        "year": year,
        "month": month,
    }


def datacite(doi):
    a = get_json(f"https://api.datacite.org/dois/{doi}")["data"]["attributes"]
    authors = ", ".join(
        (initials(c["givenName"]) + " " if c.get("givenName") else "") + c.get("familyName", c.get("name", ""))
        for c in a.get("creators", [])
    )
    return {
        "title": a["titles"][0]["title"],
        "authors": authors,
        "journal": a.get("publisher", ""),
        "year": a.get("publicationYear"),
    }


def jats_to_text(a):
    a = re.sub(r"<jats:title>.*?</jats:title>", "", a, flags=re.S)
    a = re.sub(r"<[^>]+>", " ", a)
    a = html.unescape(re.sub(r"\s+", " ", a)).strip()
    return re.sub(r"^(Abstract|ABSTRACT)[:.\s]+", "", a)


def get_abstract(doi):
    try:
        m = get_json(f"https://api.crossref.org/works/{doi}")["message"]
        if m.get("abstract"):
            return jats_to_text(m["abstract"])
    except Exception:
        pass
    try:
        inv = get_json(f"https://api.openalex.org/works/doi:{doi}").get("abstract_inverted_index") or {}
        pos = sorted((i, w) for w, idx in inv.items() for i in idx)
        return " ".join(w for _, w in pos)
    except Exception:
        return ""


def orcid_papers(orcid):
    works = get_json(f"https://pub.orcid.org/v3.0/{orcid}/works")["group"]
    papers = []
    for g in works:
        s = g["work-summary"][0]
        ids = {e["external-id-type"]: e["external-id-value"] for e in (s.get("external-ids") or {}).get("external-id", [])}
        doi = ids.get("doi")
        kind = "preprint" if s["type"] in ("preprint", "working-paper") or (doi or "").lower().startswith("10.48550/") else (
            "article" if s["type"] in ("journal-article", "conference-paper") else "other")
        meta = {}
        if doi:
            for fetch in (crossref, datacite):
                try:
                    meta = fetch(doi)
                    break
                except Exception:
                    continue
        if not meta:
            pd = s.get("publication-date") or {}
            meta = {
                "title": s["title"]["title"]["value"],
                "authors": "",
                "journal": (s.get("journal-title") or {}).get("value", ""),
                "year": int((pd.get("year") or {}).get("value", 0) or 0),
            }
        if ids.get("arxiv") and kind == "preprint":
            meta["journal"] = f"arXiv:{ids['arxiv']}"
        meta["kind"] = kind
        meta["doi"] = doi
        meta["abstract"] = get_abstract(doi) if doi else ""
        meta["arxiv"] = ids.get("arxiv")
        papers.append(meta)
    return papers


def load_papers(cfg):
    cache = HERE / "papers.json"
    try:
        papers = orcid_papers(cfg["orcid"])
        cache.write_text(json.dumps(papers, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        source = "ORCID"
    except Exception as e:  # network problem: fall back to last good list
        print(f"warning: ORCID fetch failed ({e}); using papers.json", file=sys.stderr)
        papers = json.loads(cache.read_text(encoding="utf-8"))
        source = "cache"
    have = {norm(p["title"]) for p in papers}
    for x in cfg.get("extra_papers") or []:
        if norm(x["title"]) not in have:
            papers.append({**x, "kind": "submitted", "journal": x.get("venue", "")})
    extra_links = {norm(k): v for k, v in (cfg.get("paper_links") or {}).items()}
    for p in papers:
        links = list(p.get("links") or [])
        if p.get("arxiv"):
            links.insert(0, {"label": "arXiv", "url": f"https://arxiv.org/abs/{p['arxiv']}"})
        elif p.get("doi"):
            links.insert(0, {"label": "doi", "url": f"https://doi.org/{p['doi']}"})
        for l in extra_links.get(norm(p["title"]), []):
            if l not in links:
                links.append(l)
        p["links"] = links
    papers.sort(key=lambda p: -(int(p.get("year") or 0)))
    return papers, source


def venue(p):
    if p.get("kind") == "submitted":
        return f"{html.escape(p['journal'])}, {p['year']}."
    j = f"<i>{html.escape(p['journal'])}</i>" if p.get("journal") else ""
    if p.get("volume"):
        j += f" {p['volume']}"
        if p.get("issue"):
            j += f"({p['issue']})"
        tail = p.get("pages") or p.get("number")
        if tail:
            j += f", {html.escape(str(tail)).replace('-', '–')}"
    return f"{j}, {p['year']}." if j else f"{p['year']}."


def abstract_block(text):
    if not text:
        return ""
    return f'\n      <details class="abs"><summary>Abstract</summary><p>{html.escape(text)}</p></details>'


def paper_li(p):
    links = '<span class="links">' + " ".join(f'<a href="{html.escape(l["url"])}">{html.escape(l["label"])}</a>' for l in p["links"]) + "</span>"
    authors = f'{html.escape(p["authors"])}.<br>\n      ' if p.get("authors") else ""
    return (f'    <li>\n      <span class="title">{clean_title(p["title"])}.</span><br>\n      {authors}'
            f'<span class="meta">{venue(p)}</span>\n      {links}{abstract_block(p.get("abstract"))}\n    </li>')


def talk_li(t):
    tag = '<span class="tag">invited</span>' if t.get("invited") else ""
    title = f'<span class="title">{html.escape(t["title"])}.</span>{tag}<br>\n      ' if t.get("title") else f"{tag}"
    when = news_date(t["date"])
    where = ", ".join(x for x in [t.get("event"), t.get("place")] if x)
    links = t.get("links") or []
    if t.get("slides"):
        links = [{"label": "slides", "url": t["slides"]}] + links
    lk = (' <span class="links">' + " ".join(f'<a href="{html.escape(l["url"])}">{html.escape(l["label"])}</a>' for l in links) + "</span>") if links else ""
    authors = f'{html.escape(t["authors"])}.<br>\n      ' if t.get("authors") else ""
    return (f'    <li>\n      {title}{authors}<span class="meta">{html.escape(where)}, {when}.</span>{lk}'
            f'{abstract_block((t.get("abstract") or "").strip())}\n    </li>')


ICONS = {
    "mail": '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></svg>',
    "id": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M9 8v8M12.5 8H14a4 4 0 0 1 0 8h-1.5z"/></svg>',
    "code": '<svg viewBox="0 0 24 24"><path d="m8 7-5 5 5 5M16 7l5 5-5 5"/></svg>',
    "book": '<svg viewBox="0 0 24 24"><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z"/><path d="M4 19V5"/></svg>',
    "doc": '<svg viewBox="0 0 24 24"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/></svg>',
    "pin": '<svg viewBox="0 0 24 24"><path d="M12 21s-6-5.3-6-11a6 6 0 0 1 12 0c0 5.7-6 11-6 11z"/><circle cx="12" cy="10" r="2"/></svg>',
}


def icon_for(link):
    u, l = link["url"], link["label"].lower()
    if u.startswith("mailto:"):
        return ICONS["mail"]
    if "orcid" in u:
        return ICONS["id"]
    if "github" in u:
        return ICONS["code"]
    if "scholar" in u:
        return ICONS["book"]
    if l == "cv" or u.endswith(".pdf"):
        return ICONS["doc"]
    return ICONS["pin"]


def news_date(d):
    d = str(d)[:7]
    if re.fullmatch(r"\d{4}-\d{2}", d):
        return datetime.date(int(d[:4]), int(d[5:]), 1).strftime("%b %Y")
    return d


def build_html(cfg, papers):
    e = html.escape
    # sidebar
    if cfg.get("photo"):
        pos = cfg.get("photo_position", "50% 50%")
        zoom = cfg.get("photo_zoom", 1)
        photo = (f'<div class="photo"><img src="{e(cfg["photo"])}" alt="{e(cfg["name"])}" '
                 f'style="transform-origin: {e(pos)}; transform: scale({zoom})"></div>')
    else:
        ini = "".join(w[0] for w in cfg["name"].split()[:2])
        photo = f'<div class="photo mono" aria-hidden="true">{e(ini)}</div>'
    links = "\n".join(f'  <li>{icon_for(l)}<a href="{e(l["url"])}">{e(l["label"])}</a></li>' for l in cfg["links"])
    toc_items = [("about", "About"), ("news", "News"), ("papers", "Papers"), ("teaching", "Teaching"), ("talks", "Talks"), ("education", "Education"), ("awards", "Awards")]
    toc = "\n".join(f'  <li><a href="#{k}">{v}</a></li>' for k, v in toc_items)
    hero = (f'    {photo}\n    <div>\n      <h1>{e(cfg["name"])}</h1>\n'
            f'      <p class="role">{"<br>".join(e(r) for r in cfg["role"])}</p>\n    </div>')
    sidebar = f'<ul>\n  <li>{ICONS["pin"]}Kelowna, BC, Canada</li>\n{links}\n</ul>\n<ul class="toc">\n{toc}\n</ul>'

    out = []
    out.append(f'<section id="about">\n  <h2>About</h2>\n  <p>{e(cfg["about"])}</p>\n  <p>{e(cfg["research"])}</p>\n</section>')
    items = [(str(n["date"]), n["text"]) for n in cfg.get("news") or []]
    for t in cfg.get("talks") or []:
        what = "Invited talk" if t.get("invited") else "Talk"
        ttl = f' <i>{e(t["title"])}</i>.' if t.get("title") else ""
        where = ", ".join(e(x) for x in [t.get("event"), t.get("place")] if x)
        items.append((str(t["date"])[:7], f'{what}:{ttl} {where}.'))
    for p in papers:
        if p["kind"] == "article" and p.get("month"):
            items.append((f'{p["year"]}-{int(p["month"]):02d}',
                          f'Published in <i>{e(p["journal"])}</i>: {clean_title(p["title"])}.'))
    today = datetime.date.today()
    cutoff = (today.year * 12 + today.month) - int(cfg.get("news_months", 24))
    def months(d):
        y, m = (d.split("-") + ["12"])[:2]
        return int(y) * 12 + int(m)
    items = sorted((x for x in items if months(x[0]) > cutoff), key=lambda x: months(x[0]), reverse=True)
    news = "\n".join(f'    <li><time>{e(news_date(d))}</time><span>{t}</span></li>' for d, t in items)
    if news:
        out.append(f'<section id="news">\n  <h2>News</h2>\n  <ul class="news">\n{news}\n  </ul>\n</section>')

    groups = [("Preprints and submitted", ("preprint", "submitted")), ("Journal articles", ("article",)), ("Other", ("other",))]
    sec = ['<section id="papers">\n  <h2>Papers</h2>']
    for head, kinds in groups:
        items = [p for p in papers if p["kind"] in kinds]
        if items:
            sec.append(f"\n  <h3>{head}</h3>\n  <ol class=\"pubs\">\n" + "\n".join(paper_li(p) for p in items) + "\n  </ol>")
    out.append("".join(sec) + "\n</section>")

    t = cfg["teaching"]
    courses = "\n".join(
        f'    <li><span class="title">{e(c["where"])}</span>, {e(c["role"])}<br>\n      {e(c["what"])}</li>' for c in t["courses"])
    ta = "\n".join(f"    <li>{e(x)}</li>" for x in t["assistant"])
    out.append(f'<section id="teaching">\n  <h2>Teaching</h2>\n  <p>{e(t["intro"])}</p>\n\n  <h3>Courses taught</h3>\n  <ol class="pubs">\n{courses}\n  </ol>\n\n'
               f'  <h3>Teaching assistant</h3>\n  <ol class="pubs">\n{ta}\n  </ol>\n</section>')
    tl = sorted(cfg.get("talks") or [], key=lambda t: str(t["date"]), reverse=True)
    talks = "\n".join(talk_li(t) for t in tl)
    out.append(f'<section id="talks">\n  <h2>Talks</h2>\n  <ol class="pubs">\n{talks}\n  </ol>\n</section>')
    ed = "\n".join(
        f'    <li><span class="title">{e(x["degree"])}</span>, {e(x["when"])}<br>\n      {e(x["where"])}'
        + (f'<br>\n      <span class="meta">{e(x["note"])}</span>' if x.get("note") else "") + "</li>"
        for x in cfg.get("education") or [])
    if ed:
        out.append(f'<section id="education">\n  <h2>Education</h2>\n  <ol class="pubs">\n{ed}\n  </ol>\n</section>')
    aw = "\n".join(f"    <li>{e(x)}</li>" for x in cfg.get("awards") or [])
    if aw:
        out.append(f'<section id="awards">\n  <h2>Awards</h2>\n  <ol class="pubs">\n{aw}\n  </ol>\n</section>')
    o = cfg["outside"]
    img = f'<img src="{e(o["image"])}" alt="">' if o.get("image") else ""
    out.append(f'<section id="outside">\n  <h2>Outside research</h2>\n  <div class="outside">{img}<p>{o["text"]}</p></div>\n</section>')
    main = "\n\n".join(out)

    tpl = (HERE / "template.html").read_text(encoding="utf-8")
    tpl = re.sub(r"^<!--.*?-->\n", "", tpl, flags=re.S)
    month = datetime.date.today().strftime("%B %Y")
    desc = f"{cfg['name']}, {cfg['short_bio']}"
    b = cfg.get("banner") or {}
    rep = {"{{NAME}}": e(cfg["name"]), "{{DESC}}": e(desc), "{{ORCID}}": cfg["orcid"], "{{UPDATED}}": month,
           "{{SIDEBAR}}": sidebar, "{{HERO}}": hero, "{{MAIN}}": main, "{{BANNER_ALT}}": e(b.get("alt", "")), "{{BANNER_CREDIT}}": b.get("credit", "")}
    for k, v in rep.items():
        tpl = tpl.replace(k, v)
    return tpl


def md_title(t):
    t = re.sub(r"<sub>(.*?)</sub>", r"\1", t)
    t = re.sub(r"<sup>(.*?)</sup>", r"^\1", t)
    return re.sub(r"<[^>]+>", "", t).strip().rstrip(".")


def build_readme(cfg, papers):
    lines = [f"# {cfg['name']}", "", cfg["short_bio"], "", "**Recent papers**", ""]
    for p in papers[:4]:
        links = " · ".join(f"[{l['label']}]({l['url']})" for l in p["links"])
        v = p["journal"] if p["kind"] == "submitted" else f"*{p['journal']}*"
        lines.append(f"- {md_title(p['title'])}. {v}, {p['year']}. {links}".rstrip())
    lines += ["", "Full list and teaching on my [homepage](%s)." % cfg["homepage"], "",
              " · ".join(f"[{l['label']}]({l['url']})" for l in cfg["links"] if not l["url"].startswith("mailto:")), ""]
    return "\n".join(lines)


def main():
    cfg = yaml.safe_load((HERE / "profile.yaml").read_text(encoding="utf-8"))
    papers, source = load_papers(cfg)
    (HERE / "index.html").write_text(build_html(cfg, papers), encoding="utf-8")
    readme = build_readme(cfg, papers)
    (HERE / "profile-README.md").write_text(readme, encoding="utf-8")
    if "--readme" in sys.argv:
        Path(sys.argv[sys.argv.index("--readme") + 1]).write_text(readme, encoding="utf-8")
    print(f"built index.html and profile-README.md from {len(papers)} papers ({source})")


if __name__ == "__main__":
    main()
