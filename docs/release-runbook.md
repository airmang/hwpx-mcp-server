# Release-state runbook

The automation train uses three states because approval, publication, and
observed remote truth are different facts. `src/hwpx_automation/identity.json`
is the machine-readable authority; README and current-facing guides must say
the same thing.

## 1. `unreleased-candidate`

Use while implementation and audit are in progress. `candidate` records the
proposed 5.0/6.0/1.0 coordinates. `currentPublic` continues to record the
observed 4.2/5.1/0.8 release. No tag or upload is authorized.

## 2. `release-approved`

Enter this state only after separate owner approval to publish. Keep
`currentPublic` unchanged because it names the last coherent public three-stack,
not whichever dependency happened to publish first.
Update the README marker to `release-state: release-approved` and make README,
`docs/use-cases.md`, `docs/hardening_guide_ko.md`, and
`docs/skill-first-workflows.md` say that approval exists but remote truth is
pending.

The tag workflow accepts only this state. It builds and validates canonical and
compatibility artifacts before any upload, publishes canonical first, observes
that exact version from PyPI, then publishes and observes the compatibility
shell. The successful tag release attaches an automation publication receipt
and hands it to plugin publication. It rejects both `unreleased-candidate` and
premature `released`; observing the two automation PyPI distributions does not
promote the global state or `currentPublic`.

## 3. `released`

Use only after every member of the coherent train has remote evidence:

1. `python-hwpx 5.0.1` is observed on PyPI and as its GitHub Release;
2. canonical `python-hwpx-automation 6.0.1` and compatibility
   `hwpx-mcp-server 6.0.1` are observed on PyPI, and the automation GitHub
   Release carries the handoff receipt;
3. `hwpx-plugin 1.0.0` is observed in its GitHub Release and marketplace entry;
4. a real marketplace install resolves the exact 5.0/6.0/1.0 stack and contract
   hash `0ce938371f0b55a6`.

Only then, in a follow-up commit on the main branch:

1. set `releaseState.status` to `released`;
2. promote `currentPublic` to core 5.0.1, canonical automation 6.0.1, plugin
   1.0.0, and contract hash `0ce938371f0b55a6`;
3. change the README marker to `release-state: released`;
4. remove candidate/pending wording from the four current-facing guides;
5. run `python scripts/check_transition_identity.py` and the full clean
   artifact/install verification.

The automation receipt deliberately says `release-approved` and preserves the
old `currentPublic`; plugin publication consumes that receipt in dependency
order. Neither an approved tag, a successful upload attempt, nor the two
automation PyPI observations alone are sufficient to claim the whole stack
`released`.
