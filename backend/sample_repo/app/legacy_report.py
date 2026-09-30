def build_legacy_report(rows):
    """Old CSV-style reporting code retained for historical compatibility."""
    return "\n".join(
        f"{row.get('id')},{row.get('name')}"
        for row in rows
    )
