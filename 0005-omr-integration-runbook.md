# Runbook 0005 — เชื่อม OMRChecker เข้ากับ Classwise (Django)

> **นี่คือ runbook สำหรับงานแรกของการ integration** — ทำทีละเฟส เฟสหนึ่งจบและ "พิสูจน์ว่าใช้ได้จริง" ก่อนขึ้นเฟสถัดไป
> เป้าหมาย: ครูอัปโหลดรูปกระดาษคำตอบ → OMR อ่าน → ครูตรวจทาน → คะแนนไหลเข้า `grades` อัตโนมัติ
>
> เอกสารนี้คือแผน "ลงมือทำ" ของ ADR [`0003-omr-integration.md`](0003-omr-integration.md) (source of truth ของดีไซน์)

---

## Context — ทำไปทำไม

ตอนนี้ OMRChecker (CLI Python + OpenCV) อ่านกระดาษคำตอบได้แม่นแล้ว (เฟส 0 ผ่าน — พิสูจน์ด้วย `web_test/` Flask) ขั้นต่อไปคือย้ายความสามารถนี้เข้า **Classwise** (Django web app, repo แยกที่ `../Classwise/`) ให้ครูใช้งานผ่านเว็บ แล้วคะแนนไหลเข้าแอป `grades` ที่มีอยู่ โดย **ไม่แตะ logic คะแนนเดิม** และ **ไม่รัน OMR ใน web request** (กิน CPU หนัก — ต้องเข้าคิว)

## สองรีโปที่เกี่ยวข้อง (สำคัญ)

| รีโป | path | บทบาท |
|------|------|-------|
| **OMRChecker** (รีโปนี้) | `d:\Cubic World\Claude_Workspace\OMRChecker` | engine อ่านกระดาษ — **ไม่แก้โค้ด engine** ใช้เป็น subprocess |
| **Classwise** (รีโปเป้าหมาย) | `d:\Cubic World\Claude_Workspace\Classwise` | Django app — **โค้ดใหม่ทั้งหมดอยู่ที่นี่** (แอป `omr` ใหม่) |

> โค้ด Django ทุกบรรทัดใน runbook นี้เขียนใน `Classwise/` งานในรีโป `OMRChecker/` มีแค่ "อย่าแก้ ให้ engine นิ่ง + ยืนยันว่า template มาตรฐานถูกต้อง"

## การตัดสินใจที่ล็อกแล้ว (จาก grilling)

1. **ขอบเขต:** ทำครบทั้ง 4 เฟส แต่ละเฟส self-contained + ทดสอบได้เอง
2. **Engine อยู่ที่ไหน:** รีโปแยก + **venv ของตัวเอง** — Django เรียกผ่าน `settings.OMR_ENGINE_DIR` + `OMR_PYTHON` (opencv/pymupdf ไม่ปนกับ web) → **ADR 0002** ใน Classwise
3. **กุญแจ matching:** `student_id` 5 หลักบนกระดาษ → `User.student_id` ตรงๆ **แต่ค้นเฉพาะใน roster ของวิชานั้น** (`get_roster`) นอก roster = `UNMATCHED`
4. **เฉลย:** มี **คลังเฉลย** ตั้งแต่แรก — `AnswerKey` เป็น entity อิสระ นำมาผูกกับ `GradeType` ตอนสร้างงานตรวจ
5. **เก็บรูป:** `source_image` + `overlay_image` เก็บถาวรใน MEDIA (หลักฐาน) — cleanup ลบแค่ temp dir ของ subprocess
6. **Redis ตอน dev:** Memurai (Windows-native)
7. **ประตูยืนยัน:** ปุ่ม "ยืนยันบันทึก" ใช้ได้ก็ต่อเมื่อ **ทุกแผ่น resolved (MATCHED) ครบ** — เขียนแบบ all-or-nothing ใน transaction เดียว
8. **คิว:** `django-rq` (ไม่ใช่ Celery) worker 1 process + `nice` → **ADR 0002 (คู่กับข้อ 2)**

## Golden sheet — ของกลางสำหรับทดสอบทุกเฟส

ก่อนเริ่ม เตรียม **1 กระดาษคำตอบที่รู้คำตอบแน่นอน** ไว้เป็น regression fixture ใช้ซ้ำทุกเฟส:
- ใช้ `samples/yothin/doc-60-ans.pdf` (มีอยู่แล้ว) หรือถ่ายรูปจริง 1 แผ่น
- จดค่าที่ "ถูกต้อง" ลงไฟล์: `student_id` ที่ฝน, คำตอบ q1..60, และคะแนนดิบที่คาดหวังเทียบเฉลย
- ทุกเฟสต้องอ่าน golden sheet นี้ออกมาได้ค่าเดิมเป๊ะ — ถ้าเพี้ยนแปลว่าเฟสนั้นพัง

---

# เฟส 0 — ตรึง engine + template มาตรฐาน (ในรีโป OMRChecker)

**เป้า:** ยืนยันว่า engine + `samples/yothin/template.json` อ่าน golden sheet ถูกต้อง และ freeze ไว้เป็น "กระดาษมาตรฐาน"

### ขั้นตอน
1. สร้าง venv เฉพาะของ engine (แยกจาก Classwise):
   ```powershell
   cd "d:\Cubic World\Claude_Workspace\OMRChecker"
   python -m venv .venv-omr
   .\.venv-omr\Scripts\Activate.ps1
   pip install -r requirements.txt
   pip install opencv-python        # ไม่อยู่ใน requirements.txt โดยตั้งใจ (ดู CLAUDE.md)
   ```
2. รัน engine กับ golden sheet ลง output dir ใหม่ (กัน CSV ทับ):
   ```powershell
   python main.py -i samples\yothin -o outputs\yothin_check
   ```
3. เปิด `outputs\yothin_check\...\Results\Results_*.csv` — คอลัมน์ `student_id`, `q1..q60`, `score`

### วิธีทดสอบว่าถูกต้อง ✅
- คอลัมน์ `student_id` = ค่าที่ฝนจริงบน golden sheet
- `q1..q60` ตรงกับที่ฝน (สุ่มเช็ค 5–10 ข้อกับตาเปล่าเทียบ overlay ใน `CheckedOMRs/`)
- `score` ตรงกับที่คำนวณมือเทียบ `evaluation.json`
- จด path + ค่าเหล่านี้ไว้เป็น "ค่าอ้างอิง golden" สำหรับเฟสถัดไป

### Freeze
คัดลอก 2 ไฟล์นี้เป็น asset มาตรฐานสำหรับ Classwise (ทำในเฟส 1): `samples/yothin/template.json`, `samples/yothin/omr_marker.jpg`

---

# เฟส 1 — เชื่อม synchronous (แอป `omr` + อัปโหลด → subprocess → แสดงผลบนจอ)

**เป้า:** สร้างแอป Django `omr` ที่ครูอัปโหลดกระดาษได้ → เรียก engine เป็น subprocess → แสดง overlay + ค่าที่อ่านได้บนจอ **ยังไม่มีคิว ยังไม่เขียน DB**

> เฟสนี้คือ "ยกตรรกะ `web_test/runner.py` เข้ามาเป็น service ของ Django" — โค้ดพิสูจน์แล้วว่าใช้ subprocess boundary เดียวกันได้

### 1.1 สร้างโครงแอป
```powershell
cd "d:\Cubic World\Claude_Workspace\Classwise"
.\venv\Scripts\Activate.ps1
python manage.py startapp omr
```
โครงไฟล์เป้าหมายในโฟลเดอร์ `omr/`:
```
omr/
├── apps.py            # ← ตั้ง name = "omr"
├── engine_assets/     # ← ไฟล์มาตรฐานจากเฟส 0
│   ├── template.json
│   └── omr_marker.jpg
├── engine_runner.py   # ← ยกตรรกะ subprocess จาก web_test/runner.py
├── services.py        # ← จุดเขียนข้อมูลเดียว
├── forms.py
├── views.py
├── urls.py
├── models.py          # ← เฟส 1 ยังว่าง (ยังไม่เขียน DB)
└── templates/omr/
    ├── upload.html
    └── result.html
```

### 1.2 ลงทะเบียนแอป + settings
- `classwise/settings/base.py` — เพิ่ม `"omr"` ท้าย `LOCAL_APPS` (บรรทัด ~45-56)
- เพิ่มบล็อก OMR ใน `base.py`:
  ```python
  # OMR engine (รีโปแยก + venv ของตัวเอง เรียกเป็น subprocess)
  OMR_ENGINE_DIR = env("OMR_ENGINE_DIR", default=str(BASE_DIR.parent / "OMRChecker"))
  OMR_PYTHON = env("OMR_PYTHON", default=str(BASE_DIR.parent / "OMRChecker" / ".venv-omr" / "Scripts" / "python.exe"))
  OMR_SUBPROCESS_TIMEOUT = env.int("OMR_SUBPROCESS_TIMEOUT", default=600)
  ```
- `.env.example` + `.env` — เพิ่ม `OMR_ENGINE_DIR=` และ `OMR_PYTHON=` (dev ปล่อยว่างให้ใช้ default ได้)
- `classwise/urls.py` — เพิ่ม `path('omr/', include('omr.urls')),` (หลังบรรทัด grades, ~27)

### 1.3 `omr/engine_runner.py` — ยกจาก `web_test/runner.py`
ก๊อป logic เหล่านี้จาก `web_test/runner.py` (พิสูจน์แล้ว) แล้วปรับ:
- `_force_headless()` — **ต้องมี** (บังคับ `show_image_level: 0` กัน cv2.imshow ค้าง) — ยกทั้งฟังก์ชัน
- `_copy_template_assets()` / `_copy_relative()` — ก๊อปมา แต่ต้นทาง template = `omr/engine_assets/`
- `build_evaluation_json()` — ก๊อปมาตรงๆ (สร้าง evaluation.json จาก dict เฉลย) จะได้ใช้ในเฟส 3
- ฟังก์ชันหลักใหม่ `run_engine(image_paths, answers=None) -> dict`:
  - สร้าง temp dir (`tempfile.mkdtemp`), ก๊อป `template.json` + `omr_marker.jpg` + (ถ้ามี answers) `evaluation.json`
  - วางรูปที่อัปโหลด, เรียก
    `subprocess.run([settings.OMR_PYTHON, "main.py", "-i", in_dir, "-o", out_dir], cwd=settings.OMR_ENGINE_DIR, capture_output=True, text=True, timeout=settings.OMR_SUBPROCESS_TIMEOUT)`
  - อ่าน `Results/Results_*.csv` กลับ (logic `_collect_results` เดิม) → คืน list ของ dict ต่อแผ่น: `{file_id, student_id, answers{q1..}, score(raw), overlay_path}`
  - **ต่างจาก web_test:** คืน `overlay_path` เป็น path ในดิสก์ (ไม่ใช่ URL) ให้ service ชั้นบนจัดการเก็บลง MEDIA เอง

### 1.4 View + form + template (เฟส 1: แสดงผลเฉยๆ)
- `omr/forms.py` — `OMRUploadForm` (MultipleFileField รับหลายรูป/PDF) ลอกสไตล์จาก `students/forms.py` `StudentImportForm`
- `omr/views.py` — 1 view `omr_try(request)` ครอบ `@teacher_or_admin_required` (import จาก `accounts.decorators`):
  - GET → render `upload.html`
  - POST → เซฟรูป temp → `engine_runner.run_engine(paths)` → ก๊อป overlay ไป `MEDIA_ROOT/omr/tmp/` → render `result.html` แสดงตารางค่าที่อ่านได้ + `<img>` overlay
- `omr/urls.py` — `app_name = "omr"`, `path("try/", views.omr_try, name="try")`
- template สืบ `manage_base.html` (base เดิมของหน้าจัดการ) — เช็คชื่อจริงจาก `templates/students/student_import.html`

### วิธีทดสอบว่าถูกต้อง ✅
1. **Unit test engine boundary** — `omr/tests.py`:
   ```python
   def test_run_engine_reads_golden_sheet(self):
       rows = run_engine([GOLDEN_SHEET_PATH])
       self.assertEqual(rows[0]["student_id"], EXPECTED_ID)   # ค่าจากเฟส 0
       self.assertEqual(rows[0]["answers"]["q1"], EXPECTED_Q1)
   ```
   รัน: `python manage.py test omr`
2. **Manual end-to-end:** `python manage.py runserver` → ล็อกอินครู → `/omr/try/` → อัปโหลด golden sheet → หน้า result ต้องโชว์ overlay + `student_id`/คำตอบ **ตรงกับค่า golden จากเฟส 0**
3. **Regression:** ค่าที่จอแสดง = ค่าเดิมเป๊ะจากเฟส 0 (ถ้าเพี้ยน = engine_runner ยกมาผิด)

**เสร็จเฟส 1 เมื่อ:** อัปโหลดผ่านเว็บแล้วเห็น overlay + ค่าอ่านถูก โดยยังไม่แตะ DB

---

# เฟส 2 — ใส่คิว `django-rq` + polling

**เป้า:** ย้าย subprocess ไปหลังคิว worker (1 process, `nice`) หน้าเว็บโชว์ "กำลังประมวลผล..." แล้ว AJAX poll สถานะ

### 2.1 ติดตั้ง Redis (Memurai) + django-rq
1. ติดตั้ง Memurai (Developer edition) — รันเป็น Windows service ที่ `localhost:6379` ตรวจ: `memurai-cli ping` → `PONG`
2. `Classwise/requirements.txt` — เพิ่ม `django-rq>=2.10` แล้ว `pip install -r requirements.txt`
3. `base.py` เพิ่ม:
   ```python
   RQ_QUEUES = {
       "omr": {"HOST": env("REDIS_HOST", default="127.0.0.1"),
               "PORT": env.int("REDIS_PORT", default=6379),
               "DB": 0, "DEFAULT_TIMEOUT": 900},
   }
   ```
   และเพิ่ม `"django_rq"` ใน `LOCAL_APPS` (หรือ THIRD_PARTY) + `path("django-rq/", include("django_rq.urls"))` ใน urls (หน้า dashboard คิว, ครอบ admin-only)

### 2.2 แปลง flow เป็น async — ต้องมี model ขั้นต่ำแล้ว
เฟสนี้เริ่มต้องเก็บสถานะงาน → สร้าง model `OMRJob` + `OMRSheetResult` (โครงเต็มดูหัวข้อ "โมเดล" ในเฟส 3 แต่เฟส 2 ใช้แค่ field สถานะ/ผลดิบ ยังไม่ต้องมี `answer_key`)
- `omr/tasks.py` — `def process_job(job_id):` โหลด job → `run_engine()` → เซฟ `OMRSheetResult` ต่อแผ่น (source_image, overlay_image เข้า MEDIA) → set `job.status = DONE`; ถ้า error → `FAILED` + `error_message`
- `omr/services.py` — `enqueue_job(job)`:
  ```python
  import django_rq
  django_rq.get_queue("omr").enqueue("omr.tasks.process_job", job.id)
  ```
- view POST เปลี่ยนเป็น: สร้าง `OMRJob(status=PENDING)` → `enqueue_job` → redirect ไปหน้า `job_detail` ที่โชว์ spinner
- endpoint polling `omr/views.py::job_status(request, pk)` คืน `JsonResponse({"status": job.status})` — ลอก pattern polling จาก `notifications` (กระดิ่งแจ้งเตือน) — JS `setInterval` fetch ทุก 2–3 วิ จนสถานะ `DONE`/`FAILED`

### 2.3 รัน worker (dev)
```powershell
# หน้าต่างที่ 2 (แยกจาก runserver)
cd "d:\Cubic World\Claude_Workspace\Classwise"; .\venv\Scripts\Activate.ps1
python manage.py rqworker omr
```
> prod: systemd unit รัน `rqworker omr` ด้วย `Nice=10` — บันทึกใน DEPLOY (เฟสหลัง)

### วิธีทดสอบว่าถูกต้อง ✅
1. **Worker test (RQ sync/burst):** ใน `omr/tests.py` ใช้ `django_rq.get_queue("omr", is_async=False)` หรือ enqueue แล้วรัน `worker.work(burst=True)` → ยืนยัน job ไปจบ `DONE` และมี `OMRSheetResult` ครบตามจำนวนแผ่น ค่าตรง golden
2. **สถานะไหลถูก:** อัปโหลด → หน้า detail ขึ้น "กำลังประมวลผล..." → (worker ทำงาน) → polling เปลี่ยนเป็นตารางผล **โดยไม่ต้อง refresh มือ**
3. **เว็บไม่หน่วง:** ระหว่าง worker ประมวลผล เปิดหน้าอื่น (เช่น `/students/`) ต้องตอบเร็วปกติ (พิสูจน์ว่า OMR ไม่บล็อก web process)
4. **Error path:** อัปโหลดไฟล์ขยะ (ไม่ใช่รูป) → job `FAILED` + `error_message` โผล่บนจอ ไม่ใช่ 500

**เสร็จเฟส 2 เมื่อ:** อัปโหลด → เข้าคิว → worker ทำ → จอ update เอง; เว็บไม่หน่วง

---

# เฟส 3 — เฉลย (คลังเฉลย) + ตรวจทาน + บันทึกคะแนน (ปิด loop)

**เป้า:** ครูสร้าง/เลือกเฉลยจากคลัง → จับคู่ student_id → หน้าตรวจทานแก้ได้ → กด "ยืนยัน" → เขียน `Score` ผ่าน `bulk_record_scores`

### 3.1 โมเดลเต็ม (`omr/models.py`)
> scope ทุก query ผ่าน `subjects_for(user)` เหมือนทุกแอป

- **`AnswerSheetTemplate`** — กระดาษมาตรฐาน (เฟสแรกมี record เดียว) : `name`, `num_questions=60`, `num_choices=4`, `student_id_digits=5`, `template_json` (TextField/JSON), asset marker
- **`AnswerKey`** (คลังเฉลย, entity อิสระ reusable) : `name`, FK `template`, `answers` (JSON `{"1":"A",...}` — เก็บเป็น engine choice A–D), `created_by`, timestamps
- **`OMRJob`** (งานตรวจ 1 ครั้ง) : FK `subject`, FK `grade_type`, FK `answer_key`, FK `template`, `uploaded_by`, `status` (`PENDING`/`PROCESSING`/`DONE`/`FAILED`/`REVIEWED`), `error_message`, timestamps
- **`OMRSheetResult`** (ผลรายแผ่น) : FK `job` (`related_name="results"`), `source_image` (FileField), `overlay_image` (FileField), `detected_student_id`, FK `matched_student`(User,null), `detected_answers`(JSON), `raw_score`(int), `computed_score`(Decimal,null), `status` (`MATCHED`/`UNMATCHED`/`NEEDS_REVIEW`/`CONFIRMED`)

`python manage.py makemigrations omr && python manage.py migrate`

### 3.2 คลังเฉลย + หน้ากรอกเฉลย
- CRUD `AnswerKey` : list/create/edit ครอบ `teacher_or_admin_required` — grid กรอกเฉลย 60 ข้อ × ก-ง (แสดงไทย ก/ข/ค/ง แต่เก็บ A/B/C/D) ลอก mapping `THAI_CHOICE_LABELS`/`ENGINE_CHOICES` จาก `web_test/runner.py:26`
- ตอนสร้างงานตรวจ ครู "เลือกเฉลยจากคลัง" (dropdown `AnswerKey` ของตัวเอง) → ผูกเข้า `OMRJob`

### 3.3 Matching + คำนวณคะแนน (`omr/services.py`)
- `match_students(job)` — สำหรับแต่ละ result:
  ```python
  roster = {u.student_id: u for u in get_roster(job.subject)}   # จาก grades.services
  student = roster.get(result.detected_student_id)
  result.matched_student = student
  result.status = "MATCHED" if student else "UNMATCHED"
  ```
  เคสพิเศษ: `detected_student_id` ซ้ำกันในงานเดียว → mark ทั้งคู่ `NEEDS_REVIEW`
- `compute_scores(job)` :
  ```python
  from decimal import Decimal, ROUND_HALF_UP
  computed = (Decimal(result.raw_score) / job.template.num_questions
              * job.grade_type.max_score).quantize(Decimal("0.01"), ROUND_HALF_UP)
  ```
- `apply_to_grades(job)` — เขียนจริง (เรียกตอนกดยืนยัน):
  ```python
  scores = {r.matched_student: r.computed_score
            for r in job.results.filter(status="CONFIRMED")}
  bulk_record_scores(grade_type=job.grade_type, scores=scores, recorded_by=job.uploaded_by)
  job.status = "REVIEWED"
  ```
  > ใช้ `grades.services.bulk_record_scores` (grades/services.py:74) ตรงๆ — ไม่เขียน logic คะแนนใหม่ (มันกันเกิน max_score + upsert ให้แล้ว)

### 3.4 หน้าตรวจทาน (ลอก pattern preview→confirm จาก students import)
โครงเดียวกับ `students/views.py::student_import` + `student_import_confirm` (students/views.py:94):
- หน้า `job_review` : ตารางรายแผ่น — overlay, `detected_student_id`, dropdown เลือกนักเรียนจาก roster (แก้ UNMATCHED มือได้), คำตอบที่อ่าน (แก้ได้), `computed_score`
- ครูแก้ → บันทึกลงตัว result (status → `CONFIRMED`)
- **ประตูยืนยัน:** ปุ่ม "ยืนยันบันทึกคะแนน" **disabled จนกว่า** `job.results.exclude(status="CONFIRMED").count() == 0` (ทุกแผ่นต้อง resolved ครบ)
- กดยืนยัน (POST) → `apply_to_grades(job)` ใน `transaction.atomic()` (all-or-nothing) + server-side guard เช็คซ้ำก่อนเขียน

### 3.5 Cleanup command
`omr/management/commands/omr_cleanup.py` — ลบ temp dir subprocess ที่ค้างเกิน N ชม. (ลอก pattern `cleanup_pending_attachments`) — **ไม่ลบ** `source_image`/`overlay_image` ใน MEDIA (เก็บถาวร)

### วิธีทดสอบว่าถูกต้อง (สำคัญสุด — คะแนนกระทบเด็ก) ✅
1. **Unit: compute** — raw 40/60, `max_score=20` → computed `13.33` (เทียบมือ). เช็ค ROUND_HALF_UP
2. **Unit: matching** — สร้าง roster ปลอม + result ที่ `detected_student_id` (ก) อยู่ใน roster → MATCHED, (ข) ไม่อยู่ → UNMATCHED, (ค) ซ้ำ → NEEDS_REVIEW
3. **Unit: apply** — เรียก `apply_to_grades` แล้ว query `Score.objects.get(grade_type=..., student=...)` → ค่าตรง `computed_score`; ยืนยันเรียก `bulk_record_scores` จริง (ไม่เขียน Score เอง)
4. **ประตูยืนยัน** — job ที่มีแผ่น UNMATCHED 1 แผ่น → endpoint ยืนยันต้อง **ปฏิเสธ** (ปุ่ม disabled + server-side guard) แก้ครบแล้วค่อยยืนยันได้
5. **End-to-end (golden):** สร้าง GradeType จริง + roster ที่มี student_id ของ golden sheet + AnswerKey จากเฉลยจริง → อัปโหลด golden → ตรวจทาน → ยืนยัน → เปิดหน้า **สรุปคะแนน grades เดิม** ต้องเห็นคะแนนโผล่ถูกคน ถูกค่า
6. **ไม่เกินเต็ม:** ลองกรณี raw = จำนวนข้อเต็ม → computed = max_score พอดี ไม่ raise `ScoreOverMax`

**เสร็จเฟส 3 เมื่อ:** อัปโหลด → ตรวจทาน → ยืนยัน → คะแนนโผล่ในหน้า grades/portal นักเรียน อัตโนมัติ ครบ loop

---

## เอกสาร domain model ที่ต้องเขียนระหว่างทำ (domain-modeling)

- **`Classwise/CONTEXT.md`** — เพิ่มคำ: *Gradable item* (= `GradeType`), *Sheet match status* (MATCHED/UNMATCHED/NEEDS_REVIEW/CONFIRMED), และกฎ *raw → computed* (`raw/num_questions × max_score`, ROUND_HALF_UP) — sync กับ `OMRChecker/CONTEXT.md` ที่มีคำ OMR อยู่แล้ว
- **`Classwise/docs/adr/0002-omr-engine-and-queue.md`** — บันทึก 2 การตัดสินใจ: engine เป็นรีโปแยก + venv ของตัวเอง (เรียก subprocess ผ่าน settings), และเลือก `django-rq` แทน Celery (เหตุผล + ทางเลือกที่ไม่เลือก)

## ลำดับ commit ที่แนะนำ (ทำงานในรีโป Classwise, branch ใหม่)
1. `omr` scaffold + settings + engine_runner + หน้าลองอัปโหลด (เฟส 1)
2. models `OMRJob`/`OMRSheetResult` + django-rq + worker + polling (เฟส 2)
3. `AnswerKey` คลังเฉลย + matching/compute/apply + หน้าตรวจทาน + cleanup (เฟส 3)
4. CONTEXT.md + ADR 0002

## ความเสี่ยง / จุดที่ต้องระวัง
- **CSV ทับกัน:** engine append ลง `Results_<hour>.csv` — `engine_runner` ต้องใช้ temp out dir ใหม่ทุกครั้ง (logic เดิมใน runner.py ทำแล้ว — อย่าลืมยกมา)
- **subprocess timeout:** PDF หลายแผ่นอาจนาน — `OMR_SUBPROCESS_TIMEOUT` ตั้งเผื่อ
- **student_id ฝนเบลอ/ไม่ครบ 5 หลัก:** ต้องออกมาเป็น UNMATCHED ไม่ใช่ crash — ทดสอบด้วยแผ่นที่ฝน id ไม่ครบ
- **prod media:** Nginx ต้องตั้ง `client_max_body_size` เผื่อ PDF/หลายรูป (บันทึกใน DEPLOY.md ตอนขึ้น prod)

## Verification สรุป (คำสั่งเดียวจบต่อเฟส)
- เฟส 0: `python main.py -i samples\yothin -o outputs\yothin_check` → เทียบ CSV กับ golden
- เฟส 1: `python manage.py test omr` + อัปโหลดจริงเห็น overlay
- เฟส 2: `python manage.py rqworker omr` + อัปโหลด → จอ update เอง + เว็บไม่หน่วง
- เฟส 3: `python manage.py test omr` (compute/match/apply/gate) + golden e2e → คะแนนโผล่หน้า grades
