# apiwatch

Detects **undocumented** changes in APIs you depend on, by watching live
responses instead of reading changelogs.

Changelog readers are a weekend project and half a dozen exist. The gap this
targets is the change the vendor never wrote down: a field quietly appears, a
default flips, an error code changes, a response type shifts from string to
int. None of that shows up in release notes, and none of it is visible until
something breaks in production.

Zero dependencies. Pure Python 3.10+ standard library.

## Run it

```bash
python -m apiwatch snapshot      # capture every target
python -m apiwatch snapshot npm  # just one
python -m apiwatch diff          # compare the two most recent snapshots
python -m apiwatch log           # list what you've captured
```

`diff` exits 1 when it finds a BREAKING change, so CI can fail on it.

Four targets work with no credentials at all (npm, pypi, crates, and github
at 60 req/hr). Set `GITHUB_TOKEN` and `STRIPE_TEST_KEY` in the environment to
enable the rest. No secrets go in `targets.json`.

## Free scheduled runs

`.github/workflows/watch.yml` runs this every 6 hours on GitHub Actions,
commits the snapshot history back to the repo, and opens an issue when
something breaking appears. Push it to a public repo and it costs nothing,
permanently. You do not need a server.

## What it records

Per probe: HTTP status, response body **schema**, watched headers, and pinned
values. Deliberately *not* values in general — ids and timestamps change every
call and would bury you in noise.

Three design decisions carry most of the weight:

**Schema, not values.** A type tree, not the response. Types change when the
vendor changes something; values change constantly.

**Unreliable responses never reach the diff.** A 429 or a 503 gets recorded and
skipped. Without this guard, one rate-limit response reports as "every field
removed" and users learn to ignore your alerts within a week. This is the
difference between a monitor and a spam generator.

**Id-keyed maps get collapsed.** An object with many same-shaped values is data,
not schema. Left alone, one npm package expands to 25,043 schema paths and
every release looks like thousands of new fields. Collapsed, it's 127.

## Error probes matter more than happy paths

Every target should include at least one deliberately-failing request. Error
surfaces drift far more often than success responses and are almost never
documented. Set `expect_status` so an intentional 404 isn't confused with a
real outage.

## Severity

| Level | Meaning |
|---|---|
| BREAKING | Field removed, type changed, status changed, pinned value changed, or a `Sunset`/`Deprecation` header appeared. Your code may already be broken. |
| ADDITIVE | New field. Not urgent, but it's the vendor shipping something unannounced. |
| INFO | Latency shifts, optionality flips, skipped probes. |

`Sunset` and `Deprecation` are RFC 8594. Vendors who use them are announcing a
breaking change months ahead, and almost nobody reads them.

## The two-week protocol

This exists to answer one question: **do undocumented changes happen often
enough that anyone would pay to hear about them?**

1. Pick 3–5 APIs. Prefer ones shipping fast, with large developer bases.
2. Let it run every 6 hours for 14 days. Don't touch it.
3. Log every BREAKING and ADDITIVE finding. For each, check whether the vendor
   documented it anywhere. **Undocumented catches are the only number that
   matters.**

### Kill criteria — decide now, not later

- **0 undocumented catches in 14 days** → stop. The thesis is wrong and you
  spent two weeks, which was the point.
- **1–2** → real but too rare to sell. Either widen to noisier APIs (AI
  provider APIs move much faster than package registries) or stop.
- **3+** → you have a screenshot that argues for itself. That's your Hacker
  News post, and it's worth more than any landing page.

Do not extend to week three hoping. A fast honest no is the whole point of
building it this way.

## What v2 looks like (only after the above passes)

Map findings back to source: "field `X` was removed, and you call it in
`src/billing.ts:47`." That's when it stops being a monitor and starts being a
product. Don't build it before you have the catches.
