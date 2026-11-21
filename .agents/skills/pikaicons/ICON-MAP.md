# Pikaicons: names for common needs

A starting point, not a replacement for `search`: names change, and there are
usually several versions of each (`settings-01`, `settings-02`, …). Run
`npx pikaicons search <words>` to see the others and to confirm a name before
you add it. **F** = in the free pack when written (works signed out);
`search --free` is the truth. Signed out, choose from the F entries; an
unmarked one needs a license.

## Everyday UI

| Need | Icon | Search words that find it |
|---|---|---|
| Home | `home-default` F | home |
| Search | `search-default` F | search |
| Settings | `settings-01` F | settings, gear |
| User / account | `user-default` F, `user-circle` F | user, avatar |
| Team | `user-three`, `community` | team |
| Add user | `user-plus` F | user add |
| Notifications | `notification-bell-on` F; unread: `notification-bell-dot` | bell, notification |
| Inbox | `inbox-default`; free: `envelope-default` F | inbox |
| Email | `envelope-default` F | email, mail |
| Calendar | `calendar-default` F | calendar |
| Clock / time | `clock-default` F | clock |
| Dashboard | `grid-dashboard-02` F, `grid-dashboard-01` | dashboard |
| Chart | `barchart-default` | chart |
| Folder | `folder-default` F | folder |
| File | `file-default` F | file |
| Image | `photo-image-default` F | image |
| Tag | `tag` F | tag |
| Link | `link-slant` F, `link-horizontal` | link |
| Globe / language | `globe` F | globe |
| Billing | `invoice-01`, `credit-card` F | billing, credit card |
| Database | `database` F | database |
| Server | `server` F | server |
| Cloud | `cloud-default` F | cloud |
| Code | `code` F | code |
| Git branch | `git-branch` F | git branch |
| Kanban / board | `kanban-board`; free: `grid-dashboard-02` F | kanban, grid |

## Actions

| Need | Icon | Search words |
|---|---|---|
| Add | `plus-default` F | plus |
| Close / remove (×) | `multiple-cross-cancel-default` F | close, cross |
| Confirm (✓) | `check-tick-single` | check |
| Edit | `pencil-edit` F | edit |
| Delete | `delete-dustbin-01` F | trash, delete |
| Copy | `copy-default` F, `copy-copied` F (done state) | copy |
| Save | `floppy-default` F | save |
| Download | `download-down` F | download |
| Upload | `upload-up` F | upload |
| Share | `share-01` F | share |
| Send | `send-plane-horizontal` F | send |
| Attach | `paperclip-vertical` F | attach |
| Refresh | `refresh` F | refresh |
| Undo | `rotate-left` | undo, rotate |
| Filter | `filter-funnel` F | filter |
| Sort | `chevron-sort-vertical` | sort |
| Open in new tab | `external-link-square` F | external link |
| Sign in | `log-in-right` F | login |
| Sign out | `log-out-right` F | logout |
| Show / hide | `eye-on` F, `eye-off` F | eye |
| Like / favourite | `heart` F, `star` F | heart, star |
| Drag handle | `draggable-six-dots` | drag handle |
| More (⋯ / ⋮) | `three-dots-menu-horizontal` F, `three-dots-menu-vertical` F | more, ellipsis, kebab |
| Menu (☰) | `burger-menu-three` F | menu, hamburger |
| Arrow / chevron | `arrow-right` F, `chevron-down` F | arrow right, chevron down |
| Loading | `spinner` | spinner, loading |

## Status

| Need | Icon | Search words |
|---|---|---|
| Success | `check-tick-circle` F | success |
| Error | `alert-circle` F | error |
| Warning | `alert-triangle` F | warning |
| Info | `information-circle` F | info |
| Help | `question-mark-circle` F | help |
| Locked / secure | `lock-close` F, `shield` F | lock, shield |
| Light / dark mode | `sun` F, `moon` F | sun, dark mode |
| On / off | `toggle-on` F, `toggle-off` | toggle |

## Stand-ins for things the library has no icon for

| Concept | Use | Why |
|---|---|---|
| Onboarding, get started | `rocket-ship` F, `list-check` | launch; setup steps |
| Changelog, what's new | `megaphone-announcement-shout`, `gift-default` F | an announcement |
| AI feature | `sparkle-ai-01` F | the settled AI mark |
| Upgrade, premium, pro | `crown-winner-king` F, `rocket-ship` F | status; lift-off |
| Beta, experimental, labs | `lab-flask-conical`; free: `tag` F | an experiment; a label |
| 2FA, security | `shield` F, `fingerprint-default`, `key-slant` | protection, biometric, secret |
| SSO | `log-in-right` F, `key-slant` | signing in; one key |
| API key | `key-slant`, `api` | secret; endpoint |
| Webhook | `webhook`, `api` | exists; stroke and duo-stroke only |
| Rate limit, quota | `gauge-speedometer`, `hourglass` | a limit; a wait |
| Churn | `user-remove`, `trendline-down` | customer leaving; metric falling |
| Refund | `credit-card-arrow-left`, `wallet-arrow-left` | money going back |
| Feature flag | `flag` F, `toggle-on` F | flag; switch |
| History, activity | `clock-default` F, `activity` | time; pulse |
| Integrations, plugins | `plugin-addon-default`, `plugin-addon-puzzle` | plug; puzzle piece |
| Container, package | `cube`, `archive-default` | a box |
| Cluster (Kubernetes, Docker) | `server` F | what it is; never fake the logo |
| Empty state | the icon of the empty thing | `inbox-default`, `folder-default` F, `search-default` F |

## Search traps seen in real results

| You typed | You got | Do instead |
|---|---|---|
| `sso` | scissors | `login`, `key` |
| `two factor` | `user-two`, `factory`, `layer-two` (one hit per word) | `shield`, `fingerprint` |
| `skeleton` | `bone` | a loading skeleton isn't an icon; use `spinner` or none |
| `rate limit` | `media-speed-1x` | `gauge`, `hourglass` |
| `close` | also `lock-close`, `sidebar-left-close` | `multiple-cross-cancel-default` |
| `hamburger` | also `burger` (the food) | `burger-menu-three` |
| `error` | `bug` first | `alert-circle` |
| `x` | `x-com` (the brand) | `close` or `cross` for × |
| `anchor` | align icons | no anchor icon; use `link-slant` for an anchor link |
