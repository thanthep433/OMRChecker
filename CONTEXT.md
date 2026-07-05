# OMR Exam Grading

Vocabulary for reading OMR answer sheets and feeding the resulting scores into
the Classwise Django app. Bridges two worlds: the OMRChecker engine's terms and
Classwise's grading terms. Integration design lives in `0003-omr-integration.md`.

## OMR engine

**Answer sheet**:
One physical/scanned page a student marks. The engine reads which bubbles are
filled; it does not inherently know whose sheet it is.
_Avoid_: form, paper, scan

**Template**:
The definition of where every bubble sits on an answer sheet, stored as
`template.json`. One template is reused across many sheets of the same layout.
_Avoid_: layout file, schema

**Field block**:
A group of bubbles in a template that reads one logical field (a question, or
one digit of a number), e.g. `QTYPE_MCQ5` or `QTYPE_INT`.
_Avoid_: question group, section

**Overlay**:
The annotated image the engine saves to `CheckedOMRs/` showing which bubbles it
detected — the evidence a human reviews.
_Avoid_: marked image, output image, debug image

**Marker**:
The corner registration marks used for perspective correction (the
`CropOnMarkers` preprocessor), essential for phone photos.
_Avoid_: fiducial, anchor, corner dot

## Integration (OMR ↔ Classwise)

**student_id block**:
A field block on the sheet where the student fills in their own student number,
so a sheet can be matched to a Classwise user. The linchpin of automation —
without it a teacher must pair each sheet to a name by hand.
_Avoid_: roll number, ID field

**Answer key**:
The correct answers for one gradable item, entered by the teacher inside
Classwise (not by hand-editing `evaluation.json`).
_Avoid_: solution, evaluation, key file

**Raw score**:
The count of correct answers the engine computes. Distinct from the **computed
score**, which is `raw / num_questions * GradeType.max_score`.
_Avoid_: mark, points (unqualified)

**Teacher review**:
The mandatory step where a teacher checks/corrects detected answers and matched
students on-screen before scores are written to the database. Never skipped —
phone-photo accuracy is only ~90%.
_Avoid_: verification, approval, confirmation (unqualified)
