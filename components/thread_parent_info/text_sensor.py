import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import text_sensor
from esphome.const import CONF_ID, ENTITY_CATEGORY_DIAGNOSTIC

DEPENDENCIES = ["openthread"]
AUTO_LOAD = ["text_sensor"]

CONF_EVENT_BASED = "event_based"
CONF_PARENT_EXTADDR = "parent_extaddr"
CONF_PARENT_RLOC16 = "parent_rloc16"

thread_parent_info_ns = cg.esphome_ns.namespace("thread_parent_info")
ThreadParentInfoComponent = thread_parent_info_ns.class_(
    "ThreadParentInfoComponent", cg.PollingComponent
)


def validate_at_least_one_sensor(config):
    if CONF_PARENT_EXTADDR not in config and CONF_PARENT_RLOC16 not in config:
        raise cv.Invalid(
            f"Configure at least one of {CONF_PARENT_EXTADDR} or {CONF_PARENT_RLOC16}"
        )
    return config


CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(ThreadParentInfoComponent),
            cv.Optional(CONF_EVENT_BASED, default=False): cv.boolean,
            cv.Optional(CONF_PARENT_EXTADDR): text_sensor.text_sensor_schema(
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_PARENT_RLOC16): text_sensor.text_sensor_schema(
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
        }
    ).extend(cv.polling_component_schema("30s")),
    validate_at_least_one_sensor,
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    cg.add(var.set_event_based(config[CONF_EVENT_BASED]))

    if parent_extaddr_config := config.get(CONF_PARENT_EXTADDR):
        sens = await text_sensor.new_text_sensor(parent_extaddr_config)
        cg.add(var.set_parent_extaddr_sensor(sens))

    if parent_rloc16_config := config.get(CONF_PARENT_RLOC16):
        sens = await text_sensor.new_text_sensor(parent_rloc16_config)
        cg.add(var.set_parent_rloc16_sensor(sens))
