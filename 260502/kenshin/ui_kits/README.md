# Kenshin Design System — UI Kit README

## Portfolio Website (`ui_kits/portfolio/`)

A full-page React portfolio site for Kenshin. Interactive click-through with smooth scroll navigation between sections.

### Components
- `Nav.jsx` — Fixed top nav with scroll-aware background, active section highlighting
- `Hero.jsx` — Full-viewport hero with gradient headline, stats, and CTA row
- `Services.jsx` — 6-card service grid with hover lift effects
- `Projects.jsx` — Tabbed case study detail view with outcomes list
- `About.jsx` — Two-column bio with skills tags and availability card
- `Contact.jsx` — Contact form with success state
- `App.jsx` — Root with IntersectionObserver-based active section tracking + footer

### Design width: 1200px max-width, responsive

---

## Proposal Deck (`ui_kits/deck/`)

An 8-slide proposal deck template using `deck-stage.js` (1920×1080, keyboard navigable).

### Slides
1. Title — hero display with gradient
2. Agenda — numbered list
3. Problem statement — two-column with problem cards
4. Approach — 3-phase Discover/Design/Deliver
5. Timeline — 4-phase roadmap with color-coded phases
6. Big quote — full-screen typographic statement
7. Investment — 3-tier pricing cards
8. CTA / Next steps — closing with action items

---

## Consulting One-Pager (`ui_kits/onepager/`)

A single-page A4-style consulting overview document. All contained in `index.html`.

### Sections
- Header band — logo, tagline, headline, contact details
- Services list — 4 services with icons
- Expertise tags
- Track record stats (2×2 grid)
- How I work — 3-step process
- Core quote block
- Footer with CTA

### Design width: 900px max-width
