---
slug: index
title: Captain Playbooks Index
last_updated: 2026-04-29
---

# Captain Playbooks Index

Canonical list of all playbooks in this package. Captain-core uses this file for playbook discovery. Each entry includes the filename, domain, and a one-line summary.

## Playbooks

| File | Domain | Summary |
|---|---|---|
| [indie-author-launch.md](indie-author-launch.md) | `author-publishing` | Pre-launch, launch week, and post-launch tactics for indie/self-published books on Amazon KDP, including ARC programs, metadata strategy, and ranking velocity. |
| [oss-marketing.md](oss-marketing.md) | `developer-tools` | Marketing lifecycle for open-source developer tools: README and demo polish, Show HN and Reddit launch, sustained community growth, and sponsor funnel. |
| [federal-contracting.md](federal-contracting.md) | `gov-contracting` | Registration prerequisites, SDVOSB certification, capability statement, SAM.gov opportunity sourcing, SBIR/STTR Phase I, and FedRAMP 20x path for an SDVOSB AI tooling company. |
| [linkedin-algorithm.md](linkedin-algorithm.md) | `social-distribution` | 2026 LinkedIn feed algorithm mechanics: dwell-time optimization, hook construction, post shape by content type, comment amplification, and anti-patterns. |
| [b2b-saas-gtm.md](b2b-saas-gtm.md) | `saas-commercial` | Go-to-market fundamentals for indie B2B SaaS at $50-200K ARR: ICP definition, pricing tiers, onboarding-to-conversion mechanics, channel selection, and churn prevention. |
| [sdvosb-paperwork.md](sdvosb-paperwork.md) | `vet-business-ops` | Business entity setup, VA-verified SDVOSB certification, SAM.gov registration, GA business exemption, VR&E Self-Employment Track, CHAMPVA, and S-corp election for a Georgia SDVOSB. |

## Structure

Each playbook follows the same schema:

- `slug` — machine-readable identifier matching the filename (without `.md`)
- `domain` — single category tag used for filtering
- `applies_to` — list of fleet app modes or contexts that trigger this playbook
- `last_updated` — ISO date of last content update

Sections in every playbook: Summary, When to consult, Recommendations, Anti-patterns, Decision rubric, Sources.
