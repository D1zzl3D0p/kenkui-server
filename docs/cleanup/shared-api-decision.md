# Shared API decision

The supported near-term architecture remains Python Kenkui + Python local API/workers and TypeScript hosted API, with reviewed public contracts and capability negotiation. Studio remains TypeScript/Rust. The local adapter experiment is documented at `spikes/shared-local-api/report.md`; it is not a product entry point.

A2 produced a reproducible adapter evaluation but did not satisfy real-synthesis, active-lifecycle or packaging acceptance. A3 therefore selects the design's contract-only fallback and defers a replacement rollout plan. In particular, retaining the entire FastAPI app behind Node does not satisfy the intended simplification. No production local server replacement is authorized by this result. Retain the experiment as evidence; future implementation requires the narrower state/compute ports and an accepted package/lifecycle experiment.
