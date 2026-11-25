use revayat_persian::{falint, famorph, isolate_persian_line, lookup, persian_lint};

#[test]
fn english_first_persian_line_is_isolated_once() {
    let text = "PDF را باز کن.\r\n";
    let isolated = isolate_persian_line(text);
    assert_eq!(isolated, "\u{200f}\u{2067}PDF را باز کن.\u{2069}\r\n");
    assert_eq!(isolate_persian_line(&isolated), isolated);
    assert_eq!(isolate_persian_line("PDF only"), "PDF only");
}

#[test]
fn publication_does_not_join_the_noun_for_wine() -> Result<(), Box<dyn std::error::Error>> {
    assert!(!famorph::is_verb_form("سفید"));
    assert_eq!(
        falint::fix_text("می سفید و **می رود**", &falint::Options::default())?,
        "می سفید و **می‌رود**"
    );
    Ok(())
}

#[test]
fn standalone_mechanics_preserves_protected_tokens() -> Result<(), Box<dyn std::error::Error>> {
    let (fixed, stats) =
        persian_lint::normalize("كتاب 12 `كتاب 12`", persian_lint::Digits::Persian)?;
    assert_eq!(fixed, "کتاب ۱۲ `كتاب 12`");
    assert_eq!(stats.get("digits_to_persian"), Some(&2));
    assert_eq!(
        persian_lint::normalize(&fixed, persian_lint::Digits::Persian)?.0,
        fixed
    );
    Ok(())
}

#[test]
fn lookup_matches_inflected_final_token_and_keeps_the_first_entry()
-> Result<(), Box<dyn std::error::Error>> {
    let rows =
        lookup::parse_tsv("business partner\tشریک تجاری\nbusiness partner\tشریک\nAI\tهوش مصنوعی\n");
    let hits = lookup::find_en(&rows, "BUSINESS partners, not aid")?;
    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0].fa, "شریک تجاری");
    Ok(())
}
