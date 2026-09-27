from insights.mapping import COLUMNS, MAX_COMPOSE_S, REASON_CODES, Tracker, reason_code

T0 = 1_790_000_000.0


def signal(t, emg=10.0, threshold=35.0, **kw):
    return {"type": "SIGNAL", "t": t, "ch": [1.0, 2.0, 3.0, 4.0], "connected": True, "profile": "taher",
            "emg": emg, "threshold": threshold, "blocked": None, **kw}


def event(t, kind="CLENCH", source="muse", accepted=True, reason=None, strength=0.7, duration=None):
    return {"type": "INPUT_EVENT", "t": t, "kind": kind, "source": source, "accepted": accepted,
            "reason": reason, "strength": strength if kind == "CLENCH" else None, "duration": duration}


METRICS = {"type": "METRICS", "text": "Mija, estoy bien, llámame a las seis.", "selections": 2,
           "scan_steps": 3, "day1_selections": 5, "day1_scan_steps": 6}


def test_signal_row_keeps_counts_and_drops_the_profile_name():
    [row] = Tracker().handle(signal(T0, emg=17.5, threshold=35.0), T0)
    assert row.table == "signal"
    assert row.values["margin"] == 0.5
    assert row.values["connected"] is True and row.values["blocked"] is False
    assert "taher" not in repr(row.values)
    assert set(row.values) == set(COLUMNS["signal"])


def test_signal_blocked_text_is_stored_only_as_a_flag():
    [row] = Tracker().handle(signal(T0, blocked="head moving: Luis turned"), T0)
    assert row.values["blocked"] is True
    assert "Luis" not in repr(row.values)


def test_signal_replayed_on_reconnect_is_not_stored_twice():
    tr = Tracker()
    assert tr.handle(signal(T0), T0)
    assert tr.handle(signal(T0), T0) == []
    assert tr.handle(signal(T0 - 1), T0) == []
    assert tr.handle(signal(T0 + 0.25), T0)


def test_signal_without_optional_fields():
    [row] = Tracker().handle({"type": "SIGNAL", "t": T0, "ch": []}, T0)
    assert row.values["emg"] is None and row.values["margin"] is None and row.values["connected"] is None


def test_every_core_refusal_maps_to_a_code():
    for text, code in REASON_CODES.items():
        assert reason_code(text) == code
    assert reason_code(None) is None
    assert reason_code("jaw muscle too tense", blocked_text="jaw muscle too tense") == "blocked"
    assert reason_code("something new the core started saying") == "other"


def test_refused_gesture_stores_a_code_never_the_text():
    tr = Tracker()
    tr.handle(signal(T0, blocked="Carlos is adjusting the headband"), T0)
    [row] = tr.handle(event(T0 + 0.1, accepted=False, reason="Carlos is adjusting the headband"), T0)
    assert row.values["reason"] == "blocked"
    assert "Carlos" not in repr(row.values)


def test_clench_peak_margin_uses_signal_just_before_it():
    tr = Tracker()
    tr.handle(signal(T0 - 3.0, emg=70.0), T0)          # too early: outside the window
    tr.handle(signal(T0 - 1.0, emg=52.5), T0)          # 1.5x
    tr.handle(signal(T0 - 0.5, emg=42.0), T0)          # 1.2x
    [row] = tr.handle(event(T0), T0)
    assert row.values["peak_margin"] == 1.5
    assert row.values["strength"] == 0.7


def test_keyboard_clench_gets_no_peak_margin():
    tr = Tracker()
    tr.handle(signal(T0 - 0.5, emg=52.5), T0)
    [row] = tr.handle(event(T0, source="dev", strength=1.0), T0)
    assert row.values["peak_margin"] is None and row.values["source"] == "dev"


def test_long_clench_keeps_duration_not_strength():
    [row] = Tracker().handle(event(T0, kind="LONG_CLENCH", duration=2.6, strength=0.9), T0)
    assert row.values["duration"] == 2.6 and row.values["strength"] is None


def test_malformed_input_event_is_ignored():
    assert Tracker().handle({"type": "INPUT_EVENT", "t": T0, "kind": "WINK", "source": "muse", "accepted": True}, T0) == []


def test_metrics_row_has_counts_and_never_the_sentence():
    tr = Tracker()
    tr.handle({"type": "SETTINGS", "scan_ms": 1500}, T0)
    tr.handle(event(T0 + 1), T0 + 1)
    [row] = tr.handle(METRICS, T0 + 9)
    v = row.values
    assert (v["selections"], v["scan_steps"], v["day1_selections"], v["day1_scan_steps"]) == (2, 3, 5, 6)
    assert v["wait_s"] == 4.5 and v["day1_wait_s"] == 9.0
    assert v["compose_s"] == 8.0
    assert "estoy bien" not in repr(v)
    assert set(v) == set(COLUMNS["messages"])


def test_compose_time_starts_at_the_first_accepted_clench_and_resets():
    tr = Tracker()
    tr.handle(event(T0, accepted=False, reason="Muse input is paused"), T0)
    tr.handle(event(T0 + 2), T0 + 2)
    tr.handle(event(T0 + 4), T0 + 4)
    assert tr.handle(METRICS, T0 + 6)[0].values["compose_s"] == 4.0
    assert tr.handle(METRICS, T0 + 7)[0].values["compose_s"] is None


def test_compose_time_of_an_abandoned_message_is_left_empty():
    tr = Tracker()
    tr.handle(event(T0), T0)
    assert tr.handle(METRICS, T0 + MAX_COMPOSE_S + 1)[0].values["compose_s"] is None


def test_metrics_before_any_settings_has_no_wait_seconds():
    [row] = Tracker().handle(METRICS, T0)
    assert row.values["wait_s"] is None and row.values["scan_ms"] is None


def test_state_row():
    [row] = Tracker().handle({"type": "STATE", "t": T0, "level": "elevated", "hr": 94.0,
                              "motion": 0.7, "eyes_closed": False}, T0)
    assert row.table == "body_state" and row.values["bpm"] == 94.0 and row.values["level"] == "elevated"


def test_board_messages_are_not_recorded():
    tr = Tracker()
    for msg in ({"type": "SCREEN", "tiles": [{"label": "Dolor"}]}, {"type": "SPEAK", "text": "hola"},
                {"type": "CONFIRM", "text": "hola"}, {"type": "ACTION_RESULT", "contact": "María"}):
        assert tr.handle(msg, T0) == []


def test_no_table_has_a_column_for_text_or_names():
    for cols in COLUMNS.values():
        assert not {"text", "profile", "contact", "label", "detail"} & set(cols)
