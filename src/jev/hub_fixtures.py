"""Staged Knowledge Hub export for hub_answers.py (built Oct 31, 2026).

Pseudonym fixture world only: the four Cedar Hollow single units and the
Cedar Hollow Retreat. Every fact, code, and name here is invented; nothing
comes from a real listing, a real Knowledge Hub, or a real guest. The
markdown follows the private Knowledge Hub export's layout (one "## Topic"
heading per Hub topic, items tagged "[edited]" or "[auto]", multi-line
items running until the next item or heading), so the parser is tested on
the same shape it reads in private.

Built in on purpose, so the tests can show them held:
  - staged codes inside edited items (a laundry door code, a lockbox
    combination in dial format, a fixed laundry code as a digit run);
  - barred topics stated in edited items (pets, parking, occupancy, an
    address, a fee);
  - [auto] items that would answer a question if they were ever a source;
  - two conflicts: Garden Unit 1B's trash item against its own checkout
    steps, and Studio 2A's coffee item against an operator-confirmed fact;
  - one consistent pair: Upper Unit 2B's trash item and its confirmed fact.
"""
from datetime import datetime, timedelta, timezone

from .hub_answers import sha256

CEDAR_1A, CEDAR_1B, CEDAR_2A, CEDAR_2B, CEDAR_RETREAT = (
    "cedar-1a", "cedar-1b", "cedar-2a", "cedar-2b", "cedar-retreat")
SINGLE_UNITS = (CEDAR_1A, CEDAR_1B, CEDAR_2A, CEDAR_2B)
NAMES = {
    CEDAR_1A: "Garden Unit 1A, Cedar Hollow",
    CEDAR_1B: "Garden Unit 1B, Cedar Hollow",
    CEDAR_2A: "Studio 2A, Cedar Hollow",
    CEDAR_2B: "Upper Unit 2B, Cedar Hollow",
    CEDAR_RETREAT: "Cedar Hollow Retreat | Sleeps 10 | 4 Units",
}

HUB_MARKDOWN = {
    CEDAR_1A: """# Garden Unit 1A, Cedar Hollow (staged)

Source: staged fixture in the Knowledge Hub export layout. Invented facts.

## Arrival
- [edited] Check-in time is 16:00 (4:00 PM).

## Trash Disposal
- [edited] Trash goes in the gray bins on the side of the garage, next to the recycling cart.

## Laundry
- [edited] Laundry Room:
The shared laundry room is in the basement, down the back stairs.
The laundry room door code is 4-1-7-7, the same for every guest.
Detergent and dryer sheets are on the shelf above the washer.

## Coffee & Kitchen Appliances
- [edited] The kitchen has a drip coffee maker and an electric kettle.
- [edited] Ground coffee and paper filters are in the cabinet above the sink.

## Pet Policy
- [edited] Dogs are welcome with a pet fee of $40 per stay.

## Parking
- [edited] Park in the left lane of the driveway, one car behind the other.

## Emergency keys
- [edited] A backup key is in the black lockbox by the back steps, combination 9-6-9-2.

## Rules & Regulations
- [edited] No smoking anywhere on the property, including the porch.
- [edited] Quiet hours are 10:00 PM to 7:00 AM.

## TV
- [auto] The living room TV has Netflix and Hulu.

## Heating System
- [auto] The radiators take about an hour to warm the unit.
""",
    CEDAR_1B: """# Garden Unit 1B, Cedar Hollow (staged)

Source: staged fixture in the Knowledge Hub export layout. Invented facts.

## Trash & Recycling
- [edited] Before leaving, please take all trash and recycling out to the black bins in the alley behind the building.

## Check-out Policies
- [edited] Standard check-out time is 11:00 AM. Guests may request a late checkout until 12:00 PM, subject to approval.

Before leaving, please:
• Place used towels in the bathtub.
• Take all trash out to the bins by the front curb.
• Turn off all lights.

## Laundry
- [edited] The washer and dryer are inside the unit, in the hallway closet.
- [edited] Laundry detergent pods are under the kitchen sink.

## Towels & Linens
- [edited] Extra bath towels are on the top shelf of the bathroom closet.

## Occupancy
- [edited] The unit sleeps up to four guests, including children.

## Smoking
- [auto] Smoking is allowed on the back patio.
""",
    CEDAR_2A: """# Studio 2A, Cedar Hollow (staged)

Source: staged fixture in the Knowledge Hub export layout. Invented facts.

## Amenities
- [edited] The laundry room is shared with the other units. It is at the front of the building, on the left side when you face the house.

## Wifi/Internet
- [edited] Network: CedarHollow-Guest

## Coffee
- [edited] There is a single-serve pod coffee maker on the counter. Coffee pods are in the drawer below it.

## Address & Location
- [edited] The studio is at 412 Hollow Road, upstairs on the right.

## Extra Bedding
- [edited] An extra blanket and two spare pillows are in the closet by the front door.

## Rules & Regulations
- [edited] Smoking is not allowed inside the studio. Smoking is allowed only at the bench by the side yard.
""",
    CEDAR_2B: """# Upper Unit 2B, Cedar Hollow (staged)

Source: staged fixture in the Knowledge Hub export layout. Invented facts.

## Departure
- [edited] Check-out time is 11:00 AM. Guests should leave the unit tidy.

## Laundry
- [edited] The on-site laundry room is on the left side of the building, shared with the other apartments. It has its own fixed code, 5512, which every guest uses.

## Rules & Regulations
- [edited] Pets are allowed with a pet fee, provided they are well-trained.
- [edited] The unit can accommodate up to two guests, including children.
- [edited] Quiet hours are 10:00 PM to 8:00 AM.

## Heating
- [edited] The radiators heat slowly, so give the unit about an hour to warm up after you change the thermostat.

## Trash
- [edited] Trash and recycling go in the blue bins at the end of the driveway.

## Kitchen
- [edited] The kitchen has a microwave, a toaster, and a full-size oven.
- [auto] There is a blender in the cabinet by the fridge.
""",
    CEDAR_RETREAT: """# Cedar Hollow Retreat (staged)

Source: staged fixture in the Knowledge Hub export layout. Invented facts.

## Trash
- [edited] Trash goes in the gray bins on the side of the garage.

## Laundry
- [edited] The shared laundry room is in the basement, down the back stairs.
""",
}

CONFIRMED_AT = datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc)


def _fact(key, value, at=CONFIRMED_AT, status="known"):
    return {"value": value, "status": status, "source": "operator: staged messaging rule",
            "confirmed_at": at.isoformat(), "confirmed_sha256": sha256(value)}


def fact_sheet():
    """A fresh copy of the staged operator fact sheet (the actuator's
    fact-sheets.json shape). Only operator-confirmed facts within the
    7-day window can be a hub source; one unconfirmed and one expired
    fact show the other two cases."""
    return {
        "schema": "autopilot-live/fact-sheets/v1",
        "properties": {
            CEDAR_1A: {"name": NAMES[CEDAR_1A], "facts": {
                # never confirmed: not a source
                "tv_details": {"value": "The TV is a smart TV with Netflix.", "status": "known",
                               "source": "operator: staged messaging rule"},
            }},
            CEDAR_1B: {"name": NAMES[CEDAR_1B], "facts": {
                "smoking_policy": _fact("smoking_policy", "Smoking is not allowed anywhere at the unit, inside or out."),
            }},
            CEDAR_2A: {"name": NAMES[CEDAR_2A], "facts": {
                "coffee_details": _fact("coffee_details",
                                        "The studio has a pour-over coffee setup with paper filters in the top drawer."),
            }},
            CEDAR_2B: {"name": NAMES[CEDAR_2B], "facts": {
                "trash_location": _fact("trash_location",
                                        "Trash and recycling go in the blue bins at the end of the driveway, lids closed."),
                # confirmed more than 7 days before the fixture clock: expired, not a source
                "tv_details": _fact("tv_details", "The TV has Roku with Netflix and YouTube.",
                                    at=CONFIRMED_AT - timedelta(days=12)),
            }},
            CEDAR_RETREAT: {"name": NAMES[CEDAR_RETREAT], "facts": {}},
        },
    }


# The clock the fixture world runs at: an hour after the confirmations.
FIXTURE_NOW = CONFIRMED_AT + timedelta(hours=1)
ACCEPTED = {"status": "accepted"}

# A real guest question (Oct 31, 2026; the words verbatim, nothing else
# about the guest or the listing kept) and the kind of [edited] Hub item
# that answers it, on a pseudonym listing outside the Cedar Hollow scope
# (so the blind sets are unchanged). The kitchen bin is full: where is the
# outdoor one? The item's wording is the fixture's own.
FULL_BIN_QUESTION = "if the garbage bin in the kitchen is full is there an outside garbage bin we throw it away in?"
FERNWOOD = "fernwood-flat"
OUTDOOR_BIN_STATEMENT = "The outdoor garbage bins are in the other driveway, by the three-car garage."
OUTDOOR_BIN_MARKDOWN = f"""# Fernwood Garden Flat (staged)

Source: staged fixture in the Knowledge Hub export layout. Invented facts.

## Trash Disposal
- [edited] {OUTDOOR_BIN_STATEMENT}

## Laundry
- [edited] The washer and dryer are in the basement, at the bottom of the back stairs.
"""
# The same listing with the outdoor-bin item absent: the question must hold.
NO_OUTDOOR_BIN_MARKDOWN = OUTDOOR_BIN_MARKDOWN.replace(f"## Trash Disposal\n- [edited] {OUTDOOR_BIN_STATEMENT}\n\n", "")
