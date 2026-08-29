# Website

This website is built using [Docusaurus](https://docusaurus.io/), a modern static website generator.

## Installation

```bash
npm install
```

**Note**: feel free to use the package manager of your choice.

## Local Development

```bash
npm run start
```

This command starts a local development server and opens up a browser window. Most changes are reflected live without having to restart the server.

## Build

```bash
npm run build
```

This command generates static content into the `build` directory and can be served using any static contents hosting service.

## Testing & CI Safety

Docusaurus pre-renders every page to static HTML at build time (SSR). Any component that
touches a browser-only API (`window`, `document`, or a "naked" `setTimeout`/`setInterval`
outside of React lifecycle) outside a `useEffect` hook or a `<BrowserOnly>` wrapper will
crash `npm run build` — and therefore the production deploy.

**Currently enforced in CI:** [`.github/workflows/deploy-docs.yml`](../.github/workflows/deploy-docs.yml)
runs `npm run build` on every push to `main` that touches `website/**`, and only deploys to
Firebase Hosting if the build succeeds. This is the SSR-safety gate today — for example,
[`InteractiveShieldDemo`](src/components/Homepage/InteractiveShieldDemo/index.tsx)'s
simulated SSE lookahead buffer schedules its `setTimeout` calls from inside `useEffect`
specifically so the component pre-renders cleanly.

Before submitting a website PR, run the same check locally:

```bash
npm run build
```

**Planned hardening (not yet implemented):** to add a pre-merge gate — rather than only
catching SSR/regression issues after landing on `main` — the roadmap is:

1. **Component-level unit tests** (Jest + React Testing Library) for
   `InteractiveShieldDemo`: assert the simulated Tier 1/Tier 2 redaction timeline updates
   the "Received" box state after the simulated SSE lookahead delay, and that selecting a
   Tier 3 NER example auto-fills the input textarea.
2. **Firebase preview channels** via `FirebaseExtended/action-hosting-deploy`, so every PR
   gets an ephemeral preview URL instead of only validating against `main`.
3. **A headless smoke test** (Playwright) against that preview URL: type PII into the demo,
   wait for the simulated network delay, and assert the original text is rehydrated —
   an automated check of the zero-egress rehydration flow end-to-end.

If you want to pick this up, open an issue first so the Firebase preview-channel service
account / secrets can be provisioned.
