use revayat_persian::glossary::{self, AliasTargets, MentionBlock, Translation};

fn names() -> glossary::Glossary {
    let mut entry = glossary::make_entry(1, "Elizabeth Bennet", "person", 2, ["Lizzy".into()]);
    entry.target = "الیزابت بنت".into();
    entry.first_form = "الیزابت بنت (Elizabeth Bennet)".into();
    entry.alias_targets = AliasTargets::Mapped([("Lizzy".into(), "لیزی".into())].into());
    entry.locked = true;
    let mut glossary = glossary::new_glossary();
    glossary.entries.push(entry);
    glossary
}

#[test]
fn a_locked_full_name_and_its_alias_require_separate_renderings()
-> Result<(), Box<dyn std::error::Error>> {
    let glossary = names();
    let mut block = Translation {
        id: "b1",
        source: "Elizabeth Bennet arrived. They called her Lizzy.",
        target: "لیزی آمد.",
    };
    let findings = glossary::check(&glossary, [block])?;
    assert_eq!(findings.len(), 1);
    assert_eq!(findings[0].source_forms, ["Elizabeth Bennet"]);
    block.target = "الیزابت بنت آمد. او را لیزی صدا زدند.";
    assert!(glossary::check(&glossary, [block])?.is_empty());
    Ok(())
}

#[test]
fn first_mention_keeps_markup_and_ignores_names_inside_urls()
-> Result<(), Box<dyn std::error::Error>> {
    let glossary = names();
    let mut blocks = [
        MentionBlock {
            id: "b1".into(),
            chapter: "c1".into(),
            target: "https://example.test/الیزابت بنت".into(),
        },
        MentionBlock {
            id: "b2".into(),
            chapter: "c1".into(),
            target: "**الیزابت بنت** آمد.".into(),
        },
        MentionBlock {
            id: "b3".into(),
            chapter: "c1".into(),
            target: "**الیزابت بنت** (Elizabeth Bennet) برگشت.".into(),
        },
    ];
    let report = glossary::enforce_first_mentions(&glossary, &mut blocks)?;
    assert_eq!(
        report.introduced.get("g0001").map(String::as_str),
        Some("b2")
    );
    assert_eq!(report.flattened, 1);
    assert_eq!(blocks[0].target, "https://example.test/الیزابت بنت");
    assert_eq!(blocks[1].target, "**الیزابت بنت (Elizabeth Bennet)** آمد.");
    assert_eq!(blocks[2].target, "**الیزابت بنت** برگشت.");
    let once = blocks.clone();
    assert_eq!(
        glossary::enforce_first_mentions(&glossary, &mut blocks)?.flattened,
        0
    );
    assert_eq!(blocks, once);
    Ok(())
}

#[test]
fn scanner_keeps_proper_name_aliases_and_ignores_sentence_openers()
-> Result<(), Box<dyn std::error::Error>> {
    let entries = glossary::scan(
        [
            (
                "b001",
                "Then Elizabeth Bennet arrived. I saw Elizabeth Bennet there.",
            ),
            (
                "b002",
                "Then the guests saw Elizabeth. Then everybody went home.",
            ),
        ],
        1,
        10,
    )?;
    let mut glossary = glossary::new_glossary();
    glossary.entries = entries;
    let found = glossary::lookup(&glossary, "ÉLIZABETH").next();
    assert_eq!(
        found.map(|entry| entry.source.as_str()),
        Some("Elizabeth Bennet")
    );
    assert_eq!(
        found.map(|entry| entry.first_block_id.as_str()),
        Some("b001")
    );
    assert!(glossary::lookup(&glossary, "Then").next().is_none());
    Ok(())
}

#[test]
fn glossary_store_round_trips_persian_and_aliases() -> Result<(), Box<dyn std::error::Error>> {
    let dir = tempfile::tempdir()?;
    let path = dir.path().join("names and terms.json");
    assert!(glossary::load(&path)?.entries.is_empty());
    let glossary = names();
    glossary::save(&glossary, &path)?;
    assert_eq!(glossary::load(&path)?, glossary);
    Ok(())
}
