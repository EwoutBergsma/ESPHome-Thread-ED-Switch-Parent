import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import sensor, text_sensor
from esphome.const import (
    CONF_ID,
    ENTITY_CATEGORY_DIAGNOSTIC,
    UNIT_MILLISECOND,
    UNIT_PERCENT,
)

DEPENDENCIES = ["openthread"]
AUTO_LOAD = ["sensor", "text_sensor", "button"]

CONF_AUTO_INTERVAL = "auto_interval"
CONF_AUTO_START = "auto_start"
CONF_TIMEOUT = "timeout"

CONF_STATE = "state"
CONF_LAST_RESULT = "last_result"
CONF_TARGET_EXTADDR = "target_extaddr"
CONF_TARGET_RLOC16 = "target_rloc16"
CONF_TARGET_ADDRESS = "target_address"

CONF_SENT = "sent"
CONF_RECEIVED = "received"
CONF_LOSS = "loss"
CONF_RTT = "rtt"

thread_ping_ns = cg.esphome_ns.namespace("thread_ping")
ThreadPingComponent = thread_ping_ns.class_("ThreadPingComponent", cg.Component)


def validate_timeout(value):
    value = cv.positive_time_period_milliseconds(value)
    if value.total_milliseconds > 65535:
        raise cv.Invalid("timeout must be <= 65535ms because OpenThread stores ping timeout as uint16_t")
    return value


CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(ThreadPingComponent),
        cv.Optional(CONF_AUTO_START, default=False): cv.boolean,
        cv.Optional(CONF_AUTO_INTERVAL, default="60s"): cv.positive_time_period_milliseconds,
        cv.Optional(CONF_TIMEOUT, default="3000ms"): validate_timeout,
        cv.Optional(CONF_STATE): text_sensor.text_sensor_schema(
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
        cv.Optional(CONF_LAST_RESULT): text_sensor.text_sensor_schema(
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
        cv.Optional(CONF_TARGET_EXTADDR): text_sensor.text_sensor_schema(
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
        cv.Optional(CONF_TARGET_RLOC16): text_sensor.text_sensor_schema(
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
        cv.Optional(CONF_TARGET_ADDRESS): text_sensor.text_sensor_schema(
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
        cv.Optional(CONF_SENT): sensor.sensor_schema(
            accuracy_decimals=0,
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
        cv.Optional(CONF_RECEIVED): sensor.sensor_schema(
            accuracy_decimals=0,
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
        cv.Optional(CONF_LOSS): sensor.sensor_schema(
            unit_of_measurement=UNIT_PERCENT,
            accuracy_decimals=0,
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
        cv.Optional(CONF_RTT): sensor.sensor_schema(
            unit_of_measurement=UNIT_MILLISECOND,
            accuracy_decimals=0,
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    cg.add(var.set_auto_start(config[CONF_AUTO_START]))
    cg.add(var.set_auto_interval_ms(config[CONF_AUTO_INTERVAL].total_milliseconds))
    cg.add(var.set_timeout_ms(config[CONF_TIMEOUT].total_milliseconds))

    if state_config := config.get(CONF_STATE):
        sens = await text_sensor.new_text_sensor(state_config)
        cg.add(var.set_state_sensor(sens))

    if result_config := config.get(CONF_LAST_RESULT):
        sens = await text_sensor.new_text_sensor(result_config)
        cg.add(var.set_last_result_sensor(sens))

    if extaddr_config := config.get(CONF_TARGET_EXTADDR):
        sens = await text_sensor.new_text_sensor(extaddr_config)
        cg.add(var.set_target_extaddr_sensor(sens))

    if rloc16_config := config.get(CONF_TARGET_RLOC16):
        sens = await text_sensor.new_text_sensor(rloc16_config)
        cg.add(var.set_target_rloc16_sensor(sens))

    if address_config := config.get(CONF_TARGET_ADDRESS):
        sens = await text_sensor.new_text_sensor(address_config)
        cg.add(var.set_target_address_sensor(sens))

    if sent_config := config.get(CONF_SENT):
        sens = await sensor.new_sensor(sent_config)
        cg.add(var.set_sent_sensor(sens))

    if received_config := config.get(CONF_RECEIVED):
        sens = await sensor.new_sensor(received_config)
        cg.add(var.set_received_sensor(sens))

    if loss_config := config.get(CONF_LOSS):
        sens = await sensor.new_sensor(loss_config)
        cg.add(var.set_loss_sensor(sens))

    if rtt_config := config.get(CONF_RTT):
        sens = await sensor.new_sensor(rtt_config)
        cg.add(var.set_rtt_sensor(sens))
