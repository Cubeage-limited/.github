# self-hosted-runners

Fails a check when any workflow in the repository can run on a runner that
costs GitHub money. The goal is zero GitHub spend (owner standards/dx.md):

- **Private repositories** run every job on our own runners:
  `sylphx-linux-standard`, `sylphx-linux-xlarge`, or
  `[self-hosted, sylphx, macos, standard]`.
- **Public repositories** may also use GitHub's free standard hosted runners
  (`ubuntu-*`, `windows-*`, `macos-*` with an OS version or `latest`, optionally
  `-arm`/`-intel`).
- **Larger and GPU runners** (a size suffix such as `-xlarge` or `-8-cores`,
  anything naming `gpu`, or a `runs-on: {group: ...}` runner group) fail in
  every repository.

It reads each job's `runs-on` as a scalar or list label, a label inside an
expression (e.g. a fallback), a matrix value used by `runs-on`, and a `runs-on`
taken from `vars.*`/`inputs.*` without an own-runner label in the same
expression (that one fails everywhere, since its value is not reviewed).

The `visibility` input defaults to the triggering repository's visibility
(`github.event.repository.private`); a missing or unknown value is treated as
private.

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
