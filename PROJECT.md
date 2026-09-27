# Cubeage GitHub Infrastructure

Cubeage GitHub Infrastructure owns Cubeage organization-level GitHub Actions
workflow templates and the reusable iOS signing/build workflow for Cubeage app
repositories. It documents how consumer repos call the workflow, which secrets
they provide, and how temporary signing assets are isolated during builds.

Machine-readable project identity lives in
[.doctrine/project.json](./.doctrine/project.json).

## Lifecycle And Layer

- Lifecycle: `production`
- Layer: `tooling`

## Goals

- Provide reusable GitHub Actions workflow surfaces for Cubeage repositories.
- Keep iOS signing/build workflow inputs, secrets, outputs, and runner labels
  visible in Git.
- Document the security model for temporary keychains, provisioning profiles,
  and IPA build artifacts.

## Non-Goals

- This repository does not own individual game/application source code.
- This repository does not own Apple Developer accounts, signing secrets, or
  per-app release decisions.
- This repository does not own the Kubernetes/Talos runner infrastructure that
  backs the self-hosted macOS runner labels.

## Boundary Summary

Consumer repositories may call the reusable workflow by its public GitHub
Actions workflow path and may copy the workflow template. They must provide
their own secrets and app-specific scheme/export configuration. This repository
does not take ownership of consumer app behavior, store metadata, runtime
deployments, or production support.

## Public Surfaces

- Reusable workflow: `.github/workflows/ios-sign-and-build.yml`.
- Reusable workflow: `.github/workflows/firebase-app-distribution.yml`.
- Shared script: `scripts/pages-publish.sh`, the Cloudflare Pages upload that
  Cubeage title repositories fetch at a pinned commit.
- Template metadata: `workflow-templates/ios-app-store.properties.json` (the
  matching `ios-app-store.yml` template is missing from the repository).
- Shared action: `.github/actions/game-standard/`, the check title
  repositories run on their `game-standard.toml` (rule ids, levels and module
  names only, no business content).
- Workflow template: `workflow-templates/game-standard.yml`, the caller
  example title repositories copy, with its
  `workflow-templates/game-standard.properties.json` metadata.
- This repository's own content checks: `.github/workflows/content-checks.yml`;
  the game-standard action's own tests:
  `.github/workflows/game-standard-action.yml`.
- Operator documentation: `README.md`.

## Delivery Proof

There is no application deployment from this repository. Durable changes land
on `main`; workflow behavior is proven by syntax validation and by a consumer
repository invoking the reusable workflow with the expected runner labels and
signing inputs.
