# thegemtimepost.github.io
# THE BKK GEM POST

หนังสือพิมพ์ภาษาอังกฤษรายวันสำหรับคนทำงาน (CEFR B1+ ถึง C1) ระบบสร้างฉบับใหม่ทุกเช้าด้วย AI แล้วเผยแพร่บน GitHub Pages ให้อัตโนมัติ

## ระบบทำงานอย่างไร

```
04:17 น. (เวลาไทย) GitHub Actions เริ่มทำงาน
 │
 ├─ 1. คำนวณแผนของวัน (โค้ด ไม่ใช้ AI)
 │     วันที่ · เลขฉบับ · ธีมตามวัน · ภาษาประจำฉบับ
 │     อ่านฉบับเก่าใน data/issues/ → คำที่ห้ามซ้ำ 30 วัน + คำทบทวน ↺ (1, 3, 7, 21 วันก่อน)
 │
 ├─ 2. ดึงข่าวจริงจาก RSS (BBC, Guardian, Bangkok Post, Thai PBS World ฯลฯ)
 │     จับกลุ่มข่าวเดียวกันจากหลายสำนัก → ข่าวไทยเลือกเรื่องที่มี 2 แหล่งก่อน
 │
 ├─ 3. โค้ดเลือกข่าว 5 เรื่อง (ข่าวที่หลายสำนักรายงานก่อน, ไม่ซ้ำกัน) → ดาวน์โหลดเนื้อข่าวเต็ม
 │
 ├─ 4. AI ขั้นที่ 1: เขียนบทความ (Cover, World, Thailand, Features, Opinion)       ← 1 request
 │     → โค้ดแก้เอง: ทำตัวหนาคำ ★ ที่ลืม, ลบ HTML, ตัดคำ ★ ที่ไม่มีในบทความ
 │     → ตรวจเฉพาะเรื่องร้ายแรง ถ้าไม่ผ่านขอ AI แก้ 1 ครั้ง
 │     → บันทึกเป็น draft (ถ้าขั้นต่อไปล้ม รอบถัดไปไม่ต้องเขียนใหม่)
 │
 ├─ 5. AI ขั้นที่ 2: Language Lab, Other Languages, Glossary                     ← 1 request
 │     → โค้ดเรียง Glossary และหาประโยคตัวอย่างจากบทความเอง
 │
 └─ 6. ใส่ลงเทมเพลต HTML (sepia / ขาวดำ) → commit → GitHub Pages
       หน้าแรก = ฉบับล่าสุด · /archive.html = ฉบับย้อนหลังทั้งหมด
```

**วันปกติใช้แค่ 2 request** และมีเพดาน `max_requests_per_run` (12) ต่อรอบ ต่อให้ Google ล่มทั้งเช้าก็ไม่กินโควตาเกินนี้

**เมื่อ Gemini ตอบ 503 (high demand)** ระบบจะสลับไปใช้โมเดลหลักอีกตัว แล้วรอนานขึ้นทีละรอบ (1, 2, 4, 8 นาที รวมไม่เกิน `retry_wait_minutes`) ถ้ายังไม่ว่างจึงใช้ Flash-Lite เขียนแทน แล้วรอบ 06:27 น. จะลองเขียนใหม่ด้วยโมเดลหลัก

**AI ทำแค่การเขียน** ส่วนวันที่ ธีม ภาษา หน้าตาเว็บ และการตรวจคุณภาพ เป็นโค้ดทั้งหมด หน้าตาจึงเหมือนเดิมทุกวัน และ AI คำนวณวันผิดไม่ได้

**ระบบมีความจำข้ามวันแล้ว** ทุกฉบับถูกเก็บเป็น JSON ใน repo การรันวันถัดไปจึงอ่านย้อนได้ คำ ↺ เป็นคำ ★ จริงของฉบับก่อนๆ ตามรอบทบทวน

---

ระบบจะรันเองทุกวันตอน **04:17 น.** และมีรอบสำรองตอน **06:27 น.** (ถ้าฉบับวันนั้นออกแล้วด้วยโมเดลหลัก รอบสำรองจะไม่ทำอะไร)

---

## การใช้งานประจำวัน

| ต้องการ | ทำอย่างไร |
|---|---|
| สร้างฉบับวันนี้ใหม่ | Actions → Daily issue → Run workflow → ติ๊ก **force** |
| สร้างฉบับของวันที่ระบุ | Run workflow → ใส่ **date** เช่น `2026-10-28` |
| เปลี่ยนแหล่งข่าว | แก้ `news.feeds` ใน `config.yml` |
| เปลี่ยนธีมหรือภาษา | แก้ `rotation` / `languages` ใน `config.yml` |
| ปรับวิธีเขียน ระดับภาษา | แก้ `prompts/articles.md` (บทความ) หรือ `prompts/lab.md` (แบบฝึกหัด) |
| เปลี่ยนโมเดล / ลำดับโมเดล | แก้ `models.gemini` ใน `config.yml` |
| ปรับหน้าตาเว็บ | แก้ `templates/style.css` |

## ถ้ามีปัญหา

- **ฉบับไม่ออก** GitHub จะส่งอีเมลแจ้งเมื่อการรันล้มเหลว เปิดรอบนั้นใน Actions แล้วดู Summary หรือ log
  - `Not enough news`: แหล่ง RSS ใช้ไม่ได้หลายแห่ง (ดูบรรทัด `feed skipped` ใน log) ให้เปลี่ยน URL ใน `config.yml`
  - `HTTP 503 high demand`: เซิร์ฟเวอร์ Gemini คนใช้เยอะ ระบบจะรอแล้วลองโมเดลหลักซ้ำ (รอนานขึ้นทีละรอบ รวมไม่เกิน `retry_wait_minutes`) ก่อนใช้ Flash-Lite ถ้ารอบ 04:17 น. ต้องใช้ Flash-Lite รอบ 06:27 น. จะลองเขียนใหม่ด้วยโมเดลหลัก ถ้ายังไม่ว่างก็คงฉบับเดิมไว้ ดูได้ใน Summary ว่าฉบับนั้นใช้โมเดลอะไร
  - `HTTP 429`: โควตาฟรีของ Gemini หมด ระบบจะลองโมเดลถัดไปให้เอง ถ้าหมดทุกตัวให้รอวันถัดไป (โควตารีเซ็ตเที่ยงคืนเวลาแปซิฟิก)
  - `HTTP 404` / `model not found`: Google เปลี่ยนชื่อโมเดล ให้แก้ `models.gemini` ใน `config.yml` ตามชื่อใน AI Studio
  - `request budget for this run is used up`: ครบเพดาน request ของรอบนั้น ระบบหยุดเพื่อเก็บโควตาไว้ รอบถัดไปจะลองใหม่
  - `answer stopped early (MAX_TOKENS)`: คำตอบยาวเกินเพดาน ระบบสลับไปใช้โมเดลอีกตัวเอง ถ้าเกิดบ่อยให้ลดความยาวใน prompts
  - `still has critical problems after repair`: AI เขียนไม่ผ่านการตรวจ ดาวน์โหลด **failed-draft** จากหน้ารอบนั้นมาดูได้ ถ้าบทความผ่านแล้วแต่ Lab ไม่ผ่าน รอบถัดไปจะเขียนแค่ Lab ใหม่
- **ดูว่าแต่ละรอบใช้ request ไปเท่าไหร่** เปิดรอบนั้นใน Actions → Summary จะมีรายการ request ทุกครั้งพร้อมผล และรายการ Notes ที่ระบบปล่อยผ่าน (เช่น จำนวนคำต่ำกว่าเป้าเล็กน้อย)
- **push ไม่ได้ (403)** Settings → Actions → General → Workflow permissions → เลือก **Read and write permissions**
- **รันช้ากว่าเวลาที่ตั้ง** ปกติของ GitHub ในช่วงที่คนใช้เยอะ บางครั้งช้า 10-30 นาที จึงมีรอบสำรอง 06:27 น.

## ทดสอบในเครื่อง (ไม่ต้องมี API key)

```bash
pip install -r requirements.txt
PROVIDER=mock python scripts/build.py --date 2026-10-28 --news-fixture tests/fixtures/news.json
python tests/fake_gemini_test.py    # จำลอง Gemini ล่ม/โควตาหมด 12 กรณี (ไม่ใช้โควตาจริง)
# เปิด docs/index.html ดูผล แล้วลบ docs/index.html docs/archive.html docs/issues/* data/issues/*.json ก่อน push
```

## โครงสร้างไฟล์

```
config.yml                 ตั้งค่าทั้งหมด (วันเริ่ม, ข่าว, ธีม, ภาษา, โมเดล)
prompts/common.md          บทบาทและกฎระดับภาษา (ใช้ทั้ง 2 ขั้น)
prompts/articles.md        AI ขั้นที่ 1: บทความ
prompts/lab.md             AI ขั้นที่ 2: Language Lab, Other Languages, Glossary
scripts/build.py           ตัวหลัก
scripts/plan.py            วันที่ เลขฉบับ ธีม คำทบทวน
scripts/news.py            ดึง RSS และเนื้อข่าว
scripts/llm.py             เชื่อม Gemini / Claude และ JSON schema
scripts/checks.py          ตรวจคุณภาพ (แยกปัญหาร้ายแรง / ข้อสังเกต)
scripts/autofix.py         แก้ปัญหาเล็กๆ ด้วยโค้ด
scripts/render.py          สร้าง HTML
templates/                 หน้าตาเว็บ
data/issues/               เนื้อหาทุกฉบับ (ความจำของระบบ)
data/drafts/               บทความที่เขียนเสร็จแล้วแต่ฉบับยังไม่ออก
docs/                      เว็บที่เผยแพร่
.github/workflows/daily.yml ตารางเวลาและขั้นตอนบน GitHub
```
