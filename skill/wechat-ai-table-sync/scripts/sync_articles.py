#!/usr/bin/env python3
"""Synchronize SQLite articles into a DingTalk Notable table."""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "results" / "articles.sqlite3"
BASE_ID = "P0MALyR8kNpXlRO7FYXjkO4bJ3bzYmDO"
SHEET_ID = "0md26ggk3sgnjzj22zp3e"
OPERATOR_UNION_ID = "nH3HfDiPL40MDE9MAPN5BZQiEiE"
TABLE_FIELDS = ("id", "content_text", "publish_time", "account_name", "application_type", "summary", "url", "domains", "title")


def to_table_fields(row: dict[str, Any]) -> dict[str, str]:
    domains = row.get("domains")
    if domains is None:
        domains = []
    if isinstance(domains, str):
        domains_text = domains
    else:
        domains_text = ",".join(str(x) for x in domains)
    return {
        "id": str(row.get("id", "")),
        "url": str(row.get("url", "")),
        "title": str(row.get("title", "")),
        "account_name": str(row.get("account_name", "")),
        "publish_time": str(row.get("publish_time", "")),
        "application_type": str(row.get("application_type", "")),
        "domains": domains_text,
        "summary": str(row.get("summary", "")),
        "content_text": str(row.get("content_text", "")),
    }


def load_rows(db_path: Path) -> list[dict[str, Any]]:
    import subprocess
    completed = subprocess.run(
        [str(REPO / "wx-crawl-db"), "--db", str(db_path), "list", "--limit", "100000", "--json"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    rows = json.loads(completed.stdout)
    if not isinstance(rows, list):
        raise RuntimeError("wx-crawl-db list returned invalid JSON")
    return rows


class NotableAPI:
    def __init__(self, base_id: str, sheet_id: str, operator_id: str):
        from dingtalk_stream import Credential, DingTalkStreamClient
        from alibabacloud_dingtalk.notable_1_0 import client, models
        from alibabacloud_tea_openapi import models as openapi_models
        from alibabacloud_tea_util import models as util_models
        self.models = models
        self.client = client.Client(openapi_models.Config(protocol="https", region_id="central"))
        env = {}
        for line in Path(os.environ.get("HERMES_HOME", "/root/.hermes") + "/.env").read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")
        token = DingTalkStreamClient(Credential(env["DINGTALK_CLIENT_ID"], env["DINGTALK_CLIENT_SECRET"])).get_access_token()
        self.base_id, self.sheet_id, self.operator_id, self.token = base_id, sheet_id, operator_id, token
        self.headers = models.ListRecordsHeaders(x_acs_dingtalk_access_token=token)
        self.runtime = util_models.RuntimeOptions()

    def list_records(self) -> list[dict[str, Any]]:
        result = []
        token = ""
        while True:
            request = self.models.ListRecordsRequest(operator_id=self.operator_id, max_results=100, next_token=token or None)
            response = self.client.list_records_with_options(self.base_id, self.sheet_id, request, self.headers, self.runtime)
            body = response.body.to_map()
            result.extend(body.get("records") or [])
            if not body.get("hasMore"):
                return result
            token = body.get("nextToken") or ""

    def insert_records(self, fields_list: list[dict[str, str]]) -> None:
        if not fields_list:
            return
        headers = self.models.InsertRecordsHeaders(x_acs_dingtalk_access_token=self.token)
        records = [self.models.InsertRecordsRequestRecords(fields=f) for f in fields_list]
        self.client.insert_records_with_options(self.base_id, self.sheet_id, self.models.InsertRecordsRequest(records=records, operator_id=self.operator_id, client_token=str(uuid.uuid4())), headers, self.runtime)

    def update_records(self, records_list: list[dict[str, Any]]) -> None:
        if not records_list:
            return
        headers = self.models.UpdateRecordsHeaders(x_acs_dingtalk_access_token=self.token)
        records = [self.models.UpdateRecordsRequestRecords(id=x["id"], fields=x["fields"]) for x in records_list]
        self.client.update_records_with_options(self.base_id, self.sheet_id, self.models.UpdateRecordsRequest(records=records, operator_id=self.operator_id), headers, self.runtime)


def sync_rows(api: Any, rows: list[dict[str, Any]], mode: str = "incremental", batch_size: int = 5) -> dict[str, int]:
    remote = api.list_records()
    by_id = {str(r.get("fields", {}).get("id")): r for r in remote if r.get("fields", {}).get("id") is not None}
    inserts, updates = [], []
    unchanged = 0
    for row in rows:
        fields = to_table_fields(row)
        old = by_id.get(fields["id"])
        if old is None:
            inserts.append(fields)
        elif mode == "full" or {k: old.get("fields", {}).get(k, "") for k in fields} != fields:
            updates.append({"id": old["id"], "fields": fields})
        else:
            unchanged += 1
    for i in range(0, len(inserts), batch_size):
        api.insert_records(inserts[i:i + batch_size])
    for i in range(0, len(updates), batch_size):
        api.update_records(updates[i:i + batch_size])
    return {"inserted": len(inserts), "updated": len(updates), "unchanged": unchanged}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("full", "incremental"), default="incremental")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--base-id", default=BASE_ID)
    parser.add_argument("--sheet-id", default=SHEET_ID)
    parser.add_argument("--operator-id", default=OPERATOR_UNION_ID)
    args = parser.parse_args()
    result = sync_rows(NotableAPI(args.base_id, args.sheet_id, args.operator_id), load_rows(args.db), args.mode)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
