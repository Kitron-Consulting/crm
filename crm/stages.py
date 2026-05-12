"""Stage and source vocabulary, plus accessors over the config blob.

Pure logic — takes/returns plain data. No print, no sys.exit.
"""

DEFAULT_STAGES = ["cold", "contacted", "responded", "meeting", "proposal", "won", "lost", "dormant"]
DEFAULT_SOURCES = ["cold", "referral", "inbound"]


def get_stages(data):
    return data["config"]["stages"]


def get_sources(data):
    return data["config"]["sources"]
