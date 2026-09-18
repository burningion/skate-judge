# Skateboard IMU research

Selected for **Judgy Skateboard**: a board-mounted accelerometer and gyroscope,
video-reviewed landed/failed attempts, and eventual spoken feedback after a
missed trick. Sources and download availability checked **2026-09-18**.

The closest hardware match is **Groh et al. (2015)**. Read its **2017 follow-up**
for failed attempts and false event detections, **Anlauff et al. (2010)** for
skateboard-triggered audio, and **Scholz et al. (2026)** for independent validation.

The central distinction is between **detecting an attempt**, **naming the trick**,
and **judging whether the rider landed and maintained control**. The reported
trick-recognition scores below do not establish make/bail/fall accuracy for this
project. In particular, an impact can locate board contact without proving that
the rider stayed on.

## Reading list and local copies

| Priority | Paper | Why it matters here | Copy |
| --- | --- | --- | --- |
| 1 | Groh et al., 2015 | Board-mounted six-axis IMU at 200 Hz; event detection and simple classifiers | [PDF](groh-2015-imu-based-trick-classification.pdf) |
| 2 | Groh et al., 2017 | Larger study includes incorrect attempts, bails, and false detections | Reference below; no public full PDF verified |
| 3 | Anlauff et al., 2010 | Real skateboard controls a game with audio feedback | Public author PDF linked below; download timed out |
| 4 | Scholz et al., 2026 | Independent field validation; exposes the gap between event detection and trick classification | [PDF](scholz-2026-skateboard-imu-validation.pdf) |
| 5 | Abdullah et al., 2021 | IMU signal classification with published data and code | Open-access links below; download unavailable from this environment |
| 6 | Ibrahim et al., 2020 | Small, interpretable feature-based baseline | Public university PDF linked below; download timed out |
| 7 | Corrêa et al., 2017 | Accelerometer-only neural-network approach, evaluated on synthetic signals | [PDF](correa-2017-accelerometry-machine-learning.pdf) |

The three saved PDFs are unmodified source files. Their original copyright and
license notices remain in the documents. A missing local copy does not necessarily
mean a paper is paywalled: the table distinguishes missing public full text from
download failures.

## 1. IMU-based Trick Classification in Skateboarding

**Benjamin H. Groh, Thomas Kautz, Dominik Schuldhaus, and Bjoern M. Eskofier.
2015.** KDD Workshop on Large-Scale Sports Analytics, Sydney, Australia.

[Local PDF](groh-2015-imu-based-trick-classification.pdf) ·
[University-hosted source PDF](https://www5.cs.fau.de/Forschung/Publikationen/2015/Groh15-ITC.pdf) ·
[Author's publication list](https://www.mad.tf.fau.de/person/benjamin-groh/)

- **Setup:** Board-mounted accelerometer/gyroscope at 200 Hz, ±16 g and
  ±2000 degrees/s; seven skateboarders, six tricks, and video/expert annotations.
  Our nominal 208 Hz logger is similar, with a wider ±32 g range.
- **Method:** Acceleration-energy thresholds locate impacts. Features span one
  second before through half a second after contact; several simple classifiers
  predict trick identity.
- **Evidence:** 94.2% event sensitivity and 97.8% best classification accuracy with
  leave-one-subject-out evaluation. Classification excluded false event detections;
  that score measures trick identity, not landing outcome.
- **Use here:** Sections 2.3–2.6 provide an event/feature baseline. Record mounting
  axes and stance: their preprocessing normalizes regular/goofy signals.

## 2. Classification and visualization of skateboard tricks using wearable sensors

**Benjamin H. Groh, Martin Fleckenstein, Thomas Kautz, and Bjoern M. Eskofier.
2017.** *Pervasive and Mobile Computing* **40**, 42–55.
DOI: [10.1016/j.pmcj.2017.05.007](https://doi.org/10.1016/j.pmcj.2017.05.007).

[Publisher article and preview](https://www.sciencedirect.com/science/article/pii/S1574119217302833)

- **Setup:** One skateboard-mounted inertial–magnetic unit; 11 skateboarders,
  11 tricks, and 905 performances, including correct and incorrect executions
  and bails.
- **Evidence:** The event detector found 872 of 905 attempts, with 431 false
  detections: 96.4% recall and 66.9% precision. Subsequent classification includes
  a rest class to reject non-trick events. The advertised 89.1% trick-classification
  accuracy concerns correctly performed tricks.
- **Use here:** This is the strongest match for collecting failures and background
  alongside makes. It illustrates why a high-recall impact detector needs a second
  stage before triggering a roast. The reported recognition result does not tell
  us how accurately our make/bail/fall labels can be predicted.
- **Limit/access:** Its nine-axis hardware includes a magnetometer that our
  LSM6DSO32 lacks. These notes use the publicly available abstract and preview;
  no public complete PDF was verified. The publisher offers institutional access
  or PDF purchase.

## 3. A Method for Outdoor Skateboarding Video Games

**Jan Anlauff, Erik Weitnauer, Alexander Lehnhardt, Stefanie Schirmer,
Sebastian Zehe, and Keywan Tonekaboni. 2010.** *Proceedings of the 7th International
Conference on Advances in Computer Entertainment Technology (ACE '10)*, 40–44.
DOI: [10.1145/1971630.1971642](https://doi.org/10.1145/1971630.1971642).

[Public author PDF at Bielefeld University](https://www.techfak.uni-bielefeld.de/ags/ami/publications/media/AnlauffWeitnauerLehnhardtSchirmerZeheTonekaboni2010-AMF.pdf)

- **Setup:** The Tilt'n'Roll prototype connects a sensor-equipped real skateboard
  to a mobile game. It collects acceleration, angular velocity, and foot-pressure
  information at approximately 70 Hz, and uses linear discriminant analysis for
  Ollie, Ollie 180, and a no-trick class.
- **Use here:** An unusually close precedent for the product idea: detected tricks
  drive game events, and the rider receives progress information through audio.
  Its pressure channel is also relevant if board motion alone proves ambiguous
  about whether the rider is still aboard.
- **Limit/access:** Its extra pressure sensors and small trick vocabulary differ
  from our setup; it does not validate make/bail/fall classification. The public
  author copy is indexed, but university and publisher downloads timed out here.

## 4. Validity of a Commercially Available Inertial Measurement Unit for Artificial Intelligence-Based Trick Detection and Kinematic Performance Assessment in Skateboarding

**Birte Scholz, Niklas Noth, Maren Witt, and Olaf Ueberschär. 2026.**
*Sensors* **26**(8), 2537.
DOI: [10.3390/s26082537](https://doi.org/10.3390/s26082537).

[Local PDF](scholz-2026-skateboard-imu-validation.pdf) ·
[Open-access article](https://www.mdpi.com/1424-8220/26/8/2537) ·
[Source PDF](https://mdpi-res.com/d_attachment/sensors/sensors-26-02537/article_deploy/sensors-26-02537.pdf)

- **Setup:** Independent Spinnax Freak validation with 23 skateboarders and
  reference measurements including video and light barriers.
- **Evidence:** Ollie/Kickflip event detection outperformed trick naming; airtime
  and other motion measurements showed substantial errors.
- **Critical limitation:** Section 2.3 explains that failed attempts were repeated
  or abandoned; all documented tricks were successfully landed. This does not
  validate bail/fall detection. The classifier is proprietary.
- **Use here:** Validate event detection, trick identity, and outcome separately
  against reviewed video before using a prediction to trigger audio.

## 5. The classification of skateboarding tricks via transfer learning pipelines

**Muhammad Amirul Abdullah, Muhammad Ar Rahim Ibrahim, Muhammad Nur Aiman
Shapiee, Muhammad Aizzat Zakaria, Mohd Azraai Mohd Razman, Rabiu Muazu Musa,
Noor Azuan Abu Osman, and Anwar P.P. Abdul Majeed. 2021.**
*PeerJ Computer Science* **7**, e680.
DOI: [10.7717/peerj-cs.680](https://doi.org/10.7717/peerj-cs.680).

[Open-access article](https://peerj.com/articles/cs-680/) ·
[PubMed Central full text](https://pmc.ncbi.nlm.nih.gov/articles/PMC8384043/) ·
[Publisher PDF](https://peerj.com/articles/cs-680.pdf) ·
[Code supplement](https://doi.org/10.7717/peerj-cs.680/supp-1) ·
[Dataset supplement](https://doi.org/10.7717/peerj-cs.680/supp-2)

- **Setup/method:** Six skateboarders perform five trick types. The six IMU
  channels become raw-signal images or continuous-wavelet-transform images;
  pretrained CNNs extract features for an optimized SVM. These are images of
  sensor signals, not camera frames.
- **Use here:** Compare learned representations with time-domain features. Raw
  data and code supplements support inspecting the collection format and
  reproducing a trick-identity baseline.
- **Limit/access:** Trick classes differ from our outcome classes. Check the data
  and evaluation splits before reuse; results do not establish failed-attempt
  recognition. Public PDF hosts timed out or blocked retrieval here. Supplements
  are linked, not imported into training sessions.

## 6. The Classification of Skateboarding Trick Manoeuvres: A K-Nearest Neighbour Approach

**Muhammad Ar Rahim Ibrahim, Muhammad Amirul Abdullah, Muhammad Nur Aiman
Shapiee, Mohd Azraai Mohd Razman, Rabiu Muazu Musa, Muhammad Aizzat Zakaria,
Noor Azuan Abu Osman, and Anwar P.P. Abdul Majeed. 2020.** In *Enhancing Health
and Sports Performance by Design*, Lecture Notes in Bioengineering, 341–347.
DOI: [10.1007/978-981-15-3270-2_36](https://doi.org/10.1007/978-981-15-3270-2_36).

[University repository record](https://umpir.ump.edu.my/id/eprint/33630/) ·
[Public PDF](https://umpir.ump.edu.my/id/eprint/33630/1/MAR1.pdf)

- **Setup/method:** One experienced amateur skateboarder performs five trick
  types on an IMU-equipped board. Features include mean, skewness, kurtosis,
  peak-to-peak, RMS, and standard deviation of acceleration and angular velocity.
  The best tested kNN configuration achieved 85% trick-classification accuracy.
- **Use here:** A compact feature list for an inexpensive first baseline before
  trying larger neural models.
- **Limit/access:** A single-rider preliminary study cannot establish generalization
  across riders, boards, or surfaces, and its target is trick identity. The
  university advertises a public PDF, but its download host timed out here.

## 7. Development of a skateboarding trick classifier using accelerometry and machine learning

**Nicholas Kluge Corrêa, Júlio César Marques de Lima, Thais Russomano, and
Marlise Araujo dos Santos. 2017.** *Research on Biomedical Engineering*
**33**(4), 362–369.
DOI: [10.1590/2446-4740.04717](https://doi.org/10.1590/2446-4740.04717).

[Local PDF](correa-2017-accelerometry-machine-learning.pdf) ·
[Journal record](https://www.rbejournal.periodikos.com.br/article/doi/10.1590/2446-4740.04717) ·
[arXiv record](https://arxiv.org/abs/2005.04186) ·
[Source PDF via arXiv export](https://export.arxiv.org/pdf/2005.04186)

- **Method:** Feed-forward neural networks classify five trick types using
  modeled acceleration signatures, with separate networks for individual axes.
- **Critical limitation:** The study uses **543 artificial axis signals representing
  181 simulated tricks**, not a dataset of 543 real attempts. Its reported scores
  therefore do not demonstrate recognition of actual makes, bails, or falls.
- **Use here:** Background on accelerometer signatures, preprocessing, and an
  accelerometer-only comparison. Prioritize real, reviewed recordings for our
  outcome model. Retaining gyro channels also allows an accelerometer-only versus
  six-axis comparison on the same held-out sessions.
- **Version:** The journal paper is from 2017; its arXiv deposit is from 2020.
  The downloaded PDF contains the journal article and its CC BY notice.

## Implications for this repo

These are proposed experiments, not claims that the papers validated our model.
They align with the existing [model plan](../docs/recording.md) and
[video-labeling workflow](../docs/trick-review.md).

1. **Keep outcome and trick identity separate.** Preserve `make`, `bail`, `fall`,
   `unknown`, and `background`. A binary landed/not-landed target can later map
   `make` versus `bail`/`fall`; do not silently map unknown or background to a miss.
2. **Keep the roll-away.** Pop/contact markers locate the action, while the full
   attempt window must include enough aftermath to judge control. Compare contact
   windows with windows that include post-contact motion.
3. **Measure the whole trigger pipeline.** Evaluate missed attempts and false
   roasts during continuous skating, including pushing, carrying, dropping the
   board, and ordinary rolling. Report outcome precision/recall, false triggers
   per minute, and delay after the outcome.
4. **Hold out sessions and riders.** Avoid mixing neighboring windows from one
   recording across training and test sets. Preserve mounting orientation, board,
   stance, surface, and sensor-quality metadata to investigate errors.
5. **Start with a small baseline and an uncertain result.** Compare simple
   acceleration/gyro features before heavier models. Tune the failure threshold
   and cooldown on validation sessions; ambiguous board motion should be allowed
   to produce no audio response.
