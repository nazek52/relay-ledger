from pathlib import Path
import secrets

path = Path(".env")
try:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(
            "RELAY_TOKEN="
            + secrets.token_urlsafe(32)
            + "\nRELAY_SIGNING_SECRET="
            + secrets.token_urlsafe(48)
            + "\n"
        )
    print("Created .env with random local secrets. Keep it private.")
except FileExistsError:
    print("Existing .env preserved.")
