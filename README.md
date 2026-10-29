# THE CITY TIMES

## ในแต่ละฉบับมีอะไร (ประมาณ 2,300–2,700 คำ อ่านราว 20 นาที)

| ส่วน | ระดับ | ความยาว |
|---|---|---|
| Cover Story | C1 | 470–510 คำ |
| World in Brief (2 ข่าว) · Thailand Today (2 ข่าว) | B1+ | ข่าวละ 140–160 คำ |
| Feature (วันละ 1 บทความ ตามธีมของวัน) | B2 | 310–370 คำ |
| Real English: Two Takes หรือ Say It Better + Thai Speaker's Trap | B2/C1 | 260–320 คำ |
| Language Lab: แบบฝึกหัดประจำวัน 6 ข้อ + Error Identification 5 ข้อ (ทำก่อนดู Word Bank) | | |
| Word Bank: คำ ★ 8 คำ (ความหมาย, collocations, ประโยคจากข่าว, คำแปลไทยกดดู) + คำทบทวน ↺ 4 คำ | | |
| Other Languages: วลีในชีวิตประจำวัน, รากศัพท์, คำที่อังกฤษยืมมา, คำน่าสนใจ (เยอรมัน → สเปน → ฝรั่งเศส → อิตาลี) | | 140–190 คำ |

ฉบับที่ 1 คือวันศุกร์ 30 ต.ค. 2026 (`start_date` ใน `config.yml`) หน้าเว็บแสดงฉบับตามวันที่ ไม่แสดงเลขฉบับ เลขฉบับยังใช้ภายในระบบสำหรับหมุนภาษาและธีมวลี ข้อมูลฉบับก่อนวันเริ่มจะถูกลบออกจากเว็บและ Archive ให้เองในการรันครั้งแรก

ธีม Feature: อา. Business / Money · จ. Health & Wellbeing · อ. Tech & Science · พ. Arts & Entertainment · พฤ. Home / Environment · ศ. Food & Travel · ส. Cultures / Digital Life (แก้ได้ที่ `rotation` ใน `config.yml`)

หนังสือพิมพ์ภาษาอังกฤษรายวันสำหรับคนทำงาน (CEFR B1+ ถึง C1) ระบบสร้างฉบับใหม่ทุกเช้าด้วย AI แล้วเผยแพร่บน GitHub Pages ให้อัตโนมัติ

## ระบบทำงานอย่างไร

```
03:10 น. (เวลาไทย) GitHub Actions เริ่มทำงาน (GitHub อาจเริ่มช้าในช่วงคนใช้เยอะ)
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
 ├─ 4. AI ขั้นที่ 1: เขียนบทความ (Cover, World, Thailand, Feature, Real English) ← 1 request
 │     → โค้ดแก้เอง: ทำตัวหนาคำ ★ ที่ลืม, ลบ HTML, ตัดคำ ★ ที่ไม่มีในบทความ
 │     → ตรวจเฉพาะเรื่องร้ายแรง ถ้าไม่ผ่านขอ AI แก้ 1 ครั้ง
 │     → บันทึกเป็น draft (ถ้าขั้นต่อไปล้ม รอบถัดไปไม่ต้องเขียนใหม่)
 │
 ├─ 5. AI ขั้นที่ 2: Word Bank, Language Lab, Other Languages                    ← 1 request
 │     → ส่งไปแค่ประโยคที่มีคำศัพท์ + พาดหัวข่าว (ไม่ส่งบทความเต็ม ประหยัด token)
 │     → โค้ดเรียง Word Bank และหาประโยคตัวอย่างจากบทความเอง
 │
 └─ 6. ใส่ลงเทมเพลต HTML (sepia / ขาวดำ) → commit → GitHub Pages
       หน้าแรก = ฉบับล่าสุด · /archive.html = ฉบับย้อนหลังทั้งหมด
```

**วันปกติใช้แค่ 2 request** และมีเพดาน `max_requests_per_run` (12) ต่อรอบ ต่อให้ Google ล่มทั้งเช้าก็ไม่กินโควตาเกินนี้

**เมื่อ Gemini ตอบ 503 (high demand)** ระบบจะสลับไปใช้โมเดลหลักอีกตัว แล้วรอนานขึ้นทีละรอบ (1, 2, 4, 8 นาที รวมไม่เกิน `retry_wait_minutes`) ถ้ายังไม่ว่างจึงใช้ Flash-Lite เขียนแทน แล้วรอบสำรองจะลองเขียนใหม่ด้วยโมเดลหลัก

**AI ทำแค่การเขียน** ส่วนวันที่ ธีม ภาษา หน้าตาเว็บ และการตรวจคุณภาพ เป็นโค้ดทั้งหมด หน้าตาจึงเหมือนเดิมทุกวัน และ AI คำนวณวันผิดไม่ได้

**ระบบมีความจำข้ามวันแล้ว** ทุกฉบับถูกเก็บเป็น JSON ใน repo การรันวันถัดไปจึงอ่านย้อนได้ คำ ↺ เป็นคำ ★ จริงของฉบับก่อนๆ ตามรอบทบทวน

---

## ติดตั้ง (ประมาณ 15 นาที)

### 1. ขอ Gemini API key (ฟรี)
1. เข้า <https://aistudio.google.com> แล้วล็อกอินด้วยบัญชี Google
2. กด **Get API key** → **Create API key**
3. คัดลอก key เก็บไว้ (ไม่ต้องผูกบัตรเครดิต)

### 2. สร้าง repository บน GitHub
1. สร้าง repo ใหม่ เช่น `bkk-gem-post` ตั้งเป็น **Public** (GitHub Pages ใช้ฟรีกับ repo สาธารณะ)
2. อัปโหลดไฟล์ทั้งหมดในโฟลเดอร์นี้ขึ้น repo
   - วิธีง่ายสุด: หน้า repo → **Add file → Upload files** → ลากไฟล์และโฟลเดอร์ทั้งหมดเข้าไป → Commit
   - ⚠️ **macOS ซ่อนโฟลเดอร์ `.github`** ถ้าหลังอัปโหลดไม่เห็นโฟลเดอร์นี้ใน repo ให้กด **Add file → Create new file** ตั้งชื่อ `.github/workflows/daily.yml` แล้ววางเนื้อหาของไฟล์นั้นลงไป

### 3. ใส่ API key เป็น Secret
**Settings → Secrets and variables → Actions → New repository secret**
- Name: `GEMINI_API_KEY`
- Secret: วาง key จากข้อ 1

### 4. เปิด GitHub Pages
**Settings → Pages → Build and deployment → Source:** เลือก **GitHub Actions**

### 5. ทดสอบรันครั้งแรก
1. แท็บ **Actions** → ถ้ามีปุ่มให้เปิดใช้ workflows ให้กดเปิด
2. เลือก **Daily issue** → **Run workflow** → **Run workflow**
3. รอ 3-8 นาที เมื่อขึ้นเครื่องหมายถูกสีเขียว เว็บจะอยู่ที่
   `https://<ชื่อผู้ใช้ GitHub>.github.io/bkk-gem-post/`
4. กดเข้าไปในรอบที่รันเสร็จเพื่อดู **Summary** จะแสดงจำนวนคำและข้อสังเกตของฉบับนั้น

หลังจากนี้ระบบจะรันเองทุกวัน รอบหลัก **03:10 น.** และรอบสำรอง **05:15 น.** GitHub อาจเริ่มรันช้ากว่าเวลาที่ตั้งในช่วงที่คนใช้เยอะ ถ้าต้องการเวลาตรง ดูหัวข้อ "เวลารันไม่ตรง" (รอบสำรองไม่ทำอะไรถ้าฉบับวันนั้นออกแล้วด้วยโมเดลหลัก)

---

## การใช้งานประจำวัน

| ต้องการ | ทำอย่างไร |
|---|---|
| สร้างฉบับวันนี้ใหม่ | Actions → Daily issue → Run workflow → ติ๊ก **force** |
| ลองเขียนฉบับที่ใช้ Flash-Lite ใหม่ด้วยโมเดลหลัก | Run workflow → mode = **upgrade** |
| สร้างฉบับของวันที่ระบุ | Run workflow → ใส่ **date** เช่น `2026-11-02` |
| เปลี่ยนแหล่งข่าว | แก้ `news.feeds` ใน `config.yml` |
| เปลี่ยนธีมหรือภาษา | แก้ `rotation` / `languages` ใน `config.yml` |
| ปรับวิธีเขียน ระดับภาษา | แก้ `prompts/articles.md` (บทความ) หรือ `prompts/lab.md` (แบบฝึกหัด) |
| เปลี่ยนโมเดล / ลำดับโมเดล | แก้ `models.gemini` ใน `config.yml` |
| ปรับหน้าตาเว็บ | แก้ `templates/style.css` |

## ถ้ามีปัญหา

- **ฉบับไม่ออก** GitHub จะส่งอีเมลแจ้งเมื่อการรันล้มเหลว เปิดรอบนั้นใน Actions แล้วดู Summary หรือ log
  - `Not enough news`: แหล่ง RSS ใช้ไม่ได้หลายแห่ง (ดูบรรทัด `feed skipped` ใน log) ให้เปลี่ยน URL ใน `config.yml`
  - `HTTP 503 high demand`: เซิร์ฟเวอร์ Gemini คนใช้เยอะ ระบบจะรอแล้วลองโมเดลหลักซ้ำ (รอนานขึ้นทีละรอบ รวมไม่เกิน `retry_wait_minutes`) ก่อนใช้ Flash-Lite ถ้ารอบหลักต้องใช้ Flash-Lite รอบสำรองจะลองเขียนใหม่ด้วยโมเดลหลัก ถ้ายังไม่ว่างก็คงฉบับเดิมไว้ ดูได้ใน Summary ว่าฉบับนั้นใช้โมเดลอะไร
  - `HTTP 429`: โควตาฟรีของ Gemini หมด ระบบจะลองโมเดลถัดไปให้เอง ถ้าหมดทุกตัวให้รอวันถัดไป (โควตารีเซ็ตเที่ยงคืนเวลาแปซิฟิก)
  - `HTTP 404` / `model not found`: Google เปลี่ยนชื่อโมเดล ให้แก้ `models.gemini` ใน `config.yml` ตามชื่อใน AI Studio
  - `request budget for this run is used up`: ครบเพดาน request ของรอบนั้น ระบบหยุดเพื่อเก็บโควตาไว้ รอบถัดไปจะลองใหม่
  - `answer stopped early (MAX_TOKENS)`: คำตอบยาวเกินเพดาน ระบบสลับไปใช้โมเดลอีกตัวเอง ถ้าเกิดบ่อยให้ลดความยาวใน prompts
  - `still has critical problems after repair`: AI เขียนไม่ผ่านการตรวจ ดาวน์โหลด **failed-draft** จากหน้ารอบนั้นมาดูได้ ถ้าบทความผ่านแล้วแต่ Lab ไม่ผ่าน รอบถัดไปจะเขียนแค่ Lab ใหม่
- **ดูว่าแต่ละรอบใช้ request ไปเท่าไหร่** เปิดรอบนั้นใน Actions → Summary จะมีรายการ request ทุกครั้งพร้อมผล และรายการ Notes ที่ระบบปล่อยผ่าน (เช่น จำนวนคำต่ำกว่าเป้าเล็กน้อย)
- **push ไม่ได้ (403)** Settings → Actions → General → Workflow permissions → เลือก **Read and write permissions**
- **ข่าวซ้ำกับเมื่อวาน** ระบบข้ามข่าวที่หัวข้อคล้ายข่าวใน 3 วันก่อนให้อัตโนมัติ ถ้ายังซ้ำบ่อยแปลว่ามีแหล่งข่าวน้อย ให้เพิ่ม feed ใน `config.yml`
- **บทความข่าวสั้นกว่าเป้า** ถ้าเว็บข่าวบล็อกหรือติด paywall ระบบจะลองข่าวอื่นก่อน ถ้าไม่มีจริงๆ จะให้ AI เขียนสั้นลงแทนการเติมเนื้อหาเอง (ดูได้ใน Notes ว่า "little source text")
- **`unrecognized arguments`** ไฟล์ `scripts/build.py` กับ `.github/workflows/daily.yml` มาจากคนละรุ่น ให้อัปโหลดทั้งสองไฟล์จาก zip ล่าสุดพร้อมกัน

## เวลารันไม่ตรง

ตารางเวลาของ GitHub Actions **ไม่รับประกันเวลา** ช่วงที่คนใช้เยอะอาจเริ่มช้าไป 1-3 ชั่วโมง และบางครั้งข้ามรอบไปเลย จึงมีทางเลือก 2 แบบ

**แบบ ก. (ตั้งไว้แล้ว ไม่ต้องทำอะไร)** ตั้งเวลาให้เช้ากว่าเวลาอ่านจริง รอบหลัก 03:10 น. ถ้าช้า 2-3 ชั่วโมงก็ยังเสร็จราว 06:00 น. และถ้าต้องการให้เผื่อมากขึ้น แก้เลขในบรรทัด `cron:` ของ `.github/workflows/daily.yml` (เวลา UTC = เวลาไทย − 7)

**แบบ ข. (เวลาตรงภายใน 1 นาที)** ใช้บริการตั้งเวลาฟรีภายนอก เช่น [cron-job.org](https://cron-job.org) ให้สั่ง GitHub รันตามเวลาที่ต้องการ
1. สร้าง token: GitHub → Settings (ของบัญชี) → Developer settings → **Fine-grained tokens** → Generate new token
   - Repository access: **Only select repositories** → เลือก repo นี้
   - Permissions → Repository permissions → **Actions: Read and write**
   - ตั้งวันหมดอายุ และจดไว้ว่าต้องสร้างใหม่ก่อนหมด
2. ที่ cron-job.org สมัครแล้วกด **Create cronjob** สร้าง 2 งาน
   - URL: `https://api.github.com/repos/<ชื่อผู้ใช้>/<ชื่อ repo>/actions/workflows/daily.yml/dispatches`
   - Schedule: งานแรก 03:10 งานที่สอง 05:15 (หรือเวลาที่ต้องการ), Time zone: **Asia/Bangkok**
   - Advanced → Request method: **POST**
   - Headers:
     - `Authorization: Bearer <token จากข้อ 1>`
     - `Accept: application/vnd.github+json`
     - `X-GitHub-Api-Version: 2022-11-28`
   - Request body งานแรก: `{"ref":"main","inputs":{"mode":"normal"}}`
   - Request body งานที่สอง: `{"ref":"main","inputs":{"mode":"upgrade"}}`
3. กด **Test run** ถ้าได้ผลเป็น `204` แปลว่าใช้ได้ และจะเห็นรอบใหม่ในแท็บ Actions

เก็บตารางเวลาของ GitHub ไว้เป็นตัวสำรองได้ เพราะถ้าฉบับวันนั้นออกแล้ว รอบที่มาทีหลังจะไม่ทำอะไร ถ้าอยากให้รันตามเวลาภายนอกอย่างเดียว ให้ลบบรรทัด `cron:` ทั้งสองบรรทัดใน workflow

## ใช้ Claude แทน Gemini (มีค่าใช้จ่าย)

Claude API ไม่มี free tier มีแค่เครดิตทดลองตอนสมัคร หลังจากนั้นจ่ายตามการใช้งาน
1. สร้าง key ที่ <https://console.anthropic.com> แล้วเพิ่ม Secret ชื่อ `ANTHROPIC_API_KEY`
2. รันครั้งเดียว: Run workflow → provider = `anthropic`
3. ใช้ประจำ: **Settings → Secrets and variables → Actions → Variables** → สร้าง `PROVIDER` = `anthropic`

## ข้อควรทราบ

- Free tier ของ Gemini: Google อาจนำข้อมูลที่ส่งไปใช้ปรับปรุงผลิตภัณฑ์ ซึ่งในระบบนี้เป็นแค่ข่าวสาธารณะ
- Free tier ไม่มี Google Search grounding ระบบจึงดึงข่าวจาก RSS เอง ข้อเท็จจริงทุกข้อมาจากข่าวที่ดาวน์โหลดมา และมีลิงก์ใน Sources ท้ายฉบับ
- เว็บเป็นสาธารณะ ใครมีลิงก์ก็เปิดอ่านได้

## ทดสอบในเครื่อง (ไม่ต้องมี API key)

```bash
pip install -r requirements.txt
PROVIDER=mock python scripts/build.py --date 2026-10-31 --news-fixture tests/fixtures/news.json
python tests/fake_gemini_test.py    # จำลอง Gemini ล่ม/โควตาหมด 13 กรณี (ไม่ใช้โควตาจริง)
# เปิด docs/index.html ดูผล แล้วลบ docs/index.html docs/archive.html docs/issues/* data/issues/*.json ก่อน push
```

## โครงสร้างไฟล์

```
config.yml                 ตั้งค่าทั้งหมด (วันเริ่ม, ข่าว, ธีม, ภาษา, โมเดล)
prompts/common.md          บทบาทและกฎระดับภาษา (ใช้ทั้ง 2 ขั้น)
prompts/articles.md        AI ขั้นที่ 1: บทความ
prompts/lab.md             AI ขั้นที่ 2: Word Bank, Language Lab, Other Languages
scripts/build.py           ตัวหลัก
scripts/plan.py            วันที่ เลขฉบับ ธีม คำทบทวน
scripts/news.py            ดึง RSS และเนื้อข่าว
scripts/llm.py             เชื่อม Gemini / Claude และ JSON schema
scripts/checks.py          ตรวจคุณภาพ (แยกปัญหาร้ายแรง / ข้อสังเกต)
scripts/autofix.py         แก้ปัญหาเล็กๆ ด้วยโค้ด
scripts/render.py          สร้าง HTML
templates/                 หน้าตาเว็บ (base = กรอบหน้า, issue = หนึ่งวัน, archive = รายการทุกฉบับ, style.css)
data/issues/               เนื้อหาทุกฉบับ (ความจำของระบบ)
data/drafts/               บทความที่เขียนเสร็จแล้วแต่ฉบับยังไม่ออก
docs/                      เว็บที่เผยแพร่
tests/                     ข้อมูลทดสอบ + จำลอง Gemini ล่ม (fake_gemini_test.py)
.github/workflows/daily.yml ตารางเวลาและขั้นตอนบน GitHub
```
