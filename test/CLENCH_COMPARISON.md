# Clench detector candidates

Evaluated 2026-09-26. **BioSPPy's default EMG onset detector is the strongest new
library candidate in this test; it has not demonstrated an improvement over our
current detector.** The MNE integration draft is paused and saved under ignored
recordings; the production detection source is unchanged.

## What was tested

Ran actual BioSPPy 2.2.4 default, Hodges–Bui and Solnik functions; NeuroKit2
0.2.13 mixture and BioSPPy-derived methods; the previous baseline-threshold NK
adapter; and the current detector on the same completed Taher session.
Also ran four separate synthetic cases: reference, one-fifth amplitude/noise,
four-times noise, and blink-associated temporal bursts. Constants were fixed
before inspecting these results, without tuning to each case.

Sources:
[BioSPPy's published onset algorithms](https://biosppy.readthedocs.io/en/main/api/signals/biosppy.signals.emg.html),
[BioSPPy implementation](https://biosppy.readthedocs.io/en/main/_modules/biosppy/signals/emg.html),
[NeuroKit activation implementation](https://neuropsychology.github.io/NeuroKit/_modules/neurokit2/emg/emg_activation.html).

These are muscle-activity methods, not pretrained Muse jaw-gesture classifiers.
BioSPPy default rectifies/smooths EMG and thresholds it; Hodges–Bui uses rest
statistics; Solnik uses an energy operator. NK mixture fits two amplitude
populations. The separate NeuroSkill project describes a 30–50 Hz envelope
thresholded at five times baseline, which is another threshold approach, not
evidence of a ready-made superior classifier. Its executable code was not tested.
[NeuroSkill method description](https://github.com/NeuroSkill-com/neuroskill/blob/main/METRICS.md#102-jaw-clench-detection)

## Taher's recording

| Candidate | Strict hits / 6 | Unmatched clench events | Clenches during 12 blink windows |
| --- | ---: | ---: | ---: |
| Current | 5* | 1* | 0 |
| BioSPPy default | 6 | 1 | 0 |
| BioSPPy Hodges–Bui | 6 | 6 | 0 |
| BioSPPy Solnik, energy baseline | 6 | 30 | 7 |
| NK baseline threshold (previous adapter) | 6 | 12 | 6 |
| NK mixture | 6 | 13 | 5 |
| NK BioSPPy-derived method | 5 | 21 | 11 |

\* Current emitted six clenches corresponding to the six prompts. The first
emission at 89.150 s is 37 ms later than the scoring window ending at 89.113 s,
so strict scoring counts the same timing discrepancy as a miss and an unmatched
event. This is not an absent clench or an unrelated extra trigger. BioSPPy's
extra event at 87.562 s precedes the first clench cue at 88.013 s; its physical
cause is not annotated. No replacement is proven better by these six trials.

Earlier tests of muse-vtuber missed all six clenches in both frame configurations.
LibMuse remains an untested licensed SDK option.

## Synthetic results and limits

All candidates found 3/3 clenches in each synthetic case. Reference and
one-fifth-amplitude cases had no extra clenches. In the increased-noise case,
NK's BioSPPy method emitted one extra and Hodges–Bui five; others emitted none.
**Every candidate, including current and BioSPPy default, emitted four false
clenches on the four synthetic contaminated blinks.** No tested candidate solves
that constructed confusion case by itself.

## Adapter policies that matter

- New candidates get separately filtered TP9/TP10 signals: offline fourth-order
  20–110 Hz bandpass plus 50/60 Hz notches, followed by a 200 ms cross-ear release
  deduplication. No raw channel averaging that could cancel opposing signals.
- NK mixture gets 200 ms RMS, default mixture probability and 80 ms minimum
  activation; the BioSPPy-derived NK method gets filtered EMG and an 80 ms minimum.
- BioSPPy default uses its full-recording amplitude statistics. NK mixture fits
  the full recording too. Both see test data when choosing activity thresholds,
  unlike the current detector's separately saved calibration.
- Hodges–Bui uses the marked rest interval, a 50 ms window (passed as samples
  because that implementation expects samples), and a threshold factor of six.
- Solnik receives **energy-domain** rest mean/SD, factor six, and an 80 ms duration
  in samples. This explicit adapter avoids comparing energy against amplitude
  statistics; it is not the package's raw-rest configuration.
- BioSPPy's `onsets` array includes rising AND falling transitions. The adapter
  emits one release event per activation and accounts for initial activity.
- The previous NK baseline adapter keeps its original preprocessing for an
  honest reference. Differences therefore compare complete adapter policies,
  not isolated mathematical methods with every other variable identical.
- All new candidates run offline. Output timestamps are estimated signal
  transitions; **none of these runtimes proves live response latency**. Long
  clenches, talking and chewing were not evaluated by this session.

## Reproduce

From the repo root, using the isolated comparison environment:

```powershell
.\test\.venv-compare\Scripts\python.exe -m pip install -r test/requirements-comparison.txt
.\test\.venv-compare\Scripts\python.exe test/compare_clenches.py --manifest test/recordings/20260926-204507-572018_taher-blink-session2_manifest.json --synthetic --output test/recordings/clench-candidates.json
.\test\.venv-compare\Scripts\python.exe -m pytest test -q -p no:cacheprovider
```

Any matching friend manifest can replace the Taher manifest. The JSON preserves
all emission times, strict per-action matches, warnings, exceptions and versions.
Raw data and the personal output JSON remain ignored; only the reusable code and
aggregate findings are tracked.

## Recommendation

Keep the current live clench detector while evaluating BioSPPy as the leading
alternative. To decide whether it helps the original missed-clench problem,
record one affected friend's own calibration plus the same actions. Use fixed
rules on that file; do not tune constants until that individual passes and call
it generalization. A live version would also need calibration-only or causal
threshold selection, release hysteresis and validation of LONG_CLENCH.

Correction to earlier blink interpretation: the user may have blinked naturally
during clench and movement phases without marking every blink. Those additional
MNE events are **unverified**, not established false detections. That uncertainty
does not label unexpected clench outputs as intentional jaw clenches.
