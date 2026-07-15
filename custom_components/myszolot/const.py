DOMAIN = "myszolot"

# External HA entities (read)
SENSOR_PRICE = "sensor.pstryk_current_buy_price"
SENSOR_SOC = "sensor.myszolot_battery_level"
BINARY_SENSOR_CABLE = "binary_sensor.myszolot_charge_cable"
BINARY_SENSOR_GARAGE_CAR = "binary_sensor.garage_car_present"
DEVICE_TRACKER = "device_tracker.myszolot_location"
SENSOR_CHARGING = "sensor.myszolot_charging"
NUMBER_CHARGE_CURRENT = "number.myszolot_charge_current"
INPUT_BOOLEAN_LOCATION_OVERRIDE = "input_boolean.myszolot_location_override"
INPUT_NUMBER_CUSTOM_TARGET_SOC = "input_number.myszolot_custom_target_soc"
INPUT_NUMBER_TIMED_START_HOUR = "input_number.myszolot_timed_start_hour"
INPUT_NUMBER_TIMED_DURATION_MINUTES = "input_number.myszolot_timed_duration_minutes"

# Config keys
CONF_CHARGER_PHASES = "charger_phases"
CONF_VOLTAGE = "voltage"
CONF_FAST_AMPS = "fast_amps"
CONF_SLOW_AMPS = "slow_amps"
CONF_BATTERY_CAPACITY_KWH = "battery_capacity_kWh"
CONF_DEFAULT_TARGET_SOC = "default_target_soc"
CONF_TRIP_TARGET_SOC = "trip_target_soc"
CONF_MIN_SOC = "min_soc"
CONF_CHARGE_START_SOC = "charge_start_soc"
CONF_MAX_PRICE_THRESHOLD = "max_price_threshold"
CONF_PLAN_TRIP_DEADLINE_HOURS = "plan_trip_deadline_hours"

# Defaults
DEFAULT_CHARGER_PHASES = 3
DEFAULT_VOLTAGE = 230
DEFAULT_FAST_AMPS = 12
DEFAULT_SLOW_AMPS = 5
DEFAULT_BATTERY_CAPACITY_KWH = 68.9
DEFAULT_TARGET_SOC = 80
DEFAULT_TRIP_TARGET_SOC = 95
DEFAULT_MIN_SOC = 30
DEFAULT_CHARGE_START_SOC = 69
DEFAULT_MAX_PRICE_THRESHOLD = 1.0
DEFAULT_PLAN_TRIP_DEADLINE_HOURS = 8

# Charge modes
MODE_SMART = "smart"
MODE_NOW_FAST = "now_fast"
MODE_NOW_SLOW = "now_slow"
MODE_PLAN_TRIP = "plan_trip"
MODE_TRIP_NOW = "trip_now"
MODE_SMART_CUSTOM = "smart_custom"
MODE_NOW_CUSTOM = "now_custom"
MODE_TIMED = "timed"

CHARGE_MODES = [
    MODE_SMART,
    MODE_NOW_FAST,
    MODE_NOW_SLOW,
    MODE_PLAN_TRIP,
    MODE_TRIP_NOW,
    MODE_SMART_CUSTOM,
    MODE_NOW_CUSTOM,
    MODE_TIMED,
]

# Timed-mode helper defaults (used when helpers are missing)
DEFAULT_TIMED_START_HOUR = 2
DEFAULT_TIMED_DURATION_MINUTES = 180

# Reasons
REASON_OUTSIDE_CHARGING = "outside_charging"
REASON_OUTSIDE_NOT_CHARGING = "outside_not_charging"
REASON_TARGET_REACHED = "target_reached"
REASON_MIN_SOC_FLOOR = "min_soc_floor"
REASON_CHARGING_NOW_FAST = "charging_now_fast"
REASON_CHARGING_NOW_SLOW = "charging_now_slow"
REASON_TRIP_CHARGING_NOW = "trip_charging_now"
REASON_SOC_SUFFICIENT = "soc_sufficient"
REASON_PRICE_TOO_HIGH = "price_too_high"
REASON_SCHEDULED = "scheduled"
REASON_WAITING_FOR_SESSION = "waiting_for_session"
REASON_NO_ELIGIBLE_HOURS = "no_eligible_hours"
REASON_HOME_NOT_PLUGGED = "home_not_plugged"
REASON_CHARGING_NOW_CUSTOM = "charging_now_custom"
REASON_TIMED_SESSION_DONE = "timed_session_done"
