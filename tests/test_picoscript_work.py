from picoscript_work import TraceEvent, TraceSink, WorkBudget


def test_bounded_trace_drop_digest_and_clear():
    sink = TraceSink(1)
    assert sink.append(TraceEvent(1, 0, token=4, payload=b"x"))
    assert not sink.append(TraceEvent(2, 1))
    assert sink.dropped == 1 and sink.next().kind == 1
    assert len(sink.digest()) == 64
    sink.clear()
    assert sink.next() is None and sink.dropped == 0


def test_budget_exhaustion_and_cancellation_are_explicit():
    budget = WorkBudget(2, 1, 8)
    assert budget.consume(1, 1, 4)
    assert not budget.consume(2)
    budget.cancel()
    assert not budget.consume(1)
