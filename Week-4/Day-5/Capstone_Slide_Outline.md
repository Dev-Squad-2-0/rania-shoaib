# Slide Outline — Predicting Income >$50K (5–7 min presentation)

## Slide 1: Title & Business Goal
- Predicting >$50K earners for a targeted outreach program
- Precision-first: false positives waste real outreach dollars (Day 1 cost framing)

## Slide 2: Data & Approach
- UCI Adult/Census Income dataset, ~48.8K records, stratified 70/12.5/17.5 split
- Engineered features: education×hours interaction, capital-gain/loss indicators, age/hours buckets
- Final model: tuned Histogram Gradient Boosting, isotonic-calibrated

## Slide 3: Model Comparison (Task 1)
| Model | Precision | Recall | ROC AUC | Inference (ms/row) |
|---|---|---|---|---|
| **HGB (final)** | **0.8440** | 0.5205 | 0.9173 | 0.0361 |
| Random Forest (tuned) | 0.8134 | 0.5073 | 0.9055 | 0.0119 |
| Logistic Regression | 0.7927 | **0.5218** | 0.9102 | **0.0052** |
- HGB wins on precision (our primary metric) and PR-AUC
- LogReg edges HGB on recall and is ~7x faster — noted as a lightweight alternative if latency ever matters
- RF underperforms both on nearly every metric
- Headline: HGB retained — wins on the metric that matters most for this business objective

## Slide 4: Handling Class Imbalance
- Baseline vs. class_weight vs. oversampling vs. SMOTE — all have equivalent Average Precision (0.79–0.80)
- Resampling only shifts the default threshold, not underlying model quality
- Decision: keep the simple baseline, tune the threshold instead (t=0.57)

## Slide 5: What Drives the Model (Interpretability)
- Top 8 features (permutation importance + SHAP agree): capital-gain, marital status, education×hours,
  capital-loss, age, education-num, occupation, workclass
- One-line story per top feature (marital status = single cleanest signal; capital-gain = high-impact
  for a small subset)

## Slide 6: Individual Case Studies
- True positive: correctly confident, married professional, long hours
- False positive: same profile but reduced hours (semi-retired) — model missed the hours signal
- False negative: stable government manager, but lower formal education pulled the score down

## Slide 7: Fairness Findings
- Sex: precision favors women, but recall is 15 points lower for women — model under-flags women
- Race: Asian-Pac-Islander group has notably lower precision despite similar base rate
- Proposed mitigations: group-aware thresholds, audit marital-status as a sex proxy, monitor by group

## Slide 8: Deployment & Monitoring
- Inference script: validated inputs, returns probability + class + top-3 SHAP features, unit-tested
- Monitoring: data drift (weekly), score distribution (daily), precision/recall by group (rolling)
- Retraining: every 6 months, or immediately on alert triggers

## Slide 9: Recommended Next Steps
- A/B test model-driven vs. current outreach targeting for 4–6 weeks
- Fairness remediation pilot before next production release
- Ongoing per-group monitoring, not just aggregate metrics

## Slide 10: Questions
