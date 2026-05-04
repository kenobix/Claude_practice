# Kenshin Design System

## Overview

**Kenshin** is the personal brand of an IT engineer and business consultant who bridges the gap between technology and organizational strategy. The work spans requirements definition, business process improvement, system architecture, and change management — delivered with engineering rigour and strategic clarity.

**Brand Positioning:** Technical precision meets strategic thinking. Kenshin operates at the intersection of IT and business — not purely a developer, not purely a consultant, but a systems thinker who can move fluidly between both worlds.

**Audience:** Mid-to-large enterprise clients, project sponsors, CTOs, and business stakeholders. Decision-makers who appreciate competence and clarity over flash.

---

## Sources

This design system was generated from scratch based on the brand brief:
- No codebase or Figma file provided
- No existing brand assets provided
- All visual foundations, tokens, and components are original, designed to match the brand brief

**To improve fidelity:** Attach a codebase, Figma link, or existing brand materials via the Import menu and ask for an update.

---

## CONTENT FUNDAMENTALS

### Voice & Tone
- **Confident, not arrogant.** Statements are declarative and grounded. Avoid hedging language ("maybe", "sort of", "I think").
- **Technical yet accessible.** Uses precise terminology without jargon overload. Complex ideas are explained with analogies and structure.
- **Strategic framing.** Problems are contextualized before solutions are presented. "Why" comes before "how."
- **Concise.** No padding. No filler sentences. Every word earns its place.

### Perspective
- **First-person singular** ("I" not "we") — this is a personal brand, not an agency.
- Speaks to the client as **"you"** — direct, engaged, not distant.
- Occasionally uses **"we"** when describing collaborative work on a project together.

### Casing
- Headlines: **Sentence case** (not title case). "How I approach requirements" not "How I Approach Requirements."
- UI labels: **Sentence case** throughout.
- No all-caps except for acronyms (IT, CTO, KPI, MVP, etc.)

### Emoji
- **Not used** in professional documents, decks, or the portfolio.
- May appear sparingly in casual social contexts only.

### Copy Examples
> "I work at the intersection of technology and business strategy — translating complex requirements into systems that actually get built."

> "The challenge isn't always technical. Often it's organisational. I help teams bridge that gap."

> "Let's define the problem before we talk about the solution."

---

## VISUAL FOUNDATIONS

### Color Philosophy
Dark-anchored palette. Deep backgrounds create focus and professionalism. A single electric accent (indigo/violet) provides direction and hierarchy without distraction. Subtle warm-neutral grays prevent the coldness of pure black/white.

See `colors_and_type.css` for all tokens.

**Primary palette:**
- Background: near-black `#0D0D12` with subtle warm undertone
- Surface: `#16161F` (cards, panels)
- Border: `#2A2A38` (hairlines, dividers)
- Foreground primary: `#F0EEF8` (near-white, slightly warm)
- Foreground secondary: `#8B8A9E` (muted, secondary text)
- Accent: `#6C63FF` (electric indigo) — used sparingly for CTAs, active states, highlights
- Accent warm: `#A78BFA` (violet) — gradient partner, hover states

### Typography
- **Display/Headlines:** Syne — geometric, modern, slightly unusual. Strong personality without being decorative.
- **Body/UI:** DM Sans — clean, approachable, excellent at small sizes. Not as overused as Inter.
- **Code/Technical:** JetBrains Mono — developer credibility, precise.

Type scale is modular (1.25 ratio). Generous line heights for readability.

### Backgrounds
- Predominantly **dark solid** (`#0D0D12`, `#16161F`)
- Subtle **noise texture overlay** at low opacity for depth
- Occasional **grid dot pattern** as a background motif (engineering precision visual metaphor)
- **No gradients as backgrounds** — gradients reserved for accent elements and CTAs only
- Full-bleed section dividers use border lines, not color fills

### Spacing & Layout
- Base unit: **8px**
- Scale: 4, 8, 12, 16, 24, 32, 48, 64, 96, 128
- Max content width: **1200px**
- Grid: 12-column with 24px gutters
- Section padding: 96px vertical on desktop, 64px on tablet

### Animation
- **Subtle and purposeful.** Not decorative.
- Fade + slight upward translate on scroll reveal (duration: 400ms, ease: cubic-bezier(0.4, 0, 0.2, 1))
- Hover states: smooth color/opacity transitions (150ms ease)
- No bounces, no spring physics, no playful easing
- Page transitions: simple fade (200ms)

### Hover & Press States
- Links/buttons: color shift toward accent, 150ms ease
- Cards: subtle border lightening + very slight lift (translateY: -2px)
- Press state: slight opacity reduction (0.85) + no lift

### Borders & Radius
- **Minimal rounding:** `4px` for small elements (chips, tags), `8px` for cards and panels
- **No large border radii** — avoid pill/rounded aesthetic
- Hairline borders: 1px `#2A2A38`
- Focus rings: 2px solid accent with 2px offset

### Cards
- Background: `#16161F`
- Border: 1px `#2A2A38`
- Border radius: `8px`
- Shadow: `0 1px 3px rgba(0,0,0,0.4), 0 0 0 1px rgba(255,255,255,0.04)`
- Hover: border color lightens to `#3D3D52`, translateY(-2px)

### Shadows & Elevation
- Level 0: no shadow (flat, inline)
- Level 1: `0 1px 3px rgba(0,0,0,0.4)` — cards
- Level 2: `0 4px 16px rgba(0,0,0,0.5)` — modals, dropdowns
- Level 3: `0 8px 32px rgba(0,0,0,0.6)` — overlays

### Imagery
- **Monochromatic or dark-tinted** photography if used
- Architecture, abstract geometry, circuit/grid motifs align with the brand
- No stock photo smiling-people imagery
- Diagrams and charts use the brand palette

### Iconography
See ICONOGRAPHY section below.

### Corner Radius Summary
| Element | Radius |
|---|---|
| Button | 6px |
| Card | 8px |
| Input | 6px |
| Tag/badge | 4px |
| Avatar | 50% (circle) |
| Modal | 10px |

---

## ICONOGRAPHY

### Approach
- **Stroke-based icons only** — no filled icons. Consistent 1.5px stroke weight at 20px size.
- Clean, geometric, minimal — matching the brand's precision aesthetic.
- **Lucide Icons** (CDN: `https://unpkg.com/lucide@latest`) — used via `<i data-lucide="...">` or React component.
- No emoji used as icons in any professional context.
- No icon fonts (e.g. FontAwesome) — SVG-based only for crispness.

### Usage
- Navigation: 20px, stroke `#8B8A9E`, active stroke `#F0EEF8`
- CTAs and buttons: 16px inline with label
- Feature blocks: 24px accent-colored
- Never use icons as decoration without semantic meaning

---

## Files & Index

| Path | Description |
|---|---|
| `README.md` | This file — brand overview and visual foundations |
| `SKILL.md` | Agent skill definition for Claude Code |
| `colors_and_type.css` | All CSS custom properties (colors, type, spacing, shadows) |
| `assets/logo.svg` | Full wordmark — dark background |
| `assets/logo-light.svg` | Full wordmark — light background |
| `assets/logo-mark.svg` | K mark only |
| `preview/` | 15 HTML design system cards (see Design System tab) |
| `ui_kits/README.md` | UI kit overview |
| `ui_kits/portfolio/` | Portfolio website UI kit (React, 6 components) |
| `ui_kits/deck/` | Proposal deck template (8 slides, deck-stage.js) |
| `ui_kits/onepager/` | Consulting one-pager (single HTML file) |

---

## UI Kits

### Portfolio Website (`ui_kits/portfolio/index.html`)
Full interactive React portfolio. Sections: hero, services (6 cards), projects (tabbed case studies), about, contact form. Smooth scroll nav with active section tracking.
**Components:** `Nav.jsx`, `Hero.jsx`, `Services.jsx`, `Projects.jsx`, `About.jsx`, `Contact.jsx`, `App.jsx`

### Proposal Deck (`ui_kits/deck/index.html`)
8-slide proposal deck at 1920×1080. Keyboard navigable (← →). Slides: title, agenda, problem, approach, timeline, quote, investment, CTA.
**Template:** `deck-stage.js` handles scaling, keyboard nav, print-to-PDF.

### Consulting One-Pager (`ui_kits/onepager/index.html`)
Single-page document. Sections: header with contact, services list, expertise tags, track record stats, process steps, quote, footer CTA.

---

## Design System Cards (preview/)

| Card | Group |
|---|---|
| Background & Border Colors | Colors |
| Foreground Colors | Colors |
| Accent Colors | Colors |
| Status Colors | Colors |
| Display Type — Syne | Type |
| Body Type — DM Sans | Type |
| Mono Type — JetBrains Mono | Type |
| Spacing Scale | Spacing |
| Shadows & Radius | Spacing |
| Buttons | Components |
| Form Inputs | Components |
| Cards | Components |
| Badges & Tags | Components |
| Logo | Brand |
| Iconography | Brand |
