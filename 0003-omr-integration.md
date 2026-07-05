# ADR 0003 — การเชื่อมระบบตรวจข้อสอบ OMR เข้ากับ Classwise

- **สถานะ:** เสนอ (Proposed)
- **วันที่:** 2568-07
- **เกี่ยวข้องกับแอป:** `omr` (ใหม่), `grades`, `students`, `school`
- **อ้างอิง:** README ของ OMRChecker (Python + OpenCV, MIT license), CLAUDE.md ข้อ 4–5 (grades / students)

---

## 1. บริบท (Context)

ปัจจุบันมี 2 ระบบที่ต้องการให้ทำงานร่วมกัน:

1. **OMRChecker** — โปรแกรม Python + OpenCV อ่านกระดาษคำตอบ OMR เป็น **batch CLI script**
   (`python3 main.py --inputDir ... --outputDir ...`) อ่านรูป → detect ช่องที่ฝน → พ่นผลเป็น CSV
   รองรับ PDF ในตัวผ่าน PyMuPDF ความเร็ว ~200 แผ่น/นาที ความแม่นเกือบ 100% กับสแกนเนอร์
   แต่ **~90% กับรูปถ่ายมือถือ** ต้องมี `template.json` กำหนดพิกัดทุกช่องให้ตรงกับกระดาษจริง
2. **Classwise** — Django web app มีแอป `grades` ที่จัดการคะแนนอยู่แล้ว
   (`GradeType` = ชิ้นงานต่อวิชา, `Score` = คะแนนรายบุคคล, service `bulk_record_scores` upsert + กันเกินเต็ม)

**เป้าหมาย:** ให้ครูตรวจข้อสอบปรนัยด้วย OMR แล้วคะแนนไหลเข้าระบบ `grades` อัตโนมัติ
โดยไม่ต้องกรอกมือทีละคน

**ธรรมชาติที่ขัดกันของสองระบบ (แกนของปัญหา):** OMRChecker เป็นงาน batch กิน CPU หนัก ใช้เวลานาน
ส่วน web ต้องตอบผู้ใช้เป็นวินาที **จึงห้ามรัน OMR ในตัว web process** — สอดคล้องกับผล load test ก่อนหน้า
ที่พบว่า CPU คือคอขวดของระบบตั้งแต่ ~200–250 users (จาก password hashing) การประมวลผลภาพจะยิ่งหนักกว่านั้น

**ข้อจำกัด deployment ปัจจุบัน:** VPS ตัวเดียว (Gunicorn + Nginx) → worker ประมวลผล OMR
ต้องอยู่เครื่องเดียวกับ web และแย่ง CPU กัน แต่ลักษณะงานตรวจข้อสอบเป็น **burst เป็นครั้งคราว**
(ครูคนหนึ่งตรวจข้อสอบชุดหนึ่ง ไม่ใช่โหลดต่อเนื่องพร้อมกันจำนวนมาก) จึงจัดการได้ด้วยคิวงาน

---

## 2. การตัดสินใจ (Decision)

### 2.1 สร้าง Django app ใหม่ชื่อ `omr` หุ้มทุกอย่างที่เกี่ยวกับ OMR

แยกโค้ดที่เกี่ยวกับ OMR ทั้งหมดไว้ใน `omr` เพื่อไม่ให้ปนกับแอปอื่น ยึด convention เดิม:
`services.py` เป็นจุดเขียนข้อมูลเดียว, ทุก view ใช้ `teacher_or_admin_required` + resolve วิชาผ่าน
`subjects_for(user)` (กันเข้าถึงข้ามครู → 404), reuse `manage_base.html`

### 2.2 เรียก OMRChecker แบบ subprocess (ไม่ import main.py ตรงๆ)

OMRChecker เขียนมาเป็น CLI ไม่ใช่ library — การ `import main` เข้ามาตรงๆ จะพังเพราะมันอ่าน argv
สแกนทั้งโฟลเดอร์ และเขียนไฟล์เอง **จึงเรียกเป็น subprocess:** worker เตรียมโฟลเดอร์ชั่วคราว
วางรูป + `template.json` ที่ generate ให้ → `python3 main.py -i <tmp> -o <tmp>/out` → อ่าน CSV กลับ

- **ข้อดี:** แทบไม่ต้องแก้โค้ด OMRChecker, decouple ชัดเจน (แยกโปรเจกต์กันได้)
- **ทางเลือกในอนาคต:** ถ้าต้องการควบคุม/จับ error ละเอียดขึ้น ค่อย wrap ฟังก์ชันหลักของ OMRChecker
  เป็น service function `(image_path, template, key) -> dict` ทีหลัง — ไม่ทำในเฟสแรก
- **License:** OMRChecker เป็น MIT ใช้/แก้/fork ได้ ไม่มีปัญหากับโปรเจกต์เพื่อการศึกษา

### 2.3 กระดาษคำตอบต้องมีบล็อกฝน `student_id` (จุดเชื่อมข้อมูล)

OMRChecker อ่านได้แค่ "ช่องไหนถูกฝน" ไม่รู้ว่ากระดาษเป็นของใคร → กระดาษคำตอบต้องมี
**บล็อก OMR ให้นักเรียนฝนรหัสนักเรียนของตัวเอง** (เช่น 5 หลัก = 5 คอลัมน์ × 0–9)
กำหนดใน `template.json` ให้อ่านบล็อกนี้เป็น field `student_id` แล้วระบบเอาไป match กับ
`User.student_id` ที่มีอยู่ → ผูกคะแนนเข้า `Score` ของคนนั้น
**ถ้าไม่ทำ ครูต้องจับคู่กระดาษ↔ชื่อเองทีละแผ่น** ซึ่งลบล้างประโยชน์ของ OMR

### 2.4 มี "ขั้นตอนครูตรวจทานก่อนบันทึก" เสมอ (บังคับ)

เพราะความแม่นรูปมือถือ ~90% → ห้ามเขียนคะแนนเข้า DB อัตโนมัติ ต้องมีหน้า preview รายแผ่น:
รูปที่มี overlay ว่าอ่านช่องไหน + รหัสนักเรียนที่จับคู่ได้ (เตือนถ้าจับคู่ไม่ได้) + คะแนนที่คำนวณ
ครูแก้ได้ก่อนกด "ยืนยันบันทึก" — **reuse pattern จากหน้า import รายชื่อ** (`/students/import/`)
ที่มี preview ตรวจสถานะรายแถวก่อน confirm อยู่แล้ว

### 2.5 ครูกรอกเฉลยในระบบ Classwise — ไม่แก้ evaluation.json เอง

เก็บเฉลยเป็นโมเดลใน Classwise (ผูกกับ `GradeType` = ชิ้นงานที่จะบันทึกคะแนนลง) ครูเห็นแค่หน้ากรอกเฉลยสวยๆ
ระบบ **generate ไฟล์ config ให้ OMRChecker ตอน runtime** (ซ่อนความยุ่งยากของ JSON ไว้เบื้องหลัง)

### 2.6 ใช้คิวงาน `django-rq` (RQ + Redis) — ไม่ใช้ Celery

RQ เรียนรู้ง่ายกว่ามากสำหรับเฟสนี้ setup ไม่กี่บรรทัด เพียงพอกับงาน burst ค่อยพิจารณา Celery
ถ้าโตขึ้นจริง บน VPS เดียวต้อง:

- จำกัด worker ให้ทำ **ทีละ 1 process** (กัน OMR หลายตัวรุมกิน CPU พร้อมกัน)
- รัน worker ด้วย **priority ต่ำ (`nice`)** เพื่อให้ request ของ web ชนะ CPU เสมอ → เว็บไม่หน่วงตอนตรวจข้อสอบ
- web แสดงสถานะ "กำลังประมวลผล..." แล้ว **poll ผลผ่าน AJAX** (reuse pattern polling จากกระดิ่งแจ้งเตือน)

### 2.7 กระดาษคำตอบมาตรฐานแบบเดียวก่อน + มาร์กเกอร์ 4 มุม

อย่าเพิ่งรองรับหลาย layout ออกแบบกระดาษมาตรฐาน 1 แบบ (60 ข้อ × **4 ตัวเลือก (ก–ง, MCQ4)** + บล็อกรหัสนักเรียน
_(แก้จากเดิม "5 ตัวเลือก" → 4 ตัวเลือก ตามข้อสอบจริง — ดู ADR 0004 และ `samples/yothin/`)_

+ **มาร์กเกอร์ 4 มุมสำหรับ perspective correction** จำเป็นมากกับรูปมือถือ) ทำ `template.json` ตัวเดียวให้เป๊ะ
  ใช้ซ้ำทุกวิชา แล้วค่อยเพิ่มแบบอื่นทีหลัง

### 2.8 แปลงคะแนนดิบ → คะแนนตาม `GradeType.max_score` แบบสัดส่วน

`computed = raw_correct / num_questions * grade_type.max_score` (เช่น ถูก 40/50 → 16/20)
ปัด Decimal ตามแนวทางเดิมของ `grades` แล้วบันทึกผ่าน **`grades.services.bulk_record_scores()` ที่มีอยู่แล้ว**
→ ไม่ต้องเขียน logic คะแนนใหม่ คะแนนไปโผล่ในหน้าสรุปคะแนน / export Excel / portal นักเรียน อัตโนมัติ

---

## 3. ร่างโมเดล (Proposed models — `omr/models.py`)

> เป็นจุดตั้งต้นสำหรับ Claude Code ปรับได้ตามจริง แต่ให้รักษาแนวคิดหลักไว้

- **`AnswerSheetTemplate`** — กระดาษคำตอบแบบหนึ่ง
  `name`, `num_questions`, `num_choices`, `student_id_digits`, `template_json` (หรือชี้ path ไฟล์ static)
  *(เฟสแรกอาจเริ่มเป็น template คงที่ตัวเดียวก่อน แล้วค่อยยกเป็นโมเดลเต็มเมื่อรองรับหลายแบบ)*

- **`AnswerKey`** — เฉลยของชิ้นงานหนึ่ง
  FK `grade_type` (`school.Subject` มากับ GradeType ครบ → ได้ ครู+ห้อง+ภาค+ปี), FK `template`,
  `answers` (JSON: `{"1":"A","2":"C",...}`), `created_by`, timestamps; unique `(grade_type)` หรือรองรับหลายชุดถ้าต้องการ

- **`OMRJob`** — งานตรวจ 1 ครั้ง
  FK `subject`, FK `grade_type`, FK `template`, FK `answer_key`, `uploaded_by`,
  `status` (`PENDING`/`PROCESSING`/`DONE`/`FAILED`/`REVIEWED`), `error_message`, timestamps
  → scope ทุก view ผ่าน `subjects_for(user)` เหมือนแอปอื่น

- **`OMRSheetResult`** — ผลรายแผ่น (1 job มีหลาย result)
  FK `job` (`related_name="results"`), `source_image` (FileField), `overlay_image` (FileField, รูปที่ mark ให้ครูดู),
  `detected_student_id` (str), FK `matched_student` (User, null ได้), `detected_answers` (JSON),
  `raw_score` (int), `computed_score` (Decimal, null ได้),
  `status` (`MATCHED`/`UNMATCHED`/`NEEDS_REVIEW`/`CONFIRMED`), timestamps

**Service ที่ควรมี (`omr/services.py` — จุดเขียนข้อมูลเดียว):**
`create_job` / `enqueue_job` / `process_job` (ตัวที่ worker เรียก) / `match_students` /
`compute_scores` / `apply_to_grades` (เรียก `grades.bulk_record_scores`) / `subjects_for` / `get_roster`

---

## 4. ผลที่ตามมา (Consequences)

**ข้อดี**

- แยก concern ชัด: OMR อยู่ใน `omr` อย่างเดียว, ปลายทางต่อเข้า `grades` ที่ทดสอบแล้ว โดยไม่แตะ logic คะแนนเดิม
- คิว + nice priority ทำให้เว็บไม่หน่วงแม้ตอนประมวลผลข้อสอบ บน VPS เดียว
- subprocess ทำให้อัปเดต/สลับเวอร์ชัน OMRChecker ได้อิสระ (แยก repo/แยก dependency)
- ขั้นตอนครูตรวจทานรักษาความถูกต้องของคะแนน (evidence-first) — คะแนนผิดกระทบเด็กโดยตรง

**ต้นทุน / สิ่งที่ต้องระวัง**

- **งานที่กินเวลาจริง = ออกแบบกระดาษ + จูน `template.json` ให้ตรงเป๊ะ** (README ประเมิน setup ~20 นาที,
  มี flag `--setLayout` ช่วยจูน) ต้องผ่านด่านนี้ก่อนเขียนโค้ดเชื่อมใดๆ
- ต้องเพิ่ม **Redis** เป็น dependency ใหม่ + จัดการ worker process ตอน deploy (systemd unit)
- ไฟล์รูปที่อัปโหลด + overlay ต้องเก็บใน MEDIA (มี `MEDIA_URL` จาก homework attachments แล้ว);
  Nginx prod ต้องตั้ง `client_max_body_size` ให้พอกับ PDF/รูปหลายแผ่น
- โฟลเดอร์ชั่วคราวของ subprocess ต้องมี **management command cleanup** (ลอก pattern
  `cleanup_pending_attachments` — ตั้ง cron)
- ต้อง handle เคส `UNMATCHED` (อ่านรหัสนักเรียนไม่ได้/ไม่พบในระบบ) ให้ครูแก้มือในหน้าตรวจทาน

**ทางเลือกที่พิจารณาแล้วไม่เลือก**

- *รัน OMR ใน web request ตรงๆ* → ตัดทิ้ง: request timeout + แย่ง CPU กับเว็บทั้งระบบ
- *แยก OMR เป็น microservice/API แยกเครื่อง* → เกินจำเป็นสำหรับ VPS เดียว/งาน burst; เก็บไว้พิจารณาตอน scale จริง
- *Celery* → overhead การเรียนรู้/ตั้งค่าสูงเกินสำหรับเฟสนี้
- *ให้ครูแก้ evaluation.json เอง* → UX แย่มากสำหรับครู

---

## 5. แผนดำเนินงานเป็นเฟส (Roadmap)

ทำทีละเฟส แต่ละเฟสจบในตัว/ใช้งานได้ก่อนไปต่อ:

- **เฟส 0 — พิสูจน์ OMRChecker (ยังไม่แตะ Django):** ออกแบบกระดาษ 1 แบบ พิมพ์ ฝนมือ ถ่ายรูป
  รัน `python3 main.py` ให้อ่านถูก จูน `template.json` จนแม่น *(ด่านยากสุด ต้องผ่านก่อน)*
- **เฟส 1 — เชื่อม synchronous ง่ายๆ:** หน้าอัปโหลด → subprocess → แสดงผลบนจอ
  (ยังไม่มีคิว ยังไม่เขียน DB) ทดกับรูป 2–3 แผ่น ให้เห็นเส้นทางข้อมูลไหลครบ
- **เฟส 2 — ใส่คิวงาน:** ย้าย subprocess ไปหลัง `django-rq` (worker 1 process, `nice`);
  หน้าเว็บแสดง "กำลังประมวลผล..." + AJAX polling
- **เฟส 3 — เฉลย + ตรวจทาน + บันทึก:** หน้ากรอกเฉลย, หน้า preview รายแผ่นแก้ได้ (ลอกจาก import รายชื่อ),
  ปุ่มยืนยัน → `bulk_record_scores()` ปิด loop

---

## 6. คำถามเปิดที่ต้องตัดสินใจภายหลัง (Open questions)

- รองรับ 1 ชิ้นงาน ต่อ 1 เฉลย เท่านั้น หรือให้กระดาษแผ่นเดียวออกได้หลายชิ้นงาน?
- เก็บ `template.json` เป็นไฟล์ static หรือ generate จากโมเดลทั้งหมด?
- กรณีรหัสนักเรียนซ้ำ/ฝนผิด (UNMATCHED) — flow ให้ครูแก้แบบไหนถึงเร็วที่สุด?
- ต้องเก็บรูปกระดาษไว้ถาวรเป็นหลักฐาน หรือลบหลังบันทึกคะแนนเสร็จ (privacy vs. audit)?
