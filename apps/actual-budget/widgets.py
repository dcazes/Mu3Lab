"""Home widgets for Actual Budget (manifest ``widgets``; ctl/app_widgets.py runs them).

Budget: the viewer's own budget this month: spent against budgeted, and the categories they spend
most on, read with actualpy. Actual sends the whole budget file, so it is read every half hour at most.

Actual here signs people in only with Mu3Lab's sign-in (OpenID), which a server can't complete, and
it can't keep a password beside it (setting one switches everyone to passwords). So Mu3Lab gives each
person one long-lived Actual sign-in of their own: the same ``sessions`` row Actual writes when they
sign in, added inside Actual's container, for their own Actual user. It reads only budgets they own
or were given, and is made again if Actual forgets it.
"""

from __future__ import annotations

import json
import tempfile
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from actual import Actual
from actual.exceptions import ActualError, AuthorizationError
from actual.queries import get_budgets, get_transactions

from ctl import actions, person_connectors
from ctl.app_widgets import NeedsSetup, WidgetContext, WidgetError
from ctl.store.secrets import SecretStore

SERVICE = "actual-server"
# Each person's Actual sign-in, by their Mu3Lab uid (encrypted with Mu3Lab's other secrets).
SESSIONS = "actual-widget-session"
# Run with node inside Actual's container (argv: the Actual user name). Adds a session that never
# expires for that enabled user, signed in the way they sign in (OpenID), and prints its token; the
# token is made there, so it never appears on a command line.
MINT = """
const crypto = require('node:crypto');
const Database = require('better-sqlite3');
const db = new Database('/data/server-files/account.sqlite');
const user = db.prepare('SELECT id FROM users WHERE user_name = ? AND enabled = 1').get(process.argv[1]);
if (!user) { console.log(JSON.stringify({ missing: true })); process.exit(0); }
const token = crypto.randomUUID();
db.prepare("INSERT INTO sessions (token, expires_at, user_id, auth_method) VALUES (?, -1, ?, 'openid')")
  .run(token, user.id);
console.log(JSON.stringify({ token }));
"""


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _username(ctx: WidgetContext) -> str:
    person = next((found for found in person_connectors.household() if found["uid"] == ctx.uid), None)
    if not person:
        raise NeedsSetup("Only people in the household have a budget here.")
    return person["username"]


def _mint(ctx: WidgetContext) -> str:
    code, output = actions.compose_exec(
        ctx.paths.projects / ctx.app.id, SERVICE, ["node", "-e", MINT, _username(ctx)], lambda _line: None, timeout=30
    )
    lines = [line for line in (output or "").splitlines() if line.startswith("{")]
    reply = json.loads(lines[-1]) if not code and lines else {}
    if reply.get("missing"):
        raise NeedsSetup("Open Actual Budget once so it knows you; your budget appears here within half an hour.")
    if not reply.get("token"):
        raise WidgetError("Mu3Lab could not sign in to Actual Budget for you.")
    SecretStore(ctx.paths).put(SESSIONS, ctx.uid, str(reply["token"]))
    return str(reply["token"])


def _mine(actual: Actual) -> Any:
    """The budget to show: the viewer's own, else one shared with them (the first Actual lists)."""
    me = actual.validate().data.user_id
    files = [found for found in actual.list_user_files().data if not found.deleted]
    own = [found for found in files if found.owner in (None, me)]
    if not (own or files):
        raise NeedsSetup("You have no budget in Actual yet. Make one there to see it here.")
    return (own or files)[0]


def _month(actual: Actual, start: date, end: date) -> tuple[dict[str, Decimal], Decimal]:
    spent: dict[str, Decimal] = defaultdict(Decimal)
    for transaction in get_transactions(actual.session, start_date=start, end_date=end, transfer=False):
        amount = transaction.get_amount()
        if amount < 0 and transaction.category and not transaction.category.is_income:
            spent[transaction.category.name or "Other"] -= amount
    budgeted = sum((item.get_amount() for item in get_budgets(actual.session, month=start)), Decimal(0))
    return spent, budgeted


def _read(ctx: WidgetContext, token: str, start: date, end: date) -> tuple[dict[str, Decimal], Decimal]:
    with (
        tempfile.TemporaryDirectory(prefix="mu3lab-actual-") as folder,
        Actual(base_url=ctx.base, token=token, data_dir=folder, timeout=60) as actual,
    ):
        actual.set_file(_mine(actual))
        actual.download_budget()
        return _month(actual, start, end)


def budget(ctx: WidgetContext) -> dict[str, Any]:
    start = date.today().replace(day=1)
    end = (start.replace(day=28) + (date.resolution * 4)).replace(day=1)
    token = SecretStore(ctx.paths).get(SESSIONS, ctx.uid) or _mint(ctx)
    try:
        try:
            spent, budgeted = _read(ctx, str(token), start, end)
        except AuthorizationError:
            # Actual forgot the sign-in (its sessions were cleared): make a new one once.
            spent, budgeted = _read(ctx, _mint(ctx), start, end)
    except ActualError:
        raise WidgetError("Actual Budget did not give your budget.") from None
    total = sum(spent.values(), Decimal(0))
    top = sorted(spent.items(), key=lambda pair: pair[1], reverse=True)[:8]
    over = budgeted and total > budgeted
    return {
        "stats": [
            {"label": "Spent this month", "value": _money(total), "tone": "bad" if over else ""},
            {"label": "Budgeted", "value": _money(budgeted) if budgeted else "Not set"},
            *(
                [{"label": "Left", "value": _money(budgeted - total), "tone": "bad" if over else "good"}]
                if budgeted
                else []
            ),
        ],
        "items_title": "Most spent on",
        "items": [
            {
                "title": name,
                "meta": _money(amount),
                "progress": float(amount / total) if total else None,
            }
            for name, amount in top
        ],
        "empty": "Nothing spent yet this month.",
    }
