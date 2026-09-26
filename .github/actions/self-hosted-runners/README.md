# self-hosted-runners

Fails a check when any workflow in the repository can run on a GitHub-hosted
runner. Cubeage runs every workflow on its own runners: `sylphx-linux-standard`,
`sylphx-linux-xlarge`, or `[self-hosted, sylphx, macos, standard]`.

It flags a hosted label (`ubuntu-*`, `windows-*`, `macos-*`) as a scalar or in a
list, a hosted fallback inside an expression, a hosted matrix value used by
`runs-on`, and a `runs-on` taken from `vars.*`/`inputs.*` without an own-runner
label in the same expression.

Use it as a job in the repository's `content-checks.yml` (it feeds `ci-ok`):

```yaml
  self-hosted-runners:
    runs-on: sylphx-linux-standard
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
      - uses: Cubeage/.github/.github/actions/self-hosted-runners@<commit>
```

Test: `python3 .github/actions/self-hosted-runners/test/test_check_runners.py`.
