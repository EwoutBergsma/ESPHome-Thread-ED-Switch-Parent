import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import button
from esphome.const import CONF_ID, ENTITY_CATEGORY_DIAGNOSTIC
from . import ThreadPingComponent, thread_ping_ns

CONF_THREAD_PING_ID = "thread_ping_id"

ThreadPingToggleButton = thread_ping_ns.class_(
    "ThreadPingToggleButton", button.Button, cg.Component
)

CONFIG_SCHEMA = button.button_schema(
    ThreadPingToggleButton,
    entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
).extend(
    {
        cv.Required(CONF_THREAD_PING_ID): cv.use_id(ThreadPingComponent),
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = await button.new_button(config)
    await cg.register_component(var, config)
    parent = await cg.get_variable(config[CONF_THREAD_PING_ID])
    cg.add(var.set_parent(parent))
