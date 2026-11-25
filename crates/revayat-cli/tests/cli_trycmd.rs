#[test]
fn cli_trycmd_snapshots() {
    trycmd::TestCases::new().case("tests/cmd/*.toml");
}
