"""Staged fact sheet for the knowledge base (knowledge.py).

Two invented fixture properties: no real addresses, no real codes, no real
guest data. Statuses cover the full range: known operator facts, an
unverified Knowledge Hub style item, an unknown fact, and one operator
text whose raw form carries a (staged) entry code, scrubbed at
construction exactly as the live builder scrubs ingest text.
"""
from jev.knowledge import scrub_text

# Raw operator text as a staged check-in message would carry it. The code
# sentence must never reach the sheet.
MAPLE_LAUNDRY_RAW = ("The washer and dryer are in the basement laundry room, left side. "
                     "The laundry room keypad code is 0-0-0-0. "
                     "Detergent is on the shelf above the washer.")


def default_fact_sheet():
    """A fresh copy of the staged sheet on every call."""
    return {
        "schema": "comfi-autopilot/fact-sheets/staged-v1",
        "properties": {
            "prop-harbor": {
                "name": "Harbor House (staged)",
                "facts": {
                    "trash_location": {
                        "value": "the outdoor bins by the three-car garage on the far driveway",
                        "status": "known", "source": "operator: staged check-in message, TRASH"},
                    "parking_instructions": {
                        "value": "Guests usually park on the street out front.",
                        "status": "unverified", "source": "knowledge-hub"},
                    "wifi_password": {
                        "value": "tidepool-staged-4417",
                        "status": "known", "source": "operator: staged property record .details.wifi_password"},
                    "tv_details": {"value": None, "status": "unknown", "source": None},
                },
            },
            "prop-maple": {
                "name": "Maple Flat (staged)",
                "facts": {
                    "checkin_time": {
                        "value": "4 PM",
                        "status": "known", "source": "operator: staged property record .details.check_in"},
                    "trash_location": {
                        "value": "Bins are in the alley behind the building.",
                        "status": "unverified", "source": "knowledge-hub"},
                    "laundry_details": {
                        "value": scrub_text(MAPLE_LAUNDRY_RAW),
                        "status": "known", "source": "operator: staged check-in message, LAUNDRY ROOM"},
                },
            },
        },
    }
