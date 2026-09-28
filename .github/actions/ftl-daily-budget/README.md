# ftl-daily-budget

Fails a job before it can spend on Firebase Test Lab once the Google Cloud
project has used its daily allowance for the device kind the step is about to
run. Test Lab bills device time, and spending needs the owner's approval, so
this guard is the check that does not depend on anyone's discipline: it reads
what the project has already run today and refuses the next one.

## What it counts

It runs after the workflow has authenticated gcloud, gets a token with
`gcloud auth print-access-token` (the token is never printed), and reads the
Tool Results API - the API Test Lab files its own executions in:

- `projects/{project}/histories`, then each history's `executions`, then each
  recent execution's `steps`;
- a step counts when its own `creationTime` is at or after the current Test Lab
  day's start: **midnight America/Los_Angeles**, where the quota resets;
- the device kind comes from the step's `dimensionValue` device id, and from the
  testing API's Android device catalog (`form: VIRTUAL | EMULATOR | PHYSICAL`)
  when that catalog can be read - the catalog is best effort, and when it cannot
  be read the id rule decides alone.

An unrecognised device, and a step with no `creationTime`, both count as
**physical**: the expensive part of the quota is the direction a guard must err
towards.

## What it refuses

`count + devices > max-per-day` fails the job with

```
::error::Test Lab daily budget reached (<count>/<max> <kind> today); spending needs owner approval
```

Everything that stops it proving the count does the same: no access token, a
transport error, a timeout, a body that is not valid JSON, a payload that is not
the listing it should be. A guard that cannot count never lets a run through.
The count and the verdict are also written to the job's step summary.

## Use it

Immediately before the step that runs `gcloud firebase test`, on the same `if:`
condition as that step:

```yaml
      - name: Test Lab daily budget
        if: ${{ github.event_name == 'push' }}
        uses: Cubeage/.github/.github/actions/ftl-daily-budget@<commit>
        with:
          project: ${{ vars.FIREBASE_TEST_LAB_PROJECT_ID || 'lavapot-1292' }}
          kind: physical          # physical | virtual
          max-per-day: "3"        # 3 physical, 10 virtual
          devices: "1"            # how many devices this step can run
```

`kind` is the device the step will use: iOS Test Lab devices are always
physical; on Android, `.arm`/`.x86` device ids (such as `MediumPhone.arm`) are
virtual and ids such as `oriole` (Pixel 6), `a16x` or `iphone16pro` are
physical. `devices` is the most the step can add in one run - a step with a
retry loop or a control arm passes that maximum, so the guard counts the whole
run and not just its first device.

Test: `python3 -m unittest discover -s .github/actions/ftl-daily-budget/test`.
Its tests run in `.github/workflows/ftl-daily-budget-action.yml`, which also
runs the action itself and asserts that it fails closed with no credentials.
