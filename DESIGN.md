# PolyKit UI Design Contract

This document is the source of truth for product UI and frontend component decisions.

PolyKit is a dense creative tool, not a marketing site. The UI should feel calm, technical, fast, and predictable while keeping generated 3D content visually dominant.

## Non-negotiable rules

### 1. shadcn primitives are the component foundation

New shared UI must live under:

`src/shared/components/ui/`

Use the local shadcn components before writing feature-specific controls. Buttons, inputs, labels, cards, dialogs, switches, selects, badges, separators, tooltips, dropdowns, tabs, and similar primitives belong in the shared UI layer.

Feature-specific composition belongs next to the owning feature, for example `src/areas/settings/components/SettingsLayout.tsx`. Do not recreate a second shared compatibility/adapter UI directory.

Feature code must not introduce a second UI kit.

### 2. Tailwind is the styling language

Use Tailwind utilities for component and page styling. CSS files are reserved for:

- design tokens and global theme variables,
- global resets and accessibility behavior,
- app-shell utilities that cannot be expressed cleanly in Tailwind,
- canvas/WebGL specific behavior.

Do not add CSS modules, styled-components, Emotion, inline style systems, or another utility CSS framework.

### 3. Use semantic tokens, not palette colors

Feature UI should prefer semantic classes:

- `bg-background`, `text-foreground`
- `bg-card`, `text-card-foreground`
- `bg-muted`, `text-muted-foreground`
- `bg-primary`, `text-primary-foreground`
- `bg-secondary`, `text-secondary-foreground`
- `bg-destructive`, `text-destructive-foreground`
- `border-border`, `border-input`, `ring-ring`

Do not add new `zinc-*`, `neutral-*`, raw hex, rgb, rgba, hsl, or arbitrary color values inside feature UI.

Allowed exceptions:

- 3D/WebGL scene colors and material values,
- data visualization where colors encode data,
- semantic status colors such as success/warning/destructive when no existing token fits,
- brand artwork and generated asset previews.

Legacy palette classes may remain during migration, but touched UI should move toward semantic tokens.

### 4. Primary color is emphasis, not decoration

PolyKit primary is a restrained Blender-inspired blue accent. Use it for:

- the primary action on a surface,
- selected navigation state,
- focus/ring emphasis,
- progress/current-state accents.

Do not flood large surfaces with primary. Most UI should remain neutral so 3D content and outputs stay visually dominant.

Cyan is a secondary technical accent for information and auxiliary states, not a generic action color.

### 5. One control anatomy per interaction

Do not hand-build local versions of:

- buttons,
- toggles/switches,
- text inputs,
- selects,
- dialogs/modals,
- badges,
- tabs,
- tooltips,
- dropdown menus,
- toast notifications.

If a needed primitive does not exist, add a shadcn-based primitive under `src/shared/components/ui/` first, then consume it from features.

### 6. Iconography

Use `lucide-react` for product UI icons.

Do not add new handwritten inline SVG icons in feature code unless the icon is:

- a PolyKit brand mark,
- a domain-specific diagram not available in Lucide,
- part of the actual 3D/canvas visualization.

Default icon sizes:

- compact control: `size-4`
- standard action: `size-4` or `size-[18px]`
- navigation: `size-5`
- empty-state illustration: `size-8` to `size-10`

Icons inside buttons should not create their own padding.

### 7. Typography

Use the application font token (`--app-font`) for product UI. Reserve `--brand-font` (Space Grotesk) for the PolyKit wordmark.

Recommended hierarchy:

- page title: `text-2xl font-semibold tracking-tight`
- section title: `text-base font-semibold`
- card/control title: `text-sm font-medium`
- body: `text-sm`
- secondary/help text: `text-xs text-muted-foreground`
- metadata: `text-[11px] text-muted-foreground`

Avoid excessive uppercase labels and letter spacing. Uppercase is reserved for tiny metadata/category labels where scanning benefits from it.

Use monospace only for paths, IDs, logs, code, numeric technical output, and machine-readable values.

### 8. Spacing and density

PolyKit is a desktop-class creative tool. Prefer compact, consistent spacing over oversized SaaS-dashboard spacing.

Common spacing:

- control height: 32–36 px (`h-8` / `h-9`)
- compact toolbar control: 32 px (`size-8`)
- page content padding: `p-6` desktop; avoid defaulting to `p-10`
- card padding: `p-4` or `p-5`
- related control gap: `gap-2`
- section gap: `gap-6`

Use the Tailwind spacing scale. Arbitrary pixel values require a concrete layout reason.

### 9. Radius and elevation

Use the shared radius scale derived from `--radius`. Radius should communicate containment and hierarchy, not decorate every element.

- controls: `rounded-md`
- cards/panels: `rounded-lg`
- large dialogs: `rounded-xl`
- nested media and inset surfaces: equal to or slightly tighter than their parent
- `rounded-full`: reserved for icon buttons, avatars, and compact status chips

Avoid mixing `rounded-lg`, `rounded-xl`, and `rounded-2xl` randomly on adjacent surfaces. Keep nested corners optically consistent: an inner surface should not have a larger radius than the container around it.

Use surface contrast, spacing, and radius to establish depth. **Do not add shadows to PolyKit product UI:** no Tailwind `shadow-*` utilities, CSS `box-shadow`, or decorative `drop-shadow` effects. This applies to panels, cards, controls, dialogs, dropdowns, popovers, tooltips, and transient overlays. When regions need a divider, use the dark semantic `border-divider` token; never use bright white, default `border-border`, or high-opacity lines for structure. Keep the surface stack responsible for most of the hierarchy so edges stay crisp in the dark workspace.

### 9.1. Surface-first hierarchy: restrained borders and dividers

Express hierarchy with background, fill, spacing, and radius first. Borders and dividers are state and structure cues, not decoration.

- The outermost application shell must remain borderless. This includes the `MainLayout` shell, the `Router` route wrapper, and page-level viewport roots: do not add a frame, outline, or high-contrast edge around the whole UI. Let the outer background, breathing room, clipping radius, and inner surface contrast define the boundary.
- Add borders only to meaningful inner surfaces (panels, split panes, cards, controls, or active states) and use the dark `border-divider` token for structural separation.

- Start with the semantic surface stack (`background` → `card` → `muted`) and generous spacing before adding a line.
- Default cards, rows, and media tiles should have no visible outline when their fill and spacing already separate them.
- Use a border for selected, focused, disabled, destructive, or otherwise meaningful state; keep it subtle outside the active state.
- Use dividers only between major regions or when spacing and surface grouping cannot make the relationship clear.
- Never stack an outer card border, an inner media border, and a content divider on the same component.
- When a filled surface and a radius already establish grouping, do not add a redundant separator.

Preferred pattern: a rounded `card` surface containing a tighter rounded `muted` inset, separated by padding rather than lines.

### 10. Surface hierarchy

Use a small, predictable surface stack:

1. `background` — application/page base
2. `card` — panels, cards, sidebars, dialogs
3. `muted` — inset controls, subtle groups, secondary containers
4. `popover` — floating menus/popovers

Do not create new near-black shades per component.

### 11. States must be visible without relying only on color

Interactive controls need clear hover, focus, disabled, selected, loading, error, and success states.

- selected navigation: background + text/icon change; optional indicator
- disabled: reduced opacity and pointer behavior
- destructive: label/icon plus destructive styling
- loading: progress/spinner plus readable text
- errors: concise human-readable summary with technical detail separately selectable/copyable

### 12. Accessibility

Required for every new or touched interactive component:

- keyboard reachable,
- visible `focus-visible` ring,
- semantic element (`button`, `input`, `nav`, etc.),
- accessible label/name,
- `aria-current`, `aria-pressed`, `aria-expanded`, or dialog semantics where applicable,
- always keep a visible focus indicator; replace the default outline instead of removing it,
- target size should normally be at least 32 px in this desktop UI.

Prefer Radix/shadcn primitives for dialogs, menus, selects, switches, tabs, and tooltips because focus management and keyboard semantics are difficult to reproduce correctly.

### 13. Dialogs, drawers, and destructive flows

Use the shared Dialog foundation for modal interactions. Do not make a modal from `fixed inset-0` or `absolute inset-0` feature-owned overlay divs.

Drawers and sheets are part of the dialog family. Build them from the shared Radix/shadcn dialog foundation so focus trapping, Escape handling, outside dismissal, scroll locking, and focus restoration are consistent across the app. A side panel may change the placement and animation of `DialogContent`; it must not reimplement dialog behavior.

Feature code must not combine `createPortal`, a full-screen overlay, and a custom global Escape listener to simulate a modal, drawer, or sheet. `createPortal` is reserved for specialized non-modal canvas positioning when an existing shared primitive cannot represent the interaction.

Dialog order:

1. title,
2. short description,
3. content,
4. actions aligned consistently.

Destructive actions must be visually distinct and must not be the default focused action unless the workflow specifically requires it.

### 14. Forms

Every form control should have a visible or screen-reader label. Help text belongs below the control or alongside the setting label, not inside placeholder text.

Do not use placeholder text as the only label.

Use consistent validation language: state what happened and how to resolve it.

### 15. Notifications

Transient feedback uses the shared toast system. Blocking or actionable failures use Dialog/AlertDialog.

Do not create page-specific toast implementations.

### 16. Navigation

Global navigation and Settings navigation should use the same interaction grammar:

- neutral inactive state,
- primary selected state,
- stable hit area,
- Lucide icons,
- readable labels or accessible tooltip/title for icon-only controls.

Avoid decorative glows except for very small selected/current-state accents.

### 17. Motion

Motion should explain state changes, not decorate idle UI.

Use approximately 150–250 ms transitions for hover, popover, drawer, and dialog changes. Respect `prefers-reduced-motion`.

Avoid continuous animation except for progress/loading indicators.

### 18. 3D and workflow canvas exceptions

The 3D viewer and React Flow canvas are specialized workspaces. They may use bespoke layout, colors, and interaction visuals where semantic app tokens are insufficient.

However, controls layered over those canvases should still use shared UI primitives.

## Component ownership

- `src/shared/components/ui/` — shadcn primitives and universally reusable UI.
- `src/shared/components/layout/` — application shell/navigation composition.
- `src/areas/**/components/` — feature/domain composition built from shared primitives.
- page files — layout and feature orchestration; avoid defining reusable primitives inline.

A component should move to shared UI when it appears in two unrelated product areas or represents a standard interaction primitive. Migration-only wrappers should be deleted once their callers can use shared primitives directly; do not preserve them as permanent abstraction layers.

## Migration policy

This is an incremental migration. Do not rewrite stable feature logic merely to change class names.

When touching an existing UI area:

1. replace local controls with existing shared shadcn primitives,
2. replace raw palette colors with semantic tokens where practical,
3. replace generic handwritten SVGs with Lucide icons,
4. replace feature-owned modal/drawer overlays with shared dialog-family primitives,
5. preserve behavior and tests,
6. add a shared primitive instead of copying a new local pattern,
7. delete migration-only compatibility wrappers after their last caller is migrated.

A touched legacy block is considered migrated only when its ordinary controls come from the shared system, ordinary colors use semantic tokens, generic icons use Lucide, modal layers use shared primitives, and keyboard/focus behavior remains intact. Do not leave a half-migrated control cluster unless the remaining part is a documented canvas/domain-specific exception.

New UI must follow this document immediately; legacy UI is migrated opportunistically and in focused passes.

## Review checklist

Before merging UI work, verify:

- [ ] Uses shared shadcn primitives where one exists.
- [ ] No new UI framework or local primitive duplication.
- [ ] No migration-only compatibility wrapper remains after its callers are gone.
- [ ] No new raw palette/hex colors in ordinary feature UI.
- [ ] Uses Lucide for generic product icons.
- [ ] No feature-owned modal/drawer overlay when the shared dialog foundation can represent it.
- [ ] Keyboard and focus behavior works.
- [ ] Disabled/loading/error states are explicit.
- [ ] Spacing/radius match the shared scale.
- [x] The Web build is the single React UI and renders against the FastAPI API.
- [ ] `npm test`, Web type-check, Web build, and desktop build remain valid.
