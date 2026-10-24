#!/usr/bin/env python3
"""CLI для БД профиля priest. Вся запись в базу — только через него.

Примеры:
  life.py metric sleep_hours 6.5 --date 2026-10-22 --note "лёг в 2"
  life.py weight 78.4
  life.py workout --kind gym --min 60 --load hard
  life.py task "сдать отчёт" --project income --due 2026-10-30 --prio 1
  life.py tasks
  life.py done 3
  life.py event 2026-11-25 "аренда VPN" --remind 1d
  life.py tx 1500 --dir out --category food
  life.py journal --mood 6 --energy 5 --text "норм день"
  life.py report --days 14
  life.py fact city "Нальчик"
  life.py get
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import store  # noqa: E402


def _print(obj) -> None:
    if isinstance(obj, (dict, list)):
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        print(obj)


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, *rest = argv

    try:
        if cmd == "metric":
            kind, value = rest[0], rest[1]
            opts = _opts(rest[2:], flags={"--unit": "unit", "--date": "ts",
                                          "--note": "note", "--source": "source"})
            mid = store.add_metric(kind, value, ts=opts.get("ts"), unit=opts.get("unit"),
                                   note=opts.get("note"), source=opts.get("source", "cli"))
            _print({"ok": True, "id": mid})

        elif cmd in store.METRIC_KINDS:  # шорткат: life.py weight 78.4
            value = rest[0]
            o = _opts(rest[1:], flags={"--date": "ts", "--note": "note", "--source": "source"})
            mid = store.add_metric(cmd, value, **o)
            _print({"ok": True, "id": mid})

        elif cmd == "workout":
            o = _opts(rest, flags={"--date": "date_", "--kind": "kind", "--min": "duration_min",
                                   "--load": "load", "--note": "note"})
            _print({"ok": True, "id": store.add_workout(**o)})

        elif cmd == "task":
            title = rest[0]
            o = _opts(rest[1:], flags={"--project": "project", "--due": "due",
                                       "--prio": "priority", "--note": "note"})
            _print({"ok": True, "id": store.add_task(title, **o)})

        elif cmd == "tasks":
            with store.session() as c:
                _print(store.rows(c, "SELECT id,title,project,due,priority,status FROM tasks "
                                     "WHERE status='open' ORDER BY priority, COALESCE(due,'9999')"))

        elif cmd == "done":
            store.close_task(int(rest[0]), "done")
            _print({"ok": True, "closed": int(rest[0])})

        elif cmd == "drop":
            store.close_task(int(rest[0]), "dropped")
            _print({"ok": True, "dropped": int(rest[0])})

        elif cmd == "event":
            date_, title = rest[0], rest[1]
            o = _opts(rest[2:], flags={"--type": "type_", "--remind": "remind", "--note": "note"})
            _print({"ok": True, "id": store.add_event(date_, title, **o)})

        elif cmd == "tx":
            amount = rest[0]
            o = _opts(rest[1:], flags={"--dir": "direction", "--date": "ts", "--cur": "currency",
                                       "--cat": "category", "--acc": "account", "--note": "note"})
            _print({"ok": True, "id": store.add_tx(amount, **o)})

        elif cmd == "journal":
            o = _opts(rest, flags={"--date": "date_", "--mood": "mood",
                                   "--energy": "energy", "--text": "text"})
            for k in ("mood", "energy"):
                if k in o:
                    o[k] = int(o[k])
            _print({"ok": True, "id": store.add_journal(**o)})

        elif cmd == "report":
            o = _opts(rest, flags={"--days": "days"})
            _print(store.report(int(o.get("days", 7))))

        elif cmd == "item":
            name = rest[0]
            o = _opts(rest[1:], flags={"--cat": "category", "--brand": "brand", "--vol": "volume",
                                       "--cadence": "cadence", "--role": "role", "--note": "note",
                                       "--status": "status"})
            _print({"ok": True, "id": store.add_item(name, **o)})

        elif cmd == "items":
            o = _opts(rest, flags={"--cat": "category", "--all": "include_dropped"})
            _print(store.items_list(o.get("category"), bool(o.get("include_dropped"))))

        elif cmd in ("used", "skip", "restock"):
            action = {"used": "used", "skip": "skipped", "restock": "restocked"}[cmd]
            o = _opts(rest[1:], flags={"--date": "date_", "--note": "note"})
            _print({"ok": True, "id": store.log_item(rest[0], action=action, **o)})

        elif cmd == "usage":
            o = _opts(rest, flags={"--days": "days"})
            _print(store.item_stats(int(o.get("days", 30))))

        elif cmd == "habits":
            _print(store.habits_list(include_inactive="--all" in rest))

        elif cmd == "habit":
            sub = rest[0]
            if sub == "add":
                o = _opts(rest[1:], flags={"--key": "key", "--title": "title",
                                           "--rule": "rule", "--sort": "sort", "--from": "from"})
                store.add_habit(o["key"], o["title"], json.loads(o["rule"]),
                                active_from=o.get("from"), sort=int(o.get("sort", 100)))
                _print({"ok": True})
            elif sub in ("off", "rm"):
                store.deactivate_habit(rest[1])
                _print({"ok": True})
            else:
                raise ValueError("habit add | habit off <key>")

        elif cmd in ("measurements", "body"):
            _print(store.measurements(int(rest[0]) if rest else 365))

        elif cmd == "asym":
            _print(store.thigh_asymmetry())

        elif cmd == "score":
            _print(store.day_score(rest[0] if rest else None))

        elif cmd == "hitmap":
            _print(store.hitmap(int(rest[0]) if rest else 84))

        elif cmd == "fact":
            store.set_fact(rest[0], " ".join(rest[1:]))
            _print({"ok": True})

        elif cmd == "get":
            with store.session() as c:
                _print({"facts": store.rows(c, "SELECT key,value,updated FROM facts ORDER BY key"),
                        "metrics": store.rows(c, "SELECT ts,kind,value,unit FROM metrics ORDER BY id DESC LIMIT 10"),
                        "workouts": store.rows(c, "SELECT date,kind,duration_min,load FROM workouts ORDER BY id DESC LIMIT 10"),
                        "tasks_open": store.rows(c, "SELECT id,title,due,priority FROM tasks WHERE status='open'"),
                        "events": store.rows(c, "SELECT date,title,status FROM events WHERE status='active' ORDER BY date"),
                        "tx": store.rows(c, "SELECT ts,amount,currency,direction,category FROM tx ORDER BY id DESC LIMIT 10")})

        elif cmd == "sql":  # только чтение
            q = " ".join(rest)
            if not q.lstrip().lower().startswith(("select", "with")):
                raise ValueError("sql: только SELECT/WITH")
            with store.session() as c:
                _print(store.rows(c, q))

        else:
            print(f"неизвестная команда: {cmd}\n", file=sys.stderr)
            print(__doc__, file=sys.stderr)
            return 2

    except (ValueError, IndexError) as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 1
    return 0


def _opts(args: list[str], flags: dict[str, str]) -> dict:
    """--key value / --flag (для --flag без значения кладёт True-подобное)."""
    out: dict = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a in flags:
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                out[flags[a]] = args[i + 1]
                i += 2
            else:
                out[flags[a]] = True
                i += 1
        else:
            raise ValueError(f"неизвестный параметр {a}")
    return out


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
