import csv
from typing import List, Dict, Any


class CSVReaderService:
    def read_targets(self, content: str) -> List[Dict[str, Any]]:
        """
        Reads target users from CSV string content.
        Expected format: ID, Username, Full Name (case-insensitive)
        """
        targets = []
        reader = csv.DictReader(content.splitlines())
        if not reader.fieldnames:
            return targets

        # Create case-insensitive map of fieldnames
        field_map = {f.strip().lower(): f for f in reader.fieldnames if f}

        id_field = (
            field_map.get("id")
            or field_map.get("user_id")
            or field_map.get("user id")
            or field_map.get("tg_id")
        )
        user_field = field_map.get("username") or field_map.get("user")
        name_field = field_map.get("full name") or field_map.get("name")

        for row in reader:
            tg_id_val = row.get(id_field, "").strip() if id_field else ""
            user_val = row.get(user_field, "").strip() if user_field else ""
            name_val = row.get(name_field, "").strip() if name_field else ""

            if not tg_id_val and not user_val:
                continue

            targets.append(
                {
                    "tg_id": int(tg_id_val) if tg_id_val.isdigit() else None,
                    "username": user_val if user_val else None,
                    "full_name": name_val if name_val else None,
                }
            )
        return targets
