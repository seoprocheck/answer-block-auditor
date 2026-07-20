<h1 align="center">answer-block-auditor</h1>

<p align="center">
  <strong>Can an AI lift an answer off your page — or is it buried 600 words down?</strong>
</p>

<p align="center">
  <img alt="Python 3.8+" src="https://img.shields.io/badge/python-3.8%2B-blue">
  <img alt="Zero dependencies" src="https://img.shields.io/badge/dependencies-0-brightgreen">
  <img alt="No API key" src="https://img.shields.io/badge/API%20key-none-orange">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-black">
</p>

---

Generative engines quote **passages**, not pages. A page can rank perfectly well and still be useless to them: the answer arrives after 600 words of preamble, the headings are labels rather than questions, there is no list or table to extract, and nothing in the markup says what kind of page it is.

This audits the thing those engines actually need — whether an answer is reachable — and tells you what to change.

It scores five signals:

- **Answer proximity.** Where the first short, self-contained sentence appears. Crucially it keeps scanning past the window, so it can tell "there is no answer here" apart from "the answer is buried at word 340" — different problems, different fixes.
- **Question-form headings.** The passages engines match a query against. A page of labels gives them nothing to align to.
- **Extractable structure.** Lists and tables get quoted verbatim far more often than prose.
- **Passage granularity.** A 900-word run under one heading cannot be quoted in isolation.
- **Answer-bearing schema.** `FAQPage`, `HowTo`, `QAPage`, `Article` — and whether your question headings are actually marked up.

```
Answer Block Audit
==========================================================================
  3 pages · mean answer-readiness 65/100

   35 ███████·············  weak
      /filter-maintenance/
      548w · 0 subheads · 0 Q-heads · 0 list items · 0 tables
        + Nothing structurally extractable — add a list or a comparison table.
        + Longest section runs 546 words with no subheading — break it up so a passage
          can be quoted in isolation.
        + No answer-bearing schema — add FAQPage, HowTo, QAPage or Article.

  100 ████████████████████  strong
      /how-long-does-a-filter-last/
      71w · 3 subheads · 3 Q-heads · 4 list items · 1 table · schema: FAQPage, Question
      answer @ 0w: "A hollow fibre hiking filter lasts roughly 1,500 litres in clean water…"
```

## Usage

```bash
python3 answer_block_auditor.py --sitemap https://example.com/sitemap.xml
python3 answer_block_auditor.py --urls urls.txt --limit 40
python3 answer_block_auditor.py https://example.com/faq/ https://example.com/guide/
python3 answer_block_auditor.py --sitemap https://example.com/sitemap.xml --json > answers.json
```

Pages are listed weakest first, because that is the work queue.

Try it instantly on the bundled fixture — a strong page, a middling one and a wall of prose:

```bash
python3 answer_block_auditor.py "fixtures/*.html" --delay 0
```

Verify the internals — question detection, chrome exclusion, scoring order and the buried-answer case:

```bash
python3 answer_block_auditor.py --selftest
```

## Reading the output honestly

**Chrome is detected by class, not just by tag.** Most CMS themes build menus, related-post rails and share bars out of plain `<div>`s rather than `<nav>`. Tested against a live WordPress site, tag-only detection counted **341 "content" links and 261 list items on every page** — identical figures across pages, which is the signature of template markup. Class/id/role detection brings that to 37 links and 3–12 list items. `entry-header` and `post-header` are deliberately *not* treated as chrome, because that is where most themes put the H1.


**The score is a triage order, not a grade.** It is a weighted checklist, not a model of any real engine. Nobody outside those companies knows their retrieval weights. Use it to rank your pages against each other and to find the obvious structural failures.

**A high score does not mean you will be cited.** Being extractable is necessary, not sufficient — the answer still has to be correct, and the page still has to be found. This measures the mechanics only.

**Short is not always right.** The "answer sentence" check rewards a direct opening. On pages where the honest answer genuinely is "it depends", a forced one-liner is worse writing. Take the recommendation as a prompt to check, not an order.

**Question headings can be overdone.** Rewriting every heading into a question reads like a listicle. The advice fires when few or none are questions; it is not asking for all of them.

**Pages are read as served.** Client-rendered content is invisible here — which is frequently true for the crawlers too, so a surprisingly low score is worth investigating rather than dismissing.

## License

MIT © [SEO Pro Check](https://seoprocheck.com) · built by [@seoprocheck](https://github.com/seoprocheck).
