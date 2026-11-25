//! Conservative Persian morphology for publication typography.

const PRESENT_STEMS: &str = "رو آی کن شو گو بین خور ده گیر دان توان خواه مان آور زن بر نویس خوان نشین ایست افت رس پرس فهم کش گذار دار باش یاب شنو خواب ترس لرز چرخ گرد پذیر سپار بخش اندیش گریز پوش شکن سوز رنج خند گری جوش پر دو آ";
const PAST_STEMS: &str = "رفت آمد کرد شد گفت دید خورد داد گرفت دانست توانست خواست ماند آورد زد برد نوشت خواند نشست ایستاد افتاد رسید پرسید فهمید کشید گذاشت داشت بود یافت شنید خوابید ترسید لرزید چرخید گشت پذیرفت سپرد بخشید اندیشید گریخت پوشید شکست سوخت رنجید خندید گریست جوشید پرید دوید";
const PERSONAL: &str = "م ی د یم ید ند";
const COMPARABLE: &str = "بزرگ کوچک ریز درشت بلند کوتاه پهن باریک کلان عظیم وسیع تنگ گسترده عمیق کم زیاد بیش اندک روشن تاریک سفید سیاه تیره پررنگ کم‌رنگ گرم سرد داغ خنک سوزان یخ خوب بد زیبا زشت قوی ضعیف سخت نرم آسان دشوار گران ارزان تازه کهنه نو قدیمی جدید مهم بهتر ساده پیچیده دقیق درست نادرست روان سنگین سبک جوان پیر خوشحال غمگین شاد ناراحت آرام عصبانی مهربان تند کند سریع آهسته خسته هوشیار دانا نادان شجاع ترسان مشهور ناشناس نزدیک دور شبیه متفاوت خالی پر پاک کثیف خشک تمیز شلوغ ساکت بلندتر محکم شکننده زنده مرده سالم بیمار گرسنه سیر تشنه";

pub fn is_verb_form(word: &str) -> bool {
    let word = word.strip_suffix('\n').unwrap_or(word);
    let contains = |words: &str, stem| words.split(' ').any(|candidate| candidate == stem);
    contains(PAST_STEMS, word)
        || PERSONAL.split(' ').any(|ending| {
            word.strip_suffix(ending)
                .is_some_and(|stem| contains(PRESENT_STEMS, stem) || contains(PAST_STEMS, stem))
        })
}

pub fn takes_comparative(word: &str, suffix: &str) -> bool {
    suffix == "ترین" || COMPARABLE.split(' ').any(|candidate| candidate == word)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recognizes_bare_past_stems_but_not_bare_present_stems() {
        assert!(is_verb_form("رفت"));
        assert!(is_verb_form("روند"));
        assert!(!is_verb_form("رو"));
    }
}
