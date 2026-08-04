# Taste Skill upstream notice

VELIA Design Taste is a curated adaptation of selected ideas from:

- Repository: `https://github.com/Leonxlnx/taste-skill`
- Upstream commit reviewed: `e988add20dab0fa97d7a76781c48961c8184288e`
- License: MIT; see `LICENSE` in this directory.

Primary upstream material reviewed:

- `skills/taste-skill/SKILL.md`
- `skills/redesign-skill/SKILL.md`
- `skills/gpt-tasteskill/SKILL.md`
- `skills/imagegen-frontend-mobile/SKILL.md`

VELIA does not vendor the upstream media assets, examples, research corpus, or full prompt collection. The runtime adaptation is intentionally compact to keep model context and user cost bounded.

Notable VELIA changes:

- all guidance is contextual rather than globally mandatory;
- forced GSAP, simulated randomness, library bans, and image-generation-only behavior were removed;
- existing project stack and design system take priority;
- Android, iOS, and cross-platform coding rules are separated;
- the skill adds no extra model call;
- backend-only requests bypass the design layer;
- audit, accessibility, dependency verification, responsive behavior, and complete UI states are retained.
