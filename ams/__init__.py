import importlib
import sys


try:
    sys.modules.setdefault("attendance", importlib.import_module("ams.attendance"))
except ModuleNotFoundError:
    pass