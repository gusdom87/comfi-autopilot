"""Tests for the deterministic frame gate (plays/frame_gate.py).

Synthetic pixel grids only: no real photos, no real people. The gate's
job is to refuse frames that do not actually contain a usable subject,
including the exact failure seen live on Oct 27, 2026: a delivered
still whose enlarged frame contained no person at all.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jev.plays.frame_gate import gate_frame, centered_crop_box

W, H = 200, 120
BG = (100, 120, 80)        # grass-ish
PERSON = (60, 40, 150)     # strong contrast in every channel


def _frame(person_box=None):
    px = []
    for y in range(H):
        for x in range(W):
            c = BG
            if person_box and person_box[0] <= x <= person_box[1] \
                    and person_box[2] <= y <= person_box[3]:
                c = PERSON
            px.extend(c)
    return px


REF = _frame()


def test_empty_frame_fails_no_subject():
    r = gate_frame(_frame(), REF, W, H)
    assert not r.passed
    assert r.subject is None
    assert "no subject" in r.reason


def test_centered_person_passes():
    r = gate_frame(_frame((85, 115, 25, 105)), REF, W, H)
    assert r.passed, r.reason
    assert r.subject["height_share"] >= 0.12
    box = centered_crop_box(r.subject, W, H)
    cx = (r.subject["bbox"]["x_min"] + r.subject["bbox"]["x_max"]) / 2
    assert box[0] <= cx <= box[2]


def test_offcenter_unclipped_person_passes_and_crop_centers():
    r = gate_frame(_frame((140, 170, 25, 105)), REF, W, H)
    assert r.passed, r.reason
    box = centered_crop_box(r.subject, W, H)
    cx = (r.subject["bbox"]["x_min"] + r.subject["bbox"]["x_max"]) / 2
    center = (box[0] + box[2]) / 2
    assert abs(center - cx) <= (box[2] - box[0]) * 0.2


def test_edge_clipped_person_fails():
    r = gate_frame(_frame((0, 30, 25, 105)), REF, W, H)
    assert not r.passed
    assert r.gates["not_clipped_by_edge"] is False


def test_tiny_person_fails():
    r = gate_frame(_frame((95, 104, 55, 64)), REF, W, H)
    assert not r.passed


def test_size_mismatch_is_an_error():
    try:
        gate_frame([0, 0, 0], REF, W, H)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a size mismatch")
