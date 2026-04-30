import os
import sys
from pathlib import Path

_root = str(Path(__file__).resolve().parents[1])

# Ставим downloader первым — защита от коллизии с shell/orchestrator путями
# при совместном запуске pytest из корня проекта.
if _root not in sys.path:
    sys.path.insert(0, _root)
else:
    sys.path.remove(_root)
    sys.path.insert(0, _root)

# Сбрасываем кеш модулей, которые могли подтянуться из другого сервиса
_own_modules = {"main", "config", "auth", "state", "logging_setup"}
for _m in list(sys.modules):
    if _m in _own_modules or _m.startswith(("jobs.", "strategies.", "tasks.", "orchestrator.")):
        del sys.modules[_m]

# Гарантируем dev-настройки до импорта config (lru_cache)
os.environ.setdefault("DOWNLOADER_TOKEN", "test-token")
os.environ.setdefault("STUB_MODE", "true")
