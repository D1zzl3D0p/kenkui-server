# Modal CPU benchmark — 2026-09-13–14

> Historical benchmark: “production” below means the 2-CPU/1-worker baseline.
> The live service now uses 8 CPUs, 8 workers and 12 GiB requested memory.

## Recommendation

Use **8 CPUs / 8 Kenkui workers** as the value-oriented default for long books.
Use **24 CPUs / 16 workers** as a speed tier: it saves 1h 26m on Dune for about
$0.91 more. Do not raise Kenkui's process cap to 24: 24 synthesis workers were
slower and more expensive than 16 on the same 24-CPU container.

The full Dune runs requested 8 GiB, but settled billing implies about 9.7 GiB
of metered memory at 8/8 and roughly 16 GiB at the higher configurations.
Request at least **12 GiB for 8/8** and **16 GiB for 16/16 or 24/16** for
scheduling reliability. Capture peak RSS before imposing a hard memory limit.

The short-fixture cost-efficiency choice was **4 CPUs / 4 workers**. It was
about 37% faster than the current deployment at nearly identical cost per
finished audio hour, but it has not been validated on the complete Dune input.

No production configuration was changed by this benchmark.

## Method

- Environment: Modal `staging`, voice `eponine`, production image and model volume
- Input: deterministic EPUB with 24 independently renderable chapters
- Work: 2,919 normalized speech characters producing about 183 seconds of audio
- Resources: 8,192 MiB memory request for every run; memory was not hard-capped
- CPU: equal request and hard limit, preventing opportunistic bursting
- Prices queried from `modal billing rates` at run time:
  - CPU: $0.04730 per CPU-hour
  - memory: $0.00800 per GiB-hour
- Request-based cost estimates use client-observed invocation time and the CPU and
  memory requests. Modal bills the greater of requested or actual usage, so these
  are not guaranteed upper bounds. Settled billing is authoritative where reported.
- Modal billed $0.3813 total for all short-fixture sweeps plus one interrupted
  exploratory run, including image/container startup. This total is not used for
  per-row cost because Modal's report aggregates all functions within each app.
- Short-fixture runs were sequential. The 16- and 24-CPU Dune finalists ran
  concurrently in isolated containers; the 8-CPU validation ran afterward.

Modal's `cpu` setting is described as physical cores in its documentation. The
runtime affinity reported 2 CPUs for both the 1- and 2-CPU allocations, then the
requested count for 4 and above.

## CPU sweep

The means include four samples for production, 4, 8, 16, and 24 CPUs. The 1- and
2-worker exploratory rows have one sample each. The 24-CPU row uses the library's
existing maximum of 16 render workers.

| Modal CPUs | Kenkui workers | Mean end-to-end | Mean render RTF | Request-based $ / audio hour | Interpretation |
| ---: | ---: | ---: | ---: | ---: | --- |
| 2 | 1 | 107.7 s | 0.571 | $0.093 | Current production policy |
| 1 | 1 | 155.3 s | 0.835 | $0.095 | Slower with no meaningful saving |
| 2 | 2 | 130.3 s | 0.665 | $0.114 | Process overhead loses on this workload |
| 4 | 4 | 67.7 s | 0.355 | $0.094 | Efficiency knee |
| 8 | 8 | 52.5 s | 0.271 | $0.126 | Faster, moderate premium |
| 16 | 16 | 46.2 s | 0.236 | $0.207 | Diminishing returns |
| 24 | 16 | **37.1 s** | **0.187** | $0.243 | Fastest tested production-compatible policy |

The 24/16 policy was 2.91 times as fast as current production end to end. Its
short-job, cold-start-inclusive estimate was 2.61 times the cost per finished
audio hour. Based on render RTF alone, one finished audio hour takes about 11.2
minutes of wall time and $0.225 of steady compute, compared with 34.3 minutes and
$0.091 for current production.

## Full Dune validation

Three configurations rendered the complete local Dune EPUB in isolated Modal
containers. The source contained 64 chapters and 1,189,736 normalized speech
characters. Its SHA-256 was
`b33ca040968a0c87cd2d7453719210ed30748bac330113c206be1f0e400b550a`.

| Modal CPUs | Kenkui workers | Wall time | Finished audio | Render RTF | Settled billed cost | Billed $ / audio hour |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 8 | 8 | 3h 22m 22s | 22h 02m 17s | 0.1529 | **$1.5460** | **$0.0702** |
| 16 | 16 | 2h 40m 14s | 21h 59m 47s | 0.1212 | $2.3632 | $0.1074 |
| 24 | 16 | **1h 56m 23s** | 22h 02m 55s | **0.0879** | $2.4535 | $0.1113 |

The 24/16 configuration finished 43m 51s sooner: **27.4% less wall time** and
1.38 times the throughput, for only **$0.0903 / 3.8% more actual billed cost**.
Startup and image overhead were naturally diluted by the full-book workload.

Compared with 16/16, 8/8 cost **$0.8173 / 34.6% less** and took 42m 08s
longer. Compared with the speed-first 24/16 tier, it cost **$0.9075 / 37.0%
less** and took 1h 25m 59s longer. The 8-core result confirms that fewer CPUs
are materially cheaper for Pocket-TTS; the extra cores buy latency rather than
lower total cost.

The settled memory rows were $0.3407 over 2.67 hours for 16/16 and $0.2457 over
1.94 hours for 24/16. At Modal's $0.008/GiB-hour rate, both imply about 16 GiB of
metered memory. The 8/8 run's $0.2608 over 3.37 hours implies about 9.7 GiB.
Every configuration therefore exceeded the 8 GiB request, benefited from
memory bursting, and was billed for it.

## Whole-book single-voice and multi-voice estimate

The Modal rows below are measured settled costs. The multi-voice additions are
provider-cost estimates; no matched multi-voice Dune synthesis run was made, so
they assume voice switching does not materially change Pocket-TTS throughput.

| Modal policy | Single voice, measured | Multi-voice as published | Multi-voice with reconciled spaCy roster |
| --- | ---: | ---: | ---: |
| 8 CPUs / 8 workers | **$1.5460** | $1.6415–$1.6534 | $1.6320–$1.6440 |
| 16 CPUs / 16 workers | $2.3632 | $2.4587–$2.4706 | $2.4493–$2.4612 |
| 24 CPUs / 16 workers | $2.4535 | $2.5490–$2.5609 | $2.5396–$2.5515 |

The Dune attribution prompts contain 589,737 input tokens. Encoding the 6,697
stored answers ranges from 85,251 tokens as compact JSON to 152,523 as pretty
JSON. At the queried DeepSeek V4 Flash route prices of $0.088606 per million
input tokens and $0.177212 per million output tokens, quote attribution is
therefore **$0.0674–$0.0793**.

The published server also uses the DeepSeek model to discover the character
roster chapter by chapter. Its exact Dune input is 294,725 tokens; using the
stored chapter rosters as an output-size proxy gives about 11,304 output tokens
and **$0.0281**. Together, published multi-voice model work is estimated at
**$0.0955–$0.1074**.

The intended reconciled design instead uses an offline spaCy roster plus two
GLM 5.3 Flash identity calls. The two recorded Dune calls cost $0.00670221 and
$0.01199085, or **$0.01869306 total**. With attribution, that makes the
multi-voice premium **$0.0861–$0.0980**. This is slightly cheaper than the
published path because the offline roster removes the estimated $0.0281 model
roster pass before adding $0.0187 of reconciliation.

The server currently constructs multi-voice jobs with `identity=None`, and its
DeepSeek model-roster path does not invoke the spaCy-only identity pass. Thus
the "as published" column is the accurate description of current behavior;
the reconciled column requires a pipeline configuration change.

## Worker-count sweep at 24 CPUs

| Kenkui workers | Samples | Mean end-to-end | Observed range | Request-based $ / audio hour |
| ---: | ---: | ---: | ---: | ---: |
| 12 | 3 | 39.5 s | 36.1–45.6 s | $0.258 |
| 16 | 4 | **37.1 s** | 31.0–46.0 s | **$0.243** |
| 20 | 3 | 41.8 s | 40.7–42.4 s | $0.274 |
| 24 | 3 | 47.6 s | 42.2–56.9 s | $0.313 |

The genuine 24-worker test temporarily raised `MAX_RENDER_WORKERS` only inside
the ephemeral benchmark function. Production code remained capped at 16.

## Run evidence

- Full CPU sweep: `ap-OaFdVxcWcJrVQ2r3Oz08qK`
- Production/4/8 repeats: `ap-7YHttfTPdsJuhMKlsXcHWi`
- 16/24 repeats with the production 16-worker cap: `ap-OQ4Gsz5FCzMANAbPU6SDn8`
- Genuine 24-worker repeats: `ap-Ozl3zc8WYBzeQi1vmvKEFY`
- 12/20-worker repeats at 24 CPUs: `ap-ei1tqx0N02VY7pSDMyMrq5`
- Full Dune at 16/16: `ap-QKIO9oNuMIjJ5OAIducljb`
- Full Dune at 24/16: `ap-eiihlcSquSvDcPX8lSMiVB`
- Full Dune at 8/8: `ap-Ih92DJiWXTPp90fiAXH6yx`

## Limits and next check

This workload intentionally exposes chapter parallelism, but its chapters are
short and uniform. Books with fewer than 16 chapters cannot use all 16 workers,
and a fixed 24-CPU request will still be billed at 24 CPUs. The experiment did
not capture peak RSS; full-book billing establishes that the 8 GiB request was
insufficient to cover actual memory use without bursting.

The full Dune result validates the high-end choice for a 64-chapter novel. A
separate representative test is still needed for books with fewer than 16
chapters, where a fixed 24-CPU reservation cannot expose the same parallelism.
