"""
Group scored tables by whichever Archiving Object or Housekeeping Program was
recommended for them (see scoring.py), so multiple tables sharing the same
object/program can be seen -- and prioritized -- together instead of only as
a flat per-table list.

Takes scoring.score_archiving_objects()'s "recommended" list (one row per
table) and re-emits it grouped: every member table of a group appears as its
own row, with the group's cumulative size and table count repeated on each
one (the same repeated-per-row convention main.py's "All Scored Objects"
sheet already uses for per-table Volume/Description across candidate rows --
keeps the sheet directly sortable/filterable in Excel with no merged cells).

Archiving Object and Housekeeping Program are always kept in their own
columns, exactly as scoring.py already produces them -- never combined into
a single "key" column -- so a row is unambiguous about which kind of
recommendation (if any) it belongs to just by which column is non-empty.
"""

from collections import OrderedDict


def build_object_groups(recommended_rows: list[dict]) -> list[dict]:
    """
    Group *recommended_rows* by Archiving Object (if set), else Housekeeping
    Program (if set), else a "nothing found" bucket. Returns a flat list of
    rows -- one per table -- each carrying its group's cumulative
    "Cumulative Size (GB)"/"Cumulative Size (MB)"/"Table Count", sorted by
    that cumulative GB size descending. The "nothing found" bucket (both
    Archiving Object and Housekeeping Program blank) is always last,
    regardless of its own total.
    """
    groups: "OrderedDict[tuple, dict]" = OrderedDict()

    for row in recommended_rows:
        archiving_object = row.get("Archiving Object", "")
        housekeeping_program = row.get("Housekeeping Program", "")

        if archiving_object:
            key = ("AO", archiving_object)
        elif housekeeping_program:
            key = ("HK", housekeeping_program)
        else:
            key = ("NONE", None)

        group = groups.setdefault(key, {"members": []})
        group["members"].append(row)

    def _group_size_key(group: dict) -> tuple[float, float]:
        gb = sum(_to_float(m.get("Volume (GB)", "")) for m in group["members"])
        mb = sum(_to_float(m.get("Volume (MB)", "")) for m in group["members"])
        return (gb, mb)

    output: list[dict] = []
    sortable_groups = [g for k, g in groups.items() if k[0] != "NONE"]
    # Primary sort: cumulative GB descending. Tie-break: cumulative MB
    # descending -- GB is rounded to 2 decimals, so two groups can tie on GB
    # while still differing once you look at the finer MB figure.
    sortable_groups.sort(key=_group_size_key, reverse=True)
    none_group = groups.get(("NONE", None))

    ordered_groups = sortable_groups + ([none_group] if none_group else [])

    for group in ordered_groups:
        members = sorted(
            group["members"],
            key=lambda m: (_to_float(m.get("Volume (GB)", "")), _to_float(m.get("Volume (MB)", ""))),
            reverse=True,
        )
        cumulative_gb = sum(_to_float(m.get("Volume (GB)", "")) for m in members)
        cumulative_mb = sum(_to_float(m.get("Volume (MB)", "")) for m in members)
        table_count = len(members)

        for member in members:
            output.append({
                "Archiving Object": member.get("Archiving Object", ""),
                "Object Description": member.get("Object Description", ""),
                "Housekeeping Program": member.get("Housekeeping Program", ""),
                "Table Name": member.get("Table Name", ""),
                "Table Description": member.get("Table Description", ""),
                "Volume (GB)": member.get("Volume (GB)", ""),
                "Volume (MB)": member.get("Volume (MB)", ""),
                "Cumulative Size (GB)": f"{cumulative_gb:.2f}",
                "Cumulative Size (MB)": f"{cumulative_mb:.2f}",
                "Table Count": str(table_count),
                "Rationale": member.get("Rationale", ""),
            })

    return output


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
