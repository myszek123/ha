DOMAIN = "myszolot"

# Weekly drive summary (pushed from charge-log → service myszolot.set_weekly_drive)
STORAGE_WEEKLY_DRIVE = "myszolot.weekly_drive"
SIGNAL_WEEKLY_DRIVE_UPDATED = "myszolot_weekly_drive_updated"


# External HA entities (read)
SENSOR_PRICE = "sensor.pstryk_current_buy_price"
SENSOR_SOC = "sensor.myszolot_battery_level"
BINARY_SENSOR_CABLE = "binary_sensor.myszolot_charge_cable"
BINARY_SENSOR_GARAGE_CAR = "binary_sensor.garage_car_present"
DEVICE_TRACKER = "device_tracker.myszolot_location"
SENSOR_CHARGING = "sensor.myszolot_charging"
NUMBER_CHARGE_CURRENT = "number.myszolot_charge_current"
INPUT_BOOLEAN_LOCATION_OVERRIDE = "input_boolean.myszolot_location_override"
# Override mode helpers (target SoC + deadline window) — dashboard panel
INPUT_NUMBER_CUSTOM_TARGET_SOC = "input_number.myszolot_custom_target_soc"
INPUT_NUMBER_DEADLINE_HOURS = "input_number.myszolot_deadline_hours"
# Tunables as HA helpers (not on dashboard): change via Helpers / Dev Tools
INPUT_NUMBER_MIN_SOC = "input_number.myszolot_min_soc"
INPUT_NUMBER_MAX_PRICE_THRESHOLD = "input_number.myszolot_max_price_threshold"

# Config keys
CONF_CHARGER_PHASES = "charger_phases"
CONF_VOLTAGE = "voltage"
CONF_FAST_AMPS = "fast_amps"
CONF_BATTERY_CAPACITY_KWH = "battery_capacity_kWh"
CONF_DEFAULT_TARGET_SOC = "default_target_soc"
CONF_MIN_SOC = "min_soc"
CONF_CHARGE_START_SOC = "charge_start_soc"
CONF_MAX_PRICE_THRESHOLD = "max_price_threshold"
CONF_SMART_DEADLINE_HOURS = "smart_deadline_hours"

# Defaults
DEFAULT_CHARGER_PHASES = 3
DEFAULT_VOLTAGE = 230
DEFAULT_FAST_AMPS = 12
DEFAULT_BATTERY_CAPACITY_KWH = 68.9
DEFAULT_TARGET_SOC = 80
DEFAULT_MIN_SOC = 30
DEFAULT_CHARGE_START_SOC = 69
DEFAULT_MAX_PRICE_THRESHOLD = 1.0
DEFAULT_SMART_DEADLINE_HOURS = 48
DEFAULT_OVERRIDE_DEADLINE_HOURS = 24
# Floor when flattening amps inside selected cheap hours (still ≤ charge_amps)
DEFAULT_MIN_FLAT_AMPS = 6

# Charge modes — only daily smart + temporary override
MODE_SMART = "smart"
MODE_OVERRIDE = "override"

CHARGE_MODES = [
    MODE_SMART,
    MODE_OVERRIDE,
]

# Reasons
REASON_OUTSIDE_CHARGING = "outside_charging"
REASON_OUTSIDE_NOT_CHARGING = "outside_not_charging"
REASON_TARGET_REACHED = "target_reached"
REASON_MIN_SOC_FLOOR = "min_soc_floor"
REASON_SOC_SUFFICIENT = "soc_sufficient"
REASON_PRICE_TOO_HIGH = "price_too_high"
REASON_SCHEDULED = "scheduled"
REASON_WAITING_FOR_SESSION = "waiting_for_session"
REASON_NO_ELIGIBLE_HOURS = "no_eligible_hours"
REASON_HOME_NOT_PLUGGED = "home_not_plugged"
REASON_TARGET_UNREACHABLE = "target_unreachable"
REASON_OVERRIDE_EXPIRED = "override_expired"
