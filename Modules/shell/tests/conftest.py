import sys
from pathlib import Path

# Делаем shell-модуль импортируемым как top-level (как в Dockerfile)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
