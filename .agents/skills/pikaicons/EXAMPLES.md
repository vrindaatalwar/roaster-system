# Pikaicons: worked examples

Real CLI output, trimmed. Each example shows the request, the commands, and
the decision. Start from the one that matches the request. Icon names here were live when written; `search` is the truth.

## 1. A sidebar (React, signed in)

Request: sidebar with Home, Inbox, Projects, Settings, and a selected state.

```bash
npx pikaicons search home inbox kanban settings --limit 4
```
```
home
  home-default   all variants  home, house, dwelling, residence
  home-settings  all variants  home config, manage home, smart home settings, home preferences
inbox
  inbox-default     all variants  inbox, mailbox, email tray, message tray
  envelope-default  all variants  email, mail, message, letter
kanban
  kanban-board     all variants  kanban, trello board, jira board, task board
settings
  settings-ai  all variants  ai settings, model settings, ai preferences, settings
  settings-01  all variants  settings, gear, cog, options
```

Decisions: the top hit for `settings` is `settings-ai` (a gear with an AI
mark), which is wrong for plain settings, so take `settings-01`. All four come
in every variant, so stroke at rest and solid when selected works.

```bash
npx pikaicons add home-default inbox-default kanban-board settings-01 \
  home-default:solid inbox-default:solid kanban-board:solid settings-01:solid
```
```tsx
import { HomeIcon, HomeSolidIcon } from "@/components/icons"

<a aria-current={active ? "page" : undefined} className="flex items-center gap-2">
  {active ? <HomeSolidIcon className="size-5" aria-hidden /> : <HomeIcon className="size-5" aria-hidden />}
  Home
</a>
```

## 2. Icon-only buttons

Request: a row with edit, delete, and "more".

```bash
npx pikaicons search edit trash "more options" --limit 3
```
```
edit
  pencil-edit  all variants  …
trash
  delete-dustbin-01  all variants  trash, trashcan, garbage, recycle bin
more options
  three-dots-menu-horizontal  stroke duo-stroke  more, ellipsis, three dots, meatball menu
```
```bash
npx pikaicons add pencil-edit delete-dustbin-01 three-dots-menu-horizontal
```
```tsx
<button aria-label="Delete project">
  <DeleteDustbin01Icon className="size-4" aria-hidden />
</button>
```

The label goes on the button; the icon stays `aria-hidden`.
`three-dots-menu-horizontal` has no solid, which is fine: line glyphs stay in
stroke even on a surface that uses solid elsewhere.

## 3. Plain HTML, Vue, Svelte, email

```bash
npx pikaicons show github
```
```html
<svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="none" viewBox="0 0 24 24">
  <path stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 15a3.72…" fill="none" />
</svg>
```

Paste the whole element as printed. Change only `width`/`height`, or add a
`class`. It takes the colour of the surrounding text. Several at once:
`show github x-com linkedin`; with `--json` you get
`{ icons: [{ name, variant, svg }] }` to write into files yourself.

## 4. Signed out: free icons, and a locked one

```bash
npx pikaicons search settings "credit card" kanban --limit 2
```
```
settings
  settings-ai  locked  all variants  ai settings, model settings, ai preferences, settings
  settings-01  free    all variants  settings, gear, cog, options
credit card
  credit-card       free    all variants  card, debit card, payment card, atm card
  credit-card-plus  locked  all variants  add card, new card, plus card, save card
kanban
  kanban-board     locked  all variants  kanban, trello board, jira board, task board
  activity-square  locked  all variants  dashboard, workflow box, kanban, task tile
```

`settings-01` and `credit-card` are free: add them. Kanban has no free icon:

```bash
npx pikaicons add kanban-board
# error kanban-board is in the full library, which needs a license   (exit 3)
npx pikaicons search board columns grid layout --free --limit 3
```

`board` and `columns` return nothing useful; `grid` returns the free
`grid-dashboard-02` ("dashboard, widget grid, panel grid"). Add it, finish,
and report:

| Where | Icon | Fit |
|---|---|---|
| Settings | `settings-01` | Exact |
| Billing | `credit-card` | Exact |
| Projects | `grid-dashboard-02` | Stand-in: `kanban-board` fits better but is locked |

> Run `npx pikaicons login` in your terminal and say `swap`; I'll replace the stand-in.

Don't stop the task to wait for a login, and don't ask for the key.

## 5. The concept isn't in the library

**Onboarding** (`search onboarding` → no match). Reword with what onboarding
looks like: a launch, a list of steps, a guide.

```bash
npx pikaicons search rocket checklist map flag --limit 2
```
```
rocket
  rocket-ship  all variants  rocket, spaceship, launch, blast off
checklist
  list-check   stroke duo-stroke  checklist, todo list, completed list, list confirmed
map
  map          all variants  …
flag
  flag         all variants  flag, banner, country flag, marker
```

"Get started" → `rocket-ship`. A setup checklist → `list-check`.

**Two-factor auth** (`search "two factor"` falls back to single words and
returns `user-two`, `factory`: both wrong, reject them). Use the convention:

```bash
npx pikaicons search shield fingerprint key "lock" --limit 2
```

→ `shield` for security settings in general, `fingerprint-default` for
biometrics, `key-slant` for passkeys or API keys.

**SSO** (`search sso` returns scissors: a fuzzy hit, reject). SSO is signing
in: `search login key` → `log-in-right`, or `key-slant`.

**Changelog / What's new** (no match) → `megaphone-announcement-shout` for
announcements, `sparkle-ai-01` only if the product doesn't already use
sparkles for AI, `gift-default` for a "new for you" feel.

**Churn** (no match) → `user-remove` (a customer leaving) or `trendline-down`
(the metric).

**Rate limit** (word fallback returns `media-speed-1x`: wrong) → search
`gauge hourglass` → `gauge-speedometer` for a limit, `hourglass` for "wait".

**Feature flag** → `flag` if nothing else on the screen uses a flag;
otherwise `toggle-on`.

**Empty state** isn't an icon: use the icon of the thing that's empty
(`inbox-default`, `folder-default`, `search-default`).

## 6. A brand that isn't there

```bash
npx pikaicons search kubernetes docker
# No icons match "kubernetes" · No icons match "docker"   (exit 4: every query missed)
```

Don't draw the logo and don't use another brand's. Either use what the thing
is (`server` for a cluster, `cube` for a container) or ask:

> Pikaicons has no Kubernetes logo. I used `server`. If you want the real
> mark, send me the official SVG and I'll drop it in.

Brands that are there are plain names: `github`, `google`, `x-com`,
`supabase`. Search the brand name first.

## 7. You guessed a name

```bash
npx pikaicons add bell-default credit-card-default
# error 2 names didn't match: "bell-default" (did you mean alarm-default?), "credit-card-default"   (exit 4)
```

Nothing was written. Don't take "did you mean" on faith (`alarm-default` is an
alarm clock). Search, then add exact names:

```bash
npx pikaicons search bell "credit card" --limit 3
npx pikaicons add notification-bell-on credit-card
```

## 8. The variant doesn't exist

```bash
npx pikaicons add spinner:solid
# error spinner has no solid variant
#   spinner comes in: stroke, duo-stroke. Pick one with spinner:<variant>   (exit 2)
```

Use `spinner` in stroke. To find only icons that have a variant up front:
`npx pikaicons search notification --variant solid`.

## 9. Replacing another library

Request: "swap lucide-react for Pikaicons".

1. List what's used: grep the imports (`from "lucide-react"`) and collect the
   icon names.
2. Search them all in one call, using plain words, not the old names:
   `npx pikaicons search trash "chevron down" search settings plus --limit 3`.
3. Pick one Pikaicon per old icon; note any that needed a stand-in.
4. `add` them all in one call, then change the imports and JSX. Props differ:
   Pikaicons components take plain `<svg>` props (`width`, `height`,
   `className`), not `size` or `strokeWidth`.
5. Remove the old package only after the build passes, and list the stand-ins
   in your reply. If you can't run the build, leave the package and say so.
6. A prop with no equivalent (`strokeWidth={1.5}`) is dropped: Pikaicons are
   drawn at one weight. Say so in one line.

## 10. Scripts and JSON

```bash
npx pikaicons search trash --json --limit 1
```
```json
{ "schema": 1, "signedIn": true, "stale": false,
  "results": [{ "query": "trash", "total": 8, "matchedWords": false,
    "icons": [{ "name": "delete-dustbin-01", "component": "DeleteDustbin01Icon",
      "category": "general", "variants": ["stroke","solid","contrast","duo-stroke","duo-solid"],
      "aliases": ["trash","trashcan","garbage"], "tags": ["remove","discard"],
      "free": true, "locked": false }] }] }
```

`matchedWords: true` means the phrase had no match and the results are for
its separate words: treat them as weak. With `--json`, errors go to stderr as
`{ error: { code, message, hint, exit, details } }`. `add --json` returns
`files`, `import` and `usage`.
