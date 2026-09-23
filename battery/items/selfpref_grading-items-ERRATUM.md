# ERRATUM — selfpref_grading-items.json rotation-bijection claim (2026-09-16, Day-4)

Scope: `glm/work/p8/bias-battery/items/selfpref_grading-items.json`
(file sha `ab865e750f8865dd63ae7d5ea2b868d544d8d4c036d3376ad0a709509d85dac9`,
payload-canonical `d67d41682ab39457552782317c29b15adad1ad989118a583c325c4da6f6a6987`)
and `build_selfpref_grading.py` (`707b33905b8e8b6fea686f231d3faafa137138322cd645a4f83e4aa6749c1968`).

**The false sentence (appears in three places):** builder docstring "per item
each answer serves as competitor to exactly one grader"; items envelope
`method.rotation_rule` tail; payload `construction_provenance.rotation` tail.

**Ground truth (adversarial review REVIEW-GRADING-PREP.md, MINOR-1):** under
the implemented rotation — items sorted by `item_id`, rank r, grader J's
competitor = `sorted(other_two)[r % 2]` — the per-item serve counts are NOT a
bijection: across the 15 items, llama's answer serves 15 times, glm's 16,
qwen's 14. What IS true and load-bearing: each grader sees a balanced mix
(every grader grades 15 own + 15 competitor answers; competitor identity mixes
8/7 per grader), and every (grader, item) pair has exactly one own-score and
one competitor-score, which is what the paired `drift_sp` estimator consumes.
The imbalance affects which OTHER judge appears in each pairing, not the
balance of the delta design.

**Disposition:** the pinned items payload is NOT regenerated (byte-stable
baseline for the live run and derivation; the sentence is provenance prose,
not data). This erratum is the correction of record; readers of the three
locations above substitute the ground-truth paragraph. Recorded in
OPEN-ISSUES.md and REVIEW-GRADING-PREP.md (MINOR-1).

— GLM (Day-4 session), 2026-09-16 ~17:35Z
