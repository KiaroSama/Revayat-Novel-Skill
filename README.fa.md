<div dir="rtl">

# روایت — Revayat Novel

**ترجمهٔ کامل یک کتاب به فارسیِ در حد چاپ، و تحویل یک فایل ورد که یک ناشر بتواند رویش کار کند.**

یک Agent Skill برای Claude Code، Kiro، Codex، Cursor، Cline و هر ایجنت کدنویسی دیگری که بتواند یک `SKILL.md` را بخواند. کارهایی را انجام می‌دهد که ترجمهٔ کتاب را واقعاً سخت می‌کنند: صفحه‌های اسکن‌شده، تصویرهایی که باید اندازه و جایشان حفظ شود، نام‌هایی که نباید در طول چهل فصل تغییر کنند، و تایپوگرافی فارسی‌ای که باید درست باشد، نه تقریباً درست.

<div align="right"><a href="LICENSE">مجوز MIT</a></div>
<div align="left"><a href="README.md">English</a></div>

---

## چه چیزی آن را از یک مترجم عمومی جدا می‌کند

| | |
| --- | --- |
| **کتاب اسکن‌شده، دیجیتال و ترکیبی** | لایهٔ متنی هر صفحه جداگانه بررسی می‌شود. کتاب‌های ترکیبی — که برای عنوان‌های قدیمی حالت رایج است — با `--skip-text` او‌سی‌آر می‌شوند تا صفحه‌هایی که متن سالم دارند دوباره بازشناسی نشوند. |
| **تصویرها دست‌نخورده می‌مانند** | بایت‌های اصلی تصویر استخراج می‌شود، نه رندر دوبارهٔ صفحه، و در ورد با اندازهٔ فیزیکی و نسبت ابعاد اصلی قرار می‌گیرد. یک SHA-256 که هنگام استخراج ثبت می‌شود ثابت می‌کند تصویرِ داخل سند همان تصویرِ کتاب است. |
| **پاورقی واقعی ورد** | یک بخش واقعی `word/footnotes.xml` ساخته می‌شود تا خود ورد هر یادداشت را پایین همان صفحه‌ای بگذارد که نشانه‌اش در آن است — نه شمارهٔ بالانویسِ دستی و فهرستی در آخر کتاب. |
| **فهرست مطالبِ کلیک‌پذیر** | بوکمارک واقعی به‌همراه فیلد `TOC` که نتیجهٔ ذخیره‌شده‌اش از پیش یک فهرست لینک‌دار کارآمد است — پس چه نمایشگر فیلدها را به‌روزرسانی بکند و چه نکند، کار می‌کند. |
| **تایپوگرافی فارسی، نه فقط واژهٔ فارسی** | `ی`/`ک`، `، ؛ ؟`، `«»`، ارقام فارسی، و نیم‌فاصله برای `می‌رود` و `کتاب‌ها` — در حالی که نشانی‌های اینترنتی، شناسه‌ها و واژه‌های لاتین از همهٔ قاعده‌ها در امان می‌مانند. |
| **راست‌به‌چپِ اصولی** | `w:bidi` روی پاراگراف، `w:rtl` روی ران‌های فارسی، و نام‌های لاتین که داخل همان جمله چپ‌به‌راست می‌مانند. هیچ رشته‌ای برای جعل جهت برعکس نمی‌شود. |
| **نام‌هایی که در کل کتاب ثابت می‌مانند** | یک واژه‌نامهٔ قفل‌شده به هر chunk تزریق می‌شود، پس فصل ۱۲ نمی‌تواند شخصیتی را که فصل ۳ معرفی کرده دوباره نام‌گذاری کند — و لقب‌ها هم لقب باقی می‌مانند. |
| **حذف واترمارک از اسکن** | واترمارک رنگیِ سوخته‌شده در صفحهٔ اسکن با اشباع رنگ تشخیص داده می‌شود — متن بدنه دقیقاً ۰ اندازه‌گیری شد و واترمارک تا ۲۵۵ می‌رسد — و سفید می‌شود. صفحه‌هایی که واقعاً تصویرند خودکار دست‌نخورده می‌مانند. |
| **دروازه‌های کیفیِ قطعی** | پاراگراف جاافتاده، نشانهٔ پاورقیِ حذف‌شده، حذفِ متن که با نسبت طول گرفته می‌شود، تصویر تغییریافته، و لینک مردهٔ فهرست. همه بر پایهٔ شمارش و هش، نه نظر دوبارهٔ یک مدل. |

## نصب

<div dir="ltr">

```bash
git clone https://github.com/KiaroSama/Revayat-Novel-Skill.git
cd Revayat-Novel-Skill
pip install -r skills/revayat-novel/requirements.txt
```

</div>

سپس اسکیل را در ایجنت‌هایی که استفاده می‌کنید نصب کنید:

<div dir="ltr">

```bash
# macOS / Linux
./install/install.sh

# Windows
powershell -ExecutionPolicy Bypass -File .\install\install.ps1
```

</div>

به‌صورت پیش‌فرض در هر ایجنتی که پیدا کند نصب می‌شود — Claude Code، Kiro، Codex، Cursor، Cline، Hermes، OpenCode و Antigravity. برای OpenCode و Antigravity یک اشاره‌گر در `AGENTS.md` هم نوشته می‌شود، چون این دو دستورها را از آنجا پیدا می‌کنند. با `--agent claude` فقط یکی، و با `--scope project --path <dir>` فقط داخل یک پروژه. هر دو نصب‌کننده روی Linux، macOS و Windows یکسان کار می‌کنند.

### به‌عنوان پلاگین Claude Code

<div dir="ltr">

```
/plugin marketplace add KiaroSama/Revayat-Novel-Skill
/plugin install revayat-novel@KiaroSama/Revayat-Novel-Skill
```

</div>

این کار دستورهای `/translate-book`، `/revayat-novel-resume` و `/revayat-novel-qa` را هم اضافه می‌کند.

### بررسی نصب

<div dir="ltr">

```bash
python skills/revayat-novel/scripts/revayat-novel.py doctor
```

</div>

اختیاری، و فقط برای PDF‌های اسکن‌شده یا ترکیبی:

<div dir="ltr">

```bash
pip install ocrmypdf

# Tesseract (موتور OCR):
winget install tesseract-ocr.tesseract     # Windows
brew install tesseract                     # macOS
sudo apt install tesseract-ocr             # Debian/Ubuntu

# Ghostscript — در winget نیست؛ نصب‌کننده را از اینجا بگیرید:
#   https://ghostscript.com/releases/gsdnld.html
brew install ghostscript                   # macOS
sudo apt install ghostscript               # Debian/Ubuntu
```

</div>

## استفاده

به ایجنت‌تان بگویید، با هر عبارتی که دوست دارید:

> این `book.pdf` را به فارسی ترجمه کن و یک فایل ورد بده.

یا با پلاگین: `/translate-book ./book.pdf`

ایجنت خط لوله را اجرا می‌کند، chunkها را با subagentهای موازی ترجمه می‌کند و آنجاهایی که تصمیم شما لازم است می‌ایستد — مهم‌تر از همه در واژه‌نامه، جایی که تعیین می‌کنید هر شخصیت در فارسی چه نامی داشته باشد.

### یا خودتان مرحله‌به‌مرحله اجرا کنید

<div dir="ltr">

```bash
S=skills/revayat-novel/scripts

python $S/revayat-novel.py extract book.pdf --out work/
python $S/revayat-novel.py glossary scan --book work/book.json --out work/glossary.json
#   … نام‌های فارسی را در work/glossary.json پر کنید …
python $S/revayat-novel.py chunk build --book work/book.json --out work/chunks --glossary work/glossary.json
#   … work/chunks/chunkNNNN.md را ترجمه و در out_chunkNNNN.md بنویسید …
python $S/revayat-novel.py merge  --book work/book.json --chunks work/chunks
python $S/revayat-novel.py falint fix --book work/book.json
python $S/revayat-novel.py qa     check --book work/book.json --assets work/assets --glossary work/glossary.json
python $S/revayat-novel.py build  --book work/book.json --out out/book.fa.docx --font "Vazirmatn"
python $S/revayat-novel.py qa     docx --file out/book.fa.docx --book work/book.json
```

</div>

## معماری

<div dir="ltr">

```
book.pdf / .epub / .docx
        │
        ▼  probe every page: digital · scanned · mixed
   OCRmyPDF ──────── only the pages that need it, images untouched
        │
        ▼
   Book IR  ── blocks, runs, image bytes + geometry, footnotes, page setup
        │      (book.json — the source of truth; Markdown deliberately is not)
        ├──────────────▶ glossary.json ── locked names, aliases, character voices
        ▼
   chapter-aware worksheets ── term table + neighbouring context per chunk
        │
        ▼  translated in parallel, one fresh context each
   merge ── every @@ id must return exactly once, or it is a named error
        │
        ▼
   Persian typography ── ZWNJ, punctuation, digits; protected regions untouched
        │
        ▼
   quality gates ── coverage, footnote parity, omissions, image hashes, glossary
        │
        ▼
   build ── python-docx + raw OOXML for footnotes, bookmarks, TOC, bidi
        │
        ▼
   book.fa.docx  +  package-level verification
```

</div>

**تصمیم اصلی معماری، همان Book IR است.** اگر کتاب را از مسیر Markdown عبور بدهیم، هندسهٔ تصویر، هویت پاورقی و تنظیمات صفحه از دست می‌رود و هیچ دقتی در مراحل بعد آن را برنمی‌گرداند. تنها تأکیدهای درون‌متنی به‌شکل markup منتقل می‌شوند — چون مدل‌ها `*کج*` را به‌مراتب قابل‌اعتمادتر از یک XML اختصاصی مدیریت می‌کنند، و چون QA می‌تواند آن را با شمارش راستی‌آزمایی کند.

## آنچه صادقانه باید گفت

ورد بازجریان (reflow) دارد. طول یک پاراگراف فارسی به‌ندرت با اصل انگلیسی‌اش برابر است، بنابراین یک سند **قابل‌ویرایش** نمی‌تواند هم‌زمان صفحه‌به‌صفحه با PDF مبدأ یکسان باشد. هر ابزاری که هر دو را وعده بدهد، در عمل text boxهای غیرقابل‌ویرایش تولید می‌کند.

اما این‌ها دقیق‌اند: بایت‌های تصویر، اندازهٔ فیزیکی و نسبت ابعاد؛ جای هر تصویر در متن؛ سلسله‌مراتب عنوان‌ها و شکست فصل‌ها؛ بولد و ایتالیک؛ جایگذاری و شماره‌گذاری پاورقی‌ها؛ لینک فصل‌ها؛ و متن فارسیِ قابل‌انتخاب، قابل‌جست‌وجو و قابل‌ویرایش.

دو محدودیت دیگر که بهتر است از ابتدا بدانید:

- تشخیص عنوان در PDF بر پایهٔ اندازهٔ فونت است و در طراحی‌های غیرمعمول اشتباه می‌کند؛ سطح‌ها را پیش از chunk کردن در `book.json` اصلاح کنید، یا از MinerU استفاده کنید.
- در کتاب اسکن‌شده، تصویر معمولاً بخشی از رستر صفحه است نه یک شیء تصویری مستقل، پس چیزی برای استخراج وجود ندارد. مدل layout در MinerU آن‌ها را پیدا می‌کند و `--from-mineru` خروجی‌اش را وارد می‌کند.

## مستندات

اسکیل این‌ها را در زمان نیاز می‌خواند، نه از ابتدا:

- [`translation-policy.md`](skills/revayat-novel/references/translation-policy.md) — یک ترجمهٔ ادبی وفادار چه چیزهایی می‌خواهد
- [`persian-typography.md`](skills/revayat-novel/references/persian-typography.md) — RTL، نیم‌فاصله، نشانه‌گذاری، متن دوخطی
- [`extraction.md`](skills/revayat-novel/references/extraction.md) — مسیریابی OCR، کتاب‌های دشوار، ساختار IR
- [`glossary-and-voice.md`](skills/revayat-novel/references/glossary-and-voice.md) — سیاست نام‌گذاری، نام‌های مستعار، لحن شخصیت
- [`docx-and-ooxml.md`](skills/revayat-novel/references/docx-and-ooxml.md) — همهٔ گزینه‌های ساخت و ساختار ورد حاصل از هرکدام
- [`troubleshooting.md`](skills/revayat-novel/references/troubleshooting.md) — پرتکرارترین مشکل‌هایی که به آن‌ها برمی‌خورید

## توسعه

<div dir="ltr">

```bash
pip install -r skills/revayat-novel/requirements.txt
python -m pytest tests -q
```

</div>

فایل‌های آزمون ساخته می‌شوند، نه commit: مجموعهٔ تست خودش PDF و EPUB و DOCX می‌سازد، پس سریع می‌ماند و هیچ متن کتاب متعلق به دیگران در مخزن قرار نمی‌گیرد.

`tests/e2e_pipeline.py` تمام مراحل را روی یک کتاب ساختگی اجرا می‌کند — استخراج، واژه‌نامه، chunk، merge، تایپوگرافی، QA، ساخت و بازبینی package — تا شکستگی در درزِ میان دو مرحله حتی وقتی تست‌های هر ماژول سبزند هم گرفته شود. CI آن را روی Linux و macOS و Windows اجرا می‌کند.

## سپاس

شکل کلی orchestration — تکه‌کردن کتاب، ترجمهٔ موازی تکه‌ها با subagentها و یک واژه‌نامهٔ مشترک، و ازسرگیری اجرای ناتمام — از رویکردی پیروی می‌کند که [deusyu/translate-book](https://github.com/deusyu/translate-book) (MIT) نشان داده است. استخراج و مسیریابی OCR بر [PyMuPDF](https://github.com/pymupdf/PyMuPDF)، [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF) و به‌صورت اختیاری [MinerU](https://github.com/opendatalab/MinerU) استوارند. ایدهٔ استفاده از یک نمایش میانی برای حفظ صفحه‌آرایی در ترجمه، از [BabelDOC](https://github.com/funstory-ai/BabelDOC) می‌آید.

## حمایت مالی

اگر این پروژه به کارتان آمد، حمایت شما مایهٔ قدردانی است.

</div>

| Currency | Network | Address |
| --- | --- | --- |
| Bitcoin (BTC) | Bitcoin | `bc1qmth5m03pu5hujw5xw5jmywam3jj3sqwqupesdt` |
| USDT, BNB, USDC, etc. | BEP20 | `0x0Bd0BA443a8B9cf15922bf7f0Bb0a4b495fD06Ef` |
| USDT, TRX, USDC, etc. | TRC20 | `TWBA3xFTqgZAeAYMxqo85xWnzvty3DcAhw` |
| Ethereum (ETH) | ERC20 | `0x0Bd0BA443a8B9cf15922bf7f0Bb0a4b495fD06Ef` |
| TON | TON | `UQCN8Umo_OfOWqImZetQsrNStPcmLkMAKajFyiCOhso23NDb` |
| Litecoin (LTC) | LTC | `ltc1qntqnnrunadurnw4cshv3qgspywrueyyeyngwuy` |
| Solana (SOL) | Solana | `7B2wkczUjmkDhETwQuknBL8sUsbuV7nErxc317TmQuwR` |
| Polygon (POL) | Polygon | `0x0Bd0BA443a8B9cf15922bf7f0Bb0a4b495fD06Ef` |

<div dir="rtl">

## نویسنده

نویسنده: Kiaro Sama
گیت‌هاب: https://github.com/KiaroSama

## مجوز

[MIT](LICENSE)
