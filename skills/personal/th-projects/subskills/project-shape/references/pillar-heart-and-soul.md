# Heart and Soul — Doctrine Layer Guide

## Purpose

The doctrine layer answers **WHY**: why this project exists, what it believes, what it refuses to be, and what principles constrain every decision. Doctrine is not aspirational — it's constitutional. It defines the magnetic field that aligns all other project artifacts.

## Recommended Structure

```
about/heart-and-soul/
├── README.md          # Reading order, one-line per file, selection guide
├── vision.md          # Core thesis, what this IS and IS NOT, performance goals
├── architecture.md    # Structural philosophy (not implementation — that's RFCs)
├── v1.md              # Scope boundary: what ships, what defers, success criteria
└── [domain].md        # One file per domain that has non-negotiable principles
```

### Core Files (every project needs these)

**vision.md** — The thesis statement. Write this first. Must answer:
- What is this project? (one paragraph)
- What is it NOT? (equally important — prevents scope creep)
- What are the non-negotiable rules? (5-7 max, numbered)
- What does success look like?

**v1.md** — The scope boundary. Must answer:
- What does v1 ship? (explicit list)
- What does v1 defer? (explicit list with rationale)
- What are the platform targets?
- What are the success criteria?

### Domain Files (add as needed)

Add a domain file when a topic has principles that code must embody:

| Common domains | Content |
|----------------|---------|
| `security.md` | Trust model, auth philosophy, capability scopes |
| `privacy.md` | Data handling principles, user consent model |
| `failure.md` | Error philosophy, degradation strategy, recovery contracts |
| `performance.md` | Performance philosophy only when it is constitutional rather than contractual |

## Writing Doctrine

### Do

- State principles as constraints ("X must never Y", "Z always takes priority over W")
- Include anti-patterns (what NOT to do is as valuable as what to do)
- Number non-negotiable rules — they become anchors for design reviews
- Keep files self-contained — each file readable independently
- Write for a reader who has never seen the codebase
- Move implementation-quality standards, review rules, testing discipline, and generic contribution hygiene into `about/craft-and-care/`

### Don't

- Include implementation details (that's RFCs/specs)
- List technologies or libraries (that's architecture docs)
- Write aspirational prose without testable constraints
- Mix doctrine with operational runbooks
- Hide execution standards inside doctrine when they belong in `craft-and-care`
- Exceed ~300 lines per file — split by domain instead

## Maturity Levels

| Level | Signal |
|-------|--------|
| Absent | No vision, principles, or scope docs |
| Nascent | README has some philosophy but no dedicated folder |
| Structured | `about/heart-and-soul/` exists with vision and scope |
| Mature | Complete doctrine, domain files, numbered rules, actively referenced by RFCs/specs |

## Evolution

Doctrine should be **slow to change** — it's constitutional. When doctrine evolves:
1. The change must be discussed and agreed (not unilateral)
2. All downstream artifacts (RFCs, specs) must be checked for alignment
3. The doctrine text is rewritten in place to state current belief in present tense; its history lives in git and the amendment's decision record, never inline
