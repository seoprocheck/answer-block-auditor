#!/usr/bin/env python3
"""
answer-block-auditor — can an AI lift an answer off your page?

Generative engines quote passages, not pages. A page that buries its answer 600
words down, under headings that are labels rather than questions, in prose with
no list or table to extract, is invisible to them however well it ranks.

This scores each page on whether the answer is reachable: how early it appears,
whether headings are phrased as questions, whether anything is structurally
extractable, and whether the schema says what the page is.

No API keys, no dependencies.

MIT © SEO Pro Check
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import glob
import gzip
import json
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser

UA = "answer-block-auditor/1.0 (+https://github.com/seoprocheck/answer-block-auditor)"
CHROME_TAGS = {"script", "style", "nav", "header", "footer", "aside", "form",
               "noscript", "svg", "template", "button", "select"}

# Tag-based detection alone is not enough: most CMS themes build their menus,
# related-post rails and share bars out of plain <div>s. Measured on a real
# WordPress site that leaked ~340 nav links and ~260 menu list items into every
# page's "content" counts.
CHROME_CONTAINERS = {"div", "section", "ul", "ol", "aside", "span", "table"}
CHROME_HINT = re.compile(
    r"(^|[-_ ])("
    r"nav(bar|igation)?|(sub|main|primary|top)?menu|breadcrumbs?|pagination|pager|"
    r"sidebar|widgets?|footer|(site|global|page|main)[-_]header|masthead|banner|"
    r"related|recirc|share|social|subscribe|newsletter|cookie|consent|promo|popup|"
    r"modal|offcanvas|drawer|comments?|disqus|toc|tableofcontents|skip|screen-reader"
    r")([-_ ]|$)", re.I)
VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "source", "track", "wbr",
             "col", "area", "base", "embed", "param"}


def is_chrome_attrs(attrs):
    """Site chrome by class/id/role. Deliberately does NOT match entry-header or
    post-header — those hold the H1 on most themes."""
    blob = " ".join(filter(None, (attrs.get("class"), attrs.get("id"), attrs.get("role"))))
    if not blob:
        return False
    if attrs.get("role") in ("navigation", "banner", "contentinfo", "complementary", "search"):
        return True
    return bool(CHROME_HINT.search(blob))

HEADING_TAGS = {"h1", "h2", "h3", "h4"}
QUESTION_STARTS = ("what", "why", "how", "when", "where", "which", "who", "is", "are",
                   "can", "should", "does", "do", "did", "will", "would", "was", "if")
ANSWER_WINDOW = 100      # words after the H1 in which the answer should land
LONG_SECTION = 350       # words under one heading before extraction gets hard
ANSWER_SENTENCE_MAX = 45 # a liftable answer sentence is short
SCAN_DEPTH = 600         # how far in to keep looking before giving up


class Doc(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chrome = 0
        self._stack = []
        self.title = ""
        self._in_title = False
        self.nodes = []              # ordered ("h",lvl,text) / ("p",text) / ("list",n) / ("table",n)
        self._h = None
        self._hbuf = []
        self._buf = []
        self.schema_types = []
        self.faq_questions = []
        self.has_speakable = False
        self._in_jsonld = False
        self._jbuf = []
        self._li = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        chrome = False
        if tag in CHROME_TAGS:
            if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
                self._in_jsonld = True
            chrome = True
        elif tag in CHROME_CONTAINERS and is_chrome_attrs(a):
            chrome = True
        if tag not in VOID_TAGS:
            self._stack.append((tag, chrome))
        if chrome:
            self.chrome += 1
            return
        if tag == "title":
            self._in_title = True
        if self.chrome:
            return
        if tag in HEADING_TAGS:
            self._flush()
            self._h = int(tag[1])
            self._hbuf = []
        elif tag in ("p", "li", "td", "th", "blockquote", "dd", "dt"):
            self._flush()
            if tag == "li":
                self._li += 1
        elif tag == "table":
            self.nodes.append(("table", 1))
        elif tag in ("ul", "ol"):
            self._li = 0

    def handle_endtag(self, tag):
        if tag == "script" and self._in_jsonld:
            self._absorb()
        # Unwind to the matching open tag, clearing chrome for everything popped.
        # Themes leave tags unclosed constantly; without unwinding, one stray
        # </div> permanently mis-tracks whether we are inside chrome.
        closed_chrome = False
        if any(t == tag for t, _ in self._stack):
            while self._stack:
                t, was_chrome = self._stack.pop()
                if was_chrome:
                    self.chrome = max(0, self.chrome - 1)
                    closed_chrome = True
                if t == tag:
                    break
        if closed_chrome or tag in CHROME_TAGS:
            return
        if tag == "title":
            self._in_title = False
        if tag in HEADING_TAGS and self._h:
            t = " ".join(" ".join(self._hbuf).split())
            if t:
                self.nodes.append(("h", self._h, t))
            self._h = None
            self._hbuf = []
        elif tag in ("p", "li", "td", "th", "blockquote", "dd", "dt"):
            self._flush()
        elif tag in ("ul", "ol"):
            if self._li:
                self.nodes.append(("list", self._li))
            self._li = 0

    def handle_data(self, data):
        if self._in_jsonld:
            self._jbuf.append(data)
            return
        if self._in_title:
            self.title += data
        if self.chrome:
            return
        if self._h:
            self._hbuf.append(data)
        elif data.strip():
            self._buf.append(data)

    def _flush(self):
        t = " ".join(" ".join(self._buf).split())
        if len(t) > 1:
            self.nodes.append(("p", t))
        self._buf = []

    def _absorb(self):
        raw = "".join(self._jbuf).strip()
        self._in_jsonld = False
        self._jbuf = []
        try:
            data = json.loads(raw)
        except Exception:
            return
        def walk(o):
            if isinstance(o, dict):
                t = o.get("@type")
                for x in ([t] if isinstance(t, str) else (t or [])):
                    if isinstance(x, str):
                        self.schema_types.append(x)
                if o.get("@type") == "Question" and isinstance(o.get("name"), str):
                    self.faq_questions.append(o["name"].strip())
                if "speakable" in o:
                    self.has_speakable = True
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(data)

    def close(self):
        self._flush()
        super().close()


def words(t):
    return re.findall(r"[A-Za-z0-9'’]+", t)


def is_question(text):
    t = text.strip()
    if t.endswith("?"):
        return True
    w = [x.lower() for x in words(t)]
    return bool(w) and w[0] in QUESTION_STARTS and len(w) >= 4


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def audit(src):
    html, final = read_source(src)
    d = Doc()
    try:
        d.feed(html)
        d.close()
    except Exception:
        pass

    headings = [(lvl, t) for kind, lvl, t in
                [(n[0], n[1], n[2]) for n in d.nodes if n[0] == "h"]]
    h1 = next((t for lvl, t in headings if lvl == 1), "")
    subs = [t for lvl, t in headings if lvl in (2, 3, 4)]
    q_headings = [t for t in subs if is_question(t)]

    # Find the first liftable sentence and how deep into the page it sits.
    # Scanning only as far as the window cannot tell "there is no answer" apart
    # from "the answer is buried", which are different problems with different fixes.
    def scan(nodes, skip_to_h1):
        started = not skip_to_h1
        offset, found = 0, None
        for n in nodes:
            if not started:
                if n[0] == "h":
                    started = True
                continue
            if n[0] != "p":
                continue
            for s in sentences(n[1]):
                wl = len(words(s))
                if found is None and 4 <= wl <= ANSWER_SENTENCE_MAX:
                    found = (offset, s)
                offset += wl
            if offset >= SCAN_DEPTH and found is not None:
                break
        return found, offset

    found, scanned = scan(d.nodes, True)
    if found is None and scanned == 0:
        found, scanned = scan(d.nodes, False)   # no heading at all
    answer_sentence = found[1] if found else ""
    lead_words = found[0] if found else scanned

    # Section lengths — a wall of prose under one heading is hard to quote.
    sections, cur = [], 0
    for n in d.nodes:
        if n[0] == "h":
            sections.append(cur)
            cur = 0
        elif n[0] == "p":
            cur += len(words(n[1]))
    sections.append(cur)
    longest = max(sections) if sections else 0

    lists = sum(n[1] for n in d.nodes if n[0] == "list")
    tables = sum(1 for n in d.nodes if n[0] == "table")
    total_words = sum(len(words(n[1])) for n in d.nodes if n[0] == "p")

    return {
        "url": final,
        "title": " ".join(d.title.split()),
        "h1": h1,
        "words": total_words,
        "subheadings": len(subs),
        "question_headings": len(q_headings),
        "question_heading_examples": q_headings[:5],
        "lead_words": lead_words,
        "answer_sentence": answer_sentence,
        "answer_sentence_words": len(words(answer_sentence)),
        "lists": lists,
        "tables": tables,
        "longest_section": longest,
        "schema_types": sorted(set(d.schema_types)),
        "faq_questions": len(d.faq_questions),
        "speakable": d.has_speakable,
    }


def read_source(src, timeout=30):
    if not src.startswith(("http://", "https://")):
        with open(src, "rb") as f:
            return f.read().decode("utf-8", "replace"), src
    req = urllib.request.Request(src, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if (r.headers.get("Content-Encoding") or "") == "gzip" or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return raw.decode(r.headers.get_content_charset() or "utf-8", "replace"), r.geturl()


ANSWER_SCHEMA = {"FAQPage", "QAPage", "HowTo", "Article", "NewsArticle",
                 "BlogPosting", "TechArticle", "Recipe"}


def score(p):
    """0-100, higher = easier for an engine to lift an answer from."""
    s = 0
    # An answer that arrives early is the single biggest factor.
    if p["answer_sentence"]:
        s += 30 if p["lead_words"] <= ANSWER_WINDOW else 18
    if p["h1"]:
        s += 5
    # Question-phrased headings are the passages engines match against.
    if p["question_headings"] >= 3:
        s += 20
    elif p["question_headings"] >= 1:
        s += 12
    # Something structurally extractable.
    if p["lists"] >= 3:
        s += 15
    elif p["lists"] >= 1:
        s += 8
    if p["tables"] >= 1:
        s += 8
    # Passage granularity.
    if p["subheadings"] >= 3 and p["longest_section"] <= LONG_SECTION:
        s += 12
    elif p["subheadings"] >= 1:
        s += 6
    # Schema that declares what the page is.
    if any(t in ANSWER_SCHEMA for t in p["schema_types"]):
        s += 10
    if p["faq_questions"]:
        s = min(100, s + 5)
    return max(0, min(100, s))


def fixes(p):
    out = []
    if not p["answer_sentence"]:
        out.append("No liftable answer sentence in the opening — lead with one direct, "
                   "self-contained sentence under %d words." % ANSWER_SENTENCE_MAX)
    elif p["lead_words"] > ANSWER_WINDOW:
        out.append("The answer starts %d words in — move a direct answer into the first %d."
                   % (p["lead_words"], ANSWER_WINDOW))
    if not p["h1"]:
        out.append("No H1 — engines use it to decide what the page is about.")
    if p["question_headings"] == 0 and p["subheadings"]:
        out.append("None of the %d subheadings are phrased as questions — rewrite the ones "
                   "that answer something into question form." % p["subheadings"])
    elif p["question_headings"] < 3 and p["subheadings"] >= 4:
        out.append("Only %d of %d subheadings are questions — more Q-form headings means more "
                   "matchable passages." % (p["question_headings"], p["subheadings"]))
    if p["lists"] == 0 and p["tables"] == 0:
        out.append("Nothing structurally extractable — add a list or a comparison table.")
    if p["longest_section"] > LONG_SECTION:
        out.append("Longest section runs %d words with no subheading — break it up so a "
                   "passage can be quoted in isolation." % p["longest_section"])
    if p["subheadings"] == 0:
        out.append("No subheadings at all — the page is one undifferentiated passage.")
    if not any(t in ANSWER_SCHEMA for t in p["schema_types"]):
        out.append("No answer-bearing schema — add FAQPage, HowTo, QAPage or Article.")
    if p["question_headings"] >= 2 and not p["faq_questions"]:
        out.append("You have %d question headings but no FAQPage schema marking them up."
                   % p["question_headings"])
    return out


def bar(v, width=20):
    f = max(0, min(width, int(round(width * v / 100.0))))
    return "█" * f + "·" * (width - f)


def grade(v):
    return "strong" if v >= 75 else "workable" if v >= 50 else "weak" if v >= 30 else "invisible"


def render(pages, args):
    pages = sorted(pages, key=lambda p: p["score"])
    print()
    print("Answer Block Audit")
    print("=" * 74)
    avg = sum(p["score"] for p in pages) / float(len(pages))
    print("  %d pages · mean answer-readiness %.0f/100" % (len(pages), avg))
    print()
    for p in pages[: args.limit]:
        print("  %3d %s  %s" % (p["score"], bar(p["score"]), grade(p["score"])))
        print("      %s" % p["url"])
        plural = lambda n, w: "%d %s%s" % (n, w, "" if n == 1 else "s")
        bits = ["%dw" % p["words"], plural(p["subheadings"], "subhead"),
                plural(p["question_headings"], "Q-head"), plural(p["lists"], "list item"),
                plural(p["tables"], "table")]
        if p["schema_types"]:
            bits.append("schema: " + ", ".join(p["schema_types"][:3]))
        print("      %s" % " · ".join(bits))
        if p["answer_sentence"]:
            print("      answer @ %dw: \"%s\"" % (p["lead_words"], p["answer_sentence"][:96]))
        for f in fixes(p):
            print("        + %s" % f)
        print()
    if len(pages) > args.limit:
        print("  … %d more. Raise --limit." % (len(pages) - args.limit))


def selftest():
    checks = []
    def ok(l, c):
        checks.append((l, bool(c)))

    ok("question mark detected", is_question("Does it ship free?"))
    ok("question-word heading detected", is_question("How long does a filter last"))
    ok("label heading is not a question", not is_question("Filter maintenance"))

    good = """<html><head><title>T</title><script type="application/ld+json">
      {"@type":"FAQPage","mainEntity":[{"@type":"Question","name":"How long?"}]}</script></head>
      <body><nav>CHROME</nav><h1>How long does a water filter last?</h1>
      <p>A hollow fibre filter lasts about 1,500 litres in clean water.</p>
      <h2>What shortens filter life?</h2><p>Silt and freezing.</p>
      <h2>How do you extend it?</h2><ul><li>Backflush</li><li>Pre-filter</li><li>Store dry</li></ul>
      <h2>When should you replace it?</h2><table><tr><td>x</td></tr></table>
      <footer>FOOT</footer></body></html>"""
    g = parse_string(good)
    ok("chrome excluded from analysis", "CHROME" not in g["answer_sentence"])
    ok("h1 captured", g["h1"].startswith("How long"))
    ok("answer sentence found early", g["answer_sentence"] and g["lead_words"] <= ANSWER_WINDOW)
    ok("question headings counted", g["question_headings"] == 3)
    ok("list items counted", g["lists"] == 3)
    ok("table counted", g["tables"] == 1)
    ok("faq schema detected", g["faq_questions"] == 1)
    ok("good page scores strong", score(g) >= 75)

    bad = "<html><body><h1>Filters</h1>" + ("<p>" + ("word " * 200) + "</p>") * 3 + "</body></html>"
    b = parse_string(bad)
    ok("wall of prose scores low", score(b) < 50)
    ok("long section flagged", b["longest_section"] > LONG_SECTION)
    f = fixes(b)
    ok("missing structure is called out", any("extractable" in x for x in f))
    ok("missing schema is called out", any("schema" in x for x in f))
    ok("no subheadings called out", any("subheading" in x.lower() for x in f))
    ok("good page beats bad page", score(g) > score(b))
    ok("score stays in range", 0 <= score(g) <= 100 and 0 <= score(b) <= 100)

    late = ("<html><body><h1>Q</h1>" + "<p>" + ("filler " * 150) + "</p>"
            + "<p>The answer is forty two.</p></body></html>")
    lt = parse_string(late)
    ok("late answer is penalised", any("first %d" % ANSWER_WINDOW in x for x in fixes(lt)))

    w = max(len(c[0]) for c in checks) + 2
    for l, passed in checks:
        print("  %s %s" % ("PASS" if passed else "FAIL", l.ljust(w)))
    bad_ = [c[0] for c in checks if not c[1]]
    print("\n%d/%d passed" % (len(checks) - len(bad_), len(checks)))
    return 1 if bad_ else 0


def parse_string(html):
    """Audit an HTML string directly — used by the selftest."""
    import tempfile
    import os
    fd, path = tempfile.mkstemp(suffix=".html")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(html)
        return audit(path)
    finally:
        os.unlink(path)


def main():
    ap = argparse.ArgumentParser(
        prog="answer_block_auditor.py",
        description="Score pages on whether an AI can lift an answer off them. Zero dependencies.")
    ap.add_argument("pages", nargs="*", help="page URLs or local .html paths")
    ap.add_argument("--urls", metavar="FILE", help="text file of URLs, one per line")
    ap.add_argument("--sitemap", help="sitemap.xml to pull URLs from")
    ap.add_argument("--limit", type=int, default=20, help="pages to detail, weakest first")
    ap.add_argument("--max-pages", type=int, default=200, dest="max_pages")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--selftest", action="store_true", help="verify the analysis internals")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())

    srcs = list(args.pages)
    for pat in list(srcs):
        if not pat.startswith(("http://", "https://")) and any(c in pat for c in "*?["):
            srcs.remove(pat)
            srcs.extend(sorted(glob.glob(pat)))
    if args.urls:
        with open(args.urls) as f:
            srcs += [l.strip() for l in f if l.strip() and not l.startswith("#")]
    if args.sitemap:
        xml, _ = read_source(args.sitemap)
        srcs += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml, re.I)
    srcs = list(dict.fromkeys(srcs))[: args.max_pages]
    if not srcs:
        ap.error("give page URLs/paths, --urls FILE, or --sitemap URL")

    verbose = not args.quiet and not args.json
    if verbose:
        print("Auditing %d pages..." % len(srcs), file=sys.stderr)

    pages, failed = [], []
    def job(s):
        if args.delay:
            time.sleep(args.delay)
        return audit(s)
    with futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for src, fut in zip(srcs, [ex.submit(job, s) for s in srcs]):
            try:
                pages.append(fut.result())
            except Exception as e:
                failed.append((src, str(e)))
    if failed and verbose:
        for s, e in failed[:5]:
            print("  ! %s — %s" % (s, e), file=sys.stderr)
    if not pages:
        raise SystemExit("No pages could be audited.")
    for p in pages:
        p["score"] = score(p)
        p["fixes"] = fixes(p)

    if args.json:
        json.dump({"pages": sorted(pages, key=lambda p: p["score"])}, sys.stdout, indent=2)
        print()
    else:
        render(pages, args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
