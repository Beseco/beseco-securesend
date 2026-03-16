"""
core/vcf.py — vCard 3.0 Import/Export + Parser

Keine Flask-Abhängigkeit, keine globalen Variablen.
"""

from __future__ import annotations


def _vcf_escape(value: str) -> str:
    """Escape special characters per RFC 6350."""
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def contacts_to_vcf(contacts: list[dict]) -> str:
    """Erzeugt einen vCard 3.0 String aus einer Liste von Kontakt-Dicts."""
    blocks = []
    for c in contacts:
        fn = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
        blocks.append("\r\n".join([
            "BEGIN:VCARD",
            "VERSION:3.0",
            f"FN:{_vcf_escape(fn)}",
            f"N:{_vcf_escape(c.get('last_name', ''))};{_vcf_escape(c.get('first_name', ''))};; ;",
            f"ORG:{_vcf_escape(c.get('company', ''))}",
            f"TEL;TYPE=CELL:{c.get('mobile', '')}",
            f"EMAIL:{c.get('email', '')}",
            "END:VCARD",
        ]))
    return "\r\n\r\n".join(blocks)


def parse_vcf(text: str) -> list[dict]:
    """Minimaler vCard 3.0/4.0 Parser. Gibt Liste von Kontakt-Dicts zurück."""
    import quopri

    def _unfold(lines):
        """RFC 6350 line unfolding: continuation lines start with space or tab."""
        result = []
        for line in lines:
            if line and line[0] in (" ", "\t") and result:
                result[-1] += line[1:]
            else:
                result.append(line)
        return result

    contacts = []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = []
    in_card = False
    current = []
    for line in text.splitlines():
        if line.strip().upper() == "BEGIN:VCARD":
            in_card = True
            current = []
        elif line.strip().upper() == "END:VCARD":
            if in_card:
                blocks.append(current)
            in_card = False
        elif in_card:
            current.append(line)

    for block in blocks:
        lines = _unfold(block)
        contact = {"company": "", "last_name": "", "first_name": "", "mobile": "", "email": ""}
        fn_fallback = ""
        mobile_found = False

        for line in lines:
            if "ENCODING=QUOTED-PRINTABLE" in line.upper():
                parts = line.split(":", 1)
                if len(parts) == 2:
                    try:
                        line = parts[0] + ":" + quopri.decodestring(parts[1].encode()).decode("utf-8", errors="replace")
                    except Exception:
                        pass

            if ":" not in line:
                continue
            prop_full, value = line.split(":", 1)
            value = value.strip()
            prop_upper = prop_full.upper()

            if prop_upper.startswith("FN"):
                fn_fallback = value
            elif prop_upper.startswith("N;") or prop_upper == "N":
                parts = value.split(";")
                contact["last_name"]  = parts[0].strip() if len(parts) > 0 else ""
                contact["first_name"] = parts[1].strip() if len(parts) > 1 else ""
            elif prop_upper.startswith("ORG"):
                contact["company"] = value.split(";")[0].strip()
            elif prop_upper.startswith("TEL"):
                is_mobile = any(t in prop_upper for t in ("CELL", "MOBILE"))
                if is_mobile:
                    contact["mobile"] = value
                    mobile_found = True
                elif not mobile_found:
                    contact["mobile"] = value
            elif prop_upper.startswith("EMAIL") and not contact["email"]:
                contact["email"] = value

        if not contact["last_name"] and fn_fallback:
            parts = fn_fallback.rsplit(" ", 1)
            contact["first_name"] = parts[0] if len(parts) > 1 else ""
            contact["last_name"]  = parts[-1]

        if any(contact.values()):
            contacts.append(contact)

    return contacts
