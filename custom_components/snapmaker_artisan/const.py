"""Constants for the Snapmaker Artisan integration."""

DOMAIN = "snapmaker_artisan"

CONF_MODEL = "model"
CONF_MACHINE_NAME = "machine_name"

SACP_PORT = 8888
DISCOVERY_PORT = 20054
DISCOVERY_TIMEOUT = 3

SOCKET_TIMEOUT = 5
DEFAULT_SUBSCRIPTION_INTERVAL_MS = 1000
UPDATE_INTERVAL_MIN_S = 1
UPDATE_INTERVAL_MAX_S = 60
RECONNECT_DELAY = 5

# Position is a direct one-shot query (CONFIRMED WORKING), not a
# subscription -- we have to keep re-sending it ourselves on a timer.
POSITION_POLL_INTERVAL_S = 1.0

STATUS_NAMES = {
    0: "idle",
    1: "starting",
    2: "printing",
    3: "pausing",
    4: "paused",
    5: "stopping",
    6: "stopped",
    7: "finishing",
    8: "completed",
    9: "recovering",
    10: "resuming",
}

PLATFORMS = ["sensor", "button", "number"]
