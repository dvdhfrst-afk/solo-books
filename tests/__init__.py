"""The tests never read the settings of the person running them: no settings file, and no SOLO_BOOKS_ variables."""
import os
import tempfile

for _name in [name for name in os.environ if name.startswith("SOLO_BOOKS_") and name != "SOLO_BOOKS_DEBUG"]:
    del os.environ[_name]
os.environ["SOLO_BOOKS_CONFIG"] = os.path.join(tempfile.gettempdir(), "solo-books-tests-have-no-settings", "config.json")
