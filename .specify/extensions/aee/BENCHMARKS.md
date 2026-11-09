# Benchmarks and gated releases

Established upstream tasks and labels are maintained in the
[benchmark repository](https://github.com/electrohire/spec-kit-aee-benchmark).
Read its [Mermaid harness/results guide](https://github.com/electrohire/spec-kit-aee-benchmark/blob/main/docs/benchmark-harness.md)
and [release policy](https://github.com/electrohire/spec-kit-aee-benchmark/blob/main/docs/benchmark-release-gates.md).

The completed model-only HaluEval/RAGTruth subset results are83.3%/80.8%. They do
NOT establish an AEE or Spec Kit benefit. Full matched treatment and established
FeatureBench/SWE-EVO economic results remain pending. Generic test failures and
AEE findings are not independent hallucination labels; token savings require
retained independently correct work. No100%benchmark-coverage target.

The Benchmark candidate workflow automatically builds changed trusted-main code
or accepts a manual rebuild. It creates an immutable distribution bundle plus a
source-SHA/content-hash request. Hosted CI does not invoke a model. The local
worker consumes admitted requests serially after its active study; execution
requires the accepted matched baseline and complete candidate/grader integration.
A queued request is not benchmark success. No accepted release baseline exists yet.

Promotion is manual workflow dispatch on main with the successful candidate build
run ID and new version tag. It verifies authenticated Actions source metadata before
artifact download, checks bundle/distribution hashes and the literal source version,
then requires the protected benchmark release gate. The same verified distributions
are published only after complete audited exact-build approval. Missing evidence,
incomplete grades/usage, a gross regression or inconclusive noninferiority blocks
release. Small changes within declared margins can pass; clear improvements with
quality intact are reported as good news. Policy uncertainty is not a claim of
universal equivalence. See the benchmark policy for tolerances and limitations.

No version, tag, release, PyPI upload, new credential, model/service change or private
unpublished feature is created by adding these hooks. Engine publication retains
its pypi environment and short-lived OIDC credentials. Only the controlled workflow
publishes distributions; manually creating a GitHub release cannot trigger an
unguarded PyPI publish. Repository owners can override workflows or publish tags
manually; this is not a claim of protection from administrator override. Existing
branch/tag/integrity/CI/review controls are preserved.
