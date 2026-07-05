"""Standalone Flask test UI for OMRChecker — reskinned as a Classwiser prototype.

Purpose (see ../0004-omr-prototype-ui.md): prove the OMR engine reads *your*
answer sheets and tune template.json, while doubling as a faithful visual
prototype of the future Django `omr` feature ("ตรวจข้อสอบ"). The teacher flow is
a 6-step wizard; steps that need Classwiser DB data (subject/gradebook, student
roster, answer-key library, saving scores) are structural placeholders, left
empty on purpose. The engine core (upload -> read -> overlay/answers/score) is real.

This is a throwaway dev tool — not production. Run locally:

    pip install -r requirements.txt -r requirements.web.txt
    python web_test/app.py
    # open http://127.0.0.1:5000

See ../0003-omr-integration.md for the real Django integration plan.
"""

from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

import runner

app = Flask(__name__)
app.secret_key = "omr-web-test-ui-dev-only"  # dev tool; not a secret

# Placeholder options for the subject / gradebook dropdowns. In Classwiser these
# come from the DB (subjects_for(user) + GradeType); here they are inert so the
# prototype shows the real shape without pretending to have data.
PLACEHOLDER_SUBJECTS = ["เคมี 3 — 5/1", "เคมี 3 — 5/2", "เคมี 4 — 6/1"]
PLACEHOLDER_GRADETYPES = ["สอบเก็บคะแนนครั้งที่ 1", "สอบเก็บคะแนนครั้งที่ 2", "สอบกลางภาค"]


def _default_template_id(templates):
    for t in templates:
        if t["id"].endswith("samples/yothin"):
            return t["id"]
    for t in templates:
        if t["id"].endswith("samples/sample1"):
            return t["id"]
    return templates[0]["id"] if templates else None


def _wiz():
    return session.setdefault("wiz", {})


# --------------------------------------------------------------------------- #
# Landing
# --------------------------------------------------------------------------- #
@app.route("/")
def landing():
    return render_template("landing.html", subjects=PLACEHOLDER_SUBJECTS)


# --------------------------------------------------------------------------- #
# Step 1 — subject + gradebook item + answer-sheet template
# --------------------------------------------------------------------------- #
@app.route("/new", methods=["GET", "POST"])
def step_context():
    templates = runner.list_templates()
    if request.method == "POST":
        template_id = request.form.get("template") or _default_template_id(templates)
        session["wiz"] = {
            "subject": request.form.get("subject", ""),
            "gradetype": request.form.get("gradetype", ""),
            "template_id": template_id,
            "answers": {},
        }
        return redirect(url_for("step_answer_key"))

    wiz = _wiz()
    return render_template(
        "step1_context.html",
        subjects=PLACEHOLDER_SUBJECTS,
        gradetypes=PLACEHOLDER_GRADETYPES,
        templates=templates,
        selected=wiz.get("template_id") or _default_template_id(templates),
    )


# --------------------------------------------------------------------------- #
# Step 2 — answer key (bubble grid) + answer-key library (placeholder)
# --------------------------------------------------------------------------- #
@app.route("/new/answer-key", methods=["GET", "POST"])
def step_answer_key():
    wiz = _wiz()
    template_id = wiz.get("template_id")
    if not template_id:
        return redirect(url_for("step_context"))

    fields = runner.question_fields(template_id)

    if request.method == "POST":
        answers = {q: request.form[q] for q in fields["questions"] if request.form.get(q)}
        save_to_library = bool(request.form.get("save_to_library"))
        library_name = (request.form.get("library_name") or "").strip()

        # Saving to the library requires a name (mirrors the client-side guard).
        if save_to_library and not library_name:
            flash("กรุณาตั้งชื่อเฉลยก่อนบันทึกเข้าคลัง", "error")
            return render_template(
                "step2_answer_key.html",
                fields=fields,
                answers=answers,
                template_id=template_id,
                save_to_library=True,
                library_name="",
            )

        wiz["answers"] = answers
        session["wiz"] = wiz
        if save_to_library:
            flash(f"บันทึกเฉลย “{library_name}” เข้าคลังแล้ว (ตัวอย่าง — ต่อจริงใน Django)", "info")
        return redirect(url_for("step_upload"))

    return render_template(
        "step2_answer_key.html",
        fields=fields,
        answers=wiz.get("answers", {}),
        template_id=template_id,
        save_to_library=False,
        library_name="",
    )


# --------------------------------------------------------------------------- #
# Step 3 — upload sheets (+ developer panel: template picker / JSON editor)
# --------------------------------------------------------------------------- #
@app.route("/new/upload")
def step_upload():
    wiz = _wiz()
    template_id = wiz.get("template_id")
    if not template_id:
        return redirect(url_for("step_context"))
    try:
        template_json = runner.read_template_json(template_id)
    except (ValueError, OSError) as exc:
        template_json = ""
        flash(str(exc), "error")
    return render_template(
        "step3_upload.html",
        templates=runner.list_templates(),
        template_id=template_id,
        template_json=template_json,
        n_questions=len(runner.question_fields(template_id)["questions"]),
    )


# --------------------------------------------------------------------------- #
# Step 4/5 — run the engine, then review per-sheet
# --------------------------------------------------------------------------- #
@app.route("/run", methods=["POST"])
def run():
    wiz = _wiz()
    template_id = request.form.get("template") or wiz.get("template_id")
    if not template_id:
        return redirect(url_for("step_context"))

    template_json = request.form.get("template_json", "")
    uploaded = [
        (f.filename, f.read())
        for f in request.files.getlist("images")
        if f and f.filename
    ]

    eval_override = None
    answers = wiz.get("answers") or {}
    if answers:
        fields = runner.question_fields(template_id)
        eval_override = runner.build_evaluation_json(fields["questions"], answers)

    try:
        result = runner.run_omr(
            template_id,
            uploaded,
            template_json_override=template_json,
            evaluation_json_override=eval_override,
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("step_upload"))
    except Exception as exc:  # noqa: BLE001 - surface any engine failure to the page
        flash(f"Run failed: {exc}", "error")
        return redirect(url_for("step_upload"))

    thai_map = dict(zip(runner.ENGINE_CHOICES, runner.THAI_CHOICE_LABELS))
    return render_template(
        "review.html", result=result, template_id=template_id, thai_map=thai_map
    )


# --------------------------------------------------------------------------- #
# Step 6 — confirm (structural; does NOT write scores in the prototype)
# --------------------------------------------------------------------------- #
@app.route("/confirm", methods=["GET", "POST"])
def confirm():
    wiz = _wiz()
    if request.method == "POST":
        session.pop("wiz", None)
        return render_template("done.html", subject=wiz.get("subject", ""))
    return render_template(
        "confirm.html",
        subject=wiz.get("subject", ""),
        gradetype=wiz.get("gradetype", ""),
        count=int(request.args.get("count", 0)),
    )


# --------------------------------------------------------------------------- #
# Developer helper: persist an edited template.json (templates_data/ only)
# --------------------------------------------------------------------------- #
@app.route("/save-template", methods=["POST"])
def save_template():
    template_id = request.form.get("template", "")
    template_json = request.form.get("template_json", "")
    try:
        runner.save_template_json(template_id, template_json)
        flash("บันทึก template.json แล้ว", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("step_upload"))


@app.route("/runs/<run_id>/<path:relpath>")
def run_file(run_id, relpath):
    """Serve overlay images and result CSVs produced by a run."""
    run_dir = (runner.RUNS_DIR / run_id).resolve()
    if not str(run_dir).startswith(str(runner.RUNS_DIR.resolve())) or not run_dir.is_dir():
        abort(404)
    as_attachment = request.args.get("download") == "1"
    return send_from_directory(run_dir, relpath, as_attachment=as_attachment)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
