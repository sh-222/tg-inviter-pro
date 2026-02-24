import csv
from typing import List, Dict, Any


class CSVReaderService:
    def read_targets(self, content: str) -> List[Dict[str, Any]]:
        """
        Reads target users from CSV string content.
        Expected format: ID, Username, Full Name
        """
        targets = []
        reader = csv.DictReader(content.splitlines())

        for row in reader:
            targets.append(
                {
                    "tg_id": int(row.get("ID", 0)) if row.get("ID") else None,
                    "username": row.get("Username", "").strip()
                    if row.get("Username")
                    else None,
                    "full_name": row.get("Full Name", "").strip()
                    if row.get("Full Name")
                    else None,
                }
            )
        return targets
