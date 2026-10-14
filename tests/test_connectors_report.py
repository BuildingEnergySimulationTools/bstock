from bstock.connectors import ImportReport


def test_ids_are_recorded_once_per_status():
    report = ImportReport(source="rnb")
    report.add("created", "A")
    report.add("created", "A")
    report.add("enriched", "A")
    assert report.created == ["A"]
    assert report.enriched == ["A"]


def test_messages_are_kept_in_order():
    report = ImportReport(source="dpe")
    report.add("enriched", "A", "dpe 1 used")
    report.note("A", "apartment dpe 2 ignored")
    assert report.messages == {"A": ["dpe 1 used", "apartment dpe 2 ignored"]}


def test_to_dataframe_has_one_row_per_id():
    report = ImportReport(source="bdnb")
    report.add("created", "A")
    report.add("enriched", "A", "group site created")
    report.add("not_found", "B")
    report.note("C", "system kept")
    frame = report.to_dataframe()
    assert frame.index.name == "id"
    assert list(frame.index) == ["A", "B", "C"]
    assert list(frame.columns) == ["created", "enriched", "not_found", "ambiguous", "message"]
    assert frame.loc["A", "created"]
    assert frame.loc["A", "enriched"]
    assert not frame.loc["A", "not_found"]
    assert frame.loc["A", "message"] == "group site created"
    assert frame.loc["C", "message"] == "system kept"
    assert frame["created"].dtype == bool


def test_empty_report_gives_empty_frame():
    frame = ImportReport(source="rnb").to_dataframe()
    assert frame.empty
    assert list(frame.columns) == ["created", "enriched", "not_found", "ambiguous", "message"]
