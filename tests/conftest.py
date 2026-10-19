import sys
from pathlib import Path

# Make the backend package importable when pytest is launched as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
