"""Report formatting for the admin dashboard."""


def format_user_report(rows):
    lines = []
    lines.append("=" * 40)
    lines.append("USER REPORT")
    lines.append("=" * 40)
    for row in rows:
        lines.append(f"{row['name']:<20} {row['value']:>10}")
    lines.append("-" * 40)
    lines.append(f"total: {len(rows)}")
    return "\n".join(lines)


def format_order_report(rows):
    lines = []
    lines.append("=" * 40)
    lines.append("ORDER REPORT")
    lines.append("=" * 40)
    for row in rows:
        lines.append(f"{row['name']:<20} {row['value']:>10}")
    lines.append("-" * 40)
    lines.append(f"total: {len(rows)}")
    return "\n".join(lines)


def format_audit_report(rows):
    lines = []
    lines.append("=" * 40)
    lines.append("AUDIT REPORT")
    lines.append("=" * 40)
    for row in rows:
        lines.append(f"{row['name']:<20} {row['value']:>10}")
    lines.append("-" * 40)
    lines.append(f"total: {len(rows)}")
    return "\n".join(lines)
