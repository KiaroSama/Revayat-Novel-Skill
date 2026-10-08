"""Each public scheduler observation loads and indexes once, but later calls are live."""
import bookir as ir
import pageidentity
import pagerun
import runstate


def test_next_page_reuses_operation_proofs_without_persistent_cache(tmp_path, monkeypatch):
    book = ir.new_book(source_format="epub")
    book["blocks"] = [ir.make_block("paragraph", i, page=i, text=f"Source {i}.")
                      for i in range(1, 4)]
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    pages = tmp_path / "pages"
    pagerun.build(path, pages)
    state = runstate.RunState(tmp_path)
    for page in range(1, 4):
        state.set_page(page, "merged", hashes={"translation": pagerun.translation_hash(path, page)})
    actual_load, actual_owners = ir.load_book, pageidentity.owners
    calls = {"load": 0, "owners": 0}
    def load(*args, **kwargs):
        calls["load"] += 1
        return actual_load(*args, **kwargs)
    def owners(*args, **kwargs):
        calls["owners"] += 1
        return actual_owners(*args, **kwargs)
    monkeypatch.setattr(ir, "load_book", load)
    monkeypatch.setattr(pageidentity, "owners", owners)
    result = pagerun.next_page(pages)
    assert result["page"] == 1
    assert calls == {"load": 1, "owners": 1}
    book["blocks"][0]["target"] = "تغییر تازه"
    ir.save_book(book, path)
    assert pagerun.status(pages)["pages"][0]["state"] == "stale"
