CREATE TABLE status_snapshot (
    service_id TEXT PRIMARY KEY,
    health TEXT NOT NULL,
    detail TEXT NOT NULL,
    containers_json TEXT NOT NULL,
    route_ok INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    projection_json TEXT NOT NULL
);
CREATE TABLE person_checklist (
    person_uid TEXT NOT NULL,
    item TEXT NOT NULL,
    done_at TEXT NOT NULL,
    PRIMARY KEY (person_uid, item)
);
