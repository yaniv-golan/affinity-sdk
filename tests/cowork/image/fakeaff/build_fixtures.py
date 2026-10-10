"""Generate fixtures.json for the fake Affinity API: one small synthetic org.

Everything here is invented (example.com domains, fake people); no real CRM data. Run after
editing: python3 build_fixtures.py  (writes fixtures.json next to this file).
"""

from __future__ import annotations

import json
from pathlib import Path

PAGE_END = {"nextUrl": None, "prevUrl": None}
NOW = "2026-10-01T09:00:00Z"

ME = {"id": 4001, "firstName": "Robin", "lastName": "Tester", "emailAddress": "robin@vc.example"}
DANA = {"id": 4002, "firstName": "Dana", "lastName": "Example", "emailAddress": "dana@vc.example"}

ACME = {
    "id": 9000001,
    "name": "Acme Robotics",
    "domain": "acme-robotics.example",
    "domains": ["acme-robotics.example"],
    "isGlobal": False,
}
BOLT = {
    "id": 9000002,
    "name": "Bolt Analytics",
    "domain": "bolt-analytics.example",
    "domains": ["bolt-analytics.example"],
    "isGlobal": False,
}

DEALFLOW = {"id": 7001, "name": "Dealflow", "type": "company"}
WATCH = {"id": 7002, "name": "Portfolio Watch", "type": "company"}
STATUS_OPTIONS = [
    {"id": 51, "text": "Sourced", "rank": 0, "color": "gray"},
    {"id": 52, "text": "Due Diligence", "rank": 1, "color": "blue"},
    {"id": 53, "text": "Closed", "rank": 2, "color": "green"},
]
STATUS_FIELD_ID = "field-501"
OWNER_FIELD_ID = "field-502"


def v2_list(lst: dict) -> dict:
    return {
        **lst,
        "createdAt": "2025-01-01T00:00:00Z",
        "creatorId": ME["id"],
        "ownerId": ME["id"],
        "isPublic": True,
    }


def status_value(option_id: int | None) -> dict:
    opt = next((o for o in STATUS_OPTIONS if o["id"] == option_id), None)
    data = {"dropdownOptionId": opt["id"], "text": opt["text"]} if opt else None
    return {
        "id": STATUS_FIELD_ID,
        "name": "Status",
        "type": "list",
        "enrichmentSource": None,
        "value": {"type": "dropdown", "data": data},
    }


def owner_value(person: dict | None) -> dict:
    data = (
        {
            "id": person["id"],
            "firstName": person["firstName"],
            "lastName": person["lastName"],
            "primaryEmailAddress": person["emailAddress"],
            "type": "internal",
        }
        if person
        else None
    )
    return {
        "id": OWNER_FIELD_ID,
        "name": "Owner",
        "type": "list",
        "enrichmentSource": None,
        "value": {"type": "person", "data": data},
    }


# Acme: on Dealflow (Status = Due Diligence, Owner = Dana) and on Portfolio Watch (no fields)
ACME_ENTRIES = [
    {
        "id": 8001,
        "listId": DEALFLOW["id"],
        "listName": DEALFLOW["name"],
        "createdAt": "2026-08-01T12:00:00Z",
        "creatorId": ME["id"],
        "fields": [status_value(52), owner_value(DANA)],
    },
    {
        "id": 8002,
        "listId": WATCH["id"],
        "listName": WATCH["name"],
        "createdAt": "2026-08-15T12:00:00Z",
        "creatorId": ME["id"],
        "fields": [],
    },
]


def v1_list(lst: dict, size: int) -> dict:
    return {
        "id": lst["id"],
        "type": 1,  # organization list
        "name": lst["name"],
        "public": True,
        "owner_id": ME["id"],
        "creator_id": ME["id"],
        "list_size": size,
        "fields": [
            {
                "id": 501,
                "name": "Status",
                "list_id": lst["id"],
                "value_type": 7,  # ranked dropdown / status
                "allows_multiple": False,
                "track_changes": True,
                "dropdown_options": [
                    {"id": o["id"], "text": o["text"], "rank": o["rank"], "color": 0}
                    for o in STATUS_OPTIONS
                ],
            },
            {
                "id": 502,
                "name": "Owner",
                "list_id": lst["id"],
                "value_type": 0,  # person
                "allows_multiple": False,
                "track_changes": False,
                "dropdown_options": [],
            },
        ]
        if lst is DEALFLOW
        else [],
    }


def v1_org(org: dict) -> dict:
    return {
        "id": org["id"],
        "name": org["name"],
        "domain": org["domain"],
        "domains": org["domains"],
        "global": False,
        "crunchbase_uuid": None,
        "person_ids": [],
    }


def v1_person(p: dict) -> dict:
    return {
        "id": p["id"],
        "type": 1,  # internal
        "first_name": p["firstName"],
        "last_name": p["lastName"],
        "primary_email": p["emailAddress"],
        "emails": [p["emailAddress"]],
    }


STATUS_CHANGES = [  # V1 field-value-changes, newest first
    {
        "id": 600002,
        "field_id": 501,
        "entity_id": ACME["id"],
        "list_entry_id": 8001,
        "action_type": 2,  # update
        "value": {"id": 52, "text": "Due Diligence", "rank": 1, "color": 0},
        "changed_at": "2026-09-30T14:05:00Z",
        "changer": v1_person(DANA),
    },
    {
        "id": 600001,
        "field_id": 501,
        "entity_id": ACME["id"],
        "list_entry_id": 8001,
        "action_type": 0,  # create
        "value": {"id": 51, "text": "Sourced", "rank": 0, "color": 0},
        "changed_at": "2026-08-01T12:00:00Z",
        "changer": v1_person(ME),
    },
]


def build() -> dict[str, object]:
    f: dict[str, object] = {}
    f["GET /v2/auth/whoami"] = {
        "tenant": {"id": 1, "name": "Example Ventures", "subdomain": "example-ventures"},
        "user": ME,
        "grant": {"type": "api-key", "scopes": ["api"], "createdAt": "2025-01-01T00:00:00Z"},
    }
    # companies
    f["GET /organizations"] = {
        "organizations": [v1_org(ACME), v1_org(BOLT)],
        "next_page_token": None,
    }
    f["GET /organizations?term=Acme"] = {"organizations": [v1_org(ACME)], "next_page_token": None}
    f["GET /organizations?term=Acme+Robotics"] = f["GET /organizations?term=Acme"]
    f["GET /organizations?term=Acme%20Robotics"] = f["GET /organizations?term=Acme"]
    for org in (ACME, BOLT):
        f[f"GET /organizations/{org['id']}"] = v1_org(org)
        f[f"GET /v2/companies/{org['id']}"] = {**org, "fields": []}
    f[f"GET /v2/companies/{ACME['id']}/list-entries"] = {
        "data": ACME_ENTRIES,
        "pagination": PAGE_END,
    }
    f[f"GET /v2/companies/{BOLT['id']}/list-entries"] = {"data": [], "pagination": PAGE_END}
    # lists
    f["GET /v2/lists"] = {"data": [v2_list(DEALFLOW), v2_list(WATCH)], "pagination": PAGE_END}
    f["GET /lists"] = [v1_list(DEALFLOW, 2), v1_list(WATCH, 1)]
    for lst, size in ((DEALFLOW, 2), (WATCH, 1)):
        f[f"GET /lists/{lst['id']}"] = v1_list(lst, size)
        f[f"GET /v2/lists/{lst['id']}"] = v2_list(lst)
    # list entries with their entity (list export); fields of the list live on the entity
    dealflow_rows = [
        (8001, ACME, [status_value(52), owner_value(DANA)], "2026-08-01T12:00:00Z"),
        (8003, BOLT, [status_value(51), owner_value(ME)], "2026-09-10T12:00:00Z"),
    ]
    entries = {
        DEALFLOW["id"]: [
            {
                "id": eid,
                "type": "company",
                "listId": DEALFLOW["id"],
                "createdAt": created,
                "creatorId": ME["id"],
                "entity": {**org, "fields": fields},
            }
            for eid, org, fields, created in dealflow_rows
        ],
        WATCH["id"]: [
            {
                "id": 8002,
                "type": "company",
                "listId": WATCH["id"],
                "createdAt": "2026-08-15T12:00:00Z",
                "creatorId": ME["id"],
                "entity": {**ACME, "fields": []},
            }
        ],
    }
    for list_id, rows in entries.items():
        f[f"GET /v2/lists/{list_id}/list-entries"] = {"data": rows, "pagination": PAGE_END}
        for row in rows:
            f[f"GET /v2/lists/{list_id}/list-entries/{row['id']}"] = row
    f["GET /fields?list_id=7001"] = v1_list(DEALFLOW, 2)["fields"]
    f["GET /fields?list_id=7002"] = []
    # field history (V1): Status on Acme's Dealflow entry
    for q in (
        f"field_id=501&order_by=desc&organization_id={ACME['id']}",
        "field_id=501&order_by=desc&list_entry_id=8001",
    ):
        f[f"GET /field-value-changes?{q}"] = STATUS_CHANGES
    f["GET /field-value-changes"] = STATUS_CHANGES
    return f


if __name__ == "__main__":
    out = Path(__file__).with_name("fixtures.json")
    out.write_text(json.dumps(build(), indent=1, sort_keys=True) + "\n")
    print(f"wrote {out} ({len(build())} routes)")
