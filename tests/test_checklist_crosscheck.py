from pcbcreator.agents import RequiredItem, Requirements
from pcbcreator.coverage import request_checklist_findings


def _req(*items):
    return Requirements(summary="s", purpose="p", supply="5V", interfaces=[], constraints=[], required=list(items))


def test_dropped_parts_are_caught_from_the_words_of_the_request():
    request = ("LED board: 5 V input from a 2-pin connector and one red LED with a series current-limiting resistor "
              "for about 5 mA.")
    only_conn = _req(RequiredItem(kind="connector", detail="5 V input"))
    msgs = [f.message for f in request_checklist_findings(request, only_conn)]
    assert len(msgs) == 1 and "LED" in msgs[0]
    full = _req(RequiredItem(kind="connector", detail="in"), RequiredItem(kind="led", detail="red"))
    assert request_checklist_findings(request, full) == []


def test_connector_count_follows_the_n_pin_mentions():
    request = "Power board: input from a 2-pin connector, a fixed 3.3 V LDO, and a 2-pin 3.3 V output connector."
    one = _req(RequiredItem(kind="connector", detail="in"), RequiredItem(kind="linear_regulator", detail="ldo"))
    assert any("connector" in f.message for f in request_checklist_findings(request, one))
    two = _req(RequiredItem(kind="connector", count=2, detail="in/out"), RequiredItem(kind="linear_regulator", detail="x"))
    assert request_checklist_findings(request, two) == []
