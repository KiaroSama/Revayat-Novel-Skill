<div dir="rtl" lang="fa">

<h1 align="center">‏⁧روایت · Revayat Novel⁩</h1>

<div align="center">

[![CI](https://img.shields.io/github/actions/workflow/status/KiaroSama/Revayat-Novel-Skill/ci.yml?style=flat-square&color=16a34a&label=CI)](.github/workflows/ci.yml)
[![CodeQL](https://img.shields.io/github/actions/workflow/status/KiaroSama/Revayat-Novel-Skill/codeql.yml?style=flat-square&color=0d9488&label=CodeQL)](.github/workflows/codeql.yml)
[![Gitleaks](https://img.shields.io/github/actions/workflow/status/KiaroSama/Revayat-Novel-Skill/gitleaks.yml?style=flat-square&color=2563eb&label=Gitleaks)](.github/workflows/gitleaks.yml)
[![License](https://img.shields.io/github/license/KiaroSama/Revayat-Novel-Skill?style=flat-square&color=4f46e5)](LICENSE)
[![Latest Git tag](https://img.shields.io/github/v/tag/KiaroSama/Revayat-Novel-Skill?style=flat-square&color=7c3aed&label=Version)](#releases-and-tags)
[![Rust 1.100.0](https://img.shields.io/badge/Rust-1.100.0-8b5cf6)](#installation)
[![64-bit Windows and Linux](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%2864--bit%29-0891b2)](#installation)
[![Five plugin formats](https://img.shields.io/badge/Plugins-5%20formats-0284c7)](#plugin-formats)
[![Bundled Vazirmatn v33.003](https://img.shields.io/badge/Font-Vazirmatn%20v33.003-059669)](#vazirmatn-font)
[![Six-stage multi-agent workflow](https://img.shields.io/badge/Workflow-6--stage%20multi--agent-d97706)](#six-stage-workflow)
[![Repository size](https://img.shields.io/github/repo-size/KiaroSama/Revayat-Novel-Skill?style=flat-square&color=ca8a04)](#repository-layout)
[![Pure Rust](https://img.shields.io/badge/Built%20with-Pure%20Rust-ea580c)](#architecture)
[![Four bundled skills](https://img.shields.io/badge/Skills-4%20bundled-65a30d)](skills/README.md)
[![Support donations](https://img.shields.io/badge/Support-donations-d04a9a)](#donate)

</div>

‏⁧**کتاب را کامل به فارسی ترجمه کنید و فایل وردی بگیرید که ناشر بتواند روی آن کار کند.**⁩

‏⁧روایت برای ترجمهٔ کتاب و رمان دنباله‌دار طراحی شده است. ورودی آن می‌تواند فایل `PDF`، `EPUB`، `DOCX`، متن ساده یا نشانی وب باشد. روایت صفحه‌های اسکن‌شده را هم می‌پذیرد، اندازهٔ تصویر را در صورت مشخص‌بودن حفظ می‌کند و واژه‌نامه‌ای مشترک در اختیار مترجم می‌گذارد تا نام‌ها از فصلی به فصل دیگر عوض نشوند. برای رمان اینترنتی می‌توانید از نشانی سایت یا فصل‌های ذخیره‌شده به‌ترتیب استفاده کنید. [راهنمای ورود رمان اینترنتی](skills/revayat-novel/references/web-novels.md) توضیح می‌دهد چطور نسخهٔ مبدأ را نگه دارید و کار نیمه‌تمام را از سر بگیرید.⁩

<div align="left"><a href="README.md">English</a></div>

<a id="installation"></a>

## ‏⁧نصب⁩

‏⁧فایل اجرایی `revayat-novel` برای ویندوز و لینوکس ۶۴ بیتی، روی پردازنده‌های `x64` و `arm64`، ساخته می‌شود. نصب افزونه جای نصب فایل اجرایی را نمی‌گیرد. اگر در [انتشارها](https://github.com/KiaroSama/Revayat-Novel-Skill/releases) بستهٔ امضاشده‌ای برای دستگاه شما هست، طبق راهنمای همان انتشار پیش بروید. اگر از روی کد می‌سازید، `Rust 1.100.0` را با [rustup](https://rustup.rs/) نصب کنید. در لینوکس به ابزارهای ساخت بومی و در ویندوز به ابزارهای ساخت ویژوال استودیو با مؤلفهٔ `C++` و `Windows SDK` نیاز دارید. ممکن است وابستگی‌های ارتباط رمزنگاری‌شده به `CMake` هم نیاز داشته باشند.⁩

‏⁧در لینوکس، از پوشهٔ اصلی مخزن اجرا کنید:⁩

```bash
cargo build --release --locked --bin revayat-novel
install -Dm755 target/release/revayat-novel bin/revayat-novel
./bin/revayat-novel doctor --json
./bin/revayat-novel install --agent claude --scope project --path "/path/to/project"
```

‏⁧در ویندوز، از پوشهٔ اصلی مخزن و در `PowerShell 7` اجرا کنید:⁩

```powershell
cargo build --release --locked --bin revayat-novel
New-Item -ItemType Directory -Force bin | Out-Null
Copy-Item target/release/revayat-novel.exe bin
.\bin\revayat-novel.exe doctor --json
.\bin\revayat-novel.exe install --agent claude --scope project --path 'C:\work\my project'
```

‏⁧پوشهٔ پروژه باید از قبل وجود داشته باشد. نصب‌کننده از `claude`، `kiro`، `codex`، `cursor`، `cline`، `hermes`، `opencode` و `antigravity` پشتیبانی می‌کند. گزینهٔ `--agent all` فقط در پوشه‌های موجود عامل‌ها نصب می‌کند. برای نصب در حساب کاربری از `--scope user` استفاده کنید و برای بررسی نصب، همان دستور را با `--status` اجرا کنید. نصب‌کننده اسکیل کتاب، قلم‌ها و فایل اجرایی را کپی می‌کند و هنگام جایگزینی با `--force` نسخهٔ پیشین را نگه می‌دارد. اگر عامل فایل اجرایی را پیدا نکرد، پوشهٔ مناسب `bin/` را به `PATH` اضافه کنید.⁩

‏⁧دستور `doctor --json` معماری ساخت، وضعیت قلم‌ها و پرونده‌های افزونه و ابزارهای اختیاری را گزارش می‌کند. برای بازشناسی متن اسکن، `Tesseract` را با بسته‌های زبان انگلیسی (`eng`) و فارسی (`fas`) نصب کنید. برای بازبینی ظاهر سند به `LibreOffice` (`soffice`) یا مایکروسافت ورد در ویندوز نیاز دارید. نبود این ابزارها بررسی همان مرحله را محدود می‌کند؛ خود برنامه همچنان اجرا می‌شود.⁩

<a id="plugin-formats"></a>

## ‏⁧قالب‌های افزونه⁩

- Claude Code: [Plugin](.claude-plugin/plugin.json), [marketplace](.claude-plugin/marketplace.json)
- Codex: [Plugin](.codex-plugin/plugin.json), [marketplace](.agents/plugins/marketplace.json)
- Cursor: [Plugin](.cursor-plugin/plugin.json), [marketplace](.cursor-plugin/marketplace.json)
- Antigravity: [Plugin](.antigravity-plugin/plugin.json)
- Agent Plugins: [Standard manifest](plugin.json)

‏⁧برای نصب در `Claude Code`، اول بازارچه و بعد خود افزونه را اضافه کنید:⁩

```text
/plugin marketplace add KiaroSama/Revayat-Novel-Skill
/plugin install revayat-novel@revayat-novel-skill
```

‏⁧برای شروع ترجمه از `/revayat-novel:translate-book`، برای ادامه از `/revayat-novel:revayat-novel-resume` و برای بررسی سند از `/revayat-novel:revayat-novel-qa` استفاده کنید. اسکیل‌های ویرایش [`persian`، `great-russian` و `humanizer` نسخهٔ `4.0.0`](skills/README.md) هم در بسته هستند. اسکیل را با توجه به زبان نثر انتخاب کنید؛ خروجی ترجمهٔ کتاب همیشه فارسی است.⁩

<a id="vazirmatn-font"></a>

## ‏⁧قلم وزیرمتن⁩

‏⁧چهار وزن معمولی، ضخیم، متوسط و سبکِ [وزیرمتن نسخهٔ `v33.003`](skills/revayat-novel/assets/fonts/vazirmatn/) با [مجوز آزاد قلم](skills/revayat-novel/assets/fonts/vazirmatn/OFL.txt) همراه برنامه‌اند. سازندهٔ سند برای متن فارسی از وزیرمتن استفاده می‌کند و قلم را در فایل ورد می‌گنجاند. برای اینکه بدانید نمایشگر واقعاً از همین قلم استفاده کرده و چیدمان حفظ شده است، خود سند را رندر و بررسی کنید.⁩

<a id="six-stage-workflow"></a>

## ‏⁧روال شش‌مرحله‌ای⁩

‏⁧فایل مبدأ و مسیر خروجی را به عامل بدهید؛ مثلاً «`book.epub` را به فارسی ترجمه کن و فایل ورد قابل ویرایش را در `edition.docx` تحویل بده.» برای هر کتاب پوشهٔ کاری جدا بگذارید. ورودی‌ها، مسئولیت‌ها و شرط پایان هر مرحله در [راهنمای کامل گردش کار](skills/revayat-novel/references/multi-agent-workflow.md) آمده است.⁩

1. ‏⁧**ترجمهٔ اولیه:** نسخهٔ مبدأ را بررسی و وارد کنید. فارسی را روان بنویسید و لحن راوی و طرز حرف‌زدن هر شخصیت را حفظ کنید. املای نام‌ها، عنوان‌ها و اصطلاحات تخصصی را همان موقع در منابع معتبر، مثل وب‌سایت ناشر یا نویسنده، بررسی کنید. نتیجه و نشانی منبع را در واژه‌نامه بنویسید. متن خصوصی کتاب را در جست‌وجوی عمومی وارد نکنید.⁩
2. ‏⁧**حروف‌چینی و صفحه‌آرایی:** برگه‌های تأییدشده را ادغام کنید و سند راست‌به‌چپ بسازید. اندازه‌های مشخص‌شده در مبدأ، بایت‌ها و نسبت ابعاد تصویرها، پاورقی‌های واقعی ورد و فهرست مطالب کلیک‌پذیر را حفظ کنید.⁩
3. ‏⁧**بازبینی مستقل:** درستی معنی و روانی فارسی را جداگانه بسنجید. پیش از اصلاح، همهٔ یافته‌ها را به هماهنگ‌کننده بدهید. اگر بازبین دومی در دسترس نیست، روشن بگویید که همان عامل متن را در نوبتی جدا بررسی کرده است.⁩
4. ‏⁧**اعمال اصلاحات:** پاسخ‌های اصلاح‌شده را ادغام کنید و واژه‌های مصوب و تأکیدهای داخل متن را حفظ کنید. سند را دوباره بسازید و تأییدها را برای نسخهٔ تازه از نو بررسی کنید.⁩
5. ‏⁧**بررسی دوبارهٔ فایل:** بررسی‌های کتاب، ساختار سند و رندر را روی همان نسخه انجام دهید. بعد از دریافت همهٔ نتایج، ایرادها را یک‌جا رفع کنید و بررسی‌های لازم را تکرار کنید.⁩
6. ‏⁧**بازبینی نهایی و تحویل:** فقط نسخه‌ای را تحویل دهید که بررسی همان نسخه تکمیل شده باشد. فایل ورد و گزارش گردش کار را با هم تحویل دهید.⁩

‏⁧کار موازی اختیاری است و به اجازهٔ شما نیاز دارد. دستور `batch plan-shards` یک کتاب را به بخش‌های مستقل تقسیم می‌کند و `batch merge-shards` پاسخ‌ها را دوباره کنار هم می‌گذارد. برای چند کتاب از `batch plan-books` استفاده کنید و برای هر کتاب پوشه و خروجی جدا بگیرید. هر شش مرحله برای تک‌تک کتاب‌ها لازم است. هماهنگ‌کننده واژه‌نامه و گزارش کار را نگه می‌دارد و پاسخ‌ها را ادغام می‌کند.⁩

‏⁧هنگامی که خط فارسی با واژه‌ای انگلیسی آغاز می‌شود، `RLI` (`U+2067`) و `PDI` (`U+2069`) جهت آن خط را از متن اطراف جدا می‌کنند. در متن ساده یا با جهت خودکار، پیش از `RLI` نشانهٔ `RLM` (`U+200F`) را بگذارید. ورد جهت پاراگراف، تراز در ابتدای سطر و قلم خط فارسی را جداگانه تنظیم می‌کند.⁩

## ‏⁧نمونه‌دستورها⁩

‏⁧نمونه‌ها با فرض قرارگرفتن `revayat-novel` در `PATH` نوشته شده‌اند. بین استخراج و ساخت سند باید ترجمه و بازبینی را انجام دهید. گزینه‌های هر دستور را با `--help` ببینید.⁩

```text
revayat-novel extract "book.epub" --out "work/book"
revayat-novel web-import "https://example.org/novel/" --out "work/web-book"
revayat-novel build "work/book" --out "output/edition.docx"
revayat-novel doc-qa check --help
revayat-novel persian-lint --check --style "excerpt.txt"
revayat-novel eval-score --answers "evaluation-answers.json"
```

## ‏⁧گزارش‌ها و محدودیت‌ها⁩

‏⁧هماهنگ‌کننده کنار فایل ورد گزارش `translation_YYYY-MM-DD_HH-mm-ss_UTC.log` را با کدگذاری `UTF-8` می‌نویسد. وضعیت ورودی، اصلاحات، دلیل توقف، نتیجهٔ بررسی‌ها و شناسهٔ نسخهٔ تحویلی را در آن ثبت کنید. برای ثبت گزارش هر دستور، گزینهٔ `--log-dir <work>` را بدهید تا برنامه گزارش را در `<work>/logs/` بنویسد. گزارش و پوشهٔ کاری را برای ادامهٔ کار نگه دارید و پیش از اشتراک‌گذاری، اطلاعات خصوصی را از گزارش حذف کنید. [راهنمای نگهداری و گزارش کار](skills/revayat-novel/references/preservation-and-logging.md) جزئیات را توضیح می‌دهد.⁩

‏⁧برای بازشناسی طولانی یا کتابی با بیش از حدود ۲۰۰٬۰۰۰ نویسه در متن مبدأ، اول اجازه بگیرید. بررسی پوشش متنِ اسکن، دقت بازشناسی را ثابت نمی‌کند. کتاب‌هایی که متنشان با اندازهٔ صفحه جابه‌جا می‌شود شاید اصلاً ابعاد ثابتی نداشته باشند. اگر بخشی از مبدأ استخراج نشده یا معلوم نیست بازبینی و رندر چه بخش‌هایی را پوشش داده‌اند، تحویل را متوقف کنید.⁩

‏⁧[نمونه‌های ارزیابی](evaluation/cases.json) و [آزمون آفلاین محیط ایزوله](tests/sandbox/README.md) تعداد مشخصی از حالت‌ها را بررسی می‌کنند. نتیجهٔ آن‌ها گواه کیفیت ادبی کتاب یا ممیزی کامل امنیتی نیست.⁩

<a id="architecture"></a>

## ‏⁧معماری⁩

‏⁧خط فرمان و فضای کاری با `Rust` نوشته شده‌اند. `book.json` مرجع اصلی وضعیت کتاب است. پاسخ‌های تأییدشده پس از اعتبارسنجی و زیر قفل اعمال می‌شوند تا اطلاعات لازم برای بازیابی از بین نرود. سازندهٔ ورد کتاب و دارایی‌هایش را می‌خواند. ابزارهای بررسی، ساختار سند را می‌سنجند و مدرک بازبینی را به نسخهٔ مشخصی از کتاب پیوند می‌دهند.⁩

<a id="repository-layout"></a>

## ‏⁧چیدمان مخزن⁩

‏⁧`crates/` شامل خط فرمان، مدل کتاب، ابزارهای ورود، پردازش فارسی، ساخت ورد، مراحل ترجمه، نصب و ارزیابی است.⁩

‏⁧`bin/` محل فایل نهایی `revayat-novel.exe` در ویندوز و `revayat-novel` در لینوکس است. [پوشهٔ اسکیل‌ها](skills/README.md) راهنمای کتاب، اسکیل‌های ویرایش و قلم‌ها را دارد. نمونه‌های ارزیابی در [`evaluation/`](evaluation/) و تعریف آزمون‌های خودکار در [`.github/workflows/`](.github/workflows/) هستند.⁩

‏⁧شیوهٔ تقسیم و هماهنگی کار از [deusyu/translate-book](https://github.com/deusyu/translate-book) الهام گرفته است. ایدهٔ حفظ چیدمان با مدل میانی کتاب از [BabelDOC](https://github.com/funstory-ai/BabelDOC) می‌آید.⁩

<a id="releases-and-tags"></a>

## ‏⁧انتشارها و تگ‌ها⁩

‏⁧وقتی تگ توضیح‌داری مثل `v3.0.0` را در گیت منتشر کنیم، نشان `Version` خودکار از [تگ‌های گیت](https://github.com/KiaroSama/Revayat-Novel-Skill/tags) به‌روز می‌شود. بسته‌ها را از [صفحهٔ انتشارهای گیت‌هاب](https://github.com/KiaroSama/Revayat-Novel-Skill/releases) بگیرید.⁩

‏⁧گردش کار انتشار، فایل‌های اجرایی را در محیط محافظت‌شدهٔ `revayat-novel-signing` در `GitHub Actions` امضا می‌کند. [راهنمای بررسی امضا](docs/signing/README.md) توضیح می‌دهد چطور امضای `Authenticode` در ویندوز، امضای `Ed25519` در لینوکس، هش‌های `SHA256SUMS.txt` و گواهی گیت‌هاب دربارهٔ منشأ ساخت را بررسی کنید.⁩

‏⁧نتایج روی ویندوز `x64` ثبت شده‌اند. زمان و مصرف رم، میانهٔ پنج بار اجرای هر دستور است.⁩

| ‏⁧دستور⁩ | ‏⁧`v2.0.0` (پایتون)⁩ | ‏⁧`v3.0.0` (فقط `Rust`)⁩ | ‏⁧تغییر⁩ |
| --- | --- | --- | --- |
| `doctor --json` | ‏⁧۲۱۹۴٫۱ میلی‌ثانیه / ۱۳۷٫۰ مگابایت رم⁩ | ‏⁧۴۲٫۳ میلی‌ثانیه / ۶٫۳ مگابایت رم⁩ | ‏⁧۹۸٫۱٪ سریع‌تر، ۹۵٫۴٪ رم کمتر⁩ |
| ‏⁧`persian-lint` (۵۰۰ خط)⁩ | ‏⁧۱۶۰٫۹ میلی‌ثانیه / ۱۸٫۱ مگابایت رم⁩ | ‏⁧۵۹٫۲ میلی‌ثانیه / ۷٫۶ مگابایت رم⁩ | ‏⁧۶۳٫۲٪ سریع‌تر، ۵۷٫۹٪ رم کمتر⁩ |
| ‏⁧`chunk build` (کتاب نمونه)⁩ | ‏⁧۲۵۵٫۰ میلی‌ثانیه / ۲۴٫۱ مگابایت رم⁩ | ‏⁧۴۱٫۱ میلی‌ثانیه / ۷٫۵ مگابایت رم⁩ | ‏⁧۸۳٫۹٪ سریع‌تر، ۶۸٫۸٪ رم کمتر⁩ |
| ‏⁧`persian-lookup` (۲۰۰ خط)⁩ | ‏⁧۱۴۲٫۵ میلی‌ثانیه / ۱۴٫۷ مگابایت رم⁩ | ‏⁧۱۸۴٫۷ میلی‌ثانیه / ۸٫۱ مگابایت رم⁩ | ‏⁧۲۹٫۷٪ کندتر، ۴۵٫۳٪ رم کمتر⁩ |
| ‏⁧حجم برنامه⁩ | ‏⁧۲۳۸ اسکریپت پایتون و شل (۲٫۵ مگابایت)، به‌اضافهٔ مفسر و محیط مجازی پایتون⁩ | ‏⁧۱ فایل اجرایی بومی (۲۲٫۴ مگابایت)، بدون وابستگی به پایتون⁩ | |

## ‏⁧نویسنده⁩

[Kiaro Sama](https://github.com/KiaroSama) · [Kiaro.Sama.Dev@gmail.com](mailto:Kiaro.Sama.Dev@gmail.com)

## ‏⁧مجوز⁩

‏⁧حق نشر روایت متعلق به `Kiaro Sama` است. این پروژه با مجوز [GPL-3.0-or-later](LICENSE) منتشر می‌شود.⁩

<a id="donate"></a>

## ‏⁧حمایت مالی⁩

‏⁧اگر این پروژه به کارتان آمد، حمایت شما مایهٔ قدردانی است.⁩

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
