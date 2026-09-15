"""Create local configuration without overwriting existing secrets."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
template = (root / "backend/.env.example").read_text()
template = template.replace("replace-with-a-random-secret-of-at-least-32-characters", secrets.token_urlsafe(48))
template = template.replace("replace-with-an-independent-random-secret", secrets.token_urlsafe(48))
for relative in ("../agent/jeugdzorg", "../agent", "../.agent-home", "../.venv-agent/bin"):
    template = template.replace("=" + relative + "\n", "=" + str((root / "backend" / relative).resolve()) + "\n")
target = root / "backend/.env"
with target.open("x") as stream:
    stream.write(template)
target.chmod(0o600)
print("Created backend/.env with new local secrets.")
