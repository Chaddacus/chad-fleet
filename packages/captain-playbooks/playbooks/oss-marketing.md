---
slug: oss-marketing
title: OSS Developer Tool Marketing Playbook
domain: developer-tools
applies_to:
  - oss-repo-launch
  - github-release
  - technical-content
last_updated: 2026-04-29
---

# OSS Developer Tool Marketing Playbook

## Summary

Covers the marketing lifecycle of an open-source developer tool: pre-launch polish, launch
distribution across developer channels, and sustained growth through community, documentation,
and sponsor funnel development. Fires when a new OSS project is preparing for a public launch
or an existing repo needs a distribution push.

## When to consult

- A new OSS repo is being prepared for a GitHub public launch or a Show HN post.
- A new major release is ready but distribution has been limited to existing followers.
- Stars have plateaued and no active community funnel is in place.
- A sponsor or commercial tier is being added to an existing OSS project.
- The README has not been updated in over 3 months and issues are accumulating without triage.
- A repo has 50+ stars but zero contributors outside the original author.

## Recommendations

### Pre-launch (1-4 weeks before first public post)

1. **Rewrite the README with a hero example in the first 10 lines.**

   The first thing a developer sees must be a working code snippet that shows the "after" state —
   the thing the tool enables that was painful or impossible before. No "what is X" section
   before the example. The project description (one sentence) and the hero example are the only
   content above the fold.

   Test it cold: show the README to 3 developers who are unfamiliar with the project and ask
   "what does this tool do?" If they cannot answer in 20 seconds, the README fails.

   Measurable outcome: time from README load to "I understand what this does" is under 20
   seconds, validated by 3 cold readers.

2. **Record a 60-90 second demo GIF or video.**

   Use Terminalizer or Asciinema for CLI tools; Loom for UI tools. Show the complete workflow:
   install, configure, run, see result. Do not narrate over a slide deck — show the actual
   terminal or browser.

   A README with a demo GIF gets 3x more stars in the first 48 hours than text-only READMEs.
   Host on GitHub directly or embed via CDN. The GIF must load in under 3 seconds.

   Measurable outcome: demo GIF exists, is embedded in the README above the fold, and loads
   in under 3 seconds on a 50 Mbps connection.

3. **Set your license deliberately and before launch.**

   MIT: maximum adoption, minimum friction. Apache 2.0: explicit patent grant, still permissive.
   BSL-1.1 or SSPL: protects a commercial path but introduces friction with enterprise legal teams.

   Do not launch without a LICENSE file. GitHub flags unlicensed repos and enterprise users
   cannot legally use unlicensed code. For most indie dev tools, MIT is the correct default
   unless there is a specific commercial reason to deviate.

   Measurable outcome: LICENSE file in the root of the repo before the first public post.

4. **Write a CONTRIBUTING.md and open at least 3 "good first issue" labeled issues.**

   CONTRIBUTING.md must cover: dev environment setup, how to run tests, PR format, and issue
   triage process. Issues labeled `good first issue` should be scoped to under 2 hours of work.

   Contributors arriving from a Show HN post need an immediate path to participation. Without
   these two assets, curious developers move on within 3 minutes.

   Measurable outcome: the path from "I want to contribute" to "first PR submitted" requires
   reading at most 2 files.

5. **Verify CI passes, test coverage badge is visible, and installation works on a fresh machine.**

   Run the install instructions on a machine that does not have your dev environment
   pre-configured. If `pip install mytool` fails on a fresh Python install, the launch fails.

   A repo without a passing CI badge signals untested code, which blocks enterprise evaluation
   immediately.

   Measurable outcome: zero installation errors on clean macOS and Linux environments, verified
   before the first public post.

### Launch (day 0 and week 1)

6. **Post to Hacker News as "Show HN: [Tool Name] — [one sentence description]".**

   Show HN rules: describe what you built, not the category. First comment should be a brief
   technical backstory and an honest assessment of what it does not do yet. Reply to every
   comment in the first 6 hours — HN's ranking algorithm weights early comment velocity.

   Post between 9 AM and noon Pacific time on a Tuesday, Wednesday, or Thursday. Do not post
   on Friday or during a US holiday week. Traffic is low and comment velocity craters.

   Measurable outcome: 10+ comments in the first 2 hours; front-page placement if the problem
   is widely relevant to HN's technical audience.

7. **Cross-post to one specific subreddit for your tool's domain.**

   r/Python for Python tools. r/devops for infrastructure tools. r/webdev for frontend tools.
   Do not post to r/programming or r/coding on the same day as your HN post — these audiences
   overlap heavily and simultaneous cross-posting reads as spam.

   Read the subreddit's self-promo rules before posting.

   Measurable outcome: a single subreddit post on a different day from the HN post, with zero
   rules violations.

8. **Post a LinkedIn technical post with the demo GIF embedded.**

   LinkedIn's algorithm favors native GIF embeds over link posts. Write 3-5 lines of context
   before the GIF — explain the problem it solves in terms a non-specialist can follow. Tag
   only people who were directly involved (co-authors, contributors).

   Measurable outcome: post reaches at least 500 impressions within 48 hours.

9. **Announce on Twitter/Bluesky with a thread.**

   Tweet 1: problem + solution in 1 sentence + GIF.
   Tweets 2-5: key features or interesting design decisions.
   Final tweet: repo link + Show HN link.

   Reply to every reply in the first 24 hours.

   Measurable outcome: at least 20 reposts and 50 likes in the first 48 hours.

10. **Email your existing list or Substack on launch day.**

    Keep it short: what you built, why, how to try it, direct repo link. No 1,500-word essay.
    If the list is under 200 subscribers, 15-20% open rate and 5-8% click rate is a healthy
    baseline.

    Measurable outcome: at least 50 repo visits attributable to the email, confirmed via
    GitHub Insights > Traffic.

### Sustained growth (weeks 2-12 and ongoing)

11. **Triage every new issue within 48 hours.**

    Label it (`bug`, `enhancement`, `question`, `good first issue`). Reply even if the answer
    is "looking into this." Unresponded issues kill contributor conversion.

    Projects where maintainers respond within 48 hours see 2-3x more repeat contributors than
    projects where first response takes over a week.

    Measurable outcome: no open issue older than 48 hours without a maintainer label or comment.

12. **Write one technical blog post per month tied to the tool.**

    The post must teach something concrete: a pattern the tool enables, a benchmark comparison,
    or a real-world integration example. Publish to dev.to, Hashnode, or your own blog. Distribute
    on HN, LinkedIn, and Twitter/Bluesky.

    This is the Caleb Porzio sponsorware loop: useful content generates awareness, awareness
    generates stars, stars generate sponsor interest.

    Measurable outcome: 1 post per month published and distributed across all 3 channels.

13. **Set up GitHub Sponsors or Open Collective at 100 stars.**

    Write a clear sponsors README section. Tiers: $5/month individual, $25/month company user,
    $250/month commercial sponsor with logo. Do not gate features. Transparent sustainability
    framing works better than perks theater for developer audiences.

    Reference Sindre Sorhus's sponsor page: no promises, no roadmap commitments in exchange for
    money, just honest "this funds continued maintenance."

    Measurable outcome: sponsor page live before the 200-star milestone.

14. **Run a v1.0 milestone release with a changelog.**

    Even if the project is already used in production, a deliberate v1.0 GitHub Release with a
    CHANGELOG resets the distribution clock. Post the release to all channels as if it is a
    relaunch. Tag it with proper semver.

    Measurable outcome: GitHub Release exists with a CHANGELOG section covering all changes
    since first public commit.

## Anti-patterns

- Writing a README that starts with "What is X?" before showing an example. Developers read
  READMEs to determine in 15 seconds if the tool is relevant. Abstract descriptions waste
  that window.
- Posting to HN on a Friday or during a major US holiday. Traffic is low and comment velocity
  craters fast enough that the post dies before the audience sees it.
- Tagging influencers on Twitter to ask them to share your repo. This reads as spam and most
  decline. Build a real relationship first or let the work earn the attention.
- Opening issues for every feature idea you have yourself. It inflates the issue count,
  confuses contributors about what is actually planned, and signals an unmaintained project.
- Deleting negative HN comments or Reddit posts. Transparency is the only credible response
  to criticism in a dev-tool community. Defensive deletion accelerates reputational damage.
- Using the GitHub star count as your primary success metric. Stars are vanity unless they
  convert to contributors, sponsors, or users. Track clones and unique visitors instead.
- Launching on GitHub without CI. A repo without a passing CI badge signals untested code.
  Enterprise teams stop evaluating immediately.

## Decision rubric

| Situation | Recommended action |
|---|---|
| Under 50 stars, limited traction | Focus on one distribution channel at a time; don't spray |
| 100-500 stars, no contributors | Add 5+ `good first issue` tickets; post a call for contributors |
| Stars growing but no sponsors after 500 | Add funding.yml; write a post about OSS sustainability |
| Multiple open PRs with no review >2 weeks | Block new feature work until PRs are reviewed |
| Community requesting a feature you won't build | Label `help wanted`; explain why it is not on your roadmap |

## Sources

- [Caleb Porzio — Sponsorware](https://calebporzio.com/i-just-hit-dollar-100000yr-on-github-sponsors-heres-how-i-did-it)
  — releasing to sponsors first, then OSS
- [Sindre Sorhus — OSS sustainability](https://github.com/sindresorhus/sindresorhus/blob/main/answers.md)
  — opinionated stance on OSS maintenance and sponsor expectations
- [Hacker News Show HN guidelines](https://news.ycombinator.com/showhn.html)
- [GitHub Insights](https://docs.github.com/en/repositories/viewing-activity-and-data-for-your-repository/viewing-traffic-to-a-repository)
  — traffic and clone analytics
- [dev.to](https://dev.to/) — developer blog distribution platform
- [Terminalizer](https://www.terminalizer.com/) — CLI demo recording tool
